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
jurisdiction or organization that has cooperated in the development of these
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

import pytest

from tent.containers import MarketDurations, MarketTimings
from tent.containers.time_interval import TimeInterval
from tent.enumerations import MarketState
from tent.market import Market
from tent.neighbor.bulk_supplier_dc import BulkSupplierDC
from tent.transactive_node import TransactiveNode


class _NoOpDataManager:
    """Minimal data manager so Archivable objects can initialize."""

    def archive_data(self, _records):
        return None

    def add_tables(self, _table_defs):
        return None

    def init_archive(self):
        return None


class _NoOpPriceModel:
    """Deterministic price model used by tests when forecasts are required."""

    def __init__(self, price=0.04):
        self._price = price

    def get(self, _timestamp):
        return self._price, None


class _TestTransactiveNode(TransactiveNode):
    """Concrete TransactiveNode for tests that satisfies abstract methods."""

    def issue_signal(self, _recipient, _serialized_signal):
        return None

    def register_subscription(self, subscribe_key=None, callback=None, **_kwargs):
        return None

    def _sleep(self):
        return None

    def state_machine_loop(self):
        return None


class _SimpleMarketSeries:
    """Lightweight MarketSeries stand-in used to build Market instances."""

    def __init__(self, name='TestMarketSeries'):
        self.name = name
        self.description = name
        self.market_order = 1
        self.default_price = 0.05
        self.duration: MarketDurations = MarketDurations.new(
            active=0,
            negotiation=0,
            market_lead=0,
            delivery_lead=0,
            reconciliation=0,
            market_interval=timedelta(hours=1),
            intervals_to_clear=1,
            market_clearing_interval=timedelta(hours=1),
            delivery_anchor_time=None,
        )
        self.intervals_to_clear = self.duration.intervals_to_clear
        self.initial_market_state = MarketState.Inactive


def _create_transactive_node():
    tn = _TestTransactiveNode()
    tn._data_manager = _NoOpDataManager()  # pylint: disable=protected-access
    return tn


def _create_market(transactive_node, anchor_time):
    series = _SimpleMarketSeries()
    transition_times: MarketTimings = series.duration.get_timings(anchor_time)
    market = Market(transactive_node, series, transition_times, price_model=_NoOpPriceModel())
    return market


def _create_time_interval(market, start_time, duration=timedelta(hours=1)):
    return TimeInterval(market, duration, start_time)


def _create_bulk_supplier(transactive_node, name='BulkSupplier'):
    supplier = BulkSupplierDC(
        transactive_node=transactive_node,
        name=name,
        description='Test bulk supplier',
        cost_parameters=[0.0, 0.0, 0.0],
        default_vertices=[{'price': float('inf'), 'quantity': 0.0, 'cost': 0.0}],
        maximum_power=0.0,
        minimum_power=0.0,
    )
    transactive_node.neighbors.append(supplier)
    return supplier


def _setup_environment(start_time):
    tn = _create_transactive_node()
    market = _create_market(tn, start_time)
    time_interval = _create_time_interval(market, start_time)
    market.time_intervals = [time_interval]
    market.market_clearing_time = start_time
    supplier = _create_bulk_supplier(tn)
    supplier.scheduled_powers.update(time_interval, 0.0)
    market.neighbors = [supplier]
    return supplier, market, time_interval


def _sorted_vertices(sd_curve):
    return sorted(sd_curve.vertices, key=lambda vertex: (vertex.quantity, vertex.price))


def test_update_vertices_without_demand_charges():
    supplier, market, time_interval = _setup_environment(datetime(2021, 1, 1, 1, 0, 0))
    supplier.maximum_power = 100.0
    supplier.minimum_power = 0.0
    supplier.loss_factor = 0.0
    supplier.demand_rate = 0.0
    supplier.demand_threshold = 101.0

    supplier.update_vertices(market)

    sd_curve = supplier.active_vertices.get_by_interval(time_interval)
    assert sd_curve is not None
    vertices = _sorted_vertices(sd_curve)

    assert len(vertices) == 2
    assert [vertex.quantity for vertex in vertices] == pytest.approx([0.0, 100.0])
    assert [vertex.price for vertex in vertices] == pytest.approx([0.03660, 0.03660], rel=1e-4)


def test_update_vertices_with_demand_charges():
    supplier, market, time_interval = _setup_environment(datetime(2021, 3, 3, 13, 0, 0))
    supplier.maximum_power = 100.0
    supplier.minimum_power = 0.0
    supplier.loss_factor = 0.0
    supplier.demand_rate = 0.0
    supplier.demand_threshold = 50.0

    supplier.update_vertices(market)

    sd_curve = supplier.active_vertices.get_by_interval(time_interval)
    assert sd_curve is not None
    vertices = _sorted_vertices(sd_curve)

    assert len(vertices) == 4
    assert [vertex.quantity for vertex in vertices] == pytest.approx([0.0, 50.0, 50.0, 100.0])
    assert [vertex.price for vertex in vertices] == pytest.approx([0.03641, 0.03641, 9.31641, 9.31641], rel=1e-4)

