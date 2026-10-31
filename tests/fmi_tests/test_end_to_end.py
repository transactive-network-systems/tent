"""The test that proves the point.

One market-like loop, run against three completely different backends without
changing a line of the loop. If this test passes, the abstraction is real.

The loop deliberately mimics what a TENT ``LocalAsset`` does:

1. ask the resource what it can do        -> get_envelope()
2. turn that into a bid                   -> vertices
3. clear at some price                    -> choose a power
4. command the resource                   -> dispatch()
5. record what actually happened          -> DispatchResponse
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tent.flexibility import (
    DispatchCommand,
    EnvelopePoint,
    FlexibilityEnvelope,
    Interval,
    Limits,
    ResourceIdentity,
    ResourceKind,
    SignConvention,
    capabilities_of,
)
from tent.flexibility.adapters import ReplayAdapter

UTC = timezone.utc
GEN_POS = SignConvention.GENERATION_POSITIVE


# ---------------------------------------------------------------------------
# A stand-in for the market side. This is the code that must NOT change when
# the backend changes.
# ---------------------------------------------------------------------------


def build_bid(envelope: FlexibilityEnvelope, low_price: float, high_price: float):
    """Turn an envelope into (price, quantity) vertices, TENT-style.

    A must-take resource yields one vertex; a flexible one yields two.
    """
    vertices = []
    for point in envelope:
        if point.flexible:
            vertices.append((point.interval, low_price, point.q_min_kw))
            vertices.append((point.interval, high_price, point.q_max_kw))
        else:
            vertices.append((point.interval, float("inf"), point.q_baseline_kw))
    return vertices


def clear_at_price(envelope: FlexibilityEnvelope, price: float,
                   low_price: float, high_price: float):
    """Pick an operating point per interval by linear interpolation on price."""
    cleared = {}
    for point in envelope:
        if not point.flexible:
            cleared[point.interval] = point.q_baseline_kw
            continue
        span = high_price - low_price
        frac = 0.0 if span == 0 else max(0.0, min(1.0, (price - low_price) / span))
        cleared[point.interval] = point.q_min_kw + frac * point.range_kw
    return cleared


def run_market_cycle(resource, intervals, price=0.05, low=0.0, high=0.10):
    """The backend-agnostic loop. Works with any FlexibilityProvider."""
    caps = capabilities_of(resource)
    assert caps.biddable, "resource cannot be bid into a market"

    envelope = resource.get_envelope(intervals)
    assert len(envelope) == len(intervals)

    if caps.state:
        envelope.assert_within(resource.get_limits())

    bid = build_bid(envelope, low, high)
    cleared = clear_at_price(envelope, price, low, high)

    responses = []
    if caps.dispatch:
        for interval, power in cleared.items():
            responses.append(
                resource.dispatch(
                    DispatchCommand.for_power(
                        resource.identity, interval, power, source="test_market"
                    )
                )
            )

    return {
        "envelope": envelope,
        "bid": bid,
        "cleared": cleared,
        "responses": responses,
    }


# ---------------------------------------------------------------------------
# Backend 1: a hand-built flexible load, no adapter at all
# ---------------------------------------------------------------------------


class InlineHvac:
    """Satisfies the contract with no inheritance and no dependencies."""

    identity = ResourceIdentity("inline_hvac", ResourceKind.THERMAL_LOAD, source="inline")

    def __init__(self):
        self.commands = []

    def get_envelope(self, intervals, context=None):
        return FlexibilityEnvelope(
            self.identity,
            tuple(EnvelopePoint(i, 0.0, 4.2, 2.8) for i in intervals),
        )

    def get_state(self, timestamp=None):
        from tent.flexibility import ResourceState

        return ResourceState(self.identity, timestamp or datetime.now(UTC),
                             {"power_kw": 2.8, "indoor_temp_c": 22.4})

    def get_limits(self, timestamp=None):
        return Limits(0.0, 5.0)

    def dispatch(self, command):
        from tent.flexibility import DispatchResponse

        clamped = self.get_limits().clamp(command.power_kw)
        self.commands.append(command)
        return DispatchResponse(
            self.identity, command.interval, accepted=True,
            actual={"power_kw": clamped},
        )

    def release(self, interval):
        from tent.flexibility import DispatchResponse

        return DispatchResponse(self.identity, interval, accepted=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def intervals():
    return Interval.sequence(datetime(2026, 8, 5, 14, tzinfo=UTC), 900, 4)


@pytest.fixture
def replay_resource(tmp_path, intervals):
    lines = ["start,duration_s,q_min_kw,q_max_kw,q_baseline_kw"]
    for i in intervals:
        lines.append(f"{i.start.isoformat()},{i.duration_s},0.0,4.2,2.8")
    path = tmp_path / "replay_hvac.csv"
    path.write_text("\n".join(lines) + "\n")
    return ReplayAdapter.from_csv(path)


@pytest.fixture
def pv_resource():
    pytest.importorskip("pvlib")
    pd = pytest.importorskip("pandas")
    from pvlib.location import Location
    from pvlib.pvsystem import PVSystem
    from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS

    from tent.flexibility.adapters.pvlib import PvlibPvAdapter

    return PvlibPvAdapter.from_location(
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


# ---------------------------------------------------------------------------
# The actual point
# ---------------------------------------------------------------------------


class TestSameLoopManyBackends:
    def test_inline_flexible_load(self, intervals):
        result = run_market_cycle(InlineHvac(), intervals)
        assert len(result["bid"]) == 8  # two vertices per flexible interval
        assert len(result["responses"]) == 4
        for power in result["cleared"].values():
            assert 0.0 <= power <= 4.2

    def test_replay_backend(self, replay_resource, intervals):
        result = run_market_cycle(replay_resource, intervals)
        assert len(result["bid"]) == 8
        assert len(result["responses"]) == 4

    def test_pvlib_backend(self, pv_resource):
        """Same loop, real solar physics, must-take, not dispatchable."""
        pd = pytest.importorskip("pandas")
        intervals = Interval.sequence(
            pd.Timestamp("2026-07-15 12:00", tz="US/Pacific").to_pydatetime(), 900, 4
        )
        result = run_market_cycle(pv_resource, intervals)
        assert len(result["bid"]) == 4          # one vertex each: must-take
        assert result["responses"] == []        # cannot dispatch the sun
        for power in result["cleared"].values():
            assert power < 0.0                 # generation under load-positive

    def test_identical_data_gives_identical_results(self, replay_resource, intervals):
        """Two unrelated backends carrying the same numbers must be
        indistinguishable to the market loop."""
        a = run_market_cycle(InlineHvac(), intervals)
        b = run_market_cycle(replay_resource, intervals)
        assert list(a["cleared"].values()) == pytest.approx(list(b["cleared"].values()))


class TestPriceResponse:
    @pytest.mark.parametrize(
        "price,expected",
        [(0.0, 0.0), (0.05, 2.1), (0.10, 4.2), (0.20, 4.2), (-1.0, 0.0)],
    )
    def test_monotonic_and_clamped(self, intervals, price, expected):
        result = run_market_cycle(InlineHvac(), intervals, price=price)
        for power in result["cleared"].values():
            assert power == pytest.approx(expected)

    def test_cleared_power_always_feasible(self, intervals):
        for price in (-5.0, 0.0, 0.033, 0.5, 100.0):
            result = run_market_cycle(InlineHvac(), intervals, price=price)
            envelope = result["envelope"]
            for interval, power in result["cleared"].items():
                assert envelope.for_interval(interval).contains(power)


class TestTentHandoff:
    """TENT is generation-positive; this package is load-positive."""

    def test_conversion_at_the_boundary(self, intervals):
        envelope = InlineHvac().get_envelope(intervals)
        tent_view = envelope.in_convention(GEN_POS)

        assert tent_view.sign is GEN_POS
        for load_pt, tent_pt in zip(envelope, tent_view):
            assert tent_pt.q_baseline_kw == -load_pt.q_baseline_kw
            assert tent_pt.q_min_kw == -load_pt.q_max_kw
            assert tent_pt.q_max_kw == -load_pt.q_min_kw

    def test_a_load_looks_like_negative_power_to_tent(self, intervals):
        """Sanity check on the thing most likely to be implemented backwards."""
        tent_view = InlineHvac().get_envelope(intervals).in_convention(GEN_POS)
        assert all(p.q_baseline_kw < 0.0 for p in tent_view)

    def test_pv_looks_like_positive_power_to_tent(self, pv_resource):
        pd = pytest.importorskip("pandas")
        intervals = Interval.sequence(
            pd.Timestamp("2026-07-15 12:00", tz="US/Pacific").to_pydatetime(), 900, 2
        )
        tent_view = pv_resource.get_envelope(intervals).in_convention(GEN_POS)
        assert all(p.q_baseline_kw > 0.0 for p in tent_view)

    def test_round_trip_preserves_everything(self, intervals):
        envelope = InlineHvac().get_envelope(intervals)
        assert envelope.in_convention(GEN_POS).in_convention(
            SignConvention.LOAD_POSITIVE
        ) == envelope


class TestReconciliation:
    """What TENT's empty get_actual_power() stub needs."""

    def test_deviation_is_measurable(self, intervals):
        resource = InlineHvac()
        interval = intervals[0]
        command = DispatchCommand.for_power(resource.identity, interval, 99.0)
        response = resource.dispatch(command)

        assert response.accepted
        assert response.actual_power_kw == 5.0  # clamped to limits
        assert response.deviation_kw(command) == pytest.approx(5.0 - 99.0)

    def test_settlement_inputs_available(self, replay_resource, intervals):
        result = run_market_cycle(replay_resource, intervals)
        committed = sum(result["cleared"].values())
        delivered = sum(r.actual_power_kw for r in result["responses"])
        assert delivered == pytest.approx(committed)
