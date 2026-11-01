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


import os
import csv
import logging

from datetime import timedelta
from typing import Union
from zoneinfo import ZoneInfo

from tent.information_service_model import InformationServiceModel
from tent.enumerations import MeasurementType, MeasurementUnit
from tent.utils.helpers import parse_datetime

_log = logging.getLogger(__name__)


class TemperatureForecastModel(InformationServiceModel):
    """
    Hourly temperature forecast (degF) read from a CSV file (columns Timestamp, Value), the data source used in
    simulations. The constructor also accepts the keys a service-backed subclass needs (location, max_gap_time,
    temperature_point_name, tmy_fallback_path) so that such a subclass can fall back to the file when its service fails.
    """

    def __init__(self,
                 location: dict = None,
                 max_gap_time: Union[timedelta, float, int] = timedelta(hours=4),
                 temperature_point_name: str = '',
                 tmy_fallback_path: str = '',
                 weather_file_path: str = None,
                 *args, **kwargs):
        # This service is a temperature forecast whatever the configuration says: consumers select it by type.
        kwargs['information_type'] = MeasurementType.Temperature
        kwargs['information_units'] = MeasurementUnit.degF
        super(TemperatureForecastModel, self).__init__(*args, **kwargs)
        # Preserve None (don't cast None -> 'None') so we can test whether a file path was provided.
        self.weather_file_path = str(weather_file_path) if weather_file_path is not None else None
        self.weather_data = []
        self.last_modified = None

        if self.weather_file_path is not None:
            # Only initialize weather data if the file actually exists. Protect against
            # invalid or placeholder paths (e.g., the string 'None').
            #_log.debug(f'@@@@@@@@@ TEMPERATURE_FORECAST_MODEL --- USING PATH: {self.weather_file_path}')
            if os.path.exists(self.weather_file_path):
                #_log.debug('@@@@@@@@ TEMPERATURE_FORECAST_MODEL --- THE PATH EXISTS, TOO!')
                self.init_weather_data()
            else:
                #_log.debug('@@@@@@@@@@ TEMPERATURE_FORECAST_MODEL --- THE PATH DOES NOT EXIST.')
                # If the provided path doesn't exist, treat as if no weather file was provided.
                self.weather_file_path = None
            self.location = None
            self.max_gap_time = None
            self.oat_point_name = None
        else:
            self.location = [dict(location)] if location else []
            self.max_gap_time = max_gap_time if isinstance(max_gap_time, timedelta) else \
                timedelta(seconds=float(max_gap_time))
            self.oat_point_name = str(temperature_point_name) if temperature_point_name else "OutdoorAirTemperature"
            if tmy_fallback_path:
                self.weather_file_path = str(tmy_fallback_path)
                # Only init if fallback exists
                if os.path.exists(self.weather_file_path):
                    self.init_weather_data()
            # If no valid weather file was found, ensure attribute is None
            if not self.weather_file_path:
                self.weather_file_path = None

    def update_information(self, market):
        """
        Fill predicted_values for the market's intervals from the weather file. This class is the file-backed forecast;
        a service-backed forecast (such as the TransactiveNodeAgent's weather.gov model) subclasses it, reusing the
        constructor, the file loader and get_forecast_file as its fallback, and overrides this method.
        """
        if self.weather_file_path is None:
            raise RuntimeError(f'{self.name}: no weather file is configured (weather_file_path or tmy_fallback_path) and '
                               f'this class has no other source of forecasts.')
        self.get_forecast_file(market)

    def init_weather_data(self):
        """
        To init or re-init weather data from file.
        :return:
        """
        # Get latest modified time
        cur_modified = os.path.getmtime(self.weather_file_path)

        if self.last_modified is None or cur_modified != self.last_modified:
            self.last_modified = cur_modified

            # Clear weather_data for re-init
            self.weather_data = []
            try:
                with open(self.weather_file_path) as f:
                    reader = csv.DictReader(f)
                    self.weather_data = [r for r in reader]
                    for rec in self.weather_data:
                        rec['Timestamp'] = parse_datetime(rec['Timestamp']).replace(minute=0, second=0, microsecond=0)
                        if rec['Timestamp'].tzinfo is None:
                            rec['Timestamp'] = rec['Timestamp'].replace(tzinfo=ZoneInfo(self.tn.tz))
                        rec['Value'] = float(rec['Value'])
            except Exception:
                self.weather_data = []
                _log.warning("WEATHER - problem parsing weather file!")

    def get_forecast_file(self, mkt):
        #_log.debug(f'IN GET_FORCAST_FILE FOR MARKET: {mkt.name}.')
        self.init_weather_data()
        # Copy weather data to predictedValues
        for ti in mkt.time_intervals:
            # Find item which has the same timestamp as ti.timeStamp
            start_time = ti.start_time.replace(minute=0)
            #_log.debug(f'LOOKING FOR TIMESTAMP ({type(start_time)}): {start_time}')
            #_log.debug(f'THE FIRST POSSIBILITY ({type(self.weather_data[0]["Timestamp"])}) IS: {self.weather_data[0]["Timestamp"]}')
            items = [x for x in self.weather_data if x['Timestamp'] == start_time]
            if len(items) == 0:
                trial_deltas = [-1, 1, -2, 2, -24, 24]
                for delta in trial_deltas:
                    items = [x for x in self.weather_data if x['Timestamp'] == (start_time - timedelta(hours=delta))]
                    if len(items) > 0:
                        break

                # None exist, raise exception
                if len(items) == 0:
                    raise Exception('No weather data for time: {}'.format(ti.start_time.isoformat()))

            # Add temperature to predicted values
            self.predicted_values.update(ti, items[0]['Value'])

    @property
    def table_name(self):
        return 'temperature_forecast_model'

    def get_record(self) -> dict:
        record = super(TemperatureForecastModel, self).get_record()
        record.update({
            'location': self.location,
            'max_gap_time': self.max_gap_time,
            'temperature_point_name': self.oat_point_name,
            'weather_file_path': self.weather_file_path
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(TemperatureForecastModel, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "temperature_forecast_model",
                "inherits": "information_service",
                "columns": [
                    {"name": "information_service_id", "foreign_key": "information_service.id",
                     "kwargs": {"primary_key": True}},
                    #{"name": "local_tz", "type": "TimezoneType", "type_args": ['zoneinfo']},  # type": "String", "type_args": [255]},
                    {"name": "location", "type": "JSON"},
                    {"name": "max_gap_time", "type": "Interval"},
                    {"name": "temperature_point_name", "type": "String", "type_args": [255]},
                    {"name": "weather_file_path", "type": "String", "type_args": [255]}
                ]
            }
        ])
        return definitions
