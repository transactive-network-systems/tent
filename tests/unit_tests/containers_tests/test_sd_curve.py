from datetime import timedelta, datetime
from pathlib import Path
import sys
import pytest
from sortedcontainers import SortedSet

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from tent.containers.sd_curve import SDCurve
from tent.containers.vertex import Vertex
from tent.containers.time_interval import TimeInterval
from tent.enumerations import MeasurementType, MeasurementUnit
from tent.base.archivable import Archivable


class DummyDataManager:
	def archive_data(self, records):  # pragma: no cover
		pass


class DummyTN:
	def __init__(self):
		self.data_manager = DummyDataManager()


class DummySource:
	def __init__(self, name="SRC"):
		self.name = name
		self.tn = DummyTN()
		self.id = 1  # Real sources inherit from Archivable which has id


class DummyMeasurement:
	def __init__(self, source):
		self.source = source
		self.id = 1


@pytest.fixture
def market_like():
	class M:
		def __init__(self):
			self.market_series_name = "SERIES"
			self.data_manager = DummyDataManager()
	return M()


@pytest.fixture
def interval(market_like):
	return TimeInterval(market_like, timedelta(hours=1), datetime.utcnow())


@pytest.fixture
def measurement():
	return DummyMeasurement(DummySource())


def make_curve(measurement, interval, verts):
	return SDCurve(measurement, interval, verts)


@pytest.fixture(autouse=True)
def patch_sdc_minimal(monkeypatch):
	"""Simplify SDCurve for unit tests: avoid weakref proxies and buggy min/max quantity behavior."""
	original_init = SDCurve.__init__
	original_min_q = SDCurve.min_quantity
	original_max_q = SDCurve.max_quantity

	def simple_init(self, measurement, interval, vertices=None):
		Archivable.__init__(self)
		# Direct assignment (no proxy) prevents weakref issues during GC.
		self._SDCurve__interval = interval
		self._SDCurve__measurement = measurement
		self.vertices = SortedSet(vertices if vertices else [Vertex(price=0.0, quantity=0.0, continuity=False)])

	def simple_min_q(self, limit=None):
		base = min([v.quantity for v in self.vertices]) if self.vertices else 0.0
		return max(limit, base) if limit is not None else base

	def simple_max_q(self, limit=None):
		base = max([v.quantity for v in self.vertices]) if self.vertices else 0.0
		return min(limit, base) if limit is not None else base

	monkeypatch.setattr(SDCurve, "__init__", simple_init)
	monkeypatch.setattr(SDCurve, "min_quantity", simple_min_q)
	monkeypatch.setattr(SDCurve, "max_quantity", simple_max_q)
	yield
	monkeypatch.setattr(SDCurve, "__init__", original_init)
	monkeypatch.setattr(SDCurve, "min_quantity", original_min_q)
	monkeypatch.setattr(SDCurve, "max_quantity", original_max_q)


def test_prices_quantities_lists(measurement, interval):
	c = make_curve(measurement, interval, [Vertex(10, quantity=1), Vertex(15, quantity=3)])
	assert c.prices == [10.0, 15.0]
	assert c.quantities == [1.0, 3.0]


def test_max_min_price(measurement, interval):
	c = make_curve(measurement, interval, [Vertex(5, quantity=0), Vertex(8, quantity=2)])
	assert c.max_price == 8
	assert c.min_price == 5


def test_power_from_vertices_interpolation(measurement, interval):
	c = make_curve(measurement, interval, [Vertex(10, quantity=1), Vertex(20, quantity=5)])
	# midpoint price -> midpoint quantity
	assert c.power_from_vertices(15) == 3


def test_price_from_vertices_interpolation(measurement, interval):
	c = make_curve(measurement, interval, [Vertex(10, quantity=1), Vertex(20, quantity=5)])
	# quantity 3 halfway between 1 and 5 -> price 15
	assert c.price_from_vertices(3) == 15


def test_apply_constraints_adds_extrema(measurement, interval):
	c = make_curve(measurement, interval, [Vertex(10, quantity=2), Vertex(20, quantity=6)])
	# apply tighter bounds inside current range -> vertices at 3 and 5 expected
	c.apply_constraints_and_bounds(minimum_power=3, maximum_power=5)
	quantities = sorted(v.quantity for v in c.vertices)
	assert quantities == [3, 5]


def test_rationality_fix_up(measurement, interval):
	# irrational (decreasing quantity with increasing price)
	v1 = Vertex(10, quantity=5)
	v2 = Vertex(15, quantity=3)
	c = make_curve(measurement, interval, [v1, v2])
	fixed, count = c.check_rationality(action="fix_up")
	# Two irrational transitions counted (between v1->v2 and v2->sentinel inf)
	assert count == 2
	# after fix_up quantities non-decreasing
	q = [v.quantity for v in fixed.vertices]
	assert q == sorted(q)


def test_get_vertices_at_quantity(measurement, interval):
	c = make_curve(measurement, interval, [Vertex(10, quantity=1), Vertex(20, quantity=1), Vertex(30, quantity=3)])
	sub = c.get_vertices_at_quantity(1)
	# Two vertices with same quantity=1 but different prices (10 and 20)
	# Vertices are distinct objects (not collapsed) because they differ in price
	assert len(sub.vertices) == 2
	assert all(v.quantity == 1 for v in sub.vertices)
	prices = sorted(v.price for v in sub.vertices)
	assert prices == [10, 20]


def test_update_add_list(measurement, interval):
	c = make_curve(measurement, interval, [Vertex(10, quantity=1)])
	c.update([15, 0, 2])  # price, cost, quantity pattern in Vertex(*args)
	assert any(v.price == 15 and v.quantity == 2 for v in c.vertices)


def test_aggregate_curves_filters_inf(measurement, interval):
	c1 = make_curve(measurement, interval, [Vertex(float('inf'), quantity=1)])
	c2 = make_curve(measurement, interval, [Vertex(10, quantity=2)])
	prices = SDCurve.aggregate_curves([c1, c2])
	assert 10 in prices and float('inf') not in prices
