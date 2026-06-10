import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock

from tent.base import Entity
from tent.containers import Vertex


class ConcreteEntity(Entity):
    """Concrete implementation of Entity for testing"""
    
    @property
    def table_name(self):
        return 'test_entity'
    
    def get_record(self) -> dict:
        return super().get_record()
    
    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        return super().get_archive_definitions()


class TestEntity:
    """Unit tests for Entity business logic"""
    
    def setup_method(self):
        """Set up minimal test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.id = "test_tn_id"
        self.mock_tn.data_manager = Mock()
        self.mock_tn.get_isms_by_attribute = Mock(return_value=[])
        self.mock_tn.get_meters_by_attribute = Mock(return_value=[])
        
        self.entity_params = {
            'cost_parameters': [10.0, 2.0, 0.1],  # a0, a1, a2 for cost formula
            'default_vertices': [],
            'description': 'Test entity',
            'information_services': [],
            'location': 'Test location',
            'maximum_power': 100.0,
            'meter_points': [],
            'minimum_power': 0.0,
            'name': 'TestEntity',
            'transactive_node': self.mock_tn
        }
        
        self.entity = ConcreteEntity(**self.entity_params)

    def test_prod_cost_from_formula_calculation(self):
        """Test the quadratic cost formula calculation"""
        # Mock time interval (1 hour duration)
        mock_interval = Mock()
        mock_interval.duration.total_seconds.return_value = 3600
        
        # Mock scheduled power
        self.entity.scheduled_powers.get_by_interval = Mock(return_value=50.0)
        
        # Test formula: cost = (a0 + a1*p + 0.5*a2*p^2) * duration_hours
        # = (10.0 + 2.0*50.0 + 0.5*0.1*50.0^2) * 1.0
        # = (10.0 + 100.0 + 125.0) * 1.0 = 235.0
        result = self.entity.prod_cost_from_formula(mock_interval)
        
        assert result == 235.0

    def test_prod_cost_from_formula_different_duration(self):
        """Test cost formula with different time duration"""
        # Mock time interval (30 minutes = 0.5 hours)
        mock_interval = Mock()
        mock_interval.duration.total_seconds.return_value = 1800
        
        # Mock scheduled power
        self.entity.scheduled_powers.get_by_interval = Mock(return_value=20.0)
        
        # Test formula: cost = (10.0 + 2.0*20.0 + 0.5*0.1*20.0^2) * 0.5
        # = (10.0 + 40.0 + 20.0) * 0.5 = 70.0 * 0.5 = 35.0
        result = self.entity.prod_cost_from_formula(mock_interval)
        
        assert result == 35.0

    def test_calculate_reserve_margin_with_sd_curve(self):
        """Test reserve margin calculation when SD curve is available"""
        # Create mock market with time interval
        mock_market = Mock()
        mock_interval = Mock()
        mock_market.time_intervals = [mock_interval]
        
        # Mock scheduled power (50 kW)
        mock_scheduled_power = Mock()
        mock_scheduled_power.value = 50.0
        self.entity.scheduled_powers.get_by_interval = Mock(return_value=mock_scheduled_power)
        
        # Mock SD curve that returns max operational capacity of 80 kW
        mock_sd_curve = Mock()
        mock_sd_curve.max_quantity.return_value = 80.0
        self.entity.active_vertices.get_by_interval = Mock(return_value=mock_sd_curve)
        
        # Mock the update method to capture the result
        self.entity.reserve_margins.update = Mock()
        
        # Execute the method
        self.entity.calculate_reserve_margin(mock_market)
        
        # Verify: reserve margin = max(0, operational_max - scheduled_power)
        # = max(0, 80.0 - 50.0) = 30.0
        mock_sd_curve.max_quantity.assert_called_once_with(self.entity.maximum_power)
        self.entity.reserve_margins.update.assert_called_once_with(mock_interval, 30.0)

    def test_calculate_reserve_margin_no_sd_curve(self):
        """Test reserve margin calculation when no SD curve is available"""
        # Create mock market with time interval
        mock_market = Mock()
        mock_interval = Mock()
        mock_market.time_intervals = [mock_interval]
        
        # Mock scheduled power (60 kW)
        mock_scheduled_power = Mock()
        mock_scheduled_power.value = 60.0
        self.entity.scheduled_powers.get_by_interval = Mock(return_value=mock_scheduled_power)
        
        # No SD curve available
        self.entity.active_vertices.get_by_interval = Mock(return_value=None)
        
        # Mock the update method to capture the result
        self.entity.reserve_margins.update = Mock()
        
        # Execute the method
        self.entity.calculate_reserve_margin(mock_market)
        
        # Verify: reserve margin = max(0, maximum_power - scheduled_power)
        # = max(0, 100.0 - 60.0) = 40.0
        self.entity.reserve_margins.update.assert_called_once_with(mock_interval, 40.0)

    def test_calculate_reserve_margin_negative_case(self):
        """Test reserve margin calculation when scheduled power exceeds capacity"""
        # Create mock market with time interval
        mock_market = Mock()
        mock_interval = Mock()
        mock_market.time_intervals = [mock_interval]
        
        # Mock scheduled power higher than max capacity
        mock_scheduled_power = Mock()
        mock_scheduled_power.value = 120.0  # More than maximum_power (100.0)
        self.entity.scheduled_powers.get_by_interval = Mock(return_value=mock_scheduled_power)
        
        # No SD curve (use maximum_power)
        self.entity.active_vertices.get_by_interval = Mock(return_value=None)
        
        # Mock the update method
        self.entity.reserve_margins.update = Mock()
        
        # Execute the method
        self.entity.calculate_reserve_margin(mock_market)
        
        # Verify: reserve margin = max(0, 100.0 - 120.0) = max(0, -20.0) = 0.0
        self.entity.reserve_margins.update.assert_called_once_with(mock_interval, 0.0)

    def test_update_dual_costs_calculation(self):
        """Test dual cost calculation logic"""
        # Create mock market with time interval
        mock_market = Mock()
        mock_interval = Mock()
        mock_interval.duration.total_seconds.return_value = 3600  # 1 hour
        mock_market.time_intervals = [mock_interval]
        
        # Mock market marginal price ($/kWh)
        mock_market.marginal_prices.get_by_interval.return_value = 0.12
        
        # Mock scheduled power and production cost
        self.entity.scheduled_powers.get_by_interval = Mock(return_value=75.0)  # kW
        self.entity.production_costs.get_by_interval = Mock(return_value=150.0)  # $
        
        # Mock total calculation
        self.entity.dual_costs.total = Mock(return_value=141.0)
        self.entity.dual_costs.update = Mock()
        
        # Execute the method
        self.entity.update_dual_costs(mock_market)
        
        # Verify dual cost calculation:
        # dual_cost = production_cost - (marginal_price * scheduled_power * duration_hours)
        # = 150.0 - (0.12 * 75.0 * 1.0) = 150.0 - 9.0 = 141.0
        self.entity.dual_costs.update.assert_called_once_with(mock_interval, 141.0)
        assert self.entity.total_dual_cost == 141.0

    def test_get_production_costs_with_sd_curve(self):
        """Test production cost retrieval when SD curve is available"""
        mock_interval = Mock()
        
        # Mock scheduled power
        mock_scheduled_power = Mock()
        mock_scheduled_power.value = 45.0
        self.entity.scheduled_powers.get_by_interval = Mock(return_value=mock_scheduled_power)
        
        # Mock SD curve with production cost calculation
        mock_sd_curve = Mock()
        mock_sd_curve.prod_cost_from_vertices.return_value = 125.0
        self.entity.active_vertices.get_by_interval = Mock(return_value=mock_sd_curve)
        
        # Execute the private method
        result = self.entity._get_production_costs(mock_interval)
        
        # Verify SD curve method was called with correct power
        mock_sd_curve.prod_cost_from_vertices.assert_called_once_with(45.0)
        assert result == 125.0

    def test_get_production_costs_fallback_to_formula(self):
        """Test production cost falls back to formula when no SD curve"""
        mock_interval = Mock()
        mock_interval.duration.total_seconds.return_value = 3600  # 1 hour
        
        # Mock scheduled power - return the actual value, not a Mock object
        self.entity.scheduled_powers.get_by_interval = Mock(return_value=30.0)
        
        # No SD curve available
        self.entity.active_vertices.get_by_interval = Mock(return_value=None)
        
        # Execute the private method
        result = self.entity._get_production_costs(mock_interval)
        
        # Should fall back to formula calculation:
        # cost = (10.0 + 2.0*30.0 + 0.5*0.1*30.0^2) * 1.0
        # = (10.0 + 60.0 + 45.0) * 1.0 = 115.0
        assert result == 115.0

    def test_production_method_success(self):
        """Test production method when SD curve is available"""
        mock_interval = Mock()
        mock_interval.name = "TestInterval"
        
        # Mock SD curve
        mock_sd_curve = Mock()
        mock_sd_curve.power_from_vertices.return_value = 85.0
        self.entity.active_vertices.get_by_interval = Mock(return_value=mock_sd_curve)
        
        # Execute the method
        result = self.entity.production(price=0.15, time_interval=mock_interval)
        
        # Verify correct price was passed to SD curve
        mock_sd_curve.power_from_vertices.assert_called_once_with(0.15)
        assert result == 85.0

    def test_production_method_no_vertices_raises_exception(self):
        """Test production method raises exception when no active vertices"""
        mock_interval = Mock()
        mock_interval.name = "TestInterval"
        
        # No SD curve available
        self.entity.active_vertices.get_by_interval = Mock(return_value=None)
        
        # Should raise exception
        with pytest.raises(Exception) as exc_info:
            self.entity.production(price=0.15, time_interval=mock_interval)
        
        assert "No active vertices were found" in str(exc_info.value)
        assert "TestEntity" in str(exc_info.value)
        assert "TestInterval" in str(exc_info.value)

    def test_single_vertex_price_adjustment(self):
        """Test that single default vertex gets price set to infinity"""
        params = self.entity_params.copy()
        params['default_vertices'] = [Vertex(price=50.0, quantity=100.0, cost=10.0)]
        
        entity = ConcreteEntity(**params)
        
        # Single vertex should have price adjusted to infinity
        # default_vertices is an SDCurve, access vertices through .vertices attribute
        vertices_list = list(entity.default_vertices.vertices)
        assert len(vertices_list) == 1
        assert vertices_list[0].price == float('inf')
        assert vertices_list[0].quantity == 100.0
        assert vertices_list[0].cost == 10.0

    def test_multiple_vertices_no_price_adjustment(self):
        """Test that multiple vertices keep their original prices"""
        vertices = [
            Vertex(price=50.0, quantity=100.0, cost=10.0),
            Vertex(price=60.0, quantity=200.0, cost=20.0)
        ]
        params = self.entity_params.copy()
        params['default_vertices'] = vertices
        
        entity = ConcreteEntity(**params)
        
        # Multiple vertices should keep original prices
        # default_vertices is an SDCurve, access vertices through .vertices attribute
        vertices_list = list(entity.default_vertices.vertices)
        assert len(vertices_list) == 2
        assert vertices_list[0].price == 50.0
        assert vertices_list[1].price == 60.0

    def test_cost_parameters_type_conversion(self):
        """Test that cost parameters are converted to floats"""
        params = self.entity_params.copy()
        params['cost_parameters'] = ["15.5", "3.2", "0.05"]  # String inputs
        
        entity = ConcreteEntity(**params)
        
        assert entity.cost_parameters == [15.5, 3.2, 0.05]
        assert all(isinstance(cp, float) for cp in entity.cost_parameters)


if __name__ == "__main__":
    pytest.main([__file__])
