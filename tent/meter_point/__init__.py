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

# A MeterPoint may correlate directly with a meter. It necessarily
# corresponds to one measurement type (see MeasurementType enumeration) and
# measurement location within the circuit. Therefore, a single physical
# meter might be the source of more than one MeterPoint.

from datetime import timedelta
from math import ceil
from statistics import mean
from typing import Union

from tent.base import Component
from tent.enumerations import MeasurementType, MeasurementUnit
from tent.utils.helpers import require_aware
from tent.utils.time_series_buffer import TimeSeriesBuffer
from tent.utils.timer import Timer


class MeterPoint(Component):
    def __init__(self,
                 transactive_node,
                 data_period_seconds: float = 10,
                 description: str = '',
                 max_buffer_seconds: float = 3600,
                 # TODO: Is measurement_interval intended as the market interval or the polling period?
                 #  2022-03-28: Assuming that this is set to the interval to be considered by the
                 #  mean_measurements function (i.e used for DC interval in neighbor_model). Is this correct?
                 measurement_interval: Union[timedelta, int] = timedelta(hours=1),
                 measurement_type: Union[MeasurementType, str, int] = MeasurementType.Unknown,
                 measurement_unit: Union[MeasurementUnit, str, int] = MeasurementUnit.Unknown,
                 name: str = '',
                 scale_factor: float = 1.0
                 ):
        super(MeterPoint, self).__init__(description, name, transactive_node)

        max_len = ceil(max_buffer_seconds / data_period_seconds) + 1
        self.buffer = TimeSeriesBuffer(maxlen=max_len, dt_class=Timer)

        # These are static properties that may be passed as parameters:
        self.data_period_seconds = float(data_period_seconds)
        self.max_buffer_seconds = float(max_buffer_seconds)
        self.measurement_interval = measurement_interval if isinstance(measurement_interval, timedelta) \
            else timedelta(seconds=measurement_interval)

        if isinstance(measurement_type, MeasurementType):
            self.measurement_type = measurement_type
        elif isinstance(measurement_type, int):
            self.measurement_type = MeasurementType(measurement_type)
        else:
            self.measurement_type = MeasurementType[measurement_type]

        if isinstance(measurement_unit, MeasurementUnit):
            self.measurement_unit = measurement_unit
        elif isinstance(measurement_unit, int):
            self.measurement_unit = MeasurementUnit(measurement_unit)
        else:
            self.measurement_unit = MeasurementUnit[measurement_unit]
        self.scale_factor = float(scale_factor)  # Use -1 to reverse the sign of the meter.

    @property
    def current_interval_measurements(self) -> list:
        """Last hour of measurements."""
        return self.buffer.get(since=(self.buffer.dt_class.now() - self.measurement_interval))

    @property
    def mean_measurement(self):
        """Mean of the readings in the current measurement interval; None when there are none."""
        values = self.buffer.get_values(since=(self.buffer.dt_class.now() - self.measurement_interval))
        return mean(values) if values else None

    @property
    def last_updated(self):
        """Most recent measurement."""
        return self.buffer.last.d_time

    @property
    def current_measurement(self):
        """Most recent measurement."""
        return self.buffer.last.value

    def set_meter_value(self, value, last_update=None):
        last_update = require_aware(last_update, 'last_update') if last_update else Timer.now()
        new_measurement = self.scale_factor * value
        self.buffer.append(new_measurement, last_update)
        self.archive_reading(new_measurement, last_update)

    @property
    def table_name(self):
        return 'meter_point'

    def get_record(self) -> dict:
        record = super(MeterPoint, self).get_record()
        record.update({
            'class': self.__class__.__name__,
            'measurement_type': self.measurement_type,
            'measurement_unit': self.measurement_unit,
            'measurement_interval': self.measurement_interval,
            'data_period_seconds': self.data_period_seconds,
            'max_buffer_seconds': self.max_buffer_seconds,
            'scale_factor': self.scale_factor
        })
        return record

    def archive_reading(self, reading_value, reading_time):
        """One row per reading. The key includes the time the row was recorded, so two readings that carry the same
        timestamp (a repeated publication, a device with coarse timestamps) are both kept rather than colliding."""
        self.data_manager.archive_data({
            "table_name": "meter_reading",
            "data": {
                "meter_id": self.id,
                "reading_time": reading_time,
                "record_time": Timer.now(),
                "value": reading_value
            }
        })

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(MeterPoint, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "entity_meter_point",
                "columns": [
                    {"name": "entity_id", "foreign_key": "entity.id", "kwargs": {"primary_key": True}},
                    {"name": "meter_point_id", "foreign_key": "meter_point.id", "kwargs": {"primary_key": True}}
                ]
            },
            {
                "name": "meter_point",
                "inherits": "component",
                "columns": [
                    {"name": "id", "type": "Uuid", "foreign_key": "component.id", "kwargs": {"primary_key": True}},
                    {"name": "class", "type": "String", "type_args": [255]},
                    {"name": "measurement_type", "enumeration": "MeasurementType"},
                    {"name": "measurement_unit", "enumeration": "MeasurementUnit"},
                    {"name": "measurement_interval", "type": "Interval"},
                    {"name": "data_period_seconds", "type": "Float"},
                    {"name": "max_buffer_seconds", "type": "Float"},
                    {"name": "scale_factor", "type": "Float"}
                ]
            },
            {
                "name": "meter_reading",
                "columns": [
                    {"name": "meter_id", "foreign_key": "meter_point.id", "kwargs": {"primary_key": True}},
                    {"name": "reading_time", "type": "DateTime", "kwargs": {"primary_key": True}},
                    {"name": "record_time", "type": "DateTime", "kwargs": {"primary_key": True}},
                    {"name": "value", "type": "Float"}
                ]
            }
        ])
        return definitions