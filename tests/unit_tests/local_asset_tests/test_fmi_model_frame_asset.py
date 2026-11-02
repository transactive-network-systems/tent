"""Tests for ModelFrameAsset backed by tent.flexibility adapters."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import pytest

from tent.flexibility import (
    DispatchCommand,
    DispatchResponse,
    EnvelopePoint,
    FlexibilityEnvelope,
    Interval,
    Limits,
    ResourceIdentity,
    ResourceKind,
    ResourceState,
    SignConvention,
)
from tent.flexibility.adapters.replay import ReplayAdapter
from tent.containers.time_interval import TimeInterval
from tent.local_asset import LocalAsset
from tent.local_asset.model_frame.model_frame_asset import ModelFrameAsset

UTC = timezone.utc
LOAD_POS = SignConvention.LOAD_POSITIVE
GEN_POS = SignConvention.GENERATION_POSITIVE


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeHvac:
    """A flexible load: 0 to 4.2 kW, baseline 2.8 kW, load-positive."""

    def __init__(self, resource_id="fake_hvac", q_min=0.0, q_max=4.2, baseline=2.8,
                 limit_max=5.0):
        self.identity = ResourceIdentity(resource_id, ResourceKind.THERMAL_LOAD,
                                         source="fake")
        self.q_min = q_min
        self.q_max = q_max
        self.baseline = baseline
        self.limit_max = limit_max
        self.commands = []
        self.releases = []

    def get_envelope(self, intervals, context=None):
        return FlexibilityEnvelope(
            self.identity,
            tuple(EnvelopePoint(i, self.q_min, self.q_max, self.baseline)
                  for i in intervals),
        )

    def get_state(self, timestamp=None):
        return ResourceState(self.identity, timestamp or datetime.now(UTC),
                             {"power_kw": self.baseline})

    def get_limits(self, timestamp=None):
        return Limits(0.0, self.limit_max)

    def dispatch(self, command):
        self.commands.append(command)
        limits = self.get_limits()
        # Command arrives generation-positive from TENT; convert to canonical.
        canonical = GEN_POS.to_canonical(command.power_kw)
        clamped = limits.clamp(canonical)
        violations = ()
        if abs(clamped - canonical) > 1e-9:
            violations = (f"clamped {canonical} to {clamped}",)
        return DispatchResponse(
            self.identity, command.interval, accepted=True,
            actual={"power_kw": GEN_POS.from_canonical(clamped)},
            violations=violations,
        )

    def release(self, interval):
        self.releases.append(interval)
        return DispatchResponse(self.identity, interval, accepted=True)


class FakePv:
    """A must-take generator. Not dispatchable."""

    def __init__(self, output_kw=100.0):
        self.identity = ResourceIdentity("fake_pv", ResourceKind.RENEWABLE_GENERATION)
        self.output_kw = output_kw

    def get_envelope(self, intervals, context=None):
        generation = LOAD_POS.generation(self.output_kw)
        return FlexibilityEnvelope.inflexible(
            self.identity, intervals, [generation] * len(intervals)
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tn():
    node = Mock()
    node.local_assets = []
    node.get_isms_by_attribute = Mock(return_value=[])
    node.get_meters_by_attribute = Mock(return_value=[])
    node.register_subscription = Mock()
    return node


@pytest.fixture
def market():
    mkt = Mock()
    mkt.name = "TestMarket"
    mkt.market_series_name = "TestSeries"
    start = datetime(2026, 8, 5, 14, tzinfo=UTC)
    mkt.time_intervals = [
        TimeInterval(market=mkt, duration=timedelta(minutes=15),
                     start_time=start + timedelta(minutes=15 * n))
        for n in range(4)
    ]
    # Simple price model returning mean 0.05, std 0.01
    price_model = Mock()
    price_model.get = Mock(return_value=(0.05, 0.01))
    mkt.price_model = price_model
    mkt.marginal_prices.get_by_interval.return_value = None  # no cleared price yet: the series default applies
    mkt.series.default_price = 0.05
    return mkt


@pytest.fixture
def hvac_asset(tn):
    return ModelFrameAsset(
        transactive_node=tn,
        name="hvac",
        maximum_power=0.0,
        minimum_power=-5.0,
        model_configs={
            "demand_curve_points": 2,
            "models": [
                {
                    "subscribe_key": "hvac_device",
                    "class_name": "FlexibilityModelFrameAdapter",
                    "model_config": {
                        "adapter": FakeHvac(),
                    },
                }
            ],
        },
    )


@pytest.fixture
def pv_asset(tn):
    return ModelFrameAsset(
        transactive_node=tn,
        name="pv",
        maximum_power=120.0,
        minimum_power=0.0,
        model_configs={
            "demand_curve_points": 2,
            "models": [
                {
                    "subscribe_key": "pv_device",
                    "class_name": "FlexibilityModelFrameAdapter",
                    "model_config": {
                        "adapter": FakePv(),
                    },
                }
            ],
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestModelFrameAssetWithFmi:
    def test_is_a_local_asset(self, hvac_asset):
        assert isinstance(hvac_asset, LocalAsset)
        assert isinstance(hvac_asset, ModelFrameAsset)

    def test_load_becomes_negative_power(self, hvac_asset, market):
        """FMI canonical load 2.8 kW must be negative power (-2.8 kW) in TENT."""
        hvac_asset.schedule_power(market)
        for ti in market.time_intervals:
            assert hvac_asset.scheduled_powers.get_by_interval(ti).value == pytest.approx(-2.8)

    def test_generation_becomes_positive_power(self, pv_asset, market):
        """FMI solar generation 100 kW must be positive power (+100 kW) in TENT."""
        pv_asset.schedule_power(market)
        for ti in market.time_intervals:
            assert pv_asset.scheduled_powers.get_by_interval(ti).value == pytest.approx(100.0)

    def test_min_and_max_swap_on_vertices(self, hvac_asset, market):
        """Negating endpoints converts [0.0, 4.2] load to [-4.2, 0.0] power vertices in TENT."""
        hvac_asset.schedule_power(market)
        hvac_asset.update_vertices(market)
        curve = hvac_asset.active_vertices.get_by_interval(market.time_intervals[0])
        quantities = sorted(v.quantity for v in curve.vertices)
        assert quantities == pytest.approx([-4.2, 0.0])

    def test_actuate_dispatches_to_fmi_adapter(self, hvac_asset, market):
        """actuate() routes market schedule back to the physical adapter."""
        hvac_asset.schedule_power(market)
        hvac_asset.actuate(market)

        adapter = hvac_asset.models["hvac_device"].adapter
        assert len(adapter.commands) == len(market.time_intervals)
        for cmd in adapter.commands:
            assert cmd.power_kw == pytest.approx(-2.8)
            assert cmd.sign == GEN_POS

    def test_preset_configuration_loading(self, tn, market):
        """ModelFrameAsset loads FMI presets directly from model_config."""
        pytest.importorskip("pvlib")
        asset = ModelFrameAsset(
            transactive_node=tn,
            name="preset_pv",
            model_configs={
                "demand_curve_points": 2,
                "models": [
                    {
                        "subscribe_key": "devices/pv",
                        "class_name": "FlexibilityModelFrameAdapter",
                        "model_config": {
                            "preset": "residential_pv",
                            "rated_kw": 10.0,
                        },
                    }
                ],
            },
        )
        assert "devices/pv" in asset.models
        asset.schedule_power(market)
        # Power is scheduled across all intervals
        for ti in market.time_intervals:
            assert asset.scheduled_powers.get_by_interval(ti) is not None

    def test_replay_adapter_from_csv(self, tn, market, tmp_path):
        """ModelFrameAsset loads a replay CSV dataset."""
        csv = tmp_path / "load.csv"
        csv.write_text(
            "start,duration_s,q_min_kw,q_max_kw,q_baseline_kw\n"
            "2026-08-05T14:00:00+00:00,900,0.0,4.0,2.0\n"
            "2026-08-05T14:15:00+00:00,900,0.0,4.0,2.0\n"
            "2026-08-05T14:30:00+00:00,900,0.0,4.0,2.0\n"
            "2026-08-05T14:45:00+00:00,900,0.0,4.0,2.0\n"
        )
        adapter = ReplayAdapter.from_csv(csv)
        asset = ModelFrameAsset(
            transactive_node=tn,
            name="replay_asset",
            model_configs={
                "demand_curve_points": 2,
                "models": [
                    {
                        "subscribe_key": "devices/replay",
                        "class_name": "FlexibilityModelFrameAdapter",
                        "model_config": {
                            "adapter": adapter,
                        },
                    }
                ],
            },
        )
        asset.schedule_power(market)
        for ti in market.time_intervals:
            assert asset.scheduled_powers.get_by_interval(ti).value == pytest.approx(-2.0)


def test_a_second_scheduling_pass_does_not_grow_the_curve(hvac_asset, market):  # audit T-17
    hvac_asset.schedule(market)
    first = {ti: len(hvac_asset.active_vertices.get_by_interval(ti)) for ti in market.time_intervals}
    assert set(first.values()) == {2}, 'two vertices: minimum and maximum flexibility'
    market.price_model.get = Mock(return_value=(0.06, 0.01))  # the price changes between passes
    hvac_asset.schedule(market)
    second = {ti: len(hvac_asset.active_vertices.get_by_interval(ti)) for ti in market.time_intervals}
    assert second == first, 'the curve is replaced on each pass, not accumulated'
    assert all(abs(v.price - 0.06) < 0.0101 for ti in market.time_intervals
               for v in hvac_asset.active_vertices.get_by_interval(ti).vertices), 'and reflects the latest prices'
