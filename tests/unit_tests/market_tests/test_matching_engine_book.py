"""
Order-book tests for MatchingEngine, ported from the legacy tests/integration_tests/test_matching_engine_new.py (2023).

The legacy module stubbed engine methods onto a bare Market and patched Order and Vertex back to an older signature; its
test classes were never collected (lowercase names, hand-rolled setUp/case methods) and most match cases were commented
out as "needs investigation". Here the engine is built through MarketSeries like every other market and orders use the
current Order/Vertex API. The standing-order book is the legacy one: demand and supply, market, limit and piece-wise
linear orders, continuous and discontinuous, at two magnitudes and two ages (48 orders). Expected results are derived
from the matching algorithm and checked as exact multisets rather than the legacy "in [25, 50, 100]" lists.
"""
from collections import Counter
from copy import copy
from datetime import timedelta
from unittest.mock import patch

import pytest
from uuid6 import uuid7

from tent.containers.order import Order
from tent.containers.vertex import Vertex
from tent.enumerations import MarketState, OrderStatus, SupplyOrDemand, VTypes
from tent.local_asset import LocalAsset
from tent.market.market_series import MarketSeries
from tent.market.matching_engine import MatchingEngine, MatchingStrategy, Quote, Role
from tent.neighbor import Neighbor
from tent.utils.timer import Timer
from tests.mock_tn import MockTN

INF = float('inf')
ONE_MINUTE = timedelta(minutes=1)


class RecordingDataManager:
    def __init__(self):
        self.records: list[dict] = []

    def archive_data(self, records):
        self.records.extend(records if isinstance(records, (list, tuple)) else [records])

    def by_table(self, table_name: str) -> list[dict]:
        return [r['data'] for r in self.records if r.get('table_name') == table_name]


@pytest.fixture
def engine():
    tn = MockTN()
    tn._data_manager = RecordingDataManager()
    series = MarketSeries(transactive_node=tn, name='Matching Series', market_class=MatchingEngine,
                          initial_market_state=MarketState.Negotiation, duration_negotiation=3600.0,
                          duration_delivery_lead=900.0, interval_duration=3600.0)
    tn.market_series = [series]
    tn.neighbors = [Neighbor(transactive_node=tn, name='Neighbor #1', cost_parameters=[0.0, 0.0, 0.0],
                             description='Neighbor actor example')]
    tn.local_assets = [LocalAsset(transactive_node=tn, name='LocalAsset #1', cost_parameters=[0.0, 0.0, 0.0],
                                  description='LocalAsset actor example')]
    return series.newest_market


@pytest.fixture
def neighbor(engine):
    return engine.tn.neighbors[0]


@pytest.fixture
def asset(engine):
    return engine.tn.local_assets[0]


# ---------------------------------------------------------------------------------------------------------------------
# Order factories. Demand quantities are negative, supply quantities positive.
# ---------------------------------------------------------------------------------------------------------------------

def _side(quantity):
    return SupplyOrDemand.SUPPLY if quantity > 0 else SupplyOrDemand.DEMAND


def _book(order, status, age):
    order.status = status
    order.time_stamp = Timer.now() - age
    return order


def market_order(mkt, actor, quantity, continuous=True, status=OrderStatus.STANDING_ORDER, age=timedelta(0)):
    """One vertex at an infinite price: take any price for the quantity. Continuous means partially matchable."""
    side = _side(quantity)
    price = -INF if side is SupplyOrDemand.SUPPLY else INF
    order = Order(mkt, mkt.time_intervals[0], actor, vertices=[Vertex(price=price, quantity=quantity,
                                                                      continuity=continuous)], supply_or_demand=side)
    return _book(order, status, age)


def limit_order(mkt, actor, price, quantity, continuous=True, status=OrderStatus.STANDING_ORDER, age=timedelta(0)):
    """Two vertices at one strike price: nothing below (demand: above) it, the full quantity at it."""
    order = Order(mkt, mkt.time_intervals[0], actor,
                  vertices=[Vertex(price=price, quantity=0.0, continuity=False),
                            Vertex(price=price, quantity=quantity, continuity=continuous)],
                  supply_or_demand=_side(quantity))
    return _book(order, status, age)


def pwl_order(mkt, actor, quantity, continuous=True, status=OrderStatus.STANDING_ORDER, age=timedelta(0)):
    """Piece-wise linear: supply ramps from 0 kW at 0.15 to full at 0.25; demand from 0 kW at 0.25 to full at 0.15."""
    supply = quantity > 0
    zero_price, full_price = (0.15, 0.25) if supply else (0.25, 0.15)
    order = Order(mkt, mkt.time_intervals[0], actor,
                  vertices=[Vertex(price=zero_price, quantity=0.0, continuity=False),
                            Vertex(price=full_price, quantity=quantity, continuity=continuous)],
                  supply_or_demand=_side(quantity))
    return _book(order, status, age)


def standing_book(mkt, actor) -> list[Order]:
    """The legacy 48-order book: {demand, supply} x {now, one minute old} x {100, 50 kW} x {market, limit, pwl}
    x {continuous, discontinuous}, all standing."""
    orders = []
    for sign in (-1.0, 1.0):
        for age in (timedelta(0), ONE_MINUTE):
            for magnitude in (100.0, 50.0):
                quantity = sign * magnitude
                for continuous in (True, False):
                    orders.append(market_order(mkt, actor, quantity, continuous, age=age))
                for continuous in (True, False):
                    orders.append(limit_order(mkt, actor, 0.1, quantity, continuous, age=age))
                for continuous in (True, False):
                    orders.append(pwl_order(mkt, actor, quantity, continuous, age=age))
    return orders


def quantities(orders) -> Counter:
    """Multiset of the matched (last-vertex) quantities of a list of matched orders."""
    return Counter(o.vertices[-1].quantity for o in orders)


def assert_balanced_matches(mkt, expected_legs: int, expected_magnitudes: Counter):
    """Matched legs come in pairs (standing side and aggressive side) that balance to zero."""
    assert len(mkt.matchList) == expected_legs
    assert all(m.status is OrderStatus.MATCHED for m in mkt.matchList)
    assert sum(v.quantity for m in mkt.matchList for v in m.vertices) == pytest.approx(0.0)
    assert Counter(abs(q) for q in quantities(mkt.matchList).elements()) == expected_magnitudes


# ---------------------------------------------------------------------------------------------------------------------
# match(): legacy test_match cases 1-16
# ---------------------------------------------------------------------------------------------------------------------

def test_match_without_new_order_changes_nothing(engine, neighbor):  # legacy case 1
    engine.orderList = standing_book(engine, neighbor)
    engine.match()
    assert len(engine.orderList) == 48 and engine.matchList == []


def test_match_into_empty_book_leaves_new_order_standing(engine, neighbor):  # legacy case 2
    new_order = market_order(engine, neighbor, -100.0, status=OrderStatus.NEW)
    engine.match(new_order)
    assert engine.orderList == [new_order] and new_order.status is OrderStatus.STANDING_ORDER
    assert engine.matchList == []


def test_perfect_match_of_market_orders(engine, neighbor, asset):  # legacy case 3
    standing = market_order(engine, neighbor, 100.0)
    engine.orderList = [standing]
    new_order = market_order(engine, asset, -100.0, status=OrderStatus.NEW)
    engine.match(new_order)
    assert engine.orderList == []
    assert_balanced_matches(engine, 2, Counter({100.0: 2}))
    assert {m.parent_order_id for m in engine.matchList} == {standing.id, new_order.id}
    assert standing.status is OrderStatus.SUPERSEDED and new_order.status is OrderStatus.SUPERSEDED
    supply_leg = next(m for m in engine.matchList if m.supply_or_demand is SupplyOrDemand.SUPPLY)
    assert supply_leg.seller is neighbor and supply_leg.buyer is asset


def test_aggressive_demand_twice_the_standing_supply(engine, neighbor, asset):  # legacy case 4
    standing = market_order(engine, neighbor, 100.0)
    engine.orderList = [standing]
    new_order = market_order(engine, asset, -200.0, status=OrderStatus.NEW)
    engine.match(new_order)
    assert len(engine.orderList) == 1
    remainder = engine.orderList[0]
    assert remainder.supply_or_demand is SupplyOrDemand.DEMAND and remainder.actor is asset
    assert remainder.vertices[0].quantity == -100.0 and remainder.status is OrderStatus.STANDING_ORDER
    assert remainder is not new_order and remainder.parent_order_id == new_order.id
    assert_balanced_matches(engine, 2, Counter({100.0: 2}))
    assert {m.parent_order_id for m in engine.matchList} == {standing.id, new_order.id}


def test_aggressive_demand_half_the_standing_supply(engine, neighbor, asset):  # legacy case 5
    standing = market_order(engine, neighbor, 100.0)
    engine.orderList = [standing]
    new_order = market_order(engine, asset, -50.0, status=OrderStatus.NEW)
    engine.match(new_order)
    assert len(engine.orderList) == 1
    remainder = engine.orderList[0]
    assert remainder.supply_or_demand is SupplyOrDemand.SUPPLY and remainder.actor is neighbor
    assert remainder.vertices[0].quantity == 50.0 and remainder.parent_order_id == standing.id
    assert_balanced_matches(engine, 2, Counter({50.0: 2}))
    assert {m.parent_order_id for m in engine.matchList} == {standing.id, new_order.id}


def test_discontinuous_aggressive_order_will_not_partially_fill(engine, neighbor, asset):  # legacy case 6
    standing = market_order(engine, neighbor, 100.0)
    engine.orderList = [standing]
    new_order = market_order(engine, asset, -200.0, continuous=False, status=OrderStatus.NEW)
    engine.match(new_order)
    assert engine.matchList == []
    assert engine.orderList == [standing, new_order], 'all-or-nothing: the new order stands untouched'
    assert new_order.status is OrderStatus.STANDING_ORDER
    assert {o.vertices[0].quantity for o in engine.orderList} == {100.0, -200.0}


def test_discontinuous_standing_order_will_not_partially_fill(engine, neighbor, asset):  # legacy case 7
    standing = market_order(engine, neighbor, 100.0, continuous=False)
    engine.orderList = [standing]
    new_order = market_order(engine, asset, -50.0, status=OrderStatus.NEW)
    engine.match(new_order)
    assert engine.matchList == []
    assert [o.supply_or_demand for o in engine.orderList] == [SupplyOrDemand.SUPPLY, SupplyOrDemand.DEMAND]
    assert engine.orderList[0] is standing
    demand = engine.orderList[1]
    assert demand.vertices[0].quantity == -50.0 and demand.actor is asset
    assert demand is new_order or demand.parent_order_id == new_order.id
    assert demand.status is OrderStatus.STANDING_ORDER


def test_small_demand_market_order_against_full_book(engine, neighbor, asset):  # legacy case 8
    engine.orderList = standing_book(engine, neighbor)
    engine.match(market_order(engine, asset, -40.0, status=OrderStatus.NEW))
    # The 40 kW is taken from one continuous 100 kW supply market order, whose 60 kW remainder replaces it.
    assert len(engine.orderList) == 48
    assert_balanced_matches(engine, 2, Counter({40.0: 2}))
    remainders = [o for o in engine.orderList if o.vertices[0].quantity == 60.0]
    assert len(remainders) == 1 and remainders[0].parent_order_id is not None


@pytest.mark.parametrize('strategy', [MatchingStrategy.ORDER_VOLUME, MatchingStrategy.ORDER_AGE])
def test_small_demand_takes_one_continuous_supply_market_order(engine, neighbor, asset, strategy):  # legacy case 9
    engine.orderList = standing_book(engine, neighbor)
    engine.matching_strategy = [strategy]
    engine.match(market_order(engine, asset, -25.0, status=OrderStatus.NEW))
    assert_balanced_matches(engine, 2, Counter({25.0: 2}))
    # Market orders match at the infinite price and are recorded as single-vertex market orders.
    assert all(len(m.vertices) == 1 and abs(m.vertices[0].price) == INF for m in engine.matchList)
    assert engine.matchList[0].vertices[0].quantity == 25.0, 'the standing (supply) leg is booked first'


def test_intermediate_demand_by_volume_priority(engine, neighbor, asset):  # legacy case 10
    engine.orderList = standing_book(engine, neighbor)
    engine.matching_strategy = [MatchingStrategy.ORDER_VOLUME]
    engine.match(market_order(engine, asset, -125.0, status=OrderStatus.NEW))
    # Largest first: one 100 kW market order fully, then 25 kW from the first continuous 100 kW order (discontinuous
    # orders cannot take a partial fill).
    assert_balanced_matches(engine, 4, Counter({100.0: 2, 25.0: 2}))


def test_large_demand_by_volume_priority(engine, neighbor, asset):  # legacy case 11
    engine.orderList = standing_book(engine, neighbor)
    engine.matching_strategy = [MatchingStrategy.ORDER_VOLUME]
    engine.match(market_order(engine, asset, -525.0, status=OrderStatus.NEW))
    # Supply market orders at the infinite price total 600 kW: four of 100, two of 50, then 25 of a continuous 50.
    assert_balanced_matches(engine, 14, Counter({100.0: 8, 50.0: 4, 25.0: 2}))


def test_large_supply_by_volume_priority(engine, neighbor, asset):  # legacy case 12
    engine.orderList = standing_book(engine, neighbor)
    engine.matching_strategy = [MatchingStrategy.ORDER_VOLUME]
    engine.match(market_order(engine, asset, 525.0, status=OrderStatus.NEW))
    assert_balanced_matches(engine, 14, Counter({100.0: 8, 50.0: 4, 25.0: 2}))


@pytest.mark.parametrize('quantity', [-525.0, 525.0])
def test_large_order_by_age_priority(engine, neighbor, asset, quantity):  # legacy cases 13 and 14
    book = standing_book(engine, neighbor)
    engine.orderList = list(book)
    engine.matching_strategy = [MatchingStrategy.ORDER_AGE]
    engine.match(market_order(engine, asset, quantity, status=OrderStatus.NEW))
    # Oldest first. Every order has its own creation microsecond, so age alone decides: the four minute-old market
    # orders (100, 100, 50, 50) are taken whole, then the fresh 100 kW continuous one, the fresh 100 kW discontinuous
    # one cannot take the 25 kW remainder, and the fresh 50 kW continuous one does.
    assert_balanced_matches(engine, 14, Counter({100.0: 8, 50.0: 4, 25.0: 2}))
    counter_side = SupplyOrDemand.DEMAND if quantity > 0 else SupplyOrDemand.SUPPLY
    old_market_orders = [o for o in book if len(o.vertices) == 1 and o.supply_or_demand is counter_side
                         and o.time_stamp < Timer.now() - ONE_MINUTE / 2]
    assert len(old_market_orders) == 4 and all(o.status is OrderStatus.SUPERSEDED for o in old_market_orders)
    # The 25 kW remainder of the partially filled order stands in place of its parent; the book count is unchanged.
    remainders = [o for o in engine.orderList if abs(o.vertices[0].quantity) == 25.0]
    assert len(remainders) == 1 and remainders[0].parent_order_id is not None
    assert len(engine.orderList) == 48 - 6


def test_volume_priority_leaves_small_old_orders_standing(engine, neighbor, asset):
    """The same 525 kW under volume priority takes the fresh 100 kW orders before any minute-old 50 kW one."""
    book = standing_book(engine, neighbor)
    engine.orderList = list(book)
    engine.matching_strategy = [MatchingStrategy.ORDER_VOLUME]
    engine.match(market_order(engine, asset, -525.0, status=OrderStatus.NEW))
    supply_market = [o for o in book if len(o.vertices) == 1 and o.supply_or_demand is SupplyOrDemand.SUPPLY]
    hundreds = [o for o in supply_market if o.vertices[0].quantity == 100.0]
    assert all(o.status is OrderStatus.SUPERSEDED for o in hundreds), 'all four 100 kW orders go first'
    fifties_untouched = [o for o in supply_market if o.vertices[0].quantity == 50.0
                         and o.status is OrderStatus.STANDING_ORDER]
    assert len(fifties_untouched) == 1, 'two 50s whole, one 50 partially (its remainder replaces it), one untouched'


def test_oldest_orders_matched_first(engine, neighbor, asset):
    """Age priority is visible when only two standing orders tie on price: the older one is taken."""
    older = market_order(engine, neighbor, 100.0, age=ONE_MINUTE)
    newer = market_order(engine, neighbor, 100.0)
    engine.orderList = [newer, older]
    engine.matching_strategy = [MatchingStrategy.ORDER_AGE]
    engine.match(market_order(engine, asset, -60.0, status=OrderStatus.NEW))
    assert older.status is OrderStatus.SUPERSEDED and newer.status is OrderStatus.STANDING_ORDER
    assert_balanced_matches(engine, 2, Counter({60.0: 2}))


def test_largest_orders_matched_first(engine, neighbor, asset):
    small = market_order(engine, neighbor, 50.0, age=ONE_MINUTE)
    large = market_order(engine, neighbor, 100.0)
    engine.orderList = [small, large]
    engine.matching_strategy = [MatchingStrategy.ORDER_VOLUME]
    engine.match(market_order(engine, asset, -60.0, status=OrderStatus.NEW))
    assert large.status is OrderStatus.SUPERSEDED and small.status is OrderStatus.STANDING_ORDER
    assert_balanced_matches(engine, 2, Counter({60.0: 2}))


def test_very_large_demand_walks_up_the_supply_curve(engine, neighbor, asset):  # legacy case 15
    engine.orderList = standing_book(engine, neighbor)
    engine.matching_strategy = [MatchingStrategy.ORDER_VOLUME]
    engine.match(market_order(engine, asset, -1575.0, status=OrderStatus.NEW))
    # Price ladder for a demand order: -inf (all 600 kW of supply market orders), 0.10 (all 600 kW of supply limit
    # orders), 0.15 (the piece-wise linear orders offer nothing yet), 0.25 (600 kW of pwl supply, of which 375 is
    # needed: 100, 100, 100 and 75 of a continuous 100).
    assert_balanced_matches(engine, 40, Counter({100.0: 22, 50.0: 16, 75.0: 2}))
    prices = {m.vertices[-1].price for m in engine.matchList}
    assert prices == {-INF, 0.1, 0.25}
    standing = (OrderStatus.STANDING_ORDER, OrderStatus.REVISED_STANDING_ORDER)
    assert engine.orderList and all(o.status in standing for o in engine.orderList)
    assert not any(o.supply_or_demand is SupplyOrDemand.DEMAND and o.actor is asset for o in engine.orderList), \
        'the aggressive order was fully filled'


def test_very_large_supply_walks_down_the_demand_curve(engine, neighbor, asset):  # legacy case 16
    engine.orderList = standing_book(engine, neighbor)
    engine.matching_strategy = [MatchingStrategy.ORDER_VOLUME]
    engine.match(market_order(engine, asset, 1575.0, status=OrderStatus.NEW))
    # Price ladder for a supply order: +inf (600 kW of demand market orders), 0.25 (pwl demand offers nothing yet),
    # 0.15 (600 kW of pwl demand), 0.10 (600 kW of demand limit orders, of which 375 is needed).
    assert_balanced_matches(engine, 40, Counter({100.0: 22, 50.0: 16, 75.0: 2}))
    assert {m.vertices[-1].price for m in engine.matchList} == {INF, 0.15, 0.1}


def test_partially_filled_standing_order_remainder_survives_the_pass(engine, neighbor, asset):
    """A standing order's remainder is a revised standing order; it must still be in the book after the pass."""
    standing = market_order(engine, neighbor, 100.0)
    engine.orderList = [standing]
    engine.submit_order(market_order(engine, asset, -30.0, status=OrderStatus.NEW))
    engine.while_in_negotiation()
    assert_balanced_matches(engine, 2, Counter({30.0: 2}))
    assert len(engine.orderList) == 1
    remainder = engine.orderList[0]
    assert remainder.parent_order_id == standing.id and remainder.vertices[0].quantity == 70.0
    assert remainder.status is OrderStatus.REVISED_STANDING_ORDER
    # And it is still a counteroffer for the next aggressive order.
    engine.submit_order(market_order(engine, asset, -70.0, status=OrderStatus.NEW))
    engine.while_in_negotiation()
    assert engine.orderList == [] and len(engine.matchList) == 4


def test_match_archives_every_booked_and_superseded_order(engine, neighbor, asset):
    standing = market_order(engine, neighbor, 100.0)
    engine.orderList = [standing]
    new_order = market_order(engine, asset, -50.0, status=OrderStatus.NEW)
    engine.match(new_order)
    rows = engine.tn.data_manager.by_table('order')
    by_id = {}
    for r in rows:
        by_id.setdefault(r['id'], []).append(r['status'])
    assert by_id[standing.id] == [OrderStatus.SUPERSEDED]
    assert by_id[new_order.id] == [OrderStatus.SUPERSEDED]
    assert [s for m in engine.matchList for s in by_id[m.id]] == [OrderStatus.MATCHED, OrderStatus.MATCHED]
    assert by_id[engine.orderList[0].id] == [OrderStatus.REVISED_STANDING_ORDER], 'the standing order remainder'


# ---------------------------------------------------------------------------------------------------------------------
# get_order_by_id: legacy test_get_order_by_id (order_id_is_unique was removed: ids are UUIDv7)
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def two_lists(engine, neighbor):
    engine.orderList = [market_order(engine, neighbor, -100.0), market_order(engine, neighbor, -100.0, False)]
    engine.matchList = [market_order(engine, neighbor, 100.0, status=OrderStatus.MATCHED),
                        market_order(engine, neighbor, 100.0, False, status=OrderStatus.MATCHED)]
    return engine


def test_get_order_by_id(two_lists):
    assert two_lists.get_order_by_id() is None, 'no id, no order'
    assert two_lists.get_order_by_id(an_id=two_lists.orderList[0].id) is two_lists.orderList[0]
    assert two_lists.get_order_by_id(an_id=two_lists.matchList[0].id) is two_lists.matchList[0]
    assert two_lists.get_order_by_id(an_id=uuid7()) is None



# ---------------------------------------------------------------------------------------------------------------------
# get_counteroffers: legacy test_get_counteroffers cases 1-6
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def mixed_status_book(engine, neighbor):
    engine.orderList = [
        market_order(engine, neighbor, -100.0, status=OrderStatus.STANDING_ORDER),           # 0 valid demand
        market_order(engine, neighbor, -100.0, False, status=OrderStatus.CANCELLED),
        market_order(engine, neighbor, 100.0, status=OrderStatus.REVISED_STANDING_ORDER),    # 2 valid supply
        market_order(engine, neighbor, 100.0, False, status=OrderStatus.SUPERSEDED),
        limit_order(engine, neighbor, 0.1, 100.0, status=OrderStatus.NEW),
    ]
    return engine


def test_counteroffers_require_a_standing_status(mixed_status_book):
    book = mixed_status_book.orderList
    assert mixed_status_book.get_counteroffers(SupplyOrDemand.SUPPLY) == [book[2]]
    assert mixed_status_book.get_counteroffers(SupplyOrDemand.DEMAND) == [book[0]]


def test_no_counteroffers_on_a_one_sided_book(mixed_status_book, neighbor):
    mixed_status_book.orderList = [x for x in mixed_status_book.orderList
                                   if x.supply_or_demand is SupplyOrDemand.DEMAND]
    assert len(mixed_status_book.orderList) == 2
    assert mixed_status_book.get_counteroffers(SupplyOrDemand.SUPPLY) == []
    mixed_status_book.orderList = [market_order(mixed_status_book, neighbor, 100.0)]
    assert mixed_status_book.get_counteroffers(SupplyOrDemand.DEMAND) == []


def test_counteroffers_keep_book_order(mixed_status_book, neighbor):
    extra_supply = pwl_order(mixed_status_book, neighbor, 100.0)
    extra_demand = limit_order(mixed_status_book, neighbor, 0.1, -100.0, False)
    mixed_status_book.orderList += [extra_supply, extra_demand]
    book = mixed_status_book.orderList
    assert mixed_status_book.get_counteroffers(SupplyOrDemand.SUPPLY) == [book[2], extra_supply]
    assert mixed_status_book.get_counteroffers(SupplyOrDemand.DEMAND) == [book[0], extra_demand]


# ---------------------------------------------------------------------------------------------------------------------
# get_counteroffer_vertices: legacy test_get_counteroffer_vertices cases 1-7
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def status_and_shape_book(engine, neighbor):
    S = OrderStatus
    engine.orderList = [
        market_order(engine, neighbor, -100.0, status=S.STANDING_ORDER),          # valid, continuous, -100
        market_order(engine, neighbor, -100.0, False, status=S.NEW),
        limit_order(engine, neighbor, 0.1, -100.0, status=S.CANCELLED),
        limit_order(engine, neighbor, 0.1, -100.0, False, status=S.SUPERSEDED),
        pwl_order(engine, neighbor, -100.0, status=S.STANDING_ORDER),             # valid, continuous, -100 at <=0.15
        pwl_order(engine, neighbor, -100.0, False, status=S.STANDING_ORDER),      # valid, discontinuous, -100
        market_order(engine, neighbor, 100.0, status=S.EXPIRED),
        market_order(engine, neighbor, 100.0, False, status=S.CANCELLED),
        limit_order(engine, neighbor, 0.1, 100.0, status=S.REVISED_STANDING_ORDER),  # valid, continuous, 100 at >=0.1
        limit_order(engine, neighbor, 0.1, 100.0, False, status=S.STANDING_ORDER),   # valid, discontinuous, 100
        pwl_order(engine, neighbor, 100.0, status=S.NEW_REVISED),
        pwl_order(engine, neighbor, 100.0, False, status=S.EXPIRED),
    ]
    return engine


@pytest.mark.parametrize('price, side, continuity, count, total', [
    (0.0, SupplyOrDemand.DEMAND, None, 3, -300.0),   # case 1: market order and both pwl orders are fully on
    (1.0, SupplyOrDemand.SUPPLY, None, 2, 200.0),    # case 2: both limit orders are fully on above their strike
    (0.0, SupplyOrDemand.DEMAND, True, 2, -200.0),   # case 3
    (1.0, SupplyOrDemand.SUPPLY, True, 1, 100.0),    # case 4
    (0.0, SupplyOrDemand.DEMAND, False, 1, -100.0),  # case 5
    (1.0, SupplyOrDemand.SUPPLY, False, 1, 100.0),   # case 6
    (1.0, SupplyOrDemand.DEMAND, None, 1, -100.0),   # case 7: above 0.25 only the demand market order remains
])
def test_counteroffer_vertices_at_a_price(status_and_shape_book, price, side, continuity, count, total):
    vertices = status_and_shape_book.get_counteroffer_vertices(price=price, supply_or_demand=side,
                                                               continuity=continuity)
    assert len(vertices) == count
    assert sum(v.quantity for v in vertices) == pytest.approx(total)
    assert all(v.price == price for v in vertices), 'vertices are evaluated at the test price'
    assert all(v.curve is not None for v in vertices), 'each vertex knows the order it came from'


def test_counteroffer_vertices_between_pwl_breakpoints(status_and_shape_book):
    """Between its breakpoints a continuous piece-wise linear order is interpolated; a discontinuous one offers
    nothing (it is all-or-nothing at its full-quantity price); at the zero-quantity end both offer nothing."""
    demand = status_and_shape_book.get_counteroffer_vertices(price=0.20, supply_or_demand=SupplyOrDemand.DEMAND)
    assert sorted(v.quantity for v in demand) == pytest.approx([-100.0, -50.0])
    assert {v.vType for v in demand} == {VTypes.MARKET_ORDER, VTypes.LINEAR}
    at_zero_end = status_and_shape_book.get_counteroffer_vertices(price=0.25, supply_or_demand=SupplyOrDemand.DEMAND)
    assert [v.quantity for v in at_zero_end] == [-100.0], 'only the market order'


def test_counteroffer_vertices_filter_by_type(status_and_shape_book):
    # Beyond its last breakpoint a pwl order is flat, so it counts as a limit-order quantity; in between it is linear.
    flat = status_and_shape_book.get_counteroffer_vertices(price=0.0, supply_or_demand=SupplyOrDemand.DEMAND,
                                                           vertex_type=VTypes.LIMIT_ORDER)
    assert [v.quantity for v in flat] == [-100.0, -100.0, -100.0]
    sloped = status_and_shape_book.get_counteroffer_vertices(price=0.20, supply_or_demand=SupplyOrDemand.DEMAND,
                                                             vertex_type=VTypes.LIMIT_ORDER)
    assert [v.quantity for v in sloped] == [-100.0], 'the market order only; the continuous pwl is LINEAR here'


# ---------------------------------------------------------------------------------------------------------------------
# get_market_orders_by_actor: legacy test_get_market_orders_by_actor cases 1-3
# ---------------------------------------------------------------------------------------------------------------------

def test_matched_orders_by_actor(engine, neighbor, asset):
    other = Neighbor(transactive_node=engine.tn, name='Neighbor #2', cost_parameters=[0.0, 0.0, 0.0])
    engine.matchList = [market_order(engine, neighbor, -100.0, status=OrderStatus.MATCHED),
                        market_order(engine, other, -100.0, False, status=OrderStatus.MATCHED),
                        market_order(engine, neighbor, 100.0, status=OrderStatus.MATCHED),
                        market_order(engine, asset, 100.0, False, status=OrderStatus.MATCHED)]
    stranger = LocalAsset(transactive_node=engine.tn, name='LocalAsset #2', cost_parameters=[0.0, 0.0, 0.0])
    assert engine.get_market_orders_by_actor(stranger) == []
    assert engine.get_market_orders_by_actor(asset) == [engine.matchList[3]]
    assert engine.get_market_orders_by_actor(asset)[0].actor is asset
    assert engine.get_market_orders_by_actor(neighbor) == [engine.matchList[0], engine.matchList[2]]
    # Roles: a demand order's actor is its buyer, a supply order's actor its seller.
    assert engine.get_market_orders_by_actor(neighbor, role=Role.BUYER.name) == [engine.matchList[0]]
    assert engine.get_market_orders_by_actor(neighbor, role='seller') == [engine.matchList[2]]


# ---------------------------------------------------------------------------------------------------------------------
# Spawning: legacy test_spawn_markets, which drove MatchingEngine.spawn_markets. Markets are spawned by their series.
# ---------------------------------------------------------------------------------------------------------------------

def test_series_spawns_matching_engines_with_the_same_configuration(engine):
    series = engine.series
    engine.matching_strategy = [MatchingStrategy.ORDER_AGE]  # per-market state is not inherited...
    series.spawn()
    spawned = series.newest_market
    assert spawned is not engine and isinstance(spawned, MatchingEngine)
    assert spawned.matching_strategy == series.market_kwargs.get('matching_strategy', [MatchingStrategy.ORDER_VOLUME]), \
        '...the series configuration is'
    assert spawned.market_state is MarketState.Negotiation
    assert spawned.series.id == series.id and spawned.orderList == [] and spawned.matchList == []
    for name in ('negotiation', 'market_lead', 'delivery_lead', 'delivery'):
        assert getattr(spawned.transition_times, name) > getattr(engine.transition_times, name)
    assert (spawned.transition_times.delivery - spawned.transition_times.delivery_lead
            == engine.transition_times.delivery - engine.transition_times.delivery_lead), 'same delivery lead'
    assert spawned.time_intervals[0].duration == engine.time_intervals[0].duration
    assert series.newest_market is spawned and engine in series.markets


# ---------------------------------------------------------------------------------------------------------------------
# All-or-nothing aggressive orders: nothing is committed unless the order fills completely (audit T-06)
# ---------------------------------------------------------------------------------------------------------------------

def test_unfillable_all_or_nothing_order_leaves_the_book_intact(engine, neighbor, asset):
    """50 kW partially fillable + 20 kW all-or-nothing supply cannot fill a 60 kW all-or-nothing demand: the 50 kW order
    must still be standing afterwards (it used to be superseded by a tentative match that was then abandoned)."""
    big = limit_order(engine, neighbor, 0.1, 50.0)
    small = limit_order(engine, neighbor, 0.1, 20.0, continuous=False)
    engine.orderList = [big, small]
    new_order = limit_order(engine, asset, 0.1, -60.0, continuous=False, status=OrderStatus.NEW)
    engine.match(new_order)
    assert engine.matchList == []
    assert [o for o in engine.orderList if o is big] and [o for o in engine.orderList if o is small]
    assert big.status is OrderStatus.STANDING_ORDER and small.status is OrderStatus.STANDING_ORDER
    assert big.child_order_ids == [] and small.child_order_ids == [], 'abandoned legs leave no lineage behind'
    assert new_order.status is OrderStatus.STANDING_ORDER and any(o is new_order for o in engine.orderList)
    rows = engine.tn.data_manager.by_table('order')
    assert {r['id'] for r in rows} == {new_order.id}, 'only the resting aggressive order was archived'
    # The book is still live: a partially fillable 60 kW demand now takes the 50 and rests its remaining 10.
    engine.match(limit_order(engine, asset, 0.1, -60.0, status=OrderStatus.NEW))
    assert_balanced_matches(engine, 2, Counter({50.0: 2}))
    assert Counter(o.vertices[-1].quantity for o in engine.orderList) == Counter({20.0: 1, -60.0: 1, -10.0: 1})


def test_all_or_nothing_order_fills_when_the_book_covers_it_exactly(engine, neighbor, asset):
    engine.orderList = [limit_order(engine, neighbor, 0.1, 50.0), limit_order(engine, neighbor, 0.1, 10.0)]
    engine.match(limit_order(engine, asset, 0.1, -60.0, continuous=False, status=OrderStatus.NEW))
    assert_balanced_matches(engine, 4, Counter({50.0: 2, 10.0: 2}))
    assert engine.orderList == []


# ---------------------------------------------------------------------------------------------------------------------
# PROPORTIONAL_VOLUME (audit T-INC-05)
# ---------------------------------------------------------------------------------------------------------------------

def _leg_for(engine, parent):
    return next(m for m in engine.matchList if m.parent_order_id == parent.id).vertices[-1].quantity


def test_proportional_volume_splits_the_fill_among_partially_fillable_orders(engine, neighbor, asset):
    a = limit_order(engine, neighbor, 0.1, 40.0)
    b = limit_order(engine, neighbor, 0.1, 30.0)
    c = limit_order(engine, neighbor, 0.1, 50.0, continuous=False)
    engine.orderList = [a, b, c]
    engine.matching_strategy = [MatchingStrategy.PROPORTIONAL_VOLUME]
    engine.match(limit_order(engine, asset, 0.1, -35.0, status=OrderStatus.NEW))
    # The partially fillable 70 kW covers the 35 kW asked: a and b are filled pro rata (4:3); c is not touched.
    assert_balanced_matches(engine, 4, Counter({20.0: 2, 15.0: 2}))
    assert _leg_for(engine, a) == 20.0 and _leg_for(engine, b) == 15.0
    assert any(o is c for o in engine.orderList) and c.status is OrderStatus.STANDING_ORDER
    remainders = {o.parent_order_id: o.vertices[-1].quantity for o in engine.orderList if o is not c}
    assert remainders == {a.id: 20.0, b.id: 15.0}
    assert all(o.status is OrderStatus.REVISED_STANDING_ORDER for o in engine.orderList if o is not c)


def test_proportional_volume_takes_whole_orders_until_the_partial_set_covers_the_rest(engine, neighbor, asset):
    a = limit_order(engine, neighbor, 0.1, 40.0)
    b = limit_order(engine, neighbor, 0.1, 30.0)
    c = limit_order(engine, neighbor, 0.1, 100.0, continuous=False)
    d = limit_order(engine, neighbor, 0.1, 60.0, continuous=False)
    engine.orderList = [a, b, c, d]
    engine.matching_strategy = [MatchingStrategy.PROPORTIONAL_VOLUME]
    engine.match(limit_order(engine, asset, 0.1, -150.0, status=OrderStatus.NEW))
    # 70 kW partially fillable < 150: the largest order (100, all-or-nothing) is taken whole; 70 >= the remaining 50,
    # so a and b split it 4:3. d (60 > 50, all-or-nothing) is skipped and stays.
    assert _leg_for(engine, c) == 100.0
    assert _leg_for(engine, a) == pytest.approx(50.0 * 40.0 / 70.0)
    assert _leg_for(engine, b) == pytest.approx(50.0 * 30.0 / 70.0)
    assert sum(v.quantity for m in engine.matchList for v in m.vertices) == pytest.approx(0.0)
    assert len(engine.matchList) == 6
    assert any(o is d for o in engine.orderList) and d.status is OrderStatus.STANDING_ORDER
    assert sum(o.vertices[-1].quantity for o in engine.orderList) == pytest.approx(60.0 + 20.0)


def test_proportional_volume_with_no_partially_fillable_orders_falls_back_to_whole_fills(engine, neighbor, asset):
    engine.orderList = [limit_order(engine, neighbor, 0.1, 40.0, continuous=False),
                        limit_order(engine, neighbor, 0.1, 30.0, continuous=False)]
    engine.matching_strategy = [MatchingStrategy.PROPORTIONAL_VOLUME]
    engine.match(limit_order(engine, asset, 0.1, -50.0, status=OrderStatus.NEW))
    assert_balanced_matches(engine, 2, Counter({40.0: 2}))
    assert Counter(o.vertices[-1].quantity for o in engine.orderList) == Counter({30.0: 1, -10.0: 1})


# ---------------------------------------------------------------------------------------------------------------------
# Order expiry (audit T-INC-04)
# ---------------------------------------------------------------------------------------------------------------------

def test_expired_standing_orders_leave_the_book_and_their_quote(engine, neighbor, asset):
    now = Timer.now()
    stale = Order(engine, engine.time_intervals[0], neighbor, expire_by=now - ONE_MINUTE,
                  vertices=[Vertex(price=-INF, quantity=100.0)], supply_or_demand=SupplyOrDemand.SUPPLY)
    stale.status = OrderStatus.STANDING_ORDER
    fresh = market_order(engine, neighbor, 50.0)
    engine.orderList = [stale, fresh]
    engine.quotes = {'stale': Quote(version=1, orders=[stale]), 'fresh': Quote(version=1, orders=[fresh])}
    engine.while_in_negotiation()
    assert stale.status is OrderStatus.EXPIRED and not any(o is stale for o in engine.orderList)
    assert [o for o in engine.orderList] == [fresh] and fresh.status is OrderStatus.STANDING_ORDER
    assert set(engine.quotes) == {'fresh'}
    rows = engine.tn.data_manager.by_table('order')
    assert [r['status'] for r in rows if r['id'] == stale.id] == [OrderStatus.EXPIRED]


def test_expired_remainder_expires_its_quote_lineage_and_the_curve_is_requoted(engine, neighbor, asset):
    """A quoted order's remainder (a copy) expires with its quote; the entity's still-active curve is quoted again with
    a fresh expiry. Matched legs stay where they are."""
    engine.order_expiry = timedelta(minutes=10)
    ti = engine.time_intervals[0]
    asset.active_vertices.update(ti, [Vertex(price=0.1, quantity=0.0), Vertex(price=0.1, quantity=100.0)])
    t0 = Timer.now()
    with patch.object(Timer, 'now', return_value=t0):
        engine.while_in_negotiation()
        quoted = engine.orderList[0]
        assert quoted.expire_by == t0 + timedelta(minutes=10)
        engine.submit_order(market_order(engine, neighbor, -30.0, status=OrderStatus.NEW))
        engine.while_in_negotiation()
    remainder = next(o for o in engine.orderList if o.actor is asset)
    assert remainder.root_order_id == quoted.id and remainder.vertices[-1].quantity == 70.0
    assert len(engine.matchList) == 2
    with patch.object(Timer, 'now', return_value=t0 + timedelta(minutes=11)):
        engine.while_in_negotiation()
    assert remainder.status is OrderStatus.EXPIRED
    assert len(engine.matchList) == 2, 'matched legs are transactions and do not expire'
    requoted = [o for o in engine.orderList if o.actor is asset]
    assert len(requoted) == 1 and requoted[0].root_order_id != quoted.id
    assert requoted[0].vertices[-1].quantity == 100.0 and requoted[0].expire_by == t0 + timedelta(minutes=21)
    assert engine.quotes[(asset.id, ti.id)].orders == requoted


# ---------------------------------------------------------------------------------------------------------------------
# the price of a fill made at the infinite level
# ---------------------------------------------------------------------------------------------------------------------

def test_a_curve_hitting_a_resting_market_order_prices_the_fill_from_the_curve(engine, neighbor, asset, caplog):
    """A demand market order rests (it takes any price); a piece-wise linear supply offer hits it at the infinite rung
    of the ladder. The trade is priced by the side that has a curve: the offer's price for the matched quantity."""
    engine.orderList = [market_order(engine, neighbor, -40.0)]
    with caplog.at_level('WARNING', logger='tent.market'):
        engine.match(pwl_order(engine, asset, 100.0, status=OrderStatus.NEW))  # 0 kW at 0.15 up to 100 kW at 0.25
    assert_balanced_matches(engine, 2, Counter({40.0: 2}))
    assert [m.vertices[-1].price for m in engine.matchList] == pytest.approx([0.15 + 0.10 * 40.0 / 100.0] * 2)
    assert not caplog.records
    remainder = engine.orderList[0]
    assert remainder.actor is asset and max(v.quantity for v in remainder.vertices) == pytest.approx(60.0)


def test_two_market_orders_have_no_price_and_are_named_once(engine, neighbor, asset, caplog):
    engine.orderList = [market_order(engine, neighbor, -40.0), market_order(engine, neighbor, -10.0)]
    with caplog.at_level('WARNING', logger='tent.market'):
        engine.match(market_order(engine, asset, 30.0, status=OrderStatus.NEW))
        engine.match(market_order(engine, asset, 20.0, status=OrderStatus.NEW))
    assert all(abs(m.vertices[-1].price) == INF for m in engine.matchList)
    messages = [r.getMessage() for r in caplog.records if 'neither side names a price' in r.getMessage()]
    assert len(messages) == 1, 'once per interval'
    assert 'LocalAsset #1' in messages[0] and 'Neighbor #1' in messages[0]
