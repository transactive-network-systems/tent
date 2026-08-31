"""GridLAB-D Electric Vehicle (EV) Charger physical adapter.

Connects to a GridLAB-D `evcharger` or `evcharger_det` object, modeling vehicle
arrival/departure sessions, battery state-of-charge (SOC), and controllable charge rates.
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


class GridlabdEvAdapter:
    """Controls a GridLAB-D EV charger object providing flexible smart charging.

    Args:
        identity: Who this resource is.
        object_name: Name of the `evcharger` object in GridLAB-D.
        engine: GridlabdEngine session.
        rated_charging_kw: Maximum charging rate in kW (default 7.2 kW Level 2).
        battery_capacity_kwh: Vehicle battery capacity in kWh (default 60.0 kWh).
        target_soc: Required departure state-of-charge (default 0.90 = 90%).
        charging_efficiency: Charger AC-to-DC conversion efficiency (default 0.92).
        bidirectional: Whether charger supports V2G discharging (default False).
        sign: Sign convention (default LOAD_POSITIVE).
    """

    def __init__(
        self,
        identity: ResourceIdentity,
        object_name: str,
        engine: GridlabdEngine,
        rated_charging_kw: float = 7.2,
        battery_capacity_kwh: float = 60.0,
        target_soc: float = 0.90,
        charging_efficiency: float = 0.92,
        bidirectional: bool = False,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
    ) -> None:
        self._identity = identity
        self._object_name = object_name
        self._engine = engine
        self._rated_kw = float(rated_charging_kw)
        self._capacity_kwh = float(battery_capacity_kwh)
        self._target_soc = float(target_soc)
        self._efficiency = float(charging_efficiency)
        self._bidirectional = bool(bidirectional)
        self._sign = sign
        self.commands: list[DispatchCommand] = []

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        resource_id: str,
        rated_kw: float = 7.2,
        gridlabd: Mapping[str, Any] | None = None,
        location: Mapping[str, Any] | None = None,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> "GridlabdEvAdapter":
        gld_dict = dict(gridlabd or {})
        object_name = gld_dict.pop("object_name", resource_id)
        model_path = gld_dict.pop("model_path", None)
        engine = GridlabdEngine.get_or_create(model_path)

        rated_charging_kw = float(gld_dict.pop("rated_charging_kw", rated_kw))
        capacity_kwh = float(gld_dict.pop("battery_capacity_kwh", 60.0))
        target_soc = float(gld_dict.pop("target_soc", 0.90))
        efficiency = float(gld_dict.pop("charging_efficiency", 0.92))
        bidirectional = bool(gld_dict.pop("bidirectional", False))

        loc_str = ""
        if location:
            loc_dict = dict(location)
            lat = loc_dict.get("latitude")
            lon = loc_dict.get("longitude")
            if lat is not None and lon is not None:
                loc_str = f"{lat},{lon}"

        identity = ResourceIdentity(
            resource_id=resource_id,
            kind=ResourceKind.ELECTRIC_VEHICLE,
            source=f"gridlabd:{object_name}",
            location=loc_str,
            metadata={
                "object_name": object_name,
                "model_path": str(model_path) if model_path else "",
                "rated_charging_kw": rated_charging_kw,
                "battery_capacity_kwh": capacity_kwh,
                **kwargs,
            },
        )

        return cls(
            identity=identity,
            object_name=object_name,
            engine=engine,
            rated_charging_kw=rated_charging_kw,
            battery_capacity_kwh=capacity_kwh,
            target_soc=target_soc,
            charging_efficiency=efficiency,
            bidirectional=bidirectional,
            sign=sign,
        )

    # -- properties --------------------------------------------------------

    @property
    def identity(self) -> ResourceIdentity:
        return self._identity

    @property
    def rated_charging_kw(self) -> float:
        """Rated maximum charging power in kW (auto-read from GLD if defined)."""
        p = self._engine.get_float_property(self._object_name, "maximum_charge_rate", default=None)
        if p is not None and p > 0:
            return p if p < 100.0 else (p / 1000.0)
        p = self._engine.get_float_property(self._object_name, "rated_power", default=None)
        if p is not None and p > 0:
            return p if p < 100.0 else (p / 1000.0)
        return self._rated_kw

    @property
    def battery_capacity_kwh(self) -> float:
        """Vehicle battery capacity in kWh (auto-read from GLD if defined)."""
        cap = self._engine.get_float_property(self._object_name, "battery_size", default=None)
        if cap is not None and cap > 0:
            return cap
        cap = self._engine.get_float_property(self._object_name, "battery_capacity", default=None)
        if cap is not None and cap > 0:
            return cap
        return self._capacity_kwh

    @property
    def charging_efficiency(self) -> float:
        """Charger conversion efficiency (auto-read from GLD if defined)."""
        eff = self._engine.get_float_property(self._object_name, "charging_efficiency", default=None)
        if eff is not None and 0.5 <= eff <= 1.0:
            return eff
        return self._efficiency

    @property
    def is_connected(self) -> bool:
        """Whether the EV is plugged into the charger."""
        loc = self._engine.get_property(self._object_name, "vehicle_location", default=None)
        if loc is not None:
            return str(loc).strip().upper() == "HOME"
        conn = self._engine.get_property(self._object_name, "is_connected", default=True)
        if isinstance(conn, str):
            return conn.upper() in ("1", "TRUE", "CONNECTED", "YES")
        return bool(conn)

    @property
    def soc(self) -> float:
        """Current vehicle battery state-of-charge [0.0 - 1.0]."""
        s = self._engine.get_float_property(self._object_name, "battery_SOC", default=None)
        if s is not None and s >= 0.0:
            return (s / 100.0) if s > 1.0 else s
        s_gen = self._engine.get_float_property(self._object_name, "SOC", default=0.40)
        return (s_gen / 100.0) if s_gen > 1.0 else s_gen

    @property
    def current_power_kw(self) -> float:
        """Current charging power in kW."""
        p = self._engine.get_float_property(self._object_name, "charge_rate", default=None)
        if p is not None:
            return p if p < 100.0 else (p / 1000.0)
        p = self._engine.get_float_property(self._object_name, "actual_load", default=None)
        if p is not None:
            return p if p < 100.0 else (p / 1000.0)
        return 0.0

    # -- FlexibilityProvider -----------------------------------------------

    def get_envelope(
        self,
        intervals: Sequence[Interval],
        context: Mapping[str, Any] | None = None,
    ) -> FlexibilityEnvelope:
        if not intervals:
            raise AdapterError("get_envelope requires at least one interval")

        current_soc = self.soc
        connected = self.is_connected
        rated_kw = self.rated_charging_kw
        cap_kwh = self.battery_capacity_kwh
        eff = self.charging_efficiency

        points = []
        for interval in intervals:
            dur_h = interval.hours

            if not connected or current_soc >= self._target_soc:
                # Vehicle not plugged in or fully charged
                q_min = 0.0
                q_max = 0.0
                baseline_kw = 0.0
            else:
                # Energy needed to reach target SOC
                energy_needed_kwh = max(0.0, (self._target_soc - current_soc) * cap_kwh / eff)
                max_p = (energy_needed_kwh / dur_h) if dur_h > 0 else rated_kw
                q_max = max(0.0, min(rated_kw, max_p))
                q_min = -rated_kw if self._bidirectional else 0.0

                # Autonomous baseline charging: charge if connected and under target SOC
                baseline_kw = q_max

            points.append(
                EnvelopePoint(
                    interval,
                    q_min_kw=q_min,
                    q_max_kw=q_max,
                    q_baseline_kw=baseline_kw,
                )
            )

        return FlexibilityEnvelope(self._identity, tuple(points), sign=self._sign)

    # -- StateProvider -----------------------------------------------------

    def get_state(self, timestamp: datetime | None = None) -> ResourceState:
        ts = timestamp or datetime.now(timezone.utc)
        return ResourceState(
            identity=self._identity,
            timestamp=ts,
            values={
                "soc": self.soc,
                "power_kw": self.current_power_kw,
                "is_connected": self.is_connected,
                "battery_capacity_kwh": self.battery_capacity_kwh,
                "target_soc": self._target_soc,
            },
            quality=Quality.VALID,
        )

    def get_limits(self, timestamp: datetime | None = None) -> Limits:
        rated_kw = self.rated_charging_kw
        q_min = -rated_kw if self._bidirectional else 0.0
        return Limits(
            min_power_kw=q_min,
            max_power_kw=rated_kw,
            sign=self._sign,
            constraints={
                "battery_capacity_kwh": self.battery_capacity_kwh,
                "target_soc": self._target_soc,
                "soc": self.soc,
                "is_connected": self.is_connected,
            },
        )

    # -- DispatchTarget ----------------------------------------------------

    def dispatch(self, command: DispatchCommand) -> DispatchResponse:
        """Actuate EV charging rate based on market dispatch command."""
        self.commands.append(command)
        cmd_power = command.sign.to_canonical(command.power_kw)
        violations: list[str] = []

        dur_h = command.interval.hours
        current_soc = self.soc
        connected = self.is_connected
        rated_kw = self.rated_charging_kw
        cap_kwh = self.battery_capacity_kwh
        eff = self.charging_efficiency

        if not connected:
            actual_kw = 0.0
            if cmd_power > 0:
                violations.append("EV is disconnected; charging command ignored")
        else:
            energy_room_kwh = max(0.0, (self._target_soc - current_soc) * cap_kwh / eff)
            max_charge_kw = (energy_room_kwh / dur_h) if dur_h > 0 else 0.0
            q_max = max(0.0, min(rated_kw, max_charge_kw))
            q_min = -rated_kw if self._bidirectional else 0.0

            actual_kw = max(q_min, min(q_max, cmd_power))
            if abs(actual_kw - cmd_power) > 1e-3:
                violations.append(
                    f"Commanded {cmd_power:.2f} kW clamped to {actual_kw:.2f} kW due to EV bounds"
                )

        self._engine.set_property(self._object_name, "charge_rate", actual_kw)
        self._engine.set_property(self._object_name, "actual_load", actual_kw)

        # Update SOC
        if connected and actual_kw > 0:
            new_soc = min(self._target_soc, current_soc + (actual_kw * eff * dur_h) / cap_kwh)
        elif connected and actual_kw < 0:
            new_soc = max(0.05, current_soc - (abs(actual_kw) / eff * dur_h) / cap_kwh)
        else:
            new_soc = current_soc

        self._engine.set_property(self._object_name, "battery_SOC", new_soc * 100.0)
        self._engine.set_property(self._object_name, "battery_capacity", new_soc * cap_kwh)
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
                "is_connected": connected,
            },
            violations=tuple(violations),
        )

    def release(self, interval: Interval) -> DispatchResponse:
        self._engine.set_property(self._object_name, "charge_rate", 0.0)
        return DispatchResponse(
            identity=self._identity,
            interval=interval,
            accepted=True,
            actual={"power_kw": 0.0, "soc": self.soc},
        )
