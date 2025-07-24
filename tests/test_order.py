from copy import copy, deepcopy
from datetime import datetime, timedelta
from tent.market.matching_engine import MatchingEngine
from tent.neighbor import Neighbor
from tent.containers.order import Order, OrderStatus, Vertex, SupplyOrDemand, VTypes
from tent.containers.time_interval import TimeInterval
from unittest import TestCase


tc = TestCase()


def DIVIDER():
    print('\n**********')


class test_vertex:

    def __init__(self):
        self.test_vertex = None

    def setUp(self):
        self.test_vertex = Vertex(price=0.10, curve_id=123)

    def case1(self):
        print('Case 1: No arguments are provided to the class.')
        self.setUp()
        tc.assertEqual(self.test_vertex.price, 0.10, 'Vertex price is wrong.')
        tc.assertEqual(self.test_vertex.order_id, 123, 'The order ID is wrong.')
        tc.assertTrue(self.test_vertex.continuity is True, 'Continuity is wrong.')
        tc.assertIsInstance(self.test_vertex.time_stamp, datetime)

    def case2(self):
        print('Case 2: Normal case.')
        self.setUp()
        tc.assertIsInstance(
            self.test_vertex,
            Vertex,
            "The object should be of class Vertex."
        )
        tc.assertIsInstance(
            self.test_vertex,
            Vertex,
            "The object should inherit from class Vertex."
        )
        self.test_vertex.price = 7.654321
        tc.assertEqual(
            self.test_vertex.price,
            self.test_vertex.price,
            "The Vertex property price should be a pseudonym of Vertex property marginalPrice."
        )
        self.test_vertex.quantity = 0.654321
        tc.assertEqual(
            self.test_vertex.quantity,
            self.test_vertex.quantity,
            "The Vertex property quantity should be a pseudonym of Vertex property power."
        )


class test_order:

    def __init__(self):
        self.test_market = MatchingEngine()  # Using default arguments.
        self.test_time_interval = TimeInterval(
            activation_time=datetime.now(),
            duration=timedelta(hours=1),
            market=self.test_market,
            market_clearing_time=datetime(
                year=2023,
                month=9,
                day=6,
                hour=12,
                minute=0,
                second=0,
                microsecond=0
            ),
            start_time=datetime(
                year=2023,
                month=9,
                day=6,
                hour=12,
                minute=0,
                second=0,
                microsecond=0
            )
        )
        self.test_actor = None
        self.test_vertex = None
        self.test_order = None
        self.test_pp_d1 = None
        self.test_pp_d2 = None
        self.test_pp_s1 = None
        self.test_pp_s2 = None
        self.test_order1 = None

    def setUp(self):
        # Populate some Vertex, making sure that there are both supply and demand quantities, and they are not
        # already ordered by quantity.
        self.test_actor = Neighbor()  # Using default arguments.
        self.test_vertex = Vertex(
            price=0.10,
            curve_id=123
        )
        self.test_order = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor
        )
        self.test_pp_d1 = copy(
            self.test_vertex
        )
        self.test_pp_d1.quantity = -10.0
        self.test_pp_d1.price = 0.2
        self.test_pp_d2 = copy(
            self.test_vertex
        )
        self.test_pp_d2.quantity = -100.0
        self.test_pp_d2.price = 0.1
        self.test_pp_s1 = copy(
            self.test_vertex
        )
        self.test_pp_s1.quantity = 11.0
        self.test_pp_s1.price = 0.3
        self.test_pp_s2 = copy(
            self.test_vertex
        )
        self.test_pp_s2.quantity = 101.0
        self.test_pp_s2.price = 0.4
        self.test_order1 = deepcopy(
            self.test_order
        )
        self.test_order1.vertices = [
            self.test_pp_s2,
            self.test_pp_d1,
            self.test_pp_s1,
            self.test_pp_d2
        ]

    def case1(self):
        print('Case 1: Basic initiation with default arguments.')
        self.setUp()
        tc.assertIsInstance(
            self.test_order,
            Order,
            'The type is not Order'
        )
        tc.assertEqual(
            len(self.test_order.vertices),
            1,
            'Only a single default Vertex should have been found.'
        )
        tc.assertEqual(
            len([x for x in self.test_market.orderList if x.status
                 in [OrderStatus.STANDING_ORDER, OrderStatus.REVISED_STANDING_ORDER]]),
            0,
            'The market is storing the wrong number of active Order objects.'
        )

    def case2(self):
        print('Case 2: Working with multiple supply and demand Order Vertices. (Vertices defined after Order '
              'created.)')
        self.setUp()
        self.test_market.orderList = []
        tc.assertEqual(
            len(self.test_order1.vertices),
            4,
            'The wrong number of Vertices was found.'
        )
        tc.assertEqual(
            [x.quantity for x in self.test_order1.vertices],
            [101.0, -10.0, 11.0, -100.0],
            'The Vertex quantity order was changed.'
        )
        tc.assertEqual(
            [x.price for x in self.test_order1.vertices],
            [0.4, 0.2, 0.3, 0.1],
            'The Vertex price order was changed.'
        )
        tc.assertEqual(
            len([x for x in self.test_market.orderList
                 if x.status in [OrderStatus.STANDING_ORDER, OrderStatus.REVISED_STANDING_ORDER]]),
            0,
            'The market is storing the wrong number of active Order objects.'
        )
        tc.assertEqual(
            len([x for x in self.test_market.orderList if x.status in [OrderStatus.SUPERSEDED]]),
            0,
            'The market should store only the original superseded order.'
        )

    def case3(self):
        print('Case 3: Initiation of new Order using multiple supply and demand Order Vertices.')
        self.setUp()
        self.test_market.orderList = []  # Reset the market prior to creating a new Order object.
        order3 = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor,
            vertices=[
                self.test_pp_s2,
                self.test_pp_d1,
                self.test_pp_s1,
                self.test_pp_d2
            ]
        )
        tc.assertEqual(
            len(order3.vertices),
            4,
            'The wrong number of Vertices was found.'
        )
        tc.assertEqual(
            [x.quantity for x in order3.vertices],
            [101.0, -10.0, 11.0, -100.0],
            'The Vertex quantity order was changed.'
        )
        tc.assertEqual(
            [x.price for x in order3.vertices],
            [0.4, 0.2, 0.3, 0.1],
            'The Vertex price order was changed.'
        )
        tc.assertEqual(
            len([x for x in self.test_market.orderList if x.status in [OrderStatus.NEW_REVISED]]),
            2,
            'The market is storing the wrong number of active Order objects.'
        )
        tc.assertEqual(
            len(
                [
                    x for x in self.test_market.orderList
                    if x.status in [OrderStatus.SUPERSEDED]
                ]
            ),
            0,
            'The market should store only the original superseded order.'
        )
        demand_order = [x for x in self.test_market.orderList if x.supply_or_demand == SupplyOrDemand.DEMAND][0]
        tc.assertEqual(
            [x.quantity for x in demand_order.vertices],
            [0.0, -10.0, -100.0],
            'The market demand order Vertex quantities are not as anticipated.'
        )
        supply_order = [x for x in self.test_market.orderList if x.supply_or_demand == SupplyOrDemand.SUPPLY][0]
        tc.assertEqual(
            [x.quantity for x in supply_order.vertices],
            [0.0, 11.0, 101.0],
            'The market supply order Vertex quantities are not as anticipated.'
        )

    def case4(self):
        print('Case 4: Exercise method get_quantity_from_price()-limit demand order case.')
        self.setUp()
        self.test_market.orderList = []  # Reset the market prior to creating a new Order object.
        pp0 = deepcopy(
            self.test_pp_d1
        )
        pp0.quantity = 0.0
        order4 = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor,
            vertices=[
                pp0,
                self.test_pp_d1
            ],
            supply_or_demand=SupplyOrDemand.DEMAND
        )
        assert pp0.price == 0.2 and self.test_pp_d1.price == 0.2, "The prices of the Vertices are wrong."
        assert pp0.quantity == 0.0 and self.test_pp_d1.quantity == -10.0, \
            'The quantities of the two Vertices are not correct.'
        assert [x.quantity for x in order4.vertices] == [0.0, -10.0], \
            'The Vertex quantities or their order is wrong'
        demand_point = order4.get_quantity_from_price(
            pp0.price - 1.0
        )
        tc.assertEqual(
            demand_point.quantity,
            self.test_pp_d1.quantity,
            'A price to the left of the first Vertex should have been given that Vertex quantity.'
        )
        demand_point = order4.get_quantity_from_price(
            pp0.price
        )
        tc.assertEqual(
            demand_point.quantity,
            self.test_pp_d1.quantity,
            'The price of the first demand Vertex gave the wrong quantity.'
        )
        demand_point = order4.get_quantity_from_price(
            self.test_pp_d1.price + 1.0
        )
        tc.assertEqual(
            demand_point.quantity,
            pp0.quantity,
            'The price to the right of all Vertices should have been given the rightmost Vertex quantity.'
        )
        tc.assertEqual(
            demand_point.vType,
            VTypes.LIMIT_ORDER
        )

    def case5(self):
        print('Case 5: Exercise method get_quantity_from_price()-limit supply order case.')
        self.setUp()
        self.test_market.orderList = []  # Reset the market prior to creating a new Order object.
        pp0 = deepcopy(
            self.test_pp_s1
        )
        pp0.quantity = 0.0
        order5 = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor,
            vertices=[self.test_pp_s1, pp0],
            supply_or_demand=SupplyOrDemand.SUPPLY
        )
        assert pp0.price == 0.3 and self.test_pp_s1.price == 0.3, \
            'The prices of the Vertices are wrong.'
        assert pp0.quantity == 0.0 and self.test_pp_s1.quantity == 11.0, \
            'The quantities of the two Vertices are not correct.'
        # NOTE: The supply Vertices are initially provided in the wrong order.
        assert [x.quantity for x in order5.vertices] == [11.0, 0.0], \
            'The Vertex quantities or their order is wrong'
        supply_point = order5.get_quantity_from_price(
            pp0.price - 1.0
        )
        tc.assertEqual(
            supply_point.quantity,
            pp0.quantity,
            'A price to the left of the first Vertex should have been given that Vertex quantity.'
        )
        supply_point = order5.get_quantity_from_price(
            pp0.price
        )
        tc.assertEqual(
            supply_point.quantity,
            self.test_pp_s1.quantity,
            'The price of the first supply Vertex gave the wrong quantity.'
        )
        supply_point = order5.get_quantity_from_price(
            self.test_pp_s1.price + 1.0
        )
        tc.assertEqual(
            supply_point.quantity,
            self.test_pp_s1.quantity,
            'The price to the right of all Vertices should have been given the rightmost Vertex quantity.'
        )
        tc.assertEqual(
            supply_point.vType,
            VTypes.LIMIT_ORDER
        )

    def case6(self):
        print('Case 6: Exercise method get_quantity_from_price()-piecewise linear demand order case.')
        self.setUp()
        self.test_market.orderList = []  # Reset the market prior to creating a new Order object.
        pp0 = deepcopy(
            self.test_pp_d1
        )
        pp0.quantity = self.test_pp_d1.quantity + 5.0
        pp0.price = self.test_pp_d1.price + 0.1
        order6 = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor,
            vertices=[self.test_pp_d1, pp0],
            supply_or_demand=SupplyOrDemand.DEMAND
        )
        assert pp0.price == self.test_pp_d1.price + 0.1 and self.test_pp_d1.price == 0.2, \
            'The prices of the Vertices are wrong.'
        assert pp0.quantity == -5.0 and self.test_pp_d1.quantity == -10.0, \
            'The quantities of the two Vertices are not correct.'
        # NOTE: The demand Vertices are initially provided in the wrong order.
        assert [x.quantity for x in order6.vertices] == [-10.0, -5.0], \
            'The Vertex quantities or their order is wrong'
        demand_point = order6.get_quantity_from_price(
            self.test_pp_d1.price - 1.0
        )
        tc.assertEqual(
            demand_point.quantity,
            self.test_pp_d1.quantity,
            'A price to the left of the first Vertex should have been given that Vertex quantity.'
        )
        demand_point = order6.get_quantity_from_price(
            pp0.price
        )
        tc.assertEqual(
            demand_point.quantity,
            pp0.quantity,
            'The price of the first supply Vertex gave the wrong quantity.'
        )
        demand_point = order6.get_quantity_from_price(
            self.test_pp_d1.price
        )
        tc.assertEqual(
            demand_point.quantity,
            self.test_pp_d1.quantity,
            'The price of the second supply Vertex gave the wrong quantity.'
        )
        demand_point = order6.get_quantity_from_price(
            pp0.price + 1.0
        )
        tc.assertEqual(
            demand_point.quantity,
            pp0.quantity,
            'The price to the right of all Vertices should have been given the rightmost Vertex quantity.'
        )
        demand_point = order6.get_quantity_from_price(
            (pp0.price + self.test_pp_d1.price) / 2.0
        )
        tc.assertAlmostEqual(
            demand_point.quantity,
            (pp0.quantity + self.test_pp_d1.quantity) / 2.0,
            7,
            'The price halfway between Vertices should have been given the average Vertex quantity.'
        )
        tc.assertEqual(
            demand_point.vType,
            VTypes.LINEAR
        )

    def case7(self):
        print('Case 7: Exercise method get_quantity_from_price()-piecewise linear supply order case.')
        self.setUp()
        self.test_market.orderList = []  # Reset the market prior to creating a new Order object.
        pp0 = deepcopy(
            self.test_pp_s1
        )
        pp0.quantity = self.test_pp_s1.quantity - 5.0
        pp0.price = self.test_pp_s1.price - 0.1
        order7 = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor,
            vertices=[self.test_pp_s1, pp0],
            supply_or_demand=SupplyOrDemand.SUPPLY
        )
        assert pp0.price == self.test_pp_s1.price - 0.1 and self.test_pp_s1.price == 0.3, \
            'The prices of the Vertices are wrong.'
        assert pp0.quantity == 6.0 and self.test_pp_s1.quantity == 11.0, \
            'The quantities of the two Vertices are not correct.'
        # NOTE: The demand vertices are initially provided in the wrong order.
        assert [x.quantity for x in order7.vertices] == [11.0, 6.0], \
            'The Vertex quantities or their order is wrong'
        supply_point = order7.get_quantity_from_price(
            pp0.price - 1.0
        )
        tc.assertEqual(
            supply_point.quantity,
            pp0.quantity,
            'A price to the left of the first Vertex should have been given that Vertex quantity.'
        )
        supply_point = order7.get_quantity_from_price(
            pp0.price
        )
        tc.assertEqual(
            supply_point.quantity,
            pp0.quantity,
            'The price of the first supply Vertex gave the wrong quantity.'
        )
        supply_point = order7.get_quantity_from_price(
            self.test_pp_s1.price
        )
        tc.assertEqual(
            supply_point.quantity,
            self.test_pp_s1.quantity,
            'The price of the second supply Vertex gave the wrong quantity.'
        )
        supply_point = order7.get_quantity_from_price(
            self.test_pp_s1.price + 1.0
        )
        tc.assertEqual(
            supply_point.quantity,
            self.test_pp_s1.quantity,
            'The price to the right of all Vertices should have been given the rightmost Vertex quantity.'
        )
        supply_point = order7.get_quantity_from_price(
            (pp0.price + self.test_pp_s1.price) / 2.0
        )
        tc.assertAlmostEqual(
            supply_point.quantity,
            (pp0.quantity + self.test_pp_s1.quantity) / 2.0,
            7,
            'The price halfway between Vertices should have been given the average Vertex quantity.'
        )
        tc.assertEqual(
            supply_point.vType,
            VTypes.LINEAR
        )

    def case8(self):
        print('Case 8: Exercise method check_supply_order().')
        self.setUp()
        self.test_market.orderList = []  # Reset the market prior to creating a new Order object.
        test_order8a = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor,
            vertices=[],
            supply_or_demand=SupplyOrDemand.SUPPLY
        )
        test_order8a.vertices = []
        assert not test_order8a.vertices, \
            'There should have been no Vertices.'
        returned_order = test_order8a.check_supply_order()
        tc.assertIsNone(
            returned_order,
            "The method should have returned None if there were no Vertex objects."
        )
        test_order8b = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor,
            vertices=[self.test_pp_s1],
            supply_or_demand=SupplyOrDemand.SUPPLY
        )
        assert test_order8b.vertices[0].price == self.test_pp_s1.price, \
            'The lone Vertex price is wrong.'
        returned_order = test_order8b.check_supply_order()
        tc.assertEqual(
            returned_order.vertices[0].price,
            -float('inf'),
            'The price of a lone Vertex should have been reset to negative infinity.'
        )
        tc.assertEqual(
            returned_order.status,
            OrderStatus.NEW_REVISED,
            'The returned order should have been returned with a revised status.'
        )
        test_order8c = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor,
            vertices=[self.test_pp_d1],
            supply_or_demand=SupplyOrDemand.SUPPLY
        )
        assert not any([x.quantity > 0.0 for x in test_order8c.vertices]), \
            'The Vertices are all demand, not supply.'
        returned_order = test_order8c.check_supply_order()
        tc.assertIsNone(
            returned_order,
            'The method should return None when there are no supply points.'
        )

        # NOTE: The cases of an Order having BOTH supply and demand components is adequately tested by other tests.

    def case9(self):
        print('Case 9: Exercise method check_demand_order().')
        self.setUp()
        self.test_market.orderList = []  # Reset the market prior to creating a new Order object.
        test_order9a = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor,
            vertices=[],
            supply_or_demand=SupplyOrDemand.DEMAND
        )
        test_order9a.vertices = []
        assert not test_order9a.vertices, \
            'There should have been no Vertices.'
        returned_order = test_order9a.check_demand_order()
        tc.assertIsNone(
            returned_order,
            'None should be returned if there are no Vertices.'
        )
        test_order9b = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor,
            vertices=[self.test_pp_d1],
            supply_or_demand=SupplyOrDemand.DEMAND
        )
        assert test_order9b.vertices[0].price == self.test_pp_d1.price, \
            'The lone Vertex price is wrong.'
        returned_order = test_order9b.check_demand_order()
        tc.assertEqual(
            returned_order.vertices[0].price,
            float('inf'),
            'The price of a lone Vertex should have been reset to positive infinity.'
        )
        tc.assertEqual(
            returned_order.status,
            OrderStatus.NEW_REVISED,
            'The returned order should have been returned with a revised status.'
        )
        test_order9c = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor,
            vertices=[self.test_pp_s1],
            supply_or_demand=SupplyOrDemand.DEMAND
        )
        assert not any([x.quantity < 0.0 for x in test_order9c.vertices]), \
            'The Vertices are all supply, not demand.'
        returned_order = test_order9c.check_demand_order()
        tc.assertIsNone(returned_order,
                        'The method should return None when there are no demand points.')

        # NOTE: The cases of an Order having BOTH supply and demand components is adequately tested by other tests.

    def case10(self):
        print('Case 10: Exercise method match().')
        self.setUp()
        self.test_market.orderList = []  # Reset the market order book.
        pp0 = deepcopy(
            self.test_pp_d1
        )
        pp0.quantity = 0.0
        demand_order = Order(
            market=self.test_market,
            interval=self.test_time_interval,
            actor=self.test_actor,
            vertices=[
                pp0,
                self.test_pp_d1,
                self.test_pp_d2
            ],
            supply_or_demand=SupplyOrDemand.DEMAND
        )
        # SUB-CASE: Match quantity intersects a vertical line in the demand curve.
        mo, uo = demand_order.match(
            quantity=-3.0,
            match_price=0.1234
        )
        tc.assertEqual(
            len(mo.vertices),
            2,
            'A match should always result in a limit order having two Vertices.'
        )
        tc.assertEqual(
            len(uo.vertices),
            3,
            'Three Vertices were expected for this unmatched order.'
        )
        tc.assertTrue(
            all([x.price == 0.1234 for x in mo.vertices]),
            'The prices of the matched order are wrong.'
        )
        tc.assertEqual(
            [x.quantity for x in mo.vertices],
            [0.0, -3.0],
            'The quantities of the matched order are wrong.'
        )
        assert [x.price for x in demand_order.vertices] == [0.2, 0.2, 0.1], \
            'The test demand order prices are wrong.'
        tc.assertEqual(
            [x.price for x in uo.vertices],
            [pp0.price, self.test_pp_d1.price, self.test_pp_d2.price],
            'The prices of the unmatched order remainder are wrong.'
        )
        tc.assertEqual(
            [x.quantity for x in uo.vertices],
            [0.0, self.test_pp_d1.quantity + 3.0, self.test_pp_d2.quantity + 3.0],
            'The quantities of the unmatched order remainder are wrong.'
        )


if __name__ == '__main__':

    DIVIDER()  # **********
    print('Running test_order.py.')

    DIVIDER()  # **********
    test_fixture = test_vertex()
    print('Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    test_fixture.case2()
    print('Fixture ' + str(test_fixture) + ' ran to completion.')

    DIVIDER()  # **********
    test_fixture = test_order()
    print('Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    test_fixture.case2()
    test_fixture.case3()
    test_fixture.case4()
    test_fixture.case5()
    test_fixture.case6()
    test_fixture.case7()
    test_fixture.case8()
    test_fixture.case9()
    test_fixture.case10()

    print('Fixture ' + str(test_fixture) + ' ran to completion.')
