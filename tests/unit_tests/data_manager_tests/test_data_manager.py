import pytest
from unittest.mock import Mock, patch
from collections import deque

from tent.data_manager.local_data_manager import LocalDataManager, VolttronDataManager


class TestLocalDataManagerUnits:
    """Unit tests for LocalDataManager business logic"""
    
    def setup_method(self):
        """Set up minimal test fixtures"""
        self.mock_parent = Mock()
        # Don't create real database engine in setup
        
    def test_database_url_construction_default(self):
        """Test default database URL construction"""
        ldm = LocalDataManager(self.mock_parent, {})
        
        # Should use default SQLite URL
        assert 'tent_db.sqlite' in str(ldm.engine.url)
        assert ldm.db_params == {}
        
    def test_database_url_construction_custom(self):
        """Test custom database URL construction"""
        custom_params = {'url': 'sqlite:///custom_database.db'}
        ldm = LocalDataManager(self.mock_parent, custom_params)
        
        # Should use custom URL
        assert 'custom_database.db' in str(ldm.engine.url)
        assert ldm.db_params == custom_params
        
    def test_database_url_construction_none_params(self):
        """Test database URL construction with None params"""
        ldm = LocalDataManager(self.mock_parent, None)
        
        # Should handle None params gracefully
        assert 'tent_db.sqlite' in str(ldm.engine.url)
        assert ldm.db_params == {}

    def test_archive_data_queues_when_not_ready(self):
        """Test that records are queued when archive isn't ready"""
        ldm = LocalDataManager(self.mock_parent, {})
        ldm.archive_ready = False
        
        records = [
            {'table_name': 'test_table', 'data': {'id': 1, 'name': 'test'}},
            {'table_name': 'other_table', 'data': {'id': 2, 'value': 'data'}}
        ]
        
        ldm.archive_data(records)
        
        # Should queue all records
        assert len(ldm.archival_queue) == 2
        assert ldm.archival_queue[0] == records[0]
        assert ldm.archival_queue[1] == records[1]

    def test_archive_data_handles_empty_records(self):
        """Test archive_data handles None and empty records gracefully"""
        ldm = LocalDataManager(self.mock_parent, {})
        ldm.archive_ready = False
        
        # Should handle None
        ldm.archive_data(None)
        assert len(ldm.archival_queue) == 0
        
        # Should handle empty list
        ldm.archive_data([])
        assert len(ldm.archival_queue) == 0

    def test_archive_data_extends_queue_correctly(self):
        """Test that archival queue is extended, not replaced"""
        ldm = LocalDataManager(self.mock_parent, {})
        ldm.archive_ready = False
        
        # Add first batch
        first_records = [{'table_name': 'table1', 'data': {'id': 1}}]
        ldm.archive_data(first_records)
        assert len(ldm.archival_queue) == 1
        
        # Add second batch
        second_records = [
            {'table_name': 'table2', 'data': {'id': 2}},
            {'table_name': 'table3', 'data': {'id': 3}}
        ]
        ldm.archive_data(second_records)
        
        # Should have all records
        assert len(ldm.archival_queue) == 3
        assert ldm.archival_queue[0] == first_records[0]
        assert ldm.archival_queue[1] == second_records[0]
        assert ldm.archival_queue[2] == second_records[1]

    @patch('tent.data_manager.local_data_manager.Session')
    def test_archive_data_processes_single_mapping(self, mock_session_class):
        """Test that single mapping gets converted to list for processing"""
        ldm = LocalDataManager(self.mock_parent, {})
        ldm.archive_ready = True
        ldm.orm = {'test_table': Mock()}
        
        # Mock session context manager
        mock_session = Mock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        
        # Pass single mapping (not in a list)
        single_record = {'table_name': 'test_table', 'data': {'id': 1}}
        ldm.archive_data(single_record)
        
        # Should have been processed (session.add called)
        assert mock_session.add.call_count >= 1
        mock_session.commit.assert_called_once()

    @patch('tent.data_manager.local_data_manager.Session')
    def test_archive_data_handles_missing_table_gracefully(self, mock_session_class):
        """Test that missing table doesn't crash the system"""
        ldm = LocalDataManager(self.mock_parent, {})
        ldm.archive_ready = True
        ldm.orm = {}  # No tables defined
        
        # Mock session
        mock_session = Mock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        
        records = [{'table_name': 'nonexistent_table', 'data': {'id': 1}}]
        
        # Should not raise exception
        ldm.archive_data(records)
        
        # Should still commit (even though no records were added)
        mock_session.commit.assert_called_once()
        # No records should be added to session for nonexistent table
        mock_session.add.assert_not_called()

    @patch('tent.data_manager.local_data_manager.Session')
    def test_archive_data_processes_queue_first(self, mock_session_class):
        """Test that queued records are processed before new records"""
        ldm = LocalDataManager(self.mock_parent, {})
        ldm.archive_ready = True
        ldm.orm = {'test_table': Mock()}
        
        # Add some records to queue first
        queued_records = [
            {'table_name': 'test_table', 'data': {'id': 1}},
            {'table_name': 'test_table', 'data': {'id': 2}}
        ]
        ldm.archival_queue.extend(queued_records)
        
        # Mock session
        mock_session = Mock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        
        # Now archive new records
        new_records = [{'table_name': 'test_table', 'data': {'id': 3}}]
        ldm.archive_data(new_records)
        
        # Queue should be empty (processed)
        assert len(ldm.archival_queue) == 0
        
        # Should have added 3 records total (2 from queue + 1 new)
        assert mock_session.add.call_count == 3
        mock_session.commit.assert_called_once()

    @patch('tent.data_manager.local_data_manager.Session')
    def test_add_to_session_creates_orm_object(self, mock_session_class):
        """Test that _add_to_session creates proper ORM object"""
        ldm = LocalDataManager(self.mock_parent, {})
        ldm.archive_ready = True
        
        # Mock ORM class
        mock_orm_class = Mock()
        mock_orm_instance = Mock()
        mock_orm_class.return_value = mock_orm_instance
        ldm.orm = {'test_table': mock_orm_class}
        
        # Mock session
        mock_session = Mock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        
        records = [{'table_name': 'test_table', 'data': {'id': 1, 'name': 'test'}}]
        ldm.archive_data(records)
        
        # Verify ORM class was called with correct data
        mock_orm_class.assert_called_once_with(id=1, name='test')
        # Verify ORM instance was added to session
        mock_session.add.assert_called_once_with(mock_orm_instance)

    def test_init_archive_sets_ready_flag(self):
        """Test that init_archive sets archive_ready flag"""
        # Mock the metadata creation to avoid real database
        with patch.object(LocalDataManager, '__init__', return_value=None):
            ldm = LocalDataManager.__new__(LocalDataManager)
            ldm.registry = Mock()
            ldm.registry.metadata = Mock()
            ldm.engine = Mock()
            ldm.archive_ready = False
            
            # Mock archive_data to avoid infinite recursion
            ldm.archive_data = Mock()
            
            ldm.init_archive()
            
            # Should set ready flag and call archive_data
            assert ldm.archive_ready is True
            ldm.archive_data.assert_called_once()


class TestVolttronDataManagerUnits:
    """Unit tests for VolttronDataManager business logic"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_tn = Mock()
        
    def test_init_stores_parameters(self):
        """Test initialization stores all parameters correctly"""
        vdm = VolttronDataManager(
            self.mock_tn, 
            historian_topic='Custom/Archive',
            historian_vip='custom.historian'
        )
        
        assert vdm.parent == self.mock_tn
        # Note: VolttronDataManager stores 'parent', not 'tn'
        assert vdm.historian_topic == 'Custom/Archive'
        assert vdm.historian_vip == 'custom.historian'
        
    def test_init_default_parameters(self):
        """Test initialization with default parameters"""
        vdm = VolttronDataManager(self.mock_tn)
        
        assert vdm.historian_topic == 'TNS/Archive'
        assert vdm.historian_vip == 'tns.historian'

    def test_archive_data_publishes_records(self):
        """Test that archive_data publishes records to correct topic"""
        vdm = VolttronDataManager(self.mock_tn)
        
        records = [
            {'table_name': 'test', 'data': {'id': 1}},
            {'table_name': 'other', 'data': {'id': 2}}
        ]
        
        vdm.archive_data(records)
        
        # Should publish to historian topic
        self.mock_tn.publish.assert_called_once_with('TNS/Archive', message=records)

    def test_archive_data_empty_records(self):
        """Test archive_data handles empty records"""
        vdm = VolttronDataManager(self.mock_tn)
        
        vdm.archive_data([])
        
        # Should still publish empty list
        self.mock_tn.publish.assert_called_once_with('TNS/Archive', message=[])

    @patch('tent.data_manager.local_data_manager.json.dumps')
    def test_init_archive_calls_rpc(self, mock_json_dumps):
        """Test that init_archive makes RPC call with definitions"""
        vdm = VolttronDataManager(self.mock_tn)
        vdm.definitions = {'test_table': {'name': 'test_table'}}
        
        mock_json_dumps.return_value = '{"test_table": {"name": "test_table"}}'
        
        vdm.init_archive()
        
        # Should serialize definitions and make RPC call
        mock_json_dumps.assert_called_once_with(vdm.definitions)
        self.mock_tn.rpc.call.assert_called_once_with(
            'tns.historian', 
            'create_tables', 
            '{"test_table": {"name": "test_table"}}'
        )

    def test_init_archive_uses_custom_vip(self):
        """Test init_archive uses custom historian VIP"""
        custom_vip = 'my.custom.historian'
        vdm = VolttronDataManager(self.mock_tn, historian_vip=custom_vip)
        vdm.definitions = {}
        
        with patch('tent.data_manager.local_data_manager.json.dumps', return_value='{}'):
            vdm.init_archive()
            
        # Should use custom VIP
        self.mock_tn.rpc.call.assert_called_once_with(
            custom_vip, 
            'create_tables', 
            '{}'
        )


if __name__ == "__main__":
    pytest.main([__file__])
