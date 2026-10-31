"""OCHRE Whole-Dwelling aggregated meter physical adapter.

Connects directly to an NREL OCHRE `Dwelling` simulation object, modeling the full
residential energy profile (HVAC, water heater, lighting, major appliances, and plug loads).
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


class OchreDwellingAdapter:
    """Aggregates an entire residential dwelling as a flexible net load in OCHRE.

    Args:
        identity: Who this resource is.
        dwelling: Initialized OCHRE Dwelling object (optional).
        rated_kw: Main service panel rated capacity (kW, default 15.0).
        sign: Sign convention (default LOAD_POSITIVE).
    """

    def __init__(
        self,
        identity: ResourceIdentity,
        dwelling: Any = None,
        rated_kw: float = 15.0,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> None:
        self._identity = identity
        self._rated_kw = float(rated_kw)
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
            from pathlib import Path

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

            hpxml_file = kwargs.get("hpxml_file")
            if not hpxml_file or not os.path.exists(hpxml_file):
                hpxml_file = str(bundled_hpxml) if bundled_hpxml.exists() else os.path.join(default_input_path, "Input Files", "bldg0112631-up11.xml")

            hpxml_schedule_file = kwargs.get("hpxml_schedule_file")
            if not hpxml_schedule_file or not os.path.exists(hpxml_schedule_file):
                hpxml_schedule_file = str(bundled_sched) if bundled_sched.exists() else os.path.join(default_input_path, "Input Files", "bldg0112631_schedule.csv")

            weather_file = kwargs.get("weather_file")
            if not weather_file or not os.path.exists(weather_file):
                weather_file = str(bundled_weather) if bundled_weather.exists() else os.path.join(default_input_path, "Weather", "USA_CO_Denver.Intl.AP.725650_TMY3.epw")

            dwelling_args = {
                "name": kwargs.get("name", "OchreDwelling"),
                "start_time": start_time,
                "time_res": time_res,
                "duration": duration,
                "initialization_time": dt.timedelta(hours=1),
                "hpxml_file": hpxml_file,
                "hpxml_schedule_file": hpxml_schedule_file,
                "schedule_input_file": hpxml_schedule_file,
                "weather_file": weather_file,
                "save_results": False,
                "verbosity": 0,
            }
            dwelling_args.update({k: v for k, v in kwargs.items() if k not in dwelling_args})
            self._dwelling = Dwelling(**dwelling_args)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        resource_id: str,
        rated_kw: float = 15.0,
        ochre: Mapping[str, Any] | None = None,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        start_time: Any = None,
        **kwargs: Any,
    ) -> OchreDwellingAdapter:
        ochre_cfg = dict(ochre or {})
        if start_time is not None:
            ochre_cfg["start_time"] = start_time
        identity = ResourceIdentity(
            resource_id=resource_id,
            kind=ResourceKind.BUILDING,
            source="ochre",
            metadata={
                "rated_kw": float(rated_kw),
                "description": ochre_cfg.get("description", "OCHRE Full Residential Dwelling"),
                **kwargs,
            },
        )
        return cls(
            identity=identity,
            rated_kw=rated_kw,
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
    def current_power_kw(self) -> float | None:
        """Whole-dwelling real power in canonical load-positive kW.

        OCHRE's ``Dwelling`` is a ``Simulator``, not an ``Equipment``, so it
        exposes ``total_p_kw`` rather than ``electric_kw``. Returns ``None`` when
        no value is available so callers can report Quality.MISSING instead of
        silently treating an absent reading as a genuine 0 kW.
        """
        value = getattr(self._dwelling, "total_p_kw", None)
        if value is None:
            value = getattr(self._dwelling, "electric_kw", None)
        return None if value is None else float(value)

    # -- FlexibilityModel --------------------------------------------------

    def get_envelope(
        self,
        intervals: Sequence[Interval],
        context: Mapping[str, Any] | None = None,
    ) -> FlexibilityEnvelope:
        if not intervals:
            raise AdapterError("get_envelope requires at least one interval")

        measured_kw = self.current_power_kw
        quality = Quality.VALID if measured_kw is not None else Quality.MISSING
        cur_kw = 0.0 if measured_kw is None else measured_kw
        points: list[EnvelopePoint] = []

        for iv in intervals:
            # Whole dwelling flexible envelope bounded by base load vs peak capacity
            base_kw = max(0.5, cur_kw)
            q_min = base_kw * 0.5  # shed flexible loads (HVAC, WH)
            q_max = min(self._rated_kw, base_kw * 1.5)
            # A dwelling can legitimately draw more than its configured rating
            # (EV + resistance heat + HVAC). Keep the band ordered and the
            # baseline inside it rather than letting EnvelopePoint raise.
            q_max = max(q_max, q_min)
            q_base = min(max(base_kw, q_min), q_max)

            points.append(
                EnvelopePoint(
                    interval=iv,
                    q_min_kw=q_min,
                    q_max_kw=q_max,
                    q_baseline_kw=q_base,
                    quality=quality,
                )
            )

        return FlexibilityEnvelope(
            identity=self._identity,
            points=tuple(points),
            sign=self._sign,
        )

    # -- DispatchableModel -------------------------------------------------

    def dispatch(self, command: DispatchCommand) -> DispatchResponse:
        """Translate a power target into whole-dwelling HVAC setpoint offsets.

        A dwelling has no single power actuator, so the target is expressed as a
        thermostat nudge on whichever HVAC equipment exists. Previously this
        method ignored the command entirely, stepped the dwelling, and returned
        accepted=True -- so a market settled against a counterfactual it had
        never influenced.
        """
        self.commands.append(command)

        target_kw = (
            command.sign.to_canonical(command.power_kw)
            if command.power_kw is not None
            else None
        )

        violations: list[str] = []
        control_signal: dict[str, Any] = {}

        if target_kw is None:
            violations.append("command carried no power_kw target; dwelling free-ran")
        else:
            baseline_kw = self.current_power_kw
            base = 0.5 if baseline_kw is None else max(baseline_kw, 0.5)
            # Positive delta means "consume more" -> relax cooling / raise heat.
            delta_ratio = (target_kw - base) / max(self._rated_kw, 1e-6)
            offset_c = max(-2.0, min(2.0, delta_ratio * 4.0))

            for end_use, key in (("HVAC Cooling", "Cooling"), ("HVAC Heating", "Heating")):
                equipment = self._equipment_for(end_use)
                for unit in equipment:
                    setpoint = getattr(unit, "temp_setpoint", None)
                    if setpoint is None:
                        continue
                    # More cooling power means a lower cooling setpoint; more
                    # heating power means a higher heating setpoint.
                    signed = -offset_c if key == "Cooling" else offset_c
                    control_signal[unit.name] = {
                        "Setpoint": float(setpoint) + signed
                    }

            if not control_signal:
                violations.append(
                    "dwelling exposes no controllable HVAC setpoint; target not actuated"
                )

        res = self._dwelling.update(control_signal=control_signal or None)

        measured = res.get("Total Electric Power (kW)") if isinstance(res, Mapping) else None
        if measured is None:
            measured = self.current_power_kw
        actual_values: dict[str, Any] = {"indoor_temp_f": self.indoor_temp_f}
        if measured is not None:
            actual_values["power_kw"] = command.sign.from_canonical(float(measured))

        shown = "unavailable" if measured is None else f"{float(measured):.2f} kW"
        return DispatchResponse(
            identity=self._identity,
            interval=command.interval,
            accepted=not violations,
            actual=actual_values,
            violations=tuple(violations),
            message=f"OCHRE Dwelling total load: {shown} (Indoor: {self.indoor_temp_f:.1f}°F)",
        )

    def _equipment_for(self, end_use: str) -> list[Any]:
        """Return HVAC equipment for an end use, tolerating list-or-single returns."""
        by_end_use = getattr(self._dwelling, "equipment_by_end_use", None)
        if not by_end_use:
            return []
        found = by_end_use.get(end_use)
        if found is None:
            return []
        return list(found) if isinstance(found, (list, tuple)) else [found]

    def release(self, interval: Interval) -> DispatchResponse:
        measured = self.current_power_kw
        actual: dict[str, Any] = {}
        if measured is not None:
            actual["power_kw"] = measured
        return DispatchResponse(
            identity=self._identity,
            interval=interval,
            accepted=True,
            actual=actual,
            message="OCHRE Dwelling released",
        )

    # -- StateProvider -----------------------------------------------------

    def get_state(self, timestamp: datetime | None = None) -> ResourceState:
        ts = timestamp or datetime.now(UTC)
        measured = self.current_power_kw
        values: dict[str, Any] = {
            "indoor_temp_f": self.indoor_temp_f,
            "rated_kw": self._rated_kw,
        }
        if measured is not None:
            values["power_kw"] = measured
        return ResourceState(
            identity=self._identity,
            timestamp=ts,
            values=values,
            quality=Quality.VALID if measured is not None else Quality.MISSING,
        )

    def get_limits(self, now: datetime | None = None) -> Limits:
        return Limits(
            min_power_kw=0.0,
            max_power_kw=self._rated_kw,
            sign=self._sign,
        )
