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

from datetime import datetime, timedelta
from uuid6 import uuid7

import pytest

from tent.base.component import Component
from tent.containers.time_interval import TimeInterval
from tent.containers.transactive_signal import TransactiveSignal
from tent.containers.vertex import Vertex
from tent.enumerations import SignalDirection


class _StubDataManager:
    """No-op data manager so Archivable objects can initialize during tests."""

    def archive_data(self, _records):
        return None

    def add_tables(self, _table_defs):
        return None

    def init_archive(self):
        return None


class _StubTransactiveNode:
    def __init__(self, data_manager):
        self.data_manager = data_manager
        self.id = uuid7()
        self.name = 'StubTN'


class _StubMarket:
    def __init__(self, data_manager, name='StubMarket'):
        self.data_manager = data_manager
        self.market_series_name = f'{name}Series'
        self.name = name
        self.id = uuid7()


class _StubNeighbor(Component):
    def __init__(self, transactive_node, name):
        super().__init__(description='stub neighbor', name=name, transactive_node=transactive_node)

    def get_record(self):
        return super().get_record()

    @property
    def table_name(self):
        return 'component'

    @classmethod
    def get_archive_definitions(cls):
        return super(_StubNeighbor, cls).get_archive_definitions()


LEGACY_RECEIVED_POINTS = [
    ('20180622T020000', 0, 37.2684974345231, 0.022220484745369),
    ('20180622T020000', 1, 0.0, 0.022219983435294),
    ('20180622T020000', 2, 110.0, 0.022221463079283),
]

LEGACY_SENT_POINTS = [
    ('20180622T020000', 0, -38.14998337529896, 0.021457224582343865),
    ('20180622T020000', 1, -40.11329301604832, 0.01975728208437844),
    ('20180622T020000', 2, -20.969231491568785, 0.03633327350909643),
]

def _parse_timestamp(timestamp):
    return datetime.strptime(timestamp, '%Y%m%dT%H%M%S')


def _build_time_interval(market, timestamp):
    return TimeInterval(market, timedelta(hours=1), _parse_timestamp(timestamp))


def _build_signal(points, direction, time_interval, market, tn):
    neighbor = _StubNeighbor(transactive_node=tn, name=f'{direction.name.title()}Neighbor')
    signal = TransactiveSignal(market=market, neighbor=neighbor, direction=direction)
    vertices = [Vertex(price=point[3], quantity=point[2]) for point in points]
    signal.update(time_interval, vertices, archive=False)
    balance_index = next((idx for idx, point in enumerate(points) if point[1] == 0), 0)
    signal._TransactiveSignal__balance_vertices[time_interval] = vertices[balance_index]  # pylint: disable=protected-access
    return signal


@pytest.mark.parametrize(
    ('sent_points', 'received_points', 'threshold', 'expected'),
    [
        (LEGACY_SENT_POINTS, LEGACY_RECEIVED_POINTS, 0.02, True),
        (LEGACY_SENT_POINTS, LEGACY_SENT_POINTS, 0.05, False),
    ],
)
def test_balance_points_differ(sent_points, received_points, threshold, expected):
    data_manager = _StubDataManager()
    transactive_node = _StubTransactiveNode(data_manager)
    market = _StubMarket(data_manager)
    time_interval = _build_time_interval(market, sent_points[0][0])

    sent_signal = _build_signal(sent_points, SignalDirection.sent, time_interval, market, transactive_node)
    received_signal = _build_signal(received_points, SignalDirection.received, time_interval, market, transactive_node)

    assert (
        TransactiveSignal.balance_points_differ(sent_signal, received_signal, time_interval, threshold)
        is expected
    )
