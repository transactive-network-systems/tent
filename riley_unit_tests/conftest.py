import pytest
from unittest.mock import MagicMock
from tent.base.entity import Entity
from tent.containers import Vertex, TimeInterval

class TestEntity(Entity):
    def get_record(self):
        return {}

    @classmethod
    def get_archive_definitions(cls):
        return []

@pytest.fixture
def entity():
    tn = MagicMock()
    cost_parameters = [1.0, 2.0, 3.0]
    default_vertices = [Vertex(price=10.0, cost=5.0, quantity=100.0)]
    return TestEntity(
        cost_parameters=cost_parameters,
        default_vertices=default_vertices,
        description="desc",
        information_services=[],
        location="loc",
        maximum_power=100.0,
        meter_points=[],
        minimum_power=10.0,
        name="test_entity",
        transactive_node=tn
    )

@pytest.fixture
def make_time_interval():
    def _make():
        ti = MagicMock(spec=TimeInterval)
        duration_mock = MagicMock()
        duration_mock.total_seconds.return_value = 3600
        ti.duration = duration_mock
        ti.name = "interval"
        return ti
    return _make