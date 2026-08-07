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
from dateutil import parser
from typing import Union

from tent.information_service_model import InformationServiceModel
from tent.enumerations import MeasurementType

_log = logging.getLogger(__name__)


class TemperatureForecastModel(InformationServiceModel):
    """
    Predict hourly temperature (F)
    Use CSV as we don't have internet access for now. Thus keep the csv file as small as possible
    This can be changed to read from real-time data source such as WU
    """

    def __init__(self,
                 location: dict = None,
                 max_gap_time: Union[timedelta, float, int] = timedelta(hours=4),
                 temperature_point_name: str = '',
                 tmy_fallback_path: str = '',
                 weather_file_path: str = None,
                 *args, **kwargs):
        super(TemperatureForecastModel, self).__init__(*args, **kwargs)
        self.informationType = MeasurementType.Temperature
        # Preserve None (don't cast None -> 'None') so we can test whether a file path was provided.
        self.weather_file_path = str(weather_file_path) if weather_file_path is not None else None
        self.weather_data = []
        self.last_modified = None

        if self.weather_file_path is not None:
            # Only initialize weather data if the file actually exists. Protect against
            # invalid or placeholder paths (e.g., the string 'None').
            if os.path.exists(self.weather_file_path):
                self.init_weather_data()
            else:
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
        if self.weather_file_path is not None:
            self.get_forecast_file(market)
        else:
            self.get_forecast_weather_service(market)

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
                        rec['Timestamp'] = parser.parse(rec['Timestamp']).replace(minute=0, second=0, microsecond=0)
                        rec['Value'] = float(rec['Value'])
            except Exception:
                self.weather_data = []
                _log.debug("WEATHER - problem parsing weather file!")

    def map_forecast_to_interval(self, mkt, weather_data):
        items = []
        predicted_values = []
        for ti in mkt.time_intervals:
            # Find item which has the same timestamp as ti.timeStamp
            previous_measurement = items
            items = [x[1] for x in weather_data if x[0] == ti.start_time.replace(minute=0)]
            # Create interval value and add it to predicted values
            if items:
                predicted_values.append((ti, items[0]))
            elif previous_measurement:
                predicted_values.append((ti, previous_measurement[0]))
                items = previous_measurement
            else:
                _log.debug("Cannot assign WEATHER information for interval: {}".format(ti))
        if len(mkt.time_intervals) == len(predicted_values):
            for val in predicted_values:
                self.predicted_values.update(*val)
            return True
        else:
            _log.debug("WEATHER data problem when assigning forecast {}".format(len(predicted_values)))
            return False

    def query_weather_data(self):
        """
        Implement query of remote weather data here.
        """
        return []

    def get_forecast_weather_service(self, mkt):
        """
        Gets weather data from a weather service using query_weather_data (which needs to be implemented in a subclass).
        :param mkt:
        :return:
        """
        weather_data = self.query_weather_data()
        if not self.map_forecast_to_interval(mkt, weather_data):
            if self.predicted_values:
                gap_time = mkt.time_intervals.first.start_time - self.predicted_values.first.interval.start_time
                if gap_time > self.max_gap_time:
                    if self.weather_data:
                        _log.debug("WEATHER using fallback1 tmy data for forecast!")
                        self.get_forecast_file(mkt)
                else:
                    # TODO: This seems to be intended to shift values forward by one interval.
                    #  Why do this at all? Is this really the best way to handle missing data?
                    for time_interval in mkt.time_intervals[1:]:
                        self.predicted_values.update(time_interval,
                                                     self.predicted_values.get_prior_by_interval(time_interval))
            else:
                if self.weather_data:
                    _log.debug("WEATHER using fallback2 tmy data for forecast!")
                    self.get_forecast_file(mkt)

    def get_forecast_file(self, mkt):
        self.init_weather_data()
        # Copy weather data to predictedValues
        for ti in mkt.time_intervals:
            # Find item which has the same timestamp as ti.timeStamp
            start_time = ti.start_time.replace(minute=0)
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
                    {"name": "max_gap_time", "type": "DateTimeRangeType"},
                    {"name": "temperature_point_name", "type": "String", "type_args": [255]},
                    {"name": "weather_file_path", "type": "String", "type_args": [255]}
                ]
            }
        ])
        return definitions
