"""
A waiting or failing auction market does not archive its system curve on every tick.

Run 18/21 of the auction containers archived 5924 versions of the same system curve, one per state-machine tick: the
campus real-time market sat in DeliveryLead while its handler failed on every tick (an asset without vertices after its
schedule had raised), and each tick balanced the market again and wrote the same curve again, for as long as the node
ran, since the exception also skipped the state's soft timeout. Two things hold now: an unchanged curve is never
archived twice (tent.base.archiving, T-58), and a failing state handler is logged and lets the soft timeout complete the
state (Market._run_state_handler).
"""
import logging
from datetime import timedelta
from unittest.mock import patch

from archivable import NullDataManager

from tent.enumerations import MarketState
from tent.neighbor import Neighbor
from tests.integration_tests.conftest import deliver_neighbor_signals, step_to


class CountingDataManager(NullDataManager):
    def __init__(self, parent):
        super().__init__(parent)
        self.rows: dict[str, int] = {}

    def archive_data(self, records=None):
        for record in (records if isinstance(records, (list, tuple)) else [records]):
            self.rows[record['table_name']] = self.rows.get(record['table_name'], 0) + 1

    def curves(self) -> int:
        return self.rows.get('sd_curve', 0)


def idle(tn, clock, start, ticks=50):
    for i in range(ticks):
        step_to(tn, clock, start + timedelta(seconds=1 + i))


def test_idle_ticks_in_market_lead_and_delivery_lead_archive_no_curves(campus, clock):
    tn = campus
    tn._data_manager = manager = CountingDataManager(tn)
    market = tn.get_market_series_by_attribute('name', 'Real-Time_Auction', first_only=True).newest_market
    deliver_neighbor_signals(tn, market, offices=True, supplier=False)
    step_to(tn, clock, market.transition_times.active)
    assert market.market_state is MarketState.MarketLead and market._stateIsCompleted
    before = manager.curves()
    idle(tn, clock, market.transition_times.active)
    assert manager.curves() == before, 'nothing changes while the bid is out: no curve versions'
    step_to(tn, clock, market.transition_times.delivery_lead)
    assert market.market_state is MarketState.DeliveryLead and not market._stateIsCompleted, 'waiting for the offer'
    before = manager.curves()
    idle(tn, clock, market.transition_times.delivery_lead)
    assert manager.curves() == before, 'waiting for the supplier archives nothing'
    deliver_neighbor_signals(tn, market)
    step_to(tn, clock, market.transition_times.delivery_lead + timedelta(seconds=60))
    assert market._stateIsCompleted and manager.curves() > before, 'the clearing archives the system curve once'
    cleared = manager.curves()
    idle(tn, clock, market.transition_times.delivery_lead + timedelta(seconds=61))
    assert manager.curves() == cleared


def test_a_handler_failing_on_every_tick_archives_no_new_curves_and_times_out(campus, clock, caplog):
    tn = campus
    tn._data_manager = manager = CountingDataManager(tn)
    market = tn.get_market_series_by_attribute('name', 'Real-Time_Auction', first_only=True).newest_market
    deliver_neighbor_signals(tn, market)
    step_to(tn, clock, market.transition_times.active)
    with patch.object(Neighbor, 'send_transactive_signal', side_effect=RuntimeError('the host cannot send')), \
            caplog.at_level(logging.DEBUG, logger='tent.market'):
        step_to(tn, clock, market.transition_times.delivery_lead)
        assert market.market_state is MarketState.DeliveryLead and not market._stateIsCompleted
        after_first = manager.curves()
        assert after_first > 0, 'the first balance archived the system curve'
        idle(tn, clock, market.transition_times.delivery_lead)
        assert manager.curves() == after_first, 'fifty failing ticks rebalanced to the same curve: no new versions'
        tracebacks = [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info]
        assert len(tracebacks) == 1 and 'while_in_delivery_lead' in tracebacks[0].getMessage(), 'logged once'
        step_to(tn, clock, market.transition_times.delivery)
    assert market.market_state is MarketState.Delivery, 'the soft timeout completed the state despite the failures'
    assert any('failed 5' in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING), 'count on leaving'
