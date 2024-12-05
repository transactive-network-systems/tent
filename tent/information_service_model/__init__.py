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
import abc

from datetime import datetime, timedelta
from typing import Union

from tent import Component
from tent.containers import Measurement
from tent.enumerations import MeasurementType, MeasurementUnit
from tent.utils.timer import Timer


class InformationServiceModel(Component):
    """InformationServiceModel
        Manages an InformationService and predicts or interpolates the information it provides."""

    def __init__(self,
                 description='',
                 information_type: Union[int, str, MeasurementType] = MeasurementType.Unknown,
                 information_units: Union[int, str, MeasurementUnit] = MeasurementUnit.Unknown,
                 update_period: Union[float, timedelta] = timedelta(hours=1),
                 name='',
                 update_interval: Union[float, timedelta] = timedelta(hours=1),
                 transactive_node=None
                 ):
        super(InformationServiceModel, self).__init__(description, name, transactive_node)
        self.information_type: MeasurementType = information_type if isinstance(information_type, MeasurementType)\
            else MeasurementType[information_type]
        self.information_units: MeasurementUnit = information_units if isinstance(information_units, MeasurementUnit)\
            else MeasurementUnit[information_units]
        self.update_interval: timedelta = update_interval if isinstance(update_interval, timedelta) \
            else timedelta(seconds=update_interval)  # [h]
        self.update_period: timedelta = update_period if isinstance(update_period, timedelta)\
            else timedelta(seconds=update_period) # [h]

        # These are dynamic properties that are assigned in code and should not be manually configured:
        self.last_modified: datetime | None = None
        self.predicted_values: Measurement = Measurement(self, self.information_type, self.information_units)

    @abc.abstractmethod
    def update_information(self, market):
        """Assign values to predicted_values for all intervals.

        # An implementation update predicted_values for all intervals (as possible) and update the last_modified:
        for time_interval in market.timeIntervals:
            # Update will create or update the predicted value for this time interval.
            self.predicted_values.update(time_interval, func_that_give_a_value())
            self.last_modified = Timer.now()
        """

    @property
    def table_name(self) -> str:
        return 'information_service'

    def get_record(self) -> dict:
        record = super(InformationServiceModel, self).get_record()
        record.update({
            'information_type': self.information_type,
            'information_units': self.information_units,
            'update_period': self.update_period,
            'update_interval': self.update_interval
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(InformationServiceModel, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "information_service",
                "inherits": "component",
                "columns": [
                    {"name": "id", "foreign_key": "component.id", "kwargs": {"primary_key": True}},
                    {"name": "information_type", "enumeration": "MeasurementType"},
                    {"name": "information_units", "enumeration": "MeasurementUnit"},
                    {"name": "update_period", "type": "DateTimeRangeType"},
                    {"name": "update_interval", "type": "DateTimeRangeType"}
                ]
            },
            {
                "name": "entity_information_service",
                "columns": [
                    {"name": "component", "foreign_key": "component.id", "kwargs": {"primary_key": True}},
                    {"name": "information_service_id", "foreign_key": "information_service.id"}
                ]
            }
        ])
        return definitions

