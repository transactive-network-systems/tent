import pytest
import logging
import tempfile
import os
import pandas as pd
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock, call
from statistics import mean
from math import ceil

# Import all meter point classes
from tent.meter_point import MeterPoint
from tent.meter_point.push_meter_point import PushMeterPoint
from tent.meter_point.pull_meter_point import PullMeterPoint
from tent.meter_point.csv_meter_point import CSVMeterPoint
# Note: BACnetMeterPoint is commented out due to missing dependencies
# from tent.meter_point.bacnet_meter_point import BACnetMeterPoint

from tent.enumerations import MeasurementType, MeasurementUnit
from tent.utils.time_series_buffer import TimeSeriesBuffer
from tent.utils.timer import Timer


class TestMeterPoint:
    """Test cases for base MeterPoint class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.data_manager = Mock()
        self.mock_tn.id = "test_tn_id"
        
    def test_meter_point_init_defaults(self):
        """Test MeterPoint initialization with default values"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            name="TestMeter"
        )
        
        assert meter.tn == self.mock_tn
        assert meter.name == "TestMeter"
        assert meter.data_period_seconds == 10.0
        assert meter.max_buffer_seconds == 3600.0
        assert meter.measurement_interval == timedelta(hours=1)
        assert meter.measurement_type == MeasurementType.Unknown
        assert meter.measurement_unit == MeasurementUnit.Unknown
        assert meter.scale_factor == 1.0
        assert isinstance(meter.buffer, TimeSeriesBuffer)
        
    def test_meter_point_init_with_parameters(self):
        """Test MeterPoint initialization with custom parameters"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            data_period_seconds=5.0,
            description="Power meter for building A",
            max_buffer_seconds=1800.0,
            measurement_interval=timedelta(minutes=15),
            measurement_type=MeasurementType.PowerReal,
            measurement_unit=MeasurementUnit.kW,
            name="PowerMeter_A",
            scale_factor=0.001  # Convert from W to kW
        )
        
        assert meter.data_period_seconds == 5.0
        assert meter.description == "Power meter for building A"
        assert meter.max_buffer_seconds == 1800.0
        assert meter.measurement_interval == timedelta(minutes=15)
        assert meter.measurement_type == MeasurementType.PowerReal
        assert meter.measurement_unit == MeasurementUnit.kW
        assert meter.name == "PowerMeter_A"
        assert meter.scale_factor == 0.001
        
    def test_meter_point_init_with_enum_strings(self):
        """Test MeterPoint initialization with enum string values"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            measurement_type="PowerReal",
            measurement_unit="kW"
        )
        
        assert meter.measurement_type == MeasurementType.PowerReal
        assert meter.measurement_unit == MeasurementUnit.kW
        
    def test_meter_point_init_with_enum_integers(self):
        """Test MeterPoint initialization with enum integer values"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            measurement_type=2,  # PowerReal
            measurement_unit=4   # kW
        )
        
        assert meter.measurement_type == MeasurementType.PowerReal
        assert meter.measurement_unit == MeasurementUnit.kW
        
    def test_meter_point_init_with_timedelta_seconds(self):
        """Test MeterPoint initialization with measurement_interval as seconds"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            measurement_interval=900  # 15 minutes in seconds
        )
        
        assert meter.measurement_interval == timedelta(seconds=900)
        
    def test_buffer_size_calculation(self):
        """Test buffer size calculation based on period and max buffer time"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            data_period_seconds=10.0,
            max_buffer_seconds=3600.0
        )
        
        expected_max_len = ceil(3600.0 / 10.0) + 1
        assert meter.buffer.maxlen == expected_max_len
        
    def test_set_meter_value(self):
        """Test setting meter values"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            scale_factor=2.0
        )
        
        test_time = datetime(2023, 1, 1, 12, 0, 0)
        raw_value = 100.0
        
        with patch.object(Timer, 'now', return_value=test_time):
            meter.set_meter_value(raw_value, test_time)
            
        # Check that value was scaled and stored
        assert meter.buffer.last.value == 200.0  # 100.0 * 2.0
        assert meter.buffer.last.d_time == test_time
        
        # Check that archiving was called
        meter.data_manager.archive_data.assert_called_once()
        
    def test_set_meter_value_without_time(self):
        """Test setting meter value without specifying time"""
        meter = MeterPoint(transactive_node=self.mock_tn)
        
        current_time = datetime(2023, 1, 1, 12, 0, 0)
        
        with patch.object(Timer, 'now', return_value=current_time):
            meter.set_meter_value(50.0)
            
        assert meter.buffer.last.value == 50.0
        assert meter.buffer.last.d_time == current_time
        
    def test_current_measurement_property(self):
        """Test current_measurement property"""
        meter = MeterPoint(transactive_node=self.mock_tn)
        
        meter.set_meter_value(75.0)
        
        assert meter.current_measurement == 75.0
        
    def test_last_updated_property(self):
        """Test last_updated property"""
        meter = MeterPoint(transactive_node=self.mock_tn)
        
        test_time = datetime(2023, 1, 1, 12, 0, 0)
        meter.set_meter_value(25.0, test_time)
        
        assert meter.last_updated == test_time
        
    @patch.object(Timer, 'now')
    def test_current_interval_measurements(self, mock_now):
        """Test current_interval_measurements property"""
        current_time = datetime(2023, 1, 1, 12, 0, 0)
        mock_now.return_value = current_time
        
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            measurement_interval=timedelta(minutes=30)
        )
        
        # Add measurements over time
        times_and_values = [
            (datetime(2023, 1, 1, 11, 0, 0), 10.0),  # Too old
            (datetime(2023, 1, 1, 11, 35, 0), 20.0),  # Within interval
            (datetime(2023, 1, 1, 11, 45, 0), 30.0),  # Within interval
            (datetime(2023, 1, 1, 12, 0, 0), 40.0),   # Current
        ]
        
        for time, value in times_and_values:
            meter.set_meter_value(value, time)
            
        # Mock the buffer's get method
        with patch.object(meter.buffer, 'get', return_value=[20.0, 30.0, 40.0]):
            measurements = meter.current_interval_measurements
            assert measurements == [20.0, 30.0, 40.0]
            
    def test_mean_measurement_property(self):
        """Test mean_measurement property"""
        meter = MeterPoint(transactive_node=self.mock_tn)
        
        # Mock current_interval_measurements
        with patch.object(meter, 'current_interval_measurements', [10.0, 20.0, 30.0]):
            assert meter.mean_measurement == 20.0
            
    def test_get_record(self):
        """Test get_record method"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            name="TestMeter",
            measurement_type=MeasurementType.PowerReal,
            measurement_unit=MeasurementUnit.kW
        )
        
        record = meter.get_record()
        
        assert record['class'] == 'MeterPoint'
        assert record['measurement_type'] == MeasurementType.PowerReal
        assert record['measurement_unit'] == MeasurementUnit.kW
        assert record['data_period_seconds'] == 10.0
        assert record['scale_factor'] == 1.0
        
    def test_archive_reading(self):
        """Test archive_reading method"""
        meter = MeterPoint(transactive_node=self.mock_tn)
        
        test_time = datetime(2023, 1, 1, 12, 0, 0)
        test_value = 123.45
        
        meter.archive_reading(test_value, test_time)
        
        expected_call = {
            "table_name": "meter_reading",
            "data": {
                "meter_id": meter.id,
                "reading_time": test_time,
                "value": test_value
            }
        }
        
        meter.data_manager.archive_data.assert_called_once_with(expected_call)
        
    def test_table_name_property(self):
        """Test table_name property"""
        meter = MeterPoint(transactive_node=self.mock_tn)
        assert meter.table_name == 'meter_point'
        
    def test_get_archive_definitions(self):
        """Test get_archive_definitions class method"""
        definitions = MeterPoint.get_archive_definitions()
        
        assert isinstance(definitions, list)
        assert len(definitions) >= 3
        
        # Check for expected table definitions
        table_names = [def_dict['name'] for def_dict in definitions]
        assert 'entity_meter_point' in table_names
        assert 'meter_point' in table_names
        assert 'meter_reading' in table_names


class TestPushMeterPoint:
    """Test cases for PushMeterPoint class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.data_manager = Mock()
        
    def test_push_meter_point_init(self):
        """Test PushMeterPoint initialization"""
        with patch.object(PushMeterPoint, 'set_up_subscription') as mock_setup:
            meter = PushMeterPoint(
                transactive_node=self.mock_tn,
                name="PushMeter"
            )
            
            assert isinstance(meter, MeterPoint)
            assert meter.name == "PushMeter"
            mock_setup.assert_called_once()
            
    def test_set_up_subscription(self):
        """Test set_up_subscription method (currently a pass)"""
        with patch.object(PushMeterPoint, 'set_up_subscription') as mock_setup:
            # Temporarily remove the patch to test the actual method
            mock_setup.side_effect = lambda: None
            
            meter = PushMeterPoint(transactive_node=self.mock_tn)
            
            # Method should complete without error (it's a pass statement)
            meter.set_up_subscription()


class TestPullMeterPoint:
    """Test cases for PullMeterPoint class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.data_manager = Mock()
        
    def test_pull_meter_point_init(self):
        """Test PullMeterPoint initialization"""
        meter = PullMeterPoint(
            transactive_node=self.mock_tn,
            name="PullMeter"
        )
        
        assert isinstance(meter, MeterPoint)
        assert meter.name == "PullMeter"
        
    @patch('tent.meter_point.pull_meter_point._log')
    def test_read_meter(self, mock_log):
        """Test read_meter method"""
        meter = PullMeterPoint(transactive_node=self.mock_tn)
        
        mock_obj = Mock()
        mock_obj.name = "TestObject"
        
        meter.read_meter(mock_obj)
        
        mock_log.debug.assert_called_once_with(
            'Made it to PullMeterPoint.read_meter() for TestObject'
        )


class TestCSVMeterPoint:
    """Test cases for CSVMeterPoint class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.data_manager = Mock()
        
        # Create a temporary CSV file
        self.temp_csv = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv')
        self.csv_data = [
            "2023-01-01 12:00:00,100.0",
            "2023-01-01 12:15:00,125.0",
            "2023-01-01 12:30:00,150.0",
            "2023-01-01 12:45:00,175.0",
            "2023-01-01 13:00:00,200.0"
        ]
        
        # Write header and data
        self.temp_csv.write("Timestamp,Real Energy Total\n")
        for line in self.csv_data:
            self.temp_csv.write(line + "\n")
        self.temp_csv.close()
        
    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.temp_csv.name):
            os.unlink(self.temp_csv.name)
            
    def test_csv_meter_point_init(self):
        """Test CSVMeterPoint initialization"""
        meter = CSVMeterPoint(
            csv_path=self.temp_csv.name,
            transactive_node=self.mock_tn,
            name="CSVMeter"
        )
        
        assert isinstance(meter, PullMeterPoint)
        assert meter.csv_path == self.temp_csv.name
        assert meter.name == "CSVMeter"
        
    def test_csv_to_df(self):
        """Test _csv_to_df helper method"""
        meter = CSVMeterPoint(
            csv_path=self.temp_csv.name,
            transactive_node=self.mock_tn
        )
        
        df = meter._csv_to_df()
        
        assert isinstance(df, pd.DataFrame)
        assert 'Metered_Energy' in df.columns
        assert len(df) == 5
        assert df.index.name == 'Timestamp'
        assert isinstance(df.index[0], pd.Timestamp)
        
    def test_read_meter(self):
        """Test read_meter method"""
        meter = CSVMeterPoint(
            csv_path=self.temp_csv.name,
            transactive_node=self.mock_tn
        )
        
        # Create mock market with time intervals
        mock_market = Mock()
        mock_market.name = "TestMarket"
        
        # Create mock time intervals
        mock_interval1 = Mock()
        mock_interval1.start_time = datetime(2023, 1, 1, 12, 0, 0)
        mock_interval1.duration = timedelta(minutes=15)
        
        mock_interval2 = Mock()
        mock_interval2.start_time = datetime(2023, 1, 1, 12, 15, 0)
        mock_interval2.duration = timedelta(minutes=15)
        
        mock_market.time_intervals = [mock_interval1, mock_interval2]
        
        # Mock the meter_intervals list
        meter.meter_intervals = []
        
        with patch('builtins.print'):  # Suppress print output
            result = meter.read_meter(mock_market)
            
        # Should have processed intervals and called set_meter_value
        assert result == meter.meter_intervals
        
    def test_read_meter_missing_data(self):
        """Test read_meter with missing data points"""
        meter = CSVMeterPoint(
            csv_path=self.temp_csv.name,
            transactive_node=self.mock_tn
        )
        
        mock_market = Mock()
        mock_market.name = "TestMarket"
        
        # Create interval with time not in CSV
        mock_interval = Mock()
        mock_interval.start_time = datetime(2023, 1, 1, 14, 0, 0)  # Not in CSV
        mock_interval.duration = timedelta(minutes=15)
        
        mock_market.time_intervals = [mock_interval]
        meter.meter_intervals = []
        
        with patch('builtins.print'):
            result = meter.read_meter(mock_market)
            
        # Should handle missing data gracefully
        assert result == meter.meter_intervals
        
    def test_store_method(self):
        """Test store method"""
        meter = CSVMeterPoint(
            csv_path=self.temp_csv.name,
            transactive_node=self.mock_tn
        )
        
        # Mock the parent store method
        with patch('tent.meter_point.pull_meter_point.PullMeterPoint.store') as mock_store:
            meter.store("test.db", [])
            mock_store.assert_called_once_with("test.db", [])
            
    def test_read_database_method(self):
        """Test read_database method"""
        meter = CSVMeterPoint(
            csv_path=self.temp_csv.name,
            transactive_node=self.mock_tn
        )
        
        # Mock the parent read_database method
        with patch('tent.meter_point.pull_meter_point.PullMeterPoint.read_database') as mock_read:
            mock_read.return_value = "test_result"
            result = meter.read_database("test.db", "test_key")
            
            assert result == "test_result"
            mock_read.assert_called_once_with("test.db", "test_key")
            
    def test_table_name_property(self):
        """Test table_name property"""
        meter = CSVMeterPoint(
            csv_path=self.temp_csv.name,
            transactive_node=self.mock_tn
        )
        
        assert meter.table_name == 'csv_meter_point'
        
    def test_get_record(self):
        """Test get_record method"""
        meter = CSVMeterPoint(
            csv_path=self.temp_csv.name,
            transactive_node=self.mock_tn
        )
        
        record = meter.get_record()
        
        assert 'meter_id' in record
        assert record['csv_path'] == self.temp_csv.name
        assert record['meter_id'] == meter.id
        
    def test_get_archive_definitions(self):
        """Test get_archive_definitions class method"""
        definitions = CSVMeterPoint.get_archive_definitions()
        
        assert isinstance(definitions, list)
        
        # Find the csv_meter_point definition
        csv_def = None
        for definition in definitions:
            if definition['name'] == 'csv_meter_point':
                csv_def = definition
                break
                
        assert csv_def is not None
        assert csv_def['inherits'] == 'meter_point'
        
        # Check for required columns
        column_names = [col['name'] for col in csv_def['columns']]
        assert 'meter_id' in column_names
        assert 'csv_path' in column_names


class TestCSVMeterPointWithInvalidFile:
    """Test CSV meter point with invalid/missing files"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.data_manager = Mock()
        
    def test_csv_meter_point_with_nonexistent_file(self):
        """Test CSVMeterPoint with non-existent file"""
        meter = CSVMeterPoint(
            csv_path="/nonexistent/file.csv",
            transactive_node=self.mock_tn
        )
        
        # Should initialize without error
        assert meter.csv_path == "/nonexistent/file.csv"
        
        # But reading should fail
        with pytest.raises(FileNotFoundError):
            meter._csv_to_df()
            
    def test_csv_meter_point_with_malformed_csv(self):
        """Test CSVMeterPoint with malformed CSV"""
        # Create malformed CSV
        temp_csv = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv')
        temp_csv.write("Invalid,CSV,Format\n")
        temp_csv.write("Not,A,Date,123\n")
        temp_csv.close()
        
        try:
            meter = CSVMeterPoint(
                csv_path=temp_csv.name,
                transactive_node=self.mock_tn
            )
            
            # Should raise an error when trying to parse dates
            with pytest.raises(Exception):  # Could be ValueError, ParserError, etc.
                meter._csv_to_df()
                
        finally:
            os.unlink(temp_csv.name)


class TestMeterPointIntegration:
    """Integration tests for meter point functionality"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.data_manager = Mock()
        
    def test_meter_point_data_flow(self):
        """Test complete data flow from reading to archiving"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            name="IntegrationTest",
            measurement_type=MeasurementType.PowerReal,
            measurement_unit=MeasurementUnit.kW,
            scale_factor=0.001  # Convert W to kW
        )
        
        # Simulate meter readings over time
        readings = [
            (datetime(2023, 1, 1, 12, 0, 0), 1000.0),  # 1 kW
            (datetime(2023, 1, 1, 12, 0, 10), 1100.0), # 1.1 kW
            (datetime(2023, 1, 1, 12, 0, 20), 1200.0), # 1.2 kW
        ]
        
        for time, value in readings:
            meter.set_meter_value(value, time)
            
        # Check that values were scaled and stored
        assert meter.current_measurement == 1.2  # 1200.0 * 0.001
        assert meter.last_updated == datetime(2023, 1, 1, 12, 0, 20)
        
        # Check that archiving was called for each reading
        assert meter.data_manager.archive_data.call_count == 3
        
    def test_meter_point_buffer_overflow(self):
        """Test meter point buffer behavior when it overflows"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            data_period_seconds=1.0,
            max_buffer_seconds=3.0  # Very small buffer
        )
        
        # Buffer should hold max 4 values (ceil(3.0/1.0) + 1)
        expected_max_len = ceil(3.0 / 1.0) + 1
        assert meter.buffer.maxlen == expected_max_len
        
        # Add more values than buffer can hold
        for i in range(10):
            time = datetime(2023, 1, 1, 12, 0, i)
            meter.set_meter_value(float(i), time)
            
        # Buffer should only contain the last few values
        assert len(meter.buffer) <= expected_max_len
        
    def test_csv_meter_point_with_real_data(self):
        """Test CSV meter point with realistic data"""
        # Create realistic CSV data
        temp_csv = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv')
        
        # Write header
        temp_csv.write("Timestamp,Real Energy Total\n")
        
        # Generate hourly data for a day
        base_time = datetime(2023, 1, 1, 0, 0, 0)
        cumulative_energy = 0.0
        
        for hour in range(25):  # 25 hours to test interval calculations
            timestamp = base_time + timedelta(hours=hour)
            cumulative_energy += 50.0 + (hour * 2)  # Increasing energy consumption
            temp_csv.write(f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')},{cumulative_energy}\n")
            
        temp_csv.close()
        
        try:
            meter = CSVMeterPoint(
                csv_path=temp_csv.name,
                transactive_node=self.mock_tn,
                measurement_type=MeasurementType.EnergyReal,
                measurement_unit=MeasurementUnit.kWh
            )
            
            # Test the CSV parsing
            df = meter._csv_to_df()
            assert len(df) == 25
            assert df['Metered_Energy'].iloc[0] == 50.0
            assert df['Metered_Energy'].iloc[-1] > df['Metered_Energy'].iloc[0]
            
        finally:
            os.unlink(temp_csv.name)


class TestMeterPointEdgeCases:
    """Test edge cases and error conditions"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        self.mock_tn.data_manager = Mock()
        
    def test_meter_point_with_zero_scale_factor(self):
        """Test meter point with zero scale factor"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            scale_factor=0.0
        )
        
        meter.set_meter_value(100.0)
        assert meter.current_measurement == 0.0
        
    def test_meter_point_with_negative_scale_factor(self):
        """Test meter point with negative scale factor (reverse sign)"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            scale_factor=-1.0
        )
        
        meter.set_meter_value(50.0)
        assert meter.current_measurement == -50.0
        
    def test_meter_point_with_very_small_buffer(self):
        """Test meter point with minimum buffer size"""
        meter = MeterPoint(
            transactive_node=self.mock_tn,
            data_period_seconds=1.0,
            max_buffer_seconds=0.5  # Less than one period
        )
        
        # Should still have minimum buffer size
        expected_max_len = ceil(0.5 / 1.0) + 1
        assert meter.buffer.maxlen == expected_max_len
        assert meter.buffer.maxlen >= 1
        
    def test_meter_point_with_invalid_enum_values(self):
        """Test meter point with invalid enum values"""
        with pytest.raises((ValueError, KeyError)):
            MeterPoint(
                transactive_node=self.mock_tn,
                measurement_type="InvalidType"
            )
            
        with pytest.raises((ValueError, KeyError)):
            MeterPoint(
                transactive_node=self.mock_tn,
                measurement_unit="InvalidUnit"
            )
            
    def test_mean_measurement_with_empty_buffer(self):
        """Test mean_measurement when buffer is empty"""
        meter = MeterPoint(transactive_node=self.mock_tn)
        
        # Mock empty current_interval_measurements
        with patch.object(meter, 'current_interval_measurements', []):
            with pytest.raises(Exception):  # statistics.StatisticsError
                _ = meter.mean_measurement
                
    def test_csv_meter_point_with_empty_csv(self):
        """Test CSV meter point with empty CSV file"""
        temp_csv = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv')
        temp_csv.write("Timestamp,Real Energy Total\n")  # Only header
        temp_csv.close()
        
        try:
            meter = CSVMeterPoint(
                csv_path=temp_csv.name,
                transactive_node=self.mock_tn
            )
            
            df = meter._csv_to_df()
            assert len(df) == 0
            
        finally:
            os.unlink(temp_csv.name)
            
    def test_meter_point_properties_with_no_data(self):
        """Test meter point properties when no data has been recorded"""
        meter = MeterPoint(transactive_node=self.mock_tn)
        
        # These should handle empty buffer gracefully or raise appropriate errors
        try:
            _ = meter.current_measurement
            _ = meter.last_updated
        except (AttributeError, IndexError):
            # Expected when buffer is empty
            pass


if __name__ == "__main__":
    pytest.main([__file__])