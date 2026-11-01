"""
The campus auction when a neighbor's signal does not arrive. The auction waits for the signal until the next
transition time, then the state times out with a warning and the market goes on with what it has: without the
supplier's offer it delivers at the default prices and no offers go downstream; without the offices' bids it clears on
their default demand and never bids to the supplier. A bid that arrives while MarketLead is still open is used.
"""
from datetime import timedelta

from tent.enumerations import MarketState
from tests.integration_tests.conftest import DEFAULT_PRICE, deliver_neighbor_signals, powers, prices, step_to


def timed_out(caplog, market, state):
    return any(r.message == f'MARKET: {market.name} {state} state timed out, forcing completion.'
               for r in caplog.records)


def test_without_the_supplier_offer_the_auction_delivers_at_the_default_prices(campus, clock, caplog):
    market = campus.market_series[0].newest_market
    times = market.transition_times
    cor, *offices = campus.neighbors
    deliver_neighbor_signals(campus, market, supplier=False)
    step_to(campus, clock, times.active)
    assert market.market_state is MarketState.MarketLead and market._stateIsCompleted, 'the campus bid went out'
    assert cor.last_sent_signals[market] is not None

    step_to(campus, clock, times.delivery_lead)
    assert market.market_state is MarketState.DeliveryLead and not market._stateIsCompleted, 'waiting for the offer'
    step_to(campus, clock, times.delivery_lead + timedelta(minutes=10))
    assert market.market_state is MarketState.DeliveryLead and not market._stateIsCompleted
    assert not timed_out(caplog, market, 'DeliveryLead')

    with caplog.at_level('WARNING'):
        step_to(campus, clock, times.delivery)
    assert timed_out(caplog, market, 'DeliveryLead')
    assert market.market_state is MarketState.Delivery
    assert prices(market) == [DEFAULT_PRICE] * 24, 'nothing was balanced'
    assert all(office.last_sent_signals.get(market) is None for office in offices), 'no offers went downstream'
    assert powers(cor, market) == [0.0] * 24, 'the supplier\'s default offer starts above the default price'
    for time_interval in market.time_intervals:
        assert sum(e.scheduled_powers.get_by_interval(time_interval).value
                   for e in campus.local_assets + campus.neighbors) < -100.0, 'the campus is left unbalanced'


def test_without_the_offices_bids_the_auction_clears_on_their_default_demand_and_never_bids_upstream(campus, clock,
                                                                                                    caplog):
    market = campus.market_series[0].newest_market
    times = market.transition_times
    cor, *offices = campus.neighbors
    deliver_neighbor_signals(campus, market, offices=False)
    step_to(campus, clock, times.active)
    assert market.market_state is MarketState.MarketLead and not market._stateIsCompleted, 'waiting for the bids'
    assert cor.prepared_signals.get(market) is None and cor.last_sent_signals.get(market) is None

    with caplog.at_level('WARNING'):
        step_to(campus, clock, times.delivery_lead)
    assert timed_out(caplog, market, 'MarketLead')
    assert market.market_state is MarketState.DeliveryLead and market._stateIsCompleted, 'the offer was there'
    assert cor.last_sent_signals.get(market) is None, 'no bid ever went to the supplier'
    assert DEFAULT_PRICE not in prices(market), 'the market balanced'
    for office in offices:
        assert powers(office, market) == [-60.0] * 24, 'the offices are cleared at their configured default demand'
        assert office.last_sent_signals[market] is not None, 'and are sent the offer'


def test_bids_arriving_while_market_lead_is_open_are_used(campus, clock):
    market = campus.market_series[0].newest_market
    times = market.transition_times
    cor, *offices = campus.neighbors
    deliver_neighbor_signals(campus, market, offices=False)
    step_to(campus, clock, times.active)
    assert market.market_state is MarketState.MarketLead and not market._stateIsCompleted
    deliver_neighbor_signals(campus, market, supplier=False)
    step_to(campus, clock, times.active + timedelta(minutes=5))
    assert market.market_state is MarketState.MarketLead and market._stateIsCompleted
    assert cor.last_sent_signals[market] is not None
    for office in offices:
        assert powers(office, market) == [-55.0] * 24, 'the late bids, not the defaults'
