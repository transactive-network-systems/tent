import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

from tent.neighbor import Neighbor
from tent.neighbor.bulk_supplier_dc import BulkSupplierDC
from tent.enumerations import Direction, MeasurementType, MeasurementUnit
from tent.containers import Measurement


class MockTransactiveNode:
    """Lightweight mock for TransactiveNode"""
    def __init__(self):
        self.id = 'test_node'
        self.subscriptions = {}
        self.data_manager = Mock()  # Add data_manager attribute
        
    def register_subscription(self, subscribe_key, callback):
        self.subscriptions[subscribe_key] = callback


def get_default_neighbor_params():
    """Helper function to get default parameters for Neighbor construction"""
    return {
        'cost_parameters': [0.0, 0.0, 0.0],
        'default_vertices': None,
        'information_services': [],
        'meter_points': [],
        'maximum_power': 0.0,
        'minimum_power': 0.0,
        'location': '',
        'description': ''
    }


def test_neighbor_initialization_basic():
    """Test basic Neighbor initialization with minimal parameters"""
    tn = MockTransactiveNode()
    params = get_default_neighbor_params()
    neighbor = Neighbor(transactive_node=tn, name='TestNeighbor', **params)
    
    assert neighbor.name == 'TestNeighbor'
    assert neighbor.transactive is False  # default
    assert neighbor.convergence_threshold == 0.05  # default
    assert neighbor.up_or_down == Direction.unknown  # default
    assert neighbor._coordination == {} or len(neighbor._coordination) == 0
    assert isinstance(neighbor.convergence_flags, Measurement)


def test_neighbor_initialization_full_params():
    """Test Neighbor initialization with all parameters"""
    tn = MockTransactiveNode()
    neighbor = Neighbor(
        transactive_node=tn,
        name='FullNeighbor',
        description='Test neighbor with full params',
        transactive=True,
        convergence_threshold=0.02,
        cost_parameters=[1.0, 2.0, 3.0],
        demand_rate=5.5,
        demand_threshold=1500.0,
        maximum_power=1000.0,
        minimum_power=-500.0,
        friend=True,
        loss_factor=0.05,
        up_or_down=Direction.upstream,
        # Required Entity parameters
        default_vertices=None,
        information_services=[],
        meter_points=[],
        location='test_location'
    )
    
    assert neighbor.name == 'FullNeighbor'
    assert neighbor.description == 'Test neighbor with full params'
    assert neighbor.transactive is True
    assert neighbor.convergence_threshold == 0.02
    assert neighbor.cost_parameters == [1.0, 2.0, 3.0]
    assert neighbor.demand_rate == 5.5
    assert neighbor.demand_threshold == 1500.0
    assert neighbor.maximum_power == 1000.0
    assert neighbor.minimum_power == -500.0
    assert neighbor.friend is True
    assert neighbor.loss_factor == 0.05
    assert neighbor.up_or_down == Direction.upstream


def test_neighbor_direction_conversion():
    """Test Direction enum conversion from different input types"""
    tn = MockTransactiveNode()
    params = get_default_neighbor_params()
    
    # Test with Direction enum
    neighbor1 = Neighbor(transactive_node=tn, up_or_down=Direction.downstream, **params)
    assert neighbor1.up_or_down == Direction.downstream
    
    # Test with int
    neighbor2 = Neighbor(transactive_node=tn, up_or_down=1, **params)  # assuming 1 = upstream
    assert isinstance(neighbor2.up_or_down, Direction)
    
    # Test with string
    neighbor3 = Neighbor(transactive_node=tn, up_or_down='upstream', **params)
    assert neighbor3.up_or_down == Direction.upstream


def test_neighbor_cost_parameters_assignment():
    """Test cost parameters are properly assigned and converted to floats"""
    tn = MockTransactiveNode()
    params = get_default_neighbor_params()
    
    # Test with specific cost parameters
    test_cost_params = [10.5, 0.02, 0.001]
    params['cost_parameters'] = test_cost_params
    
    neighbor = Neighbor(transactive_node=tn, **params)
    
    # Verify cost parameters are correctly assigned
    assert neighbor.cost_parameters == [10.5, 0.02, 0.001]
    
    # Test that string numbers are converted to floats
    params['cost_parameters'] = ['15.5', '0.03', '0.002']
    neighbor2 = Neighbor(transactive_node=tn, **params)
    assert neighbor2.cost_parameters == [15.5, 0.03, 0.002]


def test_neighbor_subscription_registration():
    """Test that neighbor registers for transactive signal subscription"""
    tn = MockTransactiveNode()
    params = get_default_neighbor_params()
    neighbor = Neighbor(transactive_node=tn, name='SubsNeighbor', **params)
    
    expected_key = 'SubsNeighbor_incoming_transactive_signal'
    assert expected_key in tn.subscriptions
    assert tn.subscriptions[expected_key] == neighbor.receive_transactive_signal


def test_neighbor_schedule_power_calculation():
    """Test schedule_power method with mocked market and intervals"""
    tn = MockTransactiveNode()
    params = get_default_neighbor_params()
    neighbor = Neighbor(transactive_node=tn, name='ScheduleNeighbor', **params)
    
    # Mock market with time intervals and marginal prices
    market = Mock()
    time_interval = Mock()
    time_interval.start_time = datetime.now()
    market.time_intervals = [time_interval]
    
    # Mock marginal prices
    mp_mock = Mock()
    mp_mock.value = 0.05  # $/kWh
    market.marginal_prices.get_by_interval.return_value = mp_mock
    
    # Mock price model fallback
    market.price_model.get.return_value = [0.04]
    
    # Mock scheduled_powers and production method
    neighbor.scheduled_powers = Mock()
    neighbor.production = Mock(return_value=100.0)  # 100 kW
    
    neighbor.schedule_power(market)
    
    # Verify production was called with marginal price
    neighbor.production.assert_called_once_with(0.05, time_interval)
    # Verify scheduled_powers was updated
    neighbor.scheduled_powers.update.assert_called_once_with(time_interval, 100.0)


def test_neighbor_schedule_power_fallback_price():
    """Test schedule_power uses price model when marginal price unavailable"""
    tn = MockTransactiveNode()
    params = get_default_neighbor_params()
    neighbor = Neighbor(transactive_node=tn, name='FallbackNeighbor', **params)
    
    # Mock market setup
    market = Mock()
    time_interval = Mock()
    time_interval.start_time = datetime.now()
    market.time_intervals = [time_interval]
    
    # No marginal price available
    market.marginal_prices.get_by_interval.return_value = None
    # Price model provides fallback
    market.price_model.get.return_value = [0.06]
    
    # Mock methods
    neighbor.scheduled_powers = Mock()
    neighbor.production = Mock(return_value=75.0)
    
    neighbor.schedule_power(market)
    
    # Should use price model value (0.06)
    neighbor.production.assert_called_once_with(0.06, time_interval)


class TestBulkSupplierDC:
    """Test suite for BulkSupplierDC neighbor subclass"""
    
    def test_bulk_supplier_initialization(self):
        """Test BulkSupplierDC initialization"""
        tn = MockTransactiveNode()
        params = get_default_neighbor_params()
        supplier = BulkSupplierDC(transactive_node=tn, name='BulkSupplier', **params)
        
        assert supplier.name == 'BulkSupplier'
        assert supplier.transactive is False  # Always False for BulkSupplierDC
        assert isinstance(supplier, Neighbor)  # Inherits from Neighbor
    
    def test_bulk_supplier_update_vertices_power_validation(self):
        """Test update_vertices validates power constraints"""
        tn = MockTransactiveNode()
        params = get_default_neighbor_params()
        params['maximum_power'] = -100.0  # Invalid for supplier
        
        # Test with negative maximum power (should warn and return)
        supplier = BulkSupplierDC(
            transactive_node=tn, 
            name='BadSupplier',
            **params
        )
        
        market = Mock()
        
        # Mock logging to capture warning
        import tent.neighbor.bulk_supplier_dc
        original_log = tent.neighbor.bulk_supplier_dc._log
        tent.neighbor.bulk_supplier_dc._log = Mock()
        
        try:
            supplier.update_vertices(market)
            # Should have logged a warning
            tent.neighbor.bulk_supplier_dc._log.warning.assert_called()
        finally:
            tent.neighbor.bulk_supplier_dc._log = original_log
    
    def test_bulk_supplier_with_valid_power_range(self):
        """Test BulkSupplierDC with valid power range"""
        tn = MockTransactiveNode()
        params = get_default_neighbor_params()
        params.update({
            'maximum_power': 1000.0,
            'minimum_power': 0.0,
            'cost_parameters': [100.0, 0.05, 0.001]  # a0, a1, a2
        })
        
        supplier = BulkSupplierDC(
            transactive_node=tn,
            name='ValidSupplier',
            **params
        )
        
        assert supplier.maximum_power == 1000.0
        assert supplier.minimum_power == 0.0
        assert supplier.cost_parameters == [100.0, 0.05, 0.001]


def test_neighbor_demand_threshold_handling():
    """Test demand threshold tracking functionality"""
    tn = MockTransactiveNode()
    params = get_default_neighbor_params()
    params.update({
        'demand_threshold': 1000.0,
        'demand_threshold_coef': 0.95,
        'demand_month': datetime.today().month
    })
    
    neighbor = Neighbor(transactive_node=tn, **params)
    
    assert neighbor.demand_threshold == 1000.0
    assert neighbor.demand_threshold_coef == 0.95
    assert neighbor.demand_month == datetime.today().month


def test_neighbor_measurement_attributes():
    """Test that dynamic measurement attributes are properly initialized"""
    tn = MockTransactiveNode()
    params = get_default_neighbor_params()
    neighbor = Neighbor(transactive_node=tn, name='MeasurementNeighbor', **params)
    
    # Check convergence flags measurement
    assert isinstance(neighbor.convergence_flags, Measurement)
    assert neighbor.convergence_flags.type == MeasurementType.ConvergenceFlag
    assert neighbor.convergence_flags.unit == MeasurementUnit.Boolean
    
    # Check signal dictionaries are initialized
    assert hasattr(neighbor, 'prepared_signals')
    assert hasattr(neighbor, 'last_received_signals') 
    assert hasattr(neighbor, 'last_sent_signals')


def test_neighbor_boolean_validation():
    """Test boolean parameter validation"""
    tn = MockTransactiveNode()
    params = get_default_neighbor_params()
    
    # Test valid boolean values
    neighbor1 = Neighbor(transactive_node=tn, friend=True, transactive=False, **params)
    assert neighbor1.friend is True
    assert neighbor1.transactive is False
    
    # Test integer boolean conversion (0/1)
    neighbor2 = Neighbor(transactive_node=tn, friend=1, transactive=0, **params)
    assert neighbor2.friend is True
    assert neighbor2.transactive is False


def test_neighbor_numeric_parameter_conversion():
    """Test numeric parameters are properly converted to float/int"""
    tn = MockTransactiveNode()
    params = get_default_neighbor_params()
    params.update({
        'convergence_threshold': '0.03',  # string
        'demand_rate': '4.8',            # string
        'maximum_power': '2000',         # string  
        'demand_month': '6'              # string for int
    })
    
    neighbor = Neighbor(transactive_node=tn, **params)
    
    assert neighbor.convergence_threshold == 0.03
    assert neighbor.demand_rate == 4.8
    assert neighbor.maximum_power == 2000.0
    assert neighbor.demand_month == 6


if __name__ == '__main__':
    pytest.main([__file__])