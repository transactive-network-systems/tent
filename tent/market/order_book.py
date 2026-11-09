"""
The order book shared by the matching markets.

An order book is a clearing rule: every local asset's and every neighbor's current curve is quoted into the book as
orders, the orders are matched with the configured strategy, and settlement turns the matched legs into the market's
prices and schedules (see settle). The book clears in one of two ways (OrderBookConfig.clearing): in batch mode every
order of an interval is collected and the fills are allocated where the summed supply and demand cross, all legs at that
one price, whatever order the entities were quoted in (clear_interval, crossing); in continuous mode each new or
revised order is matched on arrival against the orders then resting, at the resting orders' prices (match), so the
quoting order, which is the arrival order, decides which side rests. In both modes the interval's reported marginal
price is the crossing of every order the window saw. Three markets use it: MatchingEngine, which
runs the book through its own Negotiation on one node; MatchingAuction, which runs the Auction's signal exchange
between nodes with the book as its clearing step instead of interpolation; and MatchingConsensusMarket, which runs the
consensus protocol with the book as its local balancing step. The book is a mixin so that none of them copies it: a
market class lists OrderBook before its Market base, extends its Config with OrderBookConfig, and calls _init_book in
its constructor.
"""
import logging
import math

from copy import copy
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Literal

from pydantic import field_validator

from tent.config import ConfigModel, PositiveSeconds, Setting, enum_by_name
from tent.containers.measurement import Measurement
from tent.containers.order import Order, OrderStatus, SupplyOrDemand, Vertex, VTypes
from tent.enumerations import MarketTypes, MeasurementType, MeasurementUnit
from tent.utils.timer import Timer

_log = logging.getLogger(__name__)

# Constants:

INF = float('Inf')


class Role(Enum):
    ACTOR = 0
    BUYER = 1
    SELLER = 2


class MatchingStrategy(Enum):
    """
    Enumeration of matching strategies. Price is always be the main criterion, but age or volume may be used when
    multiple orders share exactly the same price.
    """
    NONE = 0  # There is no strategy for resolving competitions between same-price orders other than price.
    ORDER_AGE = 1  # Same-price orders are matched from oldest to newest.
    ORDER_VOLUME = 2  # Same-price orders are matched from largest quantity magnitude to smallest quantity
    # magnitude.
    PROPORTIONAL_VOLUME = 3  # If total partially-fillable order quantity exceeds the needed match quantity,
    # each partially fillable order is matched in proportion to its fractional quantity at the price. Otherwise,
    # the ORDER_VOLUME strategy is implemented until the remaining quantity to be matched is less than the total
    # partially-fillable order quantity before proceeding.


# Default order of matching strategies when neither the MarketSeries nor the constructor supplies one.
MATCHING_STRATEGY: list = [MatchingStrategy.ORDER_VOLUME]


@dataclass
class Quote:
    """The orders currently standing in the book for one entity and interval, and the curve version they express."""
    version: object
    orders: list = field(default_factory=list)


@dataclass
class Crossing:
    """Where one interval's summed supply and demand cross (see OrderBook.crossing): the price there, which is a batch
    clearing's price and the interval's reported marginal price in either clearing mode; the fills to make there as
    [(Order, signed quantity)], supply positive; and how it was found ('interior' to a segment of the ladder, at a 'rung',
    or a 'surplus' or 'deficit' of market orders no price clears). The price is infinite when no order names one."""
    price: float
    fills: list
    kind: str


# TODO: Make sure that IDs can work as UUID, instead of int.
class OrderBookConfig(ConfigModel):
    """The settings of a market that clears through an order book; a market's Config extends this and its base's."""
    clearing: Literal['batch', 'continuous'] = Setting(
        'batch',
        description='How the book clears an interval. "batch": every order of the interval is collected, supply and '
                    'demand are summed, the price where they cross is found and the fills are allocated there, all legs '
                    'at that one price; the result does not depend on the order in which the entities were quoted. '
                    'Orders below the margin fill in full; at the margin the matching_strategy tie-breaks decide which '
                    'orders fill and by how much, and all-or-nothing orders fill whole or not at all. Inelastic '
                    'quantities (market orders) enter the sums, so a node\'s own inelastic supply and load net against '
                    'each other and the exchange proposed to a neighbor is the net; an inelastic surplus or deficit the '
                    'curves cannot absorb is left standing as a market order. "continuous": each order is matched on '
                    'arrival against the book then standing, at the resting orders\' prices; order_expiry, age priority '
                    'and the arrival order (local assets, then neighbors, in configuration order) keep their meaning, '
                    'and whichever side was quoted first rests and sets the prices. In both modes the interval\'s '
                    'reported marginal price is where the supply and demand seen in the window cross.')
    matching_strategy: list[enum_by_name(MatchingStrategy)] = Setting(
        ['ORDER_VOLUME'], min_length=1,
        description='Tie-break order for same-price orders, by name: NONE, ORDER_AGE, ORDER_VOLUME, '
                    'PROPORTIONAL_VOLUME. A single name is accepted as a list of one.')
    order_expiry: PositiveSeconds | None = Setting(
        None, reconfigurable=True,
        description='[s] lifetime of the orders the engine quotes for local entities; null means they stand until '
                    'the market clears.')
    order_archiving: Literal['final_book', 'every_pass'] = Setting(
        'final_book',
        description='Which order books a negotiating market archives. Applies to a market that rebuilds its book on '
                    'every balancing pass (MatchingConsensusMarket, which clears a fresh book each time a neighbor\'s '
                    'signal or a local change moves it). "final_book": only the book standing when the market leaves '
                    'MarketLead, the one it clears on; the books of the passes a later signal superseded are not '
                    'written. "every_pass": the book standing at the end of every pass, for debugging a negotiation '
                    '(ten passes per market on the deployed campus). MatchingEngine and MatchingAuction clear one book '
                    'per market and archive orders as they are booked, so for them the two values are the same '
                    'behaviour.')

    @field_validator('matching_strategy', mode='before')
    @classmethod
    def _one_strategy_is_a_list(cls, value):
        return [value] if isinstance(value, (str, MatchingStrategy)) else value



class OrderBook:
    """
    The book and its clearing rule, as a mixin for a Market subclass (see the module docstring). Expects the host to be
    a Market (time_intervals, tn, marginal_prices, the cleared-power store, archiving) and to have called _init_book.

    Attributes:
        orderList: (list) of standing Order objects, aka the "order book".
        matchList: (list) of Order objects that have been matched (transacted).
        clearing: (str) 'batch' or 'continuous': whether an interval's orders are cleared together at one crossing
            price or each on arrival at the resting orders' prices (see OrderBookConfig).
        matching_strategy: (list of MatchingStrategy) Tie-break order for same-price orders.
        order_expiry: (timedelta | None) Lifetime of quoted orders; None means they expire at market clearing.
        order_archiving: (str) 'final_book' or 'every_pass': which books a negotiating market archives (see
            OrderBookConfig).
        order_measurement: (Measurement) Archival header under which every Order of this market is recorded.
        quotes: (dict) (entity id, interval id) -> Quote for the orders the engine has placed on entities' behalf.
    """

    def _init_book(self, cfg) -> None:
        """Set the book up from the validated configuration; call once the Market base is initialised."""
        self.order_measurement: Measurement = Measurement(self, MeasurementType.Order, MeasurementUnit.kW)
        self.orderList: list[Order] = []  # List of standing orders not yet matched
        self.matchList: list[Order] = []  # List of orders successfully matched
        self.clearing: str = cfg.clearing
        self.order_expiry: timedelta | None = cfg.order_expiry
        self.order_archiving: str = cfg.order_archiving
        self.quotes: dict[tuple, Quote] = {}
        # Every order of the window, by interval name and then by id, as submitted (its vertices are never altered:
        # matching books copies): the supply and demand the interval saw, whose crossing is its reported marginal price
        # (see crossing and settle). An order a revised or withdrawn quote replaced leaves the set; an order that was
        # filled, left standing, expired or cancelled stays in it.
        self._window_orders: dict[str, dict] = {}
        # Fills made at the infinite level in this pass, by interval: (aggressive actor, resting actors); reported in one
        # line by report_unpriced_fills, each (interval, actors) once per market (see _fill_price).
        self._unpriced_fills: dict[str, tuple[str, tuple[str, ...]]] = {}
        self._unpriced_fills_reported: set[tuple] = set()
        self._settlement_reported: set[tuple[str, str]] = set()  # (kind, interval) settle() has warned about
        # A negotiating market rebuilds its book on every balancing pass (reset_book) and archives only a pass's final
        # book, of every pass or of the market's last (order_archiving): while this is False the orders _archive_orders
        # is given are held, by identity, and written by flush_held_orders in their state at that time.
        self._book_archiving: bool = True
        self._held_orders: dict[int, Order] = {}

    def book_record(self) -> dict:
        """The book's columns of the market row."""
        return {'matching_strategy': [strategy.name for strategy in self.matching_strategy]}

    @staticmethod
    def book_table(name: str, inherits: str) -> list[dict]:
        """The archive definitions a book-clearing market adds: the order rows and its own table extending `inherits`
        with the book's settings."""
        return [*Order.get_archive_definitions(), {
            "name": name,
            "inherits": inherits,
            "columns": [
                {"name": "id", "foreign_key": f"{inherits}.id", "kwargs": {"primary_key": True}},
                {"name": "matching_strategy", "type": "JSON"}  # ordered list of MatchingStrategy names
            ]
        }]

    def quote_and_match(self) -> None:
        """One clearing pass: retire expired orders, quote every entity's current curve, match the book (match_book) and
        leave only standing orders in it."""
        self.quote_book()
        self.match_book()

    def quote_book(self) -> None:
        """Retire expired orders and quote every entity's current curve into the book, without matching: what a batch
        market does on every pass of its window, so that the book standing at the close is the current curves."""
        self._expire_orders()
        self.quote_entities()

    def match_book(self) -> None:
        """Match the book by the configured clearing: in batch mode every interval's standing orders are cleared
        together at the crossing of their summed supply and demand (clear_interval); in continuous mode every new or
        revised order is matched on arrival against the orders then resting (match). Afterwards only standing orders
        are left in the book."""
        if self.clearing == 'batch':
            for time_interval in self.time_intervals:
                self.clear_interval(time_interval)
        else:
            new_orders = [x for x in self.orderList if x.status in (OrderStatus.NEW, OrderStatus.NEW_REVISED)]
            for new_order in new_orders:
                self.match(new_order)
        # Matching removes every order it supersedes from the book and re-adds unmatched remainders as standing orders,
        # so after a pass the book should hold standing orders only. Anything else (e.g. a new order that cancelled
        # itself for having no quantity) is dropped here.
        self.orderList = [x for x in self.orderList
                          if x.status in (OrderStatus.STANDING_ORDER, OrderStatus.REVISED_STANDING_ORDER)]
        self.report_unpriced_fills()

    def reset_book(self) -> None:
        """Empty the book for a fresh clearing of the current curves: a negotiating market's balancing pass quotes
        everything anew rather than revising a book whose matches were made against curves the neighbors have since
        replaced. Orders held for archiving are dropped with it; the pass that stands at the end is what is written."""
        self.orderList = []
        self.matchList = []
        self.quotes = {}
        self._window_orders = {}
        self._unpriced_fills = {}
        self._held_orders = {}

    def hold_order_archiving(self) -> None:
        """Hold the orders given to _archive_orders instead of writing them (see _init_book)."""
        self._book_archiving = False

    def flush_held_orders(self) -> None:
        """Write every held order once, in its current state, and resume archiving as orders are booked."""
        held, self._held_orders = list(self._held_orders.values()), {}
        self._book_archiving = True
        self._archive_orders(held)

    def report_unpriced_fills(self) -> None:
        """One line for the fills of this pass that had no price-forming side (market orders meeting market orders),
        each interval and pair of actors reported once per market; the per-fill detail is logged at DEBUG as it
        happens (see _fill_price). Nothing is lost by the fill: settlement prices the interval from its priced legs,
        and the matched quantities are scheduled."""
        new = {interval: actors for interval, actors in self._unpriced_fills.items()
               if (interval, *actors) not in self._unpriced_fills_reported}
        self._unpriced_fills = {}
        if not new:
            return
        self._unpriced_fills_reported.update((interval, *actors) for interval, actors in new.items())
        pairs = sorted({f'{aggressor} by {", ".join(resting)}' for aggressor, resting in new.values()})
        _log.warning(f'MARKET: {self.name}: in {len(new)} of {len(self.time_intervals)} intervals the market order of '
                     f'{"; ".join(pairs)} was filled by market orders; neither side names a price, so those fills have '
                     f'none and the interval is priced by its priced legs (first {min(new)}).')

    def submit_order(self, order: Order) -> None:
        """
        Place a new order into this market's order book. Orders submitted while the market is in its Negotiation state
        are matched on the next pass of while_in_negotiation(); any unmatched remainder becomes a standing order.
        """
        if order.market is not self:
            raise ValueError(f'Order {order.id} belongs to market {order.market.name}, not {self.name}.')
        if order.status not in (OrderStatus.NEW, OrderStatus.NEW_REVISED):
            order.status = OrderStatus.NEW
        if not any(x is order for x in self.orderList):
            self.orderList.append(order)
            self._window_orders.setdefault(order.interval.name, {})[order.id] = order
            self._archive_orders([order])

    def window_orders(self, time_interval) -> list[Order]:
        """Every order the interval has seen in the window, as submitted: each entity's current quote and every order
        submitted directly, whether since filled, partly filled, standing, expired or cancelled. An order a revised or
        withdrawn quote replaced is not among them. Their crossing is the interval's reported marginal price."""
        return list(self._window_orders.get(time_interval.name, {}).values())

    def _forget_window_orders(self, orders) -> None:
        for order in orders:
            self._window_orders.get(order.interval.name, {}).pop(order.id, None)

    @staticmethod
    def _curve_version(curve):
        """Archived version if the curve has one, else its vertex values (for curves created with archive=False)."""
        archived = getattr(curve, 'last_record_time', None)
        return archived if archived is not None else tuple((v.price, v.quantity, v.continuity) for v in curve.vertices)

    def _quote_expiry(self):
        return Timer.now() + self.order_expiry if self.order_expiry is not None else self.transition_times.delivery_lead

    def quote_entities(self) -> None:
        """
        Offer every local asset's and neighbor's current active_vertices curve into the book, one quote per entity and
        interval. Unchanged curves are left alone. A changed curve cancels the entity's standing orders for that interval
        and submits revised ones; a curve that has emptied just cancels them.
        """
        for entity in list(self.tn.local_assets) + list(self.tn.neighbors):
            curves = getattr(entity, 'active_vertices', None)
            if curves is None:
                continue
            for time_interval in self.time_intervals:
                key = (entity.id, time_interval.id)
                curve = curves.get_by_interval(time_interval)
                quote = self.quotes.get(key)
                if curve is None or curve.empty:
                    if quote:
                        self._cancel_quote(quote)
                        del self.quotes[key]
                    continue
                version = self._curve_version(curve)
                if quote is not None and quote.version == version:
                    continue
                if quote is not None:
                    self._cancel_quote(quote)
                orders = Order.from_curve(self, time_interval, entity, curve, expire_by=self._quote_expiry())
                for order in orders:
                    order.status = OrderStatus.NEW_REVISED if quote is not None else OrderStatus.NEW
                    self.submit_order(order)
                self.quotes[key] = Quote(version, orders)

    def _cancel_quote(self, quote: Quote) -> None:
        """Withdraw whatever of a quote is still standing; matched legs are transactions and stay."""
        standing = [o for o in quote.orders if any(o is x for x in self.orderList)]
        for order in standing:
            order.status = OrderStatus.CANCELLED
        self.orderList = [x for x in self.orderList if not any(x is o for o in standing)]
        self._forget_window_orders(quote.orders)  # the revision (or nothing) now speaks for the entity
        self._archive_orders(standing)

    def _expire_orders(self) -> None:
        """
        Retire the standing orders whose expire_by has passed: status EXPIRED, archived, out of the book. The quotes they
        descend from are forgotten too, so an entity whose curve is still active is quoted afresh on this pass. Matched
        legs are transactions and are untouched.
        """
        now = Timer.now()
        expired = [x for x in self.orderList if x.expire_by <= now]
        if not expired:
            return
        for order in expired:
            order.status = OrderStatus.EXPIRED
        self.orderList = [x for x in self.orderList if not any(x is o for o in expired)]
        self._archive_orders(expired)
        roots = {o.root_order_id for o in expired}
        forgotten = [quote for quote in self.quotes.values() if any(o.id in roots for o in quote.orders)]
        for quote in forgotten:  # the curve, if still active, is quoted afresh this pass and speaks for the entity
            self._forget_window_orders(quote.orders)
        self.quotes = {key: quote for key, quote in self.quotes.items()
                       if not any(quote is gone for gone in forgotten)}

    def _archive_orders(self, orders) -> None:
        for order in orders:
            if order is None or order.measurement is None:
                continue
            if self._book_archiving:
                order.archive()
            else:
                self._held_orders[id(order)] = order

    def match(self, new_order: Order or None = None):
        """
        Match a new (aggressive) order against the order book at the resting orders' prices (the continuous clearing
        rule; a batch clearing goes through clear_interval), then archive the book changes: every order newly booked
        in matchList or orderList, and every order removed from the book (superseded by its matched/remainder copies).
        See _match for the matching algorithm.
        """
        before = self.orderList + self.matchList
        self._match(new_order)
        after = self.orderList + self.matchList
        booked = [x for x in after if not any(x is y for y in before)]
        superseded = [x for x in before if not any(x is y for y in after)]
        changed = superseded + booked
        if new_order is not None and not any(new_order is y for y in changed):
            # The aggressive order always ends in a new state: superseded by its matched legs, resting as a standing
            # order, or cancelled. Archive that final state even when it never left the book.
            changed.append(new_order)
        self._archive_orders(changed)

    def _match(self, new_order: Order or None = None):

        # Perform qualifications
        if not new_order:
            return None

        # Get eligible standing orders from the order book: the other side, in the same interval.
        if new_order.supply_or_demand == SupplyOrDemand.SUPPLY:
            standing_orders: list = self.get_counteroffers(supply_or_demand=SupplyOrDemand.DEMAND,
                                                           time_interval=new_order.interval)
        elif new_order.supply_or_demand == SupplyOrDemand.DEMAND:
            standing_orders: list = self.get_counteroffers(supply_or_demand=SupplyOrDemand.SUPPLY,
                                                           time_interval=new_order.interval)
        else:
            raise RuntimeError("There should be no case where the new order is neither supply nor demand.")

        # Get Vertex prices
        prices: list = Order.get_prices_from_curves(standing_orders + [new_order])

        # Make sure price is a unique list that includes either -INF or INF, but not both
        if new_order.supply_or_demand == SupplyOrDemand.SUPPLY:
            prices.append(INF)
            prices = [x for x in prices if x != -INF]
            prices = list(set(prices))
            prices.sort(reverse=True)
        elif new_order.supply_or_demand == SupplyOrDemand.DEMAND:
            prices.append(-INF)
            prices = [x for x in prices if x != INF]
            prices = list(set(prices))
            prices.sort(reverse=False)
        else:
            raise RuntimeError("There should be no case where the new order is neither supply nor demand.")

        # The last rung visited and the aggressive order's quantity there (what it still wanted after that rung's
        # fills), for the balance price between two rungs.
        previous_price: float | None = None
        previous_ao_quantity: float = 0.0

        # index through prices
        for price in prices:

            #   <LINEAR SO CASE> **************************************************************************************
            #     Get linear so quantities at this price
            #     Update ao quantity at this price
            #     Calculate total linear so quantity at this price
            #     CASE ao quantity = 0 (or undefined??) at this price
            #         Stop/Break (ao quantity will not increase at later prices
            #     CASE: total so quantity = 0 (so quantity may well increase at following prices or for limit orders)
            #     CASE: ao quantity >= total linear so quantity
            #         CASE: ao quantity is not continuous
            #         CASE: ao quantity is continuous
            #             Match and revise all linear so quantities at indexed price
            #             Calculate transacted and remainder ao quantities
            #             Find matched ao order at indexed price (should be a remainder)
            #             Revise ao order using transacted quantity
            #     CASE: ao quantity < total linear so quantity
            #             Calculate balance price
            #             Match and revise all linear so quantities at balance price
            #             Match and revise ao at balance price (Should not be a remainder)
            #             Stop/Break

            #  <STANDING ORDER CASE> *********************************************************************************
            #  Every standing order on the other side offers a definite quantity at this rung of the price ladder: a
            #  market or limit order its whole quantity, a piece-wise linear or complex curve the quantity its segments
            #  reach at this price. All of them can be taken here. When the standing side offers more at this rung than
            #  the aggressive order wants, and both sides are continuous between the previous rung and this one, the
            #  linear case of the pseudo-code above applies: the balance price inside the segment is found
            #  (_balance_price) and the fill made there, so a sloped standing curve is filled at its own price for the
            #  quantity taken, not at the rung above it. Otherwise the fill is made at the rung.

            #  Get the standing-order quantities at this price
            if new_order and new_order.supply_or_demand == SupplyOrDemand.DEMAND:
                limit_orders: list = self.get_counteroffer_vertices(
                    price=price,
                    supply_or_demand=SupplyOrDemand.SUPPLY,
                    time_interval=new_order.interval
                )

            elif new_order and new_order.supply_or_demand == SupplyOrDemand.SUPPLY:
                limit_orders: list = self.get_counteroffer_vertices(
                    price=price,
                    supply_or_demand=SupplyOrDemand.DEMAND,
                    time_interval=new_order.interval
                )
            else:
                break

            # Update aggressive order quantity at this price.
            ao: Vertex = new_order.get_quantity_from_price(test_price=price)

            # Calculate total limit so quantity at this price.
            total_limit_order_quantity: float = sum([x.quantity for x in limit_orders])

            # CASE: ao quantity is 0 (or undefined) at this price
            # Stop. Break. Aggressive order quantity will not increase at later price, so  there is no good reason to
            # review remaining prices.
            if ao.quantity == 0.0:
                break

            #  CASE: total limit so quantity = 0
            #  There is nothing else to do at this price, but standing order quantity might increase at later prices.
            elif total_limit_order_quantity == 0.0:
                previous_price, previous_ao_quantity = price, ao.quantity
                continue

            #  CASE: ao quantity >= total limit so quantity
            elif abs(ao.quantity) >= abs(total_limit_order_quantity):
                # Every standing limit order at this price can be taken in full. _apply_fills commits nothing if the
                # aggressive order is all-or-nothing here and would be left with a remainder.
                new_order = self._apply_fills(new_order, ao, [(so, so.quantity) for so in limit_orders], price)
                previous_price = price
                previous_ao_quantity = (new_order.get_quantity_from_price(test_price=price).quantity
                                        if new_order is not None else 0.0)

            #  CASE: ao quantity < total limit so quantity
            elif abs(ao.quantity) < abs(total_limit_order_quantity):
                balance = self._balance_price(new_order, ao, limit_orders, previous_price, previous_ao_quantity, price)
                if balance is not None:
                    # <LINEAR SO CASE>: both sides are continuous between the previous rung and this one, so the
                    # standing side's quantity meets the aggressive order's at a price inside the segment; every
                    # standing order is filled by its own quantity at that price and the aggressive order in full.
                    balance_price, balance_ao, fills = balance
                    new_order = self._apply_fills(new_order, balance_ao, fills, balance_price)
                else:
                    # The standing limit orders at this price can more than fill the aggressive order: the matching
                    # strategy decides which of them (and how much of each) are taken, at this rung.
                    limit_orders.sort(key=self._priority_key())
                    if self.matching_strategy and self.matching_strategy[0] is MatchingStrategy.PROPORTIONAL_VOLUME:
                        fills = self._proportional_fills(ao.quantity, limit_orders)
                    else:
                        fills = self._sequential_fills(ao.quantity, limit_orders)
                    new_order = self._apply_fills(new_order, ao, fills, price)

            else:
                raise RuntimeError("Code logic should not have reached this line.")

        if new_order and new_order.order_or_none():
            new_order.status = OrderStatus.STANDING_ORDER
            if not any(x is new_order for x in self.orderList):  # An untouched submission is already in the book.
                self.orderList.append(new_order)

    def _balance_price(self, aggressive_order: Order, ao: Vertex, standing: list, previous_price: float | None,
                       previous_ao_quantity: float, price: float):
        """
        The price between the previous rung of the ladder and this one at which the standing side's quantity equals the
        aggressive order's (the "linear standing order case"), with the fills to make there, or None when the fill
        belongs at this rung: there is no previous finite rung, some vertex taking part is all-or-nothing (its quantity
        does not run linearly between the rungs), or the two sides do not cross inside the segment.

        The ladder holds every breakpoint price in play, so between two consecutive rungs every continuous order is a
        straight line: the standing side's total runs from what it offered at the previous rung (nothing, everything
        there having been taken) to what it offers here, the aggressive order's from what it still wanted there to what
        it wants here, and the net of the two crosses zero at one price. Filling there gives each standing order its own
        curve's price for the quantity taken, which is the price its owner stated for it, and leaves the cleared
        quantities on the curves at the match price, as interpolation does. Returns (price, the aggressive order's
        vertex at it, [(standing vertex at it, its quantity)]).
        """
        if previous_price is None or not math.isfinite(previous_price) or not math.isfinite(price):
            return None
        if not ao.continuity or not all(so.continuity for so in standing):
            return None
        side = SupplyOrDemand.SUPPLY if ao.quantity < 0 else SupplyOrDemand.DEMAND
        interval = aggressive_order.interval
        standing_before = sum(so.quantity for so in self.get_counteroffer_vertices(price=previous_price,
                                                                                   supply_or_demand=side,
                                                                                   time_interval=interval))
        net_before = standing_before + previous_ao_quantity
        net_here = sum(so.quantity for so in standing) + ao.quantity
        if net_before == 0.0 or net_here == 0.0 or (net_before > 0) == (net_here > 0):
            return None  # no crossing strictly inside the segment: the rung is the price
        balance_price = previous_price + (price - previous_price) * net_before / (net_before - net_here)
        fills_at = self.get_counteroffer_vertices(price=balance_price, supply_or_demand=side, time_interval=interval)
        balance_ao = aggressive_order.get_quantity_from_price(test_price=balance_price)
        total = sum(so.quantity for so in fills_at)
        if not fills_at or abs(total + balance_ao.quantity) > 1e-9 * max(1.0, abs(total)):
            return None  # the sides are not linear here after all; fall back to the rung
        # The two sides meet exactly: the aggressive order keeps its own quantity at the balance price (a market
        # order's is exact) and the largest standing fill absorbs the rounding residual, so no dust remains on either side.
        fills = [(so, so.quantity) for so in fills_at]
        largest = max(range(len(fills)), key=lambda i: abs(fills[i][1]))
        fills[largest] = (fills[largest][0], fills[largest][1] - (total + balance_ao.quantity))
        return balance_price, balance_ao, fills

    # ------------------------------------------------------------------------------------------- batch clearing
    def clear_interval(self, time_interval) -> None:
        """
        Clear one interval's standing orders together (clearing 'batch'): find where their summed supply and demand
        cross (crossing), book the fills there, every leg at the crossing price (_book_fills), and archive the book
        changes as match does. What the crossing leaves unfilled, in whole or in part, stands on; a new order the
        crossing did not reach rests as a standing order. Nothing here depends on the order the orders were quoted in:
        the fills are allocated from the sums, and ties at the margin are broken by the matching strategy's ranks and
        then by the orders' own attributes (see _priority_key).
        """
        live = (OrderStatus.NEW, OrderStatus.NEW_REVISED, OrderStatus.STANDING_ORDER,
                OrderStatus.REVISED_STANDING_ORDER)
        orders = [x for x in self.orderList if x.interval.name == time_interval.name and x.status in live]
        if not orders:
            return
        before = self.orderList + self.matchList
        crossing = self.crossing(orders)
        if crossing is not None and crossing.fills:
            self._book_fills(crossing.fills, crossing.price)
            _log.debug(f'MARKET: {self.name} cleared {time_interval.name} at {crossing.price:.6g} $/kWh ({crossing.kind}): '
                       f'{len(crossing.fills)} orders fill, {sum(q for _, q in crossing.fills if q > 0):.4g} kW traded.')
        resting = [o for o in orders if o.status in (OrderStatus.NEW, OrderStatus.NEW_REVISED)]
        for order in resting:  # an unfilled new order rests, as one that found no counteroffer does in match
            order.status = OrderStatus.STANDING_ORDER
        after = self.orderList + self.matchList
        booked = [x for x in after if not any(x is y for y in before)]
        superseded = [x for x in before if not any(x is y for y in after)]
        self._archive_orders(superseded + booked + resting)

    def crossing(self, orders: list) -> 'Crossing | None':
        """
        Where the summed supply and demand of one interval's orders cross, with the fills to make there: the batch
        clearing rule, and in both clearing modes the interval's reported marginal price (settle, on the orders the
        window saw). None when the orders are all on one side.

        The orders' breakpoint prices form a ladder. Between two consecutive rungs every order's quantity is a straight
        line in the price (a continuous segment interpolates; anything else is constant or absent), so the net quantity
        N(p) = supply + demand is linear on each open interval; at a rung supply may jump up (a limit order, an
        all-or-nothing vertex) and just above it demand may drop off. N never decreases with the price. Walking up the
        ladder, the first of these to hold is the crossing:

        - N >= 0 at a rung: the price is the rung. Demand there fills in full. Supply is rationed: what each supply order
          already offered just below the rung is below the margin and fills in full; what it adds at the rung competes
          for the rest under the matching strategy (_sequential_fills, _proportional_fills), an all-or-nothing addition
          taken whole or not at all. This is what the ladder walk does at a rung where the resting side offers more than
          the aggressive order wants, applied once to the summed book.
        - N >= 0 just above a rung (demand drops off there): the price is the rung; supply fills in full and demand is
          rationed the same way, what each demand order still wants just above the rung being below the margin.
        - N > 0 just below the next rung: the crossing is strictly inside the segment, at the interpolated price, and
          every order fills its own quantity there: the base market's interpolation on the summed curve, and the ladder
          walk's linear standing-order case.

        Supply market orders exceeding all demand (N >= 0 at the bottom of the ladder) or demand market orders exceeding
        all supply (N < 0 at the top) are a surplus or deficit no price clears: the side that can trade fills in full,
        the market orders ration it and the rest of them stands, and the price is the end of the ladder nearest the
        crossing (the lowest or highest price any order names), as the base market prices an interval whose curve never
        crosses zero at the curve end nearest zero. When no order names a price at all the fills have none (an infinite
        price), which settle treats as an unpriced interval.
        """
        supply = [o for o in orders if o.supply_or_demand is SupplyOrDemand.SUPPLY]
        demand = [o for o in orders if o.supply_or_demand is SupplyOrDemand.DEMAND]
        if not supply or not demand:
            return None
        rungs = sorted({v.price for o in orders for v in o.vertices if math.isfinite(v.price)})
        tolerance = 1e-9 * max(1.0, sum(abs(v.quantity) for o in orders for v in o.vertices))
        ladder = [-INF, *rungs, INF]

        def quantity_at(order, price):
            return order.get_quantity_from_price(test_price=price).quantity

        def limits(low, high):
            """Every order's quantity just above `low` and just below `high`, consecutive rungs (the quantity is linear
            between them, so two probes inside the gap give both ends; outside the finite rungs it is constant)."""
            if math.isfinite(low) and math.isfinite(high):
                probes = (low + (high - low) / 3, low + 2 * (high - low) / 3)
            else:
                anchor = (high - 1.0) if math.isfinite(high) else (low + 1.0) if math.isfinite(low) else 0.0
                probes = (anchor, anchor)
            result = {}
            for order in orders:
                q1, q2 = quantity_at(order, probes[0]), quantity_at(order, probes[1])
                result[id(order)] = (2 * q1 - q2, 2 * q2 - q1) if probes[0] != probes[1] else (q1, q1)
            return result

        gaps = [limits(ladder[i], ladder[i + 1]) for i in range(len(ladder) - 1)]
        for i, rung in enumerate(ladder):
            above = gaps[i] if i < len(gaps) else None  # each order's quantity just above this rung
            if i == 0:
                if sum(q for q, _ in above.values()) >= -tolerance:
                    return self._end_crossing(rungs[0] if rungs else -INF, short=demand, long=supply,
                                              tolerance=tolerance, kind='surplus')
            elif above is None:
                return self._end_crossing(rungs[-1] if rungs else INF, short=supply, long=demand, tolerance=tolerance,
                                          kind='deficit')
            else:
                if sum(quantity_at(o, rung) for o in orders) >= -tolerance:
                    below = gaps[i - 1]  # supply's quantity just below the rung is below the margin
                    return self._rung_crossing(rung, short=demand, long=supply,
                                               infra={id(o): below[id(o)][1] for o in supply}, tolerance=tolerance)
                if sum(q for q, _ in above.values()) >= -tolerance:
                    return self._rung_crossing(rung, short=supply, long=demand,  # demand still wanted just above it
                                               infra={id(o): above[id(o)][0] for o in demand}, tolerance=tolerance)
            net_above = sum(q for q, _ in above.values())
            net_before_next = sum(q for _, q in above.values())
            if net_before_next > tolerance:  # the sides cross strictly inside this segment, where N is linear
                low, high = rung, ladder[i + 1]
                price = low + (high - low) * (-net_above) / (net_before_next - net_above)
                fills = [(o, quantity_at(o, price)) for o in orders]
                return Crossing(price, self._balance_fills(fills, price, tolerance), 'interior')
        return None  # not reached: the top of the ladder is a deficit when nothing before it crossed

    def _rung_crossing(self, price: float, short: list, long: list, infra: dict, tolerance: float,
                       kind: str = 'rung') -> 'Crossing':
        """
        The fills at a rung of the ladder: the short side in full at its quantity at the rung; of the long side, what
        each order offers beyond the rung (`infra`, by id: its quantity just below or just above it) in full, and what it
        adds at the rung rationed under the matching strategy for what the short side still takes.
        """
        fills = [(o, o.get_quantity_from_price(test_price=price).quantity) for o in short]
        taken = -sum(q for _, q in fills)  # signed as the long side
        increments, by_vertex, long_fills = [], {}, {}
        for order in long:
            here = order.get_quantity_from_price(test_price=price)
            long_fills[id(order)] = infra[id(order)]
            increment = here.quantity - infra[id(order)]
            if abs(increment) <= tolerance:
                continue
            if (increment > 0) != (taken > 0) and taken != 0.0:
                long_fills[id(order)] = here.quantity  # it offers less at the rung than beyond it: that much, in full
                continue
            vertex = Vertex(price=price, quantity=increment, continuity=here.continuity, curve=order)
            increments.append(vertex)
            by_vertex[id(vertex)] = order
        needed = taken - sum(long_fills.values())  # what the increments must supply, signed as the long side
        if increments and abs(needed) > tolerance and (needed > 0) == (taken > 0):
            increments.sort(key=self._priority_key())
            if self.matching_strategy and self.matching_strategy[0] is MatchingStrategy.PROPORTIONAL_VOLUME:
                planned = self._proportional_fills(-needed, increments)
            else:
                planned = self._sequential_fills(-needed, increments)
            for vertex, quantity in planned:
                long_fills[id(by_vertex[id(vertex)])] += quantity
        fills.extend((o, long_fills[id(o)]) for o in long)
        return Crossing(price, self._balance_fills(fills, price, tolerance), kind)

    def _end_crossing(self, price: float, short: list, long: list, tolerance: float, kind: str) -> 'Crossing':
        """A surplus or deficit: the short side fills in full at what it offers at the end of the ladder, the long
        side's market orders ration it (nothing else of the long side is in the money there), the rest stands."""
        market_orders = [o for o in long if all(not math.isfinite(v.price) for v in o.vertices)]
        others = [o for o in long if not any(o is m for m in market_orders)]
        end = -INF if kind == 'surplus' else INF
        infra = {id(o): 0.0 for o in long}
        infra.update({id(o): o.get_quantity_from_price(test_price=end).quantity for o in others})
        # The curves on the long side offer what they offer at the end (nothing, or the market orders would not be the
        # leftover); the market orders are the increments that compete.
        crossing = self._rung_crossing(end, short=short, long=long, infra=infra, tolerance=tolerance, kind=kind)
        return Crossing(price, crossing.fills, kind)

    def _balance_fills(self, fills: list, price: float, tolerance: float) -> list:
        """
        Make planned fills ([(order, signed quantity)]) sum to zero and drop the empty ones. Rationing at the margin can
        leave the two sides unequal when an all-or-nothing order cannot take its share: the excess is trimmed from the
        over-filled side's partially fillable orders, largest first, and when that side has none its smallest
        all-or-nothing fill is dropped and the sides compared again. Rounding dust lands on the largest fill.
        """
        fills = [(o, q) for o, q in fills if abs(q) > tolerance]
        while fills:
            net = sum(q for _, q in fills)
            if abs(net) <= tolerance:
                if net != 0.0:
                    i = max(range(len(fills)), key=lambda k: abs(fills[k][1]))
                    fills[i] = (fills[i][0], fills[i][1] - net)
                return [(o, q) for o, q in fills if q != 0.0]
            over = [i for i, (o, q) in enumerate(fills) if (q > 0) == (net > 0)]
            divisible = [i for i in over if fills[i][0].get_quantity_from_price(test_price=price).continuity]
            if divisible:
                for i in sorted(divisible, key=lambda k: -abs(fills[k][1])):
                    cut = net if abs(net) <= abs(fills[i][1]) else fills[i][1]
                    fills[i] = (fills[i][0], fills[i][1] - cut)
                    net -= cut
                    if abs(net) <= tolerance:
                        break
                fills = [(o, q) for o, q in fills if abs(q) > tolerance]
                continue
            del fills[min(over, key=lambda k: abs(fills[k][1]))]
        return []

    def _book_fills(self, fills: list, price: float) -> None:
        """
        Book the fills of a batch clearing at one price. The legs are paired across the sides (largest fills first) so
        that every leg has a counterparty, as a match's legs do; each filled order is superseded by its legs (one per
        counterparty, Order.match) and its remainder, which stands on, as _apply_fills books a match. Which order is
        paired with which does not change what anyone trades or pays: all legs are at the one price.
        """
        def rank(item):
            order, quantity = item
            return -abs(quantity), order.time_stamp, order.actor.name, str(order.id)

        supply = sorted([(o, q) for o, q in fills if q > 0], key=rank)
        demand = sorted([(o, -q) for o, q in fills if q < 0], key=rank)
        legs: dict[int, list] = {id(o): [] for o, _ in fills}
        dust = 1e-9 * max(1.0, sum(q for _, q in supply))
        i, j = 0, 0
        left_s, left_d = (supply[0][1] if supply else 0.0), (demand[0][1] if demand else 0.0)
        while i < len(supply) and j < len(demand):
            quantity = min(left_s, left_d)
            legs[id(supply[i][0])].append((demand[j][0], quantity))
            legs[id(demand[j][0])].append((supply[i][0], -quantity))
            left_s, left_d = left_s - quantity, left_d - quantity
            if left_s <= dust:
                i += 1
                left_s = supply[i][1] if i < len(supply) else 0.0
            if left_d <= dust:
                j += 1
                left_d = demand[j][1] if j < len(demand) else 0.0
        for order, total in fills:
            pairs = legs[id(order)]
            if pairs:  # the last leg absorbs the pairing's rounding, so the legs sum to the fill exactly
                counterparty, last = pairs[-1]
                pairs[-1] = (counterparty, last + (total - sum(q for _, q in pairs)))
            for counterparty, quantity in pairs:
                leg, _ = order.match(quantity=quantity, match_price=price, counter_party=counterparty.actor)
                if leg and leg.order_or_none():
                    self.matchList.append(leg)
            remainder = order.remainder(total)
            self._supersede(order)
            if remainder and remainder.order_or_none():
                if remainder.status in (OrderStatus.NEW, OrderStatus.NEW_REVISED):
                    remainder.status = OrderStatus.STANDING_ORDER
                self.orderList.append(remainder)

    def get_order_by_id(self, an_id: int or None = None):
        """
        Return order object from among the orders in orderList or matchLIst that match a given ID.

        Parameters:
            an_id: (integer)

        Returns: (list) of Order objects or None
        """
        matched_orders = [
            x for x in self.orderList + self.matchList if x.id == an_id
        ]
        if matched_orders:
            return matched_orders[0]
        else:
            return None

    @property
    def average_match_prices(self) -> Measurement:
        """Per interval, the volume-weighted average price of the interval's priced legs [$/kWh], recorded for
        reporting beside the marginal price. It is what the interval's trades cost on average, not a marginal price: in
        batch mode every leg is at the crossing price and the two coincide; in continuous mode the legs are at the
        resting orders' prices and they differ. Held in the market's BlendedPrice measurement."""
        return self.blended_prices1

    def last_fill_price(self, time_interval) -> float | None:
        """The price of the interval's last priced leg [$/kWh], or None without one: the marginal trade of a continuous
        book, kept for reporting. It is not the interval's marginal price (see settle) and is not archived."""
        priced = [leg for leg in self.matchList if leg.interval.name == time_interval.name
                  and math.isfinite(leg.vertices[-1].price)]
        return priced[-1].vertices[-1].price if priced else None

    def _legs_by_interval(self) -> dict[str, list[Order]]:
        legs: dict[str, list[Order]] = {}
        for leg in self.matchList:
            legs.setdefault(leg.interval.name, []).append(leg)
        return legs

    @staticmethod
    def _leg_quantity(leg: Order) -> float:
        """The signed quantity a matched leg transacted (supply positive, demand negative): its last vertex."""
        return leg.vertices[-1].quantity

    def settle(self) -> None:
        """
        Turn the matched legs into the market's result, per interval. Each entity's scheduled power is the signed sum
        of its matched legs (supply positive, demand negative; unmatched remainders did not trade), stored through the
        cleared-power store so that Entity.market_power returns it and the entities schedule from it as they do after
        an auction clears. The interval's marginal price is where the supply and demand the window saw cross
        (crossing, on window_orders: every order in its original quantities and prices, filled, standing, expired or
        cancelled), in both clearing modes: in batch mode that is the price every leg was made at; in continuous mode
        the legs were made at the resting orders' prices and the crossing is what the same orders would have cleared at
        together, the last fill being available as last_fill_price. The volume-weighted average price of the legs is
        recorded beside it (average_match_prices). An interval with no match keeps its opening price and is left to
        its entities' curves; such intervals are logged, once, by name, as is one whose orders name no price (market
        orders met by market orders), whose matched quantities are scheduled at the opening price. The entities are
        then asked for their schedules as Auction.while_in_delivery_lead asks them.
        """
        entities = [*self.tn.local_assets, *self.tn.neighbors]
        legs_by_interval = self._legs_by_interval()
        unmatched, unpriced = [], []
        for time_interval in self.time_intervals:
            legs = legs_by_interval.get(time_interval.name, [])
            if not legs:
                unmatched.append(time_interval.name)
                continue
            crossing = self.crossing(self.window_orders(time_interval))
            priced = [leg for leg in legs if math.isfinite(leg.vertices[-1].price)]
            volume = sum(abs(self._leg_quantity(leg)) for leg in legs)
            if crossing is not None and math.isfinite(crossing.price):
                marginal_price = crossing.price
                self.marginal_prices.update(time_interval, marginal_price)
                self._own_prices.add(time_interval.start_time)  # not to be overwritten by a prior market's price
            else:
                unpriced.append(time_interval.name)
                marginal_price = self.marginal_prices.get_by_interval(time_interval).value
            if priced:
                priced_volume = sum(abs(self._leg_quantity(leg)) for leg in priced)
                average = sum(leg.vertices[-1].price * abs(self._leg_quantity(leg)) for leg in priced) / priced_volume
                self.average_match_prices.update(time_interval, average)
            else:
                average = float('nan')
            for entity in entities:
                sd_curve = entity.active_vertices.get_by_interval(time_interval)
                if sd_curve is None or sd_curve.empty:
                    continue  # never quoted, so it cannot have traded
                power = sum(self._leg_quantity(leg) for leg in legs
                            if leg.actor is not None and leg.actor.id == entity.id)
                self._cleared_powers[(entity.id, time_interval.name)] = (sd_curve, power)
            _log.debug(f'MARKET: {self.name} settled {time_interval.name}: {len(legs)} legs, {volume / 2:.4g} kW traded, '
                       f'marginal price {marginal_price:.6g}, average {average:.6g} $/kWh.')
        # Said once per interval and kind: a negotiating market settles on every balancing pass.
        unmatched = [name for name in unmatched if ('unmatched', name) not in self._settlement_reported]
        unpriced = [name for name in unpriced if ('unpriced', name) not in self._settlement_reported]
        self._settlement_reported.update(('unmatched', name) for name in unmatched)
        self._settlement_reported.update(('unpriced', name) for name in unpriced)
        if unmatched:
            _log.warning(f'MARKET: {self.name} had no match in {len(unmatched)} of {len(self.time_intervals)} '
                         f'intervals; they keep their opening prices: {unmatched}.')
        if unpriced:
            _log.warning(f'MARKET: {self.name} saw no order naming a price in {len(unpriced)} of '
                         f'{len(self.time_intervals)} intervals (market orders met by market orders), which discovers '
                         f'no price; the matched quantities are scheduled at the opening prices: {unpriced}.')
        # Schedules follow the settlement: the assets' from the cleared powers where an interval settled (an interval
        # without a match keeps the schedule its curve gives at the opening price), the neighbors' through their own
        # schedule_power, which reads the same store.
        for local_asset in self.tn.local_assets:
            for time_interval in self.time_intervals:
                if self.cleared_power(local_asset, time_interval) is not None:
                    local_asset.scheduled_powers.update(time_interval, local_asset.market_power(self, time_interval))
        for neighbor in self.tn.neighbors:
            neighbor.schedule_power(self)

    def standing_market_orders(self, time_interval) -> list[Order]:
        """The standing orders of the interval that take any price (every vertex at an infinite price): inelastic
        quantities the book could not fill, which no price would have cleared."""
        return [order for order in self.orderList if order.interval.name == time_interval.name
                and order.vertices and all(not math.isfinite(v.price) for v in order.vertices)]

    def get_counteroffers(self, supply_or_demand: SupplyOrDemand or bool or None = SupplyOrDemand.SUPPLY,
                          time_interval=None):
        """
        Return a list of valid and eligible counteroffer Order objects from the market's orderList.

        Parameters:
            supply_or_demand: (SupplyOrDemand enumeration)
            time_interval: (TimeInterval or None) Only orders for this delivery interval; None means every interval.
                           An order trades only within its own interval, so matching always passes the new order's.

        Returns: (list) of Order objects or None
        """
        return [x for x in self.orderList if x.supply_or_demand == supply_or_demand and x.status in
                [OrderStatus.STANDING_ORDER, OrderStatus.REVISED_STANDING_ORDER]
                and (time_interval is None or x.interval.name == time_interval.name)]

    def get_counteroffer_vertices(self, price: float, supply_or_demand: SupplyOrDemand or bool or None = None,
                                  continuity: bool or None = None, vertex_type: VTypes or None = None,
                                  time_interval=None):
        """
        Returns a qualified list of Vertex counteroffer objects for this Order at the provided price.

        Parameters:
          price: (float) [$/kWh]
          supply_or_demand: (SupplyOrDemand enumeration) Indicates whether supply or demand Orders are to be
                            gathered.
          continuity: (bool) True means that Vertices are gathered for only those Order objects that allow partial
                      matching near the given price; False means that Vertices are gathered for only those Order objects
                      that disallow partial matching near the given price; None means that all Vertex objects are
                      gathered, regardless of their continuity.
          vertex_type: (VTypes or None)

        Returns: (list) of Vertex objects
        """
        counteroffers: list = self.get_counteroffers(supply_or_demand=supply_or_demand, time_interval=time_interval)
        vertices: list = []
        for co in counteroffers:
            pp: Vertex = co.get_quantity_from_price(test_price=price)
            if abs(pp.quantity) > 0.0:
                vertices.append(pp)
        if continuity is None:
            pass
        elif continuity:
            vertices = [x for x in vertices if x.continuity]
        elif not continuity:
            vertices = [x for x in vertices if not x.continuity]
        if vertex_type is None:
            pass
        else:
            vertices = [x for x in vertices if x.vType == vertex_type]
        return vertices

    def get_market_orders_by_actor(self, an_actor, role: str = Role.ACTOR.name):
        """
        Returns a list of matched Order objects for this actor in this market period.

        Parameters:
            an_actor: Neighbor or LocalAsset or None.
            role: (Role.name) The default role is actor, the originator of the Order object.

        Returns: list of matched Order objects

        Notes:
        - Assign actor as myTransactiveNode if you wish to retrieve the position of the local agent that owns this
          market.
        """
        role = role.upper()
        if role in [x.name for x in Role if x.value == Role.BUYER.value]:
            return [x for x in self.matchList if x.buyer == an_actor]
        elif role in [x.name for x in Role if x.value == Role.SELLER.value]:
            return [x for x in self.matchList if x.seller == an_actor]
        else:
            return [x for x in self.matchList if x.actor == an_actor]

    # ------------------------------------------------------------------------------------------- fill planning
    def _priority_key(self):
        """
        Sort key for the standing-order vertices competing at one price. The configured strategies rank them in order
        (ORDER_AGE: oldest first; ORDER_VOLUME and PROPORTIONAL_VOLUME: largest first; NONE: no ranking); all-or-nothing
        vertices then sort before partially fillable ones of equal rank, so whole fills are taken from orders that
        cannot accept a partial one and the partial fill falls on an order that can; the unused ranking breaks any
        remaining ties, and the actor's name the last of them, so that the outcome never depends on the order the
        orders were booked in (a batch clearing must not).
        """
        by_age = lambda so: so.curve.time_stamp
        by_volume = lambda so: -abs(so.quantity)
        ranks = []
        for strategy in self.matching_strategy:
            if strategy is MatchingStrategy.ORDER_AGE:
                ranks.append(by_age)
            elif strategy in (MatchingStrategy.ORDER_VOLUME, MatchingStrategy.PROPORTIONAL_VOLUME):
                ranks.append(by_volume)
            elif strategy is not MatchingStrategy.NONE:
                raise ValueError(f'Unrecognised matching strategy: {strategy!r}')
        ranks.append(lambda so: so.continuity)
        ranks.extend(rank for rank in (by_volume, by_age) if rank not in ranks)
        ranks.append(lambda so: so.curve.actor.name)
        return lambda so: tuple(rank(so) for rank in ranks)

    @staticmethod
    def _sequential_fills(needed: float, standing: list) -> list:
        """
        Plan fills by walking the standing-order vertices in priority order: each is taken in full while it fits in what
        is still needed, and the first partially fillable one that does not fit is taken for the remainder. Returns
        [(vertex, quantity)] with quantities signed as the standing side (opposite to needed).
        """
        fills, remainder = [], needed
        for so in standing:
            if remainder == 0.0:
                break
            if abs(remainder) < abs(so.quantity):
                if so.continuity:
                    fills.append((so, -remainder))
                    remainder = 0.0
            else:
                fills.append((so, so.quantity))
                remainder += so.quantity
        return fills

    @staticmethod
    def _proportional_fills(needed: float, standing: list) -> list:
        """
        Plan fills for PROPORTIONAL_VOLUME: while the partially fillable vertices together cannot cover what is still
        needed, whole vertices are taken in priority (largest first) order; as soon as they can, the remainder is split
        among them in proportion to their quantities at this price. Returns [(vertex, quantity)] as _sequential_fills.
        """
        fills, remainder, pending = [], needed, list(standing)
        while pending and remainder != 0.0:
            partial = [so for so in pending if so.continuity]
            partial_total = sum(so.quantity for so in partial)
            if partial and abs(remainder) <= abs(partial_total):
                allocated = 0.0
                for so in partial[:-1]:
                    share = -remainder * so.quantity / partial_total
                    fills.append((so, share))
                    allocated += share
                fills.append((partial[-1], -remainder - allocated))  # the last share absorbs any rounding residual
                remainder = 0.0
                break
            so = pending.pop(0)
            if abs(so.quantity) <= abs(remainder):
                fills.append((so, so.quantity))
                remainder += so.quantity
            # Otherwise the vertex is all-or-nothing and larger than the remainder, so it cannot be used (a partially
            # fillable vertex that large would have made partial_total sufficient).
        return fills

    def _supersede(self, order: Order) -> None:
        """Retire an order whose matched and remainder legs are booked (or which was replaced): it leaves the book."""
        order.status = OrderStatus.SUPERSEDED
        self.orderList = [x for x in self.orderList if x is not order]

    def _fill_price(self, aggressive_order: Order, matched_quantity: float, level: float, fills: list) -> float:
        """
        The price a fill is made at. Fills happen at a level of the price ladder, and a finite level is the price. The
        infinite level is where market orders rest (they take any price), and a fill made there is priced by the side
        that has a curve: the aggressive order's price for the matched quantity (the infinite level is the first rung,
        so nothing of it has matched before). When the aggressive order is a market order too, no participant forms a
        price: the fill keeps the infinite level, which settlement treats as an unpriced match. The fill is logged at
        DEBUG here and the pass's unpriced fills are reported in one line by report_unpriced_fills (a node whose local
        assets are all inelastic matches them against each other in every interval of every market, so a warning per
        interval said the same thing hundreds of times).
        """
        if math.isfinite(level):
            return level
        price = aggressive_order.price_from_vertices(matched_quantity)
        if math.isfinite(price):
            return price
        interval_name = aggressive_order.interval.name
        resting = tuple(sorted({self.get_order_by_id(an_id=so.curve.id).actor.name for so, _ in fills}))
        self._unpriced_fills.setdefault(interval_name, (aggressive_order.actor.name, resting))
        _log.debug(f'MARKET: {self.name}: in {interval_name} the market order of {aggressive_order.actor.name} is filled '
                   f'by the market orders of {list(resting)}; neither side names a price, so the match has none.')
        return level

    def _apply_fills(self, aggressive_order: Order, ao: Vertex, fills: list, price: float):
        """
        Match the aggressive order against planned fills of standing orders at one price.

        Parameters:
            aggressive_order: (Order) The order being matched, in its current (possibly already partly matched) form.
            ao: (Vertex) The aggressive order's quantity and continuity at this price.
            fills: (list) of (standing-order Vertex at this price, quantity to take from it), from a fill planner.
            price: (float) [$/kWh] The price at which these matches are made.

        Returns: (Order or None) The aggressive order's remainder to carry to later prices, the unchanged aggressive
                 order if nothing could be committed, or None when it is fully matched.

        The standing-order legs are computed first without touching the book. If the aggressive order would be left
        with a remainder it cannot accept (it is all-or-nothing at this price), nothing is committed and the book is
        unchanged. Otherwise every filled standing order is superseded by its matched and remainder legs, the
        aggressive order is superseded by its matched legs (one per counterparty), and its remainder is returned.
        """
        if not fills:
            return aggressive_order  # nothing at this price can be taken
        matched_quantity = -sum(quantity for _, quantity in fills)
        unmatched_quantity = ao.quantity - matched_quantity
        price = self._fill_price(aggressive_order, matched_quantity, price, fills)
        tentative = []
        for so, quantity in fills:
            standing_order: Order = self.get_order_by_id(an_id=so.curve.id)
            children_before = len(standing_order.child_order_ids)
            matched_order, unmatched_order = standing_order.match(quantity=quantity, match_price=price,
                                                                  counter_party=aggressive_order.actor)
            tentative.append((standing_order, children_before, quantity, matched_order, unmatched_order))
        if unmatched_quantity != 0.0 and not ao.continuity:
            for standing_order, children_before, _, _, _ in tentative:
                del standing_order.child_order_ids[children_before:]  # these legs are never booked
            return aggressive_order
        for standing_order, _, quantity, matched_order, unmatched_order in tentative:
            self._supersede(standing_order)
            if matched_order and matched_order.order_or_none():
                self.matchList.append(matched_order)
                aggressive_leg, _ = aggressive_order.match(quantity=-quantity, match_price=price,
                                                           counter_party=standing_order.actor)
                self.matchList.append(aggressive_leg)
            if unmatched_order and unmatched_order.order_or_none():
                self.orderList.append(unmatched_order)
        remainder = aggressive_order.remainder(matched_quantity) if matched_quantity != 0.0 else None
        self._supersede(aggressive_order)
        return remainder if remainder and remainder.order_or_none() else None

