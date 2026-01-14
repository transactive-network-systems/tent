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


from datetime import datetime, timedelta, date, time

from tent.containers import MarketDurations, MarketTimings, TimeInterval, Vertex
from tent.enumerations import MarketState
from tent.local_asset import LocalAsset
from tent.market import Market
from tent.neighbor import Neighbor
from tent.transactive_node import TransactiveNode


DEFAULT_PRICE = 0.05


class _TestTransactiveNode(TransactiveNode):
    """Concrete TransactiveNode for tests that satisfies abstract methods."""

    def issue_signal(self, recipient: str, serialized_signal: dict):  # pylint: disable=unused-argument
        return None

    def register_subscription(self, subscribe_key, callback):  # pylint: disable=unused-argument
        return None

    def _sleep(self):
        return None

    def state_machine_loop(self):
        return None


class _NoOpDataManager:
    """Minimal data manager stub so Archivable objects can initialize."""

    def archive_data(self, _records):
        return None

    def add_tables(self, _table_defs):
        return None

    def init_archive(self):
        return None


class _NoOpPriceModel:
    """Predictable price model for tests that returns a constant price."""

    def get(self, _timestamp):
        return DEFAULT_PRICE, None


class _SimpleMarketSeries:
    """Lightweight stand-in for MarketSeries required by Market()."""

    def __init__(self, name='TestMarketSeries'):
        self.name = name
        self.description = name
        self.market_order = 1
        self.default_price = DEFAULT_PRICE
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


def create_transactive_node():
    tn = _TestTransactiveNode()
    tn._data_manager = _NoOpDataManager()  # pylint: disable=protected-access
    return tn


def create_neighbor_model(transactive_node, name='TestNeighbor'):
    return Neighbor(
        transactive_node,
        cost_parameters=[0.0, 0.0, 0.0],
        default_vertices=[Vertex(price=DEFAULT_PRICE, cost=0.0, quantity=0.0)],
        information_services=[],
        meter_points=[],
        name=name,
        maximum_power=100.0,
        minimum_power=0.0,
    )


def create_test_market(transactive_node):
    series = _SimpleMarketSeries()
    transition_times: MarketTimings = series.duration.get_timings(datetime.now())
    market = Market(transactive_node, series, transition_times)
    market.price_model = _NoOpPriceModel()
    return market


def create_time_interval(market, start_time=None, duration=timedelta(hours=1)):
    start = start_time if start_time else datetime.now()
    return TimeInterval(market, duration, start)


def assign_price(market, time_interval, price=DEFAULT_PRICE):
    market.marginal_prices.update(time_interval, price)


def seed_measurements(entity, time_interval, scheduled_power=100, vertex_power=100, price=DEFAULT_PRICE):
    entity.scheduled_powers.update(time_interval, scheduled_power)
    entity.active_vertices.update(time_interval, [Vertex(price=price, cost=0.0, quantity=vertex_power)])


def sum_measurement_values(measurement):
    return sum(value.value for _, value in measurement.all)


def test_schedule():
    print('Running AbstractModel.test_schedule()')

    test_tn = create_transactive_node()

    #   Create a test market test_mkt
    test_mkt = create_test_market(test_tn)

    #   Create a sample time interval ti
    dur = timedelta(hours=1)
    st = datetime.combine(date.today(), time()) + timedelta(hours=20)
    ti = create_time_interval(test_mkt, st, dur)

    #   Save the time interval and assign a marginal price
    test_mkt.time_intervals = [ti]
    assign_price(test_mkt, ti)

    #   Create a Neighbor test object and give it a default maximum power value
    # test_obj = Neighbor()

    #   Create a corresponding Neighbor.
    test_mdl = create_neighbor_model(test_tn)
    test_tn.neighbors.append(test_mdl)
    test_mdl.maximum_power = 100

    # 210803DJH: The assignment of active vertices depends on whether a neighbor is transactive or not. Let's presume it
    #            is not to test the more basic case.
    test_mdl.transactive = False

    #   Make sure that the model and object cross-reference one another
    # test_obj.model = test_mdl
    # test_mdl.object = test_obj

    #   Run a test with a Neighbor object
    print('- running test with a Neighbor:')

    try:
        test_mdl.schedule(test_mkt)
        print('  - the method encountered no errors')
    except RuntimeWarning:
        print('  - ERRORS ENCOUNTERED')

    scheduled_power = test_mdl.scheduled_powers.get_by_interval(ti)
    reserve_margin = test_mdl.reserve_margins.get_by_interval(ti)
    active_vertices = test_mdl.active_vertices.get_by_interval(ti)

    assert len(test_mdl.scheduled_powers) == 1, '  - the method did not store a scheduled power'
    assert len(test_mdl.reserve_margins) == 1, '  - the method did not store a reserve margin'
    assert len(test_mdl.active_vertices) == 1, '  - the method did not store an active vertex'
    assert scheduled_power is not None, '  - scheduled power measurement missing'
    assert reserve_margin is not None, '  - reserve margin measurement missing'
    assert active_vertices is not None and not active_vertices.empty, '  - active vertices were not stored'

    # Run a test again with a LocalAsset.
    # test_obj = LocalAsset()

    test_mdl = LocalAsset(test_tn)
    test_tn.local_assets.append(test_mdl)
    # test_obj.model = test_mdl
    # test_mdl.object = test_obj
    test_mdl.maximum_power = 100

    print('- running test with a LocalAsset:')

    try:
        test_mdl.schedule(test_mkt)
        print('  - the method encountered no errors')
    except RuntimeWarning:
        print('  - ERRORS ENCOUNTERED')

    scheduled_power = test_mdl.scheduled_powers.get_by_interval(ti)
    active_vertices = test_mdl.active_vertices.get_by_interval(ti)

    assert len(test_mdl.scheduled_powers) == 1, '  - the method did not store a scheduled power'
    assert scheduled_power is not None, '  - the scheduled power record is missing'
    assert len(test_mdl.active_vertices) == 1, '  - the method did not store an active vertex'
    assert active_vertices is not None and not active_vertices.empty, '  - the stored vertex set is empty'

    # Success
    print('test_schedule() ran to completion.\n')


def test_update_costs():
    print('Running AbstractModel.test_update_costs()')

    test_tn = create_transactive_node()

    #   Create a test market test_mkt
    test_mkt = create_test_market(test_tn)

    #   Create a sample time interval ti
    dur = timedelta(hours=1)
    st = datetime.combine(date.today(), time()) + timedelta(hours=20)
    ti = create_time_interval(test_mkt, st, dur)

    #   Save the time interval and assign a marginal price in the time interval
    test_mkt.time_intervals = [ti]
    assign_price(test_mkt, ti)

    #   Create a Neighbor test object and give it a default maximum power value
    # test_obj = Neighbor()
    #     test_obj.maximumPower = 100

    #   Create a corresponding Neighbor.
    test_mdl = create_neighbor_model(test_tn, name='CostNeighbor')
    test_tn.neighbors.append(test_mdl)

    #   Make sure that the model and object cross-reference one another
    # test_obj.model = test_mdl
    # test_mdl.object = test_obj

    seed_measurements(test_mdl, ti, scheduled_power=100, vertex_power=100, price=DEFAULT_PRICE)

    #   Run a test with a Neighbor object
    print('- running test with a Neighbor:')
    try:
        test_mdl.update_costs(test_mkt)
        print('  - the method encountered no errors')
    except RuntimeWarning:
        print('  - ERRORS ENCOUNTERED')

    assert len(test_mdl.production_costs) == 1, '  - the method did not store a production cost'
    assert len(test_mdl.dual_costs) == 1, '  - the method did not store a dual cost'
    assert test_mdl.total_production_cost == sum_measurement_values(test_mdl.production_costs), \
        '  - the method did not store a total production cost'
    assert test_mdl.total_dual_cost == sum_measurement_values(test_mdl.dual_costs), \
        '  - the method did not store a total dual cost'

    # Run a test again with a LocalAsset.
    # test_obj = LocalAsset()
    test_mdl = LocalAsset(test_tn)
    test_tn.local_assets.append(test_mdl)
    # test_obj.model = test_mdl
    # test_mdl.object = test_obj
    test_mdl.maximum_power = 100

    seed_measurements(test_mdl, ti, scheduled_power=100, vertex_power=100, price=DEFAULT_PRICE)

    print('- running test with a LocalAsset:')

    try:
        test_mdl.update_costs(test_mkt)
        print('  - the method encountered no errors')
    except RuntimeWarning:
        print('  - ERRORS ENCOUNTERED')

    assert len(test_mdl.production_costs) == 1, '  - the method did not store a production cost'
    assert len(test_mdl.dual_costs) == 1, '  - the method did not store a dual cost'
    assert test_mdl.total_production_cost == sum_measurement_values(test_mdl.production_costs), \
        '  - the method did not store a total production cost'
    assert test_mdl.total_dual_cost == sum_measurement_values(test_mdl.dual_costs), \
        '  - the method did not store a total dual cost'

    # Success
    print('test_update_costs() ran to completion.\n')


if __name__ == '__main__':
    # Test the sealed AbstractModel methods
    print('Running tests in testmodel.py\n')
    test_schedule()
    test_update_costs()
    print('Tests in testmodel.py ran to completion.\n')
