"""
Curve archiving (tent.base.archiving): a curve version is written only when it differs from the last one archived for
the same measurement and interval (for a signal: the same neighbor, market, direction and interval), and the node's
curve_archiving level says which versions are written at all.
"""
from datetime import datetime, timedelta, timezone

import pytest

from tent.base import archiving
from tent.containers.transactive_signal import TransactiveSignal
from tent.containers.vertex import Vertex
from tent.enumerations import MarketState, SignalDirection
from tent.local_asset import LocalAsset
from tent.neighbor import Neighbor
from tests.integration_tests.test_consensus_negotiation_robustness import InelasticAsset, build_office, step, T0, clock  # noqa: F401
from tests.mock_tn import MockTN
from tests.stubs import make_market
from tests.unit_tests.containers_tests.test_archiving import RecordingDataManager


def market_and_manager():
    tn = MockTN()
    tn._data_manager = RecordingDataManager()
    market = make_market(tn, datetime(2017, 7, 1, tzinfo=timezone.utc), intervals_to_clear=2)
    return tn, market, tn.data_manager


def curve_rows(manager):
    return len(manager.rows('sd_curve')), len(manager.rows('vertex'))


def test_an_unchanged_curve_is_not_archived_again_and_a_changed_one_once():
    tn, market, manager = market_and_manager()
    ti = market.time_intervals[0]
    same = [Vertex(0.05, 0.0, -10.0), Vertex(0.07, 0.0, -5.0)]
    market.active_vertices.update(ti, [Vertex(0.05, 0.0, -10.0), Vertex(0.07, 0.0, -5.0)], replace=True)
    assert curve_rows(manager) == (1, 2)
    for _ in range(3):
        market.active_vertices.update(ti, [Vertex(v.price, v.cost, v.quantity) for v in same], replace=True)
    assert curve_rows(manager) == (1, 2), 'the same vertices again: no new version'
    market.active_vertices.update(ti, [Vertex(0.05, 0.0, -10.0), Vertex(0.07, 0.0, -6.0)], replace=True)
    assert curve_rows(manager) == (2, 4), 'one quantity changed: one new version'
    market.active_vertices.update(ti, [Vertex(0.05, 0.0, -10.0), Vertex(0.07, 1.0, -6.0)], replace=True)
    assert curve_rows(manager) == (3, 6), 'a cost changed: a new version too'
    market.active_vertices.update(market.time_intervals[1], [Vertex(0.05, 0.0, -10.0), Vertex(0.07, 1.0, -6.0)],
                                  replace=True)
    assert curve_rows(manager) == (4, 8), 'the same curve in another interval is another lineage'


def test_a_signal_re_sent_unchanged_writes_its_row_but_not_its_curves():
    tn, market, manager = market_and_manager()
    neighbor = Neighbor(transactive_node=tn, name='peer', transactive=True, cost_parameters=[0.0, 0.0, 0.0])

    def signal(quantity):
        s = TransactiveSignal(market=market, neighbor=neighbor, direction=SignalDirection.sent, archive_now=False)
        for ti in market.time_intervals:
            s.update(ti, [Vertex(0.05, 0.0, quantity), Vertex(0.07, 0.0, quantity / 2)], archive=False, replace=True)
        return s

    signal(-10.0).archive()
    assert len(manager.rows('transactive_signal')) == 1 and curve_rows(manager) == (2, 4)
    signal(-10.0).archive()
    assert len(manager.rows('transactive_signal')) == 2, 'the second signal is recorded'
    assert curve_rows(manager) == (2, 4), 'its curves are those already archived for this neighbor and direction'
    received = TransactiveSignal(market=market, neighbor=neighbor, direction=SignalDirection.received, archive_now=False)
    for ti in market.time_intervals:
        received.update(ti, [Vertex(0.05, 0.0, -10.0), Vertex(0.07, 0.0, -5.0)], archive=False, replace=True)
    received.archive()
    assert curve_rows(manager) == (4, 8), 'the same curve received is another lineage (direction)'
    signal(-12.0).archive()
    assert curve_rows(manager) == (6, 12), 'a changed sent curve is written in both intervals'


def office_negotiation(level: str, clock):
    """A consensus office negotiating with a default-curve neighbor through Active, Negotiation, MarketLead and the
    clearing, archiving curves at `level`; returns the recording data manager and the market."""
    tn, sent = build_office(InelasticAsset(transactive_node=None, name='load', curve_power=-10.0,
                                           cost_parameters=[0.0, 0.0, 0.0]),
                            neighbor_maximum=100.0)
    tn._data_manager = RecordingDataManager()
    archiving.set_curve_archiving(level)
    market = tn.market_series[0].newest_market
    times = market.transition_times
    step(tn, clock, times.active)
    for i in range(4):
        step(tn, clock, times.negotiation + timedelta(seconds=10 * i))
    step(tn, clock, times.market_lead)
    assert market.market_state is MarketState.MarketLead
    step(tn, clock, times.delivery_lead)
    step(tn, clock, times.delivery)
    assert market.market_state is MarketState.Delivery
    return tn.data_manager, market, sent


def test_the_three_levels_on_a_consensus_negotiation(clock):
    rows = {}
    for level in ('all', 'final', 'none'):
        manager, market, sent = office_negotiation(level, clock)
        rows[level] = {'sd_curve': len(manager.rows('sd_curve')), 'vertex': len(manager.rows('vertex')),
                       'transactive_signal': len(manager.rows('transactive_signal')),
                       'measurement': len(manager.rows('measurement'))}
        assert sent, 'the office negotiated (sent a signal) at every level'
    intervals = len(market.time_intervals)
    assert rows['none']['sd_curve'] == rows['none']['vertex'] == 0, 'no curve rows at all'
    assert rows['none']['transactive_signal'] == rows['all']['transactive_signal'] > 0, 'signal rows regardless'
    assert rows['none']['measurement'] == rows['all']['measurement'] > 0, 'measurement headers regardless'
    # Final: the system curve and the sent signal's curves when Negotiation ends, and again at clearing only if changed.
    assert 0 < rows['final']['sd_curve'] <= 2 * 2 * intervals
    assert rows['final']['sd_curve'] <= rows['all']['sd_curve'] and rows['final']['vertex'] <= rows['all']['vertex']
    assert rows['all']['sd_curve'] > intervals


def test_the_node_setting_reaches_the_archiving_module_and_is_validated():
    tn = MockTN()
    tn.configure_node({'name': 'n', 'curve_archiving': 'final'})
    assert archiving.curve_archiving() == 'final' and tn.curve_archiving == 'final'
    with pytest.raises(Exception, match='curve_archiving'):
        MockTN().configure_node({'name': 'n', 'curve_archiving': 'some'})
    with pytest.raises(ValueError):
        archiving.set_curve_archiving('some')
