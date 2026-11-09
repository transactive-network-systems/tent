"""
Batch clearing of the order book (OrderBookConfig.clearing 'batch', the default): every order of an interval is
collected, the summed supply and demand are crossed and the fills are allocated at the crossing, all legs at that one
price, whatever order the entities were quoted in. The ladder walk (continuous clearing) has its own tests in
test_matching_engine_book.py and test_matching_engine_core.py; the representative cases here are the same book, cleared
at once. In both modes the interval's reported marginal price is the crossing of the orders the window saw, and the
volume-weighted average is recorded beside it without being called a marginal price.
"""
import random
from collections import Counter
from datetime import timedelta
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from tent.containers.vertex import Vertex
from tent.enumerations import MarketState, OrderStatus, SupplyOrDemand
from tent.local_asset import LocalAsset
from tent.market.market_series import MarketSeries
from tent.market.matching_auction import MatchingAuction
from tent.market.matching_consensus import MatchingConsensusMarket
from tent.market.matching_engine import MatchingEngine, MatchingStrategy
from tent.market.order_book import OrderBookConfig
from tent.neighbor import Neighbor
from tent.utils.timer import Timer
from tests.mock_tn import MockTN
from tests.unit_tests.market_tests.test_matching_engine_book import (RecordingDataManager, limit_order, market_order,
                                                                     pwl_order, standing_book)

INF = float('inf')


def build_engine(clearing='batch', **settings):
    tn = MockTN()
    tn._data_manager = RecordingDataManager()
    series = MarketSeries(transactive_node=tn, name='Matching Series', market_class=MatchingEngine, clearing=clearing,
                          initial_market_state=MarketState.Negotiation, duration_negotiation=3600.0,
                          duration_delivery_lead=900.0, interval_duration=3600.0, **settings)
    tn.market_series = [series]
    tn.neighbors = [Neighbor(transactive_node=tn, name='Neighbor #1', cost_parameters=[0.0, 0.0, 0.0])]
    tn.local_assets = [LocalAsset(transactive_node=tn, name='LocalAsset #1', cost_parameters=[0.0, 0.0, 0.0])]
    return series.newest_market


@pytest.fixture
def engine():
    return build_engine()


@pytest.fixture
def neighbor(engine):
    return engine.tn.neighbors[0]


@pytest.fixture
def asset(engine):
    return engine.tn.local_assets[0]


def fills_of(market) -> Counter:
    """The interval's trades as a multiset of (actor, side, quantity, price) over the matched legs."""
    return Counter((leg.actor.name, leg.supply_or_demand.name, round(leg.vertices[-1].quantity, 9),
                    round(leg.vertices[-1].price, 12)) for leg in market.matchList)


def traded(market) -> float:
    return sum(abs(leg.vertices[-1].quantity) for leg in market.matchList) / 2


def clear(market):
    market.clear_interval(market.time_intervals[0])


# ---------------------------------------------------------------------------------------------------- the setting

def test_clearing_is_a_setting_of_every_book_market():
    """The field takes its two values, defaults to batch, rejects anything else and is carried by all three book
    markets; it is not reconfigurable."""
    assert OrderBookConfig().clearing == 'batch'
    assert OrderBookConfig(clearing='continuous').clearing == 'continuous'
    assert OrderBookConfig(clearing='batch').clearing == 'batch'
    for bad in ('Batch', 'auction', None, 1):
        with pytest.raises(ValidationError, match='clearing'):
            OrderBookConfig(clearing=bad)
    for market_class in (MatchingEngine, MatchingAuction, MatchingConsensusMarket):
        field = market_class.Config.model_fields['clearing']
        assert field.default == 'batch'
        assert 'batch' in field.description and 'continuous' in field.description
        assert 'clearing' not in market_class.Config.reconfigurable_fields()
    assert build_engine().clearing == 'batch' and build_engine('continuous').clearing == 'continuous'


# ------------------------------------------------------------------------------------- the 48-order book, cleared at once

def test_the_book_alone_clears_where_its_sides_cross(engine, neighbor):
    """Supply at 0.10 (600 kW of market orders and 600 kW of limit orders) meets the demand still wanted just above
    0.10 (600 kW of market orders and 600 kW of piece-wise linear orders, full up to 0.15): 1200 kW at 0.10. The
    demand limit orders at 0.10 are the margin; nothing of them is needed, and they stand."""
    engine.orderList = standing_book(engine, neighbor)
    clear(engine)
    assert traded(engine) == pytest.approx(1200.0)
    assert {leg.vertices[-1].price for leg in engine.matchList} == {0.1}
    assert sum(v.quantity for leg in engine.matchList for v in leg.vertices) == pytest.approx(0.0)
    assert all(leg.status is OrderStatus.MATCHED for leg in engine.matchList)
    standing = [o for o in engine.orderList]
    assert all(o.status in (OrderStatus.STANDING_ORDER, OrderStatus.REVISED_STANDING_ORDER) for o in standing)
    demand_limits = [o for o in standing if o.supply_or_demand is SupplyOrDemand.DEMAND and len(o.vertices) == 2
                     and o.vertices[0].price == 0.1]
    assert Counter(o.vertices[-1].quantity for o in demand_limits) == Counter({-100.0: 4, -50.0: 4})
    assert Counter(o.vertices[-1].quantity for o in standing if o.supply_or_demand is SupplyOrDemand.SUPPLY) == \
        Counter({100.0: 4, 50.0: 4}), 'the supply piece-wise linear orders, out of the money at 0.10'


def test_perfect_match_of_market_orders_has_no_price(engine, neighbor, asset):
    engine.orderList = [market_order(engine, neighbor, 100.0), market_order(engine, asset, -100.0, status=OrderStatus.NEW)]
    clear(engine)
    assert engine.orderList == [] and traded(engine) == pytest.approx(100.0)
    assert all(abs(leg.vertices[-1].price) == INF for leg in engine.matchList), 'no order names a price'
    supply_leg = next(leg for leg in engine.matchList if leg.supply_or_demand is SupplyOrDemand.SUPPLY)
    assert supply_leg.seller is neighbor and supply_leg.buyer is asset


def test_a_curve_against_a_market_order_clears_at_the_curves_price(engine, neighbor, asset):
    """A 40 kW inelastic load and an offer rising from 0 kW at 0.15 to 100 kW at 0.25 cross inside the segment, at the
    offer's price for 40 kW, whichever was booked first."""
    for first_resting in (True, False):
        market = build_engine()
        n, a = market.tn.neighbors[0], market.tn.local_assets[0]
        orders = [market_order(market, n, -40.0), pwl_order(market, a, 100.0, status=OrderStatus.NEW)]
        market.orderList = orders if first_resting else orders[::-1]
        clear(market)
        assert traded(market) == pytest.approx(40.0)
        assert [leg.vertices[-1].price for leg in market.matchList] == pytest.approx([0.15 + 0.10 * 40.0 / 100.0] * 2)
        remainder = market.orderList[0]
        assert remainder.actor is a and max(v.quantity for v in remainder.vertices) == pytest.approx(60.0)


def test_demand_twice_the_supply_leaves_the_rest_standing(engine, neighbor, asset):
    engine.orderList = [market_order(engine, neighbor, 100.0), market_order(engine, asset, -200.0, status=OrderStatus.NEW)]
    clear(engine)
    assert traded(engine) == pytest.approx(100.0)
    remainder = engine.orderList[0]
    assert remainder.actor is asset and remainder.vertices[0].quantity == -100.0
    assert remainder.status is OrderStatus.STANDING_ORDER and remainder.parent_order_id is not None


def test_an_all_or_nothing_order_the_other_side_cannot_cover_does_not_trade(engine, neighbor, asset):
    standing = market_order(engine, neighbor, 100.0)
    new_order = market_order(engine, asset, -200.0, continuous=False, status=OrderStatus.NEW)
    engine.orderList = [standing, new_order]
    clear(engine)
    assert engine.matchList == []
    assert standing.status is OrderStatus.STANDING_ORDER and new_order.status is OrderStatus.STANDING_ORDER
    assert [o for o in engine.orderList] == [standing, new_order]


def test_an_all_or_nothing_order_fills_when_the_book_covers_it_exactly(engine, neighbor, asset):
    engine.orderList = [limit_order(engine, neighbor, 0.1, 50.0), limit_order(engine, neighbor, 0.1, 10.0),
                        limit_order(engine, asset, 0.1, -60.0, continuous=False, status=OrderStatus.NEW)]
    clear(engine)
    assert traded(engine) == pytest.approx(60.0) and engine.orderList == []
    assert Counter(abs(leg.vertices[-1].quantity) for leg in engine.matchList) == Counter({50.0: 2, 10.0: 2})
    assert {leg.vertices[-1].price for leg in engine.matchList} == {0.1}


def test_the_strategy_rations_the_margin(engine, neighbor, asset):
    """Two 100 kW supply market orders of different ages against 60 kW of demand: the whole supply is at the margin
    (nothing of it is in the money below the crossing) and the strategy picks which fills."""
    for strategy, winner_name in ((MatchingStrategy.ORDER_AGE, 'older'), (MatchingStrategy.ORDER_VOLUME, 'larger')):
        market = build_engine()
        n, a = market.tn.neighbors[0], market.tn.local_assets[0]
        older = market_order(market, n, 100.0 if strategy is MatchingStrategy.ORDER_AGE else 50.0, age=timedelta(minutes=1))
        newer = market_order(market, n, 100.0)
        market.matching_strategy = [strategy]
        market.orderList = [newer, older, market_order(market, a, -60.0, status=OrderStatus.NEW)]
        clear(market)
        winner = older if winner_name == 'older' else newer
        loser = newer if winner is older else older
        assert winner.status is OrderStatus.SUPERSEDED and loser.status is OrderStatus.STANDING_ORDER
        assert traded(market) == pytest.approx(60.0)


def test_proportional_volume_splits_the_margin_among_partially_fillable_orders(engine, neighbor, asset):
    a = limit_order(engine, neighbor, 0.1, 40.0)
    b = limit_order(engine, neighbor, 0.1, 30.0)
    c = limit_order(engine, neighbor, 0.1, 50.0, continuous=False)
    engine.matching_strategy = [MatchingStrategy.PROPORTIONAL_VOLUME]
    engine.orderList = [a, b, c, limit_order(engine, asset, 0.1, -35.0, status=OrderStatus.NEW)]
    clear(engine)
    legs = {leg.parent_order_id: leg.vertices[-1].quantity for leg in engine.matchList
            if leg.supply_or_demand is SupplyOrDemand.SUPPLY}
    assert legs == {a.id: pytest.approx(20.0), b.id: pytest.approx(15.0)}
    assert any(o is c for o in engine.orderList) and c.status is OrderStatus.STANDING_ORDER


def test_a_very_large_market_order_is_a_deficit_priced_at_the_top_of_the_ladder(engine, neighbor, asset):
    """1575 kW of inelastic demand on top of the book's 600 kW of demand market orders exceeds all 1800 kW of supply:
    no price clears it. Every supply order fills, the demand market orders ration the 1800 kW (largest first, so the
    new order fills whole), the rest stands, and the price is the highest any order names, 0.25."""
    engine.orderList = standing_book(engine, neighbor)
    engine.matching_strategy = [MatchingStrategy.ORDER_VOLUME]
    new_order = market_order(engine, asset, -1575.0, status=OrderStatus.NEW)
    engine.orderList.append(new_order)
    clear(engine)
    assert traded(engine) == pytest.approx(1800.0)
    assert {leg.vertices[-1].price for leg in engine.matchList} == {0.25}, 'one price for every leg'
    assert sum(leg.vertices[-1].quantity for leg in engine.matchList if leg.actor is asset) == pytest.approx(-1575.0)
    assert new_order.status is OrderStatus.SUPERSEDED
    assert not any(o.supply_or_demand is SupplyOrDemand.SUPPLY for o in engine.orderList), 'all supply filled'
    standing_demand = sum(o.vertices[-1].quantity for o in engine.orderList)
    assert standing_demand == pytest.approx(-(600.0 + 600.0 + 375.0)), \
        'the limit and piece-wise linear demand are out of the money at 0.25; 375 kW of demand market orders stands'
    assert len(engine.standing_market_orders(engine.time_intervals[0])) >= 1


def test_a_discontinuous_demand_drop_puts_the_crossing_at_the_rung_below_it(engine, neighbor, asset):
    """Supply 50 kW at any price and 70 kW at 0.08; demand 60 kW at up to 0.08 (a limit) and 40 kW at any price. At
    0.08 supply is 120 and demand 100; just above it demand is 40 and supply 120. Walking up: at 0.08 the net is
    positive already (the supply limit at 0.08 is on), so the price is 0.08, demand fills in full and the supply at
    the margin (the limit's 70 kW, nothing of it in the money below 0.08) fills the 50 kW the market orders leave."""
    engine.orderList = [market_order(engine, neighbor, 50.0), limit_order(engine, neighbor, 0.08, 70.0),
                        limit_order(engine, asset, 0.08, -60.0), market_order(engine, asset, -40.0)]
    clear(engine)
    assert traded(engine) == pytest.approx(100.0)
    assert {leg.vertices[-1].price for leg in engine.matchList} == {0.08}
    supply = Counter()
    for leg in engine.matchList:
        if leg.supply_or_demand is SupplyOrDemand.SUPPLY:
            supply[leg.parent_order_id] += leg.vertices[-1].quantity
    assert sorted(supply.values()) == pytest.approx([50.0, 50.0]), 'the market order whole, 50 of the limit'
    assert Counter(o.vertices[-1].quantity for o in engine.orderList) == Counter({20.0: 1}), 'of the limit'


def test_demand_dropping_off_just_above_a_rung_crosses_there(engine, neighbor, asset):
    """Supply 50 kW at any price only; demand 60 kW at up to 0.05 and 40 kW at any price. At 0.05 demand is 100 against
    50 of supply (net negative); just above 0.05 only the 40 kW market order is left, so the net is positive there:
    the crossing is at 0.05. Supply fills in full; of the demand, what is still wanted above the rung (40) is in the
    money and fills whole, and the limit at the margin takes the 10 kW left."""
    limit = limit_order(engine, asset, 0.05, -60.0)
    engine.orderList = [market_order(engine, neighbor, 50.0), limit, market_order(engine, asset, -40.0)]
    clear(engine)
    assert traded(engine) == pytest.approx(50.0)
    assert {leg.vertices[-1].price for leg in engine.matchList} == {0.05}
    demand = Counter()
    for leg in engine.matchList:
        if leg.supply_or_demand is SupplyOrDemand.DEMAND:
            demand[leg.parent_order_id] += leg.vertices[-1].quantity
    assert demand[limit.id] == pytest.approx(-10.0) and sum(demand.values()) == pytest.approx(-50.0)
    remainder = next(o for o in engine.orderList if o.parent_order_id == limit.id)
    assert remainder.vertices[-1].quantity == pytest.approx(-50.0)


def test_a_surplus_of_market_orders_is_priced_at_the_bottom_of_the_ladder(engine, neighbor, asset):
    """Inelastic supply beyond everything the demand will take: the demand fills in full, the supply market orders
    ration it (one order's remainder stands as a market order), the price is the lowest any order names."""
    engine.orderList = [market_order(engine, neighbor, 300.0), market_order(engine, neighbor, 100.0),
                        limit_order(engine, asset, 0.12, -150.0), market_order(engine, asset, -50.0)]
    clear(engine)
    assert traded(engine) == pytest.approx(200.0)
    assert {leg.vertices[-1].price for leg in engine.matchList} == {0.12}
    standing = engine.standing_market_orders(engine.time_intervals[0])
    assert sum(o.vertices[-1].quantity for o in standing) == pytest.approx(200.0), 'the surplus stands, inelastic'
    assert not any(o.supply_or_demand is SupplyOrDemand.DEMAND for o in engine.orderList)


# ---------------------------------------------------------------------------------------------- order independence

def test_the_booking_order_does_not_change_a_batch_result(engine, neighbor, asset):
    """The book, a demand market order and a supply curve, cleared in several shuffles of the order list: the same
    (actor, side, quantity, price) trades every time."""
    results = []
    for seed in range(6):
        market = build_engine(matching_strategy=['ORDER_VOLUME'])
        n, a = market.tn.neighbors[0], market.tn.local_assets[0]
        with patch.object(Timer, 'now', return_value=Timer.now()):  # every order the same age: only the rule decides
            orders = standing_book(market, n) + [market_order(market, a, -140.0, status=OrderStatus.NEW),
                                                 pwl_order(market, a, 80.0, status=OrderStatus.NEW)]
        random.Random(seed).shuffle(orders)
        market.orderList = orders
        clear(market)
        results.append((fills_of(market), round(traded(market), 9), {leg.vertices[-1].price for leg in market.matchList}))
    assert all(result == results[0] for result in results[1:]), results
    assert len(results[0][2]) == 1, 'one price for the interval'


def test_quoting_neighbors_first_or_assets_first_gives_the_same_batch_fills_and_differs_continuously():
    """Two entities, a load bidding 50 kW at up to 0.09 and an offer of 100 kW at 0.06 (a limit): quoted load first
    or offer first. In batch mode the crossing is 0.06 either way, 50 kW trade at it. In continuous mode whichever is
    quoted first rests and sets the price: 0.09 when the load rests, 0.06 when the offer rests; the reported marginal
    price is nevertheless the crossing, 0.06, in both."""
    def run(clearing, load_first):
        tn = MockTN()
        tn._data_manager = RecordingDataManager()
        series = MarketSeries(transactive_node=tn, name='Matching Series', market_class=MatchingEngine,
                              clearing=clearing, initial_market_state=MarketState.Negotiation,
                              duration_negotiation=3600.0, duration_delivery_lead=900.0, interval_duration=3600.0)
        tn.market_series = [series]
        market = series.newest_market
        ti = market.time_intervals[0]
        load = Neighbor(transactive_node=tn, name='Load', cost_parameters=[0.0, 0.0, 0.0])
        offer = Neighbor(transactive_node=tn, name='Offer', cost_parameters=[0.0, 0.0, 0.0])
        load.active_vertices.update(ti, [Vertex(price=0.09, quantity=0.0), Vertex(price=0.09, quantity=-50.0)])
        offer.active_vertices.update(ti, [Vertex(price=0.06, quantity=0.0), Vertex(price=0.06, quantity=100.0)])
        tn.local_assets, tn.neighbors = [], ([load, offer] if load_first else [offer, load])
        market.quote_and_match()
        market.settle()
        return market, fills_of(market), market.marginal_prices.get_by_interval(ti).value, \
            market.average_match_prices.get_by_interval(ti).value

    batch_a, batch_b = run('batch', True), run('batch', False)
    assert batch_a[1] == batch_b[1] and batch_a[1] and traded(batch_a[0]) == pytest.approx(50.0)
    assert {leg.vertices[-1].price for leg in batch_a[0].matchList} == {0.06}
    assert batch_a[2] == batch_b[2] == pytest.approx(0.06) and batch_a[3] == pytest.approx(0.06)
    continuous_a, continuous_b = run('continuous', True), run('continuous', False)
    assert {leg.vertices[-1].price for leg in continuous_a[0].matchList} == {0.09}, 'the resting load\'s limit'
    assert {leg.vertices[-1].price for leg in continuous_b[0].matchList} == {0.06}, 'the resting offer\'s price'
    assert continuous_a[1] != continuous_b[1]
    assert continuous_a[2] == continuous_b[2] == pytest.approx(0.06), 'the crossing is the marginal price in both'
    assert continuous_a[3] == pytest.approx(0.09) and continuous_b[3] == pytest.approx(0.06), 'the averages are the fills'
    assert continuous_a[0].last_fill_price(continuous_a[0].time_intervals[0]) == pytest.approx(0.09)
    assert set(batch_a[1]) == {('Load', 'DEMAND', -50.0, 0.06), ('Offer', 'SUPPLY', 50.0, 0.06)}


# ---------------------------------------------------------------------- the engine's window: collect, clear at the close

def settlement_entities(market):
    """Two suppliers (30 kW at 0.05, 100 kW at 0.08) and a 50 kW load bidding up to 0.09, as in the core tests."""
    tn = market.tn
    cheap = LocalAsset(transactive_node=tn, name='Cheap')
    dear = LocalAsset(transactive_node=tn, name='Dear')
    load = Neighbor(transactive_node=tn, name='Load')
    tn.local_assets, tn.neighbors = [cheap, dear], [load]
    ti = market.time_intervals[0]
    cheap.active_vertices.update(ti, [Vertex(price=0.05, quantity=0.0), Vertex(price=0.05, quantity=30.0)])
    dear.active_vertices.update(ti, [Vertex(price=0.08, quantity=0.0), Vertex(price=0.08, quantity=100.0)])
    load.active_vertices.update(ti, [Vertex(price=0.09, quantity=0.0), Vertex(price=0.09, quantity=-50.0)])
    return load, cheap, dear, ti


def test_the_engine_collects_through_the_window_and_clears_once_at_the_close(engine, caplog):
    """In batch mode a Negotiation pass quotes and revises but matches nothing; an order submitted late counts the
    same as one quoted early; the close (the transition to MarketLead) clears every interval once at its crossing and
    settles: 50 kW at 0.08, the dear supplier's 20 kW being the margin, every leg at 0.08."""
    load, cheap, dear, ti = settlement_entities(engine)
    engine.while_in_negotiation()
    assert engine.matchList == [] and len(engine.orderList) == 3, 'quoted, standing, unmatched'
    assert all(o.status is OrderStatus.NEW for o in engine.orderList)
    late = market_order(engine, load, -10.0, status=OrderStatus.NEW)
    engine.submit_order(late)
    engine.while_in_negotiation()
    assert engine.matchList == [] and len(engine.orderList) == 4
    with caplog.at_level('WARNING', logger='tent.market'):
        engine.transition_from_negotiation_to_market_lead()
    assert traded(engine) == pytest.approx(60.0)
    assert {leg.vertices[-1].price for leg in engine.matchList} == {0.08}
    assert engine.marginal_prices.get_by_interval(ti).value == pytest.approx(0.08)
    assert engine.average_match_prices.get_by_interval(ti).value == pytest.approx(0.08)
    assert engine.cleared_power(cheap, ti) == pytest.approx(30.0)
    assert engine.cleared_power(dear, ti) == pytest.approx(30.0), '20 for the bid and 10 for the late market order'
    assert engine.cleared_power(load, ti) == pytest.approx(-60.0)
    assert late.status is OrderStatus.SUPERSEDED
    assert not [r for r in caplog.records if 'neither side names a price' in r.getMessage()]
    rows = engine.tn.data_manager.by_table('order')
    assert all(leg.id in {r['id'] for r in rows} for leg in engine.matchList), 'every leg archived'
    assert any(r['id'] == late.id and r['status'] == OrderStatus.SUPERSEDED for r in rows)


def test_the_continuous_engine_still_matches_on_arrival_and_reports_the_crossing():
    """The same entities in continuous mode: the suppliers rest (assets are quoted first) and the load's bid takes 30
    at 0.05 and 20 at 0.08, pay-as-rest; the marginal price is the crossing of the window's orders, 0.08, the average
    0.062, and the last fill is kept apart."""
    engine = build_engine('continuous')
    load, cheap, dear, ti = settlement_entities(engine)
    engine.while_in_negotiation()
    assert traded(engine) == pytest.approx(50.0)
    assert Counter(leg.vertices[-1].price for leg in engine.matchList) == Counter({0.05: 2, 0.08: 2})
    engine.settle()
    assert engine.marginal_prices.get_by_interval(ti).value == pytest.approx(0.08)
    assert engine.average_match_prices.get_by_interval(ti).value == pytest.approx((30 * 0.05 + 20 * 0.08) / 50)
    assert engine.last_fill_price(ti) == pytest.approx(0.08)


def test_the_reported_price_is_the_crossing_of_every_order_the_window_saw():
    """Continuous mode: an aggressive bid at 0.10 takes a resting 30 kW offer at 0.05 (the fill is at 0.05); a second
    offer at 0.07 then arrives and rests unmatched. The window saw a 30 kW bid at 0.10 and offers at 0.05 and 0.07:
    their crossing is at 0.05 (the cheap offer alone covers the bid), not the last fill's price by chance but by the
    rule: the orders in their original quantities, filled or standing."""
    engine = build_engine('continuous')
    n, a = two_assets(engine)
    ti = engine.time_intervals[0]
    engine.submit_order(limit_order(engine, n, 0.05, 30.0, status=OrderStatus.NEW))
    engine.while_in_negotiation()
    engine.submit_order(limit_order(engine, a, 0.10, -30.0, status=OrderStatus.NEW))
    engine.while_in_negotiation()
    assert traded(engine) == pytest.approx(30.0) and {leg.vertices[-1].price for leg in engine.matchList} == {0.05}
    engine.submit_order(limit_order(engine, n, 0.07, 50.0, status=OrderStatus.NEW))
    engine.while_in_negotiation()
    assert len(engine.window_orders(ti)) == 3, 'filled, filled and standing: all in their original form'
    assert all(len(o.vertices) == 2 for o in engine.window_orders(ti))
    engine.settle()
    assert engine.marginal_prices.get_by_interval(ti).value == pytest.approx(0.05)
    # Were the bid larger than the cheap offer, the crossing would be at the dearer offer.
    engine2 = build_engine('continuous')
    n2, a2 = two_assets(engine2)
    engine2.submit_order(limit_order(engine2, n2, 0.05, 30.0, status=OrderStatus.NEW))
    engine2.submit_order(limit_order(engine2, n2, 0.07, 50.0, status=OrderStatus.NEW))
    engine2.while_in_negotiation()
    engine2.submit_order(limit_order(engine2, a2, 0.10, -40.0, status=OrderStatus.NEW))
    engine2.while_in_negotiation()
    engine2.settle()
    assert engine2.marginal_prices.get_by_interval(engine2.time_intervals[0]).value == pytest.approx(0.07)
    assert engine2.last_fill_price(engine2.time_intervals[0]) == pytest.approx(0.07)
    assert engine2.average_match_prices.get_by_interval(engine2.time_intervals[0]).value == pytest.approx(
        (30 * 0.05 + 10 * 0.07) / 40)


def two_assets(market):
    """Two local assets without curves, as the actors of orders submitted directly (an entity with a curve is quoted,
    and a neighbor is asked to schedule at settlement, which needs its curve)."""
    tn = market.tn
    tn.neighbors = []
    tn.local_assets = [LocalAsset(transactive_node=tn, name='Seller', cost_parameters=[0.0, 0.0, 0.0]),
                       LocalAsset(transactive_node=tn, name='Buyer', cost_parameters=[0.0, 0.0, 0.0])]
    return tn.local_assets


def test_a_revised_quote_replaces_its_predecessor_in_the_window(engine):
    """An entity whose curve changes is quoted afresh; the window's orders then hold the revision alone, not both: the
    load now bids 40 kW at up to 0.07, which only the cheap supplier (30 kW at 0.05) is in the money for."""
    load, cheap, dear, ti = settlement_entities(engine)
    engine.while_in_negotiation()
    assert len(engine.window_orders(ti)) == 3
    load.active_vertices.update(ti, [Vertex(price=0.07, quantity=0.0), Vertex(price=0.07, quantity=-40.0)], replace=True)
    engine.while_in_negotiation()
    window = engine.window_orders(ti)
    assert len(window) == 3 and [o for o in window if o.actor is load][0].vertices[0].price == 0.07
    engine.transition_from_negotiation_to_market_lead()
    assert engine.marginal_prices.get_by_interval(ti).value == pytest.approx(0.07), 'the bid at 0.07 is the margin'
    assert traded(engine) == pytest.approx(30.0), 'the dear supplier is out of the money at 0.07'
    assert engine.cleared_power(load, ti) == pytest.approx(-30.0) and engine.cleared_power(dear, ti) == 0.0


def test_an_interval_with_orders_on_one_side_keeps_its_opening_price(engine, caplog):
    load, cheap, dear, ti = settlement_entities(engine)
    engine.tn.neighbors = []  # suppliers only
    opening = engine.marginal_prices.get_by_interval(ti).value
    with caplog.at_level('WARNING', logger='tent.market'):
        engine.transition_from_negotiation_to_market_lead()
    assert engine.matchList == [] and engine.marginal_prices.get_by_interval(ti).value == opening
    assert engine.crossing(engine.window_orders(ti)) is None
    assert sum('had no match' in r.getMessage() for r in caplog.records) == 1
