"""OCHRE HVAC Building Thermal physical adapter.

Connects directly to an NREL OCHRE `Dwelling` / HVAC simulation, modeling whole-home
building thermal RC networks, heat transfer, ambient weather dynamics, and cooling/heating
setpoint shifting.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
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

_log = logging.getLogger(__name__)
UTC = timezone.utc


class OchreHvacAdapter:
    """Controls an OCHRE Dwelling HVAC system as a flexible thermal load.

    Args:
        identity: Who this resource is.
        dwelling: Initialized OCHRE Dwelling object (optional).
        rated_kw: Rated HVAC compressor electric power (kW).
        min_cooling_setpoint_f: Minimum comfort cooling setpoint in °F (default 68.0°F / 20.0°C).
        max_cooling_setpoint_f: Maximum comfort cooling setpoint in °F (default 78.0°F / 25.56°C).
        sign: Sign convention (default LOAD_POSITIVE).
    """

    def __init__(
        self,
        identity: ResourceIdentity,
        dwelling: Any = None,
        rated_kw: float = 3.5,
        min_cooling_setpoint_f: float = 68.0,
        max_cooling_setpoint_f: float = 78.0,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> None:
        self._identity = identity
        self._rated_kw = float(rated_kw)
        self._min_cooling_setpoint_f = float(min_cooling_setpoint_f)
        self._max_cooling_setpoint_f = float(max_cooling_setpoint_f)
        self._min_cooling_setpoint_c = (self._min_cooling_setpoint_f - 32.0) * 5.0 / 9.0
        self._max_cooling_setpoint_c = (self._max_cooling_setpoint_f - 32.0) * 5.0 / 9.0
        self._sign = sign
        self.commands: list[DispatchCommand] = []

        if dwelling is not None:
            self._dwelling = dwelling
        else:
            try:
                from ochre.utils.units import ureg
                try:
                    ureg.define("inch_H2O_39F = 249.08891 * Pa")
                except Exception:
                    pass
            except ImportError:
                pass

            from ochre import Dwelling
            from ochre.utils import default_input_path

            start_time = kwargs.get("start_time", dt.datetime(2018, 1, 1, 0, 0))
            if isinstance(start_time, str):
                start_time = dt.datetime.fromisoformat(start_time)
            if isinstance(start_time, dt.datetime) and start_time.tzinfo is not None:
                # OCHRE runs on a naive clock that these adapters report as UTC; convert, do not just drop the offset.
                start_time = start_time.astimezone(dt.timezone.utc).replace(tzinfo=None)
            time_res = kwargs.get("time_res", dt.timedelta(minutes=15))
            if isinstance(time_res, (int, float)):
                time_res = dt.timedelta(seconds=time_res)
            duration = kwargs.get("duration", dt.timedelta(days=10))

            pkg_root = Path(__file__).resolve().parents[4]
            bundled_hpxml = pkg_root / "models" / "ochre" / "dwelling_up11.xml"
            bundled_sched = pkg_root / "models" / "ochre" / "dwelling_schedule.csv"
            bundled_weather = pkg_root / "models" / "ochre" / "denver_weather.epw"

            hpxml_fallback = str(bundled_hpxml) if bundled_hpxml.exists() else os.path.join(default_input_path, "Input Files", "bldg0112631-up11.xml")
            hpxml_file = kwargs.get("hpxml_file")
            if hpxml_file and hpxml_file != "default":
                p = Path(hpxml_file)
                if not p.exists():
                    for root in (Path.cwd(), pkg_root, pkg_root / "der-interface"):
                        if (root / p).exists():
                            p = root / p
                            break
                hpxml_file = str(p.resolve()) if p.exists() else hpxml_fallback
            else:
                hpxml_file = hpxml_fallback

            sched_fallback = str(bundled_sched) if bundled_sched.exists() else os.path.join(default_input_path, "Input Files", "bldg0112631_schedule.csv")
            hpxml_sched = kwargs.get("hpxml_schedule_file")
            if hpxml_sched and hpxml_sched != "default":
                p = Path(hpxml_sched)
                if not p.exists():
                    for root in (Path.cwd(), pkg_root, pkg_root / "der-interface"):
                        if (root / p).exists():
                            p = root / p
                            break
                hpxml_sched = str(p.resolve()) if p.exists() else sched_fallback
            else:
                hpxml_sched = sched_fallback

            weather_fallback = str(bundled_weather) if bundled_weather.exists() else os.path.join(default_input_path, "Weather", "USA_CO_Denver.Intl.AP.725650_TMY3.epw")
            weather_file = kwargs.get("weather_file")
            if weather_file and weather_file != "default":
                p = Path(weather_file)
                if not p.exists():
                    for root in (Path.cwd(), pkg_root, pkg_root / "der-interface"):
                        if (root / p).exists():
                            p = root / p
                            break
                weather_file = str(p.resolve()) if p.exists() else weather_fallback
            else:
                weather_file = weather_fallback

            dwelling_args = {
                "name": kwargs.get("name", "OchreHouse"),
                "start_time": start_time,
                "time_res": time_res,
                "duration": duration,
                "initialization_time": dt.timedelta(hours=1),
                "hpxml_file": hpxml_file,
                "hpxml_schedule_file": hpxml_sched,
                "schedule_input_file": hpxml_sched,
                "weather_file": weather_file,
                "save_results": False,
                "verbosity": 0,
            }
            dwelling_args.update({k: v for k, v in kwargs.items() if k not in dwelling_args})
            self._dwelling = Dwelling(**dwelling_args)

        # Identify cooling / heating equipment name in OCHRE dwelling.
        # get_equipment_by_end_use returns a list when a dwelling has several
        # units of the same end use, so normalize before reading .name.
        self._cooler = self._first_equipment("HVAC Cooling")
        self._heater = self._first_equipment("HVAC Heating")
        self._cooler_name = getattr(self._cooler, "name", None) or "ASHP Cooler"

    def _first_equipment(self, end_use: str) -> Any:
        """Return a single equipment object for *end_use*, or None."""
        found = self._dwelling.get_equipment_by_end_use(end_use)
        if isinstance(found, (list, tuple)):
            return found[0] if found else None
        return found

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        resource_id: str,
        rated_kw: float = 3.5,
        min_cooling_setpoint_f: float = 68.0,
        max_cooling_setpoint_f: float = 78.0,
        ochre: Mapping[str, Any] | None = None,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        start_time: Any = None,
        **kwargs: Any,
    ) -> OchreHvacAdapter:
        ochre_cfg = dict(ochre or {})
        if start_time is not None:
            ochre_cfg["start_time"] = start_time
        identity = ResourceIdentity(
            resource_id=resource_id,
            kind=ResourceKind.THERMAL_LOAD,
            source="ochre",
            metadata={
                "rated_kw": float(rated_kw),
                "description": ochre_cfg.get("description", "OCHRE Dwelling Thermal Envelope & HVAC"),
                **kwargs,
            },
        )
        return cls(
            identity=identity,
            rated_kw=rated_kw,
            min_cooling_setpoint_f=min_cooling_setpoint_f,
            max_cooling_setpoint_f=max_cooling_setpoint_f,
            sign=sign,
            **ochre_cfg,
        )

    # -- properties --------------------------------------------------------

    @property
    def identity(self) -> ResourceIdentity:
        return self._identity

    @property
    def dwelling(self) -> Any:
        return self._dwelling

    @property
    def indoor_temp_c(self) -> float:
        zone = self._dwelling.envelope.zones.get("Indoor")
        if zone is not None and hasattr(zone, "temperature"):
            return float(zone.temperature)
        return 22.0

    @property
    def indoor_temp_f(self) -> float:
        return self.indoor_temp_c * 9.0 / 5.0 + 32.0

    @property
    def outdoor_temp_c(self) -> float:
        schedule = getattr(self._dwelling.envelope, "schedule", None)
        current_time = getattr(self._dwelling, "current_time", None)
        if schedule is not None and current_time in schedule.index and "Ambient Dry Bulb (C)" in schedule.columns:
            return float(schedule.loc[current_time, "Ambient Dry Bulb (C)"])
        return 20.0

    @property
    def outdoor_temp_f(self) -> float:
        return self.outdoor_temp_c * 9.0 / 5.0 + 32.0

    # -- FlexibilityModel --------------------------------------------------

    def get_envelope(
        self,
        intervals: Sequence[Interval],
        context: Mapping[str, Any] | None = None,
    ) -> FlexibilityEnvelope:
        if not intervals:
            raise AdapterError("get_envelope requires at least one interval")

        indoor_f = self.indoor_temp_f
        points: list[EnvelopePoint] = []

        for iv in intervals:
            if indoor_f >= self._max_cooling_setpoint_f:
                # Room overheated: must cool
                q_min = self._rated_kw
                q_max = self._rated_kw
                q_base = self._rated_kw
            elif indoor_f <= self._min_cooling_setpoint_f:
                # Room cool: must not cool
                q_min = 0.0
                q_max = 0.0
                q_base = 0.0
            else:
                # Flexible cooling range
                q_min = 0.0
                q_max = self._rated_kw
                mid_setpoint = (self._min_cooling_setpoint_f + self._max_cooling_setpoint_f) / 2.0
                q_base = self._rated_kw if (indoor_f > mid_setpoint) else 0.0

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

        # Map target power to cooling setpoint:
        # High power -> lower setpoint (cool more)
        # Low power -> higher setpoint (relax cooling)
        power_ratio = max(0.0, min(1.0, target_kw / max(self._rated_kw, 1e-6)))
        setpoint_c = self._max_cooling_setpoint_c - power_ratio * (
            self._max_cooling_setpoint_c - self._min_cooling_setpoint_c
        )
        setpoint_f = setpoint_c * 9.0 / 5.0 + 32.0

        control_signal = {self._cooler_name: {"Setpoint": setpoint_c}}
        res = self._dwelling.update(control_signal=control_signal)

        hvac_power = float(res.get("HVAC Cooling Electric Power (kW)", res.get("Total Electric Power (kW)", target_kw)))

        return DispatchResponse(
            identity=self._identity,
            interval=command.interval,
            accepted=True,
            actual={
                "power_kw": command.sign.from_canonical(hvac_power),
                "indoor_temp_f": self.indoor_temp_f,
                "cooling_setpoint_f": setpoint_f,
            },
            message=f"OCHRE HVAC dispatched (Indoor: {self.indoor_temp_f:.1f}°F, Setpoint: {setpoint_f:.1f}°F, Power: {hvac_power:.2f} kW)",
        )

    def release(self, interval: Interval) -> DispatchResponse:
        nominal_c = (self._min_cooling_setpoint_c + self._max_cooling_setpoint_c) / 2.0
        self._dwelling.update(control_signal={self._cooler_name: {"Setpoint": nominal_c}})
        return DispatchResponse(
            identity=self._identity,
            interval=interval,
            accepted=True,
            actual={"indoor_temp_f": self.indoor_temp_f},
            message="OCHRE HVAC released to nominal comfort setpoint",
        )

    # -- StateProvider -----------------------------------------------------

    def get_state(self, timestamp: datetime | None = None) -> ResourceState:
        ts = timestamp or datetime.now(UTC)
        # Dwelling is a Simulator and exposes total_p_kw, not electric_kw.
        raw = getattr(self._dwelling, "total_p_kw", None)
        if raw is None:
            raw = getattr(self._dwelling, "electric_kw", None)
        total_kw = None if raw is None else float(raw)
        values: dict[str, Any] = {
            "indoor_temp_c": self.indoor_temp_c,
            "indoor_temp_f": self.indoor_temp_f,
            "outdoor_temp_c": self.outdoor_temp_c,
            "outdoor_temp_f": self.outdoor_temp_f,
            "rated_kw": self._rated_kw,
        }
        if total_kw is not None:
            values["power_kw"] = total_kw
        return ResourceState(
            identity=self._identity,
            timestamp=ts,
            values=values,
            quality=Quality.VALID if total_kw is not None else Quality.MISSING,
        )

    def get_limits(self, now: datetime | None = None) -> Limits:
        return Limits(
            min_power_kw=0.0,
            max_power_kw=self._rated_kw,
            sign=self._sign,
        )
