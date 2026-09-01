"""OCHRE Electric Vehicle Charger physical adapter.

Connects directly to an NREL OCHRE `ElectricVehicle` model, tracking vehicle commute
departure/arrival presence, battery state-of-charge, and Level 1/Level 2 charging throttling.
"""

from __future__ import annotations

import datetime as dt
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

_log = logging.getLogger(__name__)
UTC = timezone.utc


class OchreEvAdapter:
    """Controls an OCHRE ElectricVehicle object as a flexible charging resource.

    Args:
        identity: Who this resource is.
        ev: Initialized OCHRE ElectricVehicle object (optional).
        rated_kw: Maximum charging rate in kW (default 7.2).
        capacity_kwh: EV battery capacity in kWh (default 65.0).
        initial_soc: Initial battery state of charge [0.0 - 1.0] (default 0.40).
        target_soc: Target departure state of charge [0.0 - 1.0] (default 0.90).
        min_soc: Minimum operating SOC [0.0 - 1.0] (default 0.10).
        sign: Sign convention (default LOAD_POSITIVE).
    """

    def __init__(
        self,
        identity: ResourceIdentity,
        ev: Any = None,
        rated_kw: float = 7.2,
        capacity_kwh: float = 65.0,
        initial_soc: float = 0.40,
        target_soc: float = 0.90,
        min_soc: float = 0.10,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> None:
        self._identity = identity
        self._rated_kw = float(rated_kw)
        self._capacity_kwh = float(capacity_kwh)
        self._target_soc = float(target_soc)
        self._min_soc = float(min_soc)
        self._sign = sign
        self._force_connected = bool(kwargs.get("force_connected", True))
        self.commands: list[DispatchCommand] = []

        if ev is not None:
            self._ev = ev
        else:
            from ochre import ElectricVehicle

            start_time = kwargs.get("start_time", dt.datetime(2026, 1, 1, 0, 0))
            if isinstance(start_time, str):
                start_time = dt.datetime.fromisoformat(start_time).replace(tzinfo=None)
            elif isinstance(start_time, dt.datetime) and start_time.tzinfo is not None:
                start_time = start_time.replace(tzinfo=None)
            time_res = kwargs.get("time_res", dt.timedelta(minutes=15))
            if isinstance(time_res, (int, float)):
                time_res = dt.timedelta(seconds=time_res)
            duration = kwargs.get("duration", dt.timedelta(days=10))

            self._ev = ElectricVehicle(
                vehicle_type=kwargs.get("vehicle_type", "BEV"),
                charging_level=kwargs.get("charging_level", "Level 2"),
                capacity=self._capacity_kwh,
                range=kwargs.get("range", 200),
                start_time=start_time,
                time_res=time_res,
                duration=duration,
                save_results=False,
                verbosity=0,
                **{
                    k: v
                    for k, v in kwargs.items()
                    if k not in (
                        "start_time",
                        "time_res",
                        "duration",
                        "vehicle_type",
                        "charging_level",
                        "capacity",
                        "range",
                        "force_connected",
                    )
                },
            )
            if self._force_connected:
                import pandas as pd
                ev_event = pd.DataFrame([{
                    "day_id": 1,
                    "start_time": start_time,
                    "end_time": start_time + duration,
                    "duration": duration,
                    "start_soc": float(initial_soc) if initial_soc is not None else 0.40,
                    "end_soc": float(target_soc),
                    "initial_soc": float(initial_soc) if initial_soc is not None else 0.40,
                }])
                if hasattr(self._ev, "all_events") and self._ev.all_events is not None:
                    self._ev.all_events = pd.concat([ev_event, self._ev.all_events], ignore_index=True)
                else:
                    self._ev.all_events = ev_event
                self._ev.reset_time()
                self._ev.in_event = True

            # Override initial SOC if provided
            if initial_soc is not None:
                self._ev.soc = float(initial_soc)
                self._ev.next_soc = float(initial_soc)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        resource_id: str,
        rated_kw: float = 7.2,
        capacity_kwh: float = 65.0,
        initial_soc: float = 0.40,
        target_soc: float = 0.90,
        min_soc: float = 0.10,
        ochre: Mapping[str, Any] | None = None,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        start_time: Any = None,
        **kwargs: Any,
    ) -> OchreEvAdapter:
        ochre_cfg = dict(ochre or {})
        if start_time is not None:
            ochre_cfg["start_time"] = start_time
        identity = ResourceIdentity(
            resource_id=resource_id,
            kind=ResourceKind.ELECTRIC_VEHICLE,
            source="ochre",
            metadata={
                "rated_kw": float(rated_kw),
                "capacity_kwh": float(capacity_kwh),
                "description": ochre_cfg.get("description", "OCHRE Electric Vehicle"),
                **kwargs,
            },
        )
        combined_cfg = {**ochre_cfg, **kwargs}
        return cls(
            identity=identity,
            rated_kw=rated_kw,
            capacity_kwh=capacity_kwh,
            initial_soc=initial_soc,
            target_soc=target_soc,
            min_soc=min_soc,
            sign=sign,
            **combined_cfg,
        )

    # -- properties --------------------------------------------------------

    @property
    def identity(self) -> ResourceIdentity:
        return self._identity

    @property
    def ev(self) -> Any:
        return self._ev

    @property
    def soc(self) -> float:
        return float(self._ev.soc)

    @property
    def is_connected(self) -> bool:
        if self._force_connected:
            return True
        return bool(getattr(self._ev, "in_event", False))

    # -- FlexibilityModel --------------------------------------------------

    def get_envelope(self, intervals: Sequence[Interval]) -> FlexibilityEnvelope:
        if not intervals:
            return FlexibilityEnvelope(
                resource_id=self._identity.resource_id,
                sign=self._sign,
                points=(),
            )

        soc = self.soc
        connected = self.is_connected
        points: list[EnvelopePoint] = []

        for iv in intervals:
            duration_hours = max(iv.duration.total_seconds() / 3600.0, 1e-4)

            if not connected or soc >= self._target_soc:
                q_min = 0.0
                q_max = 0.0
                q_base = 0.0
            else:
                remaining_kwh = max(0.0, (self._target_soc - soc) * self._capacity_kwh)
                max_charge_kw = min(self._rated_kw, remaining_kwh / duration_hours)
                q_min = 0.0
                q_max = max_charge_kw
                q_base = max_charge_kw

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

        if not self.is_connected or self.soc >= self._target_soc:
            clamped_kw = 0.0
        else:
            clamped_kw = max(0.0, min(self._rated_kw, target_kw))

        violations: list[str] = []
        if abs(clamped_kw - target_kw) > 1e-3:
            violations.append(
                f"Commanded {target_kw:.2f} kW clamped to {clamped_kw:.2f} kW"
            )

        if self._force_connected:
            self._ev.in_event = True

        res = self._ev.update(control_signal={"P Setpoint": clamped_kw, "Max Power": clamped_kw})
        actual_power = float(res.get("EV Electric Power (kW)", clamped_kw))

        return DispatchResponse(
            identity=self._identity,
            interval=command.interval,
            accepted=True,
            actual={
                "power_kw": command.sign.from_canonical(actual_power),
                "soc": self.soc,
            },
            violations=tuple(violations),
            message=f"OCHRE EV dispatched to {actual_power:.2f} kW (SOC: {self.soc:.1%})",
        )

    def release(self, interval: Interval) -> DispatchResponse:
        self._ev.update(control_signal={"P Setpoint": 0.0, "Max Power": self._rated_kw})
        return DispatchResponse(
            identity=self._identity,
            interval=interval,
            accepted=True,
            actual={"power_kw": 0.0, "soc": self.soc},
            message="OCHRE EV released to idle",
        )

    # -- StateProvider -----------------------------------------------------

    def get_state(self, timestamp: datetime | None = None) -> ResourceState:
        ts = timestamp or datetime.now(UTC)
        power = float(getattr(self._ev, "electric_kw", 0.0))
        return ResourceState(
            identity=self._identity,
            timestamp=ts,
            values={
                "power_kw": power,
                "soc": self.soc,
                "is_connected": self.is_connected,
                "capacity_kwh": self._capacity_kwh,
                "rated_kw": self._rated_kw,
                "target_soc": self._target_soc,
            },
            quality=Quality.VALID,
        )

    def get_limits(self, now: datetime | None = None) -> Limits:
        return Limits(
            min_power_kw=0.0,
            max_power_kw=self._rated_kw,
            sign=self._sign,
            constraints={
                "capacity_kwh": self._capacity_kwh,
                "min_soc": self._min_soc,
                "max_soc": self._target_soc,
            },
        )
