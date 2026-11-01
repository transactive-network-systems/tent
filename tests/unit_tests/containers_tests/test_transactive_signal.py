"""TransactiveSignal.balance_points_differ: the convergence comparison between a sent and a received signal."""
from datetime import datetime, timedelta, timezone

import pytest

from tent.containers.transactive_signal import TransactiveSignal
from tent.containers.vertex import Vertex
from tent.enumerations import SignalDirection
from tent.neighbor import Neighbor
from tests.mock_tn import MockTN
from tests.stubs import make_market

# Legacy convergence fixture: (price, quantity) points, balance point first. The received signal schedules 37.27 kW;
# the sent one scheduled -38.15 kW in the neighbor's perspective, i.e. the same flow seen from the other side.
RECEIVED = [(0.022220484745369, 37.2684974345231), (0.022219983435294, 0.0), (0.022221463079283, 110.0)]
SENT = [(0.021457224582343865, -38.14998337529896), (0.01975728208437844, -40.11329301604832),
        (0.03633327350909643, -20.969231491568785)]


class _ScheduledPowers:
    """The part of a Measurement that mark_balance_points reads: (interval, value-holder) pairs."""

    def __init__(self, time_interval, power):
        self.all = [(time_interval, type('Value', (), {'value': power})())]


@pytest.fixture
def market_and_interval():
    tn = MockTN()
    market = make_market(tn, datetime(2018, 6, 22, 2, tzinfo=timezone.utc))
    return tn, market, market.time_intervals[0]


def signal(tn, market, time_interval, points, direction):
    neighbor = Neighbor(transactive_node=tn, name=f'{direction.name}Neighbor', cost_parameters=[0, 0, 0])
    sig = TransactiveSignal(market=market, neighbor=neighbor, direction=direction)
    sig.update(time_interval, [Vertex(price=p, quantity=q) for p, q in points], archive=False)
    # The balance point is marked from the scheduled power as seen from this node, so the sign flips (see
    # mark_balance_points); the first point of each fixture is its balance point.
    sig.mark_balance_points(_ScheduledPowers(time_interval, -points[0][1]))
    assert sig.balance_point(time_interval).quantity == points[0][1]
    return sig


def test_balance_points_that_disagree_beyond_the_threshold_differ(market_and_interval):
    tn, market, ti = market_and_interval
    sent = signal(tn, market, ti, SENT, SignalDirection.sent)
    received = signal(tn, market, ti, RECEIVED, SignalDirection.received)
    assert TransactiveSignal.balance_points_differ(sent, received, ti, threshold=0.02) is True


def test_identical_signals_do_not_differ(market_and_interval):
    tn, market, ti = market_and_interval
    sent = signal(tn, market, ti, SENT, SignalDirection.sent)
    again = signal(tn, market, ti, SENT, SignalDirection.sent)
    assert TransactiveSignal.balance_points_differ(sent, again, ti, threshold=0.05) is False


def test_a_side_with_nothing_for_the_interval_differs(market_and_interval):
    tn, market, ti = market_and_interval
    sent = signal(tn, market, ti, SENT, SignalDirection.sent)
    empty = TransactiveSignal(market=market, neighbor=sent.neighbor, direction=SignalDirection.received)
    assert TransactiveSignal.balance_points_differ(sent, empty, ti, threshold=0.5) is True


def test_a_lone_vertex_is_its_own_balance_point(market_and_interval):
    tn, market, ti = market_and_interval
    neighbor = Neighbor(transactive_node=tn, name='Inelastic', cost_parameters=[0, 0, 0])
    sent = TransactiveSignal(market=market, neighbor=neighbor, direction=SignalDirection.sent)
    sent.update(ti, [Vertex(price=0.05, quantity=-40.0)], archive=False)
    received = TransactiveSignal(market=market, neighbor=neighbor, direction=SignalDirection.received)
    received.update(ti, [Vertex(price=0.05, quantity=40.0)], archive=False)
    assert sent.balance_point(ti) is None and received.balance_point(ti) is None
    assert TransactiveSignal.balance_points_differ(sent, received, ti, threshold=0.02) is False
    received.update(ti, [Vertex(price=0.05, quantity=60.0)], archive=False, replace=True)
    assert TransactiveSignal.balance_points_differ(sent, received, ti, threshold=0.02) is True


def test_a_balance_point_cannot_be_marked_on_an_empty_curve(market_and_interval):
    from sortedcontainers import SortedSet
    tn, market, ti = market_and_interval
    sig = signal(tn, market, ti, SENT, SignalDirection.sent)
    sig.get_by_interval(ti).vertices = SortedSet()
    import re
    with pytest.raises(ValueError, match=re.escape(f'neighbor sentNeighbor holds an empty curve for interval {ti.name}')):
        sig.mark_balance_points(_ScheduledPowers(ti, 0.0))
