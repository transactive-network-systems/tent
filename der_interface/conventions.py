"""Units, sign conventions, and time rules.

This module exists because of one specific bug waiting to happen.

TENT's ``LocalAsset.cost()`` documents its power sign convention as::

    imported and generated power is positive
    exported or consumed power is negative

GridLAB-D, OCHRE, and pvlib all use the opposite: **load is positive**.

If an adapter forgets to normalize, the numbers are silently inverted. Nothing
raises. A market clears backwards and the result looks plausible in aggregate.
That class of bug can survive for months.

So every adapter declares the convention its upstream source uses, and
normalization happens exactly once, at the boundary, via :class:`SignConvention`.

Rules enforced elsewhere in this package
----------------------------------------
- Power is always kW. Energy is always kWh. Never W, never MW.
- Timestamps are always timezone-aware. Naive datetimes are rejected.
- Intervals are start-inclusive, end-exclusive: ``[start, start + duration)``.
"""

from __future__ import annotations

import enum
from datetime import datetime, timedelta, timezone

__all__ = [
    "SignConvention",
    "Quality",
    "POWER_UNIT",
    "ENERGY_UNIT",
    "require_aware",
    "require_positive_duration",
    "interval_contains",
]


# Unit strings, exported so adapters and consumers can assert against them
# rather than hardcoding their own.
POWER_UNIT = "kW"
ENERGY_UNIT = "kWh"


class SignConvention(enum.Enum):
    """Which direction of power flow is positive.

    Declare the convention of the *source* you are adapting, then use
    :meth:`to_canonical` on every power value crossing the boundary.

    The canonical convention inside this package is :attr:`LOAD_POSITIVE`,
    chosen because it matches GridLAB-D, OCHRE, pvlib, and the majority of
    metering equipment. TENT is the outlier and converts on its own boundary.
    """

    #: Consumption positive, generation negative.
    #: Used by GridLAB-D, OCHRE, pvlib, most meters, most inverters.
    LOAD_POSITIVE = "load_positive"

    #: Generation positive, consumption negative.
    #: Used by TENT (see ``LocalAsset.cost``).
    GENERATION_POSITIVE = "generation_positive"

    @property
    def canonical(self) -> "SignConvention":
        """The convention used internally by this package."""
        return SignConvention.LOAD_POSITIVE

    def to_canonical(self, power: float) -> float:
        """Convert a power value from this convention to the canonical one.

        >>> SignConvention.LOAD_POSITIVE.to_canonical(2.5)
        2.5
        >>> SignConvention.GENERATION_POSITIVE.to_canonical(2.5)
        -2.5
        """
        if self is SignConvention.LOAD_POSITIVE:
            return float(power)
        return -float(power)

    def from_canonical(self, power: float) -> float:
        """Convert a canonical power value into this convention.

        Involutive with :meth:`to_canonical`.

        >>> SignConvention.GENERATION_POSITIVE.from_canonical(-2.5)
        2.5
        """
        if self is SignConvention.LOAD_POSITIVE:
            return float(power)
        return -float(power)

    def consumption(self, magnitude: float) -> float:
        """Express a non-negative *consumption* magnitude in this convention."""
        if magnitude < 0.0:
            raise ValueError(f"consumption magnitude must be >= 0, got {magnitude}")
        value = float(magnitude)
        # ``+ 0.0`` normalizes -0.0 to 0.0 so equality and formatting behave.
        return value if self is SignConvention.LOAD_POSITIVE else -value + 0.0

    def generation(self, magnitude: float) -> float:
        """Express a non-negative *generation* magnitude in this convention.

        This is the method a PV adapter should use. pvlib reports generation as
        a positive number; under ``LOAD_POSITIVE`` that must become negative.

        >>> SignConvention.LOAD_POSITIVE.generation(3.5)
        -3.5
        >>> SignConvention.LOAD_POSITIVE.generation(0.0)
        0.0
        """
        if magnitude < 0.0:
            raise ValueError(f"generation magnitude must be >= 0, got {magnitude}")
        value = float(magnitude)
        return -value + 0.0 if self is SignConvention.LOAD_POSITIVE else value


class Quality(enum.Enum):
    """Trustworthiness of a reported value.

    Present because "the sensor is offline" and "the sensor reads zero" are
    different facts, and collapsing them into ``0.0`` loses information a
    market needs. Absent data must be representable without being silently
    coerced to a number.
    """

    #: Measured or computed normally.
    VALID = "valid"

    #: Older than expected but probably still usable.
    STALE = "stale"

    #: Filled by interpolation, forecast, or a default. Not measured.
    ESTIMATED = "estimated"

    #: No value available. Consumers must not treat the payload as a reading.
    MISSING = "missing"

    #: Present but known bad (failed range check, sensor fault).
    INVALID = "invalid"

    @property
    def usable(self) -> bool:
        """Whether a consumer may reasonably act on the value."""
        return self in (Quality.VALID, Quality.STALE, Quality.ESTIMATED)


def require_aware(value: datetime, field_name: str = "timestamp") -> datetime:
    """Return *value* if timezone-aware, else raise.

    Naive datetimes are rejected rather than assumed to be UTC or local. In a
    co-simulation spanning multiple timezones, guessing is how intervals end up
    misaligned by hours in ways that only show up in aggregate results.
    """
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"{field_name} must be timezone-aware; got naive {value!r}. "
            f"Use datetime(..., tzinfo=timezone.utc) or a zoneinfo tz."
        )
    return value


def require_positive_duration(seconds: int | float, field_name: str = "duration_s") -> int:
    """Return *seconds* as a positive int, else raise.

    Zero-length intervals are rejected: an interval with no duration cannot
    carry an average power, which makes every downstream energy calculation
    ambiguous.
    """
    if isinstance(seconds, timedelta):
        seconds = seconds.total_seconds()
    if seconds is None:
        raise TypeError(f"{field_name} is required")
    value = int(seconds)
    if value != seconds:
        raise ValueError(f"{field_name} must be a whole number of seconds, got {seconds}")
    if value <= 0:
        raise ValueError(f"{field_name} must be > 0, got {value}")
    return value


def interval_contains(start: datetime, duration_s: int, moment: datetime) -> bool:
    """Whether *moment* falls in ``[start, start + duration_s)``.

    End-exclusive, so back-to-back intervals never both claim the boundary
    instant.
    """
    require_aware(start, "start")
    require_aware(moment, "moment")
    return start <= moment < start + timedelta(seconds=duration_s)


def utc_now() -> datetime:
    """Timezone-aware current time. Convenience so callers avoid naive ``now()``."""
    return datetime.now(timezone.utc)
