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
from copy import deepcopy
from datetime import datetime
from weakref import WeakKeyDictionary

from tent import Archivable, Component
from tent.containers.time_interval import TimeInterval
from tent.enumerations import MeasurementType, MeasurementUnit
from tent.utils.timer import Timer


# TODO: Is this useful elsewhere, now that it is not used here?
# class DefaultWeakKeyDictionary(defaultdict):
#     def __init__(self, default_factory):
#         super(DefaultWeakKeyDictionary, self).__init__(default_factory)
#         self.data = WeakKeyDictionary()


class MeasurementValue(Archivable):
    def __init__(self, measurement, interval, value):
        super(MeasurementValue, self).__init__()
        self.interval: TimeInterval = interval  # TODO: Should this be a weak reference?
        self.measurement: Measurement = measurement
        self.value: float | bool = value  # TODO: Should there be separate Value classes for different types?
        self.archive()

    def update(self, value: float):
        self.value = value
        self.archive()

    def __add__(self, other):
        return self.value + other.value

    def __sub__(self, other):
        return self.value - other.value

    def __mul__(self, other):
        return self.value * other.value

    def __floordiv__(self, other):
        return self.value // other.value

    def __truediv__(self, other):
        return self.value / other.value

    def __mod__(self, other):
        return self.value % other.value

    def __eq__(self, other):
        return self.value == other.value

    def __ne__(self, other):
        return self.value != other.value

    def __lt__(self, other):
        return self.value < other.value

    def __gt__(self, other):
        return self.value > other.value

    def __le__(self, other):
        return self.value <= other.value

    def __ge__(self, other):
        return self.value >= other.value


    @property
    def data_manager(self):
        return self.measurement.source.data_manager

    @property
    def table_name(self):
        return 'measurement_value'

    def get_record(self) -> dict:
        record = super(MeasurementValue, self).get_record()
        record.update({
            "measurement_id": self.measurement.id,
            "interval_id": self.interval.id,
            "record_time": Timer.now(),
            "value": self.value
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(MeasurementValue, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "measurement_value",
                "columns": [
                    {"name": "id", "type": "Uuid", "identity": True, "kwargs": {"primary_key": True}},
                    {"name": "measurement_id", "foreign_key": "measurement.id", "kwargs": {"primary_key": True}},
                    {"name": "interval_id", "foreign_key": "time_interval.id", "kwargs": {"primary_key": True}},
                    {"name": "record_time", "type": "DateTime", "kwargs": {"primary_key": True}},
                    {"name": "value", "type": "Float"}
                ]
            }
        ])
        return definitions


class Measurement(Archivable):
    """
    A Measurement instance is used to keep track of a value (measurement, quality, etc.).
    """
    def __init__(self,
                 source: Component,
                 measurement_type: MeasurementType = MeasurementType.Unknown,
                 measurement_unit: MeasurementUnit = MeasurementUnit.Unknown,
                 ):
        super(Measurement, self).__init__()
        # TODO: There should probably be something to distinguish between multiple measurements with same units and
        #  type from same source. How often is this an issue?
        self.source: Component = source
        self.type: MeasurementType = measurement_type
        self.unit: MeasurementUnit = measurement_unit

        # TODO: Tests are needed to be sure that garbage collection works to remove these.
        #  It may be necessary to manually delete the intervals from the market before the market is deleted
        #  or to make the market a weak reference in the TimeInterval.
        self._data: WeakKeyDictionary[TimeInterval, MeasurementValue] = WeakKeyDictionary()

        self.archive()

    @property
    def all(self):
        return sorted(self._data.items(), key=lambda x: x[0].start_time)

    @property
    def log_report(self):
        return {k.name: v.value for k, v in self.all}

    @property
    def first(self):
        return self._data[min(self._data.keys(), key=lambda x: x.start_time)]

    @property
    def last(self):
        return self._data[max(self._data.keys(), key=lambda x: x.start_time)]

    @property
    def total(self, market=None):
        return sum(v.value for k, v in self._data.items() if k.market == market) if market else sum(self._data)

    def copy(self):
        return deepcopy(self)

    def get_by_interval(self, time_interval: TimeInterval) -> MeasurementValue | None:
        return self._data.get(time_interval)

    def get_prior_by_interval(self, time_interval: TimeInterval) -> MeasurementValue | None:
        prior_start_time = time_interval.start_time - time_interval.duration
        return self.get_by_start_time(prior_start_time)

    def get_by_start_time(self, start_time: datetime) -> MeasurementValue | None:
        return next(filter(lambda ti: ti.start_time == start_time, self._data.keys()), None)

    def update(self, time_interval: TimeInterval, value: any):
        if time_interval in self._data:
            self._data[time_interval].update(value)
        else:
            self._data[time_interval] = MeasurementValue(self, time_interval, value)

    def __bool__(self):
        return bool(self._data)

    def __len__(self):
        return len(self._data)

    @property
    def data_manager(self):
        return self.source.data_manager

    @property
    def table_name(self) -> str:
        return 'measurement'

    def get_record(self) -> dict:
        record = super(Measurement, self).get_record()
        record.update({
            "source_id": self.source.id,
            "measurement_type": self.type,
            "measurement_unit": self.unit
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(Measurement, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "measurement",
                "columns": [
                    {"name": "id", "type": "Uuid", "identity": True, "kwargs": {"primary_key": True}},
                    {"name": "source_id", "foreign_key": "component.id"},
                    {"name": "measurement_type", "enumeration": "MeasurementType"},
                    {"name": "measurement_unit", "enumeration": "MeasurementUnit"},
                ]
            }
        ])
        return definitions
