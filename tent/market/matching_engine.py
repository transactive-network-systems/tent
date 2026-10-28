import logging

from copy import copy
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum

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

    def __init__(self, *args, matching_strategy: list | str | MatchingStrategy | None = None,
                 order_expiry: timedelta | float | None = None, **kwargs):
        # Recorded by get_record(), so it must exist before Market.__init__ archives the market row.
        self.matching_strategy: list[MatchingStrategy] = self._parse_matching_strategy(matching_strategy)
        super(MatchingEngine, self).__init__(*args, **kwargs)
        self.order_measurement: Measurement = Measurement(self, MeasurementType.Order, MeasurementUnit.kW)
        self.orderList: list[Order] = []  # List of standing orders not yet matched
        self.matchList: list[Order] = []  # List of orders successfully matched
        self.order_expiry: timedelta | None = (order_expiry if isinstance(order_expiry, timedelta) or order_expiry is None
                                               else timedelta(seconds=float(order_expiry)))
        self.quotes: dict[tuple, Quote] = {}

    @staticmethod
    def _parse_matching_strategy(spec) -> list[MatchingStrategy]:
        """Accept MatchingStrategy members, their names, or their values, singly or as a list; default if empty."""
        if spec is None or spec == []:
            return list(MATCHING_STRATEGY)
        if isinstance(spec, (str, int, MatchingStrategy)):
            spec = [spec]
        strategies = []
        for item in spec:
            if isinstance(item, MatchingStrategy):
                strategies.append(item)
            elif isinstance(item, int):
                strategies.append(MatchingStrategy(item))
            elif isinstance(item, str):
                strategies.append(MatchingStrategy[item.upper()])
            else:
                raise TypeError(f'Unrecognised matching strategy: {item!r}')
        return strategies

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

        # Get eligible standing orders from the order book.
        if new_order.supply_or_demand == SupplyOrDemand.SUPPLY:
            standing_orders: list = self.get_counteroffers(supply_or_demand=SupplyOrDemand.DEMAND)
        elif new_order.supply_or_demand == SupplyOrDemand.DEMAND:
            standing_orders: list = self.get_counteroffers(supply_or_demand=SupplyOrDemand.SUPPLY)
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
                    vertex_type=VTypes.LIMIT_ORDER
                )

            elif new_order and new_order.supply_or_demand == SupplyOrDemand.SUPPLY:
                limit_orders: list = self.get_counteroffer_vertices(
                    price=price,
                    supply_or_demand=SupplyOrDemand.DEMAND,
                    vertex_type=VTypes.LIMIT_ORDER
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

                #  CASE: ao quantity is not continuous
                if not ao.continuity:
                    continue

                #  CASE: ao quantity is continuous
                elif ao.continuity:

                    # Initialize the matched and unmatched ao quantities.
                    matched_quantity = 0.0
                    unmatched_quantity = ao.quantity

                    #  Index through so limit order quantities
                    for so in limit_orders:

                        # Find the standing order for this limit order Vertex.
                        so_order: Order = self.get_order_by_id(an_id=so.curve.id)

                        # Create and matched and unmatched orders for so quantity at indexed price.
                        matched_order, unmatched_order = so_order.match(
                            quantity=so.quantity,
                            match_price=price,
                            counter_party=new_order.actor
                        )

                        # If a match transaction completed save the matched so order and revise the standing order.
                        if matched_order:
                            self.matchList.append(matched_order)
                            self.orderList = [x for x in self.orderList if x != so_order]

                        # Revise standing so order. (there should be no remainder of so quantity)
                        if unmatched_order and unmatched_order.order_or_none():
                            self.orderList.append(unmatched_order)

                        # Create match order for matched ao quantity (order identifies buyer and seller)
                        matched_order, unmatched_order = new_order.match(
                            quantity=-so.quantity,
                            match_price=price,
                            counter_party=so_order.actor
                        )

                        if matched_order and matched_order.order_or_none():
                            self.matchList.append(matched_order)

                        # Update total matched, transacted ao quantity and unmatched ao quantity remainder
                        matched_quantity = matched_quantity - so.quantity
                        unmatched_quantity = unmatched_quantity + so.quantity

                    # Revise ao order using total transacted and remainder ao quantities
                    matched_order, unmatched_order = new_order.match(
                        quantity=matched_quantity,
                        match_price=price,
                        counter_party=None
                    )

                    if unmatched_order and unmatched_order.order_or_none():
                        self.orderList = [x for x in self.orderList if x != new_order]
                        new_order = copy(new_order)
                        new_order.vertices = unmatched_order.vertices
                    else:
                        new_order = None

                else:
                    raise RuntimeError("Logic should not have reached this place in the code.")

            #  CASE: ao quantity < total limit so quantity
            elif abs(ao.quantity) < abs(total_limit_order_quantity):

                # Index through list of matching priorities
                for mp in self.matching_strategy:

                    # CASE: Matching priority is by proportion
                    if mp == MatchingStrategy.PROPORTIONAL_VOLUME:
                        pass
                        #  Continue to next matching priority (I't not clear how to make this work. Create warning.)

                    #  CASE: Matching priority is age
                    elif mp == MatchingStrategy.ORDER_AGE:
                        # Sort active so limit order quantities by age.
                        limit_orders.sort(key=lambda x: (x.curve.time_stamp, x.continuity, -abs(x.quantity)))

                        # Insert <Code Logic Block A>
                        new_order = self.limit_order_code_block(
                            aggressive_order=new_order,
                            list_of_orders=limit_orders,
                            price=price
                        )

                    #  CASE: Matching priority is volume (the default priority)
                    elif mp == MatchingStrategy.ORDER_VOLUME:

                        # Sort active so limit order quantities by volume
                        limit_orders.sort(key=lambda x: (-abs(x.quantity), x.continuity, x.curve.time_stamp))

                        # Insert <Code Logic Block A>
                        new_order = self.limit_order_code_block(
                            aggressive_order=new_order,
                            list_of_orders=limit_orders,
                            price=price
                        )

                    else:
                        raise RuntimeError("Logic should not have reached this line.")

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

    def order_id_is_unique(self, an_id: int or None = None):
        """
        Returns True if ID parameter is provided and the ID unique among those in this market's matched or standing
        orders.

        Parameters:
            an_id: (integer)

        Returns: None
        """
        if an_id:
            return not any(
                [x.id == an_id for x in self.orderList + self.matchList]
            )
        else:
            return None

    def while_in_negotiation(self):
        """
        In this market state, actors may submit from zero to many offer or bid orders to secure a position in this
        market object's delivery period. Each pass matches any new (or newly revised) orders against the order book.
        The state ends on the negotiation timing inherited from Market; there is nothing further for this market to
        complete, so the state is marked complete as soon as the book has no unprocessed orders.
        """
        self.quote_entities()
        new_orders = [x for x in self.orderList if x.status in (OrderStatus.NEW, OrderStatus.NEW_REVISED)]
        for new_order in new_orders:
            self.match(new_order)
        # Order.match() removes every order it supersedes from the book and match() re-adds unmatched remainders as
        # standing orders, so after a pass the book should hold standing orders only. Anything else (e.g. a new order
        # that cancelled itself for having no quantity) is dropped here.
        self.orderList = [x for x in self.orderList
                          if x.status in (OrderStatus.STANDING_ORDER, OrderStatus.REVISED_STANDING_ORDER)]
        self._stateIsCompleted = True
        return None

    def get_counteroffers(self, supply_or_demand: SupplyOrDemand or bool or None = SupplyOrDemand.SUPPLY):
        """
        Return a list of valid and eligible counteroffer Order objects from the market's orderList.

        Parameters:
            supply_or_demand: (SupplyOrDemand enumeration)

        Returns: (list) of Order objects or None
        """
        return [x for x in self.orderList if x.supply_or_demand == supply_or_demand and x.status in
                [OrderStatus.STANDING_ORDER, OrderStatus.REVISED_STANDING_ORDER]]

    def get_counteroffer_vertices(self, price: float, supply_or_demand: SupplyOrDemand or bool or None = None,
                                  continuity: bool or None = None, vertex_type: VTypes or None = None):
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
        counteroffers: list = self.get_counteroffers(supply_or_demand=supply_or_demand)
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

    def limit_order_code_block(self, aggressive_order: Order, list_of_orders: list, price: float):
        """
        This is a block of code that is used during the comparison of aggressive order quantity with standing limit
        order quantities at a price.

        Parameters:
            aggressive_order: (Order) The current form of the order that is being matched. This order may be revised at
                              each inflection price as successful match transactions are made.
            list_of_orders: (list) of standing limit orders at this price.
            price: (float) [$/kWh_e] The price at which matches are currently being made. This price is a member of
                           a set of Vertex inflection points among all aggressive and standing orders.

        Returns: None

        Notes:
        - This block was made into a function to improve code readability. Additionally, it would have been written out
          at least twice otherwise, so it is less vulnerable to corruption as a functional code block.
        """
        # #### Begin Code Logic Block A #### ***********************************************************

        # Initialize matched and unmatched ao quantity for this price
        matched_quantity: float = 0.0
        ao = aggressive_order.get_quantity_from_price(test_price=price)
        unmatched_quantity: float = ao.quantity

        # Initialize lists of matched and unmatched order remainders.
        tentative_new_matches = []
        tentative_revised_orders = []

        # Index through sorted so quantities
        for so in list_of_orders:

            # CASE: There is no unmatched quantity remaining
            if unmatched_quantity == 0.0:
                break

            # Retrieve the standing order from this Vertex.
            standing_order: Order = self.get_order_by_id(
                an_id=so.curve.id
            )

            # CASE: ao remainder quantity < so quantity
            if abs(unmatched_quantity) < abs(so.quantity):

                # CASE: so quantity is not continuous
                if not so.continuity:
                    continue

                # CASE: so quantity is continuous
                elif so.continuity:

                    matched_order, unmatched_order = standing_order.match(
                        quantity=-unmatched_quantity,
                        match_price=price,
                        counter_party=aggressive_order.actor
                    )

                    # Record the tentative so and ao matches
                    if matched_order and matched_order.order_or_none():
                        tentative_new_matches.append(matched_order)

                    if unmatched_order and unmatched_order.order_or_none():
                        tentative_revised_orders.append(unmatched_order)

                    # Update matched quantity and quantity remainder (there should be no remainder)
                    matched_quantity = matched_quantity + unmatched_quantity
                    unmatched_quantity = 0.0

            # CASE: ao remainder quantity >= so quantity
            elif abs(unmatched_quantity) >= abs(so.quantity):

                matched_order, unmatched_order = standing_order.match(
                    quantity=so.quantity,
                    match_price=price,
                    counter_party=aggressive_order.actor
                )

                # Record the tentative so and ao matches
                if matched_order and matched_order.order_or_none():
                    tentative_new_matches.append(matched_order)

                if unmatched_order and unmatched_order.order_or_none():
                    tentative_revised_orders.append(unmatched_order)

                # Update matched quantity and unmatched remainder quantity
                matched_quantity = matched_quantity - so.quantity
                unmatched_quantity = unmatched_quantity + so.quantity

        # CASE: There is an unmatched ao remainder and ao continuity is false
        if unmatched_quantity != 0.0 and not ao.continuity:
            return aggressive_order

        # CASE: There is either no unmatched remainder, or ao continuity is true
        elif unmatched_quantity == 0.0 or ao.continuity:

            # Index through the tentative new matches
            for tnm in tentative_new_matches:
                #  Create and record match order for matched so quantity
                self.matchList.append(tnm)

                #  Create and record order for matched ao quantity
                matched_order, unmatched_order = aggressive_order.match(
                    quantity=-tnm.vertices[-1].quantity,
                    match_price=price,
                    counter_party=tnm.actor
                )

                self.matchList.append(matched_order)

            # Index through the tentative revised orders
            for tro in tentative_revised_orders:
                self.orderList.append(tro)
                self.orderList = [x for x in self.orderList if x.id != tro.parent_order_id]

            #  Create and record match order for matched so quantity
            # if matched_quantity != 0.0:
            matched_order, unmatched_order = aggressive_order.match(
                quantity=matched_quantity,
                match_price=price,
                counter_party=None
            )

            #  Revise new order for unmatched quantity
            if unmatched_order and unmatched_order.order_or_none():
                aggressive_order = copy(aggressive_order)
                aggressive_order.vertices = unmatched_order.vertices
            else:
                aggressive_order = None

            return aggressive_order
