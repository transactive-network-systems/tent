"""Bridge between FlexibilityModelInterface (FMI) adapters and TENT's ModelFrame."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List, Mapping

import numpy as np

from tent.flexibility import (
    DispatchCommand,
    Interval,
    SignConvention,
    capabilities_of,
    from_config as load_fmi_adapter,
)

_log = logging.getLogger(__name__)


class FlexibilityModelFrameAdapter(object):
    """Wraps any FlexibilityModelInterface adapter as a Model in TENT's ModelFrame.

    Allows OCHRE dwellings, GridLAB-D houses/water heaters/feeders, pvlib solar arrays,
    and replay datasets to be nested directly in a ModelFrameAsset configuration.

    Sign Convention:
        FMI adapters are canonical load-positive (consumption > 0, generation < 0).
        TENT ModelFrame is generation-positive (consumption < 0, generation > 0).
        This adapter transparently negates power values to maintain consistency with TENT.
    """

    def __init__(self, config: Mapping[str, Any], parent: Any = None, **kwargs: Any) -> None:
        self.name = config.get("name", "FlexibilityModelFrameAdapter")
        self.topic = config.get("topic", config.get("resource_id", "fmi_model"))
        self.actuation_topic = config.get("actuation_topic", self.topic)
        self.parent = parent
        self.n_points = int(config.get("demand_curve_points", 2))
        self.interval_duration_s = int(config.get("interval_duration_s", 900))
        self.error = False
        self.last_update_time: datetime | None = None

        adapter_obj = config.get("adapter")
        if adapter_obj is not None:
            self.adapter = adapter_obj
        else:
            if load_fmi_adapter is None:
                raise RuntimeError("der_interface / flexibility_model_interface package is required")
            fmi_cfg = config.get("fmi_config", config)
            self.adapter = load_fmi_adapter(fmi_cfg)

        self.caps = capabilities_of(self.adapter)

    def _get_interval(self, params: dict | None) -> Interval:
        params = params or {}
        start = params.get("interval_time", self.last_update_time or datetime.now(timezone.utc))
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return Interval(start=start, duration_s=self.interval_duration_s)

    def predict_flexibility(self, params: dict = None) -> List[float]:
        """Return power flexibility curve in TENT sign convention (generation positive)."""
        iv = self._get_interval(params)
        env = self.adapter.get_envelope([iv])
        pt = env[0]

        # Convert from load-positive (FMI) to generation-positive (TENT)
        # Load: q_min_kw (max load) -> most negative power in TENT
        # Load: q_max_kw (min load) -> least negative power in TENT
        # Generation: q_baseline_kw (< 0) -> positive generation in TENT
        tent_min_power = -pt.q_max_kw
        tent_max_power = -pt.q_min_kw

        if self.n_points <= 2:
            return [tent_min_power, tent_max_power]
        return list(np.linspace(tent_min_power, tent_max_power, num=self.n_points))

    def predict_power(self, params: dict = None, set_point: float = None) -> float:
        """Return expected/baseline power in TENT sign convention (generation positive)."""
        iv = self._get_interval(params)
        env = self.adapter.get_envelope([iv])
        pt = env[0]
        # Invert load-positive to TENT generation-positive
        return -pt.q_baseline_kw

    def update_data(self, data: dict, now: datetime) -> None:
        """Update live telemetry or dispatch feedback from the platform."""
        self.last_update_time = now
        if hasattr(self.adapter, "update_telemetry"):
            self.adapter.update_telemetry(data, now)
        elif hasattr(self.adapter, "update_data"):
            self.adapter.update_data(data, now)


# Short alias
FlexibilityModel = FlexibilityModelFrameAdapter
FmiAdapter = FlexibilityModelFrameAdapter
