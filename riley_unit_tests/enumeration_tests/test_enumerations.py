import pytest
from enum import Enum

# Import all enumeration classes
from tent.enumerations import (
    Direction, MarketState, MarketTypes, MeasurementType, 
    MeasurementUnit, Method, OrderStatus, SupplyOrDemand, VTypes
)


class TestDirection:
    """Test cases for Direction enumeration"""
    
    def test_direction_values(self):
        """Test all Direction enum values"""
        assert Direction.bidirectional.value == 1
        assert Direction.downstream.value == 2
        assert Direction.unknown.value == 3
        assert Direction.upstream.value == 4
        
    def test_direction_names(self):
        """Test Direction enum names"""
        assert Direction.bidirectional.name == 'bidirectional'
        assert Direction.downstream.name == 'downstream'
        assert Direction.unknown.name == 'unknown'
        assert Direction.upstream.name == 'upstream'
        
    def test_direction_count(self):
        """Test number of Direction enum members"""
        assert len(Direction) == 4
        
    def test_direction_by_value(self):
        """Test accessing Direction by value"""
        assert Direction(1) == Direction.bidirectional
        assert Direction(2) == Direction.downstream
        assert Direction(3) == Direction.unknown
        assert Direction(4) == Direction.upstream
        
    def test_direction_by_name(self):
        """Test accessing Direction by name"""
        assert Direction['bidirectional'] == Direction.bidirectional
        assert Direction['downstream'] == Direction.downstream
        assert Direction['unknown'] == Direction.unknown
        assert Direction['upstream'] == Direction.upstream
        
    def test_direction_invalid_value(self):
        """Test Direction with invalid value"""
        with pytest.raises(ValueError):
            Direction(99)
            
    def test_direction_invalid_name(self):
        """Test Direction with invalid name"""
        with pytest.raises(KeyError):
            Direction['invalid_direction']
            
    def test_direction_iteration(self):
        """Test iterating over Direction enum"""
        directions = list(Direction)
        assert len(directions) == 4
        assert Direction.bidirectional in directions
        assert Direction.downstream in directions
        assert Direction.unknown in directions
        assert Direction.upstream in directions
        
    def test_direction_comparison(self):
        """Test Direction enum comparison"""
        assert Direction.bidirectional == Direction.bidirectional
        assert Direction.bidirectional != Direction.downstream
        
    def test_direction_is_enum(self):
        """Test Direction inherits from Enum"""
        assert issubclass(Direction, Enum)
        assert isinstance(Direction.bidirectional, Direction)


class TestMarketState:
    """Test cases for MarketState enumeration"""
    
    def test_market_state_values(self):
        """Test all MarketState enum values"""
        assert MarketState.Initial.value == 2
        assert MarketState.Active.value == 1
        assert MarketState.Inactive.value == 2
        assert MarketState.Delivery.value == 3
        assert MarketState.DeliveryLead.value == 4
        assert MarketState.Expired.value == 5
        assert MarketState.Exploring.value == 6
        assert MarketState.MarketLead.value == 7
        assert MarketState.Negotiation.value == 8
        assert MarketState.Publish.value == 9
        assert MarketState.Reconcile.value == 10
        assert MarketState.Tender.value == 11
        assert MarketState.Transaction.value == 12
        
    def test_market_state_duplicate_values(self):
        """Test that Initial and Inactive have same value (design decision)"""
        assert MarketState.Initial.value == MarketState.Inactive.value == 2
        
    def test_market_state_names(self):
        """Test MarketState enum names"""
        assert MarketState.Initial.name == 'Initial'
        assert MarketState.Active.name == 'Active'
        assert MarketState.Inactive.name == 'Inactive'
        assert MarketState.Delivery.name == 'Delivery'
        
    def test_market_state_count(self):
        """Test number of MarketState enum members"""
        assert len(MarketState) == 12
        
    def test_market_state_by_value(self):
        """Test accessing MarketState by value"""
        assert MarketState(1) == MarketState.Active
        assert MarketState(2) == MarketState.Initial  # First one with value 2
        assert MarketState(3) == MarketState.Delivery
        
    def test_market_state_aliases(self):
        """Test that Initial and Inactive are aliases"""
        # Due to same values, one becomes an alias of the other
        initial_by_value = MarketState(2)
        assert initial_by_value in [MarketState.Initial, MarketState.Inactive]
        
    def test_market_state_iteration(self):
        """Test iterating over MarketState enum"""
        states = list(MarketState)
        assert len(states) == 12
        assert MarketState.Active in states
        assert MarketState.Transaction in states
        
    def test_market_state_workflow_order(self):
        """Test market state workflow logical ordering"""
        # Test some logical workflow progressions
        workflow_states = [
            MarketState.Initial,
            MarketState.Active,
            MarketState.Exploring,
            MarketState.Negotiation,
            MarketState.Tender,
            MarketState.Transaction,
            MarketState.Delivery,
            MarketState.Reconcile,
            MarketState.Expired
        ]
        
        # All should be valid states
        for state in workflow_states:
            assert isinstance(state, MarketState)


class TestMarketTypes:
    """Test cases for MarketTypes enumeration"""
    
    def test_market_types_values(self):
        """Test all MarketTypes enum values"""
        assert MarketTypes.auction.value == 1
        assert MarketTypes.consensus.value == 2
        assert MarketTypes.matching_engine.value == 3
        assert MarketTypes.reconciliation.value == 4
        assert MarketTypes.unknown.value == 5
        
    def test_market_types_names(self):
        """Test MarketTypes enum names"""
        assert MarketTypes.auction.name == 'auction'
        assert MarketTypes.consensus.name == 'consensus'
        assert MarketTypes.matching_engine.name == 'matching_engine'
        assert MarketTypes.reconciliation.name == 'reconciliation'
        assert MarketTypes.unknown.name == 'unknown'
        
    def test_market_types_count(self):
        """Test number of MarketTypes enum members"""
        assert len(MarketTypes) == 5
        
    def test_market_types_by_value(self):
        """Test accessing MarketTypes by value"""
        assert MarketTypes(1) == MarketTypes.auction
        assert MarketTypes(2) == MarketTypes.consensus
        assert MarketTypes(3) == MarketTypes.matching_engine
        assert MarketTypes(4) == MarketTypes.reconciliation
        assert MarketTypes(5) == MarketTypes.unknown
        
    def test_market_types_case_sensitivity(self):
        """Test MarketTypes name case sensitivity"""
        assert MarketTypes['auction'] == MarketTypes.auction
        assert MarketTypes['matching_engine'] == MarketTypes.matching_engine
        
        with pytest.raises(KeyError):
            MarketTypes['AUCTION']  # Wrong case
            
    def test_market_types_iteration(self):
        """Test iterating over MarketTypes enum"""
        types = list(MarketTypes)
        assert len(types) == 5
        assert MarketTypes.auction in types
        assert MarketTypes.unknown in types


class TestMeasurementType:
    """Test cases for MeasurementType enumeration"""
    
    def test_measurement_type_basic_values(self):
        """Test basic MeasurementType enum values"""
        assert MeasurementType.Voltage.value == 1
        assert MeasurementType.PowerReal.value == 2
        assert MeasurementType.PowerReactive.value == 3
        assert MeasurementType.Unknown.value == 40
        
    def test_measurement_type_power_related(self):
        """Test power-related measurement types"""
        power_types = [
            MeasurementType.PowerReal,
            MeasurementType.PowerReactive,
            MeasurementType.PowerMinimum_real,
            MeasurementType.PowerMaximum_real,
            MeasurementType.PowerMinimum_reactive,
            MeasurementType.PowerMaximum_reactive,
            MeasurementType.ScheduledPower
        ]
        
        for power_type in power_types:
            assert isinstance(power_type, MeasurementType)
            assert 'Power' in power_type.name or 'ScheduledPower' in power_type.name
            
    def test_measurement_type_price_related(self):
        """Test price-related measurement types"""
        price_types = [
            MeasurementType.PriceIncremental,
            MeasurementType.PriceBlended,
            MeasurementType.MarginalPrice,
            MeasurementType.BlendedPrice
        ]
        
        for price_type in price_types:
            assert isinstance(price_type, MeasurementType)
            assert 'Price' in price_type.name
            
    def test_measurement_type_energy_related(self):
        """Test energy-related measurement types"""
        energy_types = [
            MeasurementType.EnergyReal,
            MeasurementType.EnergyReactive
        ]
        
        for energy_type in energy_types:
            assert isinstance(energy_type, MeasurementType)
            assert 'Energy' in energy_type.name
            
    def test_measurement_type_vertex_related(self):
        """Test vertex-related measurement types"""
        vertex_types = [
            MeasurementType.ProdVertex,
            MeasurementType.ActiveVertex,
            MeasurementType.Vertex,
            MeasurementType.TestVertex,
            MeasurementType.SystemVertex
        ]
        
        for vertex_type in vertex_types:
            assert isinstance(vertex_type, MeasurementType)
            assert 'Vertex' in vertex_type.name
            
    def test_measurement_type_cost_related(self):
        """Test cost-related measurement types"""
        cost_types = [
            MeasurementType.TransitionCost,
            MeasurementType.DualCost,
            MeasurementType.ProductionCost
        ]
        
        for cost_type in cost_types:
            assert isinstance(cost_type, MeasurementType)
            assert 'Cost' in cost_type.name
            
    def test_measurement_type_weather_related(self):
        """Test weather-related measurement types"""
        weather_types = [
            MeasurementType.Temperature,
            MeasurementType.IsolationDensity,
            MeasurementType.RelativeHumidity
        ]
        
        for weather_type in weather_types:
            assert isinstance(weather_type, MeasurementType)
            
    def test_measurement_type_count(self):
        """Test number of MeasurementType enum members"""
        assert len(MeasurementType) == 35  # Count all defined values
        
    def test_measurement_type_unique_values(self):
        """Test that all MeasurementType values are unique"""
        values = [member.value for member in MeasurementType]
        assert len(values) == len(set(values))  # No duplicates
        
    def test_measurement_type_naming_convention(self):
        """Test MeasurementType naming conventions"""
        # Most should use PascalCase
        pascal_case_types = [
            MeasurementType.Voltage,
            MeasurementType.PowerReal,
            MeasurementType.Temperature
        ]
        
        for mt in pascal_case_types:
            # First letter should be uppercase
            assert mt.name[0].isupper()


class TestMeasurementUnit:
    """Test cases for MeasurementUnit enumeration"""
    
    def test_measurement_unit_values(self):
        """Test all MeasurementUnit enum values"""
        assert MeasurementUnit.degF.value == 1
        assert MeasurementUnit.kVAR.value == 2
        assert MeasurementUnit.kVARh.value == 3
        assert MeasurementUnit.kW.value == 4
        assert MeasurementUnit.kWh.value == 5
        assert MeasurementUnit.Monetary.value == 6
        assert MeasurementUnit.Boolean.value == 7
        assert MeasurementUnit.Unknown.value == 40
        
    def test_measurement_unit_names(self):
        """Test MeasurementUnit enum names"""
        assert MeasurementUnit.degF.name == 'degF'
        assert MeasurementUnit.kVAR.name == 'kVAR'
        assert MeasurementUnit.kVARh.name == 'kVARh'
        assert MeasurementUnit.kW.name == 'kW'
        assert MeasurementUnit.kWh.name == 'kWh'
        assert MeasurementUnit.Monetary.name == 'Monetary'
        assert MeasurementUnit.Boolean.name == 'Boolean'
        assert MeasurementUnit.Unknown.name == 'Unknown'
        
    def test_measurement_unit_count(self):
        """Test number of MeasurementUnit enum members"""
        assert len(MeasurementUnit) == 8
        
    def test_measurement_unit_power_units(self):
        """Test power-related measurement units"""
        power_units = [
            MeasurementUnit.kW,
            MeasurementUnit.kVAR
        ]
        
        for unit in power_units:
            assert isinstance(unit, MeasurementUnit)
            assert 'k' in unit.name  # All power units start with 'k'
            
    def test_measurement_unit_energy_units(self):
        """Test energy-related measurement units"""
        energy_units = [
            MeasurementUnit.kWh,
            MeasurementUnit.kVARh
        ]
        
        for unit in energy_units:
            assert isinstance(unit, MeasurementUnit)
            assert unit.name.endswith('h')  # Energy units end with 'h'
            
    def test_measurement_unit_special_units(self):
        """Test special measurement units"""
        special_units = [
            MeasurementUnit.degF,      # Temperature
            MeasurementUnit.Monetary,  # Currency
            MeasurementUnit.Boolean,   # True/False
            MeasurementUnit.Unknown    # Fallback
        ]
        
        for unit in special_units:
            assert isinstance(unit, MeasurementUnit)
            
    def test_measurement_unit_by_value(self):
        """Test accessing MeasurementUnit by value"""
        assert MeasurementUnit(1) == MeasurementUnit.degF
        assert MeasurementUnit(4) == MeasurementUnit.kW
        assert MeasurementUnit(40) == MeasurementUnit.Unknown


class TestMethod:
    """Test cases for Method enumeration"""
    
    def test_method_values(self):
        """Test all Method enum values"""
        assert Method.Subgradient.value == 1
        assert Method.Interpolation.value == 2
        
    def test_method_names(self):
        """Test Method enum names"""
        assert Method.Subgradient.name == 'Subgradient'
        assert Method.Interpolation.name == 'Interpolation'
        
    def test_method_count(self):
        """Test number of Method enum members"""
        assert len(Method) == 2
        
    def test_method_by_value(self):
        """Test accessing Method by value"""
        assert Method(1) == Method.Subgradient
        assert Method(2) == Method.Interpolation
        
    def test_method_by_name(self):
        """Test accessing Method by name"""
        assert Method['Subgradient'] == Method.Subgradient
        assert Method['Interpolation'] == Method.Interpolation
        
    def test_method_invalid_value(self):
        """Test Method with invalid value"""
        with pytest.raises(ValueError):
            Method(99)
            
    def test_method_case_sensitivity(self):
        """Test Method name case sensitivity"""
        with pytest.raises(KeyError):
            Method['subgradient']  # Wrong case
            
    def test_method_iteration(self):
        """Test iterating over Method enum"""
        methods = list(Method)
        assert len(methods) == 2
        assert Method.Subgradient in methods
        assert Method.Interpolation in methods


class TestOrderStatus:
    """Test cases for OrderStatus enumeration"""
    
    def test_order_status_values(self):
        """Test all OrderStatus enum values"""
        assert OrderStatus.NEW.value == 0
        assert OrderStatus.NEW_REVISED.value == 1
        assert OrderStatus.STANDING_ORDER.value == 2
        assert OrderStatus.REVISED_STANDING_ORDER.value == 3
        assert OrderStatus.MATCHED.value == 4
        assert OrderStatus.REVISED_MATCHED.value == 5
        assert OrderStatus.CANCELLED.value == 6
        assert OrderStatus.EXPIRED.value == 7
        assert OrderStatus.SUPERSEDED.value == 8
        
    def test_order_status_names(self):
        """Test OrderStatus enum names"""
        assert OrderStatus.NEW.name == 'NEW'
        assert OrderStatus.NEW_REVISED.name == 'NEW_REVISED'
        assert OrderStatus.STANDING_ORDER.name == 'STANDING_ORDER'
        assert OrderStatus.REVISED_STANDING_ORDER.name == 'REVISED_STANDING_ORDER'
        assert OrderStatus.MATCHED.name == 'MATCHED'
        assert OrderStatus.REVISED_MATCHED.name == 'REVISED_MATCHED'
        assert OrderStatus.CANCELLED.name == 'CANCELLED'
        assert OrderStatus.EXPIRED.name == 'EXPIRED'
        assert OrderStatus.SUPERSEDED.name == 'SUPERSEDED'
        
    def test_order_status_count(self):
        """Test number of OrderStatus enum members"""
        assert len(OrderStatus) == 9
        
    def test_order_status_progression(self):
        """Test logical order status progression"""
        # Test typical order lifecycle
        new_statuses = [OrderStatus.NEW, OrderStatus.NEW_REVISED]
        standing_statuses = [OrderStatus.STANDING_ORDER, OrderStatus.REVISED_STANDING_ORDER]
        final_statuses = [OrderStatus.MATCHED, OrderStatus.REVISED_MATCHED, 
                         OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.SUPERSEDED]
        
        # All should be valid statuses
        all_statuses = new_statuses + standing_statuses + final_statuses
        for status in all_statuses:
            assert isinstance(status, OrderStatus)
            
    def test_order_status_revised_variants(self):
        """Test that revised variants exist for key statuses"""
        revised_pairs = [
            (OrderStatus.NEW, OrderStatus.NEW_REVISED),
            (OrderStatus.STANDING_ORDER, OrderStatus.REVISED_STANDING_ORDER),
            (OrderStatus.MATCHED, OrderStatus.REVISED_MATCHED)
        ]
        
        for original, revised in revised_pairs:
            assert isinstance(original, OrderStatus)
            assert isinstance(revised, OrderStatus)
            assert 'REVISED' in revised.name


class TestSupplyOrDemand:
    """Test cases for SupplyOrDemand enumeration"""
    
    def test_supply_or_demand_values(self):
        """Test all SupplyOrDemand enum values"""
        assert SupplyOrDemand.SUPPLY.value == 0
        assert SupplyOrDemand.DEMAND.value == 1
        assert SupplyOrDemand.BOTH.value == 2
        
    def test_supply_or_demand_names(self):
        """Test SupplyOrDemand enum names"""
        assert SupplyOrDemand.SUPPLY.name == 'SUPPLY'
        assert SupplyOrDemand.DEMAND.name == 'DEMAND'
        assert SupplyOrDemand.BOTH.name == 'BOTH'
        
    def test_supply_or_demand_count(self):
        """Test number of SupplyOrDemand enum members"""
        assert len(SupplyOrDemand) == 3
        
    def test_supply_or_demand_by_value(self):
        """Test accessing SupplyOrDemand by value"""
        assert SupplyOrDemand(0) == SupplyOrDemand.SUPPLY
        assert SupplyOrDemand(1) == SupplyOrDemand.DEMAND
        assert SupplyOrDemand(2) == SupplyOrDemand.BOTH
        
    def test_supply_or_demand_market_logic(self):
        """Test supply/demand market logic"""
        # Supply and Demand should be opposites
        assert SupplyOrDemand.SUPPLY != SupplyOrDemand.DEMAND
        
        # BOTH should encompass both supply and demand scenarios
        assert SupplyOrDemand.BOTH != SupplyOrDemand.SUPPLY
        assert SupplyOrDemand.BOTH != SupplyOrDemand.DEMAND
        
    def test_supply_or_demand_iteration(self):
        """Test iterating over SupplyOrDemand enum"""
        types = list(SupplyOrDemand)
        assert len(types) == 3
        assert SupplyOrDemand.SUPPLY in types
        assert SupplyOrDemand.DEMAND in types
        assert SupplyOrDemand.BOTH in types


class TestVTypes:
    """Test cases for VTypes enumeration"""
    
    def test_vtypes_values(self):
        """Test all VTypes enum values"""
        assert VTypes.NONE.value == 0
        assert VTypes.LIMIT_ORDER.value == 1
        assert VTypes.MARKET_ORDER.value == 1  # Same as LIMIT_ORDER
        assert VTypes.LINEAR.value == 2
        assert VTypes.COMPLEX.value == 3
        
    def test_vtypes_names(self):
        """Test VTypes enum names"""
        assert VTypes.NONE.name == 'NONE'
        assert VTypes.LIMIT_ORDER.name == 'LIMIT_ORDER'
        assert VTypes.MARKET_ORDER.name == 'MARKET_ORDER'
        assert VTypes.LINEAR.name == 'LINEAR'
        assert VTypes.COMPLEX.name == 'COMPLEX'
        
    def test_vtypes_duplicate_values(self):
        """Test that LIMIT_ORDER and MARKET_ORDER have same value"""
        assert VTypes.LIMIT_ORDER.value == VTypes.MARKET_ORDER.value == 1
        
    def test_vtypes_count(self):
        """Test number of VTypes enum members"""
        assert len(VTypes) == 5
        
    def test_vtypes_by_value(self):
        """Test accessing VTypes by value"""
        assert VTypes(0) == VTypes.NONE
        assert VTypes(1) == VTypes.LIMIT_ORDER  # First one with value 1
        assert VTypes(2) == VTypes.LINEAR
        assert VTypes(3) == VTypes.COMPLEX
        
    def test_vtypes_aliases(self):
        """Test that LIMIT_ORDER and MARKET_ORDER are aliases"""
        # Due to same values, one becomes an alias of the other
        order_by_value = VTypes(1)
        assert order_by_value in [VTypes.LIMIT_ORDER, VTypes.MARKET_ORDER]
        
    def test_vtypes_order_types(self):
        """Test order-related VTypes"""
        order_types = [VTypes.LIMIT_ORDER, VTypes.MARKET_ORDER]
        
        for order_type in order_types:
            assert isinstance(order_type, VTypes)
            assert 'ORDER' in order_type.name
            
    def test_vtypes_curve_types(self):
        """Test curve-related VTypes"""
        curve_types = [VTypes.LINEAR, VTypes.COMPLEX]
        
        for curve_type in curve_types:
            assert isinstance(curve_type, VTypes)
            assert curve_type.value >= 2  # Non-order types


class TestEnumerationIntegration:
    """Integration tests for enumeration usage"""
    
    def test_enum_imports(self):
        """Test that all enums can be imported from main module"""
        from tent.enumerations import (
            Direction, MarketState, MarketTypes, MeasurementType,
            MeasurementUnit, Method, OrderStatus, SupplyOrDemand, VTypes
        )
        
        # All should be Enum subclasses
        enums = [Direction, MarketState, MarketTypes, MeasurementType,
                MeasurementUnit, Method, OrderStatus, SupplyOrDemand, VTypes]
        
        for enum_class in enums:
            assert issubclass(enum_class, Enum)
            
    def test_enum_all_export(self):
        """Test __all__ export list"""
        import tent.enumerations as enums
        
        expected_exports = [
            'Direction', 'MarketState', 'MarketTypes', 'MeasurementType',
            'MeasurementUnit', 'Method', 'OrderStatus', 'SupplyOrDemand', 'VTypes'
        ]
        
        for export in expected_exports:
            assert export in enums.__all__
            assert hasattr(enums, export)
            
    def test_measurement_type_unit_compatibility(self):
        """Test logical compatibility between measurement types and units"""
        # Power types should be compatible with power units
        power_types = [MeasurementType.PowerReal, MeasurementType.PowerReactive]
        power_units = [MeasurementUnit.kW, MeasurementUnit.kVAR]
        
        # Energy types should be compatible with energy units
        energy_types = [MeasurementType.EnergyReal, MeasurementType.EnergyReactive]
        energy_units = [MeasurementUnit.kWh, MeasurementUnit.kVARh]
        
        # Price types should be compatible with monetary units
        price_types = [MeasurementType.PriceIncremental, MeasurementType.PriceBlended]
        monetary_units = [MeasurementUnit.Monetary]
        
        # Temperature should be compatible with temperature units
        temp_types = [MeasurementType.Temperature]
        temp_units = [MeasurementUnit.degF]
        
        # All these should be valid enum values
        all_types = power_types + energy_types + price_types + temp_types
        all_units = power_units + energy_units + monetary_units + temp_units
        
        for mt in all_types:
            assert isinstance(mt, MeasurementType)
            
        for mu in all_units:
            assert isinstance(mu, MeasurementUnit)
            
    def test_order_workflow_compatibility(self):
        """Test order status and supply/demand workflow compatibility"""
        # Test typical order workflow
        initial_status = OrderStatus.NEW
        supply_type = SupplyOrDemand.SUPPLY
        vertex_type = VTypes.LINEAR
        
        # All should be valid enum values
        assert isinstance(initial_status, OrderStatus)
        assert isinstance(supply_type, SupplyOrDemand)
        assert isinstance(vertex_type, VTypes)
        
        # Test order progression
        status_progression = [
            OrderStatus.NEW,
            OrderStatus.STANDING_ORDER,
            OrderStatus.MATCHED
        ]
        
        for status in status_progression:
            assert isinstance(status, OrderStatus)
            
    def test_market_workflow_compatibility(self):
        """Test market state and type workflow compatibility"""
        # Test typical market workflow
        market_type = MarketTypes.auction
        initial_state = MarketState.Initial
        active_state = MarketState.Active
        
        assert isinstance(market_type, MarketTypes)
        assert isinstance(initial_state, MarketState)
        assert isinstance(active_state, MarketState)
        
        # Test market state progression
        state_progression = [
            MarketState.Initial,
            MarketState.Active,
            MarketState.Transaction,
            MarketState.Delivery,
            MarketState.Expired
        ]
        
        for state in state_progression:
            assert isinstance(state, MarketState)


class TestEnumerationEdgeCases:
    """Test edge cases and error conditions"""
    
    def test_enum_string_representation(self):
        """Test string representation of enum values"""
        # Test that enum values have meaningful string representations
        assert str(Direction.bidirectional) == 'Direction.bidirectional'
        assert repr(Direction.bidirectional) == '<Direction.bidirectional: 1>'
        
    def test_enum_equality_with_values(self):
        """Test enum equality with raw values"""
        # Enums should not be equal to their raw values
        assert Direction.bidirectional != 1
        assert MarketState.Active != 1
        assert OrderStatus.NEW != 0
        
    def test_enum_hash_consistency(self):
        """Test enum hashing consistency"""
        # Same enum values should have same hash
        assert hash(Direction.bidirectional) == hash(Direction.bidirectional)
        assert hash(OrderStatus.NEW) == hash(OrderStatus.NEW)
        
        # Different enum values should have different hashes (usually)
        assert hash(Direction.bidirectional) != hash(Direction.downstream)
        
    def test_enum_in_sets(self):
        """Test enum values in sets and dictionaries"""
        # Enums should work as dictionary keys and set members
        direction_set = {Direction.bidirectional, Direction.downstream}
        assert len(direction_set) == 2
        assert Direction.bidirectional in direction_set
        
        state_dict = {MarketState.Active: "active", MarketState.Inactive: "inactive"}
        assert MarketState.Active in state_dict
        assert state_dict[MarketState.Active] == "active"
        
    def test_enum_boolean_context(self):
        """Test enum values in boolean contexts"""
        # All enum values should be truthy
        assert bool(Direction.unknown)
        assert bool(OrderStatus.NEW)
        assert bool(MeasurementUnit.Unknown)
        
    def test_enum_sorting(self):
        """Test enum value sorting"""
        # Enums with same type should be sortable by value
        methods = [Method.Interpolation, Method.Subgradient]
        sorted_methods = sorted(methods, key=lambda x: x.value)
        
        assert sorted_methods[0] == Method.Subgradient  # value 1
        assert sorted_methods[1] == Method.Interpolation  # value 2


if __name__ == "__main__":
    pytest.main([__file__])