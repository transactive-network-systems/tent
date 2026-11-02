"""
The campus auction's clearing steps stay fast and archive in batches.

The campus node (two auction series of 24 intervals, three neighbors, a load predictor on a temperature forecast and a
solar array) is the deployed configuration whose state machine must keep up with a simulation clock running at 36x: its
real-time series gives it 300 simulated seconds, about eight real ones, between its own bid and the market clearing. Each
step of a clearing used to be dominated by archiving, one transaction per updated power, cost and curve (over a thousand
per step); a market tick now writes everything it touched in one batch when the tick ends (tent.base.archiving). The time
bound here is generous (the steps take tens of milliseconds without an archive) so that it fails only on a regression of
that kind, not on a slow machine.
"""
import time

from archivable import NullDataManager

from tent.enumerations import MarketState
from tests.integration_tests.conftest import deliver_neighbor_signals, step_to

STEP_BOUND_SECONDS = 2.0


class CountingDataManager(NullDataManager):
    """A NullDataManager that counts archive_data calls and the records they carry."""

    def __init__(self, parent):
        super().__init__(parent)
        self.calls = 0
        self.records = 0

    def archive_data(self, records=None):
        self.calls += 1
        self.records += len(records) if isinstance(records, (list, tuple)) else 1


def timed(tn, clock, when) -> float:
    started = time.perf_counter()
    step_to(tn, clock, when)
    return time.perf_counter() - started


def test_each_state_of_a_campus_clearing_is_bounded(campus, clock):
    """One real-time and one day-ahead pass from Inactive to a cleared DeliveryLead, each step within the bound."""
    tn = campus
    tn._data_manager = CountingDataManager(tn)
    timings = {}
    for series_name in ('Real-Time_Auction', 'Day-Ahead_Auction'):
        market = tn.get_market_series_by_attribute('name', series_name, first_only=True).newest_market
        deliver_neighbor_signals(tn, market)
        # The campus series have no Active or Negotiation window: the step at the active time schedules the assets
        # and sends the bid; the step at the delivery lead time balances the market and sends the offers.
        timings[series_name, 'active_to_market_lead'] = timed(tn, clock, market.transition_times.active)
        assert market.market_state is MarketState.MarketLead and market._stateIsCompleted
        timings[series_name, 'delivery_lead'] = timed(tn, clock, market.transition_times.delivery_lead)
        assert market.market_state is MarketState.DeliveryLead and market._stateIsCompleted
    slow = {k: round(v, 3) for k, v in timings.items() if v > STEP_BOUND_SECONDS}
    assert not slow, f'steps over {STEP_BOUND_SECONDS} s: {slow}'


def test_a_clearing_step_archives_in_one_batch_per_market(campus, clock):
    """The step that schedules the assets and bids writes its hundreds of rows in a few archive_data calls (one per
    market the step touched), not one per row."""
    tn = campus
    tn._data_manager = manager = CountingDataManager(tn)
    market = tn.get_market_series_by_attribute('name', 'Real-Time_Auction', first_only=True).newest_market
    deliver_neighbor_signals(tn, market)
    step_to(tn, clock, market.transition_times.active)
    markets_ticked = sum(len(series.markets) for series in tn.market_series)
    assert manager.records > 200, 'the step scheduled two assets and two neighbors over 24 intervals'
    assert manager.calls <= markets_ticked, f'{manager.calls} archive_data calls for {manager.records} records'


def test_a_campus_clearing_runs_with_debug_logging_enabled(campus, clock, caplog):
    """The deployment logs at DEBUG. The dumps that are built only at that level (final prices, the solar array's
    scheduled powers) must not fail when they are built: this run of a real-time clearing with every tent logger at
    DEBUG would raise from the state machine if one did, and the solar dump must name this market's intervals only."""
    import logging
    caplog.set_level(logging.DEBUG, logger='tent')
    tn = campus
    market = tn.get_market_series_by_attribute('name', 'Real-Time_Auction', first_only=True).newest_market
    deliver_neighbor_signals(tn, market)
    step_to(tn, clock, market.transition_times.active)
    step_to(tn, clock, market.transition_times.delivery_lead)
    assert market.market_state is MarketState.DeliveryLead and market._stateIsCompleted
    solar_dumps = [r.getMessage() for r in caplog.records if r.getMessage().startswith('Scheduled_powers for market')]
    assert solar_dumps, 'the solar array reported its schedule at DEBUG'
    for dump in solar_dumps:
        named = dump.split(' --- ', 1)[0].rsplit(': ', 1)[1]
        assert named in dump.split(' --- ', 1)[1], 'the dump lists this market\'s own intervals'
