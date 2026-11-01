"""Core tests for MatchingEngine against the current Market / MarketSeries / Order API."""
from datetime import timedelta

import pytest
from uuid6 import uuid7

from tent.containers.order import Order
from tent.containers.vertex import Vertex
from tent.enumerations import MarketState, MarketTypes, OrderStatus, SupplyOrDemand
from tent.local_asset import LocalAsset
from tent.neighbor import Neighbor
from tent.market.market_series import MarketSeries
from tent.market.matching_engine import MatchingEngine, MatchingStrategy, Role
from tent.utils.timer import Timer
from tests.mock_tn import MockTN


class RecordingDataManager:
    """Collects every archive call so tests can assert on what would have been written."""
    def __init__(self):
        self.records: list[dict] = []

    def archive_data(self, records):
        self.records.extend(records if isinstance(records, (list, tuple)) else [records])

    def by_table(self, table_name: str) -> list[dict]:
        return [r['data'] for r in self.records if r.get('table_name') == table_name]


class Actor:
    """Minimal stand-in for a Neighbor or LocalAsset that owns orders."""
    def __init__(self, name: str):
        self.name = name
        self.id = uuid7()


@pytest.fixture
def market():
    tn = MockTN()
    tn._data_manager = RecordingDataManager()
    series = MarketSeries(transactive_node=tn, name='Matching Series', market_class=MatchingEngine,
                          initial_market_state=MarketState.Negotiation, duration_negotiation=3600.0,
                          duration_delivery_lead=900.0, interval_duration=3600.0)
    tn.market_series = [series]
    return series.newest_market


def limit_demand(mkt, actor, price, quantity, status=OrderStatus.STANDING_ORDER):
    """A demand limit order: buy up to |quantity| kW at or below price."""
    order = Order(mkt, mkt.time_intervals[0], actor,
                  vertices=[Vertex(price=price, quantity=0.0), Vertex(price=price, quantity=-abs(quantity))],
                  supply_or_demand=SupplyOrDemand.DEMAND)
    order.status = status
    return order


def market_supply(mkt, actor, quantity):
    """A supply market order: sell quantity kW at any price."""
    return Order(mkt, mkt.time_intervals[0], actor, vertices=[Vertex(price=0.0, quantity=abs(quantity))],
                 supply_or_demand=SupplyOrDemand.SUPPLY)


def test_constructed_through_market_series(market):
    assert isinstance(market, MatchingEngine)
    assert market.MARKET_TYPE is MarketTypes.matching_engine
    assert market.series.market_type is MarketTypes.matching_engine
    assert market.orderList == [] and market.matchList == []
    assert market.matching_strategy == [MatchingStrategy.ORDER_VOLUME]
    assert market.order_measurement.source is market or market.order_measurement.source == market
    measurements = market.tn.data_manager.by_table('measurement')
    assert any(m['measurement_type'] == 'Order' for m in measurements)
    # The strategy is recorded on the market's own extension table, which inherits the market columns.
    market.archive()
    rows = market.tn.data_manager.by_table('matching_engine')
    assert rows[-1]['id'] == market.id and rows[-1]['matching_strategy'] == ['ORDER_VOLUME']
    assert rows[-1]['market_series_id'] == market.series.id


def make_series(tn, **kwargs):
    return MarketSeries(transactive_node=tn, name='Matching Series', market_class=MatchingEngine,
                        initial_market_state=MarketState.Negotiation, **kwargs)


def test_matching_strategy_configured_on_series(market):
    tn = market.tn
    series = make_series(tn, matching_strategy=['order_age', 'ORDER_VOLUME'])
    assert series.market_kwargs == {'matching_strategy': ['order_age', 'ORDER_VOLUME']}
    assert series.newest_market.matching_strategy == [MatchingStrategy.ORDER_AGE, MatchingStrategy.ORDER_VOLUME]
    series.spawn()  # every spawned market gets the same keywords
    assert series.newest_market.matching_strategy == [MatchingStrategy.ORDER_AGE, MatchingStrategy.ORDER_VOLUME]
    # Members and values are accepted too; a single item need not be wrapped in a list.
    assert make_series(tn, matching_strategy=MatchingStrategy.NONE).newest_market.matching_strategy == [MatchingStrategy.NONE]
    assert make_series(tn, matching_strategy=[2]).newest_market.matching_strategy == [MatchingStrategy.ORDER_VOLUME]
    # Each market records the strategy it actually uses, normalised, in its extension table.
    series.newest_market.archive()
    rows = [r for r in tn.data_manager.by_table('matching_engine') if r['id'] == series.newest_market.id]
    assert rows and rows[-1]['matching_strategy'] == ['ORDER_AGE', 'ORDER_VOLUME']


def test_unknown_market_keyword_fails_at_configuration(market):
    with pytest.raises(TypeError):
        make_series(market.tn, matching_strateg=['ORDER_AGE'])  # typo reaches MatchingEngine.__init__ and is rejected


def test_spawn_keywords_cannot_be_configured(market):
    # Names that spawn() supplies itself are rejected up front with a clear message.
    with pytest.raises(TypeError, match='transition_times'):
        make_series(market.tn, transition_times=None)


def test_order_archives_under_market_measurement(market):
    buyer = Actor('buyer')
    order = limit_demand(market, buyer, price=0.2, quantity=100.0)
    assert market.tn.data_manager.by_table('order') == [], 'orders archive when booked, not on construction'
    market.submit_order(order)
    rows = market.tn.data_manager.by_table('order')
    assert len(rows) == 1
    row = rows[-1]
    assert row['status'] is OrderStatus.NEW
    assert row['id'] == order.id
    assert row['actor_id'] == buyer.id and row['buyer_id'] == buyer.id and row['seller_id'] is None
    assert row['supply_or_demand'] is SupplyOrDemand.DEMAND
    # The order table inherits from sd_curve, so the curve columns ride along in the same record.
    assert row['measurement_id'] == market.order_measurement.id
    assert row['owner_id'] == market.id
    assert row['time_interval_id'] == market.time_intervals[0].id


def test_submit_order_rejects_foreign_market(market):
    market.series.spawn()
    other = market.series.newest_market
    assert other is not market
    order = market_supply(other, Actor('seller'), 10.0)
    with pytest.raises(ValueError):
        market.submit_order(order)


def test_market_supply_order_matches_standing_demand_limit(market):
    buyer, seller = Actor('buyer'), Actor('seller')
    standing = limit_demand(market, buyer, price=0.2, quantity=100.0)
    market.orderList.append(standing)
    aggressive = market_supply(market, seller, 100.0)
    market.submit_order(aggressive)
    assert aggressive in market.orderList and aggressive.status is OrderStatus.NEW

    market.while_in_negotiation()

    assert market.orderList == [], 'both sides were fully matched, so the book should be empty'
    assert len(market.matchList) == 2
    assert all(m.status is OrderStatus.MATCHED for m in market.matchList)
    assert standing.status is OrderStatus.SUPERSEDED and aggressive.status is OrderStatus.SUPERSEDED

    demand_match = next(m for m in market.matchList if m.supply_or_demand is SupplyOrDemand.DEMAND)
    supply_match = next(m for m in market.matchList if m.supply_or_demand is SupplyOrDemand.SUPPLY)
    assert demand_match.buyer is buyer and demand_match.seller is seller
    assert supply_match.seller is seller and supply_match.buyer is buyer
    # Matched at the standing limit price, for the full quantity, with signed quantities per side.
    assert {v.price for v in demand_match.vertices} == {0.2}
    assert min(v.quantity for v in demand_match.vertices) == -100.0
    assert max(v.quantity for v in supply_match.vertices) == 100.0
    # Parentage is recorded through copies.
    assert demand_match.parent_order_id == standing.id
    assert supply_match.parent_order_id == aggressive.id
    assert not market._stateIsCompleted, 'negotiation stays open until the market-lead time'

    # ACTOR returns the leg an actor originated; BUYER/SELLER return every leg in which it holds that role.
    assert market.get_market_orders_by_actor(buyer) == [demand_match]
    assert market.get_market_orders_by_actor(seller) == [supply_match]
    assert set(market.get_market_orders_by_actor(buyer, role=Role.BUYER.name)) == set(market.matchList)
    assert set(market.get_market_orders_by_actor(seller, role=Role.SELLER.name)) == set(market.matchList)
    assert market.get_market_orders_by_actor(buyer, role=Role.SELLER.name) == []

    rows = market.tn.data_manager.by_table('order')
    assert [r['status'] for r in rows if r['id'] == aggressive.id] == [OrderStatus.NEW, OrderStatus.SUPERSEDED]
    assert [r['status'] for r in rows if r['id'] == demand_match.id] == [OrderStatus.MATCHED]
    assert (standing.id, OrderStatus.SUPERSEDED) in {(r['id'], r['status']) for r in rows}
    # Every version row carries its own record_time, the second half of the primary key.
    assert all(r['record_time'] is not None for r in rows)


def test_partial_fill_leaves_standing_remainder(market):
    buyer, seller = Actor('buyer'), Actor('seller')
    market.orderList.append(limit_demand(market, buyer, price=0.2, quantity=40.0))
    aggressive = market_supply(market, seller, 100.0)
    market.submit_order(aggressive)

    market.while_in_negotiation()

    assert len(market.matchList) == 2
    assert len(market.orderList) == 1
    remainder = market.orderList[0]
    assert remainder.supply_or_demand is SupplyOrDemand.SUPPLY
    assert remainder.status is OrderStatus.STANDING_ORDER
    assert remainder.actor is seller
    assert max(v.quantity for v in remainder.vertices) == pytest.approx(60.0)
    assert remainder is not aggressive


def test_no_counteroffers_leaves_order_standing(market):
    seller = Actor('seller')
    aggressive = market_supply(market, seller, 50.0)
    market.submit_order(aggressive)
    market.while_in_negotiation()
    assert market.matchList == []
    assert len(market.orderList) == 1
    assert market.orderList[0].status is OrderStatus.STANDING_ORDER
    assert market.get_order_by_id(market.orderList[0].id) is market.orderList[0]
    # Append-only archive: one version per state the order passed through.
    history = [r['status'] for r in market.tn.data_manager.by_table('order') if r['id'] == aggressive.id]
    assert history == [OrderStatus.NEW, OrderStatus.STANDING_ORDER]


# ---------------------------------------------------------------------------------------------------------------------
# Pull path: entity curves are quoted into the book by the engine itself.
# ---------------------------------------------------------------------------------------------------------------------

def add_entities(market):
    tn = market.tn
    load = LocalAsset(transactive_node=tn, name='Load', cost_parameters=[])
    gen = LocalAsset(transactive_node=tn, name='Gen', cost_parameters=[])
    remote = Neighbor(transactive_node=tn, name='Remote', cost_parameters=[])
    tn.local_assets = [load, gen]
    tn.neighbors = [remote]
    return load, gen, remote


def order_rows(market, order_id):
    return [r['status'] for r in market.tn.data_manager.by_table('order') if r['id'] == order_id]


def test_entity_curves_are_quoted_and_matched(market):
    load, gen, remote = add_entities(market)
    ti = market.time_intervals[0]
    load.active_vertices.update(ti, [Vertex(price=0.2, quantity=0.0), Vertex(price=0.2, quantity=-40.0)])  # buy 40 @<=0.2
    gen.active_vertices.update(ti, [Vertex(price=0.1, quantity=100.0)])                                    # sell 100, any price
    # The neighbor has no curve yet (as before any transactive signal is received) and must simply be skipped.

    market.while_in_negotiation()

    assert set(market.quotes) == {(load.id, ti.id), (gen.id, ti.id)}
    assert len(market.matchList) == 2
    demand_match = next(m for m in market.matchList if m.supply_or_demand is SupplyOrDemand.DEMAND)
    assert demand_match.buyer is load and demand_match.seller is gen
    assert min(v.quantity for v in demand_match.vertices) == -40.0
    assert len(market.orderList) == 1
    remainder = market.orderList[0]
    assert remainder.actor is gen and remainder.supply_or_demand is SupplyOrDemand.SUPPLY
    assert max(v.quantity for v in remainder.vertices) == pytest.approx(60.0)
    assert remainder.status is OrderStatus.STANDING_ORDER
    # Quoted orders expire at market clearing by default.
    assert remainder.expire_by == market.transition_times.delivery_lead


def test_unchanged_curve_is_not_requoted(market):
    load, gen, remote = add_entities(market)
    ti = market.time_intervals[0]
    gen.active_vertices.update(ti, [Vertex(price=0.1, quantity=0.0), Vertex(price=0.1, quantity=100.0)])
    market.while_in_negotiation()
    book_before = list(market.orderList)
    rows_before = len(market.tn.data_manager.by_table('order'))

    market.while_in_negotiation()

    assert market.orderList == book_before
    assert len(market.tn.data_manager.by_table('order')) == rows_before
    assert market.quotes[(gen.id, ti.id)].version == gen.active_vertices.get_by_interval(ti).last_record_time


def test_changed_curve_cancels_and_revises_quote(market):
    load, gen, remote = add_entities(market)
    ti = market.time_intervals[0]
    gen.active_vertices.update(ti, [Vertex(price=0.1, quantity=0.0), Vertex(price=0.1, quantity=100.0)])
    market.while_in_negotiation()
    first = market.orderList[0]

    gen.active_vertices.update(ti, [Vertex(price=0.3, quantity=0.0), Vertex(price=0.3, quantity=80.0)], replace=True)
    market.while_in_negotiation()

    assert first not in market.orderList and first.status is OrderStatus.CANCELLED
    assert order_rows(market, first.id) == [OrderStatus.NEW, OrderStatus.STANDING_ORDER, OrderStatus.CANCELLED]
    revised = market.orderList[0]
    assert revised is not first and revised.actor is gen
    assert max(v.quantity for v in revised.vertices) == 80.0 and {v.price for v in revised.vertices} == {0.3}
    assert order_rows(market, revised.id) == [OrderStatus.NEW_REVISED, OrderStatus.STANDING_ORDER]
    assert market.quotes[(gen.id, ti.id)].orders == [revised]


def test_unarchived_curve_versions_by_vertex_values(market):
    load, gen, remote = add_entities(market)
    ti = market.time_intervals[0]
    gen.active_vertices.update(ti, [Vertex(price=0.1, quantity=0.0), Vertex(price=0.1, quantity=100.0)], archive=False)
    market.while_in_negotiation()
    version = market.quotes[(gen.id, ti.id)].version
    assert isinstance(version, tuple), 'no archived version exists, so the vertex values stand in for it'
    market.while_in_negotiation()
    assert len(market.orderList) == 1
    gen.active_vertices.update(ti, [Vertex(price=0.1, quantity=0.0), Vertex(price=0.1, quantity=90.0)],
                               archive=False, replace=True)
    market.while_in_negotiation()
    assert len(market.orderList) == 1 and max(v.quantity for v in market.orderList[0].vertices) == 90.0


def test_emptied_curve_withdraws_quote(market):
    load, gen, remote = add_entities(market)
    ti = market.time_intervals[0]
    remote.active_vertices.update(ti, [Vertex(price=0.15, quantity=0.0), Vertex(price=0.15, quantity=50.0)])
    market.while_in_negotiation()
    assert (remote.id, ti.id) in market.quotes and len(market.orderList) == 1

    remote.active_vertices.update(ti, [], replace=True)
    market.while_in_negotiation()

    assert (remote.id, ti.id) not in market.quotes
    assert market.orderList == []


def test_order_expiry_from_series_keywords(market):
    series = make_series(market.tn, order_expiry=900)
    mkt = series.newest_market
    load, gen, remote = add_entities(mkt)
    ti = mkt.time_intervals[0]
    gen.active_vertices.update(ti, [Vertex(price=0.1, quantity=0.0), Vertex(price=0.1, quantity=10.0)])
    before = Timer.now()
    mkt.while_in_negotiation()
    assert mkt.orderList[0].expire_by - before >= timedelta(seconds=900) - timedelta(seconds=5)


def test_order_submitted_later_in_the_window_is_matched(market):
    buyer, seller = Actor('buyer'), Actor('seller')
    market.orderList.append(limit_demand(market, buyer, price=0.2, quantity=50.0))
    market.while_in_negotiation()
    assert market.matchList == [] and not market._stateIsCompleted
    market.submit_order(market_supply(market, seller, 50.0))
    market.while_in_negotiation()
    assert len(market.matchList) == 2 and market.orderList == []
    # The state completes only once the market-lead time has arrived.
    from unittest.mock import patch
    with patch.object(Timer, 'now', return_value=market.transition_times.market_lead):
        market.while_in_negotiation()
    assert market._stateIsCompleted
