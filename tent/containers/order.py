from copy import copy
from datetime import datetime, timedelta
from json import dumps, load
from warnings import warn

from tent.containers import SDCurve, TimeInterval, Vertex
from tent.enumerations import OrderStatus, SupplyOrDemand, VTypes
from tent.local_asset import LocalAsset
from tent.neighbor import Neighbor
from tent.utils.timer import Timer


OFFER_DURATION = timedelta(hours=24)  # Default time after which an Order object will expire if not otherwise specified


# TODO: Search for all 'Vertex.curve_id's and:
#  Change reads to Vertex.curve.id. (Make sure they work with UUID).
#  Change writes to the curve, not its ID.
class Order(SDCurve):
    """
    Aggregates Vertex objects into their supply or demand curve.
    Order extends SDCurve for use with matching engines.

    Parameters:
      actor
      expire_by
      market
      parent_order_id:
      vertices:
      supply_or_demand:
      interval

    Returns: (Order) object

    """
    def __init__(self,
                 market,  # PROTECTED. This should point to a valid MatchingEngine Market object
                 interval: TimeInterval,  # PROTECTED
                 actor: Neighbor or LocalAsset,  # PROTECTED. Not using default because this association is critical for
                 #                                 an Order
                 expire_by: datetime | None = None,  # Protected. Defaults to Timer.now() + OFFER_DURATION.
                 parent_order_id=None,
                 vertices: list | None = None,
                 supply_or_demand: SupplyOrDemand or bool or None = None
                 ):
        # Orders archive through the market's order-book Measurement (see MatchingEngine.order_measurement), which
        # gives them the same measurement/owner linkage as every other SDCurve. Markets without one still work; their
        # orders are simply not archived.
        measurement = getattr(market, 'order_measurement', None)
        super(Order, self).__init__(measurement, interval, vertices=vertices if vertices is not None else [],
                                    archive=False)
        # Order algorithms depend on list semantics: supply and demand vertices are sorted in opposite directions
        # (see sort_order) and duplicate (price, quantity) pairs are meaningful during matching, so the SortedSet used by
        # SDCurve is replaced with a plain list here.
        self.vertices: list = list(self.vertices)
        self.time_stamp: datetime = Timer.now()
        self.__market__ = market  # TODO: Shouldn't this refer to self.interval.market?
        if actor:
            self.__actor__: Neighbor or LocalAsset = actor
        else:
            raise TypeError("A Neighbor or LocalAsset object must own every bid or offer.")
        self.status: OrderStatus = OrderStatus.NEW
        self.parent_order_id = parent_order_id if parent_order_id else None
        self.child_order_ids: list = []
        self.__expireBy__: datetime = expire_by if expire_by is not None else Timer.now() + OFFER_DURATION
        self.supply_or_demand: SupplyOrDemand = supply_or_demand
        self.description = ''
        # A side-less order is split into a supply order and a demand order (either may be empty). The children are
        # returned through child_orders for the caller to book; this constructor does not touch the market's book.
        self.child_orders: list[Order] = []
        if self.supply_or_demand is None:
            for side, check in ((SupplyOrDemand.SUPPLY, Order.check_supply_order),
                                (SupplyOrDemand.DEMAND, Order.check_demand_order)):
                child = Order(market=self.market, interval=self.interval, actor=self.actor, expire_by=self.expire_by,
                              parent_order_id=self.id, vertices=[copy(v) for v in self.vertices], supply_or_demand=side)
                self.child_order_ids.append(child.id)
                child = check(child)
                if child and child.order_or_none():
                    self.child_orders.append(child)
            self.status = OrderStatus.SUPERSEDED
        self.seller = None
        self.buyer = None
        self.assign_buyer_or_seller()
        # Orders are archived by the MatchingEngine when they are booked (submitted, matched, or left standing) and
        # when they are superseded, not on construction: matching creates transient copies that are never booked.

    @classmethod
    def from_curve(cls, market, interval: TimeInterval, actor, curve: SDCurve, expire_by: datetime | None = None
                   ) -> list['Order']:
        """
        Build the order(s) that express an entity's supply/demand curve for one interval: a supply order for the
        positive-quantity part and a demand order for the negative-quantity part, omitting empty sides. The vertices are
        copied, so later changes to the curve do not alter the orders. Returns the (zero to two) non-empty orders.
        """
        parent = cls(market=market, interval=interval, actor=actor, expire_by=expire_by,
                     vertices=[copy(v) for v in curve.vertices], supply_or_demand=None)
        return parent.child_orders

    @property
    def market(self):
        return self.__market__

    @property
    def market_series(self) -> str:
        return self.market.series.name

    @property
    def actor(self):
        # TODO: David Raker suggested name actor may cause confusion. "owner"? "source"? The word "actor" is used in the
        #       SEPA TE Concept Model.
        # TODO: SDCurve has a measurement.source attribute which should already be this.
        #  Can this work for the MatchingEngine design? (i.e., are Orders contained in SDMeasurement?
        return self.__actor__

    @property
    def expire_by(self):
        return self.__expireBy__

    # TODO: create/load_json_message methods are not used, and may be partially or entirely superseded by get_record.
    # TODO: Should we instead have an abstract serialization method in the TN that receives a dict?
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
                      "childOrderIds": self.child_order_ids,
                      "duration [s]": self.interval.duration.total_seconds(),
                      "expire_by": self.expire_by.isoformat(),
                      "id": self.id,
                      "parentOrderId": self.parent_order_id,
                      "vertices": self.vertices,
                      "startTime": self.interval.start_time.isoformat(),
                      "status": self.status.name,
                      "timeStamp": self.time_stamp.isoformat(),
                      "supplyOrDemand": self.supply_or_demand})

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
        if self.supply_or_demand == SupplyOrDemand.SUPPLY:
            assert quantity >= 0.0, \
                "Supply quantity is defined as a positive number."
        elif self.supply_or_demand == SupplyOrDemand.DEMAND:
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
                    curve=self
                )
            )

        unmatched_order: Order or None = copy(self)
        unmatched_order.vertices = points
        unmatched_order.status = OrderStatus.NEW
        unmatched_order.assign_buyer_or_seller()

        if self.supply_or_demand == SupplyOrDemand.SUPPLY:
            unmatched_order = unmatched_order.check_supply_order()
        elif self.supply_or_demand == SupplyOrDemand.DEMAND:
            unmatched_order = unmatched_order.check_demand_order()
        else:
            unmatched_order = None

        # [The match is a market order] (I.e., it must be represented by one Vertex having infinite magnitude.)
        # Create a matched order object based on quantity and match price.
        if abs(match_price) == float('inf'):
            matched_order: Order = copy(self)
            matched_order.vertices = [
                Vertex(
                    price=match_price,
                    quantity=quantity,
                    continuity=False,
                    curve=self
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
                    curve=self
                ),
                Vertex(
                    price=match_price,
                    quantity=quantity,
                    continuity=False,
                    curve=self
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
        if self.supply_or_demand == SupplyOrDemand.SUPPLY:
            self.vertices.sort(key=lambda x: (x.price, x.quantity))
            return None
        elif self.supply_or_demand == SupplyOrDemand.DEMAND:
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
        if self.supply_or_demand is not SupplyOrDemand.DEMAND:
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
                if self.vertices[0].price != float('inf'):
                    self.vertices[0].price = float('inf')
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
                        curve=self
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
                curve=self
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
                curve=self
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
        if self.supply_or_demand == SupplyOrDemand.SUPPLY:

            # [The price is less than or equal to that of the first standing Vertex object.]
            # Return a Vertex object having the quantity and continuity of the first standing Vertex.
            if test_price < self.vertices[0].price:
                qVertex = Vertex(
                    price=test_price,
                    quantity=self.vertices[0].quantity,
                    continuity=self.vertices[0].continuity,
                    curve=self
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
                    curve=self
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
                            curve=self
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
                            curve=self
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
                            curve=self
                        )
                        qVertex.vType = VTypes.NONE
                        return qVertex

        # [This order is demand.]
        if self.supply_or_demand == SupplyOrDemand.DEMAND:

            # [The price is greater than that of the first standing Vertex.]
            # Return the quantity of the first standing Vertex.
            if test_price > self.vertices[0].price:
                qVertex = Vertex(
                    price=test_price,
                    quantity=self.vertices[0].quantity,
                    continuity=self.vertices[0].continuity,
                    curve=self
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
                    curve=self
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
                            curve=self
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
                            curve=self
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
                            curve=self
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
        if self.supply_or_demand is not SupplyOrDemand.SUPPLY:
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
                if self.vertices[0].price != -float('inf'):
                    self.vertices[0].price = -float('inf')
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
                        curve=self
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
                self.vertices[0].price = float('inf')
                if self.supply_or_demand == SupplyOrDemand.DEMAND:
                    self.vertices[0].price = -float('inf')

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
            # [The saved Vertex include quantity float('inf') at least once.]
            # Remove the Vertex having quantity float('inf').
            if len(new_pp) > 1 and [x for x in new_pp if x.quantity == float('inf')]:
                new_pp = [x for x in new_pp if x.quantity != float('inf')]

            # [More than one Vertex has been saved.]
            # [The saved Vertices include quantity -float('inf') at least once.]
            # Remove the Vertices having quantity -float('inf').
            if len(new_pp) > 1 and [x for x in new_pp if x.quantity == -float('inf')]:
                new_pp = [x for x in new_pp if x.quantity != -float('inf')]

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
                            interval=self.interval,
                            actor=self.actor,
                            expire_by=self.expire_by,
                            parent_order_id=self.id,  # This automatically points to copied Order object.
                            vertices=None,
                            supply_or_demand=self.supply_or_demand
                            )
        self.child_order_ids.append(copiedOrder.id)  # This identifies the copy as a child of the copied Order.
        copiedOrder.time_stamp = self.time_stamp
        copiedOrder.status = self.status
        copiedOrder.child_order_ids = []
        copiedOrder.description = self.description
        copiedOrder.buyer = self.buyer
        copiedOrder.seller = self.seller
        copiedOrder.vertices = [copy(pp) for pp in self.vertices]
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
        if self.supply_or_demand == SupplyOrDemand.SUPPLY:
            self.seller = self.actor
            self.buyer = None
        elif self.supply_or_demand == SupplyOrDemand.DEMAND:
            self.buyer = self.actor
            self.seller = None
        else:
            self.buyer = None
            self.seller = None
        return None

    @property
    def table_name(self):
        return 'order'

    def get_record(self) -> dict:
        record = super(Order, self).get_record()
        record.update({
            'actor_id': self.actor.id if self.actor is not None else None,
            'buyer_id': getattr(self.buyer, 'id', None),
            'seller_id': getattr(self.seller, 'id', None),
            'parent_order_id': self.parent_order_id,
            'status': self.status,
            'supply_or_demand': self.supply_or_demand,
            'time_stamp': self.time_stamp,
            'expire_by': self.expire_by
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(Order, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "order",
                "inherits": "sd_curve",
                "columns": [
                    {"name": "id", "type": "Uuid", "kwargs": {"primary_key": True}},
                    {"name": "record_time", "type": "DateTime", "kwargs": {"primary_key": True}},
                    {"name": "actor_id", "foreign_key": "component.id"},
                    {"name": "buyer_id", "foreign_key": "component.id"},
                    {"name": "seller_id", "foreign_key": "component.id"},
                    {"name": "parent_order_id", "type": "Uuid"},
                    {"name": "status", "enumeration": "OrderStatus"},
                    {"name": "supply_or_demand", "enumeration": "SupplyOrDemand"},
                    {"name": "time_stamp", "type": "DateTime"},
                    {"name": "expire_by", "type": "DateTime"}
                ],
                "constraints": [
                    {"name": "fk_order_sd_curve_version",
                     "foreign_key": {"columns": ["id", "record_time"], "references": ["sd_curve.id", "sd_curve.record_time"]}}
                ]
            }
        ])
        return definitions