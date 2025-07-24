from unittest import TestCase
from tent.market.matching_engine import MatchingEngine, MatchingStrategy
from tent.neighbor import Neighbor
from tent.containers.time_interval import TimeInterval
from datetime import datetime, timedelta
from tent.containers.order import Order, Vertex, SupplyOrDemand, OrderStatus
from tent.local_asset import LocalAsset
from copy import copy
from tent.transactive_node import TransactiveNode
from tent.enumerations.market_state import MarketState


tc = TestCase()
INF = float('inf')


def DIVIDER():
    print('\n**********')


def LF(n: int = 1):
    print('\n' * n)


MARKET = MatchingEngine()

ACTIVATION_TIME = datetime(
    year=2023,
    month=9,
    day=13,
    hour=10,
    minute=2
)

MARKET_CLEARING_TIME = datetime(
    year=2023,
    month=9,
    day=13,
    hour=20,
    minute=45
)

DELIVERY_START_TIME = MARKET_CLEARING_TIME + timedelta(minutes=15)

EXPIRATION_TIME = ACTIVATION_TIME + timedelta(days=1)

TIME_INTERVAL = TimeInterval(
    activation_time=ACTIVATION_TIME,
    duration=timedelta(hours=1),
    market=MARKET,
    market_clearing_time=MARKET_CLEARING_TIME,
    start_time=DELIVERY_START_TIME
)

# actor examples:
NEIGHBOR1 = Neighbor(
    description="Neighbor actor example",
    name="Neighbor #1"
)

LOCAL_ASSET1 = LocalAsset(
    description="LocalAsset actor example",
    name="LocalAsset #1"
)

# Order examples:
# DEMAND_ORDER[0]: Continuous demand market order: *********************************************************************
DEMAND_ORDER = [Order(
    market=MARKET,
    interval=TIME_INTERVAL,
    actor=NEIGHBOR1,
    expire_by=EXPIRATION_TIME,
    supply_or_demand=SupplyOrDemand.DEMAND
)]

DEMAND_ORDER[-1].description = "Continuous demand market order"  # This means that price is infinity and partial
#                                                                   matching is allowed.
DEMAND_ORDER[-1].vertices = [
    Vertex(
        price=INF,
        curve_id=DEMAND_ORDER[0].id,
        quantity=-100.0,
        timestamp=DEMAND_ORDER[0].time_stamp,
        continuity=True
    )
]

# DEMAND_ORDER[1]: Discontinuous demand market order: ******************************************************************
DEMAND_ORDER.append(
    Order(
        market=MARKET,
        interval=TIME_INTERVAL,
        actor=NEIGHBOR1,
        expire_by=EXPIRATION_TIME,
        supply_or_demand=SupplyOrDemand.DEMAND
    )
)
DEMAND_ORDER[-1].description = "Discontinuous demand market order"  # This means that price is infinity and partial
#                                                                    matching is not allowed.
DEMAND_ORDER[-1].vertices = [
    Vertex(
        price=INF,
        curve_id=DEMAND_ORDER[-1].id,
        quantity=-100.0,
        timestamp=DEMAND_ORDER[-1].time_stamp,
        continuity=False
    )
]

# DEMAND_ORDER[2]: Continuous demand limit order: **********************************************************************
DEMAND_ORDER.append(
    Order(
        market=MARKET,
        interval=TIME_INTERVAL,
        actor=NEIGHBOR1,
        expire_by=EXPIRATION_TIME,
        supply_or_demand=SupplyOrDemand.DEMAND
    )
)
DEMAND_ORDER[-1].description = "Continuous demand limit order"  # This means that there is a single strike price and
#                                                                partial matching is allowed.
DEMAND_ORDER[-1].vertices = [
    Vertex(
        price=0.1,
        curve_id=DEMAND_ORDER[-1].id,
        quantity=0.0,
        timestamp=DEMAND_ORDER[-1].time_stamp,
        continuity=False
    ),
    Vertex(
        price=0.1,
        curve_id=DEMAND_ORDER[-1].id,
        quantity=-100.0,
        timestamp=DEMAND_ORDER[-1].time_stamp,
        continuity=True
    )
]

# DEMAND_ORDER[3]: Discontinuous demand limit order ********************************************************************
DEMAND_ORDER.append(
    Order(
        market=MARKET,
        interval=TIME_INTERVAL,
        actor=NEIGHBOR1,
        expire_by=EXPIRATION_TIME,
        supply_or_demand=SupplyOrDemand.DEMAND
    )
)
DEMAND_ORDER[-1].description = "Discontinuous demand limit order"  # This means that there is a single strike price and
#                                                                   partial matching is not allowed.
DEMAND_ORDER[-1].vertices = [
    Vertex(
        price=0.1,
        curve_id=DEMAND_ORDER[-1].id,
        quantity=0.0,
        timestamp=DEMAND_ORDER[-1].time_stamp,
        continuity=False
    ),
    Vertex(
        price=0.1,
        curve_id=DEMAND_ORDER[-1].id,
        quantity=-100.0,
        timestamp=DEMAND_ORDER[-1].time_stamp,
        continuity=False
    )
]

# DEMAND_ORDER[4]: Continuous piece-wise linear demand order: **********************************************************
DEMAND_ORDER.append(
    Order(
        market=MARKET,
        interval=TIME_INTERVAL,
        actor=NEIGHBOR1,
        expire_by=EXPIRATION_TIME,
        supply_or_demand=SupplyOrDemand.DEMAND
    )
)
DEMAND_ORDER[-1].description = "Continuous piece-wise linear demand order"  # This means that there is a single strike
#                                                                            price and partial matching is allowed.
DEMAND_ORDER[-1].vertices = [
    Vertex(
        price=0.25,
        curve_id=DEMAND_ORDER[-1].id,
        quantity=0.0,
        timestamp=DEMAND_ORDER[-1].time_stamp,
        continuity=False
    ),
    Vertex(
        price=0.15,
        curve_id=DEMAND_ORDER[-1].id,
        quantity=-100.0,
        timestamp=DEMAND_ORDER[-1].time_stamp,
        continuity=True
    )
]

# DEMAND_ORDER[5]: Discontinuous piece-wise demand order: **************************************************************
DEMAND_ORDER.append(
    Order(
        market=MARKET,
        interval=TIME_INTERVAL,
        actor=NEIGHBOR1,
        expire_by=EXPIRATION_TIME,
        supply_or_demand=SupplyOrDemand.DEMAND
    )
)
DEMAND_ORDER[-1].description = "Discontinuous piece-wise demand order"  # This means that there is a single strike price
#                                                                        and partial matching is not allowed.
DEMAND_ORDER[-1].vertices = [
    Vertex(
        price=0.25,
        curve_id=DEMAND_ORDER[-1].id,
        quantity=0.0,
        timestamp=DEMAND_ORDER[-1].time_stamp,
        continuity=False
    ),
    Vertex(
        price=0.15,
        curve_id=DEMAND_ORDER[-1].id,
        quantity=-100.0,
        timestamp=DEMAND_ORDER[-1].time_stamp,
        continuity=False
    )
]

# **********************************************************************************************************************
# SUPPLY_ORDER[0]: Continuous supply market order: ***************
SUPPLY_ORDER = [Order(
    market=MARKET,
    interval=TIME_INTERVAL,
    actor=NEIGHBOR1,
    expire_by=EXPIRATION_TIME,
    supply_or_demand=SupplyOrDemand.SUPPLY
)]

SUPPLY_ORDER[-1].description = "Continuous supply market order"  # This means that price is neg. infinity and partial
#                                                                 matching is allowed.
SUPPLY_ORDER[-1].vertices = [
    Vertex(
        price=-INF,
        curve_id=SUPPLY_ORDER[-1].id,
        quantity=100.0,
        timestamp=SUPPLY_ORDER[-1].time_stamp,
        continuity=True
    )
]

# SUPPLY_ORDER[1]: Discontinuous supply market order: ****************
SUPPLY_ORDER.append(
    Order(
        market=MARKET,
        interval=TIME_INTERVAL,
        actor=NEIGHBOR1,
        expire_by=EXPIRATION_TIME,
        supply_or_demand=SupplyOrDemand.SUPPLY
    )
)
SUPPLY_ORDER[-1].description = "Discontinuous supply market order"  # This means that price is neg. infinity and partial
#                                                                    matching is not allowed.
SUPPLY_ORDER[-1].vertices = [
    Vertex(
        price=INF,
        curve_id=SUPPLY_ORDER[-1].id,
        quantity=100.0,
        timestamp=SUPPLY_ORDER[-1].time_stamp,
        continuity=False
    )
]

# SUPPLY_ORDER[2]: Continuous supply limit order: ***************
SUPPLY_ORDER.append(
    Order(
        market=MARKET,
        interval=TIME_INTERVAL,
        actor=NEIGHBOR1,
        expire_by=EXPIRATION_TIME,
        supply_or_demand=SupplyOrDemand.SUPPLY
    )
)
SUPPLY_ORDER[-1].description = "Continuous supply limit order"  # This means that there is a single strike price and
#                                                                partial matching is allowed.
SUPPLY_ORDER[-1].vertices = [
    Vertex(
        price=0.1,
        curve_id=SUPPLY_ORDER[-1].id,
        quantity=0.0,
        timestamp=SUPPLY_ORDER[-1].time_stamp,
        continuity=False
    ),
    Vertex(
        price=0.1,
        curve_id=SUPPLY_ORDER[-1].id,
        quantity=100.0,
        timestamp=SUPPLY_ORDER[-1].time_stamp,
        continuity=True
    )
]

# SUPPLY_ORDER[3]: Discontinuous supply limit order ***************
SUPPLY_ORDER.append(
    Order(
        market=MARKET,
        interval=TIME_INTERVAL,
        actor=NEIGHBOR1,
        expire_by=EXPIRATION_TIME,
        supply_or_demand=SupplyOrDemand.SUPPLY
    )
)
SUPPLY_ORDER[-1].description = "Discontinuous supply limit order"  # This means that there is a single strike price and
#                                                                   partial matching is not allowed.
SUPPLY_ORDER[-1].vertices = [
    Vertex(
        price=0.1,
        curve_id=SUPPLY_ORDER[-1].id,
        quantity=0.0,
        timestamp=SUPPLY_ORDER[-1].time_stamp,
        continuity=False
    ),
    Vertex(
        price=0.1,
        curve_id=SUPPLY_ORDER[-1].id,
        quantity=100.0,
        timestamp=SUPPLY_ORDER[-1].time_stamp,
        continuity=False
    )
]

# SUPPLY_ORDER[4]: Continuous piece-wise linear supply order: ***************
SUPPLY_ORDER.append(
    Order(
        market=MARKET,
        interval=TIME_INTERVAL,
        actor=NEIGHBOR1,
        expire_by=EXPIRATION_TIME,
        supply_or_demand=SupplyOrDemand.SUPPLY
    )
)
SUPPLY_ORDER[-1].description = "Continuous piece-wise linear supply order"  # This means that there is a single strike
#                                                                            price and partial matching is allowed.
SUPPLY_ORDER[-1].vertices = [
    Vertex(
        price=0.15,
        curve_id=SUPPLY_ORDER[-1].id,
        quantity=0.0,
        timestamp=SUPPLY_ORDER[-1].time_stamp,
        continuity=False
    ),
    Vertex(
        price=0.25,
        curve_id=SUPPLY_ORDER[-1].id,
        quantity=100.0,
        timestamp=SUPPLY_ORDER[-1].time_stamp,
        continuity=True
    )
]

# SUPPLY_ORDER[5]: Discontinuous piece-wise supply order: ***************
SUPPLY_ORDER.append(
    Order(market=MARKET,
          interval=TIME_INTERVAL,
          actor=NEIGHBOR1,
          expire_by=EXPIRATION_TIME,
          supply_or_demand=SupplyOrDemand.SUPPLY
          )
)
SUPPLY_ORDER[-1].description = "Discontinuous piece-wise supply order"  # This means that there is a single strike price
#                                                                        and partial matching is not allowed.
SUPPLY_ORDER[-1].vertices = [
    Vertex(
        price=0.15,
        curve_id=SUPPLY_ORDER[-1].id,
        quantity=0.0,
        timestamp=SUPPLY_ORDER[-1].time_stamp,
        continuity=False
    ),
    Vertex(
        price=0.25,
        curve_id=SUPPLY_ORDER[-1].id,
        quantity=100.0,
        timestamp=SUPPLY_ORDER[-1].time_stamp,
        continuity=False
    )
]


class test_match:
    def __init__(self):
        self.test_market = None
        # self.test_supply_order = None
        # self.test_demand_order = None

    def setUp(self):
        self.test_market: MatchingEngine = MARKET
        self.test_market.orderList = []

        # ((INF, -100), c, 0)
        self.test_market.orderList.append(copy(DEMAND_ORDER[0]))
        self.test_market.orderList[0].description = "Demand, Market order, Continuous, -100 kW, TS"
        self.test_market.orderList[0].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[0].time_stamp = ACTIVATION_TIME

        # ((INF, -100 ), d, 0)
        self.test_market.orderList.append(copy(DEMAND_ORDER[1]))
        self.test_market.orderList[1].description = "Demand, Market order, Discontinuous, -100 kW, TS"
        self.test_market.orderList[1].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[1].time_stamp = ACTIVATION_TIME

        # (((0.1, 0), (0.1, -100)), c, 0)
        self.test_market.orderList.append(copy(DEMAND_ORDER[2]))
        self.test_market.orderList[2].description = "Demand, Limit order, Continuous, -100 kW, TS"
        self.test_market.orderList[2].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[2].time_stamp = ACTIVATION_TIME

        # ( (0.1 ,0 ), (0.1 ,-100), d, 0)
        self.test_market.orderList.append(copy(DEMAND_ORDER[3]))
        self.test_market.orderList[3].description = "Demand, Limit order, Discontinuous, -100 kW, TS"
        self.test_market.orderList[3].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[3].time_stamp = ACTIVATION_TIME

        # ((( 0.25, 0), ( 0.15, -100)), c, 0)
        self.test_market.orderList.append(copy(DEMAND_ORDER[4]))
        self.test_market.orderList[4].description = "Demand, PWL, Continuous, -100 kW, TS"
        self.test_market.orderList[4].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[4].time_stamp = ACTIVATION_TIME

        # ((( 0.25, 0), ( 0.15, -100)), d, 0)
        self.test_market.orderList.append(copy(DEMAND_ORDER[5]))
        self.test_market.orderList[5].description = "Demand, PWL, Discontinuous, -100 kW, TS"
        self.test_market.orderList[5].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[5].time_stamp = ACTIVATION_TIME

        # (( INF, -50), c, 0)
        self.test_market.orderList.append(copy(DEMAND_ORDER[0]))
        self.test_market.orderList[6].description = "Demand, Market order, Continuous, -50 kW, TS"
        self.test_market.orderList[6].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[6].time_stamp = ACTIVATION_TIME
        self.test_market.orderList[6].vertices[0].quantity = -50.0

        # (( INF, -50), d, 0)
        self.test_market.orderList.append(copy(DEMAND_ORDER[1]))
        self.test_market.orderList[7].description = "Demand, Market order, Discontinuous, -50 kW, TS"
        self.test_market.orderList[7].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[7].time_stamp = ACTIVATION_TIME
        self.test_market.orderList[7].vertices[0].quantity = -50.0
        self.test_market.orderList[7].vertices[0].quantity = -50.0

        # ((( 0.1, 0), ( 0.1, -50)), c, 0)
        self.test_market.orderList.append(copy(DEMAND_ORDER[2]))
        self.test_market.orderList[8].description = "Demand, Limit order, Continuous, -50 kW, TS"
        self.test_market.orderList[8].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[8].time_stamp = ACTIVATION_TIME
        self.test_market.orderList[8].vertices[1].quantity = -50.0

        # ( (0.1 ,0 ), (0.1 ,-50), d, 0)
        self.test_market.orderList.append(copy(DEMAND_ORDER[3]))
        self.test_market.orderList[9].description = "Demand, Limit order, Discontinuous, -50 kW, TS"
        self.test_market.orderList[9].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[9].time_stamp = ACTIVATION_TIME
        self.test_market.orderList[9].vertices[1].quantity = -50.0

        # ((( 0.25, 0), ( 0.15, -50)), c, 0)
        self.test_market.orderList.append(copy(DEMAND_ORDER[4]))
        self.test_market.orderList[10].description = "Demand, PWL, Continuous, -50 kW, TS"
        self.test_market.orderList[10].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[10].time_stamp = ACTIVATION_TIME
        self.test_market.orderList[10].vertices[1].quantity = -50.0

        # ((( 0.25, 0), ( 0.15, -50)), d, 0)
        self.test_market.orderList.append(copy(DEMAND_ORDER[5]))
        self.test_market.orderList[11].description = "Demand, PWL, Discontinuous, -50 kW, TS"
        self.test_market.orderList[11].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[11].time_stamp = ACTIVATION_TIME
        self.test_market.orderList[11].vertices[1].quantity = -50.0

        # ((INF, -100), c, -1m)
        self.test_market.orderList.append(copy(DEMAND_ORDER[0]))
        self.test_market.orderList[12].description = "Demand, Market order, Continuous, -100 kW, TS-"
        self.test_market.orderList[12].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[12].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)

        # (( INF, -100), d, -1m)
        self.test_market.orderList.append(copy(DEMAND_ORDER[1]))
        self.test_market.orderList[13].description = "Demand, Market order, Discontinuous, -100 kW, TS-"
        self.test_market.orderList[13].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[13].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)

        # ((( 0.1, 0), ( 0.1, -100)), c, -1m)
        self.test_market.orderList.append(copy(DEMAND_ORDER[2]))
        self.test_market.orderList[14].description = "Demand, Limit order, Continuous, -100 kW, TS-"
        self.test_market.orderList[14].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[14].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)

        # ( (0.1 ,0 ), (0.1 ,-100), d, -1m)
        self.test_market.orderList.append(copy(DEMAND_ORDER[3]))
        self.test_market.orderList[15].description = "Demand, Limit order, Discontinuous, -100 kW, TS-"
        self.test_market.orderList[15].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[15].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)

        # ((( 0.25, 0), ( 0.15, -100)), c, -1m)
        self.test_market.orderList.append(copy(DEMAND_ORDER[4]))
        self.test_market.orderList[16].description = "Demand, PWL, Continuous, -100 kW, TS-"
        self.test_market.orderList[16].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[16].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)

        # ((( 0.25, 0), ( 0.15, -100)), d, -1m)
        self.test_market.orderList.append(copy(DEMAND_ORDER[5]))
        self.test_market.orderList[17].description = "Demand, PWL, Discontinuous, -100 kW, TS-"
        self.test_market.orderList[17].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[17].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)

        # ((INF, -50), c, -1m)
        self.test_market.orderList.append(copy(DEMAND_ORDER[0]))
        self.test_market.orderList[18].description = "Demand, Market order, Continuous, -50 kW, TS-"
        self.test_market.orderList[18].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[18].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[18].vertices[0].quantity = -50.0

        # (( INF, -50), d, -1m)
        self.test_market.orderList.append(copy(DEMAND_ORDER[1]))
        self.test_market.orderList[19].description = "Demand, Market order, Discontinuous, -50 kW, TS-"
        self.test_market.orderList[19].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[19].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[19].vertices[0].quantity = -50.0

        # ((( 0.1, 0), ( 0.1, -50)), c, -1m)
        self.test_market.orderList.append(copy(DEMAND_ORDER[2]))
        self.test_market.orderList[20].description = "Demand, Limit order, Continuous, -50 kW, TS-"
        self.test_market.orderList[20].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[20].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[20].vertices[1].quantity = -50.0

        # ( (0.1 ,0 ), (0.1 ,-50), d, -1m)
        self.test_market.orderList.append(copy(DEMAND_ORDER[3]))
        self.test_market.orderList[21].description = "Demand, Limit order, Discontinuous, -50 kW, TS-"
        self.test_market.orderList[21].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[21].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[21].vertices[1].quantity = -50.0

        # ((( 0.25, 0), ( 0.15, -50)), c, -1m)
        self.test_market.orderList.append(copy(DEMAND_ORDER[4]))
        self.test_market.orderList[22].description = "Demand, PWL, Continuous, -50 kW, TS-"
        self.test_market.orderList[22].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[22].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[22].vertices[1].quantity = -50.0

        # ((( 0.25, 0), ( 0.15, -50)), d, -1m)
        self.test_market.orderList.append(copy(DEMAND_ORDER[5]))
        self.test_market.orderList[23].description = "Demand, PWL, Discontinuous, -50 kW, TS-"
        self.test_market.orderList[23].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[23].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[23].vertices[1].quantity = -50.0

        # STANDING SUPPLY ORDERS ****************************************************************************
        # (( -INF, 100), c, 0)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[0]))
        self.test_market.orderList[24].description = "Supply, Market order, Continuous, 100 kW, TS"
        self.test_market.orderList[24].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[24].time_stamp = ACTIVATION_TIME

        # (( -INF, 100), d, 0)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[1]))
        self.test_market.orderList[25].description = "Supply, Market order, Discontinuous, 100 kW, TS"
        self.test_market.orderList[25].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[25].time_stamp = ACTIVATION_TIME

        # ((( 0.1, 0), (0.1, 100)), c, 0)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[2]))
        self.test_market.orderList[26].description = "Supply, Limit order, Continuous, 100 kW, TS"
        self.test_market.orderList[26].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[26].time_stamp = ACTIVATION_TIME

        # ((( 0.1, 0), (0.1, 100)), d, 0)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[3]))
        self.test_market.orderList[27].description = "Supply, Limit order, Discontinuous, 100 kW, TS"
        self.test_market.orderList[27].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[27].time_stamp = ACTIVATION_TIME

        # ((( 0.15, 0), (0.25, 100)), c, 0)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[4]))
        self.test_market.orderList[28].description = "Supply, PWL, Continuous, 100 kW, TS"
        self.test_market.orderList[28].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[28].time_stamp = ACTIVATION_TIME

        # ((( 0.15, 0), (0.25, 100)), d, 0)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[5]))
        self.test_market.orderList[29].description = "Supply, PWL, Discontinuous, 100 kW, TS"
        self.test_market.orderList[29].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[29].time_stamp = ACTIVATION_TIME

        # (( -INF, 50), c, 0)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[0]))
        self.test_market.orderList[30].description = "Supply, Market order, Continuous, 50 kW, TS"
        self.test_market.orderList[30].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[30].time_stamp = ACTIVATION_TIME
        self.test_market.orderList[30].vertices[0].quantity = 50.0
        self.test_market.orderList[30].vertices[0].continuity = True

        # (( -INF, 50), d, 0)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[1]))
        self.test_market.orderList[31].description = "Supply, Market order, Discontinuous, 50 kW, TS"
        self.test_market.orderList[31].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[31].time_stamp = ACTIVATION_TIME
        self.test_market.orderList[31].vertices[0].quantity = 50.0
        assert self.test_market.orderList[31].vertices[0].continuity is False, "PROBLEM!!"

        # ((( 0.1, 0), (0.1, 50)), c, 0)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[2]))
        self.test_market.orderList[32].description = "Supply, Limit order, Continuous, 50 kW, TS"
        self.test_market.orderList[32].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[32].time_stamp = ACTIVATION_TIME
        self.test_market.orderList[32].vertices[1].quantity = 50.0
        assert self.test_market.orderList[32].vertices[1].continuity is True, "PROBLEM!!"

        # ((( 0.1, 0), (0.1, 50)), d, 0)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[3]))
        self.test_market.orderList[33].description = "Supply, Limit order, Discontinuous, 50 kW, TS"
        self.test_market.orderList[33].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[33].time_stamp = ACTIVATION_TIME
        self.test_market.orderList[33].vertices[1].quantity = 50.0
        assert self.test_market.orderList[33].vertices[1].continuity is False, "PROBLEM!!"

        # ((( 0.15, 0), (0.25, 50)), c, 0)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[4]))
        self.test_market.orderList[34].description = "Supply, PWL, Continuous, 50 kW, TS"
        self.test_market.orderList[34].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[34].time_stamp = ACTIVATION_TIME
        self.test_market.orderList[34].vertices[1].quantity = 50.0
        assert self.test_market.orderList[34].vertices[1].continuity is True, "PROBLEM!!"

        # ((( 0.15, 0), (0.25, 50)), d, 0)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[5]))
        self.test_market.orderList[35].description = "Supply, PWL, Discontinuous, 50 kW, TS"
        self.test_market.orderList[35].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[35].time_stamp = ACTIVATION_TIME
        self.test_market.orderList[35].vertices[1].quantity = 50.0
        assert self.test_market.orderList[35].vertices[1].continuity is False, "PROBLEM!!"

        # (( -INF, 100), c, -1m)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[0]))
        self.test_market.orderList[36].description = "Supply, Market order, Continuous, 100 kW, TS-"
        self.test_market.orderList[36].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[36].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[36].vertices[0].continuity = True

        # (( -INF, 100), d, -1m)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[1]))
        self.test_market.orderList[37].description = "Supply, Market order, Discontinuous, 100 kW, TS-"
        self.test_market.orderList[37].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[37].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        assert self.test_market.orderList[37].vertices[0].continuity is False, "PROBLEM!!"

        # ((( 0.1, 0), (0.1, 100)), c, -1m)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[2]))
        self.test_market.orderList[38].description = "Supply, Limit order, Continuous, 100 kW, TS-"
        self.test_market.orderList[38].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[38].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        assert self.test_market.orderList[38].vertices[1].continuity is True, "PROBLEM!!"

        # ((( 0.1, 0), (0.1, 100)), d, -1m)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[3]))
        self.test_market.orderList[39].description = "Supply, Limit order, Discontinuous, 100 kW, TS-"
        self.test_market.orderList[39].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[39].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        assert self.test_market.orderList[39].vertices[1].continuity is False, "PROBLEM!!"

        # ((( 0.15, 0), (0.25, 100)), c, -1m)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[4]))
        self.test_market.orderList[40].description = "Supply, PWL, Continuous, 100 kW, TS-"
        self.test_market.orderList[40].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[40].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        assert self.test_market.orderList[40].vertices[1].continuity is True, "PROBLEM!!"

        # ((( 0.15, 0), (0.25, 100)), d, -1m)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[5]))
        self.test_market.orderList[41].description = "Supply, PWL, Discontinuous, 100 kW, TS-"
        self.test_market.orderList[41].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[41].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        assert self.test_market.orderList[41].vertices[1].continuity is False, "PROBLEM!!"

        # (( -INF, 50), c, -1m)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[0]))
        self.test_market.orderList[42].description = "Supply, Market order, Continuous, 50 kW, TS-"
        self.test_market.orderList[42].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[42].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[42].vertices[0].quantity = 50.0
        self.test_market.orderList[42].vertices[0].continuity = True

        # (( -INF, 50), d, -1m)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[1]))
        self.test_market.orderList[43].description = "Supply, Market order, Discontinuous, 50 kW, TS-"
        self.test_market.orderList[43].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[43].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[43].vertices[0].quantity = 50.0
        assert self.test_market.orderList[43].vertices[0].continuity is False, "PROBLEM!!"

        # ((( 0.1, 0), (0.1, 50)), c, -1m)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[2]))
        self.test_market.orderList[44].description = "Supply, Limit order, Continuous, 50 kW, TS-"
        self.test_market.orderList[44].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[44].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[44].vertices[1].quantity = 50.0
        assert self.test_market.orderList[44].vertices[1].continuity is True, "PROBLEM!!"

        # ((( 0.1, 0), (0.1, 50)), d, -1m)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[3]))
        self.test_market.orderList[45].description = "Supply, Limit order, Discontinuous, 50 kW, TS-"
        self.test_market.orderList[45].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[45].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[45].vertices[1].quantity = 50.0
        assert self.test_market.orderList[45].vertices[1].continuity is False, "PROBLEM!!"

        # ((( 0.15, 0), (0.25, 50)), c, -1m)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[4]))
        self.test_market.orderList[46].description = "Supply, PWL, Continuous, 50 kW, TS-"
        self.test_market.orderList[46].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[46].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[46].vertices[1].quantity = 50.0
        assert self.test_market.orderList[46].vertices[1].continuity is True, "PROBLEM!!"

        # ((( 0.15, 0), (0.25, 50)), d, -1m)
        self.test_market.orderList.append(copy(SUPPLY_ORDER[5]))
        self.test_market.orderList[47].description = "Supply, PWL, Discontinuous, 50 kW, TS-"
        self.test_market.orderList[47].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[47].time_stamp = ACTIVATION_TIME - timedelta(minutes=1)
        self.test_market.orderList[47].vertices[1].quantity = 50.0
        assert self.test_market.orderList[47].vertices[1].continuity is False, "PROBLEM!!"

        self.test_market.matchList = []

    def tearDown(self):
        self.test_market = None

    def case1(self):
        print("Case 1: No new order provided as an argument.")
        self.setUp()
        initial_order_count = len(self.test_market.orderList)
        self.test_market.match()
        tc.assertEqual(
            len(self.test_market.orderList),
            initial_order_count,
            "The number of standing orders should not have changed."
        )
        tc.assertEqual(
            len(self.test_market.matchList),
            0,
            "No orders should have been matched."
        )
        self.tearDown()

    def case2(self):
        print("Case 2: There are no Standing orders.")
        self.setUp()
        self.test_market.orderList = []
        assert len(self.test_market.orderList) == 0, 'The order book should be empty at the beginning of this test.'
        new_order = copy(DEMAND_ORDER[0])
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.orderList),
            1,
            "The number of standing orders should have increased by one."
        )
        tc.assertEqual(
            self.test_market.orderList[0],
            new_order,
            "The new order should be in the order book now."
        )
        tc.assertEqual(
            len(self.test_market.matchList),
            0,
            "No orders should have been matched."
        )
        self.tearDown()

    def case3(self):
        print("Case 3: Perfect match between a single aggressive demand market order and single standing supply market "
              "order.")
        self.setUp()
        self.test_market.orderList = [copy(SUPPLY_ORDER[0])]
        self.test_market.orderList[0].status = OrderStatus.STANDING_ORDER
        original_so_id = self.test_market.orderList[0].id
        new_order: Order = copy(SUPPLY_ORDER[0])
        new_order.supply_or_demand = SupplyOrDemand.DEMAND
        new_order.vertices[0].quantity = - SUPPLY_ORDER[0].vertices[0].quantity
        new_order = new_order.check_demand_order()
        assert len(self.test_market.orderList) == 1, \
            'The number of standing orders at the beginning of the test is wrong.'
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.orderList),
            0,
            "The number of standing orders should have decreased by one."
        )
        tc.assertFalse(
            any([x == new_order for x in self.test_market.orderList]),
            "This demand order should have been removed from the order book."
        )
        tc.assertEqual(
            len(self.test_market.matchList),
            2,
            "Both the aggressive order and one of the standing orders should have been matched."
        )
        tc.assertTrue(
            all([abs(x.vertices[0].quantity) == 100.0 for x in self.test_market.matchList]),
            "Both the matches should have had quantity magnitude 110.0."
        )
        tc.assertTrue(
            any([x.parent_order_id == new_order.id for x in self.test_market.matchList]),
            "Parentage can be traced to the aggressive order ID."
        )
        tc.assertTrue(
            any([x.parent_order_id == original_so_id for x in self.test_market.matchList]),
            "Parentage can be traced to the original standing order ID."
        )
        self.tearDown()

    def case4(self):
        print("Case 4: Aggressive demand market order quantity is twice that of the lone continuous standing supply "
              "market order.")
        self.setUp()
        self.test_market.orderList = copy([SUPPLY_ORDER[0]])
        self.test_market.orderList[0].status = OrderStatus.STANDING_ORDER
        original_so_id = self.test_market.orderList[0].id
        new_order: Order = copy(SUPPLY_ORDER[0])
        new_order.supply_or_demand = SupplyOrDemand.DEMAND
        new_order.vertices[0].quantity = - 2.0 * SUPPLY_ORDER[0].vertices[0].quantity
        new_order = new_order.check_demand_order()
        assert len(self.test_market.orderList) == 1, \
            'The number of standing orders at the beginning of the test is wrong.'
        assert self.test_market.orderList[0].vertices[0].continuity, \
            'This case presumes a continuous standing supply market order.'
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.orderList),
            1,
            "One order should remain in the order book."
        )
        tc.assertEqual(
            self.test_market.orderList[0].supply_or_demand,
            SupplyOrDemand.DEMAND,
            "The lone order on the order book should be a demand order."
        )
        tc.assertEqual(
            self.test_market.orderList[0].vertices[0].quantity,
            -100.0,
            "Half the magnitude of the aggressive demand order should remain in the order book."
        )
        tc.assertFalse(
            new_order in [x for x in self.test_market.orderList],
            "This demand order should have been removed from the order book."
        )
        tc.assertEqual(
            len(self.test_market.matchList),
            2,
            "Both the aggressive order and one of the standing orders should have been matched."
        )
        tc.assertTrue(
            all([abs(x.vertices[0].quantity) == 100.0 for x in self.test_market.matchList]),
            "Both the matches should have had quantity magnitude 110.0."
        )
        tc.assertTrue(
            new_order.id in [x.parent_order_id for x in self.test_market.matchList],
            "Parentage can be traced to the aggressive order ID."
        )
        tc.assertTrue(
            any([x.parent_order_id == original_so_id for x in self.test_market.matchList]),
            "Parentage can be traced to the original standing order ID."
        )
        self.tearDown()

    def case5(self):
        print("Case 5: Aggressive demand market order quantity is half that of the lone continuous standing supply "
              "market order.")
        self.setUp()
        self.test_market.orderList = copy([SUPPLY_ORDER[0]])
        self.test_market.orderList[0].status = OrderStatus.STANDING_ORDER
        original_so_id = self.test_market.orderList[0].id
        new_order: Order = copy(SUPPLY_ORDER[0])
        new_order.supply_or_demand = SupplyOrDemand.DEMAND
        new_order.vertices[0].quantity = - 0.5 * SUPPLY_ORDER[0].vertices[0].quantity
        new_order = new_order.check_demand_order()
        assert len(self.test_market.orderList) == 1, \
            'The number of standing orders at the beginning of the test is wrong.'
        assert new_order.vertices[0].continuity, \
            'This case presumes that the aggressive order is continuous, partially matchable.'
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.orderList),
            1,
            "Both the original standing order and new aggressive order should be in the order book."
        )
        tc.assertEqual(
            self.test_market.orderList[0].supply_or_demand,
            SupplyOrDemand.SUPPLY,
            "The lone order on the order book should be a supply order."
        )
        tc.assertEqual(
            self.test_market.orderList[0].vertices[0].quantity,
            50.0,
            "Half the magnitude of the aggressive supply order should remain in the order book."
        )
        tc.assertEqual(
            len(self.test_market.matchList),
            2,
            "Both the aggressive order and one of the standing orders should have been matched."
        )
        tc.assertTrue(
            all([abs(x.vertices[0].quantity) == 50.0 for x in self.test_market.matchList]),
            "Both the matches should have had quantity magnitude 55.0."
        )
        tc.assertTrue(
            new_order.id in [x.parent_order_id for x in self.test_market.matchList],
            "Parentage can be traced to the aggressive order ID."
        )
        tc.assertTrue(
            any([x.parent_order_id == original_so_id for x in self.test_market.matchList]),
            "Parentage can be traced to the original standing order ID."
        )
        self.tearDown()

    def case6(self):
        print("Case 6: Aggressive discontinuous demand market order quantity is twice that of the lone standing supply "
              "market order.")
        self.setUp()
        self.test_market.orderList = copy([SUPPLY_ORDER[0]])
        self.test_market.orderList[0].status = OrderStatus.STANDING_ORDER
        original_so_id = self.test_market.orderList[0].id
        new_order: Order = copy(SUPPLY_ORDER[0])
        new_order.supply_or_demand = SupplyOrDemand.DEMAND
        new_order.vertices[0].quantity = - 2.0 * SUPPLY_ORDER[0].vertices[0].quantity
        new_order = new_order.check_demand_order()
        new_order.vertices[0].continuity = False
        assert len(self.test_market.orderList) == 1, \
            'The number of standing orders at the beginning of the test is wrong.'
        assert not new_order.vertices[0].continuity, \
            'This case presumes a discontinuous new order.'
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.orderList),
            2,
            "One original standing order and new order should be in the order book."
        )
        tc.assertSetEqual(
            set([x.supply_or_demand for x in self.test_market.orderList]),
            {SupplyOrDemand.SUPPLY, SupplyOrDemand.DEMAND},
            "There should be one supply order and one demand order in the order book."
        )
        tc.assertSetEqual(
            set([x.vertices[0].quantity for x in self.test_market.orderList]),
            {-200.0, 100.0},
            "The quantities in the order book were not as expected."
        )
        tc.assertTrue(
            new_order in [x for x in self.test_market.orderList],
            "This demand order should have been added to the order book."
        )
        tc.assertTrue(
            original_so_id in [x.id for x in self.test_market.orderList],
            "This original standing order should remain in the order book."
        )
        tc.assertEqual(
            len(self.test_market.matchList),
            0,
            "No matches should have been transacted."
        )
        self.tearDown()

    def case7(self):
        print("Case 7: Aggressive demand market order quantity is half that of the lone discontinuous standing supply "
              "market order.")
        self.setUp()
        self.test_market.orderList = copy([SUPPLY_ORDER[0]])
        self.test_market.orderList[0].status = OrderStatus.STANDING_ORDER
        self.test_market.orderList[0].vertices[0].continuity = False
        new_order: Order = copy(SUPPLY_ORDER[0])
        new_order.supply_or_demand = SupplyOrDemand.DEMAND
        new_order.vertices[0].quantity = - 0.5 * SUPPLY_ORDER[0].vertices[0].quantity
        new_order = new_order.check_demand_order()
        assert len(self.test_market.orderList) == 1, \
            'The number of standing orders at the beginning of the test is wrong.'
        assert not self.test_market.orderList[0].vertices[0].continuity, \
            'This case presumes that the standing order is discontinuous, not partially matchable.'
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.orderList),
            2,
            "Both the original standing order and new order should be in the order book."
        )
        tc.assertListEqual(
            [x.supply_or_demand for x in self.test_market.orderList],
            [SupplyOrDemand.SUPPLY, SupplyOrDemand.DEMAND],
            "The order book should have one supply and one demand order."
        )
        tc.assertSetEqual(
            set([x.vertices[0].quantity for x in self.test_market.orderList]),
            {-50.0, 100.0},
            "The correct order Vertex quantities were not found in the order book."
        )
        tc.assertTrue(
            new_order in [x for x in self.test_market.orderList],
            "This demand order should have been found in the order book."
        )
        tc.assertEqual(
            len(self.test_market.matchList),
            0,
            "No matches should have been transacted."
        )
        self.tearDown()

    def case8(self):
        print("Case 8: Excite just supply market orders.")
        self.setUp()
        initial_order_count = len(self.test_market.orderList)
        new_order = copy(DEMAND_ORDER[0])
        new_order.vertices[0].continuity = True
        new_order.vertices[0].quantity = -40.0
        self.test_market.match(new_order)
        tc.assertEqual(
            initial_order_count,
            len(self.test_market.orderList),
            "The number of standing orders should have stayed the same.")

        self.tearDown()

    def case9(self):
        print("Case 9: Match small,continuous demand market order from standing orders using Volume Priority.")
        print("        Note: As of 11/1/2023, linear regions of standing orders and complex order components are "
              "ignored.")
        self.setUp()
        new_order = copy(DEMAND_ORDER[0])
        new_order.vertices[0].quantity = -25.0
        new_order.vertices[0].coninuity = True
        self.test_market.matchingStrategy = [MatchingStrategy.ORDER_VOLUME]
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.matchList),
            2,
            "There should have been two matched orders."
        )
        tc.assertEqual(
            self.test_market.matchList[0].vertices[0].quantity,
            25.0,
            "The match should have been made with a 25 kW supply market order."
        )
        self.tearDown()

    def case10(self):
        print("Case 10: Match intermediate,continuous demand market order from standing orders using Volume Priority.")
        print("         Note: As of 11/1/2023, linear regions of standing orders and complex order components are "
              "ignored.")
        self.setUp()
        new_order = copy(DEMAND_ORDER[0])
        new_order.vertices[0].quantity = -125.0
        new_order.vertices[0].coninuity = True
        self.test_market.matchingStrategy = [MatchingStrategy.ORDER_VOLUME]
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.matchList),
            4,
            "There should have been four matched orders."
        )
        vlist = []
        for x in self.test_market.matchList:
            vlist.extend(x.vertices)
        tc.assertEqual(
            sum([x.quantity for x in vlist]),
            0.0,
            "Matches should have enforced balance of supply and demand."
        )
        tc.assertTrue(
            all([abs(x.quantity) in [25.0, 100] for x in vlist]),
            "The match should have been made with a 25 kW supply market order."
        )
        self.tearDown()

    def case11(self):
        print("Case 11: Match large,continuous demand market order from standing orders using Volume Priority.")
        print("         Note: As of 11/1/2023, linear regions of standing orders and complex order components are "
              "ignored.")
        self.setUp()
        new_order = copy(DEMAND_ORDER[0])
        new_order.vertices[0].quantity = -525.0
        new_order.vertices[0].coninuity = True
        self.test_market.matchingStrategy = [MatchingStrategy.ORDER_VOLUME]
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.matchList),
            14,
            "There should have been 14 matched orders."
        )
        vlist = []
        for x in self.test_market.matchList:
            vlist.extend(x.vertices)
        tc.assertEqual(
            sum([x.quantity for x in vlist]),
            0.0,
            "Matches should have enforced balance of supply and demand."
        )
        tc.assertTrue(
            all([abs(x.quantity) in [25.0, 50, 100] for x in vlist]),
            "The match should have been made with a 25 kW supply market order."
        )
        self.tearDown()

    def case12(self):
        print("Case 12: Match large,continuous supply market order from standing orders using Volume Priority.")
        print("         Note: As of 11/1/2023, linear regions of standing orders and complex order components are "
              "ignored.")
        self.setUp()
        new_order = copy(SUPPLY_ORDER[0])
        new_order.vertices[0].quantity = 525.0
        new_order.vertices[0].coninuity = True
        self.test_market.matchingStrategy = [MatchingStrategy.ORDER_VOLUME]
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.matchList),
            14,
            "There should have been 14 matched orders."
        )
        vlist = []
        for x in self.test_market.matchList:
            vlist.extend(x.vertices)
        tc.assertEqual(
            sum([x.quantity for x in vlist]),
            0.0,
            "Matches should have enforced balance of supply and demand."
        )
        tc.assertTrue(
            all([abs(x.quantity) in [25.0, 50, 100] for x in vlist]),
            "The match should have been made with a 25 kW supply market order."
        )
        self.tearDown()

    def case13(self):
        print("Case 13: Match large,continuous demand market order from standing orders using age Priority.")
        print("         Note: As of 11/1/2023, linear regions of standing orders and complex order components are "
              "ignored.")
        self.setUp()
        new_order = copy(DEMAND_ORDER[0])
        new_order.vertices[0].quantity = -525.0
        new_order.vertices[0].coninuity = True
        self.test_market.matchingStrategy = [MatchingStrategy.ORDER_AGE]
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.matchList),
            14,
            "There should have been 14 matched orders."
        )
        vlist = []
        for x in self.test_market.matchList:
            vlist.extend(x.vertices)
        tc.assertEqual(
            sum([x.quantity for x in vlist]),
            0.0,
            "Matches should have enforced balance of supply and demand."
        )
        tc.assertTrue(
            all([abs(x.quantity) in [25.0, 50, 100] for x in vlist]),
            "The match should have been made with a 25 kW supply market order."
        )
        self.tearDown()

    def case14(self):
        print("Case 14: Match large,continuous supply market order from standing orders using age priority.")
        print("         Note: As of 11/1/2023, linear regions of standing orders and complex order components are "
              "ignored.")
        self.setUp()
        new_order = copy(SUPPLY_ORDER[0])
        new_order.vertices[0].quantity = 525.0
        new_order.vertices[0].coninuity = True
        self.test_market.matchingStrategy = [MatchingStrategy.ORDER_AGE]
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.matchList),
            14,
            "There should have been 14 matched orders."
        )
        vlist = []
        for x in self.test_market.matchList:
            vlist.extend(x.vertices)
        tc.assertEqual(
            sum([x.quantity for x in vlist]),
            0.0,
            "Matches should have enforced balance of supply and demand."
        )
        tc.assertTrue(
            all([abs(x.quantity) in [25.0, 50, 100] for x in vlist]),
            "The match should have been made with a 25 kW supply market order."
        )
        self.tearDown()

    def case15(self):
        print("Case 15: Match very large,continuous demand market order from standing orders using volume Priority.")
        print("         Note: As of 11/1/2023, linear regions of standing orders and complex order components are "
              "ignored.")
        self.setUp()
        new_order = copy(DEMAND_ORDER[0])
        new_order.vertices[0].quantity = -1575.0
        new_order.vertices[0].coninuity = True
        self.test_market.matchingStrategy = [MatchingStrategy.ORDER_VOLUME]
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.matchList),
            40,
            "There should have been 40 matched orders."
        )
        vlist = []
        for x in self.test_market.matchList:
            vlist.extend(x.vertices)
        tc.assertEqual(
            sum([x.quantity for x in vlist]),
            0.0,
            "Matches should have enforced balance of supply and demand."
        )
        tc.assertTrue(
            all([abs(x.quantity) in [0.0, 25.0, 50, 75, 100] for x in vlist]),
            "The match should have been made with a 25 kW supply market order."
        )
        self.tearDown()

    def case16(self):
        print("Case 16: Match very large,continuous supply market order from standing orders using volume priority.")
        print("         Note: As of 11/1/2023, linear regions of standing orders and complex order components are "
              "ignored.")
        self.setUp()
        new_order = copy(SUPPLY_ORDER[0])
        new_order.vertices[0].quantity = 1575.0
        new_order.vertices[0].coninuity = True
        self.test_market.matchingStrategy = [MatchingStrategy.ORDER_VOLUME]
        self.test_market.match(new_order)
        tc.assertEqual(
            len(self.test_market.matchList),
            40,
            "There should have been 40 matched orders."
        )
        vlist = []
        for x in self.test_market.matchList:
            vlist.extend(x.vertices)
        tc.assertEqual(
            sum([x.quantity for x in vlist]),
            0.0,
            "Matches should have enforced balance of supply and demand."
        )
        tc.assertTrue(
            all([abs(x.quantity) in [0.0, 25.0, 50, 100] for x in vlist]),
            "The match should have been made with a 25 kW supply market order."
        )
        self.tearDown()


class test_get_order_by_id:
    def __init__(self):
        self.test_market = None

    def setUp(self):
        self.test_market = MARKET
        self.test_market.orderList = [copy(DEMAND_ORDER[0]), copy(DEMAND_ORDER[1])]
        self.test_market.matchList = [copy(SUPPLY_ORDER[0]), copy(SUPPLY_ORDER[1])]

    def tearDown(self):
        self.test_market = None

    def case1(self):
        print("Case 1: No id provided.")
        self.setUp()
        result = self.test_market.get_order_by_id()
        tc.assertIsNone(result, "The method should return None if no argument ID is provided.")
        self.tearDown()

    def case2(self):
        print("Case 2: Unique id match to Order object in the order book.")
        self.setUp()
        test_order = self.test_market.orderList[0]
        test_id = test_order.id
        result = self.test_market.get_order_by_id(an_id=test_id)
        tc.assertEqual(test_order, result,
                       "The method should have found an Order object from the order book.")
        self.tearDown()

    def case3(self):
        print("Case 3: ID, but no match.")
        self.setUp()
        test_id = 0
        assert not any([x.id == test_id for x in self.test_market.orderList + self.test_market.matchList]), \
            'The test ID should not have matched any orders in this market.'
        result = self.test_market.get_order_by_id(an_id=test_id)
        tc.assertIsNone(result, "The method should not have found an Order object from the order book.")
        self.tearDown()

    def case4(self):
        print("Case 4: Unique ID match to Order object in the list of match transactions.")
        self.setUp()
        test_order = self.test_market.matchList[0]
        test_id = test_order.id
        result = self.test_market.get_order_by_id(an_id=test_id)
        tc.assertEqual(test_order, result,
                       "The method should have found an Order object from the matched order list.")
        self.tearDown()


class test_order_id_is_unique:
    def __init__(self):
        self.test_market = None

    def setUp(self):
        self.test_market = MARKET
        self.test_market.orderList = [DEMAND_ORDER[0], DEMAND_ORDER[1], SUPPLY_ORDER[0], SUPPLY_ORDER[1]]

    def tearDown(self):
        self.test_market = None

    def case1(self):
        print("Case 1: No id provided as an argument.")
        self.setUp()
        tc.assertIsNone(self.test_market.order_id_is_unique(), "The method should return None if no ID is provided.")
        self.tearDown()

    def case2(self):
        print("Case 2: The provided ID matches that of an order in the unmatched order book.")
        self.setUp()
        test_id = self.test_market.orderList[0].id
        assert any([x.id == test_id for x in self.test_market.orderList]), \
            'The test ID should have matched the first order in the list.'
        tc.assertFalse(self.test_market.order_id_is_unique(an_id=test_id),
                       "The method should return False if a match is found.")
        self.tearDown()

    def case3(self):
        print("Case 3: The provided ID does not match any of the order IDs in the order book.")
        self.setUp()
        test_id = 0
        assert not any([x.id == test_id for x in self.test_market.orderList]), 'ID should have been unique'
        tc.assertIsNone(self.test_market.order_id_is_unique(an_id=test_id),
                        "The method should return None if no match is found.")
        self.tearDown()

    def case4(self):
        print("Case 4: The provided ID matches that of an order in the list of match transactions.")
        self.setUp()
        self.test_market.matchList = [SUPPLY_ORDER[3]]
        test_id = SUPPLY_ORDER[3].id
        assert any([x.id == test_id for x in self.test_market.matchList]), \
            'The test ID should have matched the order in the matched order list.'
        tc.assertFalse(self.test_market.order_id_is_unique(an_id=test_id),
                       "The method should return False if a match is found.")
        self.tearDown()


class test_spawn_markets:
    def __init__(self):
        self.test_market: MARKET = None
        self.my_transactive_node: TransactiveNode or None = None

    def setUp(self):
        self.test_market: MatchingEngine = MARKET
        self.my_transactive_node: TransactiveNode = TransactiveNode()
        self.my_transactive_node.markets = []

    def tearDown(self):
        self.test_market = None
        self.my_transactive_node = None

    def case1(self):
        print("Case 1: Normal case. New MatchingEngine object is spawned with correct attributes.")
        self.setUp()
        self.test_market.spawn_markets(
            my_transactive_node=self.my_transactive_node,
            new_market_clearing_time=datetime(
                year=2023,
                month=9,
                day=19,
                hour=17,
                second=0,
                microsecond=0
            )
        )
        tc.assertEqual(len(self.my_transactive_node.markets), 1,
                       "There should be only one new market.")
        new_market = self.my_transactive_node.markets[0]
        tc.assertEqual(new_market.deliveryLeadTime, self.test_market.deliveryLeadTime,
                       "The new market should have the same delivery lead time.")
        tc.assertEqual(new_market.deliveryStartTime, new_market.marketClearingTime + self.test_market.deliveryLeadTime,
                       "The new market's delivery start time is wrong.")
        tc.assertTrue(new_market.hasMarket, "The new market should have a matching engine market.")
        tc.assertEqual(new_market.intervalDuration, self.test_market.intervalDuration,
                       "The new market should inherit its interval duration from this one.")
        tc.assertEqual(new_market.marketSeriesName, self.test_market.marketSeriesName,
                       "The new market should inherit its market series name from this one.")
        tc.assertEqual(new_market.matchingStrategy, self.test_market.matchingStrategy,
                       "The new market should inherit its matching strategy from this one.")
        tc.assertEqual(new_market.negotiationLeadTime, self.test_market.negotiationLeadTime,
                       "The new market should inherit its matching strategy from this one.")
        tc.assertEqual(new_market.priorMarketInSeries, self.test_market,
                       "The new market should point to this one as its prior market in the market series.")
        tc.assertFalse(self.test_market.isNewestMarket,
                       "This market should no longer be the newest one in the market series.")
        tc.assertTrue(new_market.isNewestMarket,
                      "The new market should be the newest market in the market series.")
        tc.assertEqual(self.my_transactive_node.markets, [new_market],
                       "the new market should be listed among my transactive node markets.")
        tc.assertEqual(new_market.marketState, MarketState.Negotiation,
                       "The new market should be initialized in its negotiation state.")
        self.tearDown()


class test_while_in_negotiation:
    def __init__(self):
        pass

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def case1(self):
        print("Case 1: No new standing orders.")
        self.setUp()
        self.tearDown()

    def case2(self):
        print("Case 2: One new standing order.")
        self.setUp()
        self.tearDown()

    def case3(self):
        print("Case 3: Multiple new standing orders.")
        self.setUp()
        self.tearDown()

    def case4(self):
        print("Case 4: Market state machine starts normally in negotiation state.")
        self.setUp()
        self.tearDown()

    def case5(self):
        print("Case 5: Market state machine starts BEFORE negotiation state.")
        self.setUp()
        self.tearDown()

    def case6(self):
        print("Case 6: Let negotiation state time out.")
        self.setUp()
        self.tearDown()


class test_get_counteroffers:
    def __init__(self):
        self.test_market = None

    def setUp(self):
        self.test_market = MARKET

        self.test_market.orderList = []

        self.test_market.orderList.append(copy(DEMAND_ORDER[0]))
        self.test_market.orderList[0].status = OrderStatus.STANDING_ORDER

        self.test_market.orderList.append(copy(DEMAND_ORDER[1]))
        self.test_market.orderList[1].status = OrderStatus.CANCELLED

        self.test_market.orderList.append(copy(SUPPLY_ORDER[0]))
        self.test_market.orderList[2].status = OrderStatus.REVISED_STANDING_ORDER

        self.test_market.orderList.append(copy(SUPPLY_ORDER[1]))
        self.test_market.orderList[3].status = OrderStatus.SUPERSEDED

        self.test_market.orderList.append(copy(SUPPLY_ORDER[2]))
        self.test_market.orderList[4].status = OrderStatus.NEW

    def tearDown(self):
        self.test_market = None

    def case1(self):
        print("Case 1: No counteroffers for aggressive demand bid.")
        self.setUp()
        self.test_market.orderList = [x for x in self.test_market.orderList
                                      if x.supply_or_demand == SupplyOrDemand.DEMAND]
        result = self.test_market.get_counteroffers(supply_or_demand=SupplyOrDemand.SUPPLY)
        tc.assertEqual(len(self.test_market.orderList), 2,
                       "There should have been 2 demand orders in the order book.")
        tc.assertEqual(result, [],
                       "The method should have found no valid supply counteroffer.")
        self.tearDown()

    def case2(self):
        print("Case 2: No counteroffers for aggressive supply offer.")
        self.setUp()
        self.test_market.orderList = [x for x in self.test_market.orderList
                                      if x.supply_or_demand == SupplyOrDemand.SUPPLY]
        result = self.test_market.get_counteroffers(supply_or_demand=SupplyOrDemand.DEMAND)
        tc.assertEqual(len(self.test_market.orderList), 3,
                       "There should have been 3 supply orders in the order book.")
        tc.assertEqual(result, [],
                       "There should have been no list entries because there were no valid demand counteroffers.")
        self.tearDown()

    def case3(self):
        print("Case 3: One valid counteroffer for an aggressive demand bid.")
        self.setUp()
        result = self.test_market.get_counteroffers(supply_or_demand=SupplyOrDemand.SUPPLY)
        tc.assertEqual(len(self.test_market.orderList), 5,
                       "There should have been 2 demand orders in the order book.")
        tc.assertEqual(result, [self.test_market.orderList[2]],
                       "The method should have returned this one valid supply counteroffer.")
        self.tearDown()

    def case4(self):
        print("Case 4: One counteroffer for aggressive supply offer.")
        self.setUp()
        result = self.test_market.get_counteroffers(supply_or_demand=SupplyOrDemand.DEMAND)
        tc.assertEqual(len(self.test_market.orderList), 5,
                       "There should have been 5 orders in the order book.")
        tc.assertEqual(result, [self.test_market.orderList[0]],
                       "The method should have returned this one valid demand counteroffer.")
        self.tearDown()

    def case5(self):
        print("Case 5: Multiple counteroffers for aggressive demand bid.")
        self.setUp()
        test_supply_order = copy(SUPPLY_ORDER[4])
        test_supply_order.status = OrderStatus.STANDING_ORDER
        self.test_market.orderList.append(test_supply_order)
        result = self.test_market.get_counteroffers(supply_or_demand=SupplyOrDemand.SUPPLY)
        tc.assertEqual(len(self.test_market.orderList), 6,
                       "There should have been 6 orders in the order book.")
        tc.assertEqual(result, [self.test_market.orderList[2], self.test_market.orderList[5]],
                       "The method should have returned these two valid supply counteroffers.")
        self.tearDown()

    def case6(self):
        print("Case 6: Multiple counteroffers for aggressive supply offer.")
        self.setUp()
        test_demand_order = copy(DEMAND_ORDER[3])
        test_demand_order.status = OrderStatus.STANDING_ORDER
        self.test_market.orderList.append(test_demand_order)
        result = self.test_market.get_counteroffers(supply_or_demand=SupplyOrDemand.DEMAND)
        tc.assertEqual(len(self.test_market.orderList), 6,
                       "There should have been 6 orders in the order book.")
        tc.assertEqual(result, [self.test_market.orderList[0], self.test_market.orderList[5]],
                       "The method should have returned these two valid demand counteroffers.")
        self.tearDown()


class test_get_counteroffer_vertices:
    def __init__(self):
        self.test_market = None
        print("NOTES: "
              "\n- This test invokes MatchingEngine.get_counteroffers()"
              "\n- This test invokes Order.get_quantity_from_price()"
              "\n- This test also exercises various price points that affect the Vertices.")

    def setUp(self):
        self.test_market = MARKET

        self.test_market.orderList = []

        self.test_market.orderList.append(copy(DEMAND_ORDER[0]))
        self.test_market.orderList[0].status = OrderStatus.STANDING_ORDER  # <<<<< Valid * continuous -100 kW

        self.test_market.orderList.append(copy(DEMAND_ORDER[1]))
        self.test_market.orderList[1].status = OrderStatus.NEW

        self.test_market.orderList.append(copy(DEMAND_ORDER[2]))
        self.test_market.orderList[2].status = OrderStatus.CANCELLED

        self.test_market.orderList.append(copy(DEMAND_ORDER[3]))
        self.test_market.orderList[3].status = OrderStatus.SUPERSEDED

        self.test_market.orderList.append(copy(DEMAND_ORDER[4]))
        self.test_market.orderList[4].status = OrderStatus.STANDING_ORDER  # <<<<< Valid and continuous -100 kW

        self.test_market.orderList.append(copy(DEMAND_ORDER[5]))
        self.test_market.orderList[5].status = OrderStatus.STANDING_ORDER  # <<<<< Valid and discontinuous -100 kW

        self.test_market.orderList.append(copy(SUPPLY_ORDER[0]))
        self.test_market.orderList[6].status = OrderStatus.EXPIRED

        self.test_market.orderList.append(copy(SUPPLY_ORDER[1]))
        self.test_market.orderList[7].status = OrderStatus.CANCELLED

        self.test_market.orderList.append(copy(SUPPLY_ORDER[2]))
        self.test_market.orderList[8].status = OrderStatus.REVISED_STANDING_ORDER  # <<<<<< Valid & continuous 100.0 kW

        self.test_market.orderList.append(copy(SUPPLY_ORDER[3]))
        self.test_market.orderList[9].status = OrderStatus.STANDING_ORDER  # <<<<<<<<<< Valid & discontinuous 100.0 kW

        self.test_market.orderList.append(copy(SUPPLY_ORDER[4]))
        self.test_market.orderList[10].status = OrderStatus.NEW_REVISED

        self.test_market.orderList.append(copy(SUPPLY_ORDER[5]))
        self.test_market.orderList[11].status = OrderStatus.EXPIRED

    def tearDown(self):
        self.test_market = None

    def case1(self):
        print("Case 1: Retrieve all valid demand Vertices at a low price, regardless of continuity.")
        self.setUp()
        test_price = 0.0
        result = self.test_market.get_counteroffer_vertices(
            price=test_price,
            supply_or_demand=SupplyOrDemand.DEMAND,
            continuity=None
        )
        tc.assertEqual(len(result), 3,
                       "There should have been three valid demand Vertices returned.")
        sum_demand = sum([x.quantity for x in result])
        tc.assertEqual(sum_demand, -300.0,
                       "The valid demand Vertex quantities should have summed to -400 [avg. kW]")
        self.tearDown()

    def case2(self):
        print("Case 2: Retrieve all valid supply Vertices at a high price, regardless of continuity.")
        self.setUp()
        test_price = 1.0
        result = self.test_market.get_counteroffer_vertices(
            price=test_price,
            supply_or_demand=SupplyOrDemand.SUPPLY,
            continuity=None
        )
        tc.assertEqual(len(result), 2,
                       "There should have been two valid supply Vertices returned.")
        sum_demand = sum([x.quantity for x in result])
        tc.assertEqual(sum_demand, 200.0,
                       "The valid demand Vertex quantities should have summed to 200.0 [avg. kW]")
        self.tearDown()

    def case3(self):
        print("Case 3: Retrieve demand Vertices that have continuity.")
        self.setUp()
        test_price = 0.0
        result = self.test_market.get_counteroffer_vertices(
            price=test_price,
            supply_or_demand=SupplyOrDemand.DEMAND,
            continuity=True
        )
        tc.assertEqual(len(result), 2,
                       "There should have been two valid demand Vertices returned.")
        sum_demand = sum([x.quantity for x in result])
        tc.assertEqual(sum_demand, -200.0,
                       "The valid demand Vertex quantities should have summed to -400 [avg. kW]")
        self.tearDown()

    def case4(self):
        print("Case 4: Retrieve supply Vertices that have continuity.")
        self.setUp()
        test_price = 1.0
        result = self.test_market.get_counteroffer_vertices(
            price=test_price,
            supply_or_demand=SupplyOrDemand.SUPPLY,
            continuity=True
        )
        tc.assertEqual(len(result), 1,
                       "There should have been one valid supply Vertex returned.")
        sum_demand = sum([x.quantity for x in result])
        tc.assertEqual(sum_demand, 100.0,
                       "The valid demand Vertex quantities should have summed to 240 [avg. kW]")
        self.tearDown()

    def case5(self):
        print("Case 5: Retrieve demand Vertices that do not have continuity.")
        self.setUp()
        test_price = 0.0
        result = self.test_market.get_counteroffer_vertices(
            price=test_price,
            supply_or_demand=SupplyOrDemand.DEMAND,
            continuity=False
        )
        tc.assertEqual(len(result), 1,
                       "There should have been one valid demand Vertex returned.")
        sum_demand = sum([x.quantity for x in result])
        tc.assertEqual(sum_demand, -100.0,
                       "The valid demand Vertex quantities should have summed to -400 [avg. kW]")
        self.tearDown()

    def case6(self):
        print("Case 6: Retrieve supply Vertices that do not have continuity.")
        self.setUp()
        test_price = 1.0
        result = self.test_market.get_counteroffer_vertices(
            price=test_price,
            supply_or_demand=SupplyOrDemand.SUPPLY,
            continuity=False
        )
        tc.assertEqual(len(result), 1,
                       "There should have been one valid supply Vertex returned.")
        sum_demand = sum([x.quantity for x in result])
        tc.assertEqual(sum_demand, 100.0,
                       "The valid demand Vertex quantities should have summed to 240 [avg. kW]")
        self.tearDown()

    def case7(self):
        print("Case 7: Retrieve all valid demand Vertices at a high price, regardless of continuity.")
        print("- Case 7 demonstrates that the Vertex quantity changes with price.")
        self.setUp()
        test_price = 1.0
        result = self.test_market.get_counteroffer_vertices(
            price=test_price,
            supply_or_demand=SupplyOrDemand.DEMAND,
            continuity=None
        )
        tc.assertEqual(len(result), 1,
                       "There should have been just one valid demand Vertices returned at this price.")
        sum_demand = sum([x.quantity for x in result])
        tc.assertEqual(sum_demand, -100.0,
                       "The valid demand Vertex quantities should have summed to -400 [avg. kW]")
        self.tearDown()


class test_get_market_orders_by_actor:
    NEIGHBOR2 = Neighbor()
    NEIGHBOR2.description = "Extra Neighbor actor object"

    def __init__(self):
        self.test_market = None

    def setUp(self):
        self.test_market = MatchingEngine()

        self.test_market.matchList = []

        self.test_market.matchList.append(copy(DEMAND_ORDER[0]))
        # 231011DJH: Protected property __actor__ has replaced property actor.
        self.test_market.matchList[0].__actor__ = NEIGHBOR1

        self.test_market.matchList.append(copy(DEMAND_ORDER[1]))
        self.test_market.matchList[1].__actor__ = None

        self.test_market.matchList.append(copy(SUPPLY_ORDER[0]))
        self.test_market.matchList[2].__actor__ = NEIGHBOR1

        self.test_market.matchList.append(copy(SUPPLY_ORDER[1]))
        self.test_market.matchList[3].__actor__ = LOCAL_ASSET1

    def tearDown(self):
        self.test_market = None

    def case1(self):
        print("Case 1: No matches to actor.")
        self.setUp()
        result = self.test_market.get_market_orders_by_actor(an_actor=self.NEIGHBOR2)
        tc.assertEqual(result, [],
                       "There should have been no matched orders.")
        self.tearDown()

    def case2(self):
        print("Case 2: Match to one Order using a LocalAsset object actor.")
        self.setUp()
        result = self.test_market.get_market_orders_by_actor(an_actor=LOCAL_ASSET1)
        tc.assertEqual(result, [self.test_market.matchList[3]],
                       "There should have been precisely one matched order.")
        tc.assertEqual(result[0].actor, LOCAL_ASSET1,
                       "The match should have found an Order having this LocalAsset as its actor.")
        self.tearDown()

    def case3(self):
        print("Case 3: Match to two Orders using a Neighbor object actor.")
        self.setUp()
        result = self.test_market.get_market_orders_by_actor(
            an_actor=NEIGHBOR1
        )
        tc.assertEqual(result, [self.test_market.matchList[0], self.test_market.matchList[2]],
                       "There should have been precisely these two matched orders.")
        tc.assertTrue(all([x.actor == NEIGHBOR1 for x in result]),
                      "All matched orders should have had this Neighbor object as its actor.")
        self.tearDown()


class test_get_aggregate_market_position:
    def __init__(self):
        pass

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def case1(self):
        print("Case 1: Normal case.")
        self.setUp()
        self.tearDown()


class test_balance:
    def __init__(self):
        pass

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def case1(self):
        print("Case 1: Normal case.")
        self.setUp()
        self.tearDown()


class test_get_effective_match_price:
    def __init__(self):
        pass

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def case1(self):
        print("Case 1: Normal case.")
        self.setUp()
        self.tearDown()


if __name__ == '__main__':

    DIVIDER()  # **********
    print('Running test_matching_engine.py.')

    DIVIDER()  # **********
    test_fixture = test_get_counteroffers()
    print('Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    test_fixture.case2()
    test_fixture.case3()
    test_fixture.case4()
    test_fixture.case5()
    test_fixture.case6()
    print('Fixture ' + str(test_fixture) + ' ran to completion.')

    DIVIDER()  # **********
    test_fixture = test_while_in_negotiation()
    print('Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    test_fixture.case2()
    test_fixture.case3()
    test_fixture.case4()
    test_fixture.case5()
    test_fixture.case6()
    print('Fixture ' + str(test_fixture) + ' ran to completion.')

    DIVIDER()  # **********
    test_fixture = test_match()
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
    test_fixture.case11()
    test_fixture.case12()
    test_fixture.case13()
    test_fixture.case14()
    test_fixture.case15()
    test_fixture.case16()
    print('Fixture ' + str(test_fixture) + ' ran to completion.')

    DIVIDER()  # **********
    test_fixture = test_get_order_by_id()
    print('Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    test_fixture.case2()
    test_fixture.case3()
    test_fixture.case4()
    print('Fixture ' + str(test_fixture) + ' ran to completion.')

    DIVIDER()  # **********
    test_fixture = test_order_id_is_unique()
    print('Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    test_fixture.case2()
    test_fixture.case3()
    test_fixture.case4()
    print('Fixture ' + str(test_fixture) + ' ran to completion.')

    DIVIDER()  # **********
    test_fixture = test_spawn_markets()
    print('Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    print('Fixture ' + str(test_fixture) + ' ran to completion.')

    DIVIDER()  # **********
    test_fixture = test_get_counteroffer_vertices()
    print('Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    test_fixture.case2()
    test_fixture.case3()
    test_fixture.case4()
    test_fixture.case5()
    test_fixture.case6()
    test_fixture.case7()
    print('Fixture ' + str(test_fixture) + ' ran to completion.')

    DIVIDER()  # **********
    test_fixture = test_get_market_orders_by_actor()
    print('Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    test_fixture.case2()
    test_fixture.case3()
    print('Fixture ' + str(test_fixture) + ' ran to completion.')

    DIVIDER()  # **********
    test_fixture = test_get_aggregate_market_position()
    print('Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    print('Fixture ' + str(test_fixture) + ' ran to completion.')

    DIVIDER()  # **********
    test_fixture = test_balance()
    print('Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    print('Fixture ' + str(test_fixture) + ' ran to completion.')

    DIVIDER()  # **********
    test_fixture = test_get_effective_match_price()
    print('Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    print('Fixture ' + str(test_fixture) + ' ran to completion.')
