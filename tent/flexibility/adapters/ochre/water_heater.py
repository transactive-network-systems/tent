"""OCHRE Water Heater physical adapter.

Connects directly to an NREL OCHRE `ElectricResistanceWaterHeater` or `HeatPumpWaterHeater`
object, modeling stratified multi-node tank thermodynamics, standby losses, cold water draw,
and temperature setpoint modulation.
"""

from __future__ import annotations

import datetime as dt
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import pandas as pd

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

_log = logging.getLogger(__name__)
UTC = timezone.utc


class OchreWaterHeaterAdapter:
    """Controls an OCHRE Water Heater as a flexible thermal storage resource.

    Args:
        identity: Who this resource is.
        water_heater: Initialized OCHRE WaterHeater object (optional).
        rated_kw: Heating element capacity in kW (default 4.5).
        t_min_f: Minimum comfort water temperature in °F (default 110.0°F / 43.3°C).
        t_max_f: Maximum safe water temperature in °F (default 140.0°F / 60.0°C).
        t_desired_f: User desired nominal setpoint in °F (default 125.0°F / 51.67°C).
        tank_volume_l: Water tank capacity in Liters (default 250 L / ~66 gal).
        sign: Sign convention (default LOAD_POSITIVE).
    """

    def __init__(
        self,
        identity: ResourceIdentity,
        water_heater: Any = None,
        rated_kw: float = 4.5,
        t_min_f: float = 110.0,
        t_max_f: float = 140.0,
        t_desired_f: float = 125.0,
        tank_volume_l: float = 250.0,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> None:
        self._identity = identity
        self._rated_kw = float(rated_kw)
        self._t_min_f = float(t_min_f)
        self._t_max_f = float(t_max_f)
        self._t_desired_f = float(t_desired_f)
        self._t_min_c = (self._t_min_f - 32.0) * 5.0 / 9.0
        self._t_max_c = (self._t_max_f - 32.0) * 5.0 / 9.0
        self._t_desired_c = (self._t_desired_f - 32.0) * 5.0 / 9.0
        self._tank_volume_l = float(tank_volume_l)
        self._sign = sign
        self.commands: list[DispatchCommand] = []

        if water_heater is not None:
            self._water_heater = water_heater
        else:
            from ochre import ElectricResistanceWaterHeater

            start_time = kwargs.get("start_time", dt.datetime(2026, 1, 1, 0, 0))
            if isinstance(start_time, str):
                start_time = dt.datetime.fromisoformat(start_time)
            if isinstance(start_time, dt.datetime) and start_time.tzinfo is not None:
                # OCHRE runs on a naive clock that these adapters report as UTC; convert, do not just drop the offset.
                start_time = start_time.astimezone(dt.timezone.utc).replace(tzinfo=None)
            time_res = kwargs.get("time_res", dt.timedelta(minutes=15))
            if isinstance(time_res, (int, float)):
                time_res = dt.timedelta(seconds=time_res)
            duration = kwargs.get("duration", dt.timedelta(days=10))

            # Create or load water draw schedule
            schedule = kwargs.get("schedule")
            schedule_file = kwargs.get("schedule_file")
            
            if schedule is None and schedule_file:
                import os
                if os.path.exists(schedule_file):
                    schedule = pd.read_csv(schedule_file, index_col=0, parse_dates=True)
                else:
                    _log.warning(f"Water heater schedule file not found: {schedule_file}, falling back to dynamic schedule")

            if schedule is None:
                times = pd.date_range(start_time, start_time + duration, freq=time_res, inclusive="left")
                draw_events = kwargs.get("draw_events")
                draw_hours = kwargs.get("draw_hours")
                draw_l_min = float(kwargs.get("draw_l_min", kwargs.get("draw_gpm", 0.0) * 3.78541 or 5.0))
                
                if draw_events:
                    # e.g. [{"hour": 7, "rate_l_min": 10.0}, {"hour": 19, "rate_l_min": 15.0}]
                    event_map = {e.get("hour"): float(e.get("rate_l_min", draw_l_min)) for e in draw_events}
                    draw_rates = [event_map.get(t.hour, 0.0) for t in times]
                elif draw_hours:
                    # e.g. [7, 8, 19, 20]
                    draw_rates = [draw_l_min if (t.hour in draw_hours) else 0.0 for t in times]
                else:
                    # Default diurnal profile: morning showers (7am) and evening dishwashing (7pm)
                    draw_rates = [
                        4.0 if (t.hour in (7, 19)) else (1.0 if (t.hour in (8, 18, 20)) else 0.0)
                        for t in times
                    ]

                mains_c = (kwargs.get("mains_temp_f", 53.6) - 32.0) * 5.0 / 9.0 if "mains_temp_f" in kwargs else kwargs.get("mains_temp_c", 12.0)
                zone_c = (kwargs.get("zone_temp_f", 68.0) - 32.0) * 5.0 / 9.0 if "zone_temp_f" in kwargs else kwargs.get("zone_temp_c", 20.0)

                schedule = pd.DataFrame(
                    {
                        "Water Heating (L/min)": draw_rates,
                        "Water Heating Setpoint (C)": [self._t_desired_c] * len(times),
                        "Water Heating Deadband (C)": [3.0] * len(times),
                        "Zone Temperature (C)": [zone_c] * len(times),
                        "Zone Wet Bulb Temperature (C)": [max(5.0, zone_c - 5.0)] * len(times),
                        "Mains Temperature (C)": [mains_c] * len(times),
                    },
                    index=times,
                )

            eq_args = {
                "start_time": start_time,
                "time_res": time_res,
                "duration": duration,
                "save_results": False,
                "verbosity": 0,
                "Setpoint Temperature (C)": self._t_desired_c,
                "Tank Volume (L)": self._tank_volume_l,
                "Tank Height (m)": 1.22,
                "UA (W/K)": 2.17,
                "schedule": schedule,
            }
            eq_args.update({k: v for k, v in kwargs.items() if k not in eq_args})
            self._water_heater = ElectricResistanceWaterHeater(**eq_args)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        resource_id: str,
        rated_kw: float = 4.5,
        t_min_f: float = 110.0,
        t_max_f: float = 140.0,
        t_desired_f: float = 125.0,
        tank_volume_l: float = 250.0,
        ochre: Mapping[str, Any] | None = None,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        start_time: Any = None,
        **kwargs: Any,
    ) -> OchreWaterHeaterAdapter:
        ochre_cfg = dict(ochre or {})
        if start_time is not None:
            ochre_cfg["start_time"] = start_time
        identity = ResourceIdentity(
            resource_id=resource_id,
            kind=ResourceKind.WATER_HEATING,
            source="ochre",
            metadata={
                "rated_kw": float(rated_kw),
                "tank_volume_l": float(tank_volume_l),
                "description": ochre_cfg.get("description", "OCHRE Stratified Water Heater"),
                **kwargs,
            },
        )
        return cls(
            identity=identity,
            rated_kw=rated_kw,
            t_min_f=t_min_f,
            t_max_f=t_max_f,
            t_desired_f=t_desired_f,
            tank_volume_l=tank_volume_l,
            sign=sign,
            **ochre_cfg,
        )

    # -- properties --------------------------------------------------------

    @property
    def identity(self) -> ResourceIdentity:
        return self._identity

    @property
    def water_heater(self) -> Any:
        return self._water_heater

    @property
    def water_temp_c(self) -> float:
        model = getattr(self._water_heater, "model", None)
        if model is not None and hasattr(model, "states") and len(model.states) > 0:
            return float(model.states.mean())
        return self._t_desired_c

    @property
    def water_temp_f(self) -> float:
        return self.water_temp_c * 9.0 / 5.0 + 32.0

    # -- FlexibilityModel --------------------------------------------------

    def get_envelope(
        self,
        intervals: Sequence[Interval],
        context: Mapping[str, Any] | None = None,
    ) -> FlexibilityEnvelope:
        if not intervals:
            raise AdapterError("get_envelope requires at least one interval")

        t_c = self.water_temp_c
        t_f = self.water_temp_f
        points: list[EnvelopePoint] = []

        for iv in intervals:
            if t_f <= self._t_min_f:
                # Tank cold: must run heating element
                q_min = self._rated_kw
                q_max = self._rated_kw
                q_base = self._rated_kw
            elif t_f >= self._t_max_f:
                # Tank hot: must shed heating
                q_min = 0.0
                q_max = 0.0
                q_base = 0.0
            else:
                # Normal comfort range: fully flexible [0, rated_kw]
                q_min = 0.0
                q_max = self._rated_kw
                # Baseline maintains desired setpoint
                q_base = self._rated_kw if (t_f < self._t_desired_f) else 0.0

            points.append(
                EnvelopePoint(
                    interval=iv,
                    q_min_kw=q_min,
                    q_max_kw=q_max,
                    q_baseline_kw=q_base,
                    quality=Quality.VALID,
                )
            )

        return FlexibilityEnvelope(
            identity=self._identity,
            points=tuple(points),
            sign=self._sign,
        )

    # -- DispatchableModel -------------------------------------------------

    def dispatch(self, command: DispatchCommand) -> DispatchResponse:
        self.commands.append(command)
        # Normalize into the canonical load-positive convention. TENT sends
        # GENERATION_POSITIVE commands, so omitting this inverts every dispatch.
        target_kw = (
            command.sign.to_canonical(command.power_kw)
            if command.power_kw is not None
            else 0.0
        )

        # Decide actuated setpoint based on dispatched power
        if target_kw >= (self._rated_kw * 0.5):
            setpoint_c = self._t_max_c
        elif target_kw <= 0.1:
            setpoint_c = self._t_min_c
        else:
            setpoint_c = self._t_desired_c

        res = self._water_heater.update(control_signal={"Setpoint": setpoint_c})
        actual_power = float(res.get("Water Heating Electric Power (kW)", target_kw))

        return DispatchResponse(
            identity=self._identity,
            interval=command.interval,
            accepted=True,
            actual={
                "power_kw": command.sign.from_canonical(actual_power),
                "water_temp_f": self.water_temp_f,
                "water_temp_c": self.water_temp_c,
            },
            message=f"OCHRE WH dispatched to {actual_power:.2f} kW (Water Temp: {self.water_temp_f:.1f}°F)",
        )

    def release(self, interval: Interval) -> DispatchResponse:
        self._water_heater.update(control_signal={"Setpoint": self._t_desired_c})
        return DispatchResponse(
            identity=self._identity,
            interval=interval,
            accepted=True,
            actual={"power_kw": 0.0, "water_temp_f": self.water_temp_f},
            message=f"OCHRE WH released to nominal setpoint {self._t_desired_f:.1f}°F",
        )

    # -- StateProvider -----------------------------------------------------

    def get_state(self, timestamp: datetime | None = None) -> ResourceState:
        ts = timestamp or datetime.now(UTC)
        power = float(getattr(self._water_heater, "electric_kw", 0.0))
        return ResourceState(
            identity=self._identity,
            timestamp=ts,
            values={
                "power_kw": power,
                "water_temp_c": self.water_temp_c,
                "water_temp_f": self.water_temp_f,
                "t_min_f": self._t_min_f,
                "t_max_f": self._t_max_f,
                "t_desired_f": self._t_desired_f,
                "rated_kw": self._rated_kw,
            },
            quality=Quality.VALID,
        )

    def get_limits(self, now: datetime | None = None) -> Limits:
        return Limits(
            min_power_kw=0.0,
            max_power_kw=self._rated_kw,
            sign=self._sign,
        )
