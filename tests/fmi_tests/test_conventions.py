"""Sign convention tests.

The most important tests in the package. TENT is generation-positive; GridLAB-D,
OCHRE, and pvlib are load-positive. A sign error here produces markets that clear
backwards while looking plausible in aggregate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tent.flexibility import Quality, SignConvention
from tent.flexibility.conventions import (
    interval_contains,
    require_aware,
    require_positive_duration,
)

UTC = timezone.utc
LOAD_POS = SignConvention.LOAD_POSITIVE
GEN_POS = SignConvention.GENERATION_POSITIVE


class TestSignConvention:
    def test_load_positive_is_canonical(self):
        assert LOAD_POS.canonical is LOAD_POS
        assert GEN_POS.canonical is LOAD_POS

    def test_to_canonical_identity_for_load_positive(self):
        assert LOAD_POS.to_canonical(2.5) == 2.5
        assert LOAD_POS.to_canonical(-2.5) == -2.5

    def test_to_canonical_flips_generation_positive(self):
        assert GEN_POS.to_canonical(2.5) == -2.5
        assert GEN_POS.to_canonical(-2.5) == 2.5

    @pytest.mark.parametrize("sign", [LOAD_POS, GEN_POS])
    @pytest.mark.parametrize("value", [0.0, 1.0, -1.0, 3.7, -12345.6])
    def test_round_trip_is_lossless(self, sign, value):
        """The property that actually matters: converting out and back is exact."""
        assert sign.from_canonical(sign.to_canonical(value)) == value

    def test_consumption_under_each_convention(self):
        assert LOAD_POS.consumption(4.0) == 4.0
        assert GEN_POS.consumption(4.0) == -4.0

    def test_generation_under_each_convention(self):
        """The exact conversion a PV adapter performs."""
        assert LOAD_POS.generation(3.5) == -3.5
        assert GEN_POS.generation(3.5) == 3.5

    def test_generation_and_consumption_have_opposite_signs(self):
        for sign in (LOAD_POS, GEN_POS):
            assert sign.generation(5.0) == -sign.consumption(5.0)

    def test_magnitude_helpers_reject_negative_input(self):
        with pytest.raises(ValueError, match="must be >= 0"):
            LOAD_POS.generation(-1.0)
        with pytest.raises(ValueError, match="must be >= 0"):
            LOAD_POS.consumption(-1.0)

    def test_zero_is_unambiguous(self):
        for sign in (LOAD_POS, GEN_POS):
            assert sign.generation(0.0) == 0.0
            assert sign.consumption(0.0) == 0.0


class TestQuality:
    def test_usable_qualities(self):
        assert Quality.VALID.usable
        assert Quality.STALE.usable
        assert Quality.ESTIMATED.usable

    def test_unusable_qualities(self):
        assert not Quality.MISSING.usable
        assert not Quality.INVALID.usable

    def test_missing_is_distinct_from_zero_reading(self):
        """The reason Quality exists: offline != reading zero."""
        assert Quality.MISSING is not Quality.VALID


class TestRequireAware:
    def test_accepts_aware(self):
        moment = datetime(2026, 8, 5, tzinfo=UTC)
        assert require_aware(moment) is moment

    def test_rejects_naive(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            require_aware(datetime(2026, 8, 5))

    def test_rejects_non_datetime(self):
        with pytest.raises(TypeError, match="must be a datetime"):
            require_aware("2026-08-05")

    def test_error_names_the_field(self):
        with pytest.raises(ValueError, match="start"):
            require_aware(datetime(2026, 8, 5), "start")


class TestRequirePositiveDuration:
    def test_accepts_positive_int(self):
        assert require_positive_duration(900) == 900

    def test_accepts_timedelta(self):
        assert require_positive_duration(timedelta(minutes=15)) == 900

    def test_rejects_zero(self):
        """Zero-length intervals make average power meaningless."""
        with pytest.raises(ValueError, match="must be > 0"):
            require_positive_duration(0)

    def test_rejects_negative(self):
        with pytest.raises(ValueError, match="must be > 0"):
            require_positive_duration(-900)

    def test_rejects_fractional_seconds(self):
        with pytest.raises(ValueError, match="whole number"):
            require_positive_duration(900.5)


class TestIntervalContains:
    def test_start_is_inclusive(self):
        start = datetime(2026, 8, 5, 14, tzinfo=UTC)
        assert interval_contains(start, 900, start)

    def test_end_is_exclusive(self):
        """Back-to-back intervals must not both claim the boundary instant."""
        start = datetime(2026, 8, 5, 14, tzinfo=UTC)
        assert not interval_contains(start, 900, start + timedelta(seconds=900))

    def test_midpoint_included(self):
        start = datetime(2026, 8, 5, 14, tzinfo=UTC)
        assert interval_contains(start, 900, start + timedelta(seconds=450))

    def test_before_excluded(self):
        start = datetime(2026, 8, 5, 14, tzinfo=UTC)
        assert not interval_contains(start, 900, start - timedelta(seconds=1))
