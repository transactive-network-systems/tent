"""GridLAB-D Inverter-coupled Battery Storage physical adapter.

Connects to a GridLAB-D `inverter` / `battery` object, querying State-of-Charge (SOC)
and dispatching 4-quadrant active power commands to the inverter.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ...conventions import Quality, SignConvention
from ...errors import AdapterError
from ..storage_envelope import soc_coupled_power_bounds
from ...schema import (
    DispatchCommand,
    DispatchResponse,
    EnvelopePoint,
    FlexibilityEnvelope,
    Interval,
    Limits,
    ResourceIdentity,
    ResourceKind,
    ResourceState,
)
from .engine import GridlabdEngine

_log = logging.getLogger(__name__)


class GridlabdBatteryInverterAdapter:
    """Controls a GridLAB-D battery/inverter object as a flexible storage resource.

    Args:
        identity: Who this resource is.
        object_name: The name of the `inverter` or `battery` object in GridLAB-D.
        engine: GridlabdEngine instance connected to the simulation.
        rated_kw: Rated inverter power capacity (kW).
        capacity_kwh: Battery energy capacity (kWh).
        min_soc: Minimum allowable SOC (default 0.10).
        max_soc: Maximum allowable SOC (default 0.95).
        sign: Sign convention (default LOAD_POSITIVE).
    """

    def __init__(
        self,
        identity: ResourceIdentity,
        object_name: str,
        engine: GridlabdEngine,
        rated_kw: float = 5.0,
        capacity_kwh: float = 13.5,
        min_soc: float = 0.10,
        max_soc: float = 0.95,
        efficiency: float = 0.95,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
    ) -> None:
        self._identity = identity
        self._object_name = object_name
        self._engine = engine
        self._rated_kw = float(rated_kw)
        self._capacity_kwh = float(capacity_kwh)
        self._min_soc = float(min_soc)
        self._max_soc = float(max_soc)
        self._efficiency = float(efficiency)
        self._sign = sign
        self.commands: list[DispatchCommand] = []

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        resource_id: str,
        rated_kw: float = 5.0,
        gridlabd: Mapping[str, Any] | None = None,
        location: Mapping[str, Any] | None = None,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> "GridlabdBatteryInverterAdapter":
        gld_dict = dict(gridlabd or {})
        object_name = gld_dict.pop("object_name", resource_id)
        model_path = gld_dict.pop("model_path", None)
        engine = GridlabdEngine.get_or_create(model_path)

        capacity_kwh = float(gld_dict.pop("capacity_kwh", rated_kw * 2.5))
        rated_power_kw = float(gld_dict.pop("rated_kw", rated_kw))
        min_soc = float(gld_dict.pop("min_soc", 0.10))
        max_soc = float(gld_dict.pop("max_soc", 0.95))
        efficiency = float(gld_dict.pop("efficiency", kwargs.pop("efficiency", 0.95)))

        loc_str = ""
        if location:
            loc_dict = dict(location)
            lat = loc_dict.get("latitude")
            lon = loc_dict.get("longitude")
            if lat is not None and lon is not None:
                loc_str = f"{lat},{lon}"

        identity = ResourceIdentity(
            resource_id=resource_id,
            kind=ResourceKind.STORAGE,
            source=f"gridlabd:{object_name}",
            location=loc_str,
            metadata={
                "object_name": object_name,
                "model_path": str(model_path) if model_path else "",
                "rated_kw": rated_power_kw,
                "capacity_kwh": capacity_kwh,
                **kwargs,
            },
        )

        return cls(
            identity=identity,
            object_name=object_name,
            engine=engine,
            rated_kw=rated_power_kw,
            capacity_kwh=capacity_kwh,
            min_soc=min_soc,
            max_soc=max_soc,
            efficiency=efficiency,
            sign=sign,
        )

    # -- properties --------------------------------------------------------

    @property
    def identity(self) -> ResourceIdentity:
        return self._identity

    @property
    def soc(self) -> float:
        val = self._engine.get_float_property(self._object_name, "battery_soc", default=None)
        if val is not None and val >= 0.0:
            return val
        val = self._engine.get_float_property(self._object_name, "state_of_charge", default=None)
        if val is not None and val >= 0.0:
            return val
        val = self._engine.get_float_property("battery_01", "state_of_charge", default=None)
        if val is not None and val >= 0.0:
            return val
        return self._engine.get_float_property(self._object_name, "SOC", default=0.50)

    @property
    def current_power_kw(self) -> float:
        """Canonical load-positive power in kW.

        GridLAB-D's ``P_Out`` is the inverter's real power output in VA and is
        generation-positive, so it must be negated and scaled to kW.
        """
        va = self._engine.get_float_property(self._object_name, "P_Out", default=0.0)
        return -va / 1000.0

    # -- FlexibilityProvider -----------------------------------------------

    def get_envelope(
        self,
        intervals: Sequence[Interval],
        context: Mapping[str, Any] | None = None,
    ) -> FlexibilityEnvelope:
        if not intervals:
            raise AdapterError("get_envelope requires at least one interval")

        # SOC is coupled across the horizon: charging in an early interval
        # reduces headroom in every later one. See storage_envelope for why a
        # per-interval headroom calculation is not sufficient.
        bounds = soc_coupled_power_bounds(
            intervals,
            soc=self.soc,
            capacity_kwh=self._capacity_kwh,
            rated_kw=self._rated_kw,
            min_soc=self._min_soc,
            max_soc=self._max_soc,
            efficiency=self._efficiency,
        )

        points = []
        for interval, (q_min, q_max) in zip(intervals, bounds):
            if q_min > q_max:
                q_min = q_max

            points.append(EnvelopePoint(interval, q_min_kw=q_min, q_max_kw=q_max, q_baseline_kw=0.0))

        return FlexibilityEnvelope(self._identity, tuple(points), sign=self._sign)

    # -- StateProvider -----------------------------------------------------

    def get_state(self, timestamp: datetime | None = None) -> ResourceState:
        ts = timestamp or datetime.now(timezone.utc)
        return ResourceState(
            identity=self._identity,
            timestamp=ts,
            values={
                "power_kw": self.current_power_kw,
                "soc": self.soc,
                "capacity_kwh": self._capacity_kwh,
            },
            quality=Quality.VALID,
        )

    def get_limits(self, timestamp: datetime | None = None) -> Limits:
        return Limits(
            min_power_kw=-self._rated_kw,
            max_power_kw=self._rated_kw,
            sign=self._sign,
            constraints={
                "min_soc": self._min_soc,
                "max_soc": self._max_soc,
                "soc": self.soc,
            },
        )

    # -- DispatchTarget ----------------------------------------------------

    def dispatch(self, command: DispatchCommand) -> DispatchResponse:
        """Actuate GridLAB-D inverter active power output."""
        self.commands.append(command)
        cmd_power = command.sign.to_canonical(command.power_kw)
        violations: list[str] = []

        dur_h = command.interval.hours
        current_soc = self.soc
        eta = self._efficiency

        # Use the same bound calculation as get_envelope so a command sitting
        # exactly on the advertised q_min/q_max is not spuriously clamped.
        (q_min, q_max) = soc_coupled_power_bounds(
            [command.interval],
            soc=current_soc,
            capacity_kwh=self._capacity_kwh,
            rated_kw=self._rated_kw,
            min_soc=self._min_soc,
            max_soc=self._max_soc,
            efficiency=eta,
        )[0]

        # Clamp power to achievable envelope [q_min, q_max]
        actual_kw = max(q_min, min(q_max, cmd_power))
        if abs(actual_kw - cmd_power) > 1e-3:
            violations.append(f"Commanded {cmd_power:.2f} kW clamped to {actual_kw:.2f} kW due to SOC limits")

        # P_Out is the inverter's real power *output* to the grid in VA, i.e.
        # generation-positive. Canonical load-positive charging must therefore
        # be negated, and kW scaled to VA.
        self._engine.set_property(self._object_name, "P_Out", -actual_kw * 1000.0)

        # Update SOC on GridLAB-D battery / inverter
        if actual_kw > 0:  # Charging
            new_soc = min(self._max_soc, current_soc + (actual_kw * eta * dur_h) / self._capacity_kwh)
        elif actual_kw < 0:  # Discharging
            new_soc = max(self._min_soc, current_soc - (abs(actual_kw) / eta * dur_h) / self._capacity_kwh)
        else:
            new_soc = current_soc
        self._engine.set_property(self._object_name, "battery_soc", new_soc)
        self._engine.set_property(self._object_name, "state_of_charge", new_soc)
        self._engine.set_property(self._object_name, "SOC", new_soc)
        self._engine.step(command.interval.duration_s)

        actual_in_sign = command.sign.from_canonical(actual_kw)

        return DispatchResponse(
            identity=self._identity,
            interval=command.interval,
            accepted=True,
            actual={
                "power_kw": actual_in_sign,
                "soc": new_soc,
            },
            violations=tuple(violations),
        )

    def release(self, interval: Interval) -> DispatchResponse:
        self._engine.set_property(self._object_name, "P_Out", 0.0)
        return DispatchResponse(
            identity=self._identity,
            interval=interval,
            accepted=True,
            actual={"power_kw": 0.0, "soc": self.soc},
        )
