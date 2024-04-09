from datetime import datetime, timedelta
from json import dumps, load
from tent.containers.time_interval import TimeInterval
from tent.neighbor import Neighbor
from enum import Enum
from tent.containers.vertex import Vertex as originalVertex  # Original Vertex class is extended here with same name.
from tent.local_asset import LocalAsset
from warnings import warn
from copy import copy


INF = float('inf')
EPS = 2.220446049250313e-16  # Floating point precision for 64-bit computation
OFFER_DURATION = timedelta(hours=24)  # Default time after which an Order object will expire if not otherwise specified


class Vertex(originalVertex):
    """
    Class Vertex is being extended here. This extended version makes it easier to initialize objects, and it is more
    useful for use with matching engine markets.

    Parameters:
        continuity (bool): Set True to indicate that quantity is defined between this Vertex and another nearest
            Vertex of the same Order object having less quantity magnitude. Defaults to True.
        price (float): [$/kWh] Price at which an inflection occurs in a piecewise linear supply or demand curve.
        cost (float): [$] Needed for historical inheritance from class Vertex. Defaults to 0.0 $.
        order_id (int or None): Should be set to the ID of the Order object to which this Vertex belongs.
            Defaults to None.
        quantity (float): [Avg. kW] The average power that, together with its price, defines an inflection in a
            supply of demand curve. Defaults to 0.0 avg. kW.
        timestamp (datetime): This should be updated by the market agent to reflect the time that the Vertex is
            received by the market agent. Defaults to datetime.datetime.now().

    Properties:
        continuity (bool): Assigned value of parameter "continuity."
        cost (float): [$] Assigned value of parameter "cost."
        marginalPrice (float): [$/kWh] Same as property "price."
        orderId (int or None): Assigned value of property "order_id."
        power (float): [kW] Same as property "quantity."
        powerUncertainty (float): See class Vertex.
        record (int): See class Vertex.
        timeStamp (datetime): Assigned value of parameter timestamp.

    Notes:
        Property "price" aliases the original Vertex property "marginalPrice."
        Property "quantity" aliases the original Vertex property "power."
        This redefined class may be invoked using this import statement "from order import Vertex".
    """
    def __init__(self, price: float, order_id: int or None = None, timestamp: datetime = datetime.now(),
                 continuity: bool = True, quantity: float = 0.0, cost: float = 0.0):
        self.marginalPrice = price
        self.power: float = quantity
        self.continuity: bool = continuity
        self.orderId: int = order_id
        self.timeStamp: datetime = timestamp
        # This next line causes the Vertex to inherit from the original Vertex class. Several Vertex attributes are
        # simplified or ignored.
        super(Vertex, self).__init__(
            marginal_price=self.price,
            prod_cost=cost,
            power=self.quantity,
            continuity=self.continuity,
            power_uncertainty=0.0,
            record=0
        )

    @property
    def price(self):
        # This method allows alias property "price" to return the value of Vertex property "marginalPrice".
        return self.marginalPrice

    @price.setter
    def price(self, inp):
        # This method allows alias property "price" to set the value of Vertex property "marginalPrice".
        self.marginalPrice = inp

    @property
    def quantity(self):
        # This method allows alias property "quantity" to return the value of Vertex property "power".
        return self.power

    @quantity.setter
    def quantity(self, inp):
        # This method allows alias property "quantity" to set the value of Vertex property "power".
        self.power = inp

    def __copy__(self):
        """
        Returns a copy of this Vertex object.
        This is a redefinition of method copy.copy() that will be evoked when a Vertex object is to be copied.

        Parameters: None

        Returns: (Vertex) A copy of this Vertex object.
        """
        return Vertex(
            price=self.price,
            order_id=self.orderId,
            timestamp=self.timeStamp,
            continuity=self.continuity,
            quantity=self.quantity,
            cost=self.cost
        )


def _assign_time_stamp():
    return datetime.now()


class OrderStatus(Enum):
    # Enumeration of valid Order object statuses.
    NEW = 0
    NEW_REVISED = 1
    STANDING_ORDER = 2          # One of two valid Order objects to be matched from a MatchingEngine object order book.
    REVISED_STANDING_ORDER = 3  # One of two valid Order objects to be matched from a MatchingEngine object order book.
    MATCHED = 4
    REVISED_MATCHED = 5
    CANCELLED = 6
    EXPIRED = 7
    SUPERSEDED = 8


class SupplyOrDemand(Enum):
    # Enumeration of Order object attribution as either supply or demand.
    SUPPLY = 0
    DEMAND = 1
    BOTH = 2


class VTypes(Enum):
    NONE = 0
    LIMIT_ORDER = 1
    MARKET_ORDER = 1  # A market order while behave much like a special limit order for matching. Same enum.
    LINEAR = 2
    COMPLEX = 3


class Order:
    """
    Aggregates Vertex objects into their supply or demand curve.
    An Order object may be used instead of an IntervalValue object to store and organize Vertices.

    Parameters:
      actor
      expire_by
      market
      parent_order_id:
      vertices:
      supply_or_demand:
      time_interval

    Returns: (Order) object

    """
    def __init__(self,
                 market,  # PROTECTED. This should point to a valid MatchingEngine Market object
                 time_interval: TimeInterval,  # PROTECTED
                 actor: Neighbor or LocalAsset,  # PROTECTED. Not using default because this association is critical for
                 #                                 an Order
                 expire_by: datetime = datetime.now() + OFFER_DURATION,  # Protected
                 parent_order_id: int or None = None,
                 vertices: list or None = None,
                 supply_or_demand: SupplyOrDemand or bool or None = None
                 ):
        self.__timeInterval__: TimeInterval = time_interval
        self.timeStamp: datetime = _assign_time_stamp()
        if vertices:
            self.vertices: list = list(vertices)
        else:
            self.vertices: list = [Vertex(price=0.0, quantity=0.0, continuity=False, order_id=self.id,
                                          timestamp=self.timeStamp)]
        self.__market__ = market
        self.__marketSeries__: str = market.marketSeriesName
        if actor:
            self.__actor__: Neighbor or LocalAsset = actor
        else:
            raise TypeError("A Neighbor or LocalAsset object must own every bid or offer.")
        self.status: OrderStatus = OrderStatus.NEW
        # self.allowPartialMatch: bool = allow_partial_match
        if parent_order_id:
            self.parentOrderId: int or None = parent_order_id
        else:
            self.parentOrderId: int or None = None
        self.childOrderIds: list = []
        self.__expireBy__: datetime = expire_by
        self.supplyOrDemand: SupplyOrDemand = supply_or_demand

        if self.supplyOrDemand is None:
            supply_order = Order(market=self.market,
                                 time_interval=self.timeInterval,
                                 actor=self.actor,
                                 expire_by=self.expireBy,
                                 parent_order_id=self.id,
                                 vertices=self.vertices,
                                 supply_or_demand=SupplyOrDemand.SUPPLY)
            self.childOrderIds.append(supply_order.id)
            supply_order = supply_order.check_supply_order()
            if supply_order and supply_order.order_or_none():
                self.market.orderList.append(supply_order)
                if any([x == self for x in self.market.orderList]):
                    self.market.orderList = [x for x in self.market.orderList if x != self]

            demand_order = Order(market=self.market, time_interval=self.timeInterval, actor=self.actor,
                                 expire_by=self.expireBy,
                                 parent_order_id=self.id, vertices=self.vertices,
                                 supply_or_demand=SupplyOrDemand.DEMAND)
            self.childOrderIds.append(demand_order.id)
            demand_order = demand_order.check_demand_order()
            if demand_order and demand_order.order_or_none():
                self.market.orderList.append(demand_order)
                if any([x == self for x in self.market.orderList]):
                    self.market.orderList = [x for x in self.market.orderList if x != self]

            self.status = OrderStatus.SUPERSEDED
            self.description = []

        self.seller = None
        self.buyer = None
        self.assign_buyer_or_seller()

    @property
    def id(self):
        # The Order object ID is assigned automatically by a builtin function. It can be read, but it should not be
        # changed.
        return id(self)

    @id.setter
    def id(self, _):
        # The Order object ID is assigned automatically by a builtin function. It can be read, but it should not be
        # changed.
        pass

    @property
    def market(self):
        return self.__market__

    @market.setter
    def market(self, _):
        pass

    @property
    def marketSeries(self):
        return self.__marketSeries__

    @marketSeries.setter
    def marketSeries(self, _):
        pass

    @property
    def actor(self):
        # TODO: David Raker suggested name actor may cause confusion. "owner"? "source"? The word "actor" is used in the
        #       SEPA TE Concept Model.
        return self.__actor__

    @actor.setter
    def actor(self, _):
        pass

    @property
    def expireBy(self):
        return self.__expireBy__

    @expireBy.setter
    def expireBy(self, _):
        pass

    @property
    def timeInterval(self):
        return self.__timeInterval__

    @timeInterval.setter
    def timeInterval(self, _):
        pass

    def create_json_message(self):
        """
        Convert an Order object into json message, which can be sent to another agent and loaded again into an
        identical Order object.

        Parameters: None

        Returns: flat JSON file
        """
        #
        # TODO: Review this method to make sure that recursion captures all the nested objects properly.
        return dumps({"actor": self.actor.name,
                      "childOrderIds": self.childOrderIds,
                      "duration [s]": self.timeInterval.duration.total_seconds(),
                      "expireBy": self.expireBy.isoformat(),
                      "id": self.id,
                      "parentOrderId": self.parentOrderId,
                      "vertices": self.vertices,
                      "startTime": self.timeInterval.startTime.isoformat(),
                      "status": self.status.name,
                      "timeStamp": self.timeStamp.isoformat(),
                      "supplyOrDemand": self.supplyOrDemand})

    def load_json_message(self, filename: str or None = None):
        # Recreate an Order object from a json file, as may have been received from a Neighbor agent object.
        # TODO: Review this method to make sure it unpacks the json file so that a new Order object may be easily
        #       recreated.
        raw_load = load(filename)
        return raw_load

    def mark_as_revised(self):
        # Update order status in [NEW, STANDING_ORDER, and MATCHED] to corresponding revised status.
        if self.status == OrderStatus.NEW:
            self.status = OrderStatus.NEW_REVISED
        elif self.status == OrderStatus.STANDING_ORDER:
            self.status = OrderStatus.REVISED_STANDING_ORDER
        elif self.status == OrderStatus.MATCHED:
            self.status = OrderStatus.REVISED_MATCHED
        return None

    def match(self, quantity: float, match_price: float, counter_party: Neighbor or LocalAsset or None = None):
        """
        Creates Order objects for the matched and remainder unmatched quantities of the original Order object after
        transacting the given quantity at the given match price.

        Parameters:
          quantity: (float) Avg. electric power [avg. kW] of this transaction
          match_price: (float) Electricity price [$/kWh] at which this transaction took place
          counter_party: (Neighbor or LocalAsset or None) The entity buying or selling to the standing order.

        Return: (tuple)
          matched_order: (Order or None) The part of the original order that is matched by this transaction.
          unmatched order: (Order or None) The remainder of the original order after this transaction.

        Notes:
        - The caller is responsible to place any returned matched or unmatched orders into the appropriate order lists
          of books.
        - The quantity property sign must be > 0 for a supply order object and < 0 for a demand order object.
        - This method name unfortunately mirrors one used by MatchingEngine market. This may be a source of confusion.
        - The matched and unmatched Order objects now originate from copies of self. The copy method is overridden to
          facilitate the tracking of parentage.
        - This method can now complete attribution to a counterparty when a match is completed. The counterparty
          should be of type Neighbor or LocalAsset.
        """
        if self.supplyOrDemand == SupplyOrDemand.SUPPLY:
            assert quantity >= 0.0, \
                "Supply quantity is defined as a positive number."
        elif self.supplyOrDemand == SupplyOrDemand.DEMAND:
            assert quantity <= 0.0, \
                "Demand quantity is defined as a negative number."
        else:
            raise AttributeError("Order must be either SUPPLY or DEMAND.")

        # Translate the order quantity and create a new draft order object for the remainder quantity. (The draft
        # order will be corrected later.)
        points = []
        for pp in list(self.vertices):
            remainder = pp.quantity - quantity
            points.append(
                Vertex(
                    price=pp.price,
                    quantity=remainder,
                    continuity=pp.continuity,
                    order_id=self.id,
                    timestamp=self.timeStamp
                )
            )

        unmatched_order: Order or None = copy(self)
        unmatched_order.vertices = points
        unmatched_order.status = OrderStatus.NEW
        unmatched_order.assign_buyer_or_seller()

        if self.supplyOrDemand == SupplyOrDemand.SUPPLY:
            unmatched_order = unmatched_order.check_supply_order()
        elif self.supplyOrDemand == SupplyOrDemand.DEMAND:
            unmatched_order = unmatched_order.check_demand_order()
        else:
            unmatched_order = None

        # [The match is a market order] (I.e., it must be represented by one Vertex having infinite magnitude.)
        # Create a matched order object based on quantity and match price.
        if abs(match_price) == INF:
            matched_order: Order = copy(self)
            matched_order.vertices = [
                Vertex(
                    price=match_price,
                    quantity=quantity,
                    continuity=False,
                    order_id=self.id,
                    timestamp=self.timeStamp
                )
            ]
            matched_order.status = OrderStatus.MATCHED

        # [Match is a limit order.] ((I.e., it must be represented by two Vertex objects.)
        else:
            matched_order: Order = copy(self)
            matched_order.vertices = [
                Vertex(
                    price=match_price,
                    quantity=0.0,
                    continuity=False,
                    order_id=self.id,
                    timestamp=self.timeStamp
                ),
                Vertex(
                    price=match_price,
                    quantity=quantity,
                    continuity=False,
                    order_id=self.id,
                    timestamp=self.timeStamp
                )
            ]
            matched_order.status = OrderStatus.MATCHED

        # if self.supplyOrDemand == SupplyOrDemand.SUPPLY:
        #     matched_order = matched_order.check_supply_order()
        # elif self.supplyOrDemand == SupplyOrDemand.DEMAND:
        #     matched_order = matched_order.check_demand_order()
        # else:
        #     matched_order = None

        self.status = OrderStatus.SUPERSEDED

        if (matched_order or unmatched_order) and any([x == self for x in self.market.orderList]):
            self.market.orderList = [x for x in self.market.orderList if x != self]

        if matched_order and matched_order.order_or_none():
            if matched_order.buyer == self.actor:
                matched_order.seller = counter_party
            elif matched_order.seller == self.actor:
                matched_order.buyer = counter_party
            else:
                raise RuntimeError("The buyer and seller were not correctly assigned for this Order.")

        return matched_order, unmatched_order

    def sort_order(self) -> None:
        """
        Sorts the vertices of a supply or demand order. See class order.Vertex.

        Parameters: None

        Returns: None

        Notes:
        - Supply order Vertex objects are sorted by increasing price and quantity; Demand order Vertex objects are
          sorted by decreasing price and decreasing quantity.
        - If it is unknown whether the order is a supply or demand order, a warning occurs and the method returns None.
        """
        if self.supplyOrDemand == SupplyOrDemand.SUPPLY:
            self.vertices.sort(key=lambda x: (x.price, x.quantity))
            return None
        elif self.supplyOrDemand == SupplyOrDemand.DEMAND:
            self.vertices.sort(key=lambda x: (-x.price, -x.quantity))
            return None
        else:
            warn("{Order.sort_order()} Supply and demand orders are sorted differently.")
            return None

    # TODO: Consider whether check_demand_order() and check_supply_order() methods can/should be consolidated into a
    #       single method.
    def check_demand_order(self):
        """
        Checks an order to make sure it is a proper demand order.

        Parameters: None

        Returns: (Order or None) of type demand.

        Notes:
        - If the order is already a good demand order, it returns the original order.
        - If the order has no demand component, returns None.
        - If the order has both supply and demand components, it returns a revised demand order.
        """

        # [This Order object is not demand.]
        if self.supplyOrDemand is not SupplyOrDemand.DEMAND:
            Warning("{Order.check_demand_order()} can only be supplied to orders with status 'DEMAND'.")
            return self.order_or_none()

        self.sort_order()

        # [This order has no Vertex objects.]
        # Cancel the order and return None.
        if not self.vertices:
            self.status = OrderStatus.CANCELLED
            return None

        # [The order is ALREADY a demand order]
        # If all its quantities are less than or equal to 0.0. Return the original order.
        if all([x.quantity <= 0.0 for x in self.vertices]):
            if len(self.vertices) == 1:
                if self.vertices[0].price != INF:
                    self.vertices[0].price = INF
                if self.status == OrderStatus.NEW:
                    self.status = OrderStatus.NEW_REVISED
                elif self.status == OrderStatus.STANDING_ORDER:
                    self.status = OrderStatus.REVISED_STANDING_ORDER
            self.assign_buyer_or_seller()
            return self.order_or_none()

        # There is no part of the order that represents demand. Cancel the order and return None.
        if not any([x.quantity < 0.0 for x in self.vertices]):
            self.status = OrderStatus.CANCELLED
            return None

        # [The order has both supply and demand components.]
        # The order object must be revised to include only demand.
        if any([x.quantity > 0.0 for x in self.vertices]):
            revised_vertices = []
            old_quantity = self.vertices[0].quantity
            assert old_quantity > 0.0, \
                "{Order.check_demand_order()} Something's wrong if first quantity is demand instead of supply."
            old_price = self.vertices[0].price

            for point in self.vertices[1:]:
                new_quantity = point.quantity
                new_price = point.price

                # [This point's quantity is supply.]
                # Do nothing. This point should not be included among demand points.
                if new_quantity > 0.0:
                    pass

                # [This Vertex has quantity 0.0.]
                # It is probably a transition between supply and demand components. Append the point to the revised
                # list of Vertex objects.
                elif new_quantity == 0.0:
                    revised_vertices.append(
                        point
                    )

                # [A transition from supply to demand is detected.]
                # A new point must be created at quantity = 0.0, and the new demand point must also be used.
                elif new_quantity < 0.0 < old_quantity:
                    price_at_q0 = old_price - old_quantity * (old_price - new_price) / (old_quantity - new_quantity)
                    new_point = Vertex(
                        price=price_at_q0,
                        quantity=0.0,
                        continuity=point.continuity,
                        order_id=self.id,
                        timestamp=self.timeStamp
                    )
                    revised_vertices.append(
                        new_point
                    )
                    revised_vertices.append(
                        point
                    )

                # [The point is a simple demand point.]
                # Append the point to the revised Vertex list.
                elif new_quantity < 0.0:
                    revised_vertices.append(
                        point
                    )

                old_quantity = new_quantity
                old_price = new_price

            self.vertices = revised_vertices
            self.mark_as_revised()

            return self.order_or_none()

    def get_quantity_from_price(self, test_price: float):
        """
        Returns aa Vertex object representing this Order object's quantity at a given test price.

        Parameters:
            test_price: (float) [$/kWh]

        Returns: (Vertex) at price [$/kWh] and discovered quantity [kW_e]
        """

        # [This order has no Vertex objects.]
        # Return a Vertex object having quantity 0.0.
        if not self.vertices:
            qVertex = Vertex(
                price=test_price,
                quantity=0.0,
                continuity=False,
                order_id=self.id,
                timestamp=self.timeStamp
            )
            qVertex.vType = VTypes.NONE
            return qVertex

        # [There is just one Vertex object in this order.] (I.e., the order is not price-flexible.)
        # Return a Vertex object using the quantity and continuity of the lone standing Vertex.
        # Note: this type of Order object is called a "market order."
        if len(self.vertices) == 1:
            qVertex = Vertex(
                price=test_price,
                quantity=self.vertices[0].quantity,
                continuity=self.vertices[0].continuity,
                order_id=self.id,
                timestamp=self.timeStamp
            )
            qVertex.vType = VTypes.MARKET_ORDER
            return qVertex

        # [The order has more than one vertex.]
        # Sort the vertices.
        # Note that supply is ordered by increasing price and quantity; Demand is sorted by decreasing price and
        # quantity. This is important because supply quantity is determined by increasing price order, and demand
        # quantity is determined by decreasing price order.
        self.sort_order()

        # [This Order object is supply.]
        if self.supplyOrDemand == SupplyOrDemand.SUPPLY:

            # [The price is less than or equal to that of the first standing Vertex object.]
            # Return a Vertex object having the quantity and continuity of the first standing Vertex.
            if test_price < self.vertices[0].price:
                qVertex = Vertex(
                    price=test_price,
                    quantity=self.vertices[0].quantity,
                    continuity=self.vertices[0].continuity,
                    order_id=self.id,
                    timestamp=self.timeStamp
                )
                qVertex.vType = VTypes.LIMIT_ORDER
                return qVertex

            # [The price is greater than that of the final standing Vertex object.]
            # Return a Vertex object having the quantity and continuity of the final standing Vertex.
            if test_price >= self.vertices[-1].price:
                qVertex = Vertex(
                    price=test_price,
                    quantity=self.vertices[-1].quantity,
                    continuity=self.vertices[-1].continuity,
                    order_id=self.id,
                    timestamp=self.timeStamp
                )
                if len(self.vertices) == 2:
                    qVertex.vType = VTypes.LIMIT_ORDER
                elif len(self.vertices) > 2:
                    qVertex.vType = VTypes.COMPLEX
                else:
                    qVertex.vType = VTypes.NONE
                return qVertex

            # Index through the Vertex objects to find the two that bound the test price.
            for i in range(len(self.vertices)-1):

                # [The quantity is bounded by the two standing Vertex objects.]
                if self.vertices[i].price <= test_price < self.vertices[i + 1].price:

                    # [The two standing Vertices define a vertical line.] (i.e., change in quantity at a price)
                    # Return a Vertex object having quantity equal to that of the second latter standing Vertex.
                    # Eligibility of this new Vertex object is determined by the eligibility of the second latter
                    # standing Vertex.
                    if self.vertices[i].price == self.vertices[i+1].price:
                        qVertex = Vertex(
                            price=test_price,
                            quantity=self.vertices[i + 1].quantity,
                            continuity=self.vertices[i + 1].continuity,
                            order_id=self.id,
                            timestamp=self.timeStamp
                        )
                        if len(self.vertices) == 2:
                            qVertex.vType = VTypes.LIMIT_ORDER
                        elif len(self.vertices) > 2:
                            qVertex.vType = VTypes.COMPLEX
                        else:
                            qVertex.vType = VTypes.NONE
                        return qVertex

                    # [Price differs for the two standing Vertices.]
                    # [Quantity is defined within this price range, as indicated by property "continuity."]
                    # Use interpolation to find the quantity at this price.
                    # Return a Vertex object having the interpolated quantity.
                    # Note: the second latter standing Vertex object indicates whether partial fulfillment is allowed
                    #       for the discovered quantity, as indicated by its property "continuity."
                    elif self.vertices[i+1].continuity:
                        q0 = self.vertices[i].quantity
                        q1 = self.vertices[i+1].quantity
                        p0 = self.vertices[i].price
                        p1 = self.vertices[i+1].price
                        q = q0 + (test_price - p0) * (q1 - q0) / (p1 - p0)
                        qVertex = Vertex(
                            price=test_price,
                            quantity=q,
                            continuity=self.vertices[i + 1].continuity,
                            order_id=self.id,
                            timestamp=self.timeStamp
                        )
                        if len(self.vertices) == 2:
                            qVertex.vType = VTypes.LINEAR
                        elif len(self.vertices) > 2:
                            qVertex.vType = VTypes.COMPLEX
                        else:
                            qVertex.vType = VTypes.NONE
                        return qVertex

                    # [No quantity is defined at the test price.]
                    # Return a Vertex with quantity 0.0.
                    else:
                        qVertex = Vertex(
                            price=test_price,
                            quantity=0.0,
                            continuity=False,
                            order_id=self.id,
                            timestamp=self.timeStamp
                        )
                        qVertex.vType = VTypes.NONE
                        return qVertex

        # [This order is demand.]
        if self.supplyOrDemand == SupplyOrDemand.DEMAND:

            # [The price is greater than that of the first standing Vertex.]
            # Return the quantity of the first standing Vertex.
            if test_price > self.vertices[0].price:
                qVertex = Vertex(
                    price=test_price,
                    quantity=self.vertices[0].quantity,
                    continuity=self.vertices[0].continuity,
                    order_id=self.id,
                    timestamp=self.timeStamp
                )
                qVertex.vType = VTypes.MARKET_ORDER
                return qVertex

            # [The price is less than or equal to that of the final standing Vertex.]
            # Return a Vertex object having the quantity of the final standing Vertex.
            if test_price <= self.vertices[-1].price:
                qVertex = Vertex(
                    price=test_price,
                    quantity=self.vertices[-1].quantity,
                    continuity=self.vertices[-1].continuity,
                    order_id=self.id,
                    timestamp=self.timeStamp
                )
                if len(self.vertices) == 2:
                    qVertex.vType = VTypes.LIMIT_ORDER
                elif len(self.vertices) > 2:
                    qVertex.vType = VTypes.COMPLEX
                else:
                    qVertex.vType = VTypes.NONE
                return qVertex

            # Index through the points to find the two that bound the test price.
            for i in range(len(self.vertices)-1):

                # [The price is between these two Vertex price values.]
                if self.vertices[i].price >= test_price > self.vertices[i+1].price:

                    # [The two points define a vertical line.] (i.e., change in quantity at the same price)
                    # Return the second point quantity and indicate eligibility for partial fulfillment based on the
                    # eligibility of the second latter Vertex object.
                    if self.vertices[i].price == self.vertices[i+1].price:
                        qVertex = Vertex(
                            price=test_price,
                            quantity=self.vertices[i + 1].quantity,
                            continuity=self.vertices[i + 1].continuity,
                            order_id=self.id,
                            timestamp=self.timeStamp
                        )
                        if len(self.vertices) == 2:
                            qVertex.vType = VTypes.LIMIT_ORDER
                        elif len(self.vertices) > 2:
                            qVertex.vType = VTypes.COMPLEX
                        else:
                            qVertex.vType = VTypes.NONE
                        return qVertex

                    # [Prices differ for these two points that bound this intersection.]
                    # [Partial fulfillment is allowed, according to the latter Vertex object.]
                    # Interpolate using price to find the quantity.
                    # Note: the eligibility of this new Vertex for partial fulfillment is based on the eligibility of
                    #       the second latter Vertex object.
                    elif self.vertices[i+1].continuity:
                        q0 = self.vertices[i].quantity
                        q1 = self.vertices[i+1].quantity
                        p0 = self.vertices[i].price
                        p1 = self.vertices[i+1].price
                        q = q0 + (test_price - p0) * (q1 - q0) / (p1 - p0)
                        qVertex = Vertex(
                            price=test_price,
                            quantity=q,
                            continuity=self.vertices[i + 1].continuity,
                            order_id=self.id,
                            timestamp=self.timeStamp
                        )
                        if len(self.vertices) == 2:
                            qVertex.vType = VTypes.LINEAR
                        elif len(self.vertices) > 2:
                            qVertex.vType = VTypes.COMPLEX
                        else:
                            qVertex.vType = VTypes.NONE
                        return qVertex

                    # [No quantity is defined at the test price.]
                    # Return a Vertex having quantity 0.0.
                    else:
                        qVertex = Vertex(
                            price=test_price,
                            quantity=0.0,
                            continuity=False,
                            order_id=self.id,
                            timestamp=self.timeStamp
                        )
                        qVertex.vType = VTypes.NONE
                        return qVertex

    def check_supply_order(self):
        """
        Checks an order to make sure it is a proper supply order.

        Parameters: None

        Returns: (Order or None)

        Notes:
        - If the order is already a good supply order, this method returns the original order.
        - If the order has no supply component, this method returns None.
        - If the order has both supply and demand components, this method returns a revised supply order.
        """

        # [This order is not supply.]
        if self.supplyOrDemand is not SupplyOrDemand.SUPPLY:
            Warning("{Order.check_supply_order()} can only be supplied to orders with status 'SUPPLY'.")
            return self

        # Sort this Order object's Vertices.
        # Note that supply Vertices are ordered by increasing price and quantity; demand Vertices are ordered
        # by decreasing price and quantity. This is important because the sorted order is critical to the correct
        # determinations of quantity and continuity from a test price.
        self.sort_order()

        # [This Order object has no Vertices.]
        # Cancel the order and return None.
        if not self.vertices:
            self.status = OrderStatus.CANCELLED
            return None

        # [The order is already a valid supply order.]
        # Return the original order.
        if all([x.quantity >= 0.0 for x in self.vertices]):
            if len(self.vertices) == 1:
                if self.vertices[0].price != -INF:
                    self.vertices[0].price = -INF
                    if self.status == OrderStatus.NEW:
                        self.status = OrderStatus.NEW_REVISED
                    elif self.status == OrderStatus.STANDING_ORDER:
                        self.status = OrderStatus.REVISED_STANDING_ORDER
            return self.order_or_none()

        # [This Order object has both supply and demand components.]
        # Return only the supply demand points.
        # Note that a new point may be needed at quantity 0.0, where the Order objects transitions from demand to
        # supply.
        if any([x.quantity < 0.0 for x in self.vertices]):
            revised_vertices = []
            old_quantity = self.vertices[0].quantity
            assert old_quantity < 0.0, \
                "{Order.check_supply_order()} Something's wrong if first quantity is supply instead of demand."
            old_price = self.vertices[0].price

            # Index through the Order's Vertices.
            for point in self.vertices[1:]:
                new_quantity = point.quantity
                new_price = point.price

                # [This Vertex is demand.]
                # It should not be included among demand points.
                if new_quantity < 0.0:
                    pass

                # [This Vertex has quantity 0.0.]
                # This is an existing transition between demand and supply components and can be used by the revised
                # supply Order object.
                elif new_quantity == 0.0:
                    revised_vertices.append(
                        point
                    )

                # [A transition from demand to supply is detected.]
                # A new point must be created at quantity = 0.0, and the new supply point must also be used.
                elif new_quantity > 0.0 > old_quantity:
                    price_at_q0 = old_price + old_quantity * (new_price - old_price) / (new_quantity - old_quantity)
                    new_point = Vertex(
                        price=price_at_q0,
                        quantity=0.0,
                        continuity=point.continuity,
                        order_id=self.id,
                        timestamp=self.timeStamp
                    )
                    revised_vertices.append(
                        new_point
                    )
                    revised_vertices.append(
                        point
                    )

                # [This is simply a supply Vertex.]
                # Include this Vertex in the revised Order object.
                elif new_quantity > 0.0:
                    revised_vertices.append(
                        point
                    )

                # Save the indexed quantity and price, as these are used to determine which Vertex objects should
                # be included in the revised Order object.
                old_quantity = new_quantity
                old_price = new_price

            # Redefine this Order object's Vertices.
            self.vertices = revised_vertices

            # Mark this Order object as having been revised.
            self.mark_as_revised()

            return self.order_or_none()

    @staticmethod
    def get_prices_from_orders(order_list: list):
        """
        Returns a simple list of the Vertex prices [$/kWh] from a list of Order objects.

        Parameters:
            order_list: list of Order objects

        Returns: (list) of unique prices [$/kwh] found among order_list

        Notes:
        - Care is used to make sure a flat list of prices is returned.
        """
        vertex_lists = [x.vertices for x in order_list]
        prices = []
        for pp in vertex_lists:
            prices.extend([x.price for x in pp])
        return list(set(prices))

    @staticmethod
    def aggregate_orders(order_list: list):
        """
        Return a new Order object that has been aggregated from a list of Order objects.
        Parameters:
             order_list: list of Order objects
        Returns: New Order object that has been aggregated from the provided list of Order objects.
        """
        # TODO: Finish this method.
        # TODO: Perform unit testing on this method.
        prices = Order.get_prices_from_orders(
            order_list
        )
        prices = [x for x in prices if abs(x) != INF]
        return prices

    def simplify_order(self):
        """
        Returns the order after eliminating all Vertices that are irrelevant or that can be inferred by
        interpolation.

        Returns: simplified Order object that omits vertices that could be inferred by linear interpolation
        TODO: Check this logic. I don't think it is right when the center value matches one of the bookend values.
        TODO: Extend logic in this method to also account for continuity.
        TODO: Write tests for this method.
        """
        self.sort_order()
        vertices = self.vertices

        # [This Order object does not have any Vertices.]
        # Return None.
        if not vertices:
            return None

        # [This Order object is probably a market order.] (A "market order" has one Vertex with infinite price
        #                                                 magnitude.)
        # Return the Order. It cannot be simplified further.
        elif len(vertices) <= 1:
            return self.order_or_none()

        # [this Order object has two Vertices.]
        elif len(vertices) == 2:

            # [This Order is a simple limit order.] (A "limit order" is defined by a single strike price.)
            # Check and perhaps correct the price assignments of the existing Vertices.
            if vertices[0].quantity == vertices[1].quantity:
                self.vertices = [vertices[0]]
                self.vertices[0].price = INF
                if self.supplyOrDemand == SupplyOrDemand.DEMAND:
                    self.vertices[0].price = -INF

            # Return the Order object, corrected or not.
            return self.order_or_none()

        # [This Order object has more than two Vertices.]
        # Check whether any of the Vertices are superfluous and may be deleted.
        else:
            # new_pp:= list of Vertex objects that should be retained as this Order object is simplified.
            new_pp = []

            # [The quantities of the first two Vertices are not the same.]
            # The first Vertex object is probably useful.
            if vertices[0].quantity != vertices[1].quantity:
                new_pp.append(vertices[0])

            # Index a PricePont and both its prior and next Vertices.
            # Three consecutive Vertices are needed to determine which are necessary.
            for i in range(1, len(vertices) - 1):
                prior_pp = vertices[i-1]
                pp = vertices[i]
                next_pp = vertices[i+1]
                lin_err = abs((pp.price - prior_pp.price) / (next_pp.price - prior_pp.price)
                              - (pp.quantity - prior_pp.quantity) / (next_pp.quantity - prior_pp.quantity))
                if lin_err > 1e-4 and \
                        not (pp.continuity and next_pp.continuity):
                    new_pp.append(pp)

            # [The last Vertex's quantity is not the same as the next-to-last's quantity.]
            # The last Vertex is probably useful and needed.
            if vertices[-1].quantity != vertices[-2].quantity:
                new_pp.append(vertices[-1])

            # [More than one Vertex has been saved.]
            # [The saved Vertex include quantity INF at least once.]
            # Remove the Vertex having quantity INF.
            if len(new_pp) > 1 and [x for x in new_pp if x.quantity == INF]:
                new_pp = [x for x in new_pp if x.quantity != INF]

            # [More than one Vertex has been saved.]
            # [The saved Vertices include quantity -INF at least once.]
            # Remove the Vertices having quantity -INF.
            if len(new_pp) > 1 and [x for x in new_pp if x.quantity == -INF]:
                new_pp = [x for x in new_pp if x.quantity != -INF]

            # Replace the original Order object's Vertices with the new simplified set of Vertices.
            self.vertices = new_pp

            return self.order_or_none()

    def order_or_none(self):
        # Return the order itself if it represents a nonzero quantity. Otherwise, return None.
        if any([abs(x.quantity) > 0.0 for x in self.vertices]):
            return self
        else:
            self.status = OrderStatus.CANCELLED
            return None

    def __copy__(self):
        """
        Redefines copy.copy() for an Order object.

        Parameters: None

        Returns: (Order)

        Notes:
        - The copy's id is new.
        - The copy's parentOrderId property automatically points to this Order from which the copy is made.f
        - The childOrderIds parameter is emptied for the new Order.
        - The new order's id is appended to the Order that is being copied.
        - This method should be used advantageously for the way that it automatically documents parent and child Order
          relationships.
        """

        copiedOrder = Order(market=self.market,
                            time_interval=self.timeInterval,
                            actor=self.actor,
                            expire_by=self.expireBy,
                            parent_order_id=self.id,  # This automatically points to copied Order object.
                            vertices=None,
                            supply_or_demand=self.supplyOrDemand
                            )
        self.childOrderIds.append(copiedOrder.id)  # This identifies the copy as a child of the copied Order.
        copiedOrder.timeStamp = self.timeStamp
        copiedOrder.status = self.status
        copiedOrder.childOrderIds = []
        copiedOrder.marketSeries = self.marketSeries
        copiedOrder.vertices = []
        copiedOrder.buyer = self.buyer
        copiedOrder.seller = self.seller
        for pp in self.vertices:
            copiedOrder.vertices.append(copy(pp))
        return copiedOrder

    def __deepcopy__(self, memodict=None):
        # This is a redefinition of method copy.deepcopy() that will be evoked when an Order object is to be
        # deep-copied.
        return copy(self)

    def assign_buyer_or_seller(self):
        """
        Make (typically) initial assignment of actor as either the prospective seller or buyer in a transaction.
        Returns: None
        """
        if self.supplyOrDemand == SupplyOrDemand.SUPPLY:
            self.seller = self.actor
            self.buyer = None
        elif self.supplyOrDemand == SupplyOrDemand.DEMAND:
            self.buyer = self.actor
            self.seller = None
        else:
            self.buyer = None
            self.seller = None
        return None


if __name__ == '__main__':
    pass
