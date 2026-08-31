"""GridLAB-D Water Heater physical adapter.

Connects directly to a GridLAB-D `waterheater` object, extracting real physical
tank temperatures, standby losses, and heating capacity, and dispatching
temperature setpoints or element control directly into the simulation engine.
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


class GridlabdWaterHeaterAdapter:
    """Controls a GridLAB-D `waterheater` object as a transactive flexible load.

    Args:
        identity: Who this resource is.
        object_name: The name of the `waterheater` object in the GridLAB-D model.
        engine: GridlabdEngine instance connected to the simulation.
        rated_kw: Rated heating element electric power (kW).
        t_min_f: Minimum comfort water temperature in °F (default 110.0°F).
        t_max_f: Maximum safe water temperature in °F (default 140.0°F).
        t_desired_f: User desired nominal setpoint in °F (default 125.0°F).
        sign: Sign convention (default LOAD_POSITIVE).
    """

    def __init__(
        self,
        identity: ResourceIdentity,
        object_name: str,
        engine: GridlabdEngine,
        rated_kw: float = 4.5,
        t_min_f: float = 110.0,
        t_max_f: float = 140.0,
        t_desired_f: float = 125.0,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
    ) -> None:
        self._identity = identity
        self._object_name = object_name
        self._engine = engine
        self._rated_kw = float(rated_kw)
        self._t_min_f = float(t_min_f)
        self._t_max_f = float(t_max_f)
        self._t_desired_f = float(t_desired_f)
        self._sign = sign
        self.commands: list[DispatchCommand] = []

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        resource_id: str,
        rated_kw: float = 4.5,
        gridlabd: Mapping[str, Any] | None = None,
        location: Mapping[str, Any] | None = None,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> "GridlabdWaterHeaterAdapter":
        """Build from declarative JSON configuration.

        Example config:
            {
              "resource_id": "wh_01",
              "rated_kw": 4.5,
              "gridlabd": {
                "object_name": "wh_01",
                "model_path": "models/gridlabd/feeder.glm",
                "t_min_f": 110.0,
                "t_max_f": 140.0,
                "t_desired_f": 125.0
              }
            }
        """
        gld_dict = dict(gridlabd or {})
        object_name = gld_dict.pop("object_name", resource_id)
        model_path = gld_dict.pop("model_path", None)
        engine = gld_dict.pop("engine", None) or GridlabdEngine.get_or_create(model_path)

        t_min_f = float(gld_dict.pop("t_min_f", 110.0))
        t_max_f = float(gld_dict.pop("t_max_f", 140.0))
        t_desired_f = float(gld_dict.pop("t_desired_f", 125.0))
        rated_element_kw = float(gld_dict.pop("rated_kw", rated_kw))

        loc_str = ""
        if location:
            loc_dict = dict(location)
            lat = loc_dict.get("latitude")
            lon = loc_dict.get("longitude")
            if lat is not None and lon is not None:
                loc_str = f"{lat},{lon}"

        identity = ResourceIdentity(
            resource_id=resource_id,
            kind=ResourceKind.WATER_HEATING,
            source=f"gridlabd:{object_name}",
            location=loc_str,
            metadata={
                "object_name": object_name,
                "model_path": str(model_path) if model_path else "",
                "rated_kw": rated_element_kw,
                **kwargs,
            },
        )

        return cls(
            identity=identity,
            object_name=object_name,
            engine=engine,
            rated_kw=rated_element_kw,
            t_min_f=t_min_f,
            t_max_f=t_max_f,
            t_desired_f=t_desired_f,
            sign=sign,
        )

    # -- properties --------------------------------------------------------

    @property
    def identity(self) -> ResourceIdentity:
        return self._identity

    @property
    def temperature_f(self) -> float:
        return self._engine.get_float_property(self._object_name, "temperature", default=self._t_desired_f)

    @property
    def setpoint_f(self) -> float:
        return self._engine.get_float_property(self._object_name, "tank_setpoint", default=self._t_desired_f)

    @property
    def element_power_kw(self) -> float:
        cap = self._engine.get_float_property(self._object_name, "heating_element_capacity", default=self._rated_kw)
        return cap if cap > 0 else self._rated_kw

    # -- FlexibilityProvider -----------------------------------------------

    def get_envelope(
        self,
        intervals: Sequence[Interval],
        context: Mapping[str, Any] | None = None,
    ) -> FlexibilityEnvelope:
        if not intervals:
            raise AdapterError("get_envelope requires at least one interval")

        current_temp = self.temperature_f
        elem_kw = self.element_power_kw
        vol_gal = self._engine.get_float_property(self._object_name, "tank_volume", default=50.0)
        c_th = (vol_gal * 8.34) / 3412.14

        points = []
        for interval in intervals:
            dur_h = interval.hours

            # Max power: heat headroom over this specific interval duration
            headroom_kwh = max(0.0, (self._t_max_f - current_temp) * c_th)
            max_p = (headroom_kwh / dur_h) + 0.05 if dur_h > 0 else elem_kw
            q_max = max(0.0, min(elem_kw, max_p))

            # Min power: heat required to avoid dropping below t_min_f over dur_h
            if current_temp <= self._t_min_f:
                q_min = elem_kw
            else:
                q_min = 0.0

            if q_min > q_max:
                q_min = q_max

            # Baseline operation: heating if below setpoint
            deadband = self._engine.get_float_property(self._object_name, "thermostat_deadband", default=4.0)
            if current_temp < (self._t_desired_f - deadband / 2.0):
                baseline_kw = q_max
            else:
                baseline_kw = 0.0

            baseline_kw = max(q_min, min(q_max, baseline_kw))
            points.append(EnvelopePoint(interval, q_min_kw=q_min, q_max_kw=q_max, q_baseline_kw=baseline_kw))

        return FlexibilityEnvelope(self._identity, tuple(points), sign=self._sign)

    # -- StateProvider -----------------------------------------------------

    def get_state(self, timestamp: datetime | None = None) -> ResourceState:
        current_temp = self.temperature_f
        actual_power = self._engine.get_float_property(self._object_name, "actual_load", default=0.0)
        ts = timestamp or datetime.now(timezone.utc)

        return ResourceState(
            identity=self._identity,
            timestamp=ts,
            values={
                "power_kw": actual_power,
                "temperature_f": current_temp,
                "tank_setpoint_f": self.setpoint_f,
                "object_name": self._object_name,
            },
            quality=Quality.VALID,
        )

    def get_limits(self, timestamp: datetime | None = None) -> Limits:
        return Limits(
            min_power_kw=0.0,
            max_power_kw=self.element_power_kw,
            sign=self._sign,
            constraints={
                "t_min_f": self._t_min_f,
                "t_max_f": self._t_max_f,
                "temperature_f": self.temperature_f,
            },
        )

    # -- DispatchTarget ----------------------------------------------------

    def dispatch(self, command: DispatchCommand) -> DispatchResponse:
        """Actuate the GridLAB-D water heater based on market-cleared power."""
        self.commands.append(command)
        cmd_power = command.sign.to_canonical(command.power_kw)
        violations: list[str] = []

        elem_kw = self.element_power_kw
        current_temp = self.temperature_f

        # Map desired power into GridLAB-D tank setpoint actuation
        if cmd_power >= (elem_kw * 0.5):
            # Command full heating -> raise setpoint to top of comfort range
            target_setpoint = self._t_max_f
            actual_kw = elem_kw if current_temp < self._t_max_f else 0.05
        elif cmd_power > 0.0:
            target_setpoint = self._t_desired_f
            actual_kw = cmd_power
        else:
            # Turn off / float -> lower setpoint to lower comfort boundary
            target_setpoint = self._t_min_f
            actual_kw = 0.0 if current_temp > self._t_min_f else elem_kw

        self._engine.set_property(self._object_name, "tank_setpoint", target_setpoint)
        self._engine.step(command.interval.duration_s)

        new_temp = self.temperature_f
        actual_in_sign = command.sign.from_canonical(actual_kw)

        return DispatchResponse(
            identity=self._identity,
            interval=command.interval,
            accepted=True,
            actual={
                "power_kw": actual_in_sign,
                "temperature_f": new_temp,
                "tank_setpoint_f": target_setpoint,
            },
            violations=tuple(violations),
        )

    def release(self, interval: Interval) -> DispatchResponse:
        """Release back to default setpoint."""
        self._engine.set_property(self._object_name, "tank_setpoint", self._t_desired_f)
        return DispatchResponse(
            identity=self._identity,
            interval=interval,
            accepted=True,
            actual={"tank_setpoint_f": self._t_desired_f},
        )
