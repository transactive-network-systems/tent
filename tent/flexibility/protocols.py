"""The four verbs a resource might support.

These are :class:`typing.Protocol` classes, checked structurally. An adapter
does **not** subclass them -- it just has the right methods. That matters because
it means an OCHRE wrapper, a GridLAB-D wrapper, and a vendor SDK wrapper can all
satisfy the contract without inheriting from anything in this package, and
without this package appearing in their dependency graph.

Not every resource supports every verb, and that is deliberate::

                     state  forecast  flexibility  dispatch
    pvlib PV           -       yes        yes          -
    weather API        -       yes         -           -
    OCHRE HVAC        yes     partly      yes         yes
    GridLAB-D house   yes     via player  yes         yes
    real BMS          yes       -         yes         yes
    replay/CSV        yes      yes        yes       no-op

You cannot dispatch the sun. Rather than giving pvlib a ``dispatch()`` that
raises, it simply does not implement :class:`DispatchTarget`, and
:class:`Capabilities` makes that queryable up front.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .schema import (
    DispatchCommand,
    DispatchResponse,
    FlexibilityEnvelope,
    Forecast,
    Interval,
    Limits,
    ResourceIdentity,
    ResourceState,
)

__all__ = [
    "Capabilities",
    "StateProvider",
    "ForecastProvider",
    "FlexibilityProvider",
    "DispatchTarget",
    "ResourceAdapter",
    "capabilities_of",
]


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a resource can be asked to do.

    Prefer checking this over catching
    :class:`~der_interface.errors.CapabilityError`. A market should discover at
    configuration time that a resource is not dispatchable, not at delivery time.
    """

    state: bool = False
    forecast: bool = False
    flexibility: bool = False
    dispatch: bool = False

    @property
    def controllable(self) -> bool:
        return self.dispatch

    @property
    def biddable(self) -> bool:
        """Enough to participate in a market, even if only as a must-take."""
        return self.flexibility


@runtime_checkable
class StateProvider(Protocol):
    """Reports what is true about a resource right now.

    Implemented by anything with sensors or simulated state: OCHRE, GridLAB-D,
    a real meter, a replay file.
    """

    @property
    def identity(self) -> ResourceIdentity: ...

    def get_state(self, timestamp: datetime | None = None) -> ResourceState:
        """Current state.

        Args:
            timestamp: Point of interest. ``None`` means "now". Implementations
                backed by a time series may interpolate or return the nearest
                sample; they should mark the result
                :attr:`~der_interface.conventions.Quality.ESTIMATED` when they do.
        """
        ...

    def get_limits(self, timestamp: datetime | None = None) -> Limits:
        """Hard physical bounds at *timestamp*.

        May vary with conditions -- a battery's charge limit falls at low
        temperature -- but never with price or preference.
        """
        ...


@runtime_checkable
class ForecastProvider(Protocol):
    """Reports what will be true later.

    Implemented by pvlib, weather services, price forecast services, and learned
    load models.
    """

    def available_forecasts(self) -> tuple[str, ...]:
        """Variable names this provider can supply, e.g. ``("solar_power_kw",)``."""
        ...

    def get_forecast(
        self,
        variable: str,
        intervals: Sequence[Interval],
    ) -> Forecast:
        """Forecast of *variable* over *intervals*.

        Takes explicit intervals rather than start/end/resolution so the caller
        controls alignment. A market has its own interval boundaries and it must
        not have to resample to use a forecast.

        Raises:
            CapabilityError: If *variable* is not in
                :meth:`available_forecasts`.
        """
        ...


@runtime_checkable
class FlexibilityProvider(Protocol):
    """Reports what a resource *can do* per interval. The keystone verb.

    This is the only protocol a resource must implement to participate in a
    market. Everything else is optional enrichment.
    """

    @property
    def identity(self) -> ResourceIdentity: ...

    def get_envelope(
        self,
        intervals: Sequence[Interval],
        context: Mapping[str, Any] | None = None,
    ) -> FlexibilityEnvelope:
        """Feasible operating band for each of *intervals*.

        The returned envelope must have one point per requested interval, in the
        same order, so callers can safely ``zip``.

        Args:
            intervals: Market or planning intervals. Must be sorted and
                non-overlapping.
            context: Optional hints -- forecasts already fetched, customer
                preference parameters, reserve requirements. Adapters should
                work sensibly when this is ``None``; anything genuinely required
                belongs in the constructor instead.
        """
        ...


@runtime_checkable
class DispatchTarget(Protocol):
    """Accepts commands and reports what actually happened.

    Implemented by anything controllable. Note that an adapter's ``dispatch``
    is where a market's power target gets translated into the device's native
    control -- a setpoint, a duty cycle, a current limit. That translation is
    the whole reason adapters exist.
    """

    @property
    def identity(self) -> ResourceIdentity: ...

    def dispatch(self, command: DispatchCommand) -> DispatchResponse:
        """Apply *command* and report the outcome.

        Implementations must clamp to physical limits rather than trusting the
        command, and record any clamping in
        :attr:`~der_interface.schema.DispatchResponse.violations`. A market
        result can never widen physics.

        Returning ``accepted=True`` with ``actual`` differing from the target is
        normal and expected -- devices track imperfectly, and that difference is
        a reconciliation input. Raise
        :class:`~der_interface.errors.DispatchRejected` only for outright
        refusal: malformed command, offline device, safety interlock.
        """
        ...

    def release(self, interval: Interval) -> DispatchResponse:
        """Relinquish control and return the resource to autonomous operation.

        Called when a market ends, an override expires, or an agent shuts down.
        Deployed systems need this: a building whose setpoints are left pinned
        by a crashed agent is a real failure mode.
        """
        ...


@runtime_checkable
class ResourceAdapter(
    StateProvider, ForecastProvider, FlexibilityProvider, DispatchTarget, Protocol
):
    """The full contract, for adapters that support everything.

    Mostly a convenience for type annotations. Do not force an adapter to
    satisfy this -- honest partial support is better than stub methods that
    raise.
    """


def capabilities_of(obj: object) -> Capabilities:
    """Detect which protocols *obj* structurally satisfies.

    Uses duck-typed attribute checks rather than ``isinstance``, because
    ``runtime_checkable`` protocols only verify method *names* and would report
    false positives for objects that happen to share them.
    """

    def has(*names: str) -> bool:
        return all(callable(getattr(obj, name, None)) for name in names)

    return Capabilities(
        state=has("get_state", "get_limits"),
        forecast=has("get_forecast", "available_forecasts"),
        flexibility=has("get_envelope"),
        dispatch=has("dispatch", "release"),
    )
