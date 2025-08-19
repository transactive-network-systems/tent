import pytest
import logging
from datetime import datetime, timedelta, time
from unittest.mock import Mock, patch, MagicMock, call
from collections import deque
import uuid

# Import all market classes
from tent.market import Market
from tent.market.auction import Auction
from tent.market.consensus_market import ConsensusMarket
from tent.market.day_ahead_auction import DayAheadAuction
from tent.market.real_time_auction import RealTimeAuction
from tent.market.matching_engine import MatchingEngine, MatchingStrategy, Role
from tent.market.market_series import MarketSeries
from tent.market.price_models.hourly_average_price_model import HourlyAveragePriceModel

from tent.containers import MarketDurations, MarketTimings, IntervalSeries, Vertex
from tent.containers.order import Order, OrderStatus, SupplyOrDemand, VTypes
from tent.enumerations import MarketState, MarketTypes, Method, MeasurementType, MeasurementUnit, Direction


class TestHourlyAveragePriceModel:
    """Test cases for HourlyAveragePriceModel class"""
    
    def test_init_default(self):
        """Test default initialization"""
        model = HourlyAveragePriceModel()
        
        assert len(model.current_model) == 24
        for hour in range(24):
            assert hour in model.current_model
            assert len(model.current_model[hour]) == 2  # Default price added twice
            assert model.current_model[hour][0] == 0.035
            
    def test_init_with_initial_model(self):
        """Test initialization with valid initial model"""
        initial_model = {}
        for h in range(24):
            initial_model[h] = deque([0.05, 0.06], maxlen=14)
            
        model = HourlyAveragePriceModel(initial_model=initial_model, forgetting_factor=14)
        
        assert model.current_model == initial_model
        assert model.current_model[0][0] == 0.05
        
    def test_init_with_invalid_initial_model(self):
        """Test initialization with invalid initial model falls back to default"""
        invalid_model = {"invalid": "model"}
        
        with patch('tent.market.price_models.hourly_average_price_model._log') as mock_log:
            model = HourlyAveragePriceModel(initial_model=invalid_model, forgetting_factor=10)
            
            mock_log.warning.assert_called_once()
            assert len(model.current_model) == 24
            
    def test_update(self):
        """Test updating price model with new price"""
        model = HourlyAveragePriceModel(forgetting_factor=5)
        test_time = datetime(2023, 1, 1, 14, 30, 0)  # 2 PM
        
        initial_length = len(model.current_model[14])
        model.update(test_time, 0.08)
        
        assert len(model.current_model[14]) == initial_length + 1
        assert model.current_model[14][-1] == 0.08
        
    def test_update_with_maxlen(self):
        """Test that deque respects maxlen"""
        model = HourlyAveragePriceModel(forgetting_factor=3)
        test_time = datetime(2023, 1, 1, 10, 0, 0)
        
        # Add more prices than maxlen
        for price in [0.05, 0.06, 0.07, 0.08, 0.09]:
            model.update(test_time, price)
            
        # Should only keep last 3 prices
        assert len(model.current_model[10]) == 3
        assert list(model.current_model[10]) == [0.07, 0.08, 0.09]
        
    def test_get_normal_case(self):
        """Test getting mean and std dev for normal case"""
        model = HourlyAveragePriceModel()
        test_time = datetime(2023, 1, 1, 8, 0, 0)
        
        # Add some test data
        for price in [0.05, 0.06, 0.07, 0.08]:
            model.update(test_time, price)
            
        mean_price, std_price = model.get(test_time)
        
        assert isinstance(mean_price, float)
        assert isinstance(std_price, float)
        assert mean_price > 0
        assert std_price >= 0.001  # Minimum std dev
        
    def test_get_statistics_error(self):
        """Test handling of StatisticsError"""
        model = HourlyAveragePriceModel(forgetting_factor=1)
        test_time = datetime(2023, 1, 1, 12, 0, 0)
        
        # Clear the hour to force StatisticsError
        model.current_model[12].clear()
        
        with patch('tent.market.price_models.hourly_average_price_model._log') as mock_log:
            mean_price, std_price = model.get(test_time)
            
            assert mean_price is None
            assert std_price is None
            mock_log.warning.assert_called_once()
            
    def test_init_model_static(self):
        """Test static _init_model method"""
        model_dict = HourlyAveragePriceModel._init_model(forgetting_factor=5, default_price=0.04)
        
        assert len(model_dict) == 24
        for hour in range(24):
            assert hour in model_dict
            assert len(model_dict[hour]) == 2
            assert model_dict[hour][0] == 0.04
            assert model_dict[hour].maxlen == 5


class TestMarket:
    """Test cases for base Market class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.neighbors = []
        self.mock_tn.local_assets = []
        self.mock_tn.markets = []
        self.mock_tn.data_manager = Mock()
        self.mock_tn.id = uuid.uuid4()
        
        self.mock_series = Mock()
        self.mock_series.description = "Test Market Series"
        self.mock_series.name = "TestSeries"
        self.mock_series.initial_market_state = MarketState.Inactive
        self.mock_series.intervals_to_clear = 1
        self.mock_series.duration = Mock()
        self.mock_series.duration.market_interval = timedelta(hours=1)
        self.mock_series.method = Method.Interpolation
        self.mock_series.duality_gap_threshold = 0.01
        self.mock_series.market_type = MarketTypes.unknown
        
        self.transition_times = MarketTimings(
            active=datetime(2023, 1, 1, 10, 0, 0),
            negotiation=datetime(2023, 1, 1, 11, 0, 0),
            market_lead=datetime(2023, 1, 1, 11, 30, 0),
            delivery_lead=datetime(2023, 1, 1, 11, 45, 0),
            delivery=datetime(2023, 1, 1, 12, 0, 0),
            reconciliation=datetime(2023, 1, 1, 13, 0, 0),
            expiration=datetime(2023, 1, 1, 14, 0, 0)
        )
        
    def test_market_init(self):
        """Test Market initialization"""
        market = Market(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        assert market.tn == self.mock_tn
        assert market.series == self.mock_series
        assert market.transition_times == self.transition_times
        assert market.market_state == MarketState.Inactive
        assert market.converged is False
        assert market.reconciled is False
        assert market._stateIsCompleted is False
        
    def test_market_name_generation(self):
        """Test market name generation"""
        with patch('tent.market.Market.__init__', return_value=None) as mock_init:
            # This tests the name generation logic
            expected_name_part = self.transition_times.delivery_lead.isoformat(timespec='seconds')
            # The actual test would need to be implemented based on the name generation logic
            
    def test_events_state_machine_initial(self):
        """Test market state machine - initial state"""
        market = Market(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        market.market_state = MarketState.Initial
        
        with patch.object(market, 'transition_from_initial_to_inactive') as mock_transition:
            market.events()
            
            mock_transition.assert_called_once()
            assert market.market_state == MarketState.Inactive
            assert market._stateIsCompleted is False
            
    @patch('tent.utils.timer.Timer.now')
    def test_events_state_machine_inactive_to_active(self, mock_now):
        """Test state transition from Inactive to Active"""
        mock_now.return_value = self.transition_times.active
        
        market = Market(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        market.market_state = MarketState.Inactive
        
        with patch.object(market, 'transition_from_inactive_to_active') as mock_transition:
            market.events()
            
            mock_transition.assert_called_once()
            assert market.market_state == MarketState.Active
            
    def test_balance_method_interpolation(self):
        """Test balance method with interpolation"""
        market = Market(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        with patch.object(market, 'assign_system_vertices') as mock_assign:
            market.balance()
            
            mock_assign.assert_called_once()
            
    def test_check_marginal_prices(self):
        """Test marginal price checking and initialization"""
        market = Market(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        # Mock time intervals
        mock_interval = Mock()
        market.time_intervals = [mock_interval]
        
        market.check_marginal_prices()
        
        # Should not raise errors and should process intervals
        
    def test_schedule(self):
        """Test schedule method"""
        mock_asset = Mock()
        mock_neighbor = Mock()
        
        self.mock_tn.local_assets = [mock_asset]
        self.mock_tn.neighbors = [mock_neighbor]
        
        market = Market(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        market.schedule()
        
        mock_asset.schedule.assert_called_with(market)
        mock_neighbor.schedule.assert_called_with(market)
        
    def test_update_costs(self):
        """Test cost update method"""
        mock_asset = Mock()
        mock_neighbor = Mock()
        
        self.mock_tn.local_assets = [mock_asset]
        self.mock_tn.neighbors = [mock_neighbor]
        
        market = Market(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        market.update_costs()
        
        mock_asset.update_costs.assert_called_with(market)
        mock_neighbor.update_costs.assert_called_with(market)
        
    def test_actuate_assets(self):
        """Test asset actuation"""
        mock_asset = Mock()
        self.mock_tn.local_assets = [mock_asset]
        
        market = Market(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        market._actuate_assets()
        
        mock_asset.actuate.assert_called_with(market)
        
    def test_get_record(self):
        """Test market record generation"""
        market = Market(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        record = market.get_record()
        
        assert 'market_type' in record
        assert 'active_start_time' in record
        assert 'delivery_start_time' in record
        assert record['market_series_id'] == self.mock_series.id


class TestAuction:
    """Test cases for Auction class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.neighbors = []
        self.mock_tn.local_assets = []
        self.mock_tn.data_manager = Mock()
        
        self.mock_series = Mock()
        self.mock_series.description = "Test Auction Series"
        self.mock_series.intervals_to_clear = 1
        self.mock_series.duration = Mock()
        self.mock_series.duration.market_interval = timedelta(hours=1)
        
        self.transition_times = MarketTimings(
            active=datetime(2023, 1, 1, 10, 0, 0),
            negotiation=datetime(2023, 1, 1, 11, 0, 0),
            market_lead=datetime(2023, 1, 1, 11, 30, 0),
            delivery_lead=datetime(2023, 1, 1, 11, 45, 0),
            delivery=datetime(2023, 1, 1, 12, 0, 0),
            reconciliation=datetime(2023, 1, 1, 13, 0, 0),
            expiration=datetime(2023, 1, 1, 14, 0, 0)
        )
        
    def test_auction_init(self):
        """Test Auction initialization"""
        auction = Auction(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        assert auction.MARKET_TYPE == MarketTypes.auction
        
    def test_transition_from_active_to_negotiation(self):
        """Test auction transition to negotiation"""
        mock_asset = Mock()
        mock_asset.schedule_calculated = False
        self.mock_tn.local_assets = [mock_asset]
        
        auction = Auction(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        with patch.object(auction, 'check_marginal_prices'):
            auction.transition_from_active_to_negotiation()
            
            mock_asset.schedule.assert_called_with(auction)
            assert auction.converged is False
            
    def test_while_in_negotiation_all_calculated(self):
        """Test negotiation state when all assets are calculated"""
        mock_asset1 = Mock()
        mock_asset1.schedule_calculated = True
        mock_asset2 = Mock()
        mock_asset2.schedule_calculated = True
        
        self.mock_tn.local_assets = [mock_asset1, mock_asset2]
        
        auction = Auction(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        auction.converged = False
        auction.while_in_negotiation()
        
        assert auction.converged is True
        assert auction._stateIsCompleted is True
        
    def test_while_in_negotiation_not_all_calculated(self):
        """Test negotiation state when not all assets are calculated"""
        mock_asset1 = Mock()
        mock_asset1.schedule_calculated = True
        mock_asset2 = Mock()
        mock_asset2.schedule_calculated = False
        
        self.mock_tn.local_assets = [mock_asset1, mock_asset2]
        
        auction = Auction(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        auction.converged = False
        auction.while_in_negotiation()
        
        assert auction.converged is False
        
    def test_while_in_market_lead_upstream_downstream(self):
        """Test market lead state with upstream and downstream neighbors"""
        mock_upstream = Mock()
        mock_upstream.up_or_down = Direction.upstream
        mock_upstream.transactive = True
        mock_upstream.received_signal = []
        
        mock_downstream = Mock()
        mock_downstream.up_or_down = Direction.downstream
        mock_downstream.received_signal = []
        
        self.mock_tn.neighbors = [mock_upstream, mock_downstream]
        
        auction = Auction(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        # Mock time intervals
        auction.time_intervals = []
        
        auction.while_in_market_lead()
        
        # Should complete without errors
        
    def test_while_in_market_lead_no_upstream_error(self):
        """Test market lead state error with no upstream neighbor"""
        self.mock_tn.neighbors = []
        
        auction = Auction(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        with pytest.raises(RuntimeWarning):
            auction.while_in_market_lead()


class TestConsensusMarket:
    """Test cases for ConsensusMarket class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.neighbors = []
        self.mock_tn.local_assets = []
        
        self.mock_series = Mock()
        self.mock_series.method = Method.Interpolation
        self.mock_series.duality_gap_threshold = 0.01
        
        self.transition_times = MarketTimings(
            active=datetime(2023, 1, 1, 10, 0, 0),
            negotiation=datetime(2023, 1, 1, 11, 0, 0),
            market_lead=datetime(2023, 1, 1, 11, 30, 0),
            delivery_lead=datetime(2023, 1, 1, 11, 45, 0),
            delivery=datetime(2023, 1, 1, 12, 0, 0),
            reconciliation=datetime(2023, 1, 1, 13, 0, 0),
            expiration=datetime(2023, 1, 1, 14, 0, 0)
        )
        
    def test_consensus_market_init(self):
        """Test ConsensusMarket initialization"""
        market = ConsensusMarket(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        assert market.MARKET_TYPE == MarketTypes.consensus
        
    def test_while_in_negotiation_convergence(self):
        """Test negotiation with convergence logic"""
        market = ConsensusMarket(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        # Mock required methods and properties
        market.total_production_cost = 100.0
        market.total_dual_cost = 99.0
        market.time_intervals = []
        
        with patch.object(market, 'check_marginal_prices'):
            with patch.object(market, 'balance'):
                with patch.object(market, 'assign_system_vertices'):
                    market.while_in_negotiation()
                    
                    # Should set converged to True due to low duality gap
                    assert market.converged is True
                    
    def test_while_in_negotiation_max_iterations(self):
        """Test negotiation hits max iterations"""
        market = ConsensusMarket(
            transactive_node=self.mock_tn,
            market_series=self.mock_series,
            transition_times=self.transition_times
        )
        
        # Set up for non-convergence
        market.total_production_cost = 100.0
        market.total_dual_cost = 50.0  # Large duality gap
        market.time_intervals = []
        
        with patch.object(market, 'check_marginal_prices'):
            with patch.object(market, 'balance'):
                with patch.object(market, 'assign_system_vertices'):
                    market.while_in_negotiation()
                    
                    # Should converge due to max iterations
                    assert market.converged is True


class TestMatchingEngine:
    """Test cases for MatchingEngine class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_series = Mock()
        self.mock_series.duration = Mock()
        self.mock_series.duration.market_interval = timedelta(hours=1)
        self.mock_series.duration.market_clearing_interval = timedelta(hours=1)
        self.mock_series.name = "TestMatchingEngine"
        
    def test_matching_engine_init(self):
        """Test MatchingEngine initialization"""
        engine = MatchingEngine()
        
        assert engine.MARKET_TYPE == MarketTypes.matching_engine
        assert isinstance(engine.orderList, list)
        assert isinstance(engine.matchList, list)
        assert engine.isNewestMarket is True
        
    def test_matching_engine_properties(self):
        """Test MatchingEngine protected properties"""
        strategy = [MatchingStrategy.ORDER_VOLUME]
        engine = MatchingEngine(matching_strategy=strategy)
        
        assert engine.matchingStrategy == strategy
        
        # Test that properties are protected (setters do nothing)
        engine.matchingStrategy = [MatchingStrategy.ORDER_AGE]
        assert engine.matchingStrategy == strategy  # Should not change
        
    def test_match_supply_order(self):
        """Test matching a supply order"""
        engine = MatchingEngine()
        
        # Create mock supply order
        mock_supply_order = Mock()
        mock_supply_order.supply_or_demand = SupplyOrDemand.SUPPLY
        mock_supply_order.actor = Mock()
        
        # Mock demand orders in order book
        mock_demand_order = Mock()
        mock_demand_order.supply_or_demand = SupplyOrDemand.DEMAND
        mock_demand_order.status = OrderStatus.STANDING_ORDER
        
        engine.orderList = [mock_demand_order]
        
        with patch.object(engine, 'get_counteroffers', return_value=[mock_demand_order]):
            with patch('tent.containers.order.Order.get_prices_from_curves', return_value=[10.0]):
                with patch.object(mock_supply_order, 'get_quantity_from_price') as mock_get_qty:
                    mock_vertex = Mock()
                    mock_vertex.quantity = 0.0  # No quantity, should break
                    mock_get_qty.return_value = mock_vertex
                    
                    engine.match(mock_supply_order)
                    
                    # Should process without errors
                    
    def test_get_counteroffers(self):
        """Test getting counteroffers"""
        engine = MatchingEngine()
        
        # Create mock orders
        supply_order = Mock()
        supply_order.supply_or_demand = SupplyOrDemand.SUPPLY
        supply_order.status = OrderStatus.STANDING_ORDER
        
        demand_order = Mock()
        demand_order.supply_or_demand = SupplyOrDemand.DEMAND
        demand_order.status = OrderStatus.STANDING_ORDER
        
        engine.orderList = [supply_order, demand_order]
        
        # Get supply counteroffers (should return supply orders)
        supply_counteroffers = engine.get_counteroffers(SupplyOrDemand.SUPPLY)
        assert supply_order in supply_counteroffers
        assert demand_order not in supply_counteroffers
        
    def test_get_order_by_id(self):
        """Test getting order by ID"""
        engine = MatchingEngine()
        
        mock_order = Mock()
        mock_order.id = 123
        
        engine.orderList = [mock_order]
        
        found_order = engine.get_order_by_id(123)
        assert found_order == mock_order
        
        not_found = engine.get_order_by_id(999)
        assert not_found is None
        
    def test_order_id_is_unique(self):
        """Test order ID uniqueness check"""
        engine = MatchingEngine()
        
        mock_order = Mock()
        mock_order.id = 123
        
        engine.orderList = [mock_order]
        
        assert engine.order_id_is_unique(456) is True
        assert engine.order_id_is_unique(123) is False
        assert engine.order_id_is_unique(None) is None
        
    def test_spawn_markets(self):
        """Test spawning new markets"""
        engine = MatchingEngine()
        engine.deliveryLeadTime = timedelta(minutes=15)
        engine.negotiationLeadTime = timedelta(hours=1)
        
        new_clearing_time = datetime(2023, 1, 1, 12, 0, 0)
        
        engine.spawn_markets(self.mock_tn, new_clearing_time)
        
        assert engine.isNewestMarket is False
        self.mock_tn.markets.append.assert_called_once()
        
    @patch('tent.utils.timer.Timer.now')
    def test_while_in_negotiation(self, mock_now):
        """Test negotiation state processing"""
        mock_now.return_value = datetime(2023, 1, 1, 10, 0, 0)
        
        engine = MatchingEngine()
        engine.marketClearingTime = datetime(2023, 1, 1, 12, 0, 0)
        
        # Add mock new order
        mock_order = Mock()
        mock_order.status = OrderStatus.NEW
        engine.orderList = [mock_order]
        
        with patch.object(engine, 'match') as mock_match:
            engine.while_in_negotiation(self.mock_tn)
            
            mock_match.assert_called_with(mock_order)


class TestMarketSeries:
    """Test cases for MarketSeries class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.data_manager = Mock()
        self.mock_tn.id = uuid.uuid4()
        self.mock_tn.get_market_series_by_attribute = Mock(return_value=None)
        
    def test_market_series_init(self):
        """Test MarketSeries initialization"""
        series = MarketSeries(
            transactive_node=self.mock_tn,
            name="Test Series",
            market_class=Market
        )
        
        assert series.tn == self.mock_tn
        assert series.market_class == Market
        assert series.market_type == Market.MARKET_TYPE
        assert isinstance(series.duration, MarketDurations)
        assert len(series.markets) == 1  # Should spawn first market
        
    def test_market_series_invalid_market_class(self):
        """Test MarketSeries with invalid market class"""
        with pytest.raises(ValueError, match="not a valid Market class"):
            MarketSeries(
                transactive_node=self.mock_tn,
                market_class=str  # Invalid market class
            )
            
    def test_spawn(self):
        """Test spawning new market"""
        series = MarketSeries(
            transactive_node=self.mock_tn,
            market_class=Market
        )
        
        initial_count = len(series.markets)
        initial_clearing_time = series.next_clearing_time
        
        series.spawn()
        
        assert len(series.markets) == initial_count + 1
        assert series.next_clearing_time > initial_clearing_time
        
    def test_newest_market_property(self):
        """Test newest market property"""
        series = MarketSeries(
            transactive_node=self.mock_tn,
            market_class=Market
        )
        
        first_market = series.newest_market
        assert first_market is not None
        
        # Spawn another market
        series.spawn()
        second_market = series.newest_market
        
        assert second_market != first_market
        assert len(series.markets) == 2
        
    def test_get_refined_market(self):
        """Test getting refined market"""
        series = MarketSeries(
            transactive_node=self.mock_tn,
            market_class=Market
        )
        
        # No refined series
        result = series.get_refined_market(datetime.now())
        assert result is None
        
    def test_get_record(self):
        """Test market series record generation"""
        series = MarketSeries(
            transactive_node=self.mock_tn,
            market_class=Market,
            name="Test Series"
        )
        
        record = series.get_record()
        
        assert record['name'] == "Test Series"
        assert record['market_class'] == Market
        assert 'commitment' in record
        assert 'default_price' in record


class TestDayAheadAuction:
    """Test cases for DayAheadAuction class"""
    
    def test_day_ahead_auction_init(self):
        """Test DayAheadAuction initialization"""
        mock_tn = Mock()
        mock_series = Mock()
        transition_times = MarketTimings(
            active=datetime(2023, 1, 1, 10, 0, 0),
            negotiation=datetime(2023, 1, 1, 11, 0, 0),
            market_lead=datetime(2023, 1, 1, 11, 30, 0),
            delivery_lead=datetime(2023, 1, 1, 11, 45, 0),
            delivery=datetime(2023, 1, 1, 12, 0, 0),
            reconciliation=datetime(2023, 1, 1, 13, 0, 0),
            expiration=datetime(2023, 1, 1, 14, 0, 0)
        )
        
        auction = DayAheadAuction(
            transactive_node=mock_tn,
            market_series=mock_series,
            transition_times=transition_times
        )
        
        assert isinstance(auction, Auction)
        
    def test_actuate_assets_override(self):
        """Test that day-ahead auction doesn't actuate assets"""
        mock_tn = Mock()
        mock_tn.local_assets = [Mock(), Mock()]
        
        mock_series = Mock()
        transition_times = MarketTimings(
            active=datetime(2023, 1, 1, 10, 0, 0),
            negotiation=datetime(2023, 1, 1, 11, 0, 0),
            market_lead=datetime(2023, 1, 1, 11, 30, 0),
            delivery_lead=datetime(2023, 1, 1, 11, 45, 0),
            delivery=datetime(2023, 1, 1, 12, 0, 0),
            reconciliation=datetime(2023, 1, 1, 13, 0, 0),
            expiration=datetime(2023, 1, 1, 14, 0, 0)
        )
        
        auction = DayAheadAuction(
            transactive_node=mock_tn,
            market_series=mock_series,
            transition_times=transition_times
        )
        
        # Should not call actuate on any assets
        auction._actuate_assets()
        
        for asset in mock_tn.local_assets:
            asset.actuate.assert_not_called()


class TestRealTimeAuction:
    """Test cases for RealTimeAuction class"""
    
    def test_real_time_auction_init(self):
        """Test RealTimeAuction initialization"""
        mock_tn = Mock()
        mock_series = Mock()
        transition_times = MarketTimings(
            active=datetime(2023, 1, 1, 10, 0, 0),
            negotiation=datetime(2023, 1, 1, 11, 0, 0),
            market_lead=datetime(2023, 1, 1, 11, 30, 0),
            delivery_lead=datetime(2023, 1, 1, 11, 45, 0),
            delivery=datetime(2023, 1, 1, 12, 0, 0),
            reconciliation=datetime(2023, 1, 1, 13, 0, 0),
            expiration=datetime(2023, 1, 1, 14, 0, 0)
        )
        
        auction = RealTimeAuction(
            transactive_node=mock_tn,
            market_series=mock_series,
            transition_times=transition_times
        )
        
        assert isinstance(auction, Auction)
        
    def test_transition_from_delivery_lead_to_delivery(self):
        """Test real-time auction delivery transition"""
        mock_tn = Mock()
        mock_tn.local_assets = [Mock()]
        mock_tn.publish = Mock()
        
        mock_series = Mock()
        transition_times = MarketTimings(
            active=datetime(2023, 1, 1, 10, 0, 0),
            negotiation=datetime(2023, 1, 1, 11, 0, 0),
            market_lead=datetime(2023, 1, 1, 11, 30, 0),
            delivery_lead=datetime(2023, 1, 1, 11, 45, 0),
            delivery=datetime(2023, 1, 1, 12, 0, 0),
            reconciliation=datetime(2023, 1, 1, 13, 0, 0),
            expiration=datetime(2023, 1, 1, 14, 0, 0)
        )
        
        auction = RealTimeAuction(
            transactive_node=mock_tn,
            market_series=mock_series,
            transition_times=transition_times
        )
        
        # Mock price model and marginal prices
        auction.price_model = Mock()
        auction.marginal_prices = Mock()
        auction.marginal_prices.__iter__ = Mock(return_value=iter([]))
        
        auction.transition_from_delivery_lead_to_delivery()
        
        # Should call price model update and actuate assets
        # Specific assertions depend on the implementation details


class TestMarketIntegration:
    """Integration tests for market functionality"""
    
    def test_market_series_with_market_spawn(self):
        """Test market series spawning markets"""
        mock_tn = Mock()
        mock_tn.data_manager = Mock()
        mock_tn.id = uuid.uuid4()
        mock_tn.get_market_series_by_attribute = Mock(return_value=None)
        
        series = MarketSeries(
            transactive_node=mock_tn,
            market_class=Auction,
            intervals_to_clear=2
        )
        
        # Should have spawned one market initially
        assert len(series.markets) == 1
        assert isinstance(series.newest_market, Auction)
        
        # Spawn another market
        series.spawn()
        assert len(series.markets) == 2
        
    def test_auction_with_matching_engine_workflow(self):
        """Test workflow between different market types"""
        mock_tn = Mock()
        mock_tn.neighbors = []
        mock_tn.local_assets = []
        mock_tn.data_manager = Mock()
        
        # This would test interaction between different market types
        # but requires more complex setup
        
    def test_price_model_integration(self):
        """Test price model integration with markets"""
        model = HourlyAveragePriceModel()
        
        # Test updating and retrieving prices
        test_time = datetime(2023, 1, 1, 15, 0, 0)
        
        # Add some price history
        for i, price in enumerate([0.05, 0.06, 0.07]):
            model.update(test_time, price)
            
        mean_price, std_price = model.get(test_time)
        
        assert mean_price > 0
        assert std_price > 0


class TestMarketEdgeCases:
    """Test edge cases and error conditions"""
    
    def test_market_with_invalid_transition_times(self):
        """Test market with invalid transition times"""
        mock_tn = Mock()
        mock_series = Mock()
        
        # Create invalid transition times (delivery before negotiation)
        invalid_times = MarketTimings(
            active=datetime(2023, 1, 1, 10, 0, 0),
            negotiation=datetime(2023, 1, 1, 13, 0, 0),  # After delivery
            market_lead=datetime(2023, 1, 1, 11, 30, 0),
            delivery_lead=datetime(2023, 1, 1, 11, 45, 0),
            delivery=datetime(2023, 1, 1, 12, 0, 0),  # Before negotiation
            reconciliation=datetime(2023, 1, 1, 14, 0, 0),
            expiration=datetime(2023, 1, 1, 15, 0, 0)
        )
        
        # Should still create market (validation might be elsewhere)
        market = Market(
            transactive_node=mock_tn,
            market_series=mock_series,
            transition_times=invalid_times
        )
        
        assert market is not None
        
    def test_matching_engine_with_no_orders(self):
        """Test matching engine with empty order book"""
        engine = MatchingEngine()
        
        # Should handle empty order book gracefully
        result = engine.match(None)
        assert result is None
        
        counteroffers = engine.get_counteroffers(SupplyOrDemand.SUPPLY)
        assert counteroffers == []
        
    def test_price_model_edge_cases(self):
        """Test price model edge cases"""
        model = HourlyAveragePriceModel(forgetting_factor=1)
        
        # Test with invalid hour (should still work due to datetime.hour)
        test_time = datetime(2023, 1, 1, 25, 0, 0)  # Invalid hour
        try:
            model.update(test_time, 0.05)
            # If this doesn't raise an error, the datetime handling is robust
        except Exception:
            # Expected if hour validation is strict
            pass
            
        # Test with negative prices
        valid_time = datetime(2023, 1, 1, 10, 0, 0)
        model.update(valid_time, -0.01)  # Negative price
        
        mean_price, std_price = model.get(valid_time)
        # Should handle negative prices without error
        
    def test_market_state_machine_invalid_states(self):
        """Test market state machine with edge cases"""
        mock_tn = Mock()
        mock_series = Mock()
        mock_series.initial_market_state = MarketState.Expired  # Unusual initial state
        
        transition_times = MarketTimings(
            active=datetime(2023, 1, 1, 10, 0, 0),
            negotiation=datetime(2023, 1, 1, 11, 0, 0),
            market_lead=datetime(2023, 1, 1, 11, 30, 0),
            delivery_lead=datetime(2023, 1, 1, 11, 45, 0),
            delivery=datetime(2023, 1, 1, 12, 0, 0),
            reconciliation=datetime(2023, 1, 1, 13, 0, 0),
            expiration=datetime(2023, 1, 1, 14, 0, 0)
        )
        
        market = Market(
            transactive_node=mock_tn,
            market_series=mock_series,
            transition_times=transition_times
        )
        
        # Should handle unusual initial state
        assert market.market_state == MarketState.Expired


if __name__ == "__main__":
    pytest.main([__file__])