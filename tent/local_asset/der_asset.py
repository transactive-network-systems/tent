from __future__ import annotations

"""
Copyright (c) 2020, Battelle Memorial Institute
All rights reserved.

PACIFIC NORTHWEST NATIONAL LABORATORY
operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
under Contract DE-AC05-76RL01830

A LocalAsset backed by an external FlexibilityModelInterface adapter.
``DerAsset`` / ``FlexibilityModelAsset`` subclasses TENT's native ``ModelFrameAsset``,
connecting any physical adapter (OCHRE, GridLAB-D, pvlib, Replay) to TENT's transactive
market machinery.
"""

import logging
from typing import Any

from tent.flexibility import (
    DispatchCommand,
    FlexibilityEnvelope,
    Interval,
    SignConvention,
    capabilities_of,
    from_config as load_fmi_adapter,
)

from tent.containers import Vertex
from tent.local_asset.model_frame.model_frame_asset import ModelFrameAsset
from tent.local_asset.model_frame.models.fmi_adapter import FlexibilityModelFrameAdapter
from tent.utils.helpers import get_class_from_spec

_log = logging.getLogger(__name__)

#: TENT reports generated power as positive.
TENT_SIGN = SignConvention.GENERATION_POSITIVE


class DerAsset(ModelFrameAsset):
    """A local asset whose physics live in an external model, subclassing ModelFrameAsset.

    Args:
        flexibility: A ``der-interface`` / ``flexibility_model_interface`` object or spec.
        dispatch: Optional object implementing ``dispatch()`` and ``release()``.
        state: Optional object implementing ``get_state()`` and ``get_limits()``.
        price_range: ``(low, high)`` prices in $/kWh used to place the vertices.
        enforce_limits: When True, verify envelope against physical limits.
        actuate_from_schedule: When True, dispatch scheduled power during ``actuate()``.
    """

    def __init__(
        self,
        flexibility,
        dispatch=None,
        state=None,
        price_range=(0.0, 0.10),
        enforce_limits=True,
        actuate_from_schedule=True,
        model_configs=None,
        *args,
        **kwargs,
    ):
        self.flexibility = self._resolve(flexibility, "flexibility")
        self.dispatch_target = self._resolve(dispatch, "dispatch")
        self.state_provider = self._resolve(state, "state")

        low, high = (float(p) for p in price_range)
        if low >= high:
            raise ValueError(
                f"price_range must be (low, high) with low < high, got ({low}, {high})"
            )
        self.low_price = low
        self.high_price = high
        self.enforce_limits = bool(enforce_limits)
        self.actuate_from_schedule = bool(actuate_from_schedule)

        # Infer dispatch / state capabilities if not explicitly provided
        if capabilities_of is not None and self.flexibility is not None:
            caps = capabilities_of(self.flexibility)
            if self.dispatch_target is None and caps.dispatch:
                self.dispatch_target = self.flexibility
            if self.state_provider is None and caps.state:
                self.state_provider = self.flexibility

            if not caps.flexibility:
                raise ValueError(
                    f"{getattr(self, 'name', 'DerAsset')}: flexibility provider "
                    f"{type(self.flexibility).__name__} has no get_envelope() method."
                )

        # Build ModelFrame configuration wrapping the primary flexibility adapter
        if model_configs is None:
            model_configs = {
                "demand_curve_points": 2,
                "models": [
                    {
                        "topic": kwargs.get("name", "primary_fmi_model"),
                        "model_type": "fmi",
                        "model_config": {
                            "adapter": self.flexibility,
                        },
                    }
                ],
            }

        super(DerAsset, self).__init__(model_configs=model_configs, *args, **kwargs)

        # Dynamic state
        self._envelope: Any = None
        self._interval_map: dict = {}
        self.dispatch_responses: list = []

    @staticmethod
    def _resolve(spec, label):
        """Accept either a live object or a class spec from JSON configuration."""
        if spec is None:
            return None
        if isinstance(spec, str):
            if load_fmi_adapter is not None:
                return load_fmi_adapter(spec)
            return spec
        if not isinstance(spec, dict):
            return spec

        spec = dict(spec)
        if "preset" in spec or "model_type" in spec:
            if load_fmi_adapter is not None:
                return load_fmi_adapter(spec)

        module_name = spec.get("module_name", "")
        if module_name.startswith("der_interface") or module_name.startswith("flexibility_model_interface"):
            spec["module_name"] = module_name.replace("der_interface", "tent.flexibility").replace(
                "flexibility_model_interface", "tent.flexibility"
            )

        adapter_class = get_class_from_spec(
            spec, default_module_prefix="tent.flexibility.adapters"
        )
        factory = spec.pop("factory", None)
        builder = getattr(adapter_class, factory) if factory else adapter_class
        return builder(**spec)

    @staticmethod
    def _to_interval(time_interval) -> Any:
        """Convert a TENT ``TimeInterval`` into a flexibility interface ``Interval``."""
        if Interval is None:
            return None
        return Interval(
            start=time_interval.start_time,
            duration_s=int(time_interval.duration.total_seconds()),
        )

    def _tent_envelope(self, market) -> Any:
        """Fetch envelope and convert it into TENT's sign convention."""
        time_intervals = list(market.time_intervals)
        intervals = [self._to_interval(ti) for ti in time_intervals]

        envelope = self.flexibility.get_envelope(intervals)
        if len(envelope) != len(intervals):
            raise RuntimeError(
                f"{self.name}: adapter returned {len(envelope)} envelope points "
                f"for {len(intervals)} requested intervals."
            )

        if self.enforce_limits and self.state_provider is not None:
            try:
                limits = self.state_provider.get_limits()
                if limits.sign is envelope.sign:
                    envelope.assert_within(limits)
            except Exception as e:
                _log.warning(f"{self.name}: could not check limits ({e})")

        envelope = envelope.in_convention(TENT_SIGN)
        self._envelope = envelope
        self._interval_map = {
            point.interval: ti for point, ti in zip(envelope, time_intervals)
        }
        return envelope

    def schedule_power(self, market):
        """Schedule baseline power reported by external flexibility model."""
        envelope = self._tent_envelope(market)
        for point in envelope:
            time_interval = self._interval_map[point.interval]
            self.scheduled_powers.update(time_interval, point.q_baseline_kw)
            self.engagement_schedule.update(time_interval, point.flexible)

        self.schedule_calculated = True
        _log.debug(f"{self.name} scheduled_powers: {self.scheduled_powers.log_report}")

    def update_vertices(self, market):
        """Create supply/demand vertices from the flexibility envelope."""
        if self._envelope is None:
            self._tent_envelope(market)

        for point in self._envelope:
            time_interval = self._interval_map[point.interval]
            if not point.flexible:
                vertices = [
                    Vertex(
                        price=float("inf"),
                        cost=0.0,
                        quantity=point.q_baseline_kw,
                    )
                ]
            else:
                vertices = [
                    Vertex(
                        price=self.low_price,
                        cost=self.cost(point.q_min_kw),
                        quantity=point.q_min_kw,
                    ),
                    Vertex(
                        price=self.high_price,
                        cost=self.cost(point.q_max_kw),
                        quantity=point.q_max_kw,
                    ),
                ]
            self.active_vertices.update(time_interval, vertices, replace=True)

        _log.debug(f"{self.name} active_vertices: {self.active_vertices.log_report}")

    def actuate(self, mkt):
        """Command external model to follow cleared market schedule."""
        if self.dispatch_target is None or not self.actuate_from_schedule:
            return

        for time_interval in mkt.time_intervals:
            scheduled = self.scheduled_powers.get_by_interval(time_interval)
            if scheduled is None:
                continue

            interval = self._to_interval(time_interval)
            command = DispatchCommand.for_power(
                identity=self.flexibility.identity,
                interval=interval,
                power_kw=scheduled.value,
                sign=TENT_SIGN,
                source=f"tent:{mkt.name}",
            )

            try:
                response = self.dispatch_target.dispatch(command)
                self.dispatch_responses.append(response)
            except Exception as e:
                _log.error(f"{self.name}: dispatch failed for {time_interval.name}: {e}")

    @property
    def table_name(self):
        return "der_asset"

    def get_record(self) -> dict:
        record = super(DerAsset, self).get_record()
        record.update(
            {
                "asset_id": self.id,
                "adapter_class": type(self.flexibility).__name__,
                "resource_id": getattr(self.flexibility.identity, "resource_id", ""),
                "low_price": self.low_price,
                "high_price": self.high_price,
                "dispatchable": self.dispatch_target is not None,
            }
        )
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(DerAsset, cls).get_archive_definitions()
        definitions.extend(
            [
                {
                    "name": "der_asset",
                    "inherits": "local_asset",
                    "columns": [
                        {
                            "name": "asset_id",
                            "foreign_key": "local_asset.id",
                            "kwargs": {"primary_key": True},
                        },
                        {"name": "adapter_class", "type": "String"},
                        {"name": "resource_id", "type": "String"},
                        {"name": "low_price", "type": "Float"},
                        {"name": "high_price", "type": "Float"},
                        {"name": "dispatchable", "type": "Boolean"},
                    ],
                }
            ]
        )
        return definitions


# Modernized alias
FlexibilityModelAsset = DerAsset
