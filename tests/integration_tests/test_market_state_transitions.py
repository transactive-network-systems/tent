"""
The campus Day-Ahead auction's state machine on the configured clock: the states a market is in at each transition
time, the successor it spawns as it activates, how Negotiation waits for the assets and is forced at the market lead
time, and that a series that runs for weeks keeps a constant number of markets.
"""
from datetime import timedelta

import pytest

from tent.enumerations import MarketState
from tent.local_asset import LocalAsset
from tests.integration_tests.conftest import DEFAULT_PRICE, configure_campus, deliver_neighbor_signals, prices, step_to


class StalledAsset(LocalAsset):
    """An asset whose scheduling never completes (schedule_calculated stays False)."""

    def schedule_power(self, market):
        return None


def test_the_day_ahead_auction_runs_its_lifecycle_on_the_configured_clock(campus, clock):
    series = campus.market_series[0]
    market = series.newest_market
    times = market.transition_times
    assert market.market_state is MarketState.Initial and len(series.markets) == 1
    assert times.active == times.negotiation == times.market_lead, 'no Active or Negotiation period is configured'
    assert times.delivery_lead - times.market_lead == times.delivery - times.delivery_lead == timedelta(minutes=15)
    deliver_neighbor_signals(campus, market)

    step_to(campus, clock, times.active - timedelta(seconds=1))
    assert market.market_state is MarketState.Inactive and len(series.markets) == 1
    step_to(campus, clock, times.active)
    assert market.market_state is MarketState.MarketLead and market._stateIsCompleted, \
        'Active, Negotiation (the assets schedule) and MarketLead (the bid is sent) at the same instant'
    successor = series.newest_market
    assert successor is not market and successor.market_state is MarketState.Inactive, 'spawned as this one activated'
    assert successor.prior_market_in_series.name == market.name
    assert successor.transition_times.delivery_lead == times.delivery_lead + timedelta(days=1)
    step_to(campus, clock, times.delivery_lead - timedelta(seconds=1))
    assert market.market_state is MarketState.MarketLead
    step_to(campus, clock, times.delivery_lead)
    assert market.market_state is MarketState.DeliveryLead and market._stateIsCompleted
    step_to(campus, clock, times.delivery)
    assert market.market_state is MarketState.Delivery
    step_to(campus, clock, times.reconciliation)
    assert market.market_state is MarketState.Expired and market.reconciled
    assert successor.market_state is MarketState.Delivery, 'the successor\'s delivery day starts as this one ends'
    campus.step()
    assert len(series.markets) == 2 and market not in series.markets, 'the expired market is pruned after the tick'


def test_negotiation_waits_for_the_assets_and_is_forced_at_the_market_lead_time(clock, caplog):
    campus = configure_campus(duration_negotiation=600)
    campus.local_assets.append(StalledAsset(transactive_node=campus, name='Stalled', cost_parameters=[0.0, 0.0, 0.0]))
    market = campus.market_series[0].newest_market
    times = market.transition_times
    assert times.market_lead - times.negotiation == timedelta(minutes=10)
    deliver_neighbor_signals(campus, market)

    step_to(campus, clock, times.negotiation)
    assert market.market_state is MarketState.Negotiation and not market._stateIsCompleted and not market.converged
    assert all(a.schedule_calculated for a in campus.local_assets if a.name != 'Stalled')
    step_to(campus, clock, times.negotiation + timedelta(minutes=5))
    assert market.market_state is MarketState.Negotiation and not market.converged, 'still waiting for the asset'
    assert not any('timed out' in r.message and market.name in r.message for r in caplog.records)

    with caplog.at_level('WARNING'):
        step_to(campus, clock, times.market_lead)
    assert any(r.message == f'MARKET: {market.name} Negotiation state timed out, forcing completion.'
               for r in caplog.records)
    assert market.converged and market.market_state is MarketState.MarketLead and market._stateIsCompleted
    assert any('Stalled has no vertices' in r.message for r in caplog.records), 'the bid leaves the stalled asset out'
    assert campus.neighbors[0].last_sent_signals[market] is not None


def test_a_series_running_for_weeks_keeps_a_constant_number_of_markets(campus, clock):
    series = campus.market_series[0]
    clearing_interval = series.duration.market_clearing_interval
    base = series.newest_market.transition_times.active
    for cycle in range(1, 15):
        # One clearing interval per tick: a market spawns its successor as it activates, so the clock must not skip an
        # Active window. Negotiation and Reconcile complete on their own for an auction with no neighbors' signals.
        step_to(campus, clock, base + clearing_interval * cycle)
        states = [m.market_state for m in series]
        assert states == [MarketState.Delivery, MarketState.MarketLead, MarketState.Inactive], (cycle, states)
        assert series.newest_market.transition_times.delivery_lead == \
               series.markets[0].transition_times.delivery_lead + 2 * clearing_interval
