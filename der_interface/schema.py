"""The shared vocabulary.

Six data objects. All frozen, all validated on construction, none importing a
simulator.

The keystone is :class:`FlexibilityEnvelope`. Everything else supports it.

Design rule that keeps this reusable
------------------------------------
If a field only makes sense for one device type, it does **not** go in a core
dataclass. It goes in a loosely-typed mapping (``ResourceState.values``,
``Limits.constraints``, ``DispatchCommand.target``).

Battery SOC, indoor air temperature, and tank temperature are all device
specific. Adding them as first-class fields is how this schema would slowly
become a union of every device anyone ever integrated -- which is the exact
problem it exists to solve. The core stays three numbers and a timestamp.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .conventions import (
    Quality,
    SignConvention,
    interval_contains,
    require_aware,
    require_positive_duration,
)
from .errors import SchemaError

__all__ = [
    "Interval",
    "ResourceKind",
    "ResourceIdentity",
    "ResourceState",
    "ForecastPoint",
    "Forecast",
    "Limits",
    "EnvelopePoint",
    "FlexibilityEnvelope",
    "DispatchCommand",
    "DispatchResponse",
]


def _freeze(mapping: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return an immutable shallow copy so frozen dataclasses stay frozen."""
    return MappingProxyType(dict(mapping or {}))


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Interval:
    """A half-open time interval ``[start, start + duration)``.

    Deliberately not tied to any market or simulator interval class. TENT has
    ``TimeInterval``; TESP will have its own. Both can be converted to and from
    this without either depending on the other.
    """

    start: datetime
    duration_s: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", require_aware(self.start, "start"))
        object.__setattr__(
            self, "duration_s", require_positive_duration(self.duration_s, "duration_s")
        )

    @property
    def duration(self) -> timedelta:
        return timedelta(seconds=self.duration_s)

    @property
    def end(self) -> datetime:
        """Exclusive end instant."""
        return self.start + self.duration

    @property
    def hours(self) -> float:
        """Duration in hours. Use this to convert average kW to kWh."""
        return self.duration_s / 3600.0

    def contains(self, moment: datetime) -> bool:
        return interval_contains(self.start, self.duration_s, moment)

    def energy_kwh(self, average_power_kw: float) -> float:
        """Energy implied by holding *average_power_kw* across this interval."""
        return float(average_power_kw) * self.hours

    @classmethod
    def sequence(
        cls, start: datetime, duration_s: int, count: int
    ) -> tuple["Interval", ...]:
        """Build *count* consecutive intervals. Convenience for tests and adapters."""
        if count <= 0:
            raise SchemaError(f"count must be > 0, got {count}")
        require_aware(start, "start")
        step = timedelta(seconds=require_positive_duration(duration_s))
        return tuple(cls(start + step * i, duration_s) for i in range(count))

    def __str__(self) -> str:
        return f"[{self.start.isoformat()} +{self.duration_s}s)"


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


class ResourceKind(enum.Enum):
    """Coarse classification of a resource.

    Used for logging, defaults, and sanity checks -- never for dispatching to
    device-specific code paths inside this package. If something here starts
    branching on ``ResourceKind``, the abstraction has sprung a leak.
    """

    LOAD = "load"
    THERMAL_LOAD = "thermal_load"
    WATER_HEATING = "water_heating"
    BUILDING = "building"
    STORAGE = "storage"
    GENERATION = "generation"
    RENEWABLE_GENERATION = "renewable_generation"
    ELECTRIC_VEHICLE = "electric_vehicle"
    MICROGRID = "microgrid"
    AGGREGATE = "aggregate"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResourceIdentity:
    """Who a resource is and where its numbers came from.

    ``source`` matters for debugging: when an envelope looks wrong, the first
    question is always which adapter produced it.
    """

    resource_id: str
    kind: ResourceKind = ResourceKind.UNKNOWN
    source: str = ""
    location: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.resource_id or not str(self.resource_id).strip():
            raise SchemaError("resource_id must be a non-empty string")
        object.__setattr__(self, "resource_id", str(self.resource_id))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResourceState:
    """What is true about a resource right now.

    ``values`` is intentionally loose. An OCHRE HVAC reports
    ``indoor_temp_c``; a battery reports ``soc``; a meter reports only
    ``power_kw``. Forcing those into one rigid schema would make the schema
    device specific.

    The one convention: if a power value is present it is named ``power_kw``
    and expressed in the canonical (load-positive) sign convention.
    """

    identity: ResourceIdentity
    timestamp: datetime
    values: Mapping[str, Any] = field(default_factory=dict)
    quality: Quality = Quality.VALID

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", require_aware(self.timestamp, "timestamp"))
        object.__setattr__(self, "values", _freeze(self.values))
        if not isinstance(self.quality, Quality):
            raise SchemaError(f"quality must be a Quality, got {self.quality!r}")

    @property
    def resource_id(self) -> str:
        return self.identity.resource_id

    @property
    def power_kw(self) -> float | None:
        """Canonical power, or ``None`` if this resource does not report it."""
        value = self.values.get("power_kw")
        return None if value is None else float(value)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def require(self, key: str) -> Any:
        """Fetch a value or raise a clear error naming the resource.

        Adapters should use this so a missing field produces an actionable
        message rather than a bare ``KeyError``.
        """
        if key not in self.values:
            raise SchemaError(
                f"resource {self.resource_id!r} state has no {key!r}; "
                f"available: {sorted(self.values)}"
            )
        return self.values[key]


# --------------------------------------------------------------------------
# Forecast
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """One interval of a forecast, with optional uncertainty.

    ``sigma`` is here from the start on purpose. TESP's planned design
    propagates forecast uncertainty through its flexibility estimation; TENT's
    information services currently carry point values only. Adding uncertainty
    later would mean touching every producer and consumer, so it is present
    (and simply left at 0.0 when unknown) from day one.
    """

    interval: Interval
    value: float
    sigma: float = 0.0
    quality: Quality = Quality.VALID

    def __post_init__(self) -> None:
        if not isinstance(self.interval, Interval):
            raise SchemaError(f"interval must be an Interval, got {type(self.interval).__name__}")
        object.__setattr__(self, "value", float(self.value))
        sigma = float(self.sigma)
        if sigma < 0.0:
            raise SchemaError(f"sigma must be >= 0, got {sigma}")
        object.__setattr__(self, "sigma", sigma)


@dataclass(frozen=True, slots=True)
class Forecast:
    """A time series of expected values for one named variable.

    Examples of ``variable``: ``solar_power_kw``, ``outdoor_temp_c``,
    ``price_per_kwh``, ``ghi_w_m2``.

    Units live in the variable name. That is blunt, but it means a consumer can
    never silently misinterpret a value, which is worth more than elegance.
    """

    variable: str
    points: tuple[ForecastPoint, ...]
    source: str = ""

    def __post_init__(self) -> None:
        if not self.variable or not str(self.variable).strip():
            raise SchemaError("variable must be a non-empty string")
        points = tuple(self.points)
        for a, b in zip(points, points[1:]):
            if b.interval.start < a.interval.end:
                raise SchemaError(
                    f"forecast {self.variable!r} points must be sorted and "
                    f"non-overlapping; {a.interval} overlaps {b.interval}"
                )
        object.__setattr__(self, "points", points)

    def __iter__(self) -> Iterator[ForecastPoint]:
        return iter(self.points)

    def __len__(self) -> int:
        return len(self.points)

    def at(self, moment: datetime) -> ForecastPoint | None:
        """The point covering *moment*, or ``None`` if outside the horizon."""
        require_aware(moment, "moment")
        for point in self.points:
            if point.interval.contains(moment):
                return point
        return None

    def values(self) -> tuple[float, ...]:
        return tuple(p.value for p in self.points)


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Limits:
    """Hard physical and safety bounds.

    These are facts about equipment, not preferences and not economics. A market
    result may never widen them -- see
    :meth:`FlexibilityEnvelope.assert_within`.

    Kept separate from :class:`FlexibilityEnvelope` because limits are
    instantaneous equipment properties, while an envelope is the interval-by-
    interval consequence of those limits *plus* current state and forecasts. A
    battery's inverter limit is +/-5 kW regardless of time; its envelope at
    3 AM depends on state of charge.
    """

    min_power_kw: float
    max_power_kw: float
    ramp_kw_per_min: float | None = None
    available: bool = True
    sign: SignConvention = SignConvention.LOAD_POSITIVE
    constraints: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        lo, hi = float(self.min_power_kw), float(self.max_power_kw)
        if lo > hi:
            raise SchemaError(f"min_power_kw ({lo}) must be <= max_power_kw ({hi})")
        object.__setattr__(self, "min_power_kw", lo)
        object.__setattr__(self, "max_power_kw", hi)
        if self.ramp_kw_per_min is not None:
            ramp = float(self.ramp_kw_per_min)
            if ramp <= 0.0:
                raise SchemaError(f"ramp_kw_per_min must be > 0 or None, got {ramp}")
            object.__setattr__(self, "ramp_kw_per_min", ramp)
        object.__setattr__(self, "constraints", _freeze(self.constraints))

    @property
    def bidirectional(self) -> bool:
        return self.min_power_kw < 0.0 < self.max_power_kw

    def clamp(self, power_kw: float) -> float:
        """Constrain a power value into these limits."""
        return min(max(float(power_kw), self.min_power_kw), self.max_power_kw)

    def contains(self, power_kw: float, tolerance: float = 1e-9) -> bool:
        return self.min_power_kw - tolerance <= power_kw <= self.max_power_kw + tolerance


# --------------------------------------------------------------------------
# Flexibility -- the keystone
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnvelopePoint:
    """What a resource can do in one interval. Three numbers.

    - ``q_min_kw`` -- least power it can draw
    - ``q_max_kw`` -- most power it can draw
    - ``q_baseline_kw`` -- what it draws if nobody intervenes

    Every resource type collapses into this shape::

        q_min == q_max           inflexible      (PV, fixed load)
        q_min <  q_max           flexible        (HVAC, water heater)
        q_min <  0 <  q_max      bidirectional   (battery, V2G)

    ``q_baseline_kw`` is not redundant. It is the counterfactual a settlement
    process measures against, and the natural starting point for a bid.
    """

    interval: Interval
    q_min_kw: float
    q_max_kw: float
    q_baseline_kw: float
    sigma_kw: float = 0.0
    quality: Quality = Quality.VALID

    def __post_init__(self) -> None:
        if not isinstance(self.interval, Interval):
            raise SchemaError(f"interval must be an Interval, got {type(self.interval).__name__}")

        lo = float(self.q_min_kw)
        hi = float(self.q_max_kw)
        base = float(self.q_baseline_kw)

        if lo > hi:
            raise SchemaError(
                f"{self.interval}: q_min_kw ({lo}) must be <= q_max_kw ({hi})"
            )
        if not (lo - 1e-9 <= base <= hi + 1e-9):
            raise SchemaError(
                f"{self.interval}: q_baseline_kw ({base}) must lie within "
                f"[{lo}, {hi}]. A baseline outside the feasible range means the "
                f"adapter's physics and its limits disagree."
            )

        sigma = float(self.sigma_kw)
        if sigma < 0.0:
            raise SchemaError(f"sigma_kw must be >= 0, got {sigma}")

        object.__setattr__(self, "q_min_kw", lo)
        object.__setattr__(self, "q_max_kw", hi)
        object.__setattr__(self, "q_baseline_kw", base)
        object.__setattr__(self, "sigma_kw", sigma)

    @property
    def flexible(self) -> bool:
        return self.q_max_kw > self.q_min_kw

    @property
    def range_kw(self) -> float:
        """Width of the feasible band. Zero for must-take resources."""
        return self.q_max_kw - self.q_min_kw

    @property
    def bidirectional(self) -> bool:
        return self.q_min_kw < 0.0 < self.q_max_kw

    @property
    def up_flex_kw(self) -> float:
        """Headroom to increase power above baseline."""
        return self.q_max_kw - self.q_baseline_kw

    @property
    def down_flex_kw(self) -> float:
        """Headroom to decrease power below baseline."""
        return self.q_baseline_kw - self.q_min_kw

    def clamp(self, power_kw: float) -> float:
        return min(max(float(power_kw), self.q_min_kw), self.q_max_kw)

    def contains(self, power_kw: float, tolerance: float = 1e-9) -> bool:
        return self.q_min_kw - tolerance <= power_kw <= self.q_max_kw + tolerance

    def negated(self) -> "EnvelopePoint":
        """This point with the sign of every power value reversed.

        Note the min/max **swap**: negating an interval's endpoints reverses
        their order, so the old maximum becomes the new minimum. This is the step
        most easily botched by hand, which is why it lives here.

        An ``EnvelopePoint`` does not carry a sign convention -- only the
        enclosing :class:`FlexibilityEnvelope` does. So this method is a pure
        negation, and deciding *whether* to negate is the envelope's job. Adding
        ``0.0`` normalizes ``-0.0`` to ``0.0`` so round-tripping compares equal.
        """
        return replace(
            self,
            q_min_kw=-self.q_max_kw + 0.0,
            q_max_kw=-self.q_min_kw + 0.0,
            q_baseline_kw=-self.q_baseline_kw + 0.0,
        )


@dataclass(frozen=True, slots=True)
class FlexibilityEnvelope:
    """A resource's feasible operating band across a horizon.

    This is the object the whole package exists to produce. A market consumes
    it; a physical model produces it; neither needs to know about the other.
    """

    identity: ResourceIdentity
    points: tuple[EnvelopePoint, ...]
    sign: SignConvention = SignConvention.LOAD_POSITIVE

    def __post_init__(self) -> None:
        points = tuple(self.points)
        if not points:
            raise SchemaError(
                f"envelope for {self.identity.resource_id!r} has no points; "
                f"an empty envelope cannot be bid into a market"
            )
        for a, b in zip(points, points[1:]):
            if b.interval.start < a.interval.end:
                raise SchemaError(
                    f"envelope for {self.identity.resource_id!r} has overlapping "
                    f"or unsorted intervals: {a.interval} then {b.interval}"
                )
        object.__setattr__(self, "points", points)

    # -- access ------------------------------------------------------------

    def __iter__(self) -> Iterator[EnvelopePoint]:
        return iter(self.points)

    def __len__(self) -> int:
        return len(self.points)

    def __getitem__(self, index: int) -> EnvelopePoint:
        return self.points[index]

    @property
    def resource_id(self) -> str:
        return self.identity.resource_id

    @property
    def start(self) -> datetime:
        return self.points[0].interval.start

    @property
    def end(self) -> datetime:
        return self.points[-1].interval.end

    @property
    def intervals(self) -> tuple[Interval, ...]:
        return tuple(p.interval for p in self.points)

    def at(self, moment: datetime) -> EnvelopePoint | None:
        require_aware(moment, "moment")
        for point in self.points:
            if point.interval.contains(moment):
                return point
        return None

    def for_interval(self, interval: Interval) -> EnvelopePoint | None:
        """Exact-match lookup. Returns ``None`` rather than raising."""
        for point in self.points:
            if point.interval == interval:
                return point
        return None

    # -- properties --------------------------------------------------------

    @property
    def flexible(self) -> bool:
        """True if any interval offers a non-zero range."""
        return any(p.flexible for p in self.points)

    @property
    def baseline_energy_kwh(self) -> float:
        return sum(p.interval.energy_kwh(p.q_baseline_kw) for p in self.points)

    def energy_bounds_kwh(self) -> tuple[float, float]:
        """Total energy if held at ``q_min`` throughout, and at ``q_max``.

        These are *envelope* bounds only. Resources with cross-interval state
        coupling -- a battery cannot sit at ``q_max`` all day without filling --
        must not treat these as jointly achievable. Enforcing that coupling is
        the adapter's job, and is precisely why storage is the last adapter to
        write rather than the first.
        """
        lo = sum(p.interval.energy_kwh(p.q_min_kw) for p in self.points)
        hi = sum(p.interval.energy_kwh(p.q_max_kw) for p in self.points)
        return lo, hi

    # -- conversion and checking ------------------------------------------

    def in_convention(self, sign: SignConvention) -> "FlexibilityEnvelope":
        """Re-express the whole envelope in another sign convention.

        This is the method a TENT adapter calls, since TENT is
        generation-positive while this package is load-positive.
        """
        if sign is self.sign:
            return self
        return FlexibilityEnvelope(
            identity=self.identity,
            points=tuple(p.negated() for p in self.points),
            sign=sign,
        )

    def assert_within(self, limits: Limits) -> None:
        """Verify no interval exceeds hard physical limits.

        A market must never be able to widen physics. Call this after building
        an envelope and before bidding it.
        """
        if limits.sign is not self.sign:
            raise SchemaError(
                f"cannot compare envelope in {self.sign.value} against limits in "
                f"{limits.sign.value}; normalize one first"
            )
        for point in self.points:
            if not limits.contains(point.q_min_kw) or not limits.contains(point.q_max_kw):
                raise SchemaError(
                    f"{self.resource_id!r} {point.interval}: envelope "
                    f"[{point.q_min_kw}, {point.q_max_kw}] exceeds limits "
                    f"[{limits.min_power_kw}, {limits.max_power_kw}]"
                )

    # -- construction helpers ---------------------------------------------

    @classmethod
    def inflexible(
        cls,
        identity: ResourceIdentity,
        intervals: Sequence[Interval],
        powers_kw: Iterable[float],
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
    ) -> "FlexibilityEnvelope":
        """Build a must-take envelope where ``q_min == q_max == q_baseline``.

        The common case for PV, fixed load, and any measured-only resource.
        """
        powers = tuple(float(p) for p in powers_kw)
        if len(powers) != len(intervals):
            raise SchemaError(
                f"got {len(intervals)} intervals but {len(powers)} power values"
            )
        return cls(
            identity=identity,
            points=tuple(
                EnvelopePoint(interval, p, p, p)
                for interval, p in zip(intervals, powers)
            ),
            sign=sign,
        )


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DispatchCommand:
    """A request for a resource to operate a particular way.

    ``target`` is a mapping rather than a single ``power_kw`` float because
    simulators and devices accept different things. OCHRE and GridLAB-D take
    thermostat setpoints; a battery inverter takes power; an EVSE takes a
    current limit. Translating a market's power target into the device's native
    control is the adapter's job -- that translation is exactly what an adapter
    is *for*.
    """

    identity: ResourceIdentity
    interval: Interval
    target: Mapping[str, Any]
    sign: SignConvention = SignConvention.LOAD_POSITIVE
    source: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.interval, Interval):
            raise SchemaError(f"interval must be an Interval, got {type(self.interval).__name__}")
        target = dict(self.target or {})
        if not target:
            raise SchemaError(
                "target must contain at least one entry, e.g. {'power_kw': 3.5} "
                "or {'setpoint_c': 21.0}"
            )
        object.__setattr__(self, "target", _freeze(target))

    @property
    def resource_id(self) -> str:
        return self.identity.resource_id

    @property
    def power_kw(self) -> float | None:
        value = self.target.get("power_kw")
        return None if value is None else float(value)

    @classmethod
    def for_power(
        cls,
        identity: ResourceIdentity,
        interval: Interval,
        power_kw: float,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        source: str = "",
    ) -> "DispatchCommand":
        return cls(identity, interval, {"power_kw": float(power_kw)}, sign, source)


@dataclass(frozen=True, slots=True)
class DispatchResponse:
    """What actually happened.

    This object is the reason the package is not read-only. TENT's
    ``ReconciliationMarket`` has ``get_actual_power()`` as an empty stub today;
    a settlement process cannot exist without measured actuals.

    ``accepted=True`` with ``actual`` differing from the command is a normal,
    expected outcome -- a device tracks imperfectly. That is a reconciliation
    input, not an error. Only outright refusal raises
    :class:`~der_interface.errors.DispatchRejected`.
    """

    identity: ResourceIdentity
    interval: Interval
    accepted: bool
    actual: Mapping[str, Any] = field(default_factory=dict)
    state: ResourceState | None = None
    violations: tuple[str, ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "actual", _freeze(self.actual))
        object.__setattr__(self, "violations", tuple(self.violations))

    @property
    def resource_id(self) -> str:
        return self.identity.resource_id

    @property
    def actual_power_kw(self) -> float | None:
        value = self.actual.get("power_kw")
        return None if value is None else float(value)

    def deviation_kw(self, command: DispatchCommand) -> float | None:
        """Signed ``actual - commanded`` power, or ``None`` if not comparable."""
        commanded = command.power_kw
        actual = self.actual_power_kw
        if commanded is None or actual is None:
            return None
        return actual - commanded
