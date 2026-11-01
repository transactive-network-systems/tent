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


class TestBalancePointAtPrices:
    """mark_balance_points_at_prices: an auction bid's record 0 is the curve's own quantity at the market's price."""

    def test_the_point_is_interpolated_on_the_curve_at_the_interval_price(self, market_and_interval, caplog):
        tn, market, ti = market_and_interval
        neighbor = Neighbor(transactive_node=tn, name='CoR', cost_parameters=[0, 0, 0])
        sig = TransactiveSignal(market=market, neighbor=neighbor)
        sig.update(ti, [Vertex(price=0.04, quantity=-100.0), Vertex(price=0.08, quantity=-20.0)], archive=False)
        market.marginal_prices.update(ti, 0.06)
        with caplog.at_level('WARNING'):
            sig.mark_balance_points_at_prices(market.marginal_prices)
        point = sig.balance_point(ti)
        assert point.quantity == pytest.approx(-60.0) and point.price == pytest.approx(0.06)
        assert any(v is point for v in sig.get_by_interval(ti).vertices) and len(sig.get_by_interval(ti)) == 3
        assert [(v.price, v.quantity) for v in sig.flexibility_vertices(ti)] == [(0.04, -100.0), (0.08, -20.0)]
        assert not any('Indeterminate price' in r.message for r in caplog.records)

    def test_a_price_beyond_the_curve_marks_its_end_vertex_and_a_lone_vertex_is_its_own_point(self, market_and_interval):
        tn, market, ti = market_and_interval
        neighbor = Neighbor(transactive_node=tn, name='CoR', cost_parameters=[0, 0, 0])
        sig = TransactiveSignal(market=market, neighbor=neighbor)
        sig.update(ti, [Vertex(price=0.04, quantity=-100.0), Vertex(price=0.08, quantity=-20.0)], archive=False)
        market.marginal_prices.update(ti, 0.50)
        sig.mark_balance_points_at_prices(market.marginal_prices)
        assert sig.balance_point(ti) is sig.get_by_interval(ti).vertices[-1] and len(sig.get_by_interval(ti)) == 2
        lone = TransactiveSignal(market=market, neighbor=neighbor)
        lone.update(ti, [Vertex(price=float('inf'), quantity=0.0)], archive=False)
        lone.mark_balance_points_at_prices(market.marginal_prices)
        assert lone.balance_point(ti) is lone.get_by_interval(ti).vertices[0]

    def test_an_interval_without_a_price_is_an_error(self, market_and_interval):
        tn, market, ti = market_and_interval
        neighbor = Neighbor(transactive_node=tn, name='CoR', cost_parameters=[0, 0, 0])
        sig = TransactiveSignal(market=market, neighbor=neighbor)
        sig.update(ti, [Vertex(price=0.04, quantity=-100.0), Vertex(price=0.08, quantity=-20.0)], archive=False)
        market.marginal_prices._data.clear()
        with pytest.raises(ValueError, match='no price for interval'):
            sig.mark_balance_points_at_prices(market.marginal_prices)


class TestBalancePointNearTheEndOfTheCurve:
    """The campus's residual curve towards the city was nearly inelastic (-442.93 to -442.93 kW over its price range)
    and the city's scheduled power, read back from it, was -442.635 kW: 0.3 kW (0.07 percent) past the end, from
    rounding. Marking that balance point warned "Indeterminate price" on every pass."""
    CURVE = [(0.0402, -442.929673980963), (0.0486, -442.929673980963 + 1e-9)]
    SCHEDULED = 442.63539393683266  # the neighbor's power as seen from this node; the balance point flips the sign

    def signal(self, tn, market, ti):
        neighbor = Neighbor(transactive_node=tn, name='city', cost_parameters=[0, 0, 0])
        sig = TransactiveSignal(market=market, neighbor=neighbor)
        sig.update(ti, [Vertex(price=p, quantity=q) for p, q in self.CURVE], archive=False)
        return sig

    def test_a_quantity_a_hair_beyond_the_end_is_the_end_vertex_without_a_warning(self, market_and_interval, caplog):
        tn, market, ti = market_and_interval
        sig = self.signal(tn, market, ti)
        with caplog.at_level('WARNING'):
            sig.mark_balance_points(_ScheduledPowers(ti, self.SCHEDULED))
        point = sig.balance_point(ti)
        assert point is max(sig.get_by_interval(ti).vertices, key=lambda v: v.quantity), 'the end vertex itself'
        assert len(sig.get_by_interval(ti)) == 2, 'no vertex added outside the curve'
        assert not any('Indeterminate price' in r.message for r in caplog.records)

    def test_a_quantity_beyond_the_tolerance_still_warns(self, market_and_interval, caplog):
        tn, market, ti = market_and_interval
        sig = self.signal(tn, market, ti)
        with caplog.at_level('WARNING'):
            sig.mark_balance_points(_ScheduledPowers(ti, 440.0))  # 0.66 percent past the end
        assert sig.balance_point(ti).quantity == pytest.approx(-440.0)
        assert len(sig.get_by_interval(ti)) == 3
        assert any('Indeterminate price' in r.message and '-440.0' in r.message for r in caplog.records)

    def test_the_tolerance_is_one_constant(self):
        from tent.containers.sd_curve import SDCurve
        assert SDCurve.END_VERTEX_TOLERANCE == 1e-3
        end = Vertex(price=0.05, quantity=-442.929673980963)
        assert SDCurve.is_at_end_vertex(-442.63539393683266, end)
        assert not SDCurve.is_at_end_vertex(-440.0, end)
        assert SDCurve.is_at_end_vertex(0.0005, Vertex(price=0.05, quantity=0.0)), 'at least 1 kW absolute near zero'
