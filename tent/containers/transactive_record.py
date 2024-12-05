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

from tent import Archivable
from tent.containers.time_interval import TimeInterval
from tent.utils.timer import Timer


class TransactiveRecord(Archivable):
    def __init__(self,
                 time_interval,
                 neighbor_name='unknown',  # Name of Neighbor object that owns this record
                 direction='unknown',  # Reference to record among {'received', 'prepared', 'sent', 'unknown'}
                 market_name='unknown',  # Reference to market name
                 record=0,
                 marginal_price=0,
                 power=0,
                 power_uncertainty=0.0,
                 cost=0.0,
                 reactive_power=0.0,
                 reactive_power_uncertainty=0.0,
                 voltage=0.0,
                 voltage_uncertainty=0.0):
        super(TransactiveRecord, self).__init__()
        # These are the four normal arguments of the constructor. NOTE: Use the time interval ti text name, not a
        # TimeInterval object itself.
        if isinstance(time_interval, TimeInterval):

            # A TimeInterval object argument must be represented by its text name.
            self.time_interval = time_interval.name

        else:

            # Argument ti is most likely received as a text string name. Further validation might be used to make sure
            # that ti is a valid name of an active time interval.
            self.time_interval = str(time_interval)

        self.record = record                                # a record number (0 refers to the balance point)
        self.marginal_price = marginal_price                 # marginal price [$/kWh]
        self.power = power                                  # power [avg.kW]
        # self.powerUncertainty = power_uncertainty  # relative [dimensionless]
        self.cost = cost                                    # ?
        # self.reactivePower = reactive_power  # [avg.kVAR]
        # self.reactivePowerUncertainty = reactive_power_uncertainty  # relative [dimensionless]
        # self.voltage = voltage  # [p.u.]
        # self.voltageUncertainty = voltage_uncertainty  # relative [dimensionless]

        # Finally, always append the timestamp that captures when the record is created.
        # TODO: Is this consistent with Timer() methods that are used with simulations?
        self.timestamp = Timer.get_cur_time()

        # 210126DJH: Property "neighbor" is introduced to assist with data collection. If the name of the associated
        #            Neighbor object is not captured, a Transactive Record can not be traced to the correct Neighbor.
        self.neighbor_name = neighbor_name

        # 210126DJH: This is a new reference to the source of a record from among ["received", "sent", "prepared"]. It
        #            is important to data collection.
        self.direction = direction
        if self.direction not in ["unknown", "received", "sent", "prepared"]:
            self.direction = "unknown"

        # 210126DJH: This is a new reference to the market in which the transactive record is relevant. A policy has
        #            emerged to name Market objects by prepending the clearing time with the market series name.
        self.market_name = market_name

    def get_dict(self):
        transactive_record_dict = {
            "timeInterval": self.time_interval,
            "marginalPrice": self.marginal_price,
            "power": self.power
        }
        return transactive_record_dict

    @property
    def data_manager(self):
        return self.tn().data_manager
        # TODO: There need to be actual references to the neighbor, market, and tn objects.

    @property
    def table_name(self):
        return 'transactive_record'

    def get_record(self) -> dict:
        # TODO: Need get_record implemented.
        raise NotImplementedError

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(TransactiveRecord, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "transactive_record",
                "columns": [
                    {"name": "time_stamp", "type": "DateTime"},
                    {"name": "id", "type": "Uuid", "identity": True, "kwargs": {"primary_key": True}},
                    {"name": "neighbor_id", "foreign_key": "neighbor.id"},
                    {"name": "node_id", "foreign_key": "transactive_node.id"},
                    {"name": "direction", "enumeration": "Direction"},
                    {"name": "time_interval_id", "foreign_key": "time_interval.id"},
                    {"name": "record_number", "type": "Integer"},
                    {"name": "price", "type": "Float"},
                    {"name": "real_power", "type": "Float"},
                    {"name": "real_power_uncertainty", "type": "Float"},
                    {"name": "cost", "type": "Float"},
                    {"name": "reactive_power", "type": "Float"},
                    {"name": "reactive_power_uncertainty", "type": "Float"},
                    {"name": "voltage", "type": "Float"},
                    {"name": "voltage_uncertainty", "type": "Float"}
                ]
            }
        ])
        return definitions
