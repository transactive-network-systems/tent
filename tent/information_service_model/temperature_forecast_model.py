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
from pathlib import Path
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

    A configured file (weather_file_path, or tmy_fallback_path for a service-backed subclass) must exist and parse when
    the model is configured; a path that does not resolve, a header without Timestamp and Value, or an unparsable row is
    a configuration error. Relative paths are resolved against the process's working directory and archived resolved.
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
        # Service settings, kept whether or not a file is configured (a service-backed subclass reads them).
        self.location: list[dict] = [dict(location)] if location else []
        self.max_gap_time: timedelta = (max_gap_time if isinstance(max_gap_time, timedelta)
                                        else timedelta(seconds=float(max_gap_time)))
        self.oat_point_name: str = str(temperature_point_name) if temperature_point_name else 'OutdoorAirTemperature'
        self.weather_data: list[dict] = []
        self.last_modified = None
        # The forecast file: weather_file_path when given, otherwise the fallback file of a service-backed subclass.
        configured = weather_file_path if weather_file_path else tmy_fallback_path
        self.weather_file_path: str | None = self._resolve_weather_file(configured) if configured else None
        if self.weather_file_path is not None:
            self.init_weather_data()

    def _resolve_weather_file(self, configured) -> str:
        path = Path(str(configured)).expanduser()
        resolved = path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f'{self.name}: weather file {str(configured)!r} (resolved to {resolved}) does not '
                                    f'exist. Give the path of a CSV with columns Timestamp and Value.')
        return str(resolved)

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
        """Load (or reload, when the file has changed) the hourly records: Timestamp (any format parse_datetime
        accepts; a naive time is in the node's zone) and Value (degF). Records are keyed to the hour."""
        cur_modified = os.path.getmtime(self.weather_file_path)
        if self.last_modified is not None and cur_modified == self.last_modified:
            return
        records = []
        with open(self.weather_file_path, newline='') as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            missing = [column for column in ('Timestamp', 'Value') if column not in header]
            if missing:
                raise ValueError(f'{self.name}: weather file {self.weather_file_path} has columns {header}; '
                                 f'it needs {missing} as well.')
            for line, row in enumerate(reader, start=2):
                try:
                    timestamp = parse_datetime(row['Timestamp']).replace(minute=0, second=0, microsecond=0)
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=ZoneInfo(self.tn.tz))
                    records.append({'Timestamp': timestamp, 'Value': float(row['Value'])})
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(f'{self.name}: weather file {self.weather_file_path} line {line} cannot be read: '
                                     f'{error}') from error
        self.weather_data = records
        self.last_modified = cur_modified

    def get_forecast_file(self, mkt):
        """Predicted values for the market's intervals from the file, by the hour of each interval's start. A missing hour
        is filled from the nearest hour present among the adjacent ones and the same hour on the adjacent days; when none
        exists the interval cannot be forecast and a LookupError names it."""
        self.init_weather_data()
        by_hour = {record['Timestamp']: record['Value'] for record in self.weather_data}
        for ti in mkt.time_intervals:
            start_time = ti.start_time.replace(minute=0, second=0, microsecond=0)
            value = by_hour.get(start_time)
            if value is None:
                for delta in (1, -1, 2, -2, 24, -24):  # the search order the file-backed forecast has always used
                    value = by_hour.get(start_time + timedelta(hours=delta))
                    if value is not None:
                        _log.debug(f'{self.name}: no weather record at {start_time.isoformat()}; using the record '
                                   f'{delta:+d} h away for interval {ti.name}.')
                        break
            if value is None:
                raise LookupError(f'{self.name}: no weather data for interval {ti.name} (hour {start_time.isoformat()}) '
                                  f'in {self.weather_file_path}.')
            self.predicted_values.update(ti, value)

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
                    {"name": "weather_file_path", "type": "Text"}
                ]
            }
        ])
        return definitions
