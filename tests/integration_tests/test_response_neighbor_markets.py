"""
A campus-like node clears against a ResponseNeighbor: a counterparty that states only a price or only a quantity.

The node is a MockTN with one local asset and one ResponseNeighbor and runs a ConsensusMarket (which, with no
transactive neighbor, is the local balancing problem alone) or an Auction. Values reach the neighbor through the node's
subscription mechanism, as a message bus would deliver them.
"""
import logging
from copy import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tent.enumerations import MarketState
from tent.local_asset import LocalAsset
from tent.market.auction import Auction
from tent.market.consensus_market import ConsensusMarket
from tent.market.market_series import MarketSeries
from tent.neighbor.response_neighbor import ResponseNeighbor
from tent.utils.timer import Timer
from tests.mock_tn import MockTN
from tests.stubs import clock_at

T0 = datetime(2017, 7, 1, 0, 0, tzinfo=timezone.utc)
INF = float('inf')
PRICE_KEY = 'utility/price'
CAP_KEY = 'dso/import_cap'

INELASTIC_LOAD = [[INF, 0.0, -100.0, True]]                          # 100 kW whatever the price
FLEXIBLE_LOAD = [[0.04, 0.0, -100.0, True], [0.08, 0.0, -20.0, True]]  # 100 kW at 0.04 $/kWh down to 20 kW at 0.08


class FlexibleAsset(LocalAsset):
    """A local load whose flexibility is its default_vertices curve and that schedules at the market price."""

    def update_vertices(self, market):
        for time_interval in market.time_intervals:
            self.active_vertices.update(time_interval, [copy(v) for v in self.default_vertices.vertices],
                                        archive=False, replace=True)

    def schedule_power(self, market):
        for time_interval in market.time_intervals:
            mp = market.marginal_prices.get_by_interval(time_interval)
            price = market.series.default_price if mp is None or mp.value is None else mp.value
            self.scheduled_powers.update(time_interval, self.default_vertices.power_from_vertices(price))
        self.schedule_calculated = True


SERIES = dict(name='Campus Market', duration_active=300.0, duration_negotiation=600.0, duration_market_lead=300.0,
              duration_delivery_lead=300.0, interval_duration=3600.0, intervals_to_clear=3,
              market_clearing_interval=3600.0, method='Interpolation', duality_gap_threshold=0.01,
              default_price=0.05)


def build_node(load_curve, load_limits, response_type, subscribe_key, market_class=ConsensusMarket, **neighbor_kwargs):
    tn = MockTN(name='campus')
    asset = FlexibleAsset(transactive_node=tn, name='buildings', cost_parameters=[0.0, 0.0, 0.0],
                          default_vertices=load_curve, minimum_power=load_limits[0], maximum_power=load_limits[1])
    tn.local_assets = [asset]
    kwargs = dict(minimum_power=0.0, maximum_power=1000.0, loss_factor=0.0, demand_rate=0.0,
                  cost_parameters=[0.0, 0.0, 0.0], up_or_down='upstream')
    kwargs.update(neighbor_kwargs)
    tn.neighbors = [ResponseNeighbor(transactive_node=tn, name='counterparty', response_type=response_type,
                                     subscribe_key=subscribe_key, **kwargs)]
    tn.market_series = [MarketSeries(transactive_node=tn, market_class=market_class, **SERIES)]
    return tn


@pytest.fixture
def clock():
    state = {'t': T0}
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: state['t']):
        yield state


def market_of(tn):
    return tn.market_series[0].newest_market


def step(tn, clock, t):
    clock['t'] = t
    tn.step()


def negotiate(tn, clock, ticks=3):
    market = market_of(tn)
    times = market.transition_times
    step(tn, clock, times.active)
    step(tn, clock, times.negotiation)
    for seconds in range(1, ticks + 1):
        step(tn, clock, times.negotiation + timedelta(seconds=seconds))
    assert market.market_state is MarketState.Negotiation
    return market


def prices(market):
    return [market.marginal_prices.get_by_interval(ti).value for ti in market.time_intervals]


def powers(entity, market):
    return [entity.scheduled_powers.get_by_interval(ti).value for ti in market.time_intervals]


def test_a_stated_price_clears_every_interval_at_exactly_that_price(clock, caplog):
    tn = build_node(INELASTIC_LOAD, (-100.0, 0.0), 'price', PRICE_KEY)
    tn.provide_subscription(PRICE_KEY, [{'start_time': T0.isoformat(), 'value': 0.0625}])
    market = negotiate(tn, clock)
    assert market.converged and market.in_consensus
    assert prices(market) == pytest.approx([0.0625] * 3, abs=1e-9)
    assert powers(tn.neighbors[0], market) == pytest.approx([100.0] * 3)
    assert powers(tn.local_assets[0], market) == pytest.approx([-100.0] * 3)
    # Nothing was sent, and the deadline closes the market cleanly.
    assert not tn.neighbors[0].last_sent_signals
    with caplog.at_level(logging.WARNING, logger='tent.market'):
        step(tn, clock, market.transition_times.market_lead)
    assert market.market_state is MarketState.MarketLead
    assert not any('timed out' in r.getMessage() for r in caplog.records)


def test_a_stated_import_cap_is_met_by_local_flexibility(clock):
    tn = build_node(FLEXIBLE_LOAD, (-100.0, -20.0), 'quantity', CAP_KEY)
    tn.provide_subscription(CAP_KEY, 50.0)
    market = negotiate(tn, clock)
    assert market.converged
    # The flexible load is 50 kW where its curve reads 0.04 + 0.04 * (50 / 80).
    assert prices(market) == pytest.approx([0.065] * 3, abs=1e-9)
    assert powers(tn.neighbors[0], market) == pytest.approx([50.0] * 3)
    assert powers(tn.local_assets[0], market) == pytest.approx([-50.0] * 3)


@pytest.mark.parametrize('reward, expected_price, expected_import', [
    (0.10, 0.10, 20.0),   # the reward exceeds what the load will pay: it curtails to its minimum, imports 20 kW
    (0.05, 0.065, 50.0),  # the reward is below the load's value of power: the instruction is met in full
])
def test_a_curtailment_reward_is_weighed_against_local_flexibility(clock, reward, expected_price, expected_import):
    tn = build_node(FLEXIBLE_LOAD, (-100.0, -20.0), 'quantity', CAP_KEY, curtailment_reward=reward)
    tn.provide_subscription(CAP_KEY, 50.0)
    market = negotiate(tn, clock)
    assert market.converged
    assert prices(market) == pytest.approx([expected_price] * 3, abs=1e-6)
    assert powers(tn.neighbors[0], market) == pytest.approx([expected_import] * 3, abs=1e-6)


def test_a_schedule_change_during_negotiation_reopens_it_with_the_new_value(clock):
    tn = build_node(INELASTIC_LOAD, (-100.0, 0.0), 'price', PRICE_KEY)
    tn.provide_subscription(PRICE_KEY, 0.05)
    market = negotiate(tn, clock)
    assert prices(market) == pytest.approx([0.05] * 3, abs=1e-9)
    neighbor = tn.neighbors[0]
    passes_before = market.negotiation_passes

    second_interval_start = market.time_intervals[1].start_time
    with patch.object(market, 'on_local_change', wraps=market.on_local_change) as on_local_change:
        tn.provide_subscription(PRICE_KEY, [{'start_time': second_interval_start.isoformat(), 'value': 0.09}])
    on_local_change.assert_called_once_with(neighbor)
    assert not market.converged, 'the market is marked for rebalancing'
    # The curves already carry the new value; the next tick rebalances at it.
    assert [v.price for v in neighbor.active_vertices.get_by_interval(market.time_intervals[1]).vertices] == [0.09, 0.09]
    step(tn, clock, market.transition_times.negotiation + timedelta(seconds=30))
    assert market.converged and market.negotiation_passes == passes_before + 1
    assert prices(market) == pytest.approx([0.05, 0.09, 0.09], abs=1e-9), 'the new price holds from its start on'


def test_intervals_before_the_first_value_wait_and_a_value_holds_until_changed(clock, caplog):
    def missing_value_errors():
        return [r for r in caplog.records
                if r.levelno == logging.ERROR and r.name == 'tent.neighbor.response_neighbor']

    with caplog.at_level(logging.WARNING):
        # The market is built before any value is stated: every interval is reported, once.
        tn = build_node(INELASTIC_LOAD, (-100.0, 0.0), 'price', PRICE_KEY)
        market = market_of(tn)
        first, second, third = market.time_intervals
        neighbor = tn.neighbors[0]
        assert len(missing_value_errors()) == 3
        # The schedule starts at the second interval; the first still has no value.
        tn.provide_subscription(PRICE_KEY, [{'start_time': second.start_time.isoformat(), 'value': 0.07}])
        negotiate(tn, clock, ticks=4)
    errors = missing_value_errors()
    assert len(errors) == 3, 'the missing value is reported once per market and interval, not once per tick'
    assert all('counterparty' in r.getMessage() for r in errors)
    assert first.name in errors[0].getMessage()
    assert neighbor.active_vertices.get_by_interval(first) is None
    assert neighbor.active_vertices.get_by_interval(second) is not None
    assert any(f'counterparty has no vertices in time interval {first.name}' in r.getMessage() for r in caplog.records)
    # The only supply is missing in the first interval, so that interval cannot balance: the load is unmet there, the
    # deficit is recorded once and the interval is out of consensus.
    assert any('cannot balance' in r.getMessage() and first.name in r.getMessage() for r in caplog.records)
    assert market.net_powers.get_by_interval(first).value == pytest.approx(-100.0)
    assert powers(neighbor, market)[0] == 0.0
    assert market.unbalanceable == {first.name: pytest.approx(-100.0)}
    assert not market.in_consensus

    # A value for the first interval arrives; the earlier entry still holds for the later intervals.
    tn.provide_subscription(PRICE_KEY, [{'start_time': first.start_time.isoformat(), 'value': 0.03}])
    step(tn, clock, market.transition_times.negotiation + timedelta(seconds=30))
    assert market.converged
    assert prices(market) == pytest.approx([0.03, 0.07, 0.07], abs=1e-9)
    assert powers(neighbor, market) == pytest.approx([100.0] * 3)


def test_a_malformed_message_is_refused_and_the_market_keeps_the_stated_value(clock):
    tn = build_node(INELASTIC_LOAD, (-100.0, 0.0), 'price', PRICE_KEY)
    tn.provide_subscription(PRICE_KEY, 0.05)
    market = negotiate(tn, clock)
    with pytest.raises(ValueError, match='timezone-aware'):
        tn.provide_subscription(PRICE_KEY, [{'start_time': '2017-07-01T01:00:00', 'value': 0.09}])
    step(tn, clock, market.transition_times.negotiation + timedelta(seconds=30))
    assert prices(market) == pytest.approx([0.05] * 3, abs=1e-9)


def test_an_auction_clears_against_an_upstream_stated_price(clock):
    tn = build_node(FLEXIBLE_LOAD, (-100.0, -20.0), 'price', PRICE_KEY, market_class=Auction)
    tn.provide_subscription(PRICE_KEY, 0.06)
    market = market_of(tn)
    times = market.transition_times
    for when in (times.active, times.negotiation, times.negotiation + timedelta(seconds=1), times.market_lead,
                 times.delivery_lead):
        step(tn, clock, when)
    assert market.market_state is MarketState.DeliveryLead
    assert prices(market) == pytest.approx([0.06] * 3, abs=1e-9)
    # The flexible load at 0.06 $/kWh is 60 kW, all of it imported at the stated price.
    assert powers(tn.local_assets[0], market) == pytest.approx([-60.0] * 3, abs=1e-6)
    assert powers(tn.neighbors[0], market) == pytest.approx([60.0] * 3, abs=1e-6)
    assert not tn.neighbors[0].last_sent_signals, 'a non-transactive upstream neighbor is sent nothing'


def test_a_configured_node_archives_the_neighbor_with_its_response_type(tmp_path):
    config = {
        'name': 'campus',
        'data_manager': {'class_name': 'LocalDataManager',
                         'db_params': {'url': f'sqlite:///{tmp_path / "archive.sqlite"}'}},
        'neighbors': [{'class_name': 'ResponseNeighbor', 'name': 'dso', 'response_type': 'quantity',
                       'subscribe_key': CAP_KEY, 'curtailment_reward': 0.2, 'minimum_power': 0.0,
                       'maximum_power': 500.0, 'loss_factor': 0.0, 'demand_rate': 0.0, 'up_or_down': 'upstream'}],
        'local_assets': [{'class_name': 'LocalAsset', 'name': 'buildings', 'cost_parameters': [0.0, 0.0, 0.0],
                          'default_vertices': INELASTIC_LOAD}],
        'market_series': [{'class_name': 'MarketSeries', 'market_class_name': 'ConsensusMarket', **SERIES}],
    }
    clock_at(T0)
    tn = MockTN()
    tn.configure_node(config)
    neighbor = tn.neighbors[0]
    assert isinstance(neighbor, ResponseNeighbor)
    rows = tn.data_manager.query_data('response_neighbor', conditions={'id': neighbor.id})
    assert len(rows) == 1
    assert rows[0]['response_type'] == 'quantity'
    assert rows[0]['subscribe_key'] == CAP_KEY
    assert rows[0]['curtailment_reward'] == 0.2
    neighbor_rows = tn.data_manager.query_data('neighbor', conditions={'id': neighbor.id})
    assert len(neighbor_rows) == 1 and neighbor_rows[0]['transactive'] is False
    assert CAP_KEY in tn.subscriptions
