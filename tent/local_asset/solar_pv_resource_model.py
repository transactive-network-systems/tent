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

from tent.local_asset import LocalAsset
from tent.utils.log import setup_logging

setup_logging()
_log = logging.getLogger(__name__)


class SolarPvResource(LocalAsset):
    # SolarPvResourceModel Subclass for renewable solar PV generation The Solar PV resource is treated here as a
    # must-take resource. This is unlike dispatchable resources in this regard. Production may be predicted. The main
    # features of this model are (1) the introduction of property cloudFactor, an IntervalValue, that allows us to
    # reduce the expected solar generation according to cloud cover, and (2) method solar_generation() that creates the
    # envelope, best-case, power production for the resource as a function of time-of-day.

    def __init__(self,
                 cloud_factor=1.0,
                 *args, **kwargs):
        super(SolarPvResource, self).__init__(*args, **kwargs)
        self.cloudFactor = float(cloud_factor)

    def schedule_power(self, market):
        # Estimate stochastic generation from a solar PV array as a function of time-of-day and a cloud-cover factor.
        # INPUTS:
        # obj - SolarPvResourceModel class object
        # tod - time of day
        # OUTPUTS:
        # power - calculated maximum power production at this time of day
        # LOCAL:
        # h - hour (presumes 24-hour clock, local time)
        # *************************************************************************

        # Index through the active time intervals ti
        for time_interval in market.time_intervals:

            # Production will be estimated from the time-of-day at the center of the time interval.
            time_of_day: datetime = time_interval.start_time + time_interval.duration / 2
            hour_of_day: float = time_of_day.hour + time_of_day.minute / 60  # Time_of_day stated as fractional hours

            # Estimate solar generation as a sinusoidal function of daylight hours.
            if hour_of_day < 5.5 or hour_of_day > 17.5:
                power = 0.0  # The time is outside the time of solar production. Set power to zero. [avg.kW]
            else:
                # A sinusoidal function is used to forecast solar generation during the normally sunny part of a day.
                clear_sky_power = 0.5 * (1 + math.cos((hour_of_day - 12) * 2.0 * math.pi / 12))
                power = self.maximum_power * clear_sky_power * self.cloudFactor  # [avg.kW]

            # Create or update scheduled power for the indexed time interval to calculated power.
            self.scheduled_powers.update(time_interval, power)

            # Assign engagement schedule in the indexed time interval NOTE: The assignment of engagement schedule, if
            # used, will often be assigned during the scheduling of power, not separately as demonstrated here.
            # Check whether an engagement schedule exists in the indexed time interval

            # Set asset as committed or engaged for this interval.
            self.engagement_schedule.update(time_interval, True)

        self.schedule_calculated = True

        _log.debug(f"Scheduled_powers for market: {market.name} --- {self.scheduled_powers.log_report}")

    @property
    def table_name(self):
        return 'solar_pv_resource'

    def get_record(self) -> dict:
        record = super(SolarPvResource, self).get_record()
        record.update({
            'asset_id': self.id,
            'cloud_factor': self.cloudFactor
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(SolarPvResource, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "solar_pv_resource",
                "inherits": "local_asset",
                "columns": [
                    {"name": "asset_id", "foreign_key": "local_asset.id", "kwargs": {"primary_key": True}},
                    {"name": "cloud_factor", "type": "Float"}
                ]
            }
        ])
        return definitions


if __name__ == '__main__':
    spvm = SolarPvResource()
