"""SDCurve.price_from_vertices at and just beyond the ends of a curve."""
import pytest

from tent.containers.sd_curve import SDCurve
from tent.containers.vertex import Vertex


def curve():
    return SDCurve(measurement=None, interval=None, vertices=[Vertex(0.04, 0.0, -100.0), Vertex(0.08, 0.0, -20.0)],
                   archive=False)


def test_the_end_vertex_itself_and_a_hair_beyond_it_price_silently(caplog):
    c = curve()
    with caplog.at_level('WARNING', logger='tent.containers.sd_curve'):
        assert c.price_from_vertices(-20.0) == 0.08, 'exactly the last vertex'
        assert c.price_from_vertices(-20.0 + 0.019) == 0.08, 'within 0.1 percent beyond the last vertex'
        assert c.price_from_vertices(-100.0 - 0.09) == 0.04, 'within 0.1 percent below the first vertex'
    assert not caplog.records


def test_beyond_the_tolerance_the_price_is_indeterminate_and_warns(caplog):
    c = curve()
    with caplog.at_level('WARNING', logger='tent.containers.sd_curve'):
        assert c.price_from_vertices(-19.0) == 0.08
        assert c.price_from_vertices(-101.0) == 0.04
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2 and all('Indeterminate price' in m for m in messages)
    assert 'greater than that of last vertex' in messages[0] and 'lower than that of first vertex' in messages[1]


def test_interior_quantities_interpolate_as_before():
    assert curve().price_from_vertices(-60.0) == pytest.approx(0.06)
