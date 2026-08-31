"""
Copyright (c) 2020, Battelle Memorial Institute
All rights reserved.
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import logging
import math
from datetime import datetime
from typing import Any

from tent.local_asset import LocalAsset

_log = logging.getLogger(__name__)


class SolarPvResource(LocalAsset):
    """Solar PV Resource Model for renewable solar generation.

    Supports dual-mode execution:
    1. Physics-based mode via pvlib / FlexibilityModelInterface (when location / pvlib_config is provided).
    2. Lightweight cosine approximation mode (default fallback for synthetic test scenarios).
    """

    def __init__(
        self,
        cloud_factor: float = 1.0,
        use_pvlib: bool = False,
        location: dict[str, Any] | None = None,
        system: dict[str, Any] | None = None,
        weather: dict[str, Any] | None = None,
        preset: str | None = None,
        *args,
        **kwargs,
    ):
        super(SolarPvResource, self).__init__(*args, **kwargs)
        self.cloudFactor = float(cloud_factor)
        self.use_pvlib = use_pvlib or (location is not None) or (preset is not None)
        self.pv_adapter = None

        if self.use_pvlib:
            try:
                from tent.flexibility import from_config as load_fmi_adapter

                if preset:
                    self.pv_adapter = load_fmi_adapter(
                        preset, rated_kw=self.maximum_power or 10.0
                    )
                elif location:
                    cfg = {
                        "model_type": "pvlib.pv",
                        "resource_id": self.name or "solar_pv",
                        "rated_kw": self.maximum_power or 10.0,
                        "location": location,
                        "system": system or {},
                        "weather": weather or {"kind": "clearsky", "cloud_factor": self.cloudFactor},
                    }
                    self.pv_adapter = load_fmi_adapter(cfg)
            except Exception as e:
                _log.warning(f"Could not initialize pvlib adapter for {self.name}: {e}. Falling back to cosine model.")
                self.pv_adapter = None

    def schedule_power(self, market):
        """Schedule power for active time intervals using pvlib physics or cosine model."""
        if self.pv_adapter is not None:
            try:
                from tent.flexibility import Interval, SignConvention

                time_intervals = list(market.time_intervals)
                intervals = [
                    Interval(
                        start=ti.start_time,
                        duration_s=int(ti.duration.total_seconds()),
                    )
                    for ti in time_intervals
                ]
                envelope = self.pv_adapter.get_envelope(intervals)
                for pt, ti in zip(envelope, time_intervals):
                    # Convert load-positive (negative generation) to TENT positive generation
                    power = -pt.q_baseline_kw
                    self.scheduled_powers.update(ti, power)
                    self.engagement_schedule.update(ti, True)

                self.schedule_calculated = True
                return
            except Exception as e:
                _log.warning(f"pvlib evaluation failed ({e}), using cosine fallback.")

        # Fallback: Sine/cosine approximation
        for time_interval in market.time_intervals:
            time_of_day: datetime = (
                time_interval.start_time + time_interval.duration / 2
            )
            hour_of_day: float = time_of_day.hour + time_of_day.minute / 60.0

            if hour_of_day < 5.5 or hour_of_day > 17.5:
                power = 0.0
            else:
                clear_sky_power = 0.5 * (
                    1.0 + math.cos((hour_of_day - 12) * 2.0 * math.pi / 12.0)
                )
                power = self.maximum_power * clear_sky_power * self.cloudFactor

            self.scheduled_powers.update(time_interval, power)
            self.engagement_schedule.update(time_interval, True)

        self.schedule_calculated = True
        _log.debug(
            f"Scheduled_powers for market: {market.name} --- {self.scheduled_powers.log_report}"
        )

    @property
    def table_name(self):
        return "solar_pv_resource"

    def get_record(self) -> dict:
        record = super(SolarPvResource, self).get_record()
        record.update(
            {"asset_id": self.id, "cloud_factor": self.cloudFactor}
        )
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(SolarPvResource, cls).get_archive_definitions()
        definitions.extend(
            [
                {
                    "name": "solar_pv_resource",
                    "inherits": "local_asset",
                    "columns": [
                        {
                            "name": "asset_id",
                            "foreign_key": "local_asset.id",
                            "kwargs": {"primary_key": True},
                        },
                        {"name": "cloud_factor", "type": "Float"},
                    ],
                }
            ]
        )
        return definitions


# Aliases
SolarPvResourceModel = SolarPvResource
