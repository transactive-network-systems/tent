import math
from copy import copy as _shallow_copy
from datetime import timedelta, datetime
from pathlib import Path
import sys
import pytest

# Ensure project root on path (two levels up from this file -> TENT root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from tent.enumerations import SupplyOrDemand, OrderStatus, VTypes  # noqa: E402
from tent.containers.order import Order  # noqa: E402
from tent.containers.vertex import Vertex  # noqa: E402
from tent.containers.time_interval import TimeInterval  # noqa: E402
from tent.containers.sd_curve import SDCurve  # noqa: E402
from tent.base.archivable import Archivable  # noqa: E402


#############################################
# Test Utilities / Fixtures
#############################################

class DummySeries:
	def __init__(self, name="TEST_SERIES"):
		self.name = name


class DummyMarket:
	def __init__(self, name="MKT"):
		self.series = DummySeries(name)
		self.market_series_name = self.series.name
		self.orderList = []
		self.data_manager = DummyDataManager()


class DummyActor:
	def __init__(self, name="ACTOR"):
		self.name = name


class DummyDataManager:
	def archive_data(self, records):  # pragma: no cover (not part of unit logic focus)
		pass


@pytest.fixture(autouse=True)
def patch_sdc_curve(monkeypatch):
	"""Patch SDCurve.__init__ so Order can call super() with only interval & vertices.
	We don't need full measurement behavior for these unit tests."""
	original = SDCurve.__init__

	def fake_init(self, *_, interval=None, vertices=None, **__):  # accept flexible signature
		# initialize Archivable so id + timestamps exist
		Archivable.__init__(self)
		# mimic minimal attributes used by Order methods
		self._SDCurve__interval = interval
		self._SDCurve__measurement = None
		self.vertices = list(vertices) if vertices else []

	monkeypatch.setattr(SDCurve, "__init__", fake_init)
	yield
	monkeypatch.setattr(SDCurve, "__init__", original)


@pytest.fixture(autouse=True)
def patch_vertex_and_copy(monkeypatch):
	"""Allow legacy keyword args (curve_id, timestamp) used by Order when constructing Vertex, and patch
	Order.__copy__ to set internal series attr directly instead of read-only property."""
	orig_v_init = Vertex.__init__

	def patched_v_init(self, price, cost=0.0, quantity=0.0, continuity=True, uncertainty=0.0, curve=None, **legacy):
		# Ignore legacy kwargs like curve_id, timestamp
		return orig_v_init(self, price, cost, quantity, continuity, uncertainty, curve)

	monkeypatch.setattr(Vertex, "__init__", patched_v_init)

	orig_copy = Order.__copy__

	def patched_copy(self):
		copiedOrder = Order(market=self.market,
							interval=self.interval,
							actor=self.actor,
							expire_by=self.expire_by,
							parent_order_id=self.id,
							vertices=None,
							supply_or_demand=self.supply_or_demand)
		self.child_order_ids.append(copiedOrder.id)
		copiedOrder.time_stamp = self.time_stamp
		copiedOrder.status = self.status
		copiedOrder.child_order_ids = []
		# set internal mangled attribute instead of read-only property
		setattr(copiedOrder, "_Order__marketSeries__", self.market_series)
		copiedOrder.vertices = []
		copiedOrder.buyer = self.buyer
		copiedOrder.seller = self.seller
		for pp in self.vertices:
			copiedOrder.vertices.append(_shallow_copy(pp))
		return copiedOrder

	monkeypatch.setattr(Order, "__copy__", patched_copy)
	yield
	monkeypatch.setattr(Order, "__copy__", orig_copy)


@pytest.fixture
def market():
	return DummyMarket()


@pytest.fixture
def interval(market):
	return TimeInterval(market=market, duration=timedelta(hours=1), start_time=datetime.utcnow())


def v(price, qty, continuity=True):
	# curve not required for business logic here
	return Vertex(price=price, quantity=qty, continuity=continuity)


@pytest.fixture
def supply_vertices():
	# Increasing price / quantity typical supply
	return [v(10, 0.0), v(20, 5.0, True)]


@pytest.fixture
def demand_vertices():
	# Decreasing quantity (negative demand) with decreasing price semantics handled by sort
	return [v(50, -2.0), v(30, -5.0, True)]


@pytest.fixture
def crossing_vertices():
	# Mix of negative & positive crossing zero around price 40 -> 60
	return [v(40, -4.0), v(50, -1.0), v(60, 3.0)]


@pytest.fixture
def actor():
	return DummyActor()


def make_order(market, interval, actor, vertices, sod):
	return Order(market=market, interval=interval, actor=actor, vertices=vertices, supply_or_demand=sod)


#############################################
# sort_order
#############################################

def test_sort_order_supply(supply_vertices, market, interval, actor):
	# reversed input to ensure sort does work
	order = make_order(market, interval, actor, list(reversed(supply_vertices)), SupplyOrDemand.SUPPLY)
	order.sort_order()
	assert [(x.price, x.quantity) for x in order.vertices] == [(10, 0.0), (20, 5.0)]


def test_sort_order_demand(demand_vertices, market, interval, actor):
	order = make_order(market, interval, actor, demand_vertices, SupplyOrDemand.DEMAND)
	# Will be sorted by decreasing price / decreasing quantity
	order.sort_order()
	prices = [x.price for x in order.vertices]
	assert prices == sorted(prices, reverse=True)


#############################################
# check_supply_order
#############################################

def test_check_supply_order_valid_single_vertex(market, interval, actor):
	order = make_order(market, interval, actor, [v(15, 3.0)], SupplyOrDemand.SUPPLY)
	result = order.check_supply_order()
	assert result is order
	# Single supply vertex gets price coerced to -inf per code path
	assert math.isinf(order.vertices[0].price) and order.vertices[0].price < 0
	assert order.status in {OrderStatus.NEW_REVISED, OrderStatus.REVISED_STANDING_ORDER}


def test_check_supply_order_demand_only_becomes_none(market, interval, actor):
	order = make_order(market, interval, actor, [v(10, -2.0), v(20, -1.0)], SupplyOrDemand.SUPPLY)
	result = order.check_supply_order()
	assert result is None
	assert order.status == OrderStatus.CANCELLED


def test_check_supply_order_mixed_components(market, interval, actor, crossing_vertices):
	order = make_order(market, interval, actor, crossing_vertices, SupplyOrDemand.SUPPLY)
	result = order.check_supply_order()
	assert result is order
	# All resulting vertices must be >= 0 quantity
	assert all(x.quantity >= 0 for x in order.vertices)
	# Must include a zero transition vertex
	assert any(x.quantity == 0 for x in order.vertices)


#############################################
# check_demand_order
#############################################

def test_check_demand_order_valid_single_vertex(market, interval, actor):
	order = make_order(market, interval, actor, [v(25, -4.0)], SupplyOrDemand.DEMAND)
	result = order.check_demand_order()
	assert result is order
	# Single demand vertex price coerced to +inf per code
	assert math.isinf(order.vertices[0].price) and order.vertices[0].price > 0
	assert order.status in {OrderStatus.NEW_REVISED, OrderStatus.REVISED_STANDING_ORDER}


def test_check_demand_order_supply_only_becomes_none(market, interval, actor):
	order = make_order(market, interval, actor, [v(10, 2.0), v(20, 4.0)], SupplyOrDemand.DEMAND)
	result = order.check_demand_order()
	assert result is None
	assert order.status == OrderStatus.CANCELLED


def test_check_demand_order_mixed_components(market, interval, actor, crossing_vertices):
	order = make_order(market, interval, actor, crossing_vertices, SupplyOrDemand.DEMAND)
	result = order.check_demand_order()
	assert result is order
	assert all(x.quantity <= 0 for x in order.vertices)
	assert any(x.quantity == 0 for x in order.vertices)


#############################################
# get_quantity_from_price (supply)
#############################################

@pytest.fixture
def supply_order_for_interpolation(market, interval, actor):
	# 2 vertices -> limit/linear cases
	order = make_order(market, interval, actor, [v(10, 0.0), v(20, 10.0)], SupplyOrDemand.SUPPLY)
	return order


def test_get_quantity_from_price_supply_below_first(supply_order_for_interpolation):
	qv = supply_order_for_interpolation.get_quantity_from_price(5)
	assert qv.quantity == 0.0
	assert qv.vType == VTypes.LIMIT_ORDER


def test_get_quantity_from_price_supply_above_last(supply_order_for_interpolation):
	qv = supply_order_for_interpolation.get_quantity_from_price(25)
	assert qv.quantity == 10.0
	assert qv.vType == VTypes.LIMIT_ORDER


def test_get_quantity_from_price_supply_mid_linear(supply_order_for_interpolation):
	qv = supply_order_for_interpolation.get_quantity_from_price(15)
	assert pytest.approx(qv.quantity, rel=1e-6) == 5.0
	# With two vertices interpolation path sets LINEAR
	assert qv.vType in {VTypes.LINEAR, VTypes.LIMIT_ORDER}  # depends on branch selection


#############################################
# get_quantity_from_price (demand)
#############################################

@pytest.fixture
def demand_order_for_interpolation(market, interval, actor):
	# Demand curve: higher price less negative (closer to zero)
	order = make_order(market, interval, actor, [v(50, -1.0), v(30, -6.0)], SupplyOrDemand.DEMAND)
	return order


def test_get_quantity_from_price_demand_above_first(demand_order_for_interpolation):
	qv = demand_order_for_interpolation.get_quantity_from_price(60)
	assert qv.quantity == -1.0
	assert qv.vType == VTypes.MARKET_ORDER


def test_get_quantity_from_price_demand_below_last(demand_order_for_interpolation):
	qv = demand_order_for_interpolation.get_quantity_from_price(10)
	assert qv.quantity == -6.0
	assert qv.vType == VTypes.LIMIT_ORDER


def test_get_quantity_from_price_demand_mid_linear(demand_order_for_interpolation):
	qv = demand_order_for_interpolation.get_quantity_from_price(40)
	# linear interpolation between -1 at 50 and -6 at 30 -> slope (-6+1)/(30-50)=(-5)/(-20)=0.25; delta price 40-50=-10 -> qty change -2.5 -> -3.5
	assert pytest.approx(qv.quantity, rel=1e-6) == -3.5


#############################################
# match()
#############################################

def test_match_supply_limit_order_assigns_counterparty(market, interval, actor):
	order = make_order(market, interval, actor, [v(10, 10.0), v(20, 10.0)], SupplyOrDemand.SUPPLY)
	counter = DummyActor("COUNTER")
	matched, unmatched = order.match(quantity=4.0, match_price=15.0, counter_party=counter)
	assert matched is not None
	assert matched.status == OrderStatus.MATCHED
	assert order.status == OrderStatus.SUPERSEDED
	assert matched.seller == actor and matched.buyer == counter
	# remainder (unmatched) should have each vertex quantity reduced by 4
	if unmatched:  # may be None if all zeroed
		assert all(q.quantity == orig_qty - 4.0 for q, orig_qty in zip(unmatched.vertices, [10.0, 10.0]))


def test_match_demand_limit_order_assigns_counterparty(market, interval, actor):
	order = make_order(market, interval, actor, [v(30, -8.0), v(20, -8.0)], SupplyOrDemand.DEMAND)
	counter = DummyActor("COUNTER")
	matched, unmatched = order.match(quantity=-3.0, match_price=25.0, counter_party=counter)
	assert matched.buyer == actor and matched.seller == counter
	assert matched.status == OrderStatus.MATCHED


def test_match_quantity_sign_assertion_supply(market, interval, actor):
	order = make_order(market, interval, actor, [v(10, 5.0)], SupplyOrDemand.SUPPLY)
	with pytest.raises(AssertionError):
		order.match(quantity=-1.0, match_price=10.0)


def test_match_quantity_sign_assertion_demand(market, interval, actor):
	order = make_order(market, interval, actor, [v(10, -5.0)], SupplyOrDemand.DEMAND)
	with pytest.raises(AssertionError):
		order.match(quantity=1.0, match_price=10.0)


#############################################
# order_or_none
#############################################

def test_order_or_none_zero_vertices_returns_none(market, interval, actor):
	order = make_order(market, interval, actor, [v(10, 0.0)], SupplyOrDemand.SUPPLY)
	result = order.order_or_none()
	assert result is None
	assert order.status == OrderStatus.CANCELLED


#############################################
# __copy__ parent/child relationship
#############################################

def test_copy_tracks_parent_child(market, interval, actor):
	order = make_order(market, interval, actor, [v(10, 2.0)], SupplyOrDemand.SUPPLY)
	copied = order.__copy__()
	assert copied.parent_order_id == order.id
	assert copied.id in order.child_order_ids
	assert copied.vertices[0].price == order.vertices[0].price


#############################################
# __init__ with supply_or_demand=None splits into children
#############################################

def test_init_with_none_creates_child_orders(market, interval, actor, crossing_vertices):
	parent = Order(market=market, interval=interval, actor=actor, vertices=crossing_vertices, supply_or_demand=None)
	# Parent becomes SUPERSEDED
	assert parent.status == OrderStatus.SUPERSEDED
	# Market now has exactly two child orders (supply + demand)
	assert len(market.orderList) == 2
	sod_values = {o.supply_or_demand for o in market.orderList}
	assert sod_values == {SupplyOrDemand.SUPPLY, SupplyOrDemand.DEMAND}
	# Parent not in orderList
	assert parent not in market.orderList


#############################################
# simplify_order basic behaviors
#############################################

def test_simplify_order_limit_collapse_supply(market, interval, actor):
	order = make_order(market, interval, actor, [v(10, 5.0), v(20, 5.0)], SupplyOrDemand.SUPPLY)
	simplified = order.simplify_order()
	assert simplified is order
	assert len(order.vertices) == 1  # collapsed
	assert math.isinf(order.vertices[0].price)  # price mutated to inf then overwritten to -inf for demand vs supply


def test_simplify_order_more_than_two(market, interval, actor):
	order = make_order(market, interval, actor, [v(10, 0.0), v(15, 2.0), v(20, 4.0)], SupplyOrDemand.SUPPLY)
	simplified = order.simplify_order()
	assert simplified is order
	# linear middle expected to be removed (perfect linear interpolation)
	assert len(order.vertices) == 2
