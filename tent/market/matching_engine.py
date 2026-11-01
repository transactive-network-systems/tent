import logging
import math

from copy import copy
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum

from pydantic import field_validator

from tent.config import ConfigModel, PositiveSeconds, Setting, enum_by_name
from tent.containers.measurement import Measurement
from tent.containers.order import Order, OrderStatus, SupplyOrDemand, Vertex, VTypes
from tent.enumerations import MarketTypes, MeasurementType, MeasurementUnit
from tent.market import Market
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


# TODO: Make sure that IDs can work as UUID, instead of int.
class MatchingEngine(Market):
    """
    A MatchingEngine is a Market child class that facilitates a price discovery process much like that used for stock
    trading. Typically, a market or limit order is matched from among standing orders in an "order book." New orders
    can be formulated, sent, and received asynchronously at any time within the Negotiation market state.

    Construction, timing, and the market lifecycle are inherited from Market and driven by the owning MarketSeries,
    exactly as for Auction: configure a MarketSeries with ``market_class`` set to this class. Extra keywords on the
    series configuration are forwarded to every spawned market, so ``matching_strategy`` is configured there, e.g.
    ``"matching_strategy": ["ORDER_VOLUME", "ORDER_AGE"]`` (names, values, or MatchingStrategy members).

    Orders enter the book in two ways. Any caller may submit_order() a ready-made Order. In addition, on every pass of
    while_in_negotiation() the engine quotes the local system itself: each local asset's and neighbor's active_vertices
    curve for each interval is turned into orders (Order.from_curve), so assets' scheduled flexibility and neighbors'
    received transactive signals are offered into the book without those classes knowing about orders. A quote is
    revised only when its curve changes, detected by the curve's archived version (SDCurve.last_record_time) or, for
    curves that are never archived, by their vertex values.

    Attributes:
        orderList: (list) of standing Order objects, aka the "order book".
        matchList: (list) of Order objects that have been matched (transacted).
        matching_strategy: (list of MatchingStrategy) Tie-break order for same-price orders.
        order_expiry: (timedelta | None) Lifetime of quoted orders; None means they expire at market clearing.
        order_measurement: (Measurement) Archival header under which every Order of this market is recorded.
        quotes: (dict) (entity id, interval id) -> Quote for the orders the engine has placed on entities' behalf.

    Notes:
    - A MatchingEngine market does not formally have a market clearing or market clearing time. The clearing time
      inherited from Market marks the end of the Negotiation period within its market state engine.
    """
    MARKET_TYPE = MarketTypes.matching_engine

    class Config(Market.Config):
        matching_strategy: list[enum_by_name(MatchingStrategy)] = Setting(
            ['ORDER_VOLUME'], min_length=1,
            description='Tie-break order for same-price orders, by name: NONE, ORDER_AGE, ORDER_VOLUME, '
                        'PROPORTIONAL_VOLUME. A single name is accepted as a list of one.')
        order_expiry: PositiveSeconds | None = Setting(
            None, reconfigurable=True,
            description='[s] lifetime of the orders the engine quotes for local entities; null means they stand until '
                        'the market clears.')

        @field_validator('matching_strategy', mode='before')
        @classmethod
        def _one_strategy_is_a_list(cls, value):
            return [value] if isinstance(value, (str, MatchingStrategy)) else value

    def __init__(self, *args, config: ConfigModel | None = None, **kwargs):
        runtime, settings = self.split_keywords(kwargs)
        cfg = self._configure(config, settings)
        # Recorded by get_record(), so it must exist before Market.__init__ archives the market row.
        self.matching_strategy: list[MatchingStrategy] = list(cfg.matching_strategy)
        super(MatchingEngine, self).__init__(*args, config=cfg, **runtime)
        self.order_measurement: Measurement = Measurement(self, MeasurementType.Order, MeasurementUnit.kW)
        self.orderList: list[Order] = []  # List of standing orders not yet matched
        self.matchList: list[Order] = []  # List of orders successfully matched
        self.order_expiry: timedelta | None = cfg.order_expiry
        self.quotes: dict[tuple, Quote] = {}
        self._unpriced_fills_reported: set[str] = set()  # intervals where market orders met market orders (see _fill_price)

    @property
    def table_name(self):
        return 'matching_engine'

    def get_record(self) -> dict:
        record = super(MatchingEngine, self).get_record()
        record.update({'matching_strategy': [strategy.name for strategy in self.matching_strategy]})
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(MatchingEngine, cls).get_archive_definitions()
        definitions.extend(Order.get_archive_definitions())  # the order book's rows
        definitions.extend([
            {
                "name": "matching_engine",
                "inherits": "market",
                "columns": [
                    {"name": "id", "foreign_key": "market.id", "kwargs": {"primary_key": True}},
                    {"name": "matching_strategy", "type": "JSON"}  # ordered list of MatchingStrategy names
                ]
            }
        ])
        return definitions

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
            self._archive_orders([order])

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
        self.quotes = {key: quote for key, quote in self.quotes.items()
                       if not any(o.id in roots for o in quote.orders)}

    def _archive_orders(self, orders) -> None:
        for order in orders:
            if order is not None and order.measurement is not None:
                order.archive()

    def match(self, new_order: Order or None = None):
        """
        Match a new (aggressive) order against the order book, then archive the book changes: every order newly booked
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

            #  <LIMIT ORDER SO CASE> *********************************************************************************

            #  Get limit order quantities at this price
            if new_order and new_order.supply_or_demand == SupplyOrDemand.DEMAND:
                limit_orders: list = self.get_counteroffer_vertices(
                    price=price,
                    supply_or_demand=SupplyOrDemand.SUPPLY,
                    vertex_type=VTypes.LIMIT_ORDER,
                    time_interval=new_order.interval
                )

            elif new_order and new_order.supply_or_demand == SupplyOrDemand.SUPPLY:
                limit_orders: list = self.get_counteroffer_vertices(
                    price=price,
                    supply_or_demand=SupplyOrDemand.DEMAND,
                    vertex_type=VTypes.LIMIT_ORDER,
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
                continue

            #  CASE: ao quantity >= total limit so quantity
            elif abs(ao.quantity) >= abs(total_limit_order_quantity):
                # Every standing limit order at this price can be taken in full. _apply_fills commits nothing if the
                # aggressive order is all-or-nothing here and would be left with a remainder.
                new_order = self._apply_fills(new_order, ao, [(so, so.quantity) for so in limit_orders], price)

            #  CASE: ao quantity < total limit so quantity
            elif abs(ao.quantity) < abs(total_limit_order_quantity):
                # The standing limit orders at this price can more than fill the aggressive order: the matching strategy
                # decides which of them (and how much of each) are taken.
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

    def transition_from_active_to_negotiation(self):
        """Price discovery opens: every local asset schedules itself so that its curve can be quoted into the book (as
        an auction's assets do), then the inherited price refresh."""
        for local_asset in self.tn.local_assets:
            local_asset.schedule(self)
        super(MatchingEngine, self).transition_from_active_to_negotiation()

    def transition_from_negotiation_to_market_lead(self):
        """The book closes: settle the matched legs into prices and schedules, then the inherited price refresh for the
        intervals settlement did not price."""
        self.settle()
        super(MatchingEngine, self).transition_from_negotiation_to_market_lead()

    # ------------------------------------------------------------------------------------------------ settlement
    @property
    def average_match_prices(self) -> Measurement:
        """Per interval, the volume-weighted average price of the interval's matches [$/kWh], recorded for reporting
        beside the marginal price (the price of the last match). Held in the market's BlendedPrice measurement."""
        return self.blended_prices1

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
        an auction clears. The interval's marginal price is the price of the last match, the marginal trade; the
        volume-weighted average match price is recorded beside it (average_match_prices). An interval with no match
        keeps its opening price and is left to its entities' curves; such intervals are logged, once, by name. The
        entities are then asked for their schedules as Auction.while_in_delivery_lead asks them.
        """
        entities = [*self.tn.local_assets, *self.tn.neighbors]
        legs_by_interval = self._legs_by_interval()
        unmatched, unpriced = [], []
        for time_interval in self.time_intervals:
            legs = legs_by_interval.get(time_interval.name, [])
            if not legs:
                unmatched.append(time_interval.name)
                continue
            # A match made at an infinite price level (a market order met by another market order) discovers no price;
            # the priced legs do. An interval whose matches are all unpriced trades its quantities at the opening price.
            priced = [leg for leg in legs if math.isfinite(leg.vertices[-1].price)]
            volume = sum(abs(self._leg_quantity(leg)) for leg in legs)
            if priced:
                marginal_price = priced[-1].vertices[-1].price
                priced_volume = sum(abs(self._leg_quantity(leg)) for leg in priced)
                average = sum(leg.vertices[-1].price * abs(self._leg_quantity(leg)) for leg in priced) / priced_volume
                self.marginal_prices.update(time_interval, marginal_price)
                self._own_prices.add(time_interval.start_time)  # not to be overwritten by a prior market's price
                self.average_match_prices.update(time_interval, average)
            else:
                unpriced.append(time_interval.name)
                marginal_price = self.marginal_prices.get_by_interval(time_interval).value
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
        if unmatched:
            _log.warning(f'MARKET: {self.name} had no match in {len(unmatched)} of {len(self.time_intervals)} '
                         f'intervals; they keep their opening prices: {unmatched}.')
        if unpriced:
            _log.warning(f'MARKET: {self.name} matched only market orders against market orders in {len(unpriced)} of '
                         f'{len(self.time_intervals)} intervals, which discovers no price; the matched quantities are '
                         f'scheduled at the opening prices: {unpriced}.')
        # Schedules follow the settlement: the assets' from the cleared powers where an interval settled (an interval
        # without a match keeps the schedule its curve gives at the opening price), the neighbors' through their own
        # schedule_power, which reads the same store.
        for local_asset in self.tn.local_assets:
            for time_interval in self.time_intervals:
                if self.cleared_power(local_asset, time_interval) is not None:
                    local_asset.scheduled_powers.update(time_interval, local_asset.market_power(self, time_interval))
        for neighbor in self.tn.neighbors:
            neighbor.schedule_power(self)

    def while_in_negotiation(self):
        """
        In this market state, actors may submit from zero to many offer or bid orders to secure a position in this
        market object's delivery period. Each pass matches any new (or newly revised) orders against the order book.
        Negotiation stays open for its whole window so that orders submitted later are still matched; the state is
        marked complete at the market-lead time.
        """
        self._expire_orders()
        self.quote_entities()
        new_orders = [x for x in self.orderList if x.status in (OrderStatus.NEW, OrderStatus.NEW_REVISED)]
        for new_order in new_orders:
            self.match(new_order)
        # Order.match() removes every order it supersedes from the book and match() re-adds unmatched remainders as
        # standing orders, so after a pass the book should hold standing orders only. Anything else (e.g. a new order
        # that cancelled itself for having no quantity) is dropped here.
        self.orderList = [x for x in self.orderList
                          if x.status in (OrderStatus.STANDING_ORDER, OrderStatus.REVISED_STANDING_ORDER)]
        if Timer.now() >= self.transition_times.market_lead:
            self._stateIsCompleted = True
        return None

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
        remaining ties.
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
        price: the fill keeps the infinite level, which settlement treats as an unpriced match, and the orders are
        named in a warning once per interval.
        """
        if math.isfinite(level):
            return level
        price = aggressive_order.price_from_vertices(matched_quantity)
        if math.isfinite(price):
            return price
        interval_name = aggressive_order.interval.name
        if interval_name not in self._unpriced_fills_reported:
            self._unpriced_fills_reported.add(interval_name)
            resting = sorted({self.get_order_by_id(an_id=so.curve.id).actor.name for so, _ in fills})
            _log.warning(f'MARKET: {self.name}: in {interval_name} the market order of {aggressive_order.actor.name} is '
                         f'filled by the market orders of {resting}; neither side names a price, so the match has none.')
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
