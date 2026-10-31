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

from copy import copy
from datetime import datetime
from math import isfinite, sqrt
from weakref import WeakKeyDictionary
from typing import TYPE_CHECKING, Self

from tent.containers import SDCurve, SDMeasurement, TimeInterval, Vertex
from tent.enumerations import MeasurementType, MeasurementUnit, SignalDirection
from tent.utils.timer import Timer

if TYPE_CHECKING:
    from tent.neighbor import Neighbor
    from tent.market import Market

_log = logging.getLogger(__name__)


class TransactiveSignal(SDMeasurement):
    PROTOCOL_VERSION = 1.0

    def __init__(self, market, neighbor, direction: SignalDirection | None = None, signal_time=None,
                 sequence: int | None = None, in_reply_to: int | None = None, revised: bool = True, **kwargs):
        self.market: Market = market
        self.neighbor: Neighbor = neighbor
        if direction is not None and not isinstance(direction, SignalDirection):
            raise TypeError(f'direction must be a SignalDirection, not {direction!r}')
        self.direction: SignalDirection | None = direction
        self.signal_time: datetime | None = signal_time
        # Acknowledgement fields (see Neighbor.check_for_convergence). sequence numbers the signals one node sends to one
        # neighbor for one market; in_reply_to is the sequence of the last signal received from that neighbor when this
        # one was prepared; revised is False when the content is unchanged from the sender's previous signal, i.e. the
        # signal is only an acknowledgement and needs no reply.
        self.sequence: int | None = sequence
        self.in_reply_to: int | None = in_reply_to
        self.revised: bool = bool(revised)
        self.__balance_vertices: WeakKeyDictionary[TimeInterval, Vertex] = WeakKeyDictionary()
        super(TransactiveSignal, self).__init__(source=neighbor, measurement_type=MeasurementType.SignalVertex,
                                                measurement_unit=MeasurementUnit.kW, archive_now=False)
        # TODO: kW is a poor unit description of an SDCurve. What should we use?

    def balance_point(self, time_interval: TimeInterval) -> Vertex | None:
        """Get the balance point vertex for the specified time interval."""
        return self.__balance_vertices.get(time_interval, None)

    def flexibility_vertices(self, time_interval: TimeInterval) -> list[Vertex]:
        """Get the non-balance-point vertices for the specified time interval."""
        # TODO: Double check that is works as a comparison here. If there is a weak reference, we need to use ==.
        return [v for v in self.get_by_interval(time_interval).vertices if v is not self.balance_point(time_interval)]

    def mark_balance_points(self, scheduled_powers):
        """
        Mark the balance point on every interval's curve: the quantity this node has scheduled with the neighbor, seen
        from the neighbor's side. The curve is the residual system curve in the neighbor's perspective (positive means
        this node supplies the neighbor), while scheduled_powers hold the neighbor's power as seen from here (positive
        means the neighbor supplies this node), so the sign flips.
        """
        for ti, sp in scheduled_powers.all:
            sd_curve: SDCurve = self.get_by_interval(ti)
            if sd_curve is None:
                continue
            quantity = -float(sp.value)
            vertices = list(sd_curve.vertices)
            tolerance = 1e-6 * max(1.0, abs(quantity))
            # A lone vertex is inelastic: it is the balance point whatever was scheduled. Otherwise reuse a vertex that
            # already sits at the scheduled quantity (within interpolation noise) rather than adding a duplicate.
            existing = vertices[0] if len(vertices) == 1 else next(
                (v for v in vertices if abs(v.quantity - quantity) <= tolerance), None)
            if existing is not None:
                self.__balance_vertices[ti] = existing
                continue
            balance_price = sd_curve.price_from_vertices(quantity)
            balance_cost = sd_curve.prod_cost_from_vertices(quantity)
            vertex = Vertex(balance_price, balance_cost, quantity, curve=sd_curve)
            sd_curve.update(vertex, archive=False)
            self.__balance_vertices[ti] = vertex

    def snapshot(self) -> Self:
        """An independent copy of this signal (new id, copied curves and vertices, same balance points and header)."""
        clone = TransactiveSignal(market=self.market, neighbor=self.neighbor, direction=self.direction,
                                  signal_time=self.signal_time, sequence=self.sequence, in_reply_to=self.in_reply_to,
                                  revised=self.revised, archive_now=False)
        for ti, sd_curve in self.all:
            copied = [copy(v) for v in sd_curve.vertices]
            clone.update(ti, copied, archive=False, replace=True)
            balance = self.balance_point(ti)
            if balance is not None:
                index = list(sd_curve.vertices).index(balance)
                clone.__balance_vertices[ti] = list(clone.get_by_interval(ti).vertices)[index]
        return clone

    def generate_signal(self, scheduled_powers=None):
        if scheduled_powers is not None:
            self.mark_balance_points(scheduled_powers)
        self.signal_time = Timer.now()
        self.direction = SignalDirection.sent
        # self.archive()
        intervals = {}
        for ti, sd in self.all:
            flexibility = {
                i: {
                    'marginal_price': v.price,
                    'quantity': v.quantity,
                    'cost': v.cost,
                    'continuity': v.continuity,
                    'uncertainty': v.uncertainty
                } for i, v in enumerate(sd.vertices)
            }
            bp = self.balance_point(ti)
            vertices = list(sd.vertices)
            intervals[ti.start_time.isoformat()] = {
                'balance_point': vertices.index(bp) if bp is not None and bp in vertices else None,
                'flexibility': flexibility
            }
        return {
            'market': self.market.name,
            'neighbor': self.neighbor.tn.name,
            'protocol_version': self.PROTOCOL_VERSION,
            'signal_time': self.signal_time.isoformat(),
            'sequence': self.sequence,
            'in_reply_to': self.in_reply_to,
            'revised': self.revised,
            'unit': self.unit.name,
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
            if not (neighbor := tn.get_neighbors_by_attribute('name', message['neighbor'], first_only=True)):
                _log.warning(f'Unable to find valid neighbor "{neighbor}" from received transactive signal.')
            signal_time = datetime.fromisoformat(message['signal_time']) if message.get('signal_time') else None
            in_reply_to = message['in_reply_to']
            signal = cls(market=market, neighbor=neighbor, direction=SignalDirection.received,
                         signal_time=signal_time, sequence=int(message['sequence']),
                         in_reply_to=None if in_reply_to is None else int(in_reply_to),
                         revised=bool(message['revised']), measurement_unit=message['unit'])
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
                if balance_point_index is not None and int(i) == int(balance_point_index):
                    # If this is the balance point, we need to store the index for later retrieval.
                    signal.__balance_vertices[time_interval] = vertex

            signal.update(time_interval, vertices, archive=False, replace=True) #archive=True)
        signal.archive()
        return signal


    @staticmethod
    def _diff_exceeds_threshold(vertex_1, vertex_2, threshold, no_flexibility, mixed_direction):
        """
        Relative distance between two vertices, compared with the threshold. With mixed_direction the second vertex is
        a received one, stated from the remote side, so its quantity is sign-flipped before comparing. Price only enters
        when both vertices express flexibility and have finite, non-zero prices (an inelastic vertex sits at an infinite
        price, where a price difference is meaningless).
        """
        sign = -1 if mixed_direction else 1
        q1, q2 = vertex_1.quantity, sign * vertex_2.quantity
        quantity_difference = abs(q2 - q1)  # [avg. kW]
        average_quantity = 0.5 * abs(q1 + q2)  # [avg. kW]
        if average_quantity == 0:
            # Both at zero, or exact opposites: judge by the absolute gap against a 1 kW scale.
            quantity_term = quantity_difference
        else:
            quantity_term = quantity_difference / average_quantity  # dimensionless
        price_term = 0.0
        if not no_flexibility:
            p1, p2 = vertex_1.price, vertex_2.price
            average_marginal_price = 0.5 * abs(p1 + p2)  # [$/kWh]
            if isfinite(p1) and isfinite(p2) and average_marginal_price != 0:
                price_term = abs(p2 - p1) / average_marginal_price
        distance = sqrt(quantity_term ** 2 + price_term ** 2)  # dimensionless relative Euclidean distance
        # If the distance (relative error) exceeds the threshold, the scheduled points are significantly different.
        return distance > threshold

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
        sent_curve = sent_signal.get_by_interval(time_interval)
        received_curve = received_signal.get_by_interval(time_interval)
        if sent_curve is None or received_curve is None or len(sent_curve) == 0 or len(received_curve) == 0:
            return True  # one side has nothing for this interval: they cannot agree
        no_flexibility = len(sent_curve) == 1 or len(received_curve) == 1
        # A lone vertex is its own balance point; otherwise a signal without a marked balance point cannot be compared.
        sent: Vertex | None = sent_signal.balance_point(time_interval) or (list(sent_curve.vertices)[0]
                                                                            if len(sent_curve) == 1 else None)
        received: Vertex | None = received_signal.balance_point(time_interval) or (
            list(received_curve.vertices)[0] if len(received_curve) == 1 else None)
        if sent is None or received is None:
            return True

        # Determine whether there are a mixture of received and sent or calculated signals.
        are_received = [s.direction is SignalDirection.received for s in [sent_signal, received_signal]]
        mixed_direction = any(are_received) and not all(are_received)

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
        sent_curve = sent_signal.get_by_interval(time_interval)
        prepared_curve = prepared_signal.get_by_interval(time_interval)
        if sent_curve is None or prepared_curve is None:
            return sent_curve is not prepared_curve  # one side has the interval and the other does not
        if len(sent_curve) != len(prepared_curve):
            return True

        # Get the scheduled values from the prepared and sent records.
        sent_balance_vertex: Vertex | None = sent_signal.balance_point(time_interval)
        calculated_balance_vertex: Vertex | None = prepared_signal.balance_point(time_interval)

        no_flexibility = len(sent_curve) == 1 or len(prepared_curve) == 1

        # If either signal is constant (one vertex), only its quantity is meaningful, not its marginal price.
        if no_flexibility:
            sent_vertex = sent_balance_vertex or list(sent_curve.vertices)[0]
            calculated_vertex = calculated_balance_vertex or list(prepared_curve.vertices)[0]
            return cls._diff_exceeds_threshold(sent_vertex, calculated_vertex, threshold, True, False)

        # There are multiple records, meaning that the neighbor is offered price responsiveness. Local convergence of
        # the coordination sub-problem does not require the exact balance point to be determined, but the flexibility
        # must be conveyed accurately: conditions have changed if any sent flexibility vertex no longer has a
        # counterpart among the prepared ones, or if the scheduled balance point itself has moved.
        prepared_vertices = prepared_signal.flexibility_vertices(time_interval)
        for sent_vertex in sent_signal.flexibility_vertices(time_interval):
            if not any(not cls._diff_exceeds_threshold(sent_vertex, calculated_vertex, threshold, False, False)
                       for calculated_vertex in prepared_vertices):
                return True
        if sent_balance_vertex is not None and calculated_balance_vertex is not None:
            return cls._diff_exceeds_threshold(sent_balance_vertex, calculated_balance_vertex, threshold, False, False)
        return False

    def archive(self, records: list[dict] = None):
        """Archive the signal header and, with it, every interval's curve (sd_curve + vertex rows) as one version."""
        super(TransactiveSignal, self).archive(records)
        for _, curve in self.all:
            curve.archive()

    @property
    def table_name(self):
        return 'transactive_signal'

    def get_record(self) -> dict:
        record = super(TransactiveSignal, self).get_record()
        record.update({
            "direction": self.direction,
            "protocol_version": self.PROTOCOL_VERSION,
            "sequence": self.sequence,
            "in_reply_to": self.in_reply_to,
            "revised": self.revised,
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
                    {"name": "direction", "enumeration": "SignalDirection"},
                    {"name": "protocol_version", "type": "Float"},
                    {"name": "sequence", "type": "Integer", "kwargs": {"nullable": True}},
                    {"name": "in_reply_to", "type": "Integer", "kwargs": {"nullable": True}},
                    {"name": "revised", "type": "Boolean", "kwargs": {"nullable": True}},
                    {"name": "market_id", "foreign_key": "market.id"},
                    {"name": "signal_time", "type": "DateTime"}
                ]
            }
        ])
        return definitions