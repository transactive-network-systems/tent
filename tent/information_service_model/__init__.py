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

from tent.base import Component
from tent.config import ConfigModel, NamedConfig, Seconds, Setting, enum_by_name
from tent.containers import Measurement
from tent.enumerations import MeasurementType, MeasurementUnit


class InformationServiceModel(Component):
    """InformationServiceModel
        Manages an InformationService and predicts or interpolates the information it provides."""

    class Config(NamedConfig):
        information_type: enum_by_name(MeasurementType) = Setting(
            'Unknown', description='Kind of information the service provides (consumers select a service by type).')
        information_units: enum_by_name(MeasurementUnit) = Setting('Unknown', description='Units of the values.')
        update_period: Seconds = Setting(3600, description='[s] period over which the information is reported.')
        update_interval: Seconds = Setting(3600, description='[s] interval between updates of the information.')

    def __init__(self, transactive_node, config: ConfigModel | None = None, **kwargs):
        cfg = self._configure(config, kwargs)
        super(InformationServiceModel, self).__init__(cfg.description, cfg.name, transactive_node, config=cfg)
        self.information_type: MeasurementType = cfg.information_type
        self.information_units: MeasurementUnit = cfg.information_units
        self.update_interval: timedelta = cfg.update_interval
        self.update_period: timedelta = cfg.update_period

        # These are dynamic properties that are assigned in code and should not be manually configured:
        self.last_modified: datetime | None = None
        self.predicted_values: Measurement = Measurement(self, self.information_type, self.information_units, archive_now=False)

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
                    {"name": "update_period", "type": "Interval"},
                    {"name": "update_interval", "type": "Interval"}
                ]
            },
            {
                "name": "entity_information_service",
                "columns": [
                    {"name": "entity_id", "foreign_key": "entity.id", "kwargs": {"primary_key": True}},
                    {"name": "information_service_id", "foreign_key": "information_service.id",
                     "kwargs": {"primary_key": True}}
                ]
            }
        ])
        return definitions

