"""GridLAB-D HVAC / House physical adapter.

Connects to a GridLAB-D `house` object, querying indoor air temperatures and
actuating cooling/heating setpoints to provide transactive thermal flexibility.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ...conventions import Quality, SignConvention
from ...errors import AdapterError
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


class GridlabdHvacAdapter:
    """Controls a GridLAB-D `house` object's HVAC system as a flexible resource.

    Args:
        identity: Who this resource is.
        object_name: The name of the `house` object in the GridLAB-D model.
        engine: GridlabdEngine instance connected to the simulation.
        rated_kw: Rated compressor power (kW).
        min_cooling_setpoint_f: Lowest allowable cooling setpoint in °F (default 68.0°F).
        max_cooling_setpoint_f: Highest allowable cooling setpoint in °F (default 78.0°F).
        sign: Sign convention (default LOAD_POSITIVE).
    """

    def __init__(
        self,
        identity: ResourceIdentity,
        object_name: str,
        engine: GridlabdEngine,
        rated_kw: float = 3.5,
        min_cooling_setpoint_f: float = 68.0,
        max_cooling_setpoint_f: float = 78.0,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
    ) -> None:
        self._identity = identity
        self._object_name = object_name
        self._engine = engine
        self._rated_kw = float(rated_kw)
        self._min_cool_f = float(min_cooling_setpoint_f)
        self._max_cool_f = float(max_cooling_setpoint_f)
        self._sign = sign
        self.commands: list[DispatchCommand] = []

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        resource_id: str,
        rated_kw: float = 3.5,
        gridlabd: Mapping[str, Any] | None = None,
        location: Mapping[str, Any] | None = None,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> "GridlabdHvacAdapter":
        gld_dict = dict(gridlabd or {})
        object_name = gld_dict.pop("object_name", resource_id)
        model_path = gld_dict.pop("model_path", None)
        engine = GridlabdEngine.get_or_create(model_path)

        min_cool_f = float(gld_dict.pop("min_cooling_setpoint_f", 68.0))
        max_cool_f = float(gld_dict.pop("max_cooling_setpoint_f", 78.0))
        rated_hvac_kw = float(gld_dict.pop("rated_kw", rated_kw))

        loc_str = ""
        if location:
            loc_dict = dict(location)
            lat = loc_dict.get("latitude")
            lon = loc_dict.get("longitude")
            if lat is not None and lon is not None:
                loc_str = f"{lat},{lon}"

        identity = ResourceIdentity(
            resource_id=resource_id,
            kind=ResourceKind.THERMAL_LOAD,
            source=f"gridlabd:{object_name}",
            location=loc_str,
            metadata={
                "object_name": object_name,
                "model_path": str(model_path) if model_path else "",
                "rated_kw": rated_hvac_kw,
                **kwargs,
            },
        )

        return cls(
            identity=identity,
            object_name=object_name,
            engine=engine,
            rated_kw=rated_hvac_kw,
            min_cooling_setpoint_f=min_cool_f,
            max_cooling_setpoint_f=max_cool_f,
            sign=sign,
        )

    # -- properties --------------------------------------------------------

    @property
    def identity(self) -> ResourceIdentity:
        return self._identity

    @property
    def air_temperature_f(self) -> float:
        return self._engine.get_float_property(self._object_name, "air_temperature", default=72.0)

    @property
    def cooling_setpoint_f(self) -> float:
        return self._engine.get_float_property(self._object_name, "cooling_setpoint", default=74.0)

    # -- FlexibilityProvider -----------------------------------------------

    def get_envelope(
        self,
        intervals: Sequence[Interval],
        context: Mapping[str, Any] | None = None,
    ) -> FlexibilityEnvelope:
        if not intervals:
            raise AdapterError("get_envelope requires at least one interval")

        current_temp = self.air_temperature_f
        hvac_capacity = self._rated_kw

        points = []
        for interval in intervals:
            # Min power: turn off cooling (0 kW) unless air temp exceeds max comfort bound
            if current_temp >= self._max_cool_f:
                q_min = hvac_capacity
            else:
                q_min = 0.0

            # Max power: full cooling (hvac_capacity) unless air temp drops below min comfort bound
            if current_temp <= self._min_cool_f:
                q_max = 0.0
            else:
                q_max = hvac_capacity

            if q_min > q_max:
                q_min = q_max

            # Baseline operation: cooling if above current cooling setpoint
            baseline_kw = hvac_capacity if current_temp > self.cooling_setpoint_f else 0.0
            baseline_kw = max(q_min, min(q_max, baseline_kw))

            points.append(EnvelopePoint(interval, q_min_kw=q_min, q_max_kw=q_max, q_baseline_kw=baseline_kw))

        return FlexibilityEnvelope(self._identity, tuple(points), sign=self._sign)

    # -- StateProvider -----------------------------------------------------

    def get_state(self, timestamp: datetime | None = None) -> ResourceState:
        ts = timestamp or datetime.now(timezone.utc)
        return ResourceState(
            identity=self._identity,
            timestamp=ts,
            values={
                "air_temperature_f": self.air_temperature_f,
                "cooling_setpoint_f": self.cooling_setpoint_f,
                "power_kw": self._engine.get_float_property(self._object_name, "hvac_power", default=0.0),
            },
            quality=Quality.VALID,
        )

    def get_limits(self, timestamp: datetime | None = None) -> Limits:
        return Limits(
            min_power_kw=0.0,
            max_power_kw=self._rated_kw,
            sign=self._sign,
            constraints={
                "min_cooling_setpoint_f": self._min_cool_f,
                "max_cooling_setpoint_f": self._max_cool_f,
                "air_temperature_f": self.air_temperature_f,
            },
        )

    # -- DispatchTarget ----------------------------------------------------

    def dispatch(self, command: DispatchCommand) -> DispatchResponse:
        """Actuate GridLAB-D house thermostat setpoints."""
        self.commands.append(command)
        cmd_power = command.sign.to_canonical(command.power_kw)
        violations: list[str] = []

        current_temp = self.air_temperature_f
        # Compute comfort boundaries
        q_min = self._rated_kw if current_temp >= self._max_cool_f else 0.0
        q_max = 0.0 if current_temp <= self._min_cool_f else self._rated_kw

        actual_kw = max(q_min, min(q_max, cmd_power))
        if abs(actual_kw - cmd_power) > 1e-3:
            violations.append(
                f"Commanded {cmd_power:.2f} kW clamped to {actual_kw:.2f} kW due to indoor comfort limits"
            )

        if actual_kw >= (self._rated_kw * 0.5):
            target_setpoint = self._min_cool_f  # Pre-cool house
        elif actual_kw > 0.0:
            target_setpoint = (self._min_cool_f + self._max_cool_f) / 2.0
        else:
            target_setpoint = self._max_cool_f  # Float / curtail cooling

        self._engine.set_property(self._object_name, "cooling_setpoint", target_setpoint)
        self._engine.set_property(self._object_name, "hvac_power", actual_kw)
        self._engine.step(command.interval.duration_s)

        new_temp = self.air_temperature_f
        actual_in_sign = command.sign.from_canonical(actual_kw)

        return DispatchResponse(
            identity=self._identity,
            interval=command.interval,
            accepted=True,
            actual={
                "power_kw": actual_in_sign,
                "air_temperature_f": new_temp,
                "cooling_setpoint_f": target_setpoint,
            },
            violations=tuple(violations),
        )

    def release(self, interval: Interval) -> DispatchResponse:
        nominal = 74.0
        self._engine.set_property(self._object_name, "cooling_setpoint", nominal)
        return DispatchResponse(
            identity=self._identity,
            interval=interval,
            accepted=True,
            actual={"cooling_setpoint_f": nominal},
        )
