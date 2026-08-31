"""GridLAB-D Feeder / Substation physical adapter.

Connects to a GridLAB-D feeder head / substation `node` or `substation` object,
measuring aggregate feeder power flow, monitoring distribution transformer limits,
and reporting grid-level telemetry.
"""

from __future__ import annotations

import logging
import math
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


class GridlabdFeederAdapter:
    """Monitors and controls aggregate power at a GridLAB-D feeder or substation head.

    Args:
        identity: Who this resource is.
        object_name: The name of the substation / swing node in the GridLAB-D model.
        engine: GridlabdEngine instance connected to the simulation.
        transformer_capacity_kw: Rated transformer capacity (kW).
        allow_reverse_flow: Whether reverse power flow (export to transmission) is allowed.
        sign: Sign convention (default LOAD_POSITIVE).
    """

    def __init__(
        self,
        identity: ResourceIdentity,
        object_name: str,
        engine: GridlabdEngine,
        transformer_capacity_kw: float = 5000.0,
        allow_reverse_flow: bool = True,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
    ) -> None:
        self._identity = identity
        self._object_name = object_name
        self._engine = engine
        self._capacity_kw = float(transformer_capacity_kw)
        self._allow_reverse = bool(allow_reverse_flow)
        self._sign = sign
        self.commands: list[DispatchCommand] = []

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        resource_id: str,
        rated_kw: float = 5000.0,
        gridlabd: Mapping[str, Any] | None = None,
        location: Mapping[str, Any] | None = None,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> "GridlabdFeederAdapter":
        """Construct a feeder adapter from JSON/dict configuration."""
        gld_dict = dict(gridlabd or {})
        object_name = gld_dict.pop("object_name", resource_id)
        model_path = gld_dict.pop("model_path", None)
        engine = GridlabdEngine.get_or_create(model_path)

        capacity_kw = float(gld_dict.pop("transformer_capacity_kw", rated_kw))
        allow_reverse = bool(gld_dict.pop("allow_reverse_flow", True))

        loc_str = ""
        if location:
            loc_dict = dict(location)
            lat = loc_dict.get("latitude")
            lon = loc_dict.get("longitude")
            if lat is not None and lon is not None:
                loc_str = f"{lat},{lon}"

        identity = ResourceIdentity(
            resource_id=resource_id,
            kind=ResourceKind.AGGREGATE,
            source=f"gridlabd:{object_name}",
            location=loc_str,
            metadata={
                "object_name": object_name,
                "model_path": str(model_path) if model_path else "",
                "transformer_capacity_kw": capacity_kw,
                **kwargs,
            },
        )

        return cls(
            identity=identity,
            object_name=object_name,
            engine=engine,
            transformer_capacity_kw=capacity_kw,
            allow_reverse_flow=allow_reverse,
            sign=sign,
        )

    # -- properties --------------------------------------------------------

    @property
    def identity(self) -> ResourceIdentity:
        return self._identity

    @property
    def total_real_power_kw(self) -> float:
        """Measure aggregate real power at the substation node."""
        # Try standard GridLAB-D powerflow properties
        p = self._engine.get_float_property(self._object_name, "distribution_load", default=None)
        if p is not None:
            return p
        p = self._engine.get_float_property(self._object_name, "measured_real_power", default=None)
        if p is not None:
            return p / 1000.0  # Convert W to kW if measured in W
        return 1500.0  # Default nominal feeder load

    @property
    def total_reactive_power_kvar(self) -> float:
        """Measure aggregate reactive power at the substation node."""
        q = self._engine.get_float_property(self._object_name, "measured_reactive_power", default=None)
        if q is not None:
            return q / 1000.0
        return 300.0

    @property
    def voltage_v(self) -> float:
        """Measure line-to-neutral voltage at the substation swing bus."""
        return self._engine.get_float_property(self._object_name, "voltage_A", default=7200.0)

    # -- FlexibilityProvider -----------------------------------------------

    def get_envelope(
        self,
        intervals: Sequence[Interval],
        context: Mapping[str, Any] | None = None,
    ) -> FlexibilityEnvelope:
        if not intervals:
            raise AdapterError("get_envelope requires at least one interval")

        q_min = -self._capacity_kw if self._allow_reverse else 0.0
        q_max = self._capacity_kw

        points = []
        for interval in intervals:
            hour = interval.start.hour + (interval.start.minute / 60.0)
            # Diurnal baseline load curve for distribution feeder
            if 6.0 <= hour <= 9.0:
                baseline_kw = 1200.0 + 400.0 * math.sin(math.pi * (hour - 6.0) / 3.0)  # Morning rush
            elif 17.0 <= hour <= 21.0:
                baseline_kw = 1600.0 + 600.0 * math.sin(math.pi * (hour - 17.0) / 4.0) # Evening peak
            elif 10.0 <= hour <= 16.0:
                baseline_kw = 1100.0  # Midday
            else:
                baseline_kw = 750.0   # Night base

            live_p = self._engine.get_float_property(self._object_name, "distribution_load", default=None)
            if live_p is not None and live_p > 0:
                baseline_kw = live_p

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
        hour = ts.hour + (ts.minute / 60.0)
        if 6.0 <= hour <= 9.0:
            base_kw = 1200.0 + 400.0 * math.sin(math.pi * (hour - 6.0) / 3.0)
        elif 17.0 <= hour <= 21.0:
            base_kw = 1600.0 + 600.0 * math.sin(math.pi * (hour - 17.0) / 4.0)
        elif 10.0 <= hour <= 16.0:
            base_kw = 1100.0
        else:
            base_kw = 750.0

        curt = self._engine.get_property(self._object_name, "curtailment_target")
        p = float(curt) if curt is not None else self.total_real_power_kw

        return ResourceState(
            identity=self._identity,
            timestamp=ts,
            values={
                "total_real_power_kw": p,
                "total_reactive_power_kvar": p * 0.25,
                "voltage_v": self.voltage_v,
                "transformer_capacity_kw": self._capacity_kw,
            },
            quality=Quality.VALID,
        )

    def get_limits(self, timestamp: datetime | None = None) -> Limits:
        q_min = -self._capacity_kw if self._allow_reverse else 0.0
        return Limits(
            min_power_kw=q_min,
            max_power_kw=self._capacity_kw,
            sign=self._sign,
            constraints={
                "transformer_capacity_kw": self._capacity_kw,
                "allow_reverse_flow": self._allow_reverse,
            },
        )

    # -- DispatchTarget ----------------------------------------------------

    def dispatch(self, command: DispatchCommand) -> DispatchResponse:
        """Dispatches an aggregate feeder power target or scheduled curtailment."""
        self.commands.append(command)
        cmd_power = command.sign.to_canonical(command.power_kw)
        violations: list[str] = []

        # Clamp command to transformer capacity
        q_min = -self._capacity_kw if self._allow_reverse else 0.0
        actual_kw = max(q_min, min(self._capacity_kw, cmd_power))

        if abs(actual_kw - cmd_power) > 1e-3:
            violations.append(
                f"Commanded {cmd_power:.2f} kW exceeds transformer capacity [{q_min:.2f}, {self._capacity_kw:.2f}] kW"
            )

        if abs(actual_kw - self.total_real_power_kw) > 1e-3 and actual_kw != 0.0:
            self._engine.set_property(self._object_name, "curtailment_target", actual_kw)
            self._engine.set_property(self._object_name, "distribution_load", actual_kw)

        self._engine.step(command.interval.duration_s)

        new_power = self.total_real_power_kw
        actual_in_sign = command.sign.from_canonical(new_power)

        return DispatchResponse(
            identity=self._identity,
            interval=command.interval,
            accepted=True,
            actual={
                "power_kw": actual_in_sign,
                "total_real_power_kw": actual_in_sign,
                "total_reactive_power_kvar": self.total_reactive_power_kvar,
                "voltage_v": self.voltage_v,
            },
            violations=tuple(violations),
        )

    def release(self, interval: Interval) -> DispatchResponse:
        self._engine.set_property(self._object_name, "curtailment_target", None)
        return DispatchResponse(
            identity=self._identity,
            interval=interval,
            accepted=True,
            actual={"total_real_power_kw": self.total_real_power_kw},
        )
