"""
A node running a MatchingEngine series clears: the book closes at the market-lead time, settlement turns the matched
legs into each entity's scheduled power and the interval's marginal price, and Delivery then updates the price model
and actuates assets as for any market. Before settlement existed the engine archived its matched legs and entered
Delivery with prices never set and schedules from the opening estimate.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tent.containers.vertex import Vertex
from tent.enumerations import MarketState
from tent.local_asset import LocalAsset
from tent.market.market_series import MarketSeries
from tent.market.matching_engine import MatchingEngine
from tent.neighbor import Neighbor
from tent.utils.timer import Timer
from tests.mock_tn import MockTN

T0 = datetime(2017, 7, 1, 0, 0, tzinfo=timezone.utc)
INF = float('inf')


class RecordingDataManager:
    def __init__(self):
        self.records = []

    def archive_data(self, records):
        self.records.extend(records if isinstance(records, (list, tuple)) else [records])

    def by_table(self, name):
        return [r['data'] for r in self.records if r.get('table_name') == name]


class LimitLoad(LocalAsset):
    """A load bidding its default power at up to 0.10 $/kWh in every interval (a limit demand order, so that the trade
    has a price: two market orders meeting discover none), recording when it is told to actuate."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.actuated_by = []

    def update_vertices(self, market):
        for time_interval in market.time_intervals:
            self.active_vertices.update(time_interval, [Vertex(price=0.10, quantity=0.0),
                                                        Vertex(price=0.10, quantity=self.default_power)],
                                        archive=False, replace=True)

    def actuate(self, market):
        self.actuated_by.append(market)


@pytest.fixture
def clock():
    state = {'t': T0}
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: state['t']):
        yield state


@pytest.fixture
def node():
    tn = MockTN(name='campus')
    tn._data_manager = RecordingDataManager()
    tn.local_assets = [LimitLoad(transactive_node=tn, name='buildings', cost_parameters=[0.0, 0.0, 0.0],
                                    default_power=-40.0)]
    tn.neighbors = [Neighbor(transactive_node=tn, name='utility', transactive=False, up_or_down='upstream',
                             default_vertices=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 100.0, True]],
                             minimum_power=0.0, maximum_power=100.0, loss_factor=0.0, demand_rate=0.0,
                             cost_parameters=[0.0, 0.0, 0.0])]
    tn.market_series = [MarketSeries(transactive_node=tn, name='Book', market_class=MatchingEngine,
                                     duration_active=300.0, duration_negotiation=600.0, duration_market_lead=300.0,
                                     duration_delivery_lead=300.0, interval_duration=3600.0, intervals_to_clear=2,
                                     market_clearing_interval=3600.0, default_price=0.05)]
    return tn


def step(tn, clock, t):
    clock['t'] = t
    tn.step()


def test_a_node_clears_its_book_into_schedules_and_prices(node, clock):
    market = node.market_series[0].newest_market
    times = market.transition_times
    load, utility = node.local_assets[0], node.neighbors[0]
    step(node, clock, times.active)
    step(node, clock, times.negotiation)  # assets schedule and are quoted; the first matching pass
    assert market.market_state is MarketState.Negotiation
    assert market.matchList, 'the inelastic load is matched against the utility\'s supply curve'
    opening = [market.marginal_prices.get_by_interval(ti).value for ti in market.time_intervals]
    assert opening == [0.05, 0.05]

    step(node, clock, times.market_lead)  # the book closes: settlement
    assert market.market_state is MarketState.MarketLead
    for ti in market.time_intervals:
        legs = [l for l in market.matchList if l.interval.name == ti.name]
        assert legs, ti.name
        traded = sum(abs(l.vertices[-1].quantity) for l in legs) / 2
        assert traded == pytest.approx(40.0)
        assert load.scheduled_powers.get_by_interval(ti).value == pytest.approx(-40.0)
        assert utility.scheduled_powers.get_by_interval(ti).value == pytest.approx(40.0)
        assert market.cleared_power(utility, ti) == pytest.approx(40.0)
        price = market.marginal_prices.get_by_interval(ti).value
        # The load's bid rests in the book (assets are quoted before neighbors) and the utility's offer hits it, so the
        # trade is at the resting order's limit, 0.10.
        assert price == pytest.approx(legs[-1].vertices[-1].price) == pytest.approx(0.10)
        assert market.average_match_prices.get_by_interval(ti).value == pytest.approx(
            sum(l.vertices[-1].price * abs(l.vertices[-1].quantity) for l in legs)
            / sum(abs(l.vertices[-1].quantity) for l in legs))
        assert price != 0.05, 'not the opening estimate'

    step(node, clock, times.delivery_lead)
    assert market.market_state is MarketState.DeliveryLead
    step(node, clock, times.delivery)  # the price model is updated from the settled prices and the assets actuate
    assert market.market_state is MarketState.Delivery
    assert load.actuated_by == [market]
    for ti in market.time_intervals:
        # The hourly-average price model blends the settled 0.10 into its prior; it has moved from the default.
        modelled = market.price_model.get(ti.start_time)[0]
        assert 0.05 < modelled <= 0.10 + 1e-9, modelled


def test_an_inelastic_load_against_a_supply_curve_discovers_the_curves_price(clock):
    """The campus case: the buildings are an inelastic market order (the base LocalAsset) resting in the book, the
    utility's offer is a curve from 0.04 at 0 kW to 0.08 at 100 kW. The 40 kW trade is priced from the curve, 0.056,
    and settlement makes that the interval's price."""
    tn = MockTN(name='campus')
    tn._data_manager = RecordingDataManager()
    tn.local_assets = [LocalAsset(transactive_node=tn, name='buildings', cost_parameters=[0.0, 0.0, 0.0],
                                  default_power=-40.0)]
    tn.neighbors = [Neighbor(transactive_node=tn, name='utility', transactive=False, up_or_down='upstream',
                             default_vertices=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 100.0, True]],
                             minimum_power=0.0, maximum_power=100.0, loss_factor=0.0, demand_rate=0.0,
                             cost_parameters=[0.0, 0.0, 0.0])]
    tn.market_series = [MarketSeries(transactive_node=tn, name='Book', market_class=MatchingEngine,
                                     duration_active=300.0, duration_negotiation=600.0, duration_market_lead=300.0,
                                     duration_delivery_lead=300.0, interval_duration=3600.0, intervals_to_clear=2,
                                     market_clearing_interval=3600.0, default_price=0.05)]
    market = tn.market_series[0].newest_market
    times = market.transition_times
    for t in (times.active, times.negotiation, times.market_lead):
        step(tn, clock, t)
    assert market.market_state is MarketState.MarketLead
    for ti in market.time_intervals:
        assert market.marginal_prices.get_by_interval(ti).value == pytest.approx(0.04 + 0.04 * 40.0 / 100.0)
        assert market.average_match_prices.get_by_interval(ti).value == pytest.approx(0.056)
        assert tn.local_assets[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(-40.0)
        assert tn.neighbors[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(40.0)
