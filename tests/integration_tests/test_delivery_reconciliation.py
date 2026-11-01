"""
After the campus Day-Ahead auction clears: the cleared prices and schedules are held through Delivery, entering
Delivery teaches the price model the cleared prices, and the market reconciles and expires at the end of its delivery
day. Settlement costs computed from the cleared prices balance across the campus.
"""
from datetime import timedelta

import pytest

from tent.enumerations import MarketState
from tests.integration_tests.conftest import DEFAULT_PRICE, asset_named, clear, powers, prices, step_to


@pytest.fixture
def cleared(campus, clock):
    market = campus.market_series[0].newest_market
    clear(campus, clock, market)
    return campus, market


def test_cleared_prices_and_schedules_are_held_through_delivery_and_the_market_expires_after_it(cleared, clock):
    campus, market = cleared
    times = market.transition_times
    cleared_prices = prices(market)
    schedules = {e.name: powers(e, market) for e in campus.local_assets + campus.neighbors}
    assert DEFAULT_PRICE not in cleared_prices

    step_to(campus, clock, times.delivery)
    assert market.market_state is MarketState.Delivery and market._stateIsCompleted
    step_to(campus, clock, times.delivery + timedelta(hours=12))
    assert market.market_state is MarketState.Delivery, 'delivery lasts the 24 cleared hours'
    assert prices(market) == cleared_prices
    assert {e.name: powers(e, market) for e in campus.local_assets + campus.neighbors} == schedules

    assert times.reconciliation == times.delivery + timedelta(hours=24) == times.expiration
    step_to(campus, clock, times.reconciliation)
    assert market.reconciled is True
    assert market.market_state is MarketState.Expired, 'no reconciliation period: Reconcile and Expired in one tick'
    assert len(market.time_intervals) == 0, 'an expired market releases its intervals'
    assert campus.market_series[0].newest_market is not market


def test_entering_delivery_teaches_the_price_model_the_cleared_prices(cleared, clock):
    campus, market = cleared
    for time_interval in market.time_intervals:
        assert market.price_model.get(time_interval.start_time)[0] == DEFAULT_PRICE
    step_to(campus, clock, market.transition_times.delivery)
    for time_interval, price in zip(market.time_intervals, prices(market)):
        # HourlyAveragePriceModel starts each hour with two default prices and averages the cleared one in.
        assert market.price_model.get(time_interval.start_time)[0] == pytest.approx((2 * DEFAULT_PRICE + price) / 3)
    successor = campus.market_series[0].newest_market
    assert successor is not market
    for time_interval in market.time_intervals:
        assert successor.price_model.get(time_interval.start_time)[0] == DEFAULT_PRICE, \
            'the successor copied the model when it was spawned, before this market cleared'


def test_settlement_costs_at_the_cleared_prices_balance_across_the_campus(cleared):
    campus, market = cleared
    market.update_costs()
    building = asset_named(campus, 'InelasticBuildings')
    cor = campus.neighbors[0]
    for time_interval, price in zip(market.time_intervals, prices(market)):
        hours = time_interval.duration.total_seconds() / 3600
        assert building.production_costs.get_by_interval(time_interval).value == 0.0, 'zero cost parameters'
        assert building.dual_costs.get_by_interval(time_interval).value == pytest.approx(
            -price * building.scheduled_powers.get_by_interval(time_interval).value * hours), 'what the load pays'
        assert cor.production_costs.get_by_interval(time_interval).value > 0.0, 'the supplier\'s cost of the energy'
        assert market.production_costs.get_by_interval(time_interval).value == pytest.approx(
            sum(e.production_costs.get_by_interval(time_interval).value
                for e in campus.local_assets + campus.neighbors))
    assert building.total_dual_cost == pytest.approx(sum(building.dual_costs.get_by_interval(ti).value
                                                         for ti in market.time_intervals))
    assert building.total_dual_cost > 4000.0, 'a day of campus load at a few cents per kWh'
    assert market.total_production_cost == pytest.approx(market.total_dual_cost), \
        'power nets to zero at the cleared price in every interval, so the price terms of the dual costs cancel'
