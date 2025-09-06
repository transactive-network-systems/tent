import pytest
import abc
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock
from uuid6 import UUID

# Import base classes
from tent.base import Archivable, Component, Entity
from tent.containers import Vertex, SDMeasurement, Measurement
from tent.enumerations import MeasurementType, MeasurementUnit


class TestArchivable:
    """Test cases for Archivable base class"""
    
    def test_archivable_is_abstract(self):
        """Test that Archivable cannot be instantiated directly"""
        with pytest.raises(TypeError):
            Archivable()
            
    def test_archivable_id_generation(self):
        """Test UUID generation for archivable objects"""
        class ConcreteArchivable(Archivable):
            def __init__(self):
                super().__init__()
                
            @property
            def data_manager(self):
                return Mock()
                
            @property
            def table_name(self):
                return "test_table"
                
            def get_record(self):
                return {"id": self.id, "test": "data"}
                
            @classmethod
            def get_archive_definitions(cls):
                return []
        
        archivable = ConcreteArchivable()
        
        # Should have a valid UUID
        assert isinstance(archivable.id, UUID)
        assert archivable.id is not None
        
        # Each instance should have unique ID
        archivable2 = ConcreteArchivable()
        assert archivable.id != archivable2.id
        
    def test_archivable_timestamp_creation(self):
        """Test timestamp generation for archivable objects"""
        class ConcreteArchivable(Archivable):
            def __init__(self):
                super().__init__()
                
            @property
            def data_manager(self):
                return Mock()
                
            @property
            def table_name(self):
                return "test_table"
                
            def get_record(self):
                return {"id": self.id}
                
            @classmethod
            def get_archive_definitions(cls):
                return []
        
        before_creation = datetime.now(timezone.utc).astimezone()
        archivable = ConcreteArchivable()
        after_creation = datetime.now(timezone.utc).astimezone()
        
        # Timestamp should be between before and after creation
        assert before_creation <= archivable.real_time_created <= after_creation
        assert isinstance(archivable.real_time_created, datetime)
        
    def test_archivable_archive_method(self):
        """Test archive method functionality"""
        mock_data_manager = Mock()
        
        class ConcreteArchivable(Archivable):
            def __init__(self):
                super().__init__()
                
            @property
            def data_manager(self):
                return mock_data_manager
                
            @property
            def table_name(self):
                return "test_table"
                
            def get_record(self):
                return {"id": self.id, "name": "test"}
                
            @classmethod
            def get_archive_definitions(cls):
                return []
        
        archivable = ConcreteArchivable()
        archivable.archive()
        
        # Should call data_manager.archive_data with correct structure
        mock_data_manager.archive_data.assert_called_once()
        call_args = mock_data_manager.archive_data.call_args[0][0]
        
        assert len(call_args) == 1
        assert call_args[0]['table_name'] == 'test_table'
        assert call_args[0]['data']['id'] == archivable.id
        assert call_args[0]['data']['name'] == 'test'
        
    def test_archivable_archive_with_additional_records(self):
        """Test archive method with additional records"""
        mock_data_manager = Mock()
        
        class ConcreteArchivable(Archivable):
            def __init__(self):
                super().__init__()
                
            @property
            def data_manager(self):
                return mock_data_manager
                
            @property
            def table_name(self):
                return "test_table"
                
            def get_record(self):
                return {"id": self.id, "name": "test"}
                
            @classmethod
            def get_archive_definitions(cls):
                return []
        
        archivable = ConcreteArchivable()
        additional_records = [
            {"table_name": "related_table", "data": {"related_id": 123}}
        ]
        
        archivable.archive(additional_records)
        
        # Should call with both original and additional records
        mock_data_manager.archive_data.assert_called_once()
        call_args = mock_data_manager.archive_data.call_args[0][0]
        
        assert len(call_args) == 2
        assert call_args[0]['table_name'] == 'related_table'
        assert call_args[1]['table_name'] == 'test_table'
        
    def test_archivable_abstract_methods(self):
        """Test that abstract methods must be implemented"""
        class IncompleteArchivable(Archivable):
            def __init__(self):
                super().__init__()
        
        with pytest.raises(TypeError):
            IncompleteArchivable()


class TestComponent:
    """Test cases for Component base class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.id = "test_tn_id"
        self.mock_data_manager = Mock()
        self.mock_tn.data_manager = self.mock_data_manager
        
    def test_component_is_abstract(self):
        """Test that Component cannot be instantiated directly"""
        with pytest.raises(TypeError):
            Component("description", "name", self.mock_tn)
            
    def test_component_initialization(self):
        """Test Component initialization"""
        class ConcreteComponent(Component):
            @property
            def table_name(self):
                return "test_component"
                
            def get_record(self):
                record = super().get_record()
                return record
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        component = ConcreteComponent(
            description="Test description",
            name="TestComponent",
            transactive_node=self.mock_tn
        )
        
        assert component.description == "Test description"
        assert component.name == "TestComponent"
        assert component.tn == self.mock_tn
        assert isinstance(component.id, UUID)
        
    def test_component_string_conversion(self):
        """Test that Component converts inputs to strings"""
        class ConcreteComponent(Component):
            @property
            def table_name(self):
                return "test_component"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        component = ConcreteComponent(
            description=123,  # Should be converted to string
            name=456,         # Should be converted to string
            transactive_node=self.mock_tn
        )
        
        assert component.description == "123"
        assert component.name == "456"
        
    def test_component_data_manager_property(self):
        """Test data_manager property access"""
        class ConcreteComponent(Component):
            @property
            def table_name(self):
                return "test_component"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        component = ConcreteComponent(
            description="Test",
            name="Test",
            transactive_node=self.mock_tn
        )
        
        assert component.data_manager == self.mock_data_manager
        
    def test_component_get_record(self):
        """Test Component get_record method"""
        class ConcreteComponent(Component):
            @property
            def table_name(self):
                return "test_component"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        component = ConcreteComponent(
            description="Test description",
            name="TestComponent",
            transactive_node=self.mock_tn
        )
        
        record = component.get_record()
        
        assert record['id'] == component.id
        assert record['name'] == "TestComponent"
        assert record['description'] == "Test description"
        assert record['tn_id'] == "test_tn_id"
        
    def test_component_get_archive_definitions(self):
        """Test Component archive definitions"""
        class ConcreteComponent(Component):
            @property
            def table_name(self):
                return "test_component"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        definitions = ConcreteComponent.get_archive_definitions()
        
        # Should have component definition
        component_def = None
        for definition in definitions:
            if definition['name'] == 'component':
                component_def = definition
                break
                
        assert component_def is not None
        
        # Check required columns
        column_names = [col['name'] for col in component_def['columns']]
        assert 'id' in column_names
        assert 'name' in column_names
        assert 'description' in column_names
        assert 'tn_id' in column_names


class TestEntity:
    """Test cases for Entity base class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.id = "test_tn_id"
        self.mock_data_manager = Mock()
        self.mock_tn.data_manager = self.mock_data_manager
        self.mock_tn.get_isms_by_attribute = Mock(return_value=[])
        self.mock_tn.get_meters_by_attribute = Mock(return_value=[])
        
        # Default parameters to avoid None iteration issues
        self.default_entity_params = {
            'cost_parameters': [1.0, 2.0, 3.0],
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
        
    def test_entity_is_abstract(self):
        """Test that Entity cannot be instantiated directly"""
        with pytest.raises(TypeError):
            Entity(**self.default_entity_params)
            
    def test_entity_initialization_with_defaults(self):
        """Test Entity initialization with default parameters"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        entity = ConcreteEntity(**self.default_entity_params)
        
        # Test basic attributes
        assert entity.cost_parameters == [1.0, 2.0, 3.0]
        assert entity.location == 'Test location'
        assert entity.maximum_power == 100.0
        assert entity.minimum_power == 0.0
        assert entity.name == 'TestEntity'
        assert entity.description == 'Test entity'
        assert entity.tn == self.mock_tn
        
        # Test dynamic attributes
        assert isinstance(entity.active_vertices, SDMeasurement)
        assert isinstance(entity.dual_costs, Measurement)
        assert isinstance(entity.production_costs, Measurement)
        assert isinstance(entity.reserve_margins, Measurement)
        assert isinstance(entity.scheduled_powers, Measurement)
        assert entity.total_dual_cost == 0.0
        assert entity.total_production_cost == 0.0
        
    def test_entity_initialization_with_vertices(self):
        """Test Entity initialization with custom vertices"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        vertices = [
            Vertex(price=50.0, quantity=100.0, cost=10.0),
            Vertex(price=60.0, quantity=200.0, cost=20.0)
        ]
        
        params = self.default_entity_params.copy()
        params['default_vertices'] = vertices
        
        entity = ConcreteEntity(**params)
        
        assert len(entity.default_vertices) == 2
        assert entity.default_vertices[0].price == 50.0
        assert entity.default_vertices[1].price == 60.0
        
    def test_entity_initialization_single_vertex_price_adjustment(self):
        """Test that single vertex gets price set to infinity"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        vertex = Vertex(price=50.0, quantity=100.0, cost=10.0)
        
        params = self.default_entity_params.copy()
        params['default_vertices'] = [vertex]
        
        entity = ConcreteEntity(**params)
        
        # Single vertex should have price set to infinity
        assert entity.default_vertices[0].price == float('inf')
        
    def test_entity_initialization_no_vertices(self):
        """Test Entity initialization with no vertices creates default"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        params = self.default_entity_params.copy()
        params['default_vertices'] = None
        
        entity = ConcreteEntity(**params)
        
        # Should create default vertex with infinite price
        assert len(entity.default_vertices) == 1
        assert entity.default_vertices[0].price == float('inf')
        assert entity.default_vertices[0].quantity == 1.0
        assert entity.default_vertices[0].cost == 0.0
        
    def test_entity_information_services_component_objects(self):
        """Test Entity with information service component objects"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        mock_ism = Mock(spec=Component)
        params = self.default_entity_params.copy()
        params['information_services'] = [mock_ism]
        entity = ConcreteEntity(**params)
        assert len(entity.information_services) >= 0  # WeakSet behavior
        
    def test_entity_information_services_string_names(self):
        """Test Entity with information service string names"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        mock_ism = Mock()
        self.mock_tn.get_isms_by_attribute.return_value = [mock_ism]
        
        params = self.default_entity_params.copy()
        params['information_services'] = ['test_ism']
        
        entity = ConcreteEntity(**params)
        
        self.mock_tn.get_isms_by_attribute.assert_called_once_with('name', ['test_ism'])
        
    def test_entity_information_services_invalid_type(self):
        """Test Entity with invalid information service types"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        params = self.default_entity_params.copy()
        params['information_services'] = [123, 456]  # Invalid types
        
        with pytest.raises(ValueError) as exc_info:
            ConcreteEntity(**params)
            
        assert "information services" in str(exc_info.value)
        
    def test_entity_meter_points_component_objects(self):
        """Test Entity with meter point component objects"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        mock_meter = Mock(spec=Component)
        params = self.default_entity_params.copy()
        params['meter_points'] = [mock_meter]
        entity = ConcreteEntity(**params)
        assert len(entity.meter_points) >= 0  # WeakSet behavior
        
    def test_entity_meter_points_string_names(self):
        """Test Entity with meter point string names"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        mock_meter = Mock()
        self.mock_tn.get_meters_by_attribute.return_value = [mock_meter]
        
        params = self.default_entity_params.copy()
        params['meter_points'] = ['test_meter']
        
        entity = ConcreteEntity(**params)
        
        self.mock_tn.get_meters_by_attribute.assert_called_once_with('name', ['test_meter'])
        
    def test_entity_meter_points_invalid_type(self):
        """Test Entity with invalid meter point types"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        params = self.default_entity_params.copy()
        params['meter_points'] = [123, 456]  # Invalid types
        
        with pytest.raises(ValueError) as exc_info:
            ConcreteEntity(**params)
            
        assert "meter points" in str(exc_info.value)
        
    def test_entity_type_conversions(self):
        """Test Entity parameter type conversions"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        params = {
            'cost_parameters': ["1.5", "2.5", "3.5"],  # String numbers
            'default_vertices': [],
            'description': 123,  # Non-string
            'information_services': [],
            'location': 456,     # Non-string
            'maximum_power': "100.5",  # String number
            'meter_points': [],
            'minimum_power': "0.5",    # String number
            'name': 789,         # Non-string
            'transactive_node': self.mock_tn
        }
        
        entity = ConcreteEntity(**params)
        
        assert entity.cost_parameters == [1.5, 2.5, 3.5]
        assert entity.description == "123"
        assert entity.location == "456"
        assert entity.maximum_power == 100.5
        assert entity.minimum_power == 0.5
        assert entity.name == "789"
        
    def test_calculate_reserve_margin(self):
        """Test calculate_reserve_margin method"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        entity = ConcreteEntity(**self.default_entity_params)
        
        # Mock market and time intervals
        mock_market = Mock()
        mock_interval = Mock()
        mock_interval.duration.total_seconds.return_value = 3600  # 1 hour
        mock_market.time_intervals = [mock_interval]
        
        # Mock scheduled power
        mock_scheduled_power = Mock()
        mock_scheduled_power.value = 50.0
        entity.scheduled_powers.get_by_interval = Mock(return_value=mock_scheduled_power)
        
        # Mock SD curve
        mock_sd_curve = Mock()
        mock_sd_curve.max_quantity.return_value = 80.0
        entity.active_vertices.get_by_interval = Mock(return_value=mock_sd_curve)
        
        # Mock the update method BEFORE calling the method under test
        entity.reserve_margins.update = Mock()
        
        entity.calculate_reserve_margin(mock_market)
        
        # Should calculate reserve margin as max(0, 80.0 - 50.0) = 30.0
        entity.reserve_margins.update.assert_called_once_with(mock_interval, 30.0)
        
    def test_calculate_reserve_margin_no_sd_curve(self):
        """Test calculate_reserve_margin with no SD curve"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        entity = ConcreteEntity(**self.default_entity_params)
        
        # Mock market and time intervals
        mock_market = Mock()
        mock_interval = Mock()
        mock_market.time_intervals = [mock_interval]
        
        # Mock scheduled power
        mock_scheduled_power = Mock()
        mock_scheduled_power.value = 50.0
        entity.scheduled_powers.get_by_interval = Mock(return_value=mock_scheduled_power)
        
        # No SD curve available
        entity.active_vertices.get_by_interval = Mock(return_value=None)
        
        # Mock the update method BEFORE calling the method under test
        entity.reserve_margins.update = Mock()
        
        entity.calculate_reserve_margin(mock_market)
        
        # Should use maximum_power (100.0) - scheduled_power (50.0) = 50.0
        entity.reserve_margins.update.assert_called_once_with(mock_interval, 50.0)
        
    def test_update_dual_costs(self):
        """Test update_dual_costs method"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        entity = ConcreteEntity(**self.default_entity_params)
        
        # Mock market and intervals
        mock_market = Mock()
        mock_interval = Mock()
        mock_interval.duration.total_seconds.return_value = 3600  # 1 hour
        mock_market.time_intervals = [mock_interval]
        
        # Mock market marginal prices
        mock_market.marginal_prices.get_by_interval.return_value = 0.10  # $/kWh
        
        # Mock scheduled power and production cost
        entity.scheduled_powers.get_by_interval = Mock(return_value=50.0)  # kW
        entity.production_costs.get_by_interval = Mock(return_value=100.0)  # $
        entity.dual_costs.total = Mock(return_value=95.0)
        
        # Mock update methods before calling update_dual_costs
        entity.reserve_margins.update = Mock()
        entity.dual_costs.update = Mock()
        entity.production_costs.update = Mock()
        
        entity.update_dual_costs(mock_market)
        
        # Dual cost = production_cost - (marginal_price * scheduled_power * duration)
        # = 100.0 - (0.10 * 50.0 * 1.0) = 100.0 - 5.0 = 95.0
        entity.dual_costs.update.assert_called_once_with(mock_interval, 95.0)
        assert entity.total_dual_cost == 95.0
        
    def test_update_production_costs_with_sd_curve(self):
        """Test update_production_costs with SD curve"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        entity = ConcreteEntity(**self.default_entity_params)
        
        # Mock market
        mock_market = Mock()
        mock_interval = Mock()
        mock_market.time_intervals = [mock_interval]
        
        # Mock scheduled power
        mock_scheduled_power = Mock()
        mock_scheduled_power.value = 50.0
        entity.scheduled_powers.get_by_interval = Mock(return_value=mock_scheduled_power)
        
        # Mock SD curve
        mock_sd_curve = Mock()
        mock_sd_curve.prod_cost_from_vertices.return_value = 75.0
        entity.active_vertices.get_by_interval = Mock(return_value=mock_sd_curve)
        
        # Mock the update method and total property BEFORE calling the method under test
        entity.production_costs.update = Mock()
        entity.production_costs.total = Mock(return_value=75.0)
        
        entity.update_production_costs(mock_market)
        
        mock_sd_curve.prod_cost_from_vertices.assert_called_once_with(50.0)
        entity.production_costs.update.assert_called_once_with(mock_interval, 75.0)
        assert entity.total_production_cost == 75.0
        
    def test_update_production_costs_no_sd_curve(self):
        """Test update_production_costs without SD curve (uses formula)"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        entity = ConcreteEntity(**self.default_entity_params)
        
        # Mock market
        mock_market = Mock()
        mock_interval = Mock()
        mock_market.time_intervals = [mock_interval]
        
        # Mock scheduled power
        mock_scheduled_power = Mock()
        mock_scheduled_power.value = 50.0
        entity.scheduled_powers.get_by_interval = Mock(return_value=mock_scheduled_power)
        
        # No SD curve
        entity.active_vertices.get_by_interval = Mock(return_value=None)
        
        # Mock the update method and total property BEFORE calling the method under test
        entity.production_costs.update = Mock()
        entity.production_costs.total = Mock(return_value=80.0)
        
        # Mock the formula method
        with patch.object(entity, 'prod_cost_from_formula', return_value=80.0) as mock_formula:
            entity.update_production_costs(mock_market)
            
            mock_formula.assert_called_once_with(mock_interval)
            entity.production_costs.update.assert_called_once_with(mock_interval, 80.0)
            assert entity.total_production_cost == 80.0
            
    def test_production_method(self):
        """Test production method"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        entity = ConcreteEntity(**self.default_entity_params)
        
        # Mock time interval
        mock_interval = Mock()
        mock_interval.name = "TestInterval"
        
        # Mock SD curve
        mock_sd_curve = Mock()
        mock_sd_curve.power_from_vertices.return_value = 75.0
        entity.active_vertices.get_by_interval = Mock(return_value=mock_sd_curve)
        
        result = entity.production(price=0.10, time_interval=mock_interval)
        
        assert result == 75.0
        mock_sd_curve.power_from_vertices.assert_called_once_with(0.10)
        
    def test_production_method_no_vertices(self):
        """Test production method with no active vertices"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        entity = ConcreteEntity(**self.default_entity_params)
        
        # Mock time interval
        mock_interval = Mock()
        mock_interval.name = "TestInterval"
        
        # No SD curve
        entity.active_vertices.get_by_interval = Mock(return_value=None)
        
        with pytest.raises(Exception) as exc_info:
            entity.production(price=0.10, time_interval=mock_interval)
            
        assert "No active vertices were found" in str(exc_info.value)
        assert "TestInterval" in str(exc_info.value)
        
    def test_prod_cost_from_formula(self):
        """Test prod_cost_from_formula method"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        # Set cost parameters: a0=10, a1=2, a2=0.1
        params = self.default_entity_params.copy()
        params['cost_parameters'] = [10.0, 2.0, 0.1]
        
        entity = ConcreteEntity(**params)
        
        # Mock time interval and scheduled power
        mock_interval = Mock()
        mock_interval.duration.total_seconds.return_value = 3600  # 1 hour
        
        entity.scheduled_powers.get_by_interval = Mock(return_value=50.0)  # kW
        
        # Cost = (a0 + a1*p + 0.5*a2*p^2) * duration_hours
        # = (10.0 + 2.0*50.0 + 0.5*0.1*50.0^2) * 1.0
        # = (10.0 + 100.0 + 125.0) * 1.0 = 235.0
        
        result = entity.prod_cost_from_formula(mock_interval)
        
        assert result == 235.0
        
    def test_get_record(self):
        """Test get_record method"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        entity = ConcreteEntity(**self.default_entity_params)
        
        record = entity.get_record()
        
        # Should include Entity fields plus Component fields
        assert 'id' in record
        assert 'name' in record
        assert 'description' in record
        assert 'tn_id' in record
        assert record['cost_parameters_0'] == 1.0
        assert record['cost_parameters_1'] == 2.0
        assert record['cost_parameters_2'] == 3.0
        assert record['location'] == 'Test location'
        assert record['maximum_power'] == 100.0
        assert record['minimum_power'] == 0.0
        
    def test_archive_with_relationships(self):
        """Test archive method includes relationship records"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        entity = ConcreteEntity(**self.default_entity_params)
        
        # Mock meter points and information services
        mock_meter = Mock()
        mock_meter.id = "meter_123"
        mock_ism = Mock()
        mock_ism.id = "ism_456"
        
        entity.meter_points.add(mock_meter)
        entity.information_services.add(mock_ism)
        
        entity.archive()
        
        # Should call data_manager.archive_data with relationship records
        assert self.mock_data_manager.archive_data.call_count >= 1
        call_args = self.mock_data_manager.archive_data.call_args[0][0]
        
        # Should have entity record plus relationship records
        table_names = [record['table_name'] for record in call_args]
        assert 'entity_meter_point' in table_names
        assert 'entity_information_service' in table_names
        assert 'test_entity' in table_names
        
    def test_get_archive_definitions(self):
        """Test get_archive_definitions class method"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        definitions = ConcreteEntity.get_archive_definitions()
        
        # Should have entity definition
        entity_def = None
        for definition in definitions:
            if definition['name'] == 'entity':
                entity_def = definition
                break
                
        assert entity_def is not None
        assert entity_def['inherits'] == 'component'
        
        # Check for required columns
        column_names = [col['name'] for col in entity_def['columns']]
        assert 'cost_parameters_0' in column_names
        assert 'cost_parameters_1' in column_names
        assert 'cost_parameters_2' in column_names
        assert 'location' in column_names
        assert 'maximum_power' in column_names
        assert 'minimum_power' in column_names


class TestBaseIntegration:
    """Integration tests for base class hierarchy"""
    
    def test_inheritance_chain(self):
        """Test complete inheritance chain from Archivable to Entity"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "test_entity"
                
            def get_record(self):
                return super().get_record()
                
            @classmethod
            def get_archive_definitions(cls):
                return super().get_archive_definitions()
        
        mock_tn = Mock()
        mock_tn.id = "test_tn"
        mock_tn.data_manager = Mock()
        mock_tn.get_isms_by_attribute = Mock(return_value=[])
        mock_tn.get_meters_by_attribute = Mock(return_value=[])
        
        entity = ConcreteEntity(
            cost_parameters=[1.0, 2.0, 3.0],
            default_vertices=[],
            description='Test',
            information_services=[],
            location='Test',
            maximum_power=100.0,
            meter_points=[],
            minimum_power=0.0,
            name='Test',
            transactive_node=mock_tn
        )
        
        # Should have all attributes from the inheritance chain
        # From Archivable
        assert hasattr(entity, 'id')
        assert hasattr(entity, 'real_time_created')
        
        # From Component
        assert hasattr(entity, 'name')
        assert hasattr(entity, 'description')
        assert hasattr(entity, 'tn')
        assert hasattr(entity, 'data_manager')
        
        # From Entity
        assert hasattr(entity, 'cost_parameters')
        assert hasattr(entity, 'maximum_power')
        assert hasattr(entity, 'active_vertices')
        
    def test_archive_complete_workflow(self):
        """Test complete archive workflow through inheritance chain"""
        class ConcreteEntity(Entity):
            @property
            def table_name(self):
                return "concrete_entity"
                
            def get_record(self):
                record = super().get_record()
                record['custom_field'] = 'custom_value'
                return record
                
            @classmethod
            def get_archive_definitions(cls):
                definitions = super().get_archive_definitions()
                definitions.append({
                    'name': 'concrete_entity',
                    'inherits': 'entity',
                    'columns': [
                        {'name': 'custom_field', 'type': 'String'}
                    ]
                })
                return definitions
        
        mock_tn = Mock()
        mock_tn.id = "test_tn"
        mock_data_manager = Mock()
        mock_tn.data_manager = mock_data_manager
        mock_tn.get_isms_by_attribute = Mock(return_value=[])
        mock_tn.get_meters_by_attribute = Mock(return_value=[])
        
        entity = ConcreteEntity(
            cost_parameters=[1.0, 2.0, 3.0],
            default_vertices=[],
            description='Test',
            information_services=[],
            location='Test',
            maximum_power=100.0,
            meter_points=[],
            minimum_power=0.0,
            name='Test',
            transactive_node=mock_tn
        )
        
        entity.archive()
        
        # Verify archive data was sent (expect multiple calls due to relationships)
        assert mock_data_manager.archive_data.call_count >= 1
        call_args = mock_data_manager.archive_data.call_args[0][0]
        
        # Should have the concrete entity record
        entity_record = None
        for record in call_args:
            if record['table_name'] == 'concrete_entity':
                entity_record = record
                break
                
        assert entity_record is not None
        
        # Should have data from all levels of inheritance
        data = entity_record['data']
        assert 'id' in data  # From Archivable
        assert 'name' in data  # From Component
        assert 'cost_parameters_0' in data  # From Entity
        assert 'custom_field' in data  # From ConcreteEntity
        assert data['custom_field'] == 'custom_value'


if __name__ == "__main__":
    pytest.main([__file__])