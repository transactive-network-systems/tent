
import pytest
from unittest.mock import MagicMock

from tent.base.entity import Entity
from tent.containers import Vertex, TimeInterval

## Fixtures are now provided by conftest.py

def test_initialization(entity):
	assert entity.cost_parameters == [1.0, 2.0, 3.0]
	assert entity.location == "loc"
	assert entity.maximum_power == 100.0
	assert entity.minimum_power == 10.0
	assert entity.name == "test_entity"
	assert len(entity.default_vertices) == 1

def test_prod_cost_from_formula(entity, make_time_interval):
	ti = make_time_interval()
	entity.scheduled_powers.get_by_interval = MagicMock(return_value=5.0)
	cost = entity.prod_cost_from_formula(ti)
	# (1.0 + 2.0*5.0 + 0.5*3.0*5.0**2) * 1
	expected = (1.0 + 2.0*5.0 + 0.5*3.0*25.0) * 1
	assert cost == pytest.approx(expected)

def test_get_production_costs_with_curve(entity, make_time_interval):
	ti = make_time_interval()
	sd_curve = MagicMock()
	sd_curve.prod_cost_from_vertices.return_value = 42.0
	entity.active_vertices.get_by_interval = MagicMock(return_value=sd_curve)
	entity.scheduled_powers.get_by_interval = MagicMock(return_value=MagicMock(value=5.0))
	cost = entity._get_production_costs(ti)
	assert cost == 42.0

def test_get_production_costs_without_curve(entity, make_time_interval):
	ti = make_time_interval()
	entity.active_vertices.get_by_interval = MagicMock(return_value=None)
	entity.scheduled_powers.get_by_interval = MagicMock(return_value=5.0)
	cost = entity._get_production_costs(ti)
	expected = (1.0 + 2.0*5.0 + 0.5*3.0*25.0) * 1
	assert cost == pytest.approx(expected)

def test_production_with_curve(entity, make_time_interval):
	ti = make_time_interval()
	sd_curve = MagicMock()
	sd_curve.power_from_vertices.return_value = 77.0
	entity.active_vertices.get_by_interval = MagicMock(return_value=sd_curve)
	result = entity.production(10.0, ti)
	assert result == 77.0

def test_production_without_curve(entity, make_time_interval):
	ti = make_time_interval()
	entity.active_vertices.get_by_interval = MagicMock(return_value=None)
	with pytest.raises(Exception):
		entity.production(10.0, ti)