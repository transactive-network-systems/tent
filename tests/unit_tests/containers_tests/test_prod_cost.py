"""Production cost from a curve is the integral of its linear marginal price between vertices."""
from datetime import timedelta
from unittest.mock import Mock

import pytest

from tent.containers.sd_curve import SDCurve
from tent.containers.vertex import Vertex


def curve(duration_hours=1.0):
    """The curve holds only a weak reference to its interval, so the interval is returned alongside it."""
    interval = Mock(); interval.duration = timedelta(hours=duration_hours)
    # Marginal price 0.04 $/kWh at 0 kW rising to 0.08 at 1000 kW; cumulative cost at 1000 kW is the full integral.
    return SDCurve(None, interval, vertices=[Vertex(price=0.04, cost=0.0, quantity=0.0),
                                             Vertex(price=0.08, cost=60.0, quantity=1000.0)], archive=False), interval


def test_interpolated_cost_integrates_the_marginal_price():
    c, _interval = curve()
    # integral from 0 to 500 of (0.04 + 0.00004 x) dx = 20 + 0.5 * 0.00004 * 500^2 = 25
    assert c.prod_cost_from_vertices(500.0) == pytest.approx(25.0)
    assert c.prod_cost_from_vertices(1000.0) == pytest.approx(60.0), 'at a vertex, its stored cumulative cost'
    assert c.prod_cost_from_vertices(0.0) == pytest.approx(0.0)


def test_cost_scales_with_interval_length():
    c, _interval = curve(duration_hours=0.25)
    assert c.prod_cost_from_vertices(500.0) == pytest.approx(25.0 / 4)


def test_consumption_has_no_production_cost():
    c, _interval = curve()
    assert c.prod_cost_from_vertices(-10.0) == 0.0
