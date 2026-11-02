"""
The campus Day-Ahead auction clears. A single-node auction balances only in DeliveryLead, and only once the upstream
supplier's offer has arrived; until then every marginal price is the series default, so these tests look at the state
after that balance and at values that differ from the default: a distinct price per interval on the supplier's curve,
a campus that nets to zero at that price, and prices ordered like the load.
"""
import pytest

from tent.enumerations import MarketState
from tests.integration_tests.conftest import (DEFAULT_PRICE, INF, SUPPLIER_OFFER, asset_named, clear,
                                              deliver_neighbor_signals, powers, prices, step_to)


@pytest.fixture
def cleared(campus, clock):
    market = campus.market_series[0].newest_market
    clear(campus, clock, market)
    return campus, market


def test_marginal_prices_are_the_default_until_delivery_lead_balances_the_market(campus, clock):
    market = campus.market_series[0].newest_market
    deliver_neighbor_signals(campus, market)
    step_to(campus, clock, market.transition_times.active)
    assert market.market_state is MarketState.MarketLead
    assert prices(market) == [DEFAULT_PRICE] * 24, 'scheduling and bidding set no prices'
    step_to(campus, clock, market.transition_times.delivery_lead)
    assert market.market_state is MarketState.DeliveryLead
    cleared = prices(market)
    assert DEFAULT_PRICE not in cleared, 'every interval was balanced to a price of its own'
    by_hour = dict(zip((ti.start_time.hour for ti in market.time_intervals), cleared))
    assert len(set(cleared)) == 24, 'the net load differs in every hour, so does the price'
    assert max(by_hour, key=by_hour.get) == 15 and min(by_hour, key=by_hour.get) == 1, \
        'the highest price at the 15:00 load peak, the lowest at 01:00'
    assert all(SUPPLIER_OFFER[0][0] < p < SUPPLIER_OFFER[1][0] for p in cleared), \
        'within the supplier\'s offer: the 19.1 MW peak import clears on the rising segment of the offer bounded to ' \
        'the 20 MW link (0.0598 with losses), well under the offer\'s 0.07 top'


def test_the_cleared_price_balances_the_campus_in_every_interval(cleared):
    campus, market = cleared
    cor = campus.neighbors[0]
    entities = campus.local_assets + campus.neighbors
    for time_interval, price in zip(market.time_intervals, prices(market)):
        scheduled = {e.name: e.scheduled_powers.get_by_interval(time_interval).value for e in entities}
        assert sum(scheduled.values()) == pytest.approx(0.0, abs=1e-6), f'{time_interval.name}: net zero at the price'
        assert scheduled['CoR'] > 0 and scheduled['InelasticBuildings'] < 0 and scheduled['SolarPv'] >= 0
        assert scheduled['SMALL_OFFICE_ILC'] == scheduled['SMALL_OFFICE_DR'] == -55.0, 'the offices get their bids'
        supply = cor.active_vertices.get_by_interval(time_interval)
        assert supply.power_from_vertices(price) == pytest.approx(scheduled['CoR'], rel=1e-9), \
            'the supplier is scheduled on its own (loss-adjusted) curve at the cleared price'
        assert supply.price_from_vertices(scheduled['CoR']) == pytest.approx(price, rel=1e-9)


def test_prices_rise_with_the_hourly_load(cleared):
    campus, market = cleared
    cor = campus.neighbors[0]
    by_price = sorted(market.time_intervals, key=lambda ti: market.marginal_prices.get_by_interval(ti).value)
    by_import = sorted(market.time_intervals, key=lambda ti: cor.scheduled_powers.get_by_interval(ti).value)
    assert by_price == by_import, 'a rising offer prices the heaviest hour highest'
    building = asset_named(campus, 'InelasticBuildings')
    load = powers(building, market)
    assert -19500.0 < min(load) < -18500.0 and -7500.0 < max(load) < -7000.0 and min(load) > building.minimum_power, \
        'the July load swings from a 7.3 MW night to a 19.0 MW afternoon peak, inside the 24.2 MW limit'
    peak_import = max(cor.scheduled_powers.get_by_interval(ti).value for ti in market.time_intervals)
    assert 0.9 * cor.maximum_power < peak_import < cor.maximum_power * (1 - cor.loss_factor), \
        'the peak import (19.1 MW) uses most of the 20 MW link but stays below what the link delivers after losses, ' \
        'so every hour clears on the rising segment of the offer'


def test_local_assets_keep_their_inelastic_schedules_through_clearing(campus, clock):
    market = campus.market_series[0].newest_market
    deliver_neighbor_signals(campus, market)
    step_to(campus, clock, market.transition_times.active)
    before = {asset.name: powers(asset, market) for asset in campus.local_assets}
    step_to(campus, clock, market.transition_times.delivery_lead)
    for asset in campus.local_assets:
        assert powers(asset, market) == before[asset.name], f'{asset.name} offered no flexibility, so none was taken'
        for time_interval in market.time_intervals:
            vertices = asset.active_vertices.get_by_interval(time_interval).vertices
            assert [(v.price, v.quantity) for v in vertices] == \
                   [(INF, asset.scheduled_powers.get_by_interval(time_interval).value)]


def test_the_bid_to_the_supplier_is_the_campus_residual_demand_in_the_supplier_perspective(campus, clock):
    market = campus.market_series[0].newest_market
    deliver_neighbor_signals(campus, market)
    step_to(campus, clock, market.transition_times.active)
    cor = campus.neighbors[0]
    bid = cor.prepared_signals[market]
    assert cor.last_sent_signals[market] is not None
    for time_interval in market.time_intervals:
        local_demand = -sum(e.scheduled_powers.get_by_interval(time_interval).value
                            for e in campus.local_assets + campus.neighbors[1:])
        (vertex,) = bid.get_by_interval(time_interval).vertices
        assert vertex.price == INF, 'the campus offers no flexibility'
        delivered = -vertex.quantity
        # The supplier delivers the campus demand plus the transport losses, loss_factor * P^2 / maximum_power.
        assert delivered - cor.loss_factor * delivered ** 2 / cor.maximum_power == pytest.approx(local_demand, rel=1e-9)
        assert delivered > local_demand > 0, time_interval.name
