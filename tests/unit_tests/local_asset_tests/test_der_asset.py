"""Tests for DerAsset, the LocalAsset backed by any der-interface adapter."""

import pytest

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

der_interface = pytest.importorskip("der_interface")

from der_interface import (  # noqa: E402
    DispatchResponse,
    EnvelopePoint,
    FlexibilityEnvelope,
    Interval,
    Limits,
    Quality,
    ResourceIdentity,
    ResourceKind,
    ResourceState,
    SignConvention,
)

from tent.containers.time_interval import TimeInterval  # noqa: E402
from tent.local_asset.der_asset import DerAsset  # noqa: E402

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


class NotAProvider:
    """Missing get_envelope."""


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
    return mkt


@pytest.fixture
def hvac_asset(tn):
    return DerAsset(flexibility=FakeHvac(), transactive_node=tn, name="hvac",
                    maximum_power=0.0, minimum_power=-5.0)


@pytest.fixture
def pv_asset(tn):
    return DerAsset(flexibility=FakePv(), transactive_node=tn, name="pv",
                    maximum_power=120.0, minimum_power=0.0)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_is_a_local_asset(self, hvac_asset):
        from tent.local_asset import LocalAsset
        assert isinstance(hvac_asset, LocalAsset)

    def test_rejects_provider_without_envelope(self, tn):
        with pytest.raises(ValueError, match="no get_envelope"):
            DerAsset(flexibility=NotAProvider(), transactive_node=tn, name="bad")

    def test_rejects_inverted_price_range(self, tn):
        with pytest.raises(ValueError, match="low < high"):
            DerAsset(flexibility=FakeHvac(), transactive_node=tn, name="x",
                     price_range=(0.10, 0.0))

    def test_reuses_provider_for_dispatch_and_state(self, hvac_asset):
        """One object commonly implements several protocols."""
        assert hvac_asset.dispatch_target is hvac_asset.flexibility
        assert hvac_asset.state_provider is hvac_asset.flexibility

    def test_pv_is_not_dispatchable(self, pv_asset):
        """You cannot command the sun."""
        assert pv_asset.dispatch_target is None
        assert pv_asset.state_provider is None

    def test_resolves_adapter_from_config_spec(self, tn, tmp_path):
        """Configuration-driven setup, as tn.campus.json would use."""
        csv = tmp_path / "load.csv"
        csv.write_text(
            "start,duration_s,q_min_kw,q_max_kw,q_baseline_kw\n"
            "2026-08-05T14:00:00+00:00,900,0.0,4.0,2.0\n"
        )
        asset = DerAsset(
            flexibility={
                "class_name": "ReplayAdapter",
                "module_name": "der_interface.adapters.replay",
                "factory": "from_csv",
                "path": str(csv),
            },
            transactive_node=tn, name="from_config",
        )
        assert asset.flexibility.identity.resource_id == "load"


# ---------------------------------------------------------------------------
# Sign conversion -- the highest-risk behaviour
# ---------------------------------------------------------------------------


class TestSignConversion:
    def test_load_becomes_negative_power(self, hvac_asset, market):
        """der-interface says a 2.8 kW load; TENT must see -2.8."""
        hvac_asset.schedule_power(market)
        for ti in market.time_intervals:
            assert hvac_asset.scheduled_powers.get_by_interval(ti).value == pytest.approx(-2.8)

    def test_generation_becomes_positive_power(self, pv_asset, market):
        """der-interface says -100 kW (generation); TENT must see +100."""
        pv_asset.schedule_power(market)
        for ti in market.time_intervals:
            assert pv_asset.scheduled_powers.get_by_interval(ti).value == pytest.approx(100.0)

    def test_min_and_max_swap(self, hvac_asset, market):
        """Negating endpoints reverses their order; a botched conversion
        would produce an inverted, invalid range."""
        hvac_asset.schedule_power(market)
        hvac_asset.update_vertices(market)
        curve = hvac_asset.active_vertices.get_by_interval(market.time_intervals[0])
        quantities = sorted(v.quantity for v in curve.vertices)
        assert quantities == pytest.approx([-4.2, 0.0])


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


class TestScheduling:
    def test_sets_schedule_calculated(self, hvac_asset, market):
        assert not hvac_asset.schedule_calculated
        hvac_asset.schedule_power(market)
        assert hvac_asset.schedule_calculated

    def test_one_schedule_per_interval(self, hvac_asset, market):
        hvac_asset.schedule_power(market)
        for ti in market.time_intervals:
            assert hvac_asset.scheduled_powers.get_by_interval(ti) is not None

    def test_engagement_reflects_flexibility(self, hvac_asset, pv_asset, market):
        hvac_asset.schedule_power(market)
        pv_asset.schedule_power(market)
        ti = market.time_intervals[0]
        assert hvac_asset.engagement_schedule.get_by_interval(ti).value is True
        assert pv_asset.engagement_schedule.get_by_interval(ti).value is False

    def test_rejects_wrong_point_count(self, tn, market):
        class BadAdapter(FakeHvac):
            def get_envelope(self, intervals, context=None):
                return FlexibilityEnvelope(
                    self.identity,
                    (EnvelopePoint(intervals[0], 0.0, 1.0, 0.5),),
                )

        asset = DerAsset(flexibility=BadAdapter(), transactive_node=tn, name="bad")
        with pytest.raises(RuntimeError, match="one point per interval"):
            asset.schedule_power(market)

    def test_limit_violation_detected(self, tn, market):
        """A market must never be able to widen physics."""
        adapter = FakeHvac(q_max=10.0, baseline=2.0, limit_max=5.0)
        asset = DerAsset(flexibility=adapter, transactive_node=tn, name="over")
        with pytest.raises(Exception, match="exceeds limits"):
            asset.schedule_power(market)

    def test_limit_check_can_be_disabled(self, tn, market):
        adapter = FakeHvac(q_max=10.0, baseline=2.0, limit_max=5.0)
        asset = DerAsset(flexibility=adapter, transactive_node=tn, name="over",
                         enforce_limits=False)
        asset.schedule_power(market)
        assert asset.schedule_calculated


# ---------------------------------------------------------------------------
# Vertices
# ---------------------------------------------------------------------------


class TestVertices:
    def test_flexible_yields_two_vertices(self, hvac_asset, market):
        hvac_asset.schedule_power(market)
        hvac_asset.update_vertices(market)
        curve = hvac_asset.active_vertices.get_by_interval(market.time_intervals[0])
        assert len(curve.vertices) == 2

    def test_must_take_yields_one_infinite_price_vertex(self, pv_asset, market):
        """Nothing to negotiate: take it at any price."""
        pv_asset.schedule_power(market)
        pv_asset.update_vertices(market)
        curve = pv_asset.active_vertices.get_by_interval(market.time_intervals[0])
        assert len(curve.vertices) == 1
        vertex = next(iter(curve.vertices))
        assert vertex.price == float("inf")
        assert vertex.quantity == pytest.approx(100.0)

    def test_vertices_use_price_range(self, tn, market):
        asset = DerAsset(flexibility=FakeHvac(), transactive_node=tn, name="hvac",
                         price_range=(0.02, 0.08))
        asset.schedule_power(market)
        asset.update_vertices(market)
        curve = asset.active_vertices.get_by_interval(market.time_intervals[0])
        assert sorted(v.price for v in curve.vertices) == pytest.approx([0.02, 0.08])

    def test_update_vertices_works_without_prior_schedule(self, hvac_asset, market):
        hvac_asset.update_vertices(market)
        assert hvac_asset.active_vertices.get_by_interval(market.time_intervals[0])

    def test_market_can_interpolate_power_from_price(self, hvac_asset, market):
        """The vertices must be usable by TENT's own curve machinery."""
        hvac_asset.schedule_power(market)
        hvac_asset.update_vertices(market)
        curve = hvac_asset.active_vertices.get_by_interval(market.time_intervals[0])
        midpoint = curve.power_from_vertices(0.05)
        assert -4.2 <= midpoint <= 0.0


# ---------------------------------------------------------------------------
# Full schedule() path
# ---------------------------------------------------------------------------


class TestScheduleIntegration:
    def test_sealed_schedule_runs_end_to_end(self, hvac_asset, market):
        """LocalAsset.schedule is marked SEALED; DerAsset must work through it."""
        hvac_asset.schedule(market)
        ti = market.time_intervals[0]
        assert hvac_asset.scheduled_powers.get_by_interval(ti) is not None
        assert hvac_asset.active_vertices.get_by_interval(ti) is not None
        assert hvac_asset.reserve_margins.get_by_interval(ti) is not None

    def test_reserve_margin_computed(self, hvac_asset, market):
        hvac_asset.schedule(market)
        margin = hvac_asset.reserve_margins.get_by_interval(market.time_intervals[0])
        assert margin.value >= 0.0


# ---------------------------------------------------------------------------
# Actuation
# ---------------------------------------------------------------------------


class TestActuation:
    def test_dispatches_scheduled_power(self, hvac_asset, market):
        hvac_asset.schedule(market)
        hvac_asset.actuate(market)
        assert len(hvac_asset.flexibility.commands) == 4

    def test_dispatch_carries_scheduled_value(self, hvac_asset, market):
        hvac_asset.schedule(market)
        hvac_asset.actuate(market)
        assert hvac_asset.flexibility.commands[0].power_kw == pytest.approx(-2.8)

    def test_responses_recorded_for_reconciliation(self, hvac_asset, market):
        hvac_asset.schedule(market)
        hvac_asset.actuate(market)
        assert len(hvac_asset.dispatch_responses) == 4

    def test_non_dispatchable_asset_is_a_noop(self, pv_asset, market):
        pv_asset.schedule(market)
        pv_asset.actuate(market)
        assert pv_asset.dispatch_responses == []

    def test_can_be_disabled(self, tn, market):
        adapter = FakeHvac()
        asset = DerAsset(flexibility=adapter, transactive_node=tn, name="x",
                         actuate_from_schedule=False)
        asset.schedule(market)
        asset.actuate(market)
        assert adapter.commands == []

    def test_dispatch_failure_does_not_halt_market(self, tn, market):
        """One bad asset must not take down the whole node."""
        class Exploding(FakeHvac):
            def dispatch(self, command):
                raise RuntimeError("device offline")

        asset = DerAsset(flexibility=Exploding(), transactive_node=tn, name="boom")
        asset.schedule(market)
        asset.actuate(market)  # must not raise
        assert asset.dispatch_responses == []

    def test_release(self, hvac_asset, market):
        hvac_asset.release(market)
        assert len(hvac_asset.flexibility.releases) == 4


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


class TestReconciliation:
    def test_actual_power_from_state(self, hvac_asset, market):
        """Fills the gap ReconciliationMarket.get_actual_power leaves."""
        actual = hvac_asset.get_actual_power(market.time_intervals[0])
        assert actual == pytest.approx(-2.8)

    def test_actual_power_falls_back_to_dispatch_log(self, tn, market):
        class NoState(FakeHvac):
            def get_state(self, timestamp=None):
                raise RuntimeError("no sensor")

        asset = DerAsset(flexibility=NoState(), transactive_node=tn, name="x")
        asset.schedule(market)
        asset.actuate(market)
        assert asset.get_actual_power(market.time_intervals[0]) == pytest.approx(-2.8)

    def test_actual_power_none_when_unavailable(self, pv_asset, market):
        assert pv_asset.get_actual_power(market.time_intervals[0]) is None

    def test_unusable_quality_is_not_reported(self, tn, market):
        """Missing is not zero."""
        class BadSensor(FakeHvac):
            def get_state(self, timestamp=None):
                return ResourceState(self.identity, datetime.now(UTC), {},
                                     quality=Quality.MISSING)

        asset = DerAsset(flexibility=BadSensor(), transactive_node=tn, name="x")
        assert asset.get_actual_power(market.time_intervals[0]) is None

    def test_deviation_detectable(self, tn, market):
        """Commanded vs delivered, the core settlement input."""
        adapter = FakeHvac(limit_max=1.0)  # will clamp
        asset = DerAsset(flexibility=adapter, transactive_node=tn, name="x",
                         enforce_limits=False)
        asset.schedule(market)
        asset.actuate(market)
        response = asset.dispatch_responses[0]
        assert response.violations
        assert response.actual_power_kw == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# Archiving
# ---------------------------------------------------------------------------


class TestArchiving:
    def test_table_name(self, hvac_asset):
        assert hvac_asset.table_name == "der_asset"

    def test_record_includes_adapter_provenance(self, hvac_asset):
        """When an envelope looks wrong, the first question is which adapter
        produced it."""
        record = hvac_asset.get_record()
        assert record["adapter_class"] == "FakeHvac"
        assert record["resource_id"] == "fake_hvac"
        assert record["dispatchable"] is True

    def test_archive_definitions_extend_local_asset(self):
        names = [d["name"] for d in DerAsset.get_archive_definitions()]
        assert "der_asset" in names
        assert "local_asset" in names


# ---------------------------------------------------------------------------
# Backend swap: the whole point
# ---------------------------------------------------------------------------


class TestBackendSwap:
    def test_one_class_many_backends(self, tn, market):
        """Same asset class, unrelated backends, no code changes."""
        assets = [
            DerAsset(flexibility=FakeHvac(), transactive_node=tn, name="a"),
            DerAsset(flexibility=FakePv(), transactive_node=tn, name="b"),
        ]
        for asset in assets:
            asset.schedule(market)
            asset.actuate(market)
            assert asset.schedule_calculated

    def test_replay_backend(self, tn, market, tmp_path):
        from der_interface.adapters import ReplayAdapter

        lines = ["start,duration_s,q_min_kw,q_max_kw,q_baseline_kw"]
        for ti in market.time_intervals:
            lines.append(f"{ti.start_time.isoformat()},900,0.0,4.2,2.8")
        csv = tmp_path / "r.csv"
        csv.write_text("\n".join(lines) + "\n")

        asset = DerAsset(flexibility=ReplayAdapter.from_csv(csv),
                         transactive_node=tn, name="replay")
        asset.schedule(market)
        ti = market.time_intervals[0]
        assert asset.scheduled_powers.get_by_interval(ti).value == pytest.approx(-2.8)

    def test_pvlib_backend(self, tn):
        """Real solar physics through the same asset class."""
        pytest.importorskip("pvlib")
        pd = pytest.importorskip("pandas")
        from pvlib.location import Location
        from pvlib.pvsystem import PVSystem
        from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS

        from der_interface.adapters.pvlib import PvlibPvAdapter

        adapter = PvlibPvAdapter.from_location(
            "campus_pv",
            Location(46.28, -119.28, tz="US/Pacific"),
            PVSystem(
                surface_tilt=30, surface_azimuth=180,
                module_parameters={"pdc0": 120_000, "gamma_pdc": -0.004},
                inverter_parameters={"pdc0": 120_000},
                temperature_model_parameters=(
                    TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]
                ),
            ),
            rated_kw=120.0,
        )

        mkt = Mock()
        mkt.name = "PvMarket"
        mkt.market_series_name = "PvSeries"
        noon = pd.Timestamp("2026-07-15 12:00", tz="US/Pacific").to_pydatetime()
        mkt.time_intervals = [
            TimeInterval(market=mkt, duration=timedelta(minutes=15),
                         start_time=noon + timedelta(minutes=15 * n))
            for n in range(4)
        ]

        asset = DerAsset(flexibility=adapter, transactive_node=tn, name="pv",
                         maximum_power=120.0, minimum_power=0.0)
        asset.schedule(mkt)

        # Generation is positive in TENT, and below nameplate.
        for ti in mkt.time_intervals:
            power = asset.scheduled_powers.get_by_interval(ti).value
            assert 0.0 < power <= 120.0

    def test_gridlabd_water_heater_backend(self, tn, market):
        """GridLAB-D water heater adapter connected to simulation engine."""
        from der_interface.adapters.gridlabd import GridlabdWaterHeaterAdapter

        wh = GridlabdWaterHeaterAdapter.from_config(
            resource_id="wh_01",
            rated_kw=4.5,
            gridlabd={
                "object_name": "wh_01",
                "t_min_f": 110.0,
                "t_max_f": 140.0,
                "t_desired_f": 125.0,
            },
        )
        asset = DerAsset(
            flexibility=wh,
            transactive_node=tn,
            name="gridlabd_wh_asset",
            price_range=(0.03, 0.12),
        )
        asset.schedule(market)
        assert asset.schedule_calculated

        ti = market.time_intervals[0]
        curve = asset.active_vertices.get_by_interval(ti)
        assert len(curve.vertices) == 2
        quantities = sorted(v.quantity for v in curve.vertices)
        # TENT convention: consumption is negative kW (-4.5 kW to 0 kW)
        assert quantities[0] == pytest.approx(-4.5, 0.1)
        assert quantities[1] == pytest.approx(0.0, 0.1)

        # Actuate heating dispatch
        asset.scheduled_powers.update(ti, -4.5)
        asset.actuate(market)
        assert len(asset.dispatch_responses) == len(market.time_intervals)

    def test_gridlabd_hvac_backend(self, tn, market):
        """GridLAB-D HVAC house adapter connected to simulation engine."""
        from der_interface.adapters.gridlabd import GridlabdHvacAdapter

        hvac = GridlabdHvacAdapter.from_config(
            resource_id="house_01",
            rated_kw=3.5,
            gridlabd={
                "object_name": "house_01",
                "min_cooling_setpoint_f": 68.0,
                "max_cooling_setpoint_f": 78.0,
            },
        )
        asset = DerAsset(
            flexibility=hvac,
            transactive_node=tn,
            name="gridlabd_hvac_asset",
            price_range=(0.02, 0.10),
        )
        asset.schedule(market)
        assert asset.schedule_calculated

        ti = market.time_intervals[0]
        curve = asset.active_vertices.get_by_interval(ti)
        assert len(curve.vertices) == 2
        quantities = sorted(v.quantity for v in curve.vertices)
        # TENT convention: cooling load is negative kW (-3.5 kW to 0.0 kW)
        assert quantities[0] == pytest.approx(-3.5, 0.1)
        assert quantities[1] == pytest.approx(0.0, 0.1)

    def test_gridlabd_battery_backend(self, tn, market):
        """GridLAB-D inverter battery adapter with bidirectional bidding."""
        from der_interface.adapters.gridlabd import GridlabdBatteryInverterAdapter

        battery = GridlabdBatteryInverterAdapter.from_config(
            resource_id="inv_battery_01",
            rated_kw=5.0,
            gridlabd={
                "object_name": "inv_battery_01",
                "capacity_kwh": 13.5,
                "min_soc": 0.10,
                "max_soc": 0.95,
            },
        )
        asset = DerAsset(
            flexibility=battery,
            transactive_node=tn,
            name="gridlabd_battery_asset",
            price_range=(0.02, 0.15),
        )
        asset.schedule(market)
        assert asset.schedule_calculated

        ti = market.time_intervals[0]
        curve = asset.active_vertices.get_by_interval(ti)
        assert len(curve.vertices) == 2
        quantities = sorted(v.quantity for v in curve.vertices)
        # In TENT (generation-positive): charging is -5 kW, discharging is +5 kW
        assert quantities[0] == pytest.approx(-5.0, 0.1)
        assert quantities[1] == pytest.approx(5.0, 0.1)
