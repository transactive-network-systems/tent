from tent.market import Market
from tent.containers.interval_value import IntervalValue
from enum import Enum
from tent.enumerations.market_types import MarketTypes
from datetime import datetime, timedelta
from tent.enumerations.market_state import MarketState
from tent.transactive_node import TransactiveNode
from tent.containers.order import Order, OrderStatus, SupplyOrDemand, Vertex, VTypes
from tent.local_asset import LocalAsset
from copy import copy


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


# The following constants may be assigned here and will affect instantiated MatchingEngine objects.
MATCHING_STRATEGY: list = [MatchingStrategy.ORDER_VOLUME]  # order of matching strategies - See Enum MatchingStrategy.
HAS_MARKET: bool = True  # set True if this agent performs market matching
MARKET_SERIES_NAME: str = "Matching_Engine"
NEGOTIATION_LEAD_TIME: timedelta = timedelta(hours=24.0)  # time prior to the delivery time with orders become permitted
DELIVERY_LEAD_TIME: timedelta = timedelta(minutes=15.0)  # time prior to deliver that orders become disallowed
INTERVAL_DURATION: timedelta = timedelta(hours=1.0)  # duration of delivery period


class MatchingEngine(Market):
    """
    A MatchingEngine is a Market child class that facilitates a price discovery process much like that used for stock
    trading. Typically, a market or limit order is matched from among standing orders in an "order book." New orders
    can be formulated, sent, and received asynchronously at any time within the Negotiation market state.

    Protected Parameters: (Cannot be altered after object initialization)
        matching_strategy: (MatchingStrategy) See enumeration MatchingStrategy.
        has_market: (boolean) True if this TransactiveNode hosts a MatchingEngine marketplace.
        market_series_name: (string)
        negotiation_lead_time: (timedelta) Duration of Negotiation market state.
        delivery_lead_time: (timedelta) Duration of time between Negotiation stop and Delivery start.
        interval_duration: (timedelta) Duration of market time interval.
        delivery_start_time: (datetime) Time and data tha Delivery market state begins.
        prior_market_in_series: (Market) Market object that precedes this one.

    Attributes:
        See Note [1].
        orderList: (list or None) of Order objects aka an "order book"
        matchList: (list or None) of Order objects that have been matched, transacted
        __matchingStrategy__: (MatchingStrategy enumeration)
        __hasMarket__: (boolean)
        __deliveryStartTime__: (datetime)
        __priorMarketInSeries__: (Market object)
        __nextMarketClearingTime__: (datetime) Scheduled "clearing time" of the next market. See Note [2].

    Returns: (MatchingEngine object)

    Notes:
    [1] This class inherits many attributes from base class Market. See base class Market for documentation.
    [2] A MatchingEngine market does not formally have a market clearing or market clearing time. However,
        the market clearing time refers to the end of the Negotiation period within its market state engine.
    - Protected properties may be read, but they should not be changeable after an object is initialized.
    """

    def __init__(
            self,
            matching_strategy: list or None = None,  # PROTECTED
            has_market: bool = HAS_MARKET,  # PROTECTED
            market_series_name=MARKET_SERIES_NAME,  # PROTECTED
            negotiation_lead_time: timedelta = NEGOTIATION_LEAD_TIME,  # PROTECTED. duration of negotiation period
            delivery_lead_time: timedelta = DELIVERY_LEAD_TIME,  # PROTECTED. time when negotiations stop prior to
            #                                                      delivery
            interval_duration: timedelta = INTERVAL_DURATION,  # PROTECTED
            delivery_start_time=datetime.now().replace(  # PROTECTED
                hour=(datetime.now().hour + 1) % 24,
                minute=0, second=0, microsecond=0
            ),
            prior_market_in_series=None  # PROTECTED
    ):
        super(MatchingEngine, self).__init__(
            market_type=MarketTypes.matching_engine,
            activation_lead_time=timedelta(hours=0.0),
            delivery_lead_time=delivery_lead_time,
            initial_market_state=MarketState.Negotiation,
            interval_duration=interval_duration,
            intervals_to_clear=1,
            market_clearing_interval=interval_duration,
            market_clearing_time=delivery_start_time - delivery_lead_time,
            market_lead_time=timedelta(hours=0.0),
            market_order=2,  # Presume the matching engine follows a subscription
            market_series_name=market_series_name,
            market_to_be_refined="Subscription_Market",
            negotiation_lead_time=negotiation_lead_time,
            prior_market_in_series=prior_market_in_series
        )
        self.orderList: list or Order or IntervalValue = []  # List of standing orders not yet matched
        self.matchList: list or Order or IntervalValue = []  # List of orders successfully matched
        if matching_strategy:
            self.__matchingStrategy__: list = matching_strategy
        else:
            self.__matchingStrategy__: list = list(MATCHING_STRATEGY)
        self.__hasMarket__: bool = has_market
        self.isNewestMarket: bool = True
        self.__deliveryStartTime__ = delivery_start_time
        self._stateIsCompleted: bool = False
        self.__priorMarketInSeries__: Market or None = prior_market_in_series
        self.__nextMarketClearingTime__: datetime = self.marketClearingTime + self.marketClearingInterval

    @property
    def matchingStrategy(self):
        return self.__matchingStrategy__

    @matchingStrategy.setter
    def matchingStrategy(self, _):
        pass

    @property
    def hasMarket(self):
        return self.__hasMarket__

    @hasMarket.setter
    def hasMarket(self, _):
        pass

    @property
    def deliveryStartTime(self):
        return self.__deliveryStartTime__

    @deliveryStartTime.setter
    def deliveryStartTime(self, _):
        pass

    @property
    def nextMarketClearingTime(self):
        return self.__nextMarketClearingTime__

    @nextMarketClearingTime.setter
    def nextMarketClearingTime(self, _):
        pass

    @property
    def priorMarketInSeries(self):
        return self.__priorMarketInSeries__

    @priorMarketInSeries.setter
    def priorMarketInSeries(self, _):
        pass

    def match(self, new_order: Order or None = None):

        # Perform qualifications
        if not new_order:
            return None

        # Get eligible standing orders from the order book.
        if new_order.supplyOrDemand == SupplyOrDemand.SUPPLY:
            standing_orders: list = self.get_counteroffers(supply_or_demand=SupplyOrDemand.DEMAND)
        elif new_order.supplyOrDemand == SupplyOrDemand.DEMAND:
            standing_orders: list = self.get_counteroffers(supply_or_demand=SupplyOrDemand.SUPPLY)
        else:
            raise RuntimeError("There should be no case where the new order is neither supply nor demand.")

        # Get Vertex prices
        prices: list = Order.get_prices_from_orders(
            order_list=standing_orders + [new_order]
        )

        # Make sure price is a unique list that includes either -INF or INF, but not both
        if new_order.supplyOrDemand == SupplyOrDemand.SUPPLY:
            prices.append(INF)
            prices = [x for x in prices if x != -INF]
            prices = list(set(prices))
            prices.sort(reverse=True)
        elif new_order.supplyOrDemand == SupplyOrDemand.DEMAND:
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
            if new_order and new_order.supplyOrDemand == SupplyOrDemand.DEMAND:
                limit_orders: list = self.get_counteroffer_vertices(
                    price=price,
                    supply_or_demand=SupplyOrDemand.SUPPLY,
                    vertex_type=VTypes.LIMIT_ORDER
                )

            elif new_order and new_order.supplyOrDemand == SupplyOrDemand.SUPPLY:
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
                        so_order: Order = self.get_order_by_id(an_id=so.orderId)

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
                for mp in self.matchingStrategy:

                    # CASE: Matching priority is by proportion
                    if mp == MatchingStrategy.PROPORTIONAL_VOLUME:
                        pass
                        #  Continue to next matching priority (I't not clear how to make this work. Create warning.)

                    #  CASE: Matching priority is age
                    elif mp == MatchingStrategy.ORDER_AGE:
                        # Sort active so limit order quantities by age.
                        limit_orders.sort(key=lambda x: (x.timeStamp, x.continuity, -abs(x.quantity)))

                        # Insert <Code Logic Block A>
                        new_order = self.limit_order_code_block(
                            aggressive_order=new_order,
                            list_of_orders=limit_orders,
                            price=price
                        )

                    #  CASE: Matching priority is volume (the default priority)
                    elif mp == MatchingStrategy.ORDER_VOLUME:

                        # Sort active so limit order quantities by volume
                        limit_orders.sort(key=lambda x: (-abs(x.quantity), x.continuity, x.timeStamp))

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

    def spawn_markets(self, my_transactive_node: TransactiveNode, new_market_clearing_time: datetime):
        """
        Creates a MatchingEngine market object as needed for each market delivery period. The new market is appended to
        the list of market objects owned by myTransactiveNode. Any orders placed in the corresponding new market's
        delivery period should reference this new market object.

        Parameters:
            my_transactive_node: (TransactiveNode)
            new_market_clearing_time: (datetime)

        Returns: None
        """
        new_market = MatchingEngine(
            delivery_lead_time=self.deliveryLeadTime,
            delivery_start_time=new_market_clearing_time + self.deliveryLeadTime,
            has_market=True,
            interval_duration=self.intervalDuration,
            market_series_name=self.marketSeriesName,
            matching_strategy=self.matchingStrategy,
            # name=self.name,
            negotiation_lead_time=self.negotiationLeadTime,
            prior_market_in_series=self
        )
        self.isNewestMarket: bool = False
        new_market.isNewestMarket = True
        my_transactive_node.markets.append(
            new_market
        )
        return None

    def while_in_negotiation(self, my_transactive_node):
        """
        In this market state, the transactive agent may prepare and submit from zero to many offer or bid orders to
        secure a position in this market object's delivery period. This state ends after the negotiation period, which
        should be a delivery delay period prior to the delivery period.

        Parameters:
            my_transactive_node: (TransactiveNOde)

        Returns: None

        Notes:
        - The transition out of the negotiation state corresponds to the assigned market clearing time which is not
          truly used by the market matching engine.
        """
        # Tend to any new aggressive orders that have been received.
        # NOTE: In some instances, a new order may have been revised.
        new_orders = [
            x for x in self.orderList if x.status in [OrderStatus.NEW, OrderStatus.NEW_REVISED]
        ]
        for new_order in new_orders:
            self.match(
                new_order
            )
        # Terminate the state Negotiation at this market's "marketClearingTime."
        if datetime.now() >= self.marketClearingTime:
            self._stateIsCompleted = True
        return None

    def get_counteroffers(self, supply_or_demand: SupplyOrDemand or bool or None = SupplyOrDemand.SUPPLY):
        """
        Return a list of valid and eligible counteroffer Order objects from the market's orderList.

        Parameters:
            supply_or_demand: (SupplyOrDemand enumeration)

        Returns: (list) of Order objects or None
        """
        return [x for x in self.orderList if x.supplyOrDemand == supply_or_demand and x.status in
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

    def get_market_orders_by_actor(self, an_actor: TransactiveNode or LocalAsset, role: str = Role.ACTOR.name):
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
            return [x for x in self.matchList if x.actor == an_actor]
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
                an_id=so.orderId
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
                self.orderList = [x for x in self.orderList if x.id != tro.parentOrderId]

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
