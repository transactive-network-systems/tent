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
                start_time = dt.datetime.fromisoformat(start_time).replace(tzinfo=None)
            elif isinstance(start_time, dt.datetime) and start_time.tzinfo is not None:
                start_time = start_time.replace(tzinfo=None)
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
    def current_power_kw(self) -> float:
        return float(getattr(self._dwelling, "electric_kw", 0.0))

    # -- FlexibilityModel --------------------------------------------------

    def get_envelope(self, intervals: Sequence[Interval]) -> FlexibilityEnvelope:
        if not intervals:
            return FlexibilityEnvelope(
                resource_id=self._identity.resource_id,
                sign=self._sign,
                points=(),
            )

        cur_kw = self.current_power_kw
        points: list[EnvelopePoint] = []

        for iv in intervals:
            # Whole dwelling flexible envelope bounded by base load vs peak capacity
            base_kw = max(0.5, cur_kw)
            q_min = base_kw * 0.5  # shed flexible loads (HVAC, WH)
            q_max = min(self._rated_kw, base_kw * 1.5)
            q_base = base_kw

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
        res = self._dwelling.update()
        actual_power = float(res.get("Total Electric Power (kW)", self.current_power_kw))

        return DispatchResponse(
            identity=self._identity,
            interval=command.interval,
            accepted=True,
            actual={
                "power_kw": actual_power,
                "indoor_temp_f": self.indoor_temp_f,
            },
            message=f"OCHRE Dwelling total load: {actual_power:.2f} kW (Indoor: {self.indoor_temp_f:.1f}°F)",
        )

    def release(self, interval: Interval) -> DispatchResponse:
        return DispatchResponse(
            identity=self._identity,
            interval=interval,
            accepted=True,
            actual={"power_kw": self.current_power_kw},
            message="OCHRE Dwelling released",
        )

    # -- StateProvider -----------------------------------------------------

    def get_state(self, timestamp: datetime | None = None) -> ResourceState:
        ts = timestamp or datetime.now(UTC)
        return ResourceState(
            identity=self._identity,
            timestamp=ts,
            values={
                "power_kw": self.current_power_kw,
                "indoor_temp_f": self.indoor_temp_f,
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
