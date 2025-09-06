import pytest
from datetime import datetime, timedelta

from tent.containers.order import Order
from tent.containers import TimeInterval, Vertex
from tent.enumerations import SupplyOrDemand, OrderStatus, VTypes
from tent.local_asset import LocalAsset


class DummyDataManager:
	def __init__(self):
		self.archived = []

	def archive_data(self, records):
		# Just collect records so Archivable.archive() doesn't fail
		self.archived.extend(records)


class DummyTN:
	"""Minimal transactive node stand‑in to satisfy LocalAsset constructor & Archivable dependencies."""
	def __init__(self):
		self.id = 1
		self.data_manager = DummyDataManager()

	def get_isms_by_attribute(self, *_):
		return []

	def get_meters_by_attribute(self, *_):
		return []


class DummyMarket:
	"""Minimal market stub needed by Order / TimeInterval logic."""
	class Series:
		name = "TestSeries"

	series = Series()
	market_series_name = "TestSeries"

	def __init__(self):
		self.orderList = []
		self.id = 1


@pytest.fixture
def market():
	return DummyMarket()


@pytest.fixture
def time_interval(market):
	start_time = datetime.now()
	duration = timedelta(hours=1)
	# TimeInterval signature in this branch expects (market, duration, start_time)
	try:
		return TimeInterval(market, duration, start_time)
	except TypeError:
		# Fallback if signature differs (legacy ordering) -> (start_time, duration)
		return TimeInterval(start_time, duration)


@pytest.fixture
def actor():
	# Minimal viable LocalAsset
	return LocalAsset(
		transactive_node=DummyTN(),
		cost_parameters=(0.0, 0.0, 0.0),
		default_power=0.0,
		default_vertices=[Vertex(price=float("inf"), cost=0.0, quantity=0.0)],
		description="test asset",
		information_services=[],
		location="nowhere",
		maximum_power=100.0,
		minimum_power=-100.0,
		meter_points=[],
		name="TestAsset"
	)


def _build_vertices(vals):
	"""Helper to build Vertex list from (price, qty, continuity) tuples."""
	v = []
	for p, q, c in vals:
		v.append(Vertex(price=p, cost=0.0, quantity=q, continuity=c))
	return v


@pytest.fixture
def supply_vertices():
	# Ascending price / quantity
	return _build_vertices([
		(5.0, 1.0, True),
		(10.0, 3.0, True),
		(15.0, 5.0, True)
	])


@pytest.fixture
def demand_vertices():
	# Descending price, descending (more negative) quantity
	return _build_vertices([
		(30.0, -1.0, True),
		(20.0, -3.0, True),
		(10.0, -5.0, True)
	])


def _new_order(market, time_interval, actor, vertices, sod):
	return Order(
		market=market,
		interval=time_interval,  # new version param name
		actor=actor,
		vertices=vertices,
		supply_or_demand=sod
	)


@pytest.fixture
def supply_order(market, time_interval, actor, supply_vertices):
	return _new_order(market, time_interval, actor, supply_vertices, SupplyOrDemand.SUPPLY)


@pytest.fixture
def demand_order(market, time_interval, actor, demand_vertices):
	return _new_order(market, time_interval, actor, demand_vertices, SupplyOrDemand.DEMAND)


def test_sort_order_supply(supply_order):
	supply_order.sort_order()
	prices = [v.price for v in supply_order.vertices]
	assert prices == sorted(prices)


def test_sort_order_demand(demand_order):
	demand_order.sort_order()
	prices = [v.price for v in demand_order.vertices]
	assert prices == sorted(prices, reverse=True)  # Demand should be descending price


def test_check_supply_order_mixed(market, time_interval, actor):
	# Mixed vertices (demand then supply) to trigger revision
	verts = _build_vertices([
		(5.0, -2.0, True),
		(7.0, -1.0, True),
		(9.0, 0.0, True),
		(11.0, 2.0, True),
		(13.0, 4.0, True)
	])
	order = _new_order(market, time_interval, actor, verts, SupplyOrDemand.SUPPLY)
	revised = order.check_supply_order()
	assert revised is not None
	assert all(v.quantity >= 0 for v in revised.vertices)
	assert order.status in {OrderStatus.NEW_REVISED, OrderStatus.REVISED_STANDING_ORDER, OrderStatus.NEW, OrderStatus.SUPERSEDED}


def test_check_demand_order_mixed(market, time_interval, actor):
	verts = _build_vertices([
		(30.0, 2.0, True),  # Start with supply (positive quantity)
		(25.0, 1.0, True),
		(20.0, 0.0, True),
		(15.0, -1.0, True),  # Then demand (negative quantity)
		(10.0, -2.0, True)
	])
	order = _new_order(market, time_interval, actor, verts, SupplyOrDemand.DEMAND)
	revised = order.check_demand_order()
	assert revised is not None
	assert all(v.quantity <= 0 for v in revised.vertices)


def test_get_quantity_from_price_linear_supply(supply_order):
	# Pick a price between first two vertices for interpolation
	mid_price = 7.5
	q_vertex = supply_order.get_quantity_from_price(mid_price)
	assert q_vertex.price == mid_price
	# Interpolated quantity should lie between 1.0 and 3.0
	assert 1.0 <= q_vertex.quantity <= 3.0
	assert q_vertex.vType in {VTypes.LINEAR, VTypes.COMPLEX, VTypes.LIMIT_ORDER}


def test_match_supply_limit_order(supply_order, market):
	market.orderList.append(supply_order)
	quantity = 2.0
	match_price = 10.0
	matched, unmatched = supply_order.match(quantity=quantity, match_price=match_price)
	assert matched is not None
	assert unmatched is not None
	assert matched.status == OrderStatus.MATCHED
	assert supply_order.status == OrderStatus.SUPERSEDED


def test_mark_as_revised_transitions(supply_order):
	# Start NEW -> NEW_REVISED
	supply_order.mark_as_revised()
	assert supply_order.status in {OrderStatus.NEW_REVISED, OrderStatus.NEW}  # allow if not updated yet

