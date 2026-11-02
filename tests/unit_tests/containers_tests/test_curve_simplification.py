"""Curve simplification before a signal is sent (T-59): tent.containers.curve_simplification and SDCurve.simplified."""
import math
from datetime import datetime, timedelta, timezone

import pytest

from tent.containers import TimeInterval, Vertex
from tent.containers.sd_curve import SDCurve
from tent.containers.curve_simplification import SEED_TOLERANCE, simplify_vertices

INF = math.inf
HOURS = 1.0


class DummyMarket:
    def __init__(self):
        self.name = 'M'
        self.id = 'M_id'
        self.market_series_name = 'MSeries'
        self.data_manager = None
        self.time_intervals = []


MARKET = DummyMarket()
INTERVAL = TimeInterval(MARKET, timedelta(hours=HOURS), datetime(2026, 7, 1, tzinfo=timezone.utc))


def interval():
    return INTERVAL  # a curve holds its interval weakly, so the tests hold the one interval for them


def with_costs(points):
    """Vertices at (price, quantity) pairs with costs that are the integral of the marginal price from the first."""
    vertices = [Vertex(points[0][0], 0.0, points[0][1])]
    for (p0, q0), (p1, q1) in zip(points, points[1:]):
        dq = q1 - q0
        vertices.append(Vertex(p1, vertices[-1].cost + (p0 * dq + 0.5 * (p1 - p0) * dq) * HOURS, q1))
    return vertices


def curve(points):
    return SDCurve(None, interval(), with_costs(points), archive=False)


def straight_line(n=12):
    return [(0.02 + 0.01 * i, -1000.0 + 50.0 * i) for i in range(n)]


def test_collinear_interior_vertices_collapse_to_the_two_ends():
    original = curve(straight_line())
    simplified, applied = original.simplified(0.01)
    assert len(simplified) == 2 and applied == 0.01
    assert [(v.price, v.quantity) for v in simplified.vertices] == [(0.02, -1000.0), (0.13, -450.0)]
    assert simplified.vertices[0].cost == original.vertices[0].cost, 'the first vertex is kept entirely'
    assert simplified.vertices[-1].cost == pytest.approx(original.vertices[-1].cost), \
        'the cost at the far end is the same integral along a straight line'
    assert simplified.interval is original.interval and len(original) == 12, 'a new curve; the original is untouched'


def test_a_kinked_curve_keeps_its_kink():
    points = [(0.02 + 0.01 * i, -1000.0 + 10.0 * i) for i in range(6)]            # shallow
    points += [(0.08 + 0.01 * i, -950.0 + 150.0 * (i + 1)) for i in range(6)]      # steep after the kink at 0.07
    original = curve(points)
    simplified, _ = original.simplified(0.01)
    assert [(v.price, v.quantity) for v in simplified.vertices] == [(0.02, -1000.0), (0.07, -950.0), (0.13, -50.0)]
    # The kept vertices' costs are the integral along the simplified polyline, and agree with the original where the
    # polyline is unchanged (the kink is exact).
    assert simplified.vertices[1].cost == pytest.approx(original.vertices[5].cost)
    assert simplified.vertices[2].cost == pytest.approx(original.vertices[-1].cost)


def test_tolerance_zero_changes_nothing():
    points = [(0.02 + 0.01 * i, -1000.0 + 50.0 * i + (7.0 if i % 2 else 0.0)) for i in range(12)]
    original = curve(points)
    simplified, applied = original.simplified(0.0)
    assert applied == 0.0
    assert [(v.price, v.quantity, v.cost) for v in simplified.vertices] == \
        [(v.price, v.quantity, v.cost) for v in original.vertices]
    assert all(a is not b for a, b in zip(simplified.vertices, original.vertices)), 'copies, not the same objects'


def test_the_cap_halves_a_curve_by_doubling_the_tolerance():
    points = [(0.02 + 0.01 * i, -1000.0 + 50.0 * i + 30.0 * math.sin(i)) for i in range(12)]
    original = curve(points)
    capped, applied = original.simplified(0.001, max_vertices=6)
    assert len(capped) <= 6 and applied > 0.001 and applied / 0.001 == 2 ** round(math.log2(applied / 0.001)), \
        'the tolerance was doubled from the configured one'
    uncapped, _ = original.simplified(applied)
    assert len(uncapped) == len(capped), 'the cap applies the doubled tolerance and nothing else'
    assert len(original.simplified(0.001)[0]) > 6, 'the configured tolerance alone would not fit'
    seeded, applied = original.simplified(0.0, max_vertices=6)
    assert len(seeded) <= 6 and applied / SEED_TOLERANCE == 2 ** round(math.log2(applied / SEED_TOLERANCE)), \
        'with tolerance 0 the doubling starts from the seed'
    ends_only, _ = original.simplified(0.0, max_vertices=2)
    assert [(v.price, v.quantity) for v in ends_only.vertices] == [points[0], points[-1]]


def test_the_power_read_at_every_original_price_stays_within_tolerance_times_the_span():
    points = [(0.02 + 0.01 * i, -1000.0 + 50.0 * i + 30.0 * math.sin(i) + 20.0 * math.cos(3 * i)) for i in range(24)]
    original = curve(points)
    for tolerance in (0.005, 0.02, 0.1):
        simplified, _ = original.simplified(tolerance)
        quantity_span = max(q for _, q in points) - min(q for _, q in points)
        price_span = points[-1][0] - points[0][0]
        for price, quantity in points:
            assert abs(simplified.power_from_vertices(price) - original.power_from_vertices(price)) \
                <= tolerance * quantity_span + 1e-9, (tolerance, price)
            assert abs(simplified.price_from_vertices(quantity) - original.price_from_vertices(quantity)) \
                <= tolerance * price_span + 1e-9, (tolerance, quantity)
        assert len(simplified) < len(original)


def test_costs_of_kept_vertices_are_the_integral_along_the_simplified_polyline():
    # A supply curve (positive quantities): prod_cost_from_vertices is 0 for consumption by design.
    points = [(0.02 + 0.01 * i, 100.0 + 50.0 * i + 30.0 * math.sin(i)) for i in range(12)]
    original = curve(points)
    simplified, _ = original.simplified(0.05)
    assert 2 < len(simplified) < 12
    kept = list(simplified.vertices)
    for lower, upper in zip(kept, kept[1:]):
        # prod_cost_from_vertices integrates lower.cost + lower.price * dq + 0.5 * dp * dq over the hours; a hair below
        # the upper quantity it must land on the upper vertex's cost.
        assert simplified.prod_cost_from_vertices(upper.quantity - 1e-9) == pytest.approx(upper.cost, abs=1e-6)
    # The area difference the removals cause is bounded by the tolerance.
    quantity_span, price_span = 550.0 + 2 * 30.0, 0.11
    assert abs(kept[-1].cost - original.vertices[-1].cost) <= 0.05 * quantity_span * price_span * HOURS * 2


def test_infinite_price_end_vertices_are_untouched():
    points = straight_line(6)
    vertices = with_costs(points) + [Vertex(INF, 123.0, -750.0)]
    original = SDCurve(None, interval(), vertices, archive=False)
    simplified, _ = original.simplified(0.01)
    assert [(v.price, v.quantity) for v in simplified.vertices] == [points[0], points[-1], (INF, -750.0)]
    # The infinite vertex keeps the cost step it had over its original neighbouring segment.
    assert simplified.vertices[-1].cost == pytest.approx(simplified.vertices[-2].cost + (123.0 - vertices[-2].cost))
    assert original.simplified(0.0, max_vertices=2)[0].vertices[-1].price == INF, 'a cap never removes it'


def test_curves_of_two_or_fewer_vertices_and_lone_vertices_pass_through():
    for points in ([(0.05, -100.0)], [(0.05, -100.0), (0.07, -50.0)]):
        original = curve(points)
        simplified, _ = original.simplified(0.5, max_vertices=2)
        assert [(v.price, v.quantity, v.cost) for v in simplified.vertices] == \
            [(v.price, v.quantity, v.cost) for v in original.vertices]


def test_a_curve_without_an_interval_cannot_recompute_costs():
    original = SDCurve(None, None, with_costs(straight_line(5)), archive=False)
    with pytest.raises(ValueError, match='has no interval'):
        original.simplified(0.01)
    assert len(SDCurve(None, None, with_costs(straight_line(2)), archive=False).simplified(0.01)[0]) == 2


def test_bad_arguments_are_errors():
    with pytest.raises(ValueError, match='must not be negative'):
        simplify_vertices(with_costs(straight_line(3)), -0.1, HOURS)
    with pytest.raises(ValueError, match='max_vertices must be at least 1'):
        simplify_vertices(with_costs(straight_line(3)), 0.1, HOURS, max_vertices=0)
