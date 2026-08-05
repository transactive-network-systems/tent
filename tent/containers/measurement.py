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
from typing import Any
from weakref import proxy, WeakKeyDictionary

from tent.base import Archivable, Component
from tent.containers.time_interval import TimeInterval
from tent.enumerations import MeasurementType, MeasurementUnit
from tent.utils.helpers import safe_weak_proxy_callback
from tent.utils.timer import Timer

# TODO: Consider renaming Measurement to DataCollection, MeasurementValue to Measurement.
class MeasurementValue(Archivable):
    def __init__(self, measurement, interval, value, archive: bool = True):
        super(MeasurementValue, self).__init__()
        self.interval: TimeInterval = proxy(interval, safe_weak_proxy_callback(self, 'interval'))
        self.measurement: Measurement = proxy(measurement, safe_weak_proxy_callback(self, 'measurement'))
        self.value: float | bool = value  # TODO: Should there be separate Value classes for different types?
        if archive:
            self.archive()

    def update(self, value: float | bool, archive=True):
        self.value = value
        if archive:
            self.archive()

    def _get_numeric_value(self, other):
        """Extract numeric value from MeasurementValue or plain numeric type."""
        return other.value if hasattr(other, 'value') else other

    def __add__(self, other):
        return self.value + self._get_numeric_value(other)

    def __sub__(self, other):
        return self.value - self._get_numeric_value(other)

    def __mul__(self, other):
        return self.value * self._get_numeric_value(other)

    # Reverse operators needed for when MeasurementValue is on right side of operation
    # e.g., float * MeasurementValue in: marginal_price * scheduled_power * duration
    def __rmul__(self, other):
        return self._get_numeric_value(other) * self.value

    def __radd__(self, other):
        return self._get_numeric_value(other) + self.value

    def __rsub__(self, other):
        return self._get_numeric_value(other) - self.value

    def __floordiv__(self, other):
        return self.value // self._get_numeric_value(other)

    def __truediv__(self, other):
        return self.value / self._get_numeric_value(other)

    def __mod__(self, other):
        return self.value % self._get_numeric_value(other)

    def __eq__(self, other):
        return self.value == self._get_numeric_value(other)

    def __ne__(self, other):
        return self.value != self._get_numeric_value(other)

    def __lt__(self, other):
        return self.value < self._get_numeric_value(other)

    def __gt__(self, other):
        return self.value > self._get_numeric_value(other)

    def __le__(self, other):
        return self.value <= self._get_numeric_value(other)

    def __ge__(self, other):
        return self.value >= self._get_numeric_value(other)


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
    DATA_TYPE: type = MeasurementValue
    def __init__(self,
                 source: Component,
                 measurement_type: MeasurementType = MeasurementType.Unknown,
                 measurement_unit: MeasurementUnit = MeasurementUnit.Unknown,
                 ):
        super(Measurement, self).__init__()
        # TODO: There should probably be something (a name?) to distinguish between multiple measurements with same
        #  units and type from same source. How often is this an issue?
        self.source: Component = proxy(source, safe_weak_proxy_callback(self, 'source'))
        self.type: MeasurementType = measurement_type
        self.unit: MeasurementUnit = measurement_unit

        # TODO: Tests are needed to be sure that garbage collection works to remove these.
        self._data: WeakKeyDictionary[TimeInterval, Measurement.DATA_TYPE] = WeakKeyDictionary()

        self.archive()

    @property
    def all(self):
        return sorted(self._data.items(), key=lambda x: x[0].start_time)

    @property
    def first(self):
        return self._data[min(self._data.keys(), key=lambda x: x.start_time)]

    @property
    def last(self):
        return self._data[max(self._data.keys(), key=lambda x: x.start_time)]

    @property
    def log_report(self):
        return {ti.name: mv.value for ti, mv in self.all}

    @property
    def start_times(self):
        return (ti.start_time for ti, _ in self.all)

    @property
    def values(self):
        return (mv.value for _, mv in self.all)

    def copy(self):
        return deepcopy(self)

    def get_by_interval(self, time_interval: TimeInterval) -> DATA_TYPE | None:
        return self._data.get(time_interval)

    def get_prior_by_interval(self, time_interval: TimeInterval) -> DATA_TYPE | None:
        prior_start_time = time_interval.start_time - time_interval.duration
        return self.get_by_start_time(prior_start_time)

    def get_by_start_time(self, start_time: datetime) -> DATA_TYPE | None:
        return next((mv for ti, mv in self._data.items() if ti.start_time == start_time), None)

    def get_interval_by_start_time(self, start_time: datetime) -> TimeInterval | None:
        """Return the TimeInterval key beginning at start_time, if any."""
        return next((ti for ti in self._data.keys() if ti.start_time == start_time), None)

    def total(self, market=None):
        return sum(mv.value for ti, mv in self._data.items() if ti.market == market) if market else sum(self.values)

    def update(self, time_interval: TimeInterval, value: Any, archive: bool = True, replace: bool = False):
        # TODO: Is replace used and/or necessary?
        # TODO: create batch_update method which uses the same record time for all values to archive all values. 
        if time_interval in self._data and not replace:
            # Receiving datatype, here, must handle all its own transformations from allowed input types.
            self._data[time_interval].update(value, archive)
        else:
            self._data[time_interval] = self.DATA_TYPE(self, time_interval, value, archive)

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
