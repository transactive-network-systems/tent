import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock

from tent.containers.time_interval import TimeInterval
from tent.containers.order import Order
from tent.containers.vertex import Vertex
from tent.neighbor import Neighbor
from tent.enumerations.order import SupplyOrDemand

class DummyTransactiveNode:
    def __init__(self):
        self.neighbors = []
    def register_subscription(self, subscribe_key, callback):
        pass

@pytest.fixture
def transactive_node():
    return DummyTransactiveNode()

@pytest.fixture
def market_series():
    # Simple mock market series
    mock_series = Mock()
    mock_series.name = "SeriesTest"
    mock_series.description = "Test market series"
    return mock_series

@pytest.fixture
def market(market_series):
    # Simple mock market that has everything Order needs
    mock_market = Mock()
    mock_market.series = market_series
    mock_market.orderList = []  # Order needs this
    mock_market.id = "market-123"
    mock_market.name = "TestMarket"
    return mock_market

@pytest.fixture
def neighbor(transactive_node):
    return Neighbor(transactive_node=transactive_node, name="TestNeighbor")

@pytest.fixture
def time_interval(market):
    return TimeInterval(
        market=market, 
        duration=timedelta(hours=1), 
        start_time=datetime.now()
    )

@pytest.fixture
def vertex():
    return Vertex(price=10.0, cost=0.0, quantity=5.0, continuity=True)

@pytest.fixture
def supply_vertices():
    return [Vertex(price=10.0, quantity=5.0, continuity=True)]

@pytest.fixture
def demand_vertices():
    return [Vertex(price=10.0, quantity=-5.0, continuity=True)]

@pytest.fixture
def order(market, time_interval, neighbor, supply_vertices):
    return Order(
        market=market,
        interval=time_interval,
        actor=neighbor,
        vertices=supply_vertices,
        supply_or_demand=SupplyOrDemand.SUPPLY
    )

@pytest.fixture
def demand_order(market, time_interval, neighbor, demand_vertices):
    return Order(
        market=market,
        interval=time_interval,
        actor=neighbor,
        vertices=demand_vertices,
        supply_or_demand=SupplyOrDemand.DEMAND
    )