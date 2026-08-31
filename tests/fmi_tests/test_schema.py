"""Schema tests: validation, immutability, and sign conversion."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from der_interface import (
    DispatchCommand,
    DispatchResponse,
    EnvelopePoint,
    FlexibilityEnvelope,
    Forecast,
    ForecastPoint,
    Interval,
    Limits,
    Quality,
    ResourceIdentity,
    ResourceKind,
    ResourceState,
    SchemaError,
    SignConvention,
)

UTC = timezone.utc
LOAD_POS = SignConvention.LOAD_POSITIVE
GEN_POS = SignConvention.GENERATION_POSITIVE


class TestInterval:
    def test_basic_properties(self, t0):
        interval = Interval(t0, 900)
        assert interval.duration == timedelta(seconds=900)
        assert interval.end == t0 + timedelta(minutes=15)
        assert interval.hours == 0.25

    def test_rejects_naive_start(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            Interval(datetime(2026, 8, 5, 14), 900)

    def test_rejects_zero_duration(self, t0):
        with pytest.raises(ValueError, match="must be > 0"):
            Interval(t0, 0)

    def test_energy_conversion(self, t0):
        """4 kW held for 15 minutes is 1 kWh."""
        assert Interval(t0, 900).energy_kwh(4.0) == 1.0

    def test_energy_conversion_signed(self, t0):
        """Generation is negative under load-positive, so energy is too."""
        assert Interval(t0, 900).energy_kwh(-4.0) == -1.0

    def test_sequence_is_contiguous(self, t0):
        intervals = Interval.sequence(t0, 900, 4)
        assert len(intervals) == 4
        for a, b in zip(intervals, intervals[1:]):
            assert a.end == b.start

    def test_sequence_rejects_zero_count(self, t0):
        with pytest.raises(SchemaError, match="count must be > 0"):
            Interval.sequence(t0, 900, 0)

    def test_frozen(self, t0):
        with pytest.raises(FrozenInstanceError):
            Interval(t0, 900).duration_s = 1800

    def test_hashable_and_comparable(self, t0):
        assert Interval(t0, 900) == Interval(t0, 900)
        assert len({Interval(t0, 900), Interval(t0, 900)}) == 1


class TestResourceIdentity:
    def test_requires_non_empty_id(self):
        with pytest.raises(SchemaError, match="non-empty"):
            ResourceIdentity("")
        with pytest.raises(SchemaError, match="non-empty"):
            ResourceIdentity("   ")

    def test_metadata_is_immutable(self):
        identity = ResourceIdentity("x", metadata={"a": 1})
        with pytest.raises(TypeError):
            identity.metadata["b"] = 2

    def test_metadata_copied_not_aliased(self):
        source = {"a": 1}
        identity = ResourceIdentity("x", metadata=source)
        source["b"] = 2
        assert "b" not in identity.metadata


class TestResourceState:
    def test_power_accessor(self, hvac_identity, t0):
        state = ResourceState(hvac_identity, t0, {"power_kw": 2.8})
        assert state.power_kw == 2.8

    def test_power_none_when_absent(self, hvac_identity, t0):
        """A resource that reports no power must not appear to report zero."""
        assert ResourceState(hvac_identity, t0, {"indoor_temp_c": 22.0}).power_kw is None

    def test_require_names_resource_and_available_keys(self, hvac_identity, t0):
        state = ResourceState(hvac_identity, t0, {"power_kw": 1.0})
        with pytest.raises(SchemaError) as exc:
            state.require("soc")
        assert "house_12_hvac" in str(exc.value)
        assert "power_kw" in str(exc.value)

    def test_device_specific_values_allowed(self, hvac_identity, t0):
        """Device-specific fields live in `values`, never in the dataclass."""
        state = ResourceState(
            hvac_identity, t0, {"indoor_temp_c": 22.4, "mode": "cooling", "soc": 0.6}
        )
        assert state.get("mode") == "cooling"
        assert state.get("missing", "fallback") == "fallback"

    def test_rejects_bad_quality(self, hvac_identity, t0):
        with pytest.raises(SchemaError, match="must be a Quality"):
            ResourceState(hvac_identity, t0, {}, quality="valid")


class TestLimits:
    def test_rejects_inverted_bounds(self):
        with pytest.raises(SchemaError, match="must be <="):
            Limits(min_power_kw=5.0, max_power_kw=1.0)

    def test_clamp(self):
        limits = Limits(0.0, 4.2)
        assert limits.clamp(10.0) == 4.2
        assert limits.clamp(-10.0) == 0.0
        assert limits.clamp(2.0) == 2.0

    def test_bidirectional_detection(self):
        assert Limits(-5.0, 5.0).bidirectional
        assert not Limits(0.0, 5.0).bidirectional
        assert not Limits(-5.0, 0.0).bidirectional

    def test_rejects_non_positive_ramp(self):
        with pytest.raises(SchemaError, match="must be > 0 or None"):
            Limits(0.0, 5.0, ramp_kw_per_min=0.0)


class TestEnvelopePoint:
    def test_rejects_inverted_range(self, t0):
        with pytest.raises(SchemaError, match="q_min_kw .* must be <= q_max_kw"):
            EnvelopePoint(Interval(t0, 900), 5.0, 1.0, 3.0)

    def test_rejects_baseline_outside_range(self, t0):
        """Catches an adapter whose physics and limits disagree."""
        with pytest.raises(SchemaError, match="must lie within"):
            EnvelopePoint(Interval(t0, 900), 0.0, 4.0, 9.0)

    def test_flexibility_headroom(self, t0):
        point = EnvelopePoint(Interval(t0, 900), 0.0, 4.0, 3.0)
        assert point.up_flex_kw == 1.0
        assert point.down_flex_kw == 3.0
        assert point.range_kw == 4.0
        assert point.flexible

    def test_must_take_is_zero_width(self, t0):
        """PV and fixed load: valid, not a special case."""
        point = EnvelopePoint(Interval(t0, 900), -3.5, -3.5, -3.5)
        assert not point.flexible
        assert point.range_kw == 0.0
        assert point.up_flex_kw == 0.0

    def test_bidirectional_detection(self, t0):
        assert EnvelopePoint(Interval(t0, 900), -5.0, 5.0, 0.0).bidirectional
        assert not EnvelopePoint(Interval(t0, 900), 0.0, 5.0, 1.0).bidirectional

    def test_clamp_and_contains(self, t0):
        point = EnvelopePoint(Interval(t0, 900), 0.0, 4.0, 2.0)
        assert point.clamp(99.0) == 4.0
        assert point.contains(4.0)
        assert not point.contains(4.1)

    def test_negated_swaps_min_and_max(self, t0):
        """The step most easily botched by hand.

        Negating an interval's endpoints reverses their order.
        """
        point = EnvelopePoint(Interval(t0, 900), q_min_kw=0.0, q_max_kw=4.2, q_baseline_kw=2.8)
        flipped = point.negated()
        assert flipped.q_min_kw == -4.2
        assert flipped.q_max_kw == 0.0
        assert flipped.q_baseline_kw == -2.8

    def test_negated_round_trip(self, t0):
        point = EnvelopePoint(Interval(t0, 900), 0.0, 4.2, 2.8)
        assert point.negated().negated() == point

    def test_negated_normalizes_negative_zero(self, t0):
        """-0.0 must not leak out, or round-trip equality breaks."""
        point = EnvelopePoint(Interval(t0, 900), 0.0, 4.2, 2.8)
        flipped = point.negated()
        assert flipped.q_max_kw == 0.0
        assert str(flipped.q_max_kw) == "0.0"


class TestFlexibilityEnvelope:
    def test_rejects_empty(self, hvac_identity):
        with pytest.raises(SchemaError, match="no points"):
            FlexibilityEnvelope(hvac_identity, ())

    def test_rejects_overlapping_intervals(self, hvac_identity, t0):
        a = EnvelopePoint(Interval(t0, 900), 0.0, 4.0, 2.0)
        b = EnvelopePoint(Interval(t0 + timedelta(seconds=450), 900), 0.0, 4.0, 2.0)
        with pytest.raises(SchemaError, match="overlapping"):
            FlexibilityEnvelope(hvac_identity, (a, b))

    def test_sequence_access(self, hvac_envelope):
        assert len(hvac_envelope) == 4
        assert hvac_envelope[0].q_baseline_kw == 2.8
        assert len(list(hvac_envelope)) == 4

    def test_horizon_bounds(self, hvac_envelope, t0):
        assert hvac_envelope.start == t0
        assert hvac_envelope.end == t0 + timedelta(hours=1)

    def test_lookup_by_moment(self, hvac_envelope, t0):
        assert hvac_envelope.at(t0 + timedelta(minutes=20)) is hvac_envelope[1]
        assert hvac_envelope.at(t0 - timedelta(hours=1)) is None

    def test_lookup_by_interval(self, hvac_envelope, intervals):
        assert hvac_envelope.for_interval(intervals[2]) is hvac_envelope[2]
        assert hvac_envelope.for_interval(Interval(intervals[0].start, 1800)) is None

    def test_baseline_energy(self, hvac_envelope):
        """2.8 kW across four 15-minute intervals is 2.8 kWh."""
        assert hvac_envelope.baseline_energy_kwh == pytest.approx(2.8)

    def test_energy_bounds(self, hvac_envelope):
        lo, hi = hvac_envelope.energy_bounds_kwh()
        assert lo == pytest.approx(0.0)
        assert hi == pytest.approx(4.2)

    def test_inflexible_helper(self, hvac_identity, intervals):
        envelope = FlexibilityEnvelope.inflexible(
            hvac_identity, intervals, [-1.0, -2.0, -3.0, -4.0]
        )
        assert not envelope.flexible
        for point in envelope:
            assert point.q_min_kw == point.q_max_kw == point.q_baseline_kw

    def test_inflexible_helper_length_mismatch(self, hvac_identity, intervals):
        with pytest.raises(SchemaError, match="4 intervals but 2 power"):
            FlexibilityEnvelope.inflexible(hvac_identity, intervals, [1.0, 2.0])

    def test_convention_conversion_whole_envelope(self, hvac_envelope):
        """What a TENT adapter calls, since TENT is generation-positive."""
        flipped = hvac_envelope.in_convention(GEN_POS)
        assert flipped.sign is GEN_POS
        assert flipped[0].q_min_kw == -4.2
        assert flipped[0].q_max_kw == 0.0

    def test_convention_round_trip(self, hvac_envelope):
        assert hvac_envelope.in_convention(GEN_POS).in_convention(LOAD_POS) == hvac_envelope

    def test_assert_within_passes(self, hvac_envelope):
        hvac_envelope.assert_within(Limits(0.0, 5.0))

    def test_assert_within_catches_violation(self, hvac_envelope):
        """A market must never be able to widen physics."""
        with pytest.raises(SchemaError, match="exceeds limits"):
            hvac_envelope.assert_within(Limits(0.0, 3.0))

    def test_assert_within_refuses_mismatched_conventions(self, hvac_envelope):
        """Comparing across conventions silently would be worse than raising."""
        with pytest.raises(SchemaError, match="normalize one first"):
            hvac_envelope.assert_within(Limits(0.0, 5.0, sign=GEN_POS))


class TestForecast:
    def test_rejects_empty_variable(self, t0):
        with pytest.raises(SchemaError, match="non-empty"):
            Forecast("", (ForecastPoint(Interval(t0, 900), 1.0),))

    def test_rejects_overlapping_points(self, t0):
        a = ForecastPoint(Interval(t0, 900), 1.0)
        b = ForecastPoint(Interval(t0 + timedelta(seconds=450), 900), 2.0)
        with pytest.raises(SchemaError, match="overlapping"):
            Forecast("temp_c", (a, b))

    def test_rejects_negative_sigma(self, t0):
        with pytest.raises(SchemaError, match="sigma must be >= 0"):
            ForecastPoint(Interval(t0, 900), 1.0, sigma=-0.5)

    def test_lookup_and_values(self, intervals):
        forecast = Forecast(
            "outdoor_temp_c",
            tuple(ForecastPoint(i, 20.0 + n) for n, i in enumerate(intervals)),
        )
        assert forecast.values() == (20.0, 21.0, 22.0, 23.0)
        assert forecast.at(intervals[2].start).value == 22.0

    def test_empty_forecast_allowed(self):
        """Unlike an envelope, an empty forecast is meaningful: nothing known."""
        assert len(Forecast("price_per_kwh", ())) == 0


class TestDispatch:
    def test_requires_non_empty_target(self, hvac_identity, t0):
        with pytest.raises(SchemaError, match="at least one entry"):
            DispatchCommand(hvac_identity, Interval(t0, 900), {})

    def test_for_power_helper(self, hvac_identity, t0):
        command = DispatchCommand.for_power(hvac_identity, Interval(t0, 900), 3.5)
        assert command.power_kw == 3.5

    def test_setpoint_target_allowed(self, hvac_identity, t0):
        """Adapters translate power to native controls; both shapes are valid."""
        command = DispatchCommand(hvac_identity, Interval(t0, 900), {"setpoint_c": 21.0})
        assert command.power_kw is None
        assert command.target["setpoint_c"] == 21.0

    def test_deviation(self, hvac_identity, t0):
        interval = Interval(t0, 900)
        command = DispatchCommand.for_power(hvac_identity, interval, 3.5)
        response = DispatchResponse(
            hvac_identity, interval, accepted=True, actual={"power_kw": 3.3}
        )
        assert response.deviation_kw(command) == pytest.approx(-0.2)

    def test_deviation_none_when_incomparable(self, hvac_identity, t0):
        interval = Interval(t0, 900)
        command = DispatchCommand(hvac_identity, interval, {"setpoint_c": 21.0})
        response = DispatchResponse(hvac_identity, interval, accepted=True, actual={})
        assert response.deviation_kw(command) is None

    def test_accepted_with_deviation_is_not_an_error(self, hvac_identity, t0):
        """Imperfect tracking is a reconciliation input, not a failure."""
        interval = Interval(t0, 900)
        response = DispatchResponse(
            hvac_identity, interval, accepted=True, actual={"power_kw": 3.3}
        )
        assert response.accepted
        assert response.violations == ()
