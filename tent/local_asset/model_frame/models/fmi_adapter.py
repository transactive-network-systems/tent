"""Bridge between FlexibilityModelInterface (FMI) adapters and TENT's ModelFrame."""

from __future__ import annotations

import logging

from tent.utils.helpers import require_aware
from datetime import datetime, timezone
from typing import Any, List, Mapping


from tent.flexibility import (
    DispatchCommand,
    Interval,
    SignConvention,
    capabilities_of,
    from_config as load_fmi_adapter,
)

from pydantic import ConfigDict
from pydantic.json_schema import SkipJsonSchema

from tent.config import ConfigModel, Setting

from .base import Model

_log = logging.getLogger(__name__)


class FlexibilityModelFrameAdapter(Model):
    """Wraps any FlexibilityModelInterface adapter as a Model in TENT's ModelFrame.

    Allows OCHRE dwellings, GridLAB-D houses/water heaters/feeders, pvlib solar arrays,
    and replay datasets to be nested directly in a ModelFrameAsset configuration.

    Sign Convention:
        FMI adapters are canonical load-positive (consumption > 0, generation < 0).
        TENT ModelFrame is generation-positive (consumption < 0, generation > 0).
        This adapter transparently negates power values to maintain consistency with TENT.
    """

    class Config(Model.Config):
        """The frame-level settings are declared; every other key is the FlexibilityModelInterface adapter
        configuration (``preset``, ``model_type``, ``rated_kw``, ``location``, ...), read by tent.flexibility.from_config
        and validated there. This is the one Config that allows undeclared keys, for that reason."""
        model_config = ConfigDict(extra='allow', arbitrary_types_allowed=True, validate_default=True)

        name: str = Setting('FlexibilityModelFrameAdapter', description='Name used in log messages.')
        resource_id: str | None = Setting(None, description='Adapter resource id; the topic when subscribe_key is not '
                                                            'given.')
        actuation_topic: str | None = Setting(None, description='Topic dispatch commands are published to; the '
                                                                'model\'s topic when null.')
        interval_duration_s: int = Setting(900, gt=0, description='[s] length of the interval the adapter is asked '
                                                                  'about.')
        adapter: SkipJsonSchema[Any] = Setting(None, description='An adapter object built in code (tests, hosts); '
                                                                 'when given, no adapter is built from the '
                                                                 'configuration.')
        fmi_config: dict[str, Any] | None = Setting(None, description='The adapter configuration as a nested dict; '
                                                                      'the undeclared keys of this block when null.')

    def __init__(self, config: Mapping[str, Any] | ConfigModel, parent: Any = None, **kwargs: Any) -> None:
        super(FlexibilityModelFrameAdapter, self).__init__(config, parent=parent, **kwargs)
        cfg = self.config
        self.name = cfg.name
        self.topic = cfg.subscribe_key if cfg.subscribe_key is not None else (cfg.resource_id or "fmi_model")
        self.actuation_topic = cfg.actuation_topic if cfg.actuation_topic is not None else self.topic
        self.n_points = int(cfg.demand_curve_points)
        self.interval_duration_s = int(cfg.interval_duration_s)
        self.error = False
        self.last_update_time: datetime | None = None

        if cfg.adapter is not None:
            self.adapter = cfg.adapter
        else:
            if load_fmi_adapter is None:
                raise RuntimeError("der_interface / flexibility_model_interface package is required")
            fmi_cfg = cfg.fmi_config if cfg.fmi_config is not None else dict(cfg.__pydantic_extra__ or {})
            if not fmi_cfg:
                raise ValueError(f'{self.describe(name=cfg.subscribe_key)}: no adapter configuration was given; give '
                                 f'fmi_config, the adapter keys (preset or model_type, ...) in this block, or an '
                                 f'adapter object.')
            self.adapter = load_fmi_adapter(fmi_cfg)

        self.caps = capabilities_of(self.adapter)

    def _get_interval(self, params: dict | None) -> Interval:
        params = params or {}
        start = params.get("interval_time", self.last_update_time or datetime.now(timezone.utc))
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        start = require_aware(start, 'interval_time')
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
        if self.n_points == 1:
            return [tent_min_power]
        step = (tent_max_power - tent_min_power) / (self.n_points - 1)
        return [tent_min_power + i * step for i in range(self.n_points)]

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

    def actuate(self, asset: Any, market: Any) -> None:
        """Command external physical adapter to follow cleared market schedule."""
        if not getattr(self.caps, "dispatch", False):
            return

        for time_interval in market.time_intervals:
            scheduled = asset.scheduled_powers.get_by_interval(time_interval)
            if scheduled is None:
                continue

            iv = Interval(
                start=time_interval.start_time,
                duration_s=int(time_interval.duration.total_seconds()),
            )
            command = DispatchCommand.for_power(
                identity=self.adapter.identity,
                interval=iv,
                power_kw=scheduled.value,
                sign=SignConvention.GENERATION_POSITIVE,
                source=f"tent:{market.name}",
            )
            try:
                response = self.adapter.dispatch(command)
                if not hasattr(self, "dispatch_responses"):
                    self.dispatch_responses = []
                self.dispatch_responses.append(response)
            except Exception as e:
                _log.error(f"{self.name}: dispatch failed for {time_interval.name}: {e}")


# Short alias
FlexibilityModel = FlexibilityModelFrameAdapter
FmiAdapter = FlexibilityModelFrameAdapter
