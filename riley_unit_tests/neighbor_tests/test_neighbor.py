import pytest
import json
import logging
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock, call
from weakref import WeakKeyDictionary
from copy import deepcopy

# Import neighbor classes
from tent.neighbor import Neighbor
from tent.neighbor.bulk_supplier_dc import BulkSupplierDC

# Import dependencies
from tent.containers import Measurement, SDCurve, TransactiveSignal, Vertex, MarketTimings
from tent.enumerations import Direction, MeasurementType, MeasurementUnit, MarketState
from tent.market import Market
from tent.utils.timer import Timer


class TestNeighbor:
    """Test cases for base Neighbor class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.register_subscription = Mock()
        self.mock_tn.data_manager = Mock()
        self.mock_tn.id = "test_tn_id"
        
        # Create a mock market
        self.mock_market = Mock()
        self.mock_market.prior_market_in_series = None
        self.mock_market.price_model = Mock()
        self.mock_market.price_model.get.return_value = (0.05, 0.01)  # mean, std
        self.mock_market.marginal_prices = Mock()
        self.mock_market.marginal_prices.get_by_interval.return_value = None
        
        # Create mock time intervals
        self.mock_interval = Mock()
        self.mock_interval.name = "TestInterval"
        self.mock_interval.start_time = datetime(2023, 1, 1, 12, 0, 0)
        self.mock_interval.duration = timedelta(hours=1)
        self.mock_interval.market = lambda: self.mock_market
        
        self.mock_market.time_intervals = [self.mock_interval]
        
    def test_neighbor_init_defaults(self):
        """Test Neighbor initialization with default values"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            name="TestNeighbor",
            # Provide empty lists instead of None to avoid iteration error
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        assert neighbor.tn == self.mock_tn
        assert neighbor.name == "TestNeighbor"
        assert neighbor.convergence_threshold == 0.05
        assert neighbor.cost_parameters == [0.0, 0.0, 0.0]
        assert neighbor.demand_month == datetime.today().month
        assert neighbor.demand_rate == 4.5
        assert neighbor.demand_threshold == 1e9
        assert neighbor.demand_threshold_coef == 1
        assert neighbor.description == ''
        assert neighbor.effective_impedance == 0.0
        assert neighbor.friend is False
        assert neighbor.location == ''
        assert neighbor.loss_factor == 0.01
        assert neighbor.maximum_power == 0.0
        assert neighbor.minimum_power == 0.0
        assert neighbor.transactive is False
        assert neighbor.up_or_down == Direction.unknown
        
    def test_neighbor_init_with_parameters(self):
        """Test Neighbor initialization with custom parameters"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            convergence_threshold=0.1,
            cost_parameters=[1.0, 2.0, 3.0],
            default_vertices=[],
            information_services=[],
            meter_points=[],
            demand_month=6,
            demand_rate=5.5,
            demand_threshold=1000.0,
            demand_threshold_coef=0.8,
            description="Test neighbor description",
            effective_impedance=0.5,
            friend=True,
            location="Test location",
            loss_factor=0.02,
            maximum_power=500.0,
            minimum_power=-200.0,
            name="CustomNeighbor",
            transactive=True,
            up_or_down=Direction.upstream
        )
        
        assert neighbor.convergence_threshold == 0.1
        assert neighbor.cost_parameters == [1.0, 2.0, 3.0]
        assert neighbor.demand_month == 6
        assert neighbor.demand_rate == 5.5
        assert neighbor.demand_threshold == 1000.0
        assert neighbor.demand_threshold_coef == 0.8
        assert neighbor.description == "Test neighbor description"
        assert neighbor.effective_impedance == 0.5
        assert neighbor.friend is True
        assert neighbor.location == "Test location"
        assert neighbor.loss_factor == 0.02
        assert neighbor.maximum_power == 500.0
        assert neighbor.minimum_power == -200.0
        assert neighbor.name == "CustomNeighbor"
        assert neighbor.transactive is True
        assert neighbor.up_or_down == Direction.upstream
        
    def test_neighbor_init_direction_as_int(self):
        """Test Neighbor initialization with Direction as integer"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            up_or_down=1,  # Direction.bidirectional
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        assert neighbor.up_or_down == Direction.bidirectional
        
    def test_neighbor_init_direction_as_string(self):
        """Test Neighbor initialization with Direction as string"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            up_or_down="downstream",
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        assert neighbor.up_or_down == Direction.downstream
        
    def test_neighbor_dynamic_attributes(self):
        """Test dynamically assigned attributes"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        assert neighbor.converged is False
        assert isinstance(neighbor.convergence_flags, Measurement)
        assert neighbor.convergence_flags.measurement_type == MeasurementType.ConvergenceFlag
        assert neighbor.convergence_flags.measurement_unit == MeasurementUnit.Boolean
        assert isinstance(neighbor.prepared_signals, WeakKeyDictionary)
        assert isinstance(neighbor.last_received_signals, WeakKeyDictionary)
        assert isinstance(neighbor.last_sent_signals, WeakKeyDictionary)
        
    def test_neighbor_subscription_registration(self):
        """Test that neighbor registers for transactive signal subscription"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            name="TestNeighbor",
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        self.mock_tn.register_subscription.assert_called_once_with(
            subscribe_key='TestNeighbor_incoming_transactive_signal',
            callback=neighbor.receive_transactive_signal
        )
        
    def test_check_for_convergence_no_sent_signal(self):
        """Test convergence check when no signal has been sent"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Set up mock signals
        neighbor.last_sent_signals[self.mock_market] = Mock()
        neighbor.last_sent_signals[self.mock_market].signal_time = None
        neighbor.last_sent_signals[self.mock_market].get_by_interval.return_value = None
        
        neighbor.last_received_signals[self.mock_market] = Mock()
        neighbor.last_received_signals[self.mock_market].signal_time = datetime.now()
        neighbor.last_received_signals[self.mock_market].get_by_interval.return_value = []
        
        neighbor.prepared_signals[self.mock_market] = Mock()
        neighbor.prepared_signals[self.mock_market].get_by_interval.return_value = []
        
        neighbor.check_for_convergence(self.mock_market)
        
        # Should not be converged when no signal has been sent
        assert neighbor.converged is False
        
    def test_check_for_convergence_different_signals(self):
        """Test convergence check when sent and received signals differ"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Mock sent signal
        sent_signal = [Mock()]
        neighbor.last_sent_signals[self.mock_market] = Mock()
        neighbor.last_sent_signals[self.mock_market].signal_time = datetime(2023, 1, 1, 11, 0, 0)
        neighbor.last_sent_signals[self.mock_market].get_by_interval.return_value = sent_signal
        
        # Mock received signal (different from sent)
        received_signal = [Mock()]
        neighbor.last_received_signals[self.mock_market] = Mock()
        neighbor.last_received_signals[self.mock_market].signal_time = datetime(2023, 1, 1, 12, 0, 0)
        neighbor.last_received_signals[self.mock_market].get_by_interval.return_value = received_signal
        
        neighbor.prepared_signals[self.mock_market] = Mock()
        neighbor.prepared_signals[self.mock_market].get_by_interval.return_value = []
        
        with patch('tent.containers.transactive_signal.TransactiveSignal.balance_points_differ', return_value=True):
            neighbor.check_for_convergence(self.mock_market)
            
        # Should not be converged when signals differ significantly
        assert neighbor.converged is False
        
    def test_check_for_convergence_conditions_changed(self):
        """Test convergence check when local conditions have changed"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Mock signals with same content
        signal_content = [Mock()]
        neighbor.last_sent_signals[self.mock_market] = Mock()
        neighbor.last_sent_signals[self.mock_market].signal_time = datetime(2023, 1, 1, 11, 0, 0)
        neighbor.last_sent_signals[self.mock_market].get_by_interval.return_value = signal_content
        
        neighbor.last_received_signals[self.mock_market] = Mock()
        neighbor.last_received_signals[self.mock_market].signal_time = datetime(2023, 1, 1, 10, 0, 0)
        neighbor.last_received_signals[self.mock_market].get_by_interval.return_value = signal_content
        
        neighbor.prepared_signals[self.mock_market] = Mock()
        neighbor.prepared_signals[self.mock_market].get_by_interval.return_value = []
        
        with patch('tent.containers.transactive_signal.TransactiveSignal.balance_points_differ', return_value=False):
            with patch('tent.containers.transactive_signal.TransactiveSignal.conditions_have_changed', return_value=True):
                neighbor.check_for_convergence(self.mock_market)
                
        # Should not be converged when conditions have changed
        assert neighbor.converged is False
        
    def test_check_for_convergence_converged(self):
        """Test convergence check when neighbor is converged"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Mock signals that indicate convergence
        signal_content = [Mock()]
        neighbor.last_sent_signals[self.mock_market] = Mock()
        neighbor.last_sent_signals[self.mock_market].signal_time = datetime(2023, 1, 1, 12, 0, 0)
        neighbor.last_sent_signals[self.mock_market].get_by_interval.return_value = signal_content
        
        neighbor.last_received_signals[self.mock_market] = Mock()
        neighbor.last_received_signals[self.mock_market].signal_time = datetime(2023, 1, 1, 11, 0, 0)
        neighbor.last_received_signals[self.mock_market].get_by_interval.return_value = signal_content
        
        neighbor.prepared_signals[self.mock_market] = Mock()
        neighbor.prepared_signals[self.mock_market].get_by_interval.return_value = []
        
        with patch('tent.containers.transactive_signal.TransactiveSignal.balance_points_differ', return_value=False):
            with patch('tent.containers.transactive_signal.TransactiveSignal.conditions_have_changed', return_value=False):
                neighbor.check_for_convergence(self.mock_market)
                
        # Should be converged when all conditions are met
        assert neighbor.converged is True
        
    def test_schedule(self):
        """Test schedule method calls correct sub-methods"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        with patch.object(neighbor, 'update_dc_threshold') as mock_update_dc:
            with patch.object(neighbor, 'update_vertices') as mock_update_vertices:
                with patch.object(neighbor, 'schedule_power') as mock_schedule_power:
                    with patch.object(neighbor, 'calculate_reserve_margin') as mock_calc_reserve:
                        neighbor.schedule(self.mock_market)
                        
                        mock_update_dc.assert_called_once_with(self.mock_market)
                        mock_update_vertices.assert_called_once_with(self.mock_market)
                        mock_schedule_power.assert_called_once_with(self.mock_market)
                        mock_calc_reserve.assert_called_once_with(self.mock_market)
                        
    def test_schedule_power(self):
        """Test schedule_power method"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Mock production method
        with patch.object(neighbor, 'production', return_value=100.0) as mock_production:
            neighbor.schedule_power(self.mock_market)
            
            mock_production.assert_called_once_with(0.05, self.mock_interval)  # marginal_price, interval
            
    def test_update_dc_threshold_with_meter(self):
        """Test DC threshold update with meter point"""
        # Create mock meter point
        mock_meter = Mock()
        mock_meter.measurement_type = MeasurementType.AverageDemandkW
        mock_meter.current_measurement = 150.0
        
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            meter_points=[mock_meter],
            demand_threshold=100.0,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[]
        )
        
        neighbor.update_dc_threshold(self.mock_market)
        
        # Threshold should be updated to max of current threshold and meter reading
        assert neighbor.demand_threshold == 150.0
        
    def test_update_dc_threshold_without_meter(self):
        """Test DC threshold update without meter point"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            meter_points=[],
            demand_threshold=100.0,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[]
        )
        
        # Mock prior market with scheduled powers
        mock_prior_market = Mock()
        mock_prior_interval = Mock()
        mock_prior_interval.market = lambda: mock_prior_market
        
        neighbor.scheduled_powers = Mock()
        neighbor.scheduled_powers.all = [(mock_prior_interval, Mock(value=200.0))]
        
        self.mock_market.prior_market_in_series = lambda: mock_prior_market
        
        neighbor.update_dc_threshold(self.mock_market)
        
        # Threshold should be updated based on prior market powers
        assert neighbor.demand_threshold == 200.0
        
    @patch.object(Timer, 'now')
    def test_update_dc_threshold_new_month(self, mock_now):
        """Test DC threshold reset in new month"""
        mock_now.return_value = datetime(2023, 2, 1, 12, 0, 0)  # February
        
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            demand_month=1,  # January
            demand_threshold=1000.0,
            demand_threshold_coef=0.8,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        neighbor.update_dc_threshold(self.mock_market)
        
        # Threshold should be reset for new month
        assert neighbor.demand_threshold == 800.0  # 1000.0 * 0.8
        assert neighbor.demand_month == 2
        
    def test_update_vertices_non_transactive(self):
        """Test update_vertices for non-transactive neighbor"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            transactive=False,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Mock default vertices
        neighbor.default_vertices = [Mock()]
        neighbor.active_vertices = Mock()
        
        neighbor.update_vertices(self.mock_market)
        
        neighbor.active_vertices.update.assert_called_once_with(
            self.mock_interval, neighbor.default_vertices, archive=False, replace=True
        )
        neighbor.active_vertices.archive.assert_called_once()
        
    def test_update_vertices_transactive_with_signal(self):
        """Test update_vertices for transactive neighbor with received signal"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            transactive=True,
            loss_factor=0.0,
            demand_rate=0.0,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Mock received signal
        mock_signal = Mock()
        mock_sd_curve = Mock()
        mock_sd_curve.check_rationality.return_value = (mock_sd_curve, None)
        mock_signal.get_by_interval.return_value = mock_sd_curve
        
        neighbor.last_received_signals[self.mock_market] = mock_signal
        neighbor.active_vertices = Mock()
        neighbor.active_vertices.get_by_interval.return_value = mock_sd_curve
        
        neighbor.update_vertices(self.mock_market)
        
        neighbor.active_vertices.update.assert_called_once()
        neighbor.active_vertices.archive.assert_called_once()
        
    def test_update_vertices_transactive_no_signal(self):
        """Test update_vertices for transactive neighbor without signal"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            transactive=True,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        neighbor.default_vertices = [Mock()]
        neighbor.active_vertices = Mock()
        neighbor.last_received_signals = {}
        
        with patch('tent.neighbor._log') as mock_log:
            neighbor.update_vertices(self.mock_market)
            
            mock_log.warning.assert_called_once()
            neighbor.active_vertices.update.assert_called_once_with(
                self.mock_interval, neighbor.default_vertices, archive=False, replace=True
            )
            
    def test_prep_transactive_signal_non_transactive(self):
        """Test prep_transactive_signal for non-transactive neighbor"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            transactive=False,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        result = neighbor.prep_transactive_signal(self.mock_market)
        
        # Should return early for non-transactive neighbors
        assert result is None
        
    def test_prep_transactive_signal_transactive(self):
        """Test prep_transactive_signal for transactive neighbor"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            transactive=True,
            demand_rate=0.0,
            loss_factor=0.0,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Mock market sum_vertices
        mock_sd_curve = Mock()
        self.mock_market.sum_vertices.return_value = mock_sd_curve
        
        # Mock scheduled powers
        neighbor.scheduled_powers = Mock()
        neighbor.scheduled_powers.get_by_interval.return_value = 100.0
        
        neighbor.prep_transactive_signal(self.mock_market)
        
        # Should create prepared signal
        assert self.mock_market in neighbor.prepared_signals
        
    def test_send_transactive_signal_non_transactive(self):
        """Test send_transactive_signal for non-transactive neighbor"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            transactive=False,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        result = neighbor.send_transactive_signal(self.mock_market)
        
        # Should return early for non-transactive neighbors
        assert result is None
        
    def test_send_transactive_signal_no_prepared_signal(self):
        """Test send_transactive_signal with no prepared signal"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            transactive=True,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        neighbor.prepared_signals = {}
        
        with patch('tent.neighbor._log') as mock_log:
            result = neighbor.send_transactive_signal(self.mock_market)
            
            mock_log.warning.assert_called_once()
            assert result is None
            
    def test_send_transactive_signal_success(self):
        """Test successful transactive signal sending"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            transactive=True,
            name="TestNeighbor",
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Mock prepared signal
        mock_signal = Mock()
        mock_signal.empty = False
        mock_signal.generate_signal.return_value = {"test": "signal"}
        neighbor.prepared_signals[self.mock_market] = mock_signal
        
        # Mock TN issue_signal method
        self.mock_tn.issue_signal = Mock()
        
        result = neighbor.send_transactive_signal(self.mock_market)
        
        self.mock_tn.issue_signal.assert_called_once_with("TestNeighbor", {"test": "signal"})
        assert neighbor.last_sent_signals[self.mock_market] == mock_signal
        assert result == mock_signal
        
    def test_receive_transactive_signal_non_transactive(self):
        """Test receive_transactive_signal for non-transactive neighbor"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            transactive=False,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        with patch('tent.neighbor._log') as mock_log:
            neighbor.receive_transactive_signal('{"test": "signal"}')
            
            mock_log.warning.assert_called_once()
            
    def test_receive_transactive_signal_success(self):
        """Test successful transactive signal reception"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            transactive=True,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        mock_parsed_signal = Mock()
        mock_parsed_signal.market = self.mock_market
        
        test_signal = '{"test": "signal"}'
        
        with patch('tent.containers.transactive_signal.TransactiveSignal.parse_signal', return_value=mock_parsed_signal):
            neighbor.receive_transactive_signal(test_signal)
            
            assert neighbor.last_received_signals[self.mock_market] == mock_parsed_signal
            
    def test_include_marginal_losses(self):
        """Test include_marginal_losses method"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            maximum_power=1000.0,
            loss_factor=0.02,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Create test SD curve with supply vertex
        vertex = Vertex(price=50.0, quantity=500.0, cost=0.0)
        sd_curve = SDCurve(vertices=[vertex])
        
        neighbor.include_marginal_losses(sd_curve)
        
        # Vertex should be modified for losses
        assert vertex.quantity < 500.0  # Reduced due to losses
        assert vertex.price > 50.0     # Increased due to loss impact
        
    def test_include_marginal_losses_invalid_conditions(self):
        """Test include_marginal_losses with invalid conditions"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            maximum_power=0.0,  # Invalid
            loss_factor=0.02,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        vertex = Vertex(price=50.0, quantity=500.0, cost=0.0)
        sd_curve = SDCurve(vertices=[vertex])
        
        with patch('builtins.print') as mock_print:
            neighbor.include_marginal_losses(sd_curve)
            
            mock_print.assert_called_once()
            
    def test_remove_marginal_losses(self):
        """Test remove_marginal_losses method"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            maximum_power=1000.0,
            loss_factor=0.02,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Create test SD curve with demand vertex (negative quantity)
        vertex = Vertex(price=55.0, quantity=-400.0, cost=0.0)
        sd_curve = SDCurve(vertices=[vertex])
        
        neighbor.remove_marginal_losses(sd_curve)
        
        # Vertex should be modified to remove loss effects
        assert vertex.quantity < -400.0  # More negative (supply perspective)
        assert vertex.price < 55.0      # Reduced price
        
    def test_include_demand_charges(self):
        """Test include_demand_charges method"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            demand_rate=5.0,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Create test SD curve with vertices above and below threshold
        vertices = [
            Vertex(price=40.0, quantity=300.0, cost=0.0),  # Below threshold
            Vertex(price=50.0, quantity=600.0, cost=0.0),  # Above threshold
        ]
        sd_curve = SDCurve(vertices=vertices)
        
        threshold = 500.0
        neighbor.include_demand_charges(sd_curve, threshold)
        
        # Vertices above threshold should have demand rate added
        assert any(v.price == 55.0 for v in sd_curve.vertices)  # 50.0 + 5.0
        
    def test_remove_demand_charges(self):
        """Test remove_demand_charges method"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            demand_rate=5.0,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Create test SD curve with demand vertices
        vertices = [
            Vertex(price=45.0, quantity=-600.0, cost=0.0),  # Below threshold
            Vertex(price=50.0, quantity=-300.0, cost=0.0),  # Above threshold
        ]
        sd_curve = SDCurve(vertices=vertices)
        
        threshold = -500.0  # Negative threshold for demand
        neighbor.remove_demand_charges(sd_curve, threshold)
        
        # Vertices below threshold should have demand rate removed
        assert any(v.price == 40.0 for v in sd_curve.vertices)  # 45.0 - 5.0
        
    def test_update_costs(self):
        """Test update_costs method"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        with patch.object(neighbor, 'update_production_costs') as mock_prod_costs:
            with patch.object(neighbor, 'update_dual_costs') as mock_dual_costs:
                neighbor.production_costs = Mock()
                neighbor.production_costs.total.return_value = 1000.0
                neighbor.dual_costs = Mock()
                neighbor.dual_costs.total.return_value = 500.0
                
                neighbor.update_costs(self.mock_market)
                
                mock_prod_costs.assert_called_once_with(self.mock_market)
                mock_dual_costs.assert_called_once_with(self.mock_market)
                assert neighbor.total_production_cost == 1000.0
                assert neighbor.total_dual_cost == 500.0
                
    def test_table_name_property(self):
        """Test table_name property"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        assert neighbor.table_name == 'neighbor'
        
    def test_get_record(self):
        """Test get_record method"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            name="TestNeighbor",
            convergence_threshold=0.1,
            transactive=True,
            up_or_down=Direction.upstream,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        record = neighbor.get_record()
        
        assert 'id' in record
        assert record['convergence_threshold'] == 0.1
        assert record['transactive'] is True
        assert record['up_or_down'] == Direction.upstream
        
    def test_get_archive_definitions(self):
        """Test get_archive_definitions class method"""
        definitions = Neighbor.get_archive_definitions()
        
        assert isinstance(definitions, list)
        
        # Find the neighbor definition
        neighbor_def = None
        for definition in definitions:
            if definition['name'] == 'neighbor':
                neighbor_def = definition
                break
                
        assert neighbor_def is not None
        assert neighbor_def['inherits'] == 'entity'
        
        # Check for required columns
        column_names = [col['name'] for col in neighbor_def['columns']]
        assert 'convergence_threshold' in column_names
        assert 'transactive' in column_names
        assert 'up_or_down' in column_names


class TestBulkSupplierDC:
    """Test cases for BulkSupplierDC class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.register_subscription = Mock()
        self.mock_tn.data_manager = Mock()
        
        # Create mock market and interval
        self.mock_market = Mock()
        self.mock_interval = Mock()
        self.mock_interval.name = "TestInterval"
        self.mock_interval.start_time = datetime(2023, 6, 15, 14, 0, 0)  # June, HLH hour
        self.mock_interval.duration = timedelta(hours=1)
        self.mock_interval.market = lambda: self.mock_market
        
        self.mock_market.time_intervals = [self.mock_interval]
        
    def test_bulk_supplier_dc_init(self):
        """Test BulkSupplierDC initialization"""
        supplier = BulkSupplierDC(
            transactive_node=self.mock_tn,
            name="BulkSupplier",
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        assert isinstance(supplier, Neighbor)
        assert supplier.transactive is False  # Should be set to False
        assert supplier.name == "BulkSupplier"
        
    @patch('tent.neighbor.bulk_supplier_dc.is_heavyloadhour')
    @patch('tent.neighbor.bulk_supplier_dc.bpa_energy_rate')
    @patch('tent.neighbor.bulk_supplier_dc.bpa_demand_rate')
    def test_update_vertices_hlh_with_demand_charges(self, mock_demand_rate, mock_energy_rate, mock_is_hlh):
        """Test update_vertices for HLH hours with demand charges"""
        mock_is_hlh.return_value = True
        mock_energy_rate.__getitem__.return_value = [0.06, 0.04]  # [HLH, LLH]
        mock_demand_rate.__getitem__.return_value = [8.0]  # Demand rate
        
        supplier = BulkSupplierDC(
            transactive_node=self.mock_tn,
            maximum_power=1000.0,
            minimum_power=0.0,
            cost_parameters=[100.0, 0.0, 0.0],
            demand_threshold=500.0,
            loss_factor=0.0,  # Disable losses for simpler test
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        supplier.active_vertices = Mock()
        
        supplier.update_vertices(self.mock_market)
        
        # Should update active vertices and set demand rate
        supplier.active_vertices.update.assert_called_once()
        supplier.active_vertices.archive.assert_called_once()
        assert supplier.demand_rate == 8.0
        
    @patch('tent.neighbor.bulk_supplier_dc.is_heavyloadhour')
    @patch('tent.neighbor.bulk_supplier_dc.bpa_energy_rate')
    @patch('tent.neighbor.bulk_supplier_dc.bpa_demand_rate')
    def test_update_vertices_llh_no_demand_charges(self, mock_demand_rate, mock_energy_rate, mock_is_hlh):
        """Test update_vertices for LLH hours without demand charges"""
        mock_is_hlh.return_value = False
        mock_energy_rate.__getitem__.return_value = [0.06, 0.04]  # [HLH, LLH]
        mock_demand_rate.__getitem__.return_value = [8.0]
        
        supplier = BulkSupplierDC(
            transactive_node=self.mock_tn,
            maximum_power=1000.0,
            minimum_power=0.0,
            cost_parameters=[100.0, 0.0, 0.0],
            loss_factor=0.0,
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        supplier.active_vertices = Mock()
        
        with patch.object(supplier, 'include_demand_charges') as mock_include_dc:
            supplier.update_vertices(self.mock_market)
            
            # Demand charges should not be applied for LLH hours
            mock_include_dc.assert_not_called()
            
    @patch('tent.neighbor.bulk_supplier_dc.is_heavyloadhour')
    @patch('tent.neighbor.bulk_supplier_dc.bpa_energy_rate')
    def test_update_vertices_with_losses(self, mock_energy_rate, mock_is_hlh):
        """Test update_vertices with marginal losses enabled"""
        mock_is_hlh.return_value = True
        mock_energy_rate.__getitem__.return_value = [0.06, 0.04]
        
        supplier = BulkSupplierDC(
            transactive_node=self.mock_tn,
            maximum_power=1000.0,
            minimum_power=0.0,
            loss_factor=0.02,  # Enable losses
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        supplier.active_vertices = Mock()
        
        with patch.object(supplier, 'include_marginal_losses') as mock_include_losses:
            supplier.update_vertices(self.mock_market)
            
            mock_include_losses.assert_called_once()
            
    @patch('tent.neighbor.bulk_supplier_dc.is_heavyloadhour')
    def test_update_vertices_negative_power_warning(self, mock_is_hlh):
        """Test update_vertices with negative maximum power (should warn)"""
        mock_is_hlh.return_value = True
        
        supplier = BulkSupplierDC(
            transactive_node=self.mock_tn,
            maximum_power=-500.0,  # Negative power
            minimum_power=-100.0,
            name="TestSupplier",
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        with patch('tent.neighbor.bulk_supplier_dc._log') as mock_log:
            supplier.update_vertices(self.mock_market)
            
            mock_log.warning.assert_called_once()
            
    @patch('tent.neighbor.bulk_supplier_dc.is_heavyloadhour')
    @patch('tent.neighbor.bulk_supplier_dc.bpa_energy_rate')
    def test_update_vertices_month_based_rates(self, mock_energy_rate, mock_is_hlh):
        """Test that update_vertices uses month-based rates"""
        mock_is_hlh.return_value = True
        mock_energy_rate.__getitem__.return_value = [0.06, 0.04]
        
        # Set interval to June (month 6)
        self.mock_interval.start_time = datetime(2023, 6, 15, 14, 0, 0)
        
        supplier = BulkSupplierDC(
            transactive_node=self.mock_tn,
            maximum_power=1000.0,
            minimum_power=0.0,
            loss_factor=0.0,
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        supplier.active_vertices = Mock()
        
        supplier.update_vertices(self.mock_market)
        
        # Should access rate for month 6 (index 5)
        mock_energy_rate.__getitem__.assert_called_with(5)
        
    def test_get_new_threshold_integration(self):
        """Test _get_new_threshold method integration"""
        supplier = BulkSupplierDC(
            transactive_node=self.mock_tn,
            demand_threshold=500.0,
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Mock SD curve
        mock_sd_curve = Mock()
        mock_sd_curve.power_from_vertices.return_value = 600.0
        
        # Mock market price model
        self.mock_market.price_model.get.return_value = (0.05, 0.01)
        
        # Mock no scheduled power
        supplier.scheduled_powers = Mock()
        supplier.scheduled_powers.get_by_interval.return_value = None
        
        new_threshold = supplier._get_new_threshold(
            mock_sd_curve, 500.0, self.mock_market, self.mock_interval
        )
        
        # Should return max of current threshold and predicted power
        assert new_threshold == 600.0


class TestNeighborIntegration:
    """Integration tests for neighbor functionality"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.register_subscription = Mock()
        self.mock_tn.data_manager = Mock()
        self.mock_tn.issue_signal = Mock()
        
    def test_transactive_neighbor_signal_workflow(self):
        """Test complete transactive signal workflow"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            name="TransactiveNeighbor",
            transactive=True,
            up_or_down=Direction.upstream,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Create mock market
        mock_market = Mock()
        mock_interval = Mock()
        mock_interval.name = "TestInterval"
        mock_interval.start_time = datetime(2023, 1, 1, 12, 0, 0)
        mock_market.time_intervals = [mock_interval]
        mock_market.sum_vertices.return_value = Mock()
        
        # Test signal preparation
        neighbor.prep_transactive_signal(mock_market)
        assert mock_market in neighbor.prepared_signals
        
        # Mock prepared signal for sending
        mock_signal = Mock()
        mock_signal.empty = False
        mock_signal.generate_signal.return_value = {"test": "signal"}
        neighbor.prepared_signals[mock_market] = mock_signal
        
        # Test signal sending
        result = neighbor.send_transactive_signal(mock_market)
        assert result == mock_signal
        assert neighbor.last_sent_signals[mock_market] == mock_signal
        
        # Test signal reception
        test_signal_json = '{"test": "received_signal"}'
        mock_parsed_signal = Mock()
        mock_parsed_signal.market = mock_market
        
        with patch('tent.containers.transactive_signal.TransactiveSignal.parse_signal', 
                  return_value=mock_parsed_signal):
            neighbor.receive_transactive_signal(test_signal_json)
            assert neighbor.last_received_signals[mock_market] == mock_parsed_signal
            
    def test_non_transactive_neighbor_behavior(self):
        """Test non-transactive neighbor behavior"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            name="NonTransactiveNeighbor",
            transactive=False,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        mock_market = Mock()
        
        # Non-transactive neighbors should not prepare or send signals
        neighbor.prep_transactive_signal(mock_market)
        assert mock_market not in neighbor.prepared_signals
        
        result = neighbor.send_transactive_signal(mock_market)
        assert result is None
        
    def test_demand_charge_threshold_progression(self):
        """Test demand charge threshold progression through intervals"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            demand_threshold=100.0,
            demand_rate=5.0,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Create multiple intervals
        intervals = []
        for i in range(3):
            interval = Mock()
            interval.name = f"Interval{i}"
            interval.start_time = datetime(2023, 1, 1, 12 + i, 0, 0)
            intervals.append(interval)
            
        mock_market = Mock()
        mock_market.time_intervals = intervals
        
        # Mock scheduled powers that increase threshold
        neighbor.scheduled_powers = Mock()
        powers = [150.0, 200.0, 250.0]  # Increasing powers
        neighbor.scheduled_powers.get_by_interval.side_effect = powers
        
        # Mock active vertices
        neighbor.active_vertices = Mock()
        neighbor.active_vertices.get_by_interval.return_value = Mock()
        
        neighbor.last_received_signals = {}
        
        with patch.object(neighbor, 'include_demand_charges') as mock_include_dc:
            neighbor.update_vertices(mock_market)
            
            # Should include demand charges for each interval
            assert mock_include_dc.call_count == 3


class TestNeighborEdgeCases:
    """Test edge cases and error conditions"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.register_subscription = Mock()
        self.mock_tn.data_manager = Mock()
        
    def test_neighbor_with_invalid_direction(self):
        """Test neighbor with invalid direction value"""
        with pytest.raises((ValueError, KeyError)):
            Neighbor(
                transactive_node=self.mock_tn,
                up_or_down="invalid_direction",
                cost_parameters=[0.0, 0.0, 0.0],
                default_vertices=[],
                information_services=[],
                meter_points=[]
            )
            
    def test_neighbor_with_none_cost_parameters(self):
        """Test neighbor with None cost parameters"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            cost_parameters=None,
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        assert neighbor.cost_parameters == [0.0, 0.0, 0.0]
        
    def test_include_marginal_losses_edge_cases(self):
        """Test include_marginal_losses with edge cases"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            maximum_power=1000.0,
            loss_factor=0.02,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Test with export power (negative quantity)
        vertex = Vertex(price=50.0, quantity=-500.0, cost=0.0)
        sd_curve = SDCurve(vertices=[vertex])
        
        original_price = vertex.price
        original_quantity = vertex.quantity
        
        neighbor.include_marginal_losses(sd_curve)
        
        # Export power should not be modified
        assert vertex.price == original_price
        assert vertex.quantity == original_quantity
        
    def test_include_marginal_losses_invalid_inputs(self):
        """Test include_marginal_losses with invalid inputs"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            maximum_power=None,  # Invalid
            loss_factor=0.02,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        with patch('builtins.print') as mock_print:
            neighbor.include_marginal_losses("not_an_sd_curve")
            mock_print.assert_called()
            
    def test_demand_charges_with_extreme_threshold(self):
        """Test demand charges with extreme threshold values"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            demand_rate=5.0,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        # Create SD curve
        vertices = [Vertex(price=50.0, quantity=500.0, cost=0.0)]
        sd_curve = SDCurve(vertices=vertices)
        
        # Test with threshold above all vertices
        neighbor.include_demand_charges(sd_curve, threshold=1000.0)
        
        # Should return without modification
        assert vertices[0].price == 50.0
        
    def test_convergence_check_with_missing_signals(self):
        """Test convergence check with missing signal components"""
        neighbor = Neighbor(
            transactive_node=self.mock_tn,
            cost_parameters=[0.0, 0.0, 0.0],
            default_vertices=[],
            information_services=[],
            meter_points=[]
        )
        
        mock_market = Mock()
        mock_interval = Mock()
        mock_interval.name = "TestInterval"
        mock_market.time_intervals = [mock_interval]
        
        # Initialize empty signal dictionaries
        neighbor.last_sent_signals[mock_market] = Mock()
        neighbor.last_sent_signals[mock_market].signal_time = None
        neighbor.last_sent_signals[mock_market].get_by_interval.return_value = None
        
        neighbor.last_received_signals[mock_market] = Mock()
        neighbor.last_received_signals[mock_market].signal_time = None
        neighbor.last_received_signals[mock_market].get_by_interval.return_value = []
        
        neighbor.prepared_signals[mock_market] = Mock()
        neighbor.prepared_signals[mock_market].get_by_interval.return_value = []
        
        # Should handle missing signals gracefully
        neighbor.check_for_convergence(mock_market)
        assert neighbor.converged is False


if __name__ == "__main__":
    pytest.main([__file__])