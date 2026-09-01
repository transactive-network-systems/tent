"""OCHRE Battery storage physical adapter.

Connects directly to an NREL OCHRE `Battery` simulation object, modeling internal
cell efficiency, degradation, state-of-charge tracking, and 4-quadrant active power dispatch.
"""

from __future__ import annotations

import datetime as dt
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

_log = logging.getLogger(__name__)
UTC = timezone.utc


class OchreBatteryAdapter:
    """Controls an OCHRE Battery object as a transactive flexible storage resource.

    Args:
        identity: Who this resource is.
        battery: Initialized OCHRE Battery object (optional; can be auto-instantiated).
        rated_kw: Inverter power capacity in kW (default 5.0).
        capacity_kwh: Battery energy capacity in kWh (default 10.0).
        min_soc: Minimum operating SOC [0.0 - 1.0] (default 0.10).
        max_soc: Maximum operating SOC [0.0 - 1.0] (default 0.95).
        sign: Sign convention (default LOAD_POSITIVE: +kW charge, -kW discharge).
    """

    def __init__(
        self,
        identity: ResourceIdentity,
        battery: Any = None,
        rated_kw: float = 5.0,
        capacity_kwh: float = 10.0,
        min_soc: float = 0.10,
        max_soc: float = 0.95,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> None:
        self._identity = identity
        self._rated_kw = float(rated_kw)
        self._capacity_kwh = float(capacity_kwh)
        self._min_soc = float(min_soc)
        self._max_soc = float(max_soc)
        self._sign = sign
        self.commands: list[DispatchCommand] = []

        if battery is not None:
            self._battery = battery
        else:
            from ochre import Battery

            start_time = kwargs.get("start_time", dt.datetime(2026, 1, 1, 0, 0))
            if isinstance(start_time, str):
                start_time = dt.datetime.fromisoformat(start_time).replace(tzinfo=None)
            elif isinstance(start_time, dt.datetime) and start_time.tzinfo is not None:
                start_time = start_time.replace(tzinfo=None)
            time_res = kwargs.get("time_res", dt.timedelta(minutes=15))
            if isinstance(time_res, (int, float)):
                time_res = dt.timedelta(seconds=time_res)
            duration = kwargs.get("duration", dt.timedelta(days=10))

            batt_kwargs = {k: v for k, v in kwargs.items() if k not in ("start_time", "time_res", "duration")}
            if batt_kwargs.get("enable_thermal_model"):
                batt_kwargs.setdefault("Initial Battery Temperature (C)", 20.0)
                if "schedule" not in batt_kwargs:
                    import pandas as pd
                    times = pd.date_range(start_time, start_time + duration, freq=time_res, inclusive="left")
                    batt_kwargs["schedule"] = pd.DataFrame({"Zone Temperature (C)": [20.0] * len(times)}, index=times)

            self._battery = Battery(
                capacity=self._rated_kw,
                capacity_kwh=self._capacity_kwh,
                start_time=start_time,
                time_res=time_res,
                duration=duration,
                save_results=False,
                verbosity=0,
                **batt_kwargs,
            )

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        resource_id: str,
        rated_kw: float = 5.0,
        capacity_kwh: float = 10.0,
        min_soc: float = 0.10,
        max_soc: float = 0.95,
        ochre: Mapping[str, Any] | None = None,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        start_time: Any = None,
        **kwargs: Any,
    ) -> OchreBatteryAdapter:
        ochre_cfg = dict(ochre or {})
        if start_time is not None:
            ochre_cfg["start_time"] = start_time
        identity = ResourceIdentity(
            resource_id=resource_id,
            kind=ResourceKind.STORAGE,
            source="ochre",
            metadata={
                "rated_kw": float(rated_kw),
                "capacity_kwh": float(capacity_kwh),
                "description": ochre_cfg.get("description", "OCHRE Battery Inverter Storage"),
                **kwargs,
            },
        )
        return cls(
            identity=identity,
            rated_kw=rated_kw,
            capacity_kwh=capacity_kwh,
            min_soc=min_soc,
            max_soc=max_soc,
            sign=sign,
            **ochre_cfg,
        )

    # -- properties --------------------------------------------------------

    @property
    def identity(self) -> ResourceIdentity:
        return self._identity

    @property
    def battery(self) -> Any:
        return self._battery

    @property
    def soc(self) -> float:
        return float(self._battery.soc)

    # -- FlexibilityModel --------------------------------------------------

    def get_envelope(self, intervals: Sequence[Interval]) -> FlexibilityEnvelope:
        if not intervals:
            return FlexibilityEnvelope(
                resource_id=self._identity.resource_id,
                sign=self._sign,
                points=(),
            )

        # SOC is coupled across the horizon: see storage_envelope for why a
        # per-interval headroom calculation overstates total energy.
        bounds = soc_coupled_power_bounds(
            intervals,
            soc=self.soc,
            capacity_kwh=self._capacity_kwh,
            rated_kw=self._rated_kw,
            min_soc=self._min_soc,
            max_soc=self._max_soc,
        )

        points: list[EnvelopePoint] = []
        for iv, (q_min, q_max) in zip(intervals, bounds):
            points.append(
                EnvelopePoint(
                    interval=iv,
                    q_min_kw=q_min,
                    q_max_kw=q_max,
                    q_baseline_kw=0.0,
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
        target_kw = float(command.power_kw if command.power_kw is not None else 0.0)

        # Enforce envelope physical bounds using the same calculation as
        # get_envelope so an envelope-feasible command is not clamped.
        (max_disch_neg, max_chg_kw) = soc_coupled_power_bounds(
            [command.interval],
            soc=self.soc,
            capacity_kwh=self._capacity_kwh,
            rated_kw=self._rated_kw,
            min_soc=self._min_soc,
            max_soc=self._max_soc,
        )[0]

        clamped_kw = max(max_disch_neg, min(max_chg_kw, target_kw))

        # Update OCHRE Battery simulator (P Setpoint in kW)
        res = self._battery.update(control_signal={"P Setpoint": clamped_kw})
        actual_power = float(res.get("Battery Electric Power (kW)", clamped_kw))

        return DispatchResponse(
            identity=self._identity,
            interval=command.interval,
            accepted=True,
            actual={
                "power_kw": actual_power,
                "soc": self.soc,
            },
            message=f"OCHRE Battery dispatched to {actual_power:.2f} kW (SOC: {self.soc:.1%})",
        )

    def release(self, interval: Interval) -> DispatchResponse:
        self._battery.update(control_signal={"P Setpoint": 0.0})
        return DispatchResponse(
            identity=self._identity,
            interval=interval,
            accepted=True,
            actual={"power_kw": 0.0, "soc": self.soc},
            message="OCHRE Battery released to idle (0 kW)",
        )

    # -- StateProvider -----------------------------------------------------

    def get_state(self, timestamp: datetime | None = None) -> ResourceState:
        ts = timestamp or datetime.now(UTC)
        power = float(getattr(self._battery, "electric_kw", 0.0))
        return ResourceState(
            identity=self._identity,
            timestamp=ts,
            values={
                "power_kw": power,
                "soc": self.soc,
                "capacity_kwh": self._capacity_kwh,
                "rated_kw": self._rated_kw,
                "min_soc": self._min_soc,
                "max_soc": self._max_soc,
            },
            quality=Quality.VALID,
        )

    def get_limits(self, now: datetime | None = None) -> Limits:
        return Limits(
            min_power_kw=-self._rated_kw,
            max_power_kw=self._rated_kw,
            sign=self._sign,
            constraints={
                "capacity_kwh": self._capacity_kwh,
                "min_soc": self._min_soc,
                "max_soc": self._max_soc,
            },
        )
