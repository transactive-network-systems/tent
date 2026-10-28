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

from uuid6 import uuid7
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
        self.id = uuid7()
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
    """Create a test TransactiveNode with required data manager."""
    tn = _TestTransactiveNode()
    tn._data_manager = _NoOpDataManager()  # pylint: disable=protected-access
    return tn


def _create_market(transactive_node, anchor_time):
    """Create a test Market with appropriate series and timings."""
    series = _SimpleMarketSeries()
    transition_times: MarketTimings = series.duration.get_timings(anchor_time)
    market = Market(transactive_node, series, series.initial_market_state, transition_times, price_model=_NoOpPriceModel())
    return market


def _create_time_interval(market, start_time, duration=timedelta(hours=1)):
    """Create a TimeInterval for the market."""
    return TimeInterval(market, duration, start_time)


def _create_bulk_supplier(transactive_node, name='BulkSupplier'):
    """Create a BulkSupplierDC neighbor."""
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


def _sorted_vertices(sd_curve):
    """Sort vertices by quantity then price for consistent testing."""
    return sorted(sd_curve.vertices, key=lambda vertex: (vertex.quantity, vertex.price))


def test_update_vertices():
    """
    Test BulkSupplierDC.update_vertices() method with two cases:
    Case 1: No demand charges or losses (expect 2 vertices)
    Case 2: With demand charges (expect 4 vertices)
    """
    print('Running BulkSupplier_dc.test_update_vertices()')

    # Case 1: Expect two active vertices when neither demand charges nor losses are in play
    print('Case 1: Expect two active vertices when neither demand charges nor losses are in play.')
    
    # Create test environment
    dt = datetime.now()
    dt = dt.replace(hour=1, month=1)  # an example LLH hour
    
    test_node = _create_transactive_node()
    test_market = _create_market(test_node, dt)
    test_time_interval = _create_time_interval(test_market, dt)
    test_market.time_intervals = [test_time_interval]
    test_market.market_clearing_time = dt
    
    # Create BulkSupplierDC neighbor
    test_neighbor = _create_bulk_supplier(test_node, 'TestSupplier1')
    test_neighbor.maximum_power = 100
    test_neighbor.minimum_power = 0
    test_neighbor.loss_factor = 0
    test_neighbor.demand_rate = 0  # CAUTION: the method makes sure this is as in the supplier table.
    test_neighbor.demand_threshold = 101  # Placing this just above the vertices' power range
    test_neighbor.scheduled_powers.update(test_time_interval, 0.0)
    
    test_market.neighbors = [test_neighbor]

    try:
        test_neighbor.update_vertices(test_market)
        print('  - The method ran without errors.')
    except RuntimeWarning as message:
        print('  - ERRORS ENCOUNTERED: ' + str(message))

    # Get active vertices for the time interval
    sd_curve = test_neighbor.active_vertices.get_by_interval(test_time_interval)
    assert sd_curve is not None, 'No active vertices found for the time interval'
    
    vertices = _sorted_vertices(sd_curve)
    
    assert len(vertices) == 2, f'An unexpected number of active vertices were found: {len(vertices)}'
    
    # Check vertex powers (quantities)
    vertex_quantities = [v.quantity for v in vertices]
    assert vertex_quantities[0] in [0, 100], f'First active vertex power was not as expected: {vertex_quantities[0]}'
    assert vertex_quantities[1] in [0, 100], f'Second active vertex power was not as expected: {vertex_quantities[1]}'
    
    # Check vertex prices
    assert vertices[0].price == pytest.approx(0.0366, rel=1e-4), \
        f'First active vertex marginal price is not as expected: {vertices[0].price}'
    assert vertices[1].price == pytest.approx(0.0366, rel=1e-4), \
        f'Second active vertex marginal price is not as expected: {vertices[1].price}'

    print('Case 1 completed successfully.')

    # Case 2: Same as Case 1, but let demand charges be in play
    print('Case 2: Same as Case 1, but let demand charges be in play.')
    
    dt2 = datetime(2021, 3, 3, 13)  # an example HLH hour
    
    test_node2 = _create_transactive_node()
    test_market2 = _create_market(test_node2, dt2)
    test_time_interval2 = _create_time_interval(test_market2, dt2)
    test_market2.time_intervals = [test_time_interval2]
    test_market2.market_clearing_time = dt2
    
    # Create BulkSupplierDC neighbor
    test_neighbor2 = _create_bulk_supplier(test_node2, 'TestSupplier2')
    test_neighbor2.maximum_power = 100
    test_neighbor2.minimum_power = 0
    test_neighbor2.loss_factor = 0
    test_neighbor2.demand_rate = 0  # CAUTION: the method makes sure this is as in the supplier table.
    test_neighbor2.demand_threshold = 50  # Placing this in the middle of the vertices' power range
    test_neighbor2.scheduled_powers.update(test_time_interval2, 0.0)
    
    test_market2.neighbors = [test_neighbor2]

    try:
        test_neighbor2.update_vertices(test_market2)
        print('  - The method ran without errors.')
    except RuntimeWarning as message:
        print('  - ERRORS ENCOUNTERED: ' + str(message))

    # Get active vertices for the time interval
    sd_curve2 = test_neighbor2.active_vertices.get_by_interval(test_time_interval2)
    assert sd_curve2 is not None, 'No active vertices found for the time interval'
    
    vertices2 = _sorted_vertices(sd_curve2)
    
    # 200806DJH: The method now orders the resulting vertices by power and marginal price, which makes it easier to
    # automate testing.
    assert len(vertices2) == 4, f'An unexpected number of active vertices were found: {len(vertices2)}'
    
    # Check vertex powers (quantities)
    assert vertices2[0].quantity == pytest.approx(0.0), \
        f'First active vertex power was not as expected: {vertices2[0].quantity}'
    assert vertices2[1].quantity == pytest.approx(50.0), \
        f'Second active vertex power was not as expected: {vertices2[1].quantity}'
    assert vertices2[2].quantity == pytest.approx(50.0), \
        f'Third active vertex power was not as expected: {vertices2[2].quantity}'
    assert vertices2[3].quantity == pytest.approx(100.0), \
        f'Fourth active vertex power was not as expected: {vertices2[3].quantity}'
    
    # Check vertex prices
    expected_prices = [0.03641, 0.03641, 9.31641, 9.31641]
    actual_prices = [v.price for v in vertices2]
    assert actual_prices == pytest.approx(expected_prices, rel=1e-4), \
        f'Marginal prices were not as expected. Expected: {expected_prices}, Got: {actual_prices}'

    print('Case 2 completed successfully.')
    print('test_update_vertices() ran to completion.\n')


if __name__ == '__main__':
    print('Running tests in test_bulk_supplier_new.py\n')
    test_update_vertices()
    print('Tests in test_bulk_supplier_new.py ran to completion.\n')
