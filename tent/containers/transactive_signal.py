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

from datetime import datetime
from math import sqrt
from weakref import WeakKeyDictionary
try:
    # Python 3.11+ provides typing.Self
    from typing import Self, TYPE_CHECKING
except Exception:  # pragma: no cover - fallback for older Python versions
    from typing import TYPE_CHECKING
    from typing_extensions import Self

from tent.containers import SDCurve, SDMeasurement, TimeInterval, Vertex
from tent.utils.timer import Timer

if TYPE_CHECKING:
    from tent.neighbor import Neighbor
    from tent.market import Market

_log = logging.getLogger(__name__)


class TransactiveSignal(SDMeasurement):
    PROTOCOL_VERSION = 1.0

    def __init__(self, market, neighbor, direction=None, signal_time=None, **kwargs):
        self.market: Market = market
        self.neighbor: Neighbor = neighbor
        self.direction = direction
        self.signal_time: datetime | None = signal_time
        self.__balance_vertices: WeakKeyDictionary[TimeInterval, Vertex] = WeakKeyDictionary()
        super(TransactiveSignal, self).__init__(source=neighbor, **kwargs)

    def balance_point(self, time_interval: TimeInterval) -> Vertex | None:
        """Get the balance point vertex for the specified time interval."""
        return self.__balance_vertices.get(time_interval, None)

    def flexibility_vertices(self, time_interval: TimeInterval) -> list[Vertex]:
        """Get the non-balance-point vertices for the specified time interval."""
        return [v for v in self.get_by_interval(time_interval).vertices if v is not self.balance_point(time_interval)]

    def _add_balance_vertices(self, scheduled_powers):
        for ti, sp in scheduled_powers.all:
            sd_curve: SDCurve = self.get_by_interval(ti)
            if not sd_curve.has_vertex_at_quantity(sp.value):
                balance_price = sd_curve.price_from_vertices(sp.value)
                balance_cost = sd_curve.prod_cost_from_vertices(sp.value)
                vertex = Vertex(balance_price, balance_cost, sp)
                sd_curve.update(vertex)
                self.__balance_vertices[ti] = vertex


    def generate_signal(self, scheduled_powers=None):
        if scheduled_powers is not None:
            self._add_balance_vertices(scheduled_powers)
        self.signal_time = Timer.now()
        self.direction = 'sent'
        self.archive()
        intervals = {}
        for ti, sd in self.all:
            # Build flexibility records from the SDCurve's vertices
            flexibility = {}
            # TODO: why have this []? that should never happen in prod. double check that. 
            for i, v in enumerate(getattr(sd, 'vertices', [])):
                flexibility[i] = {
                    'marginal_price': v.price,
                    'quantity': v.quantity,
                    'cost': v.cost,
                    'continuity': v.continuity,
                    'uncertainty': v.uncertainty
                }

            bp = self.balance_point(ti)
            intervals[ti.start_time.isoformat()] = {
                'balance_point': bp.record if bp is not None else None,
                'flexibility': flexibility
            }

        return {
            'market': self.market.name,
            'neighbor': self.neighbor.tn.name,
            'protocol_version': self.PROTOCOL_VERSION,
            'signal_time': self.signal_time.isoformat(),
            'unit': self.unit,
            'intervals': intervals
        }

    @classmethod
    def parse_signal(cls, tn, message: dict) -> Self | None:
        """Parse a transactive signal dictionary and update the SDMeasurement."""
        # TODO: Currently assumes signal is being received from a remote.
        #  If there is another reason to parse a signal, an "on_receipt" flag should be added to signature.
        try:
            # TODO: Should we allow a range of supported protocol_versions?
            if message['protocol_version'] != cls.PROTOCOL_VERSION:
                raise ValueError(f"Unsupported protocol version: {message['protocol_version']}."
                                 f" Expected: {cls.PROTOCOL_VERSION}.")
            if not isinstance(message, dict):
                raise ValueError("Signal must be a dictionary.")
            # TODO: This won't work yet, since the function to get market by name does not currently exist.
            if not (market := tn.get_market_by_name(message['market'])):
                _log.warning(f'Unable to find valid market "{market}" from received transactive signal.')
                return  # TODO: Should we raise on bad market or neighbor and let the caller handle it?
            if not (neighbor := tn.get_neighbor_by_name(message['neighbor'])):
                _log.warning(f'Unable to find valid neighbor "{neighbor}" from received transactive signal.')
            # Parse ISO format string to datetime object
            signal_time = datetime.fromisoformat(message['signal_time']) if message.get('signal_time') else None
            signal = cls(market=market, neighbor=neighbor, direction='received',
                         signal_time=signal_time, measurement_unit=message['unit'])
        except KeyError as e:
            _log.warning(f'Missing required field in transactive signal: {e}.')
            return

        for ti_start, interval_signal in message.get('intervals', {}).items():
            time_interval = market.time_intervals.get_by_start_time(ti_start)
            if not time_interval:
                _log.warning(f'Received signal for time interval "{ti_start}",'
                             f' which is not found in time intervals of market "{market.name}".')
                continue
            balance_point_index = interval_signal.get('balance_point', None)
            flexibility = interval_signal.get('flexibility', {})
            vertices = []
            for i, record in flexibility.items():
                try:
                    vertex = Vertex(price=record['marginal_price'],
                                    cost=record['cost'],
                                    quantity=record['quantity'],
                                    continuity=record['continuity'],
                                    uncertainty=record['uncertainty'])
                except KeyError as e:
                    _log.warning(f"Missing required field in flexibility record {i}: {e}. Skipping this record.")
                    continue
                vertices.append(vertex)
                if i == balance_point_index:
                    # If this is the balance point, we need to store the index for later retrieval.
                    signal.__balance_vertices[time_interval] = vertex

            signal.update(time_interval, vertices, archive=True)
        signal.archive()
        return signal


    @staticmethod
    def _diff_exceeds_threshold(vertex_1, vertex_2, threshold, no_flexibility, mixed_direction):
        sign = -1 if mixed_direction else 1
        quantity_difference = abs((sign * vertex_2.quantity) - vertex_1.quantity)  # [avg. kW]
        average_quantity = 0.5 * abs(vertex_1.quantity + (sign * vertex_2.quantity))  # [avg. kW]
        # Calculate the relative Euclidian distance (a relative error criterion) between the two scheduled points.
        if average_quantity != 0:
            if no_flexibility:
                distance = quantity_difference / average_quantity  # dimensionless
            else:
                marginal_price_difference = abs(vertex_2.price - vertex_1.price)  # [$/kWh]
                average_marginal_price = 0.5 * abs(vertex_2.price + vertex_1.price)  # [$/kWh]
                distance = sqrt(
                    (quantity_difference / average_quantity) ** 2
                    + (marginal_price_difference / average_marginal_price) ** 2
                )  # dimensionless
        else:
            distance = 0
        # If the distance (relative error) exceeds the threshold, the scheduled points are significantly different.
        return True if distance > threshold else False

    # NOTE: This was previously called helpers.are_different1().
    @classmethod
    def balance_points_differ(cls, sent_signal: Self, received_signal: Self, time_interval: TimeInterval,
                              threshold: float):
        """
        Returns true is two TransactiveSignals, representing sent and received messages in a time interval,
         are significantly different.
        :param: sent_signal: TransactiveSignal
        :param: received_signal: TransactiveSignal
        :param: time_interval: TimeInterval
        :param: threshold: float - relative error beyond which the points differ significantly.
        :returns: boolean - true if the relative distance between the scheduled points exceeds the threshold.
        """
        sent: Vertex = sent_signal.balance_point(time_interval)
        received: Vertex = received_signal.balance_point(time_interval)

        # Determine whether there are a mixture of received and sent or calculated signals.
        are_received = [s.direction == 'received' for s in [sent_signal, received_signal]]
        mixed_direction = True if any(are_received) and not all(are_received) else False
        no_flexibility = True if len(sent_signal) == 1 or len(received_signal) == 1 else False

        return cls._diff_exceeds_threshold(received, sent, threshold, no_flexibility, mixed_direction)

    # TODO: balance_points_differ and conditions_have_changed are very similar and could be merged.
    # NOTE: This was previously called helpers.are_different2().
    @classmethod
    def conditions_have_changed(cls, prepared_signal: Self, sent_signal: Self,
                                time_interval: TimeInterval, threshold: float):
        """
        Assess whether two TransactiveSignals, representing the calculated and sent messages in an active time
         interval are significantly different from one another.
        If the signals are different, this indicates that local conditions have changed, and a revised,
         updated transactive message should be sent to the Neighbor.

        :param: my_singal: TransactiveSignal - The last message calculated for this transactive neighbor.
        :param: sent_signal: TransactiveSignal - The last message sent to this transactive neighbor.
        :param: threshold: float - A dimensionless, relative error that is used as a convergence criterion.

        :returns: boolean - True if the sent and recently calculated transactive messages are significantly different.
        """
        if len(sent_signal) != len(prepared_signal):
            return True

        # Get the scheduled values from mySignal and sentSignal records.
        sent_balance_vertex: Vertex = sent_signal.balance_point(time_interval)
        calculated_balance_vertex: Vertex = prepared_signal.balance_point(time_interval)

        no_flexibility = True if len(sent_signal) == 1 or len(prepared_signal) == 1 else False

        # If either the signal is constant, (1 Vertex), use only quantity as its marginal price is not meaningful.
        if no_flexibility:
            return cls._diff_exceeds_threshold(sent_balance_vertex, calculated_balance_vertex, threshold,
                                               no_flexibility, False)
        else:  # There are multiple records, meaning that the Neighbor is price-responsive..
            # Local convergence of the coordination sub-problem does not require the exact point to be determined
            #   but rather that the flexibility is accurately conveyed to the Neighbor.
            # See if any non-balance-point vertex of sent signal cannot be matched with a corresponding vertex of
            #   calculated signal.
            for sent_vertex in sent_signal.flexibility_vertices(time_interval):
                for calculated_vertex in prepared_signal.flexibility_vertices(time_interval):
                    if cls._diff_exceeds_threshold(sent_vertex, calculated_vertex, threshold, no_flexibility, False):
                        return True
            return False

    @property
    def table_name(self):
        return 'transactive_signal'

    def get_record(self) -> dict:
        record = super(TransactiveSignal, self).get_record()
        record.update({
            "direction": self.direction,
            "protocol_version": self.PROTOCOL_VERSION,
            "market_id": self.market.id,
            "signal_time": self.signal_time.isoformat() if self.signal_time else None
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(TransactiveSignal, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "transactive_signal",
                "inherits": "measurement",
                "columns": [
                    {"name": "id", "type": "Uuid", "foreign_key": "measurement.id", "kwargs": {"primary_key": True}},
                    {"name": "direction", "enumeration": "Direction"},
                    {"name": "protocol_version", "type": "Float"},
                    {"name": "market_id", "foreign_key": "market.id"},
                    {"name": "signal_time", "type": "DateTime"}
                ]
            }
        ])
        return definitions
