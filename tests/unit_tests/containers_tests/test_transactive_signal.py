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


class TestBalancePointWithAnExchangePrice:
    """The campus's residual curve towards the city was vertical at the exchanged quantity (an inelastic system below
    its offices' flexibility): reading the price off the curve gave 0.04025 while the campus cleared at 0.03228 on the
    city's side, and the city, whose own point was (6489.48, 0.03228), saw a 25 percent price disagreement forever."""
    CURVE = [(0.04025, -6489.48), (0.0423, -6459.0)]

    def signal(self, tn, market, ti):
        neighbor = Neighbor(transactive_node=tn, name='CoR', cost_parameters=[0, 0, 0])
        sig = TransactiveSignal(market=market, neighbor=neighbor)
        sig.update(ti, [Vertex(price=p, quantity=q) for p, q in self.CURVE], archive=False)
        return sig

    def test_the_point_carries_the_exchange_price_on_a_vertical_segment(self, market_and_interval):
        tn, market, ti = market_and_interval
        sig = self.signal(tn, market, ti)
        sig.mark_balance_points_at_quantities({ti: (-6489.48, 0.03228)})
        point = sig.balance_point(ti)
        assert (point.quantity, point.price) == (pytest.approx(-6489.48), pytest.approx(0.03228))
        assert len(sig.get_by_interval(ti)) == 3, 'added: the vertex at the quantity has another price'
        assert [(v.price, v.quantity) for v in sig.flexibility_vertices(ti)] == [(0.04025, -6489.48), (0.0423, -6459.0)]
        assert sig.get_by_interval(ti).power_from_vertices(0.03228) == pytest.approx(-6489.48), 'the point lies on the curve'

    def test_a_vertex_at_the_quantity_and_the_price_is_reused(self, market_and_interval):
        tn, market, ti = market_and_interval
        sig = self.signal(tn, market, ti)
        sig.mark_balance_points_at_quantities({ti: (-6489.48, 0.04025)})
        assert sig.balance_point(ti) is sig.get_by_interval(ti).vertices[0] and len(sig.get_by_interval(ti)) == 2

    def test_without_a_price_the_vertex_at_the_quantity_is_reused_as_before(self, market_and_interval):
        tn, market, ti = market_and_interval
        sig = self.signal(tn, market, ti)
        sig.mark_balance_points_at_quantities({ti: -6489.48})
        assert sig.balance_point(ti) is sig.get_by_interval(ti).vertices[0] and len(sig.get_by_interval(ti)) == 2
        sig.mark_balance_points_at_quantities({ti: (-6489.48, None)})
        assert len(sig.get_by_interval(ti)) == 2

    def test_a_lone_vertex_is_the_point_whatever_the_price(self, market_and_interval):
        tn, market, ti = market_and_interval
        neighbor = Neighbor(transactive_node=tn, name='CoR', cost_parameters=[0, 0, 0])
        sig = TransactiveSignal(market=market, neighbor=neighbor)
        sig.update(ti, [Vertex(price=float('inf'), quantity=-100.0)], archive=False)
        sig.mark_balance_points_at_quantities({ti: (-100.0, 0.05)})
        assert len(sig.get_by_interval(ti)) == 1 and sig.balance_point(ti) is sig.get_by_interval(ti).vertices[0]


class TestConditionsHaveChangedCompareCurvesAsFunctions:
    """The campus's residual towards an office is a near-horizontal line (0.03953 to 0.03955 $/kWh over 0 to 120 kW)
    whose interior vertices are the city's breakpoints that happen to fall inside the 120 kW window; they drift in and
    out of the window between passes while the line does not move. Comparing vertex sets called every pass a change
    and the campus revised its signal to every neighbor on every tick."""
    SENT = [(0.03953, 0.0), (0.03954, 49.983), (0.03954, 50.020), (0.03955, 120.0)]            # sequence 25
    PREPARED = [(0.03953, 0.0), (0.03953, 47.591), (0.03954, 49.705), (0.03954, 51.081), (0.03955, 120.0)]  # 26

    def signals(self, tn, market, ti, sent_points, prepared_points, balance=None):
        neighbor = Neighbor(transactive_node=tn, name='SMALL_OFFICE_DR', cost_parameters=[0, 0, 0])
        sent = TransactiveSignal(market=market, neighbor=neighbor, direction=SignalDirection.sent)
        sent.update(ti, [Vertex(price=p, quantity=q) for p, q in sent_points], archive=False)
        prepared = TransactiveSignal(market=market, neighbor=neighbor)
        prepared.update(ti, [Vertex(price=p, quantity=q) for p, q in prepared_points], archive=False)
        if balance is not None:
            sent.mark_balance_points_at_quantities({ti: balance[0]})
            prepared.mark_balance_points_at_quantities({ti: balance[1]})
        return sent, prepared

    def test_vertices_drifting_along_the_same_line_are_not_a_change(self, market_and_interval):
        tn, market, ti = market_and_interval
        sent, prepared = self.signals(tn, market, ti, self.SENT, self.PREPARED, balance=((50.0, 0.03954), (49.7, 0.03954)))
        assert TransactiveSignal.conditions_have_changed(prepared, sent, ti, threshold=0.02) is False
        assert TransactiveSignal.conditions_have_changed(sent, prepared, ti, threshold=0.02) is False

    def test_a_kink_the_sent_curve_did_not_have_is_a_change(self, market_and_interval):
        tn, market, ti = market_and_interval
        kinked = [(0.03953, 0.0), (0.03953, 60.0), (0.045, 60.0), (0.045, 120.0)]  # a step: 60 kW more only at 0.045
        sent, prepared = self.signals(tn, market, ti, self.SENT, kinked)
        assert TransactiveSignal.conditions_have_changed(prepared, sent, ti, threshold=0.02) is True

    def test_a_moved_line_is_a_change(self, market_and_interval):
        tn, market, ti = market_and_interval
        moved = [(p + 0.002, q) for p, q in self.SENT]  # five percent dearer everywhere
        sent, prepared = self.signals(tn, market, ti, self.SENT, moved)
        assert TransactiveSignal.conditions_have_changed(prepared, sent, ti, threshold=0.02) is True
        assert TransactiveSignal.conditions_have_changed(prepared, sent, ti, threshold=0.10) is False

    def test_flexibility_appearing_or_vanishing_is_a_change(self, market_and_interval):
        tn, market, ti = market_and_interval
        sent, prepared = self.signals(tn, market, ti, [(float('inf'), 50.0)], self.PREPARED)
        assert TransactiveSignal.conditions_have_changed(prepared, sent, ti, threshold=0.05) is True

    def test_a_moved_balance_point_is_a_change(self, market_and_interval):
        tn, market, ti = market_and_interval
        sent, prepared = self.signals(tn, market, ti, self.SENT, self.PREPARED, balance=((50.0, 0.03954), (54.0, 0.03954)))
        assert TransactiveSignal.conditions_have_changed(prepared, sent, ti, threshold=0.02) is True

    def test_the_distance_to_a_curve_takes_the_sharper_projection(self, market_and_interval):
        tn, market, ti = market_and_interval
        sent, _ = self.signals(tn, market, ti, self.SENT, self.PREPARED)
        curve = sent.get_by_interval(ti)
        on_the_line = Vertex(price=0.039535, quantity=30.0)  # between the first two vertices of the near-flat line
        assert TransactiveSignal._distance_to_curve(on_the_line, curve) < 1e-3, 'at the quantity the price is sharp'
        off_the_line = Vertex(price=0.045, quantity=30.0)
        assert TransactiveSignal._distance_to_curve(off_the_line, curve) > 0.1
