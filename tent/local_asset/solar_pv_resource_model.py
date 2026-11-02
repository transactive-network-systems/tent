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
The views and conclusions contained in the software and documentation are those
of the authors and should not be interpreted as representing official policies,
either expressed or implied, of the FreeBSD Project.
This material was prepared as an account of work sponsored by an agency of the
United States Government. Neither the United States Government nor the United
States Department of Energy, nor Battelle, nor any of their employees, nor any
jurisdiction or organization that has cooperated in th.e development of these
materials, makes any warranty, express or implied, or assumes any legal
liability or responsibility for the accuracy, completeness, or usefulness or
any information, apparatus, product, software, or process disclosed, or
represents that its use would not infringe privately owned rights.
Reference herein to any specific commercial product, process, or service by
trade name, trademark, manufacturer, or otherwise does not necessarily
constitute or imply its endorsement, recommendation, or favoring by the
United States Government or any agency thereof, or Battelle Memorial Institute.
The views and opinions of authors expressed herein do not necessarily state or
reflect those of the United States Government or any agency thereof.

PACIFIC NORTHWEST NATIONAL LABORATORY
operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
under Contract DE-AC05-76RL01830
"""

import logging
import math
from datetime import datetime
from typing import Any

from tent.config import ConfigModel, Setting
from tent.local_asset import LocalAsset

_log = logging.getLogger(__name__)


class SolarPvResource(LocalAsset):
    """Solar PV Resource Model for renewable solar generation.

    Supports dual-mode execution:
    1. Physics-based mode via pvlib / FlexibilityModelInterface when a `site` (pvlib location dict) or `preset` is given.
       (`location` remains the ordinary Entity text field.)
    2. Lightweight cosine approximation (default when no pvlib site is configured).
    """

    class Config(LocalAsset.Config):
        cloud_factor: float = Setting(1.0, ge=0, le=1, reconfigurable=True,
                                      description='[dimensionless] fraction of clear-sky output; 1.0 is clear sky.')
        use_pvlib: bool = Setting(False, description='Model the array with pvlib (tent.flexibility) instead of the '
                                                     'cosine approximation; implied by site or preset.')
        site: dict[str, Any] | None = Setting(None, description='pvlib site: latitude, longitude, tz, altitude.')
        system: dict[str, Any] | None = Setting(None, description='pvlib system: surface_tilt, surface_azimuth, ...')
        weather: dict[str, Any] | None = Setting(None, description='pvlib weather source; default clear sky scaled '
                                                                   'by cloud_factor.')
        preset: str | None = Setting(None, description='Name of a tent.flexibility PV preset to build the adapter '
                                                       'from.')

    def __init__(self, transactive_node, config: ConfigModel | None = None, **kwargs):
        cfg = self._configure(config, kwargs)
        super(SolarPvResource, self).__init__(transactive_node, config=cfg)
        self.cloudFactor = float(cfg.cloud_factor)
        self.use_pvlib = cfg.use_pvlib or (cfg.site is not None) or (cfg.preset is not None)
        self.pv_adapter = None

        if self.use_pvlib:
            # A configured pvlib site that cannot be built is a configuration error: fail here, at configure time,
            # rather than silently running the cosine approximation in its place.
            from tent.flexibility import from_config as load_fmi_adapter
            if cfg.preset:
                self.pv_adapter = load_fmi_adapter(cfg.preset, rated_kw=self.maximum_power or 10.0)
            else:
                adapter_config = {
                    "model_type": "pvlib.pv",
                    "resource_id": self.name or "solar_pv",
                    "rated_kw": self.maximum_power or 10.0,
                    "location": cfg.site,
                    "system": cfg.system or {},
                    "weather": cfg.weather or {"kind": "clearsky", "cloud_factor": self.cloudFactor},
                }
                self.pv_adapter = load_fmi_adapter(adapter_config)

    def _apply_reconfiguration(self, cfg, changed: set[str]) -> None:
        super()._apply_reconfiguration(cfg, changed - {'cloud_factor'})
        if 'cloud_factor' in changed:
            self.cloudFactor = float(cfg.cloud_factor)

    def schedule_power(self, market):
        """Schedule power for active time intervals using pvlib physics or cosine model."""
        if self.pv_adapter is not None:
            from tent.flexibility import Interval

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
        if _log.isEnabledFor(logging.DEBUG):  # the report spans every market's intervals; build it only when shown
            _log.debug(f'Scheduled_powers for market: {market.name} --- '
                       f'{ {name: p for name, p in self.scheduled_powers.log_report.items() if name.startswith(market.name)} }')

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
