import pytest
import logging
import json
import tempfile
import os
from unittest.mock import Mock, patch, MagicMock, call
from collections import deque
from typing import Mapping, Sequence

import sqlalchemy
from sqlalchemy import create_engine, Column, Integer, String, Table, MetaData
from sqlalchemy.orm import Session, registry
from sqlalchemy.exc import SQLAlchemyError

# Import classes to test
from tent.data_manager import DataManager
from tent.data_manager.local_data_manager import LocalDataManager, VolttronDataManager
import tent.enumerations


class TestDataManager:
    """Test cases for base DataManager class"""
    
    def test_data_manager_init(self):
        """Test DataManager initialization"""
        mock_parent = Mock()
        
        dm = DataManager(mock_parent, archival_buffer_length=50)
        
        assert dm.parent == mock_parent
        assert dm.archive_ready is False
        assert isinstance(dm.archival_queue, deque)
        assert dm.archival_queue.maxlen == 50
        assert isinstance(dm.definitions, dict)
        assert isinstance(dm.orm, dict)
        assert isinstance(dm.registry, registry)
        assert isinstance(dm.tables, dict)
        
    def test_data_manager_default_buffer_length(self):
        """Test DataManager with default buffer length"""
        mock_parent = Mock()
        
        dm = DataManager(mock_parent)
        
        assert dm.archival_queue.maxlen == 100
        
    def test_add_single_table(self):
        """Test adding a single table definition"""
        mock_parent = Mock()
        dm = DataManager(mock_parent)
        
        definitions = [{
            'name': 'test_table',
            'columns': [
                {'name': 'id', 'type': 'Integer', 'identity': True, 'kwargs': {'primary_key': True}},
                {'name': 'name', 'type': 'String', 'type_args': [255]}
            ]
        }]
        
        dm.add_table('test_table', definitions)
        
        assert 'test_table' in dm.definitions
        assert 'test_table' in dm.tables
        assert 'test_table' in dm.orm
        assert dm.definitions['test_table']['name'] == 'test_table'
        
    def test_add_table_with_inheritance(self):
        """Test adding table with inheritance"""
        mock_parent = Mock()
        dm = DataManager(mock_parent)
        
        definitions = [
            {
                'name': 'base_table',
                'columns': [
                    {'name': 'id', 'type': 'Integer', 'identity': True, 'kwargs': {'primary_key': True}},
                    {'name': 'base_field', 'type': 'String', 'type_args': [255]}
                ]
            },
            {
                'name': 'child_table',
                'inherits': 'base_table',
                'columns': [
                    # For joined-table inheritance SQLAlchemy requires PK + FK to parent
                    {'name': 'id', 'type': 'Integer', 'kwargs': {'primary_key': True}, 'foreign_key': 'base_table.id'},
                    {'name': 'child_field', 'type': 'String', 'type_args': [255]}
                ]
            }
        ]
        
        dm.add_table('child_table', definitions)
        
        # Both tables should be added
        assert 'base_table' in dm.tables
        assert 'child_table' in dm.tables
        assert 'base_table' in dm.orm
        assert 'child_table' in dm.orm
        
    def test_add_multiple_tables(self):
        """Test adding multiple table definitions"""
        mock_parent = Mock()
        dm = DataManager(mock_parent)
        
        definitions = [
            {
                'name': 'table1',
                'columns': [
                    {'name': 'id', 'type': 'Integer', 'identity': True, 'kwargs': {'primary_key': True}}
                ]
            },
            {
                'name': 'table2',
                'columns': [
                    {'name': 'id', 'type': 'Integer', 'identity': True, 'kwargs': {'primary_key': True}}
                ]
            }
        ]
        
        dm.add_tables(definitions)
        
        assert len(dm.tables) == 2
        assert 'table1' in dm.tables
        assert 'table2' in dm.tables
        
    def test_parse_column_def_basic(self):
        """Test parsing basic column definition"""
        column_def = {
            'name': 'test_column',
            'type': 'String',
            'type_args': [255]
        }
        
        column = DataManager._parse_column_def(column_def)
        
        assert column.name == 'test_column'
        assert isinstance(column.type, sqlalchemy.String)
        
    def test_parse_column_def_with_foreign_key(self):
        """Test parsing column definition with foreign key"""
        column_def = {
            'name': 'parent_id',
            'type': 'Integer',
            'foreign_key': 'parent_table.id'
        }
        
        column = DataManager._parse_column_def(column_def)
        
        assert column.name == 'parent_id'
        assert len(column.foreign_keys) == 1
        
    def test_parse_column_def_with_enumeration(self):
        """Test parsing column definition with enumeration"""
        # Inject a temporary Enum into tent.enumerations so SQLAlchemy Enum succeeds
        import enum
        class TestEnum(enum.Enum):
            A = 'A'
            B = 'B'
        setattr(tent.enumerations, 'TestEnum', TestEnum)
        column_def = {
            'name': 'status',
            'enumeration': 'TestEnum'
        }
        column = DataManager._parse_column_def(column_def)
        assert column.name == 'status'
        # Cleanup (not strictly required for test isolation here)
        delattr(tent.enumerations, 'TestEnum')
            
    def test_parse_column_def_with_identity(self):
        """Test parsing column definition with identity"""
        column_def = {
            'name': 'id',
            'type': 'Integer',
            'identity': True,
            'kwargs': {'primary_key': True}
        }
        
        column = DataManager._parse_column_def(column_def)
        
        assert column.name == 'id'
        assert column.primary_key is True
        
    def test_parse_index_def(self):
        """Test parsing index definition"""
        index_def = {
            'name': 'idx_test',
            'columns': ['col1', 'col2'],
            'unique': True
        }
        
        index = DataManager._parse_index_def(index_def)
        
        assert index.name == 'idx_test'
        assert index.unique is True
        
    def test_parse_index_def_non_unique(self):
        """Test parsing non-unique index definition"""
        index_def = {
            'name': 'idx_test',
            'columns': ['col1']
        }
        
        index = DataManager._parse_index_def(index_def)
        
        assert index.name == 'idx_test'
        assert index.unique is False
        
    def test_parse_table_definition(self):
        """Test parsing complete table definition"""
        mock_parent = Mock()
        dm = DataManager(mock_parent)
        
        definition = {
            'name': 'test_table',
            'columns': [
                {'name': 'id', 'type': 'Integer', 'identity': True, 'kwargs': {'primary_key': True}},
                {'name': 'name', 'type': 'String', 'type_args': [255]}
            ],
            'indices': [
                {'name': 'idx_name', 'columns': ['name']}
            ]
        }
        
        table = dm._parse_table_definition(definition)
        
        assert table.name == 'test_table'
        assert len(table.columns) == 2
        assert len(table.indexes) == 1
        
    def test_data_manager_concrete_behavior(self):
        """DataManager is currently concrete (abstract decorators unused without ABC metaclass)."""
        mock_parent = Mock()
        dm = DataManager(mock_parent)
        # Methods are stubs that return None
        assert dm.init_archive() is None
        assert dm.archive_data([]) is None
            
    def test_get_archive_definitions_class_method(self):
        """Test get_archive_definitions class method"""
        # This method returns None in the base implementation
        result = DataManager.get_archive_definitions()
        assert result is None


class TestLocalDataManager:
    """Test cases for LocalDataManager class"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_parent = Mock()
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.sqlite')
        self.temp_db.close()
        self.db_url = f'sqlite:///{self.temp_db.name}'
        
    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)
            
    def test_local_data_manager_init_with_params(self):
        """Test LocalDataManager initialization with db_params"""
        db_params = {'url': self.db_url}
        
        ldm = LocalDataManager(self.mock_parent, db_params)
        
        assert ldm.parent == self.mock_parent
        assert ldm.db_params == db_params
        assert ldm.engine.url.database == self.temp_db.name
        
    def test_local_data_manager_init_without_params(self):
        """Test LocalDataManager initialization without db_params"""
        ldm = LocalDataManager(self.mock_parent, None)
        
        assert ldm.db_params == {}
        assert 'sqlite:///tent_db.sqlite' in str(ldm.engine.url)
        
    def test_local_data_manager_init_default_url(self):
        """Test LocalDataManager with default database URL"""
        db_params = {}
        
        ldm = LocalDataManager(self.mock_parent, db_params)
        
        assert 'tent_db.sqlite' in str(ldm.engine.url)
        
    def test_init_archive(self):
        """Test archive initialization"""
        db_params = {'url': self.db_url}
        ldm = LocalDataManager(self.mock_parent, db_params)
        
        # Add a simple table definition
        definitions = [{
            'name': 'test_table',
            'columns': [
                {'name': 'id', 'type': 'Integer', 'identity': True, 'kwargs': {'primary_key': True}},
                {'name': 'name', 'type': 'String', 'type_args': [255]}
            ]
        }]
        ldm.add_tables(definitions)
        
        # Mock the archive_data method to avoid recursion
        with patch.object(ldm, 'archive_data', side_effect=ldm.archive_data) as mock_archive:
            ldm.init_archive()
            
            assert ldm.archive_ready is True
            # archive_data should be called during init
            mock_archive.assert_called()
            
    @patch('tent.data_manager.local_data_manager.Session')
    def test_archive_data_when_ready(self, mock_session_class):
        """Test archiving data when archive is ready"""
        mock_session = Mock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        
        db_params = {'url': self.db_url}
        ldm = LocalDataManager(self.mock_parent, db_params)
        ldm.archive_ready = True
        
        # Add table definition and ORM
        ldm.orm['test_table'] = Mock()
        
        records = [
            {
                'table_name': 'test_table',
                'data': {'id': 1, 'name': 'test'}
            }
        ]
        
        ldm.archive_data(records)
        
        # Verify session operations
        mock_session.add.assert_called()
        mock_session.commit.assert_called_once()
        
    def test_archive_data_when_not_ready(self):
        """Test archiving data when archive is not ready"""
        db_params = {'url': self.db_url}
        ldm = LocalDataManager(self.mock_parent, db_params)
        ldm.archive_ready = False
        
        records = [
            {
                'table_name': 'test_table',
                'data': {'id': 1, 'name': 'test'}
            }
        ]
        
        ldm.archive_data(records)
        
        # Records should be added to queue
        assert len(ldm.archival_queue) == 1
        assert ldm.archival_queue[0] == records[0]
        
    @patch('tent.data_manager.local_data_manager.Session')
    def test_archive_data_processes_queue(self, mock_session_class):
        """Test that archive_data processes queued records"""
        mock_session = Mock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        
        db_params = {'url': self.db_url}
        ldm = LocalDataManager(self.mock_parent, db_params)
        ldm.archive_ready = True
        ldm.orm['test_table'] = Mock()
        
        # Add records to queue
        queued_record = {'table_name': 'test_table', 'data': {'id': 1}}
        ldm.archival_queue.append(queued_record)
        
        new_records = [{'table_name': 'test_table', 'data': {'id': 2}}]
        
        ldm.archive_data(new_records)
        
        # Both queued and new records should be processed
        assert mock_session.add.call_count == 2
        assert len(ldm.archival_queue) == 0
        
    @patch('tent.data_manager.local_data_manager.Session')
    def test_archive_data_with_mapping(self, mock_session_class):
        """Test archiving data with single mapping instead of sequence"""
        mock_session = Mock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        
        db_params = {'url': self.db_url}
        ldm = LocalDataManager(self.mock_parent, db_params)
        ldm.archive_ready = True
        ldm.orm['test_table'] = Mock()
        
        record = {'table_name': 'test_table', 'data': {'id': 1}}
        
        ldm.archive_data(record)
        
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        
    @patch('tent.data_manager.local_data_manager.Session')
    @patch('tent.data_manager.local_data_manager._log')
    def test_archive_data_nonexistent_table(self, mock_log, mock_session_class):
        """Test archiving data to non-existent table"""
        mock_session = Mock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        
        db_params = {'url': self.db_url}
        ldm = LocalDataManager(self.mock_parent, db_params)
        ldm.archive_ready = True
        
        records = [{'table_name': 'nonexistent_table', 'data': {'id': 1}}]
        
        ldm.archive_data(records)
        
        # Should log warning about non-existent table
        mock_log.warning.assert_called_with('Attempted to archive data to non-existent table: nonexistent_table')
        
    def test_archive_data_empty_records(self):
        """Test archiving with empty records"""
        db_params = {'url': self.db_url}
        ldm = LocalDataManager(self.mock_parent, db_params)
        ldm.archive_ready = True
        
        # Should handle None and empty list gracefully
        ldm.archive_data(None)
        ldm.archive_data([])
        
        # No errors should occur
        
    @patch('tent.data_manager.local_data_manager.Session')
    def test_archive_data_session_error_handling(self, mock_session_class):
        """Test error handling during session operations"""
        mock_session = Mock()
        mock_session.commit.side_effect = SQLAlchemyError("Database error")
        mock_session_class.return_value.__enter__.return_value = mock_session
        
        db_params = {'url': self.db_url}
        ldm = LocalDataManager(self.mock_parent, db_params)
        ldm.archive_ready = True
        ldm.orm['test_table'] = Mock()
        
        records = [{'table_name': 'test_table', 'data': {'id': 1}}]
        
        # Should raise the SQLAlchemy error
        with pytest.raises(SQLAlchemyError):
            ldm.archive_data(records)


class TestVolttronDataManager:
    """Test cases for VolttronDataManager class"""
    
    def test_volttron_data_manager_init(self):
        """Test VolttronDataManager initialization"""
        mock_tn = Mock()
        
        vdm = VolttronDataManager(mock_tn)
        
        assert vdm.parent == mock_tn  # From parent DataManager class via inheritance
        assert vdm.historian_topic == 'TNS/Archive'
        assert vdm.historian_vip == 'tns.historian'
        
    def test_volttron_data_manager_init_custom_params(self):
        """Test VolttronDataManager initialization with custom parameters"""
        mock_tn = Mock()
        custom_topic = 'Custom/Archive'
        custom_vip = 'custom.historian'
        
        vdm = VolttronDataManager(mock_tn, custom_topic, custom_vip)
        
        assert vdm.historian_topic == custom_topic
        assert vdm.historian_vip == custom_vip
        
    @patch('tent.data_manager.local_data_manager.json.dumps')
    def test_init_archive(self, mock_json_dumps):
        """Test Volttron archive initialization"""
        mock_tn = Mock()
        mock_json_dumps.return_value = '{"definitions": "test"}'
        
        vdm = VolttronDataManager(mock_tn)
        vdm.definitions = {'test': 'definition'}
        
        vdm.init_archive()
        
        # Should serialize definitions and call RPC
        mock_json_dumps.assert_called_once_with(vdm.definitions)
        mock_tn.rpc.call.assert_called_once_with('tns.historian', 'create_tables', '{"definitions": "test"}')
        
    def test_archive_data(self):
        """Test Volttron data archiving"""
        mock_tn = Mock()
        
        vdm = VolttronDataManager(mock_tn)
        
        records = [{'table_name': 'test', 'data': {'id': 1}}]
        
        vdm.archive_data(records)
        
        # Should publish records to historian topic
        mock_tn.publish.assert_called_once_with('TNS/Archive', message=records)
        
    def test_archive_data_empty_records(self):
        """Test Volttron archiving with empty records"""
        mock_tn = Mock()
        
        vdm = VolttronDataManager(mock_tn)
        
        vdm.archive_data([])
        
        # Should still call publish
        mock_tn.publish.assert_called_once_with('TNS/Archive', message=[])


class TestDataManagerIntegration:
    """Integration tests for DataManager functionality"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.sqlite')
        self.temp_db.close()
        self.db_url = f'sqlite:///{self.temp_db.name}'
        
    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)
            
    def test_full_workflow_local_data_manager(self):
        """Test complete workflow from table definition to data archiving"""
        mock_parent = Mock()
        db_params = {'url': self.db_url}
        
        ldm = LocalDataManager(mock_parent, db_params)
        
        # Define table structure
        definitions = [{
            'name': 'user',
            'columns': [
                {'name': 'id', 'type': 'Integer', 'identity': True, 'kwargs': {'primary_key': True}},
                {'name': 'username', 'type': 'String', 'type_args': [50]},
                {'name': 'email', 'type': 'String', 'type_args': [100]}
            ],
            'indices': [
                {'name': 'idx_username', 'columns': ['username'], 'unique': True}
            ]
        }]
        
        # Add tables
        ldm.add_tables(definitions)
        
        # Initialize archive (creates tables)
        ldm.init_archive()
        
        # Verify table was created
        assert 'user' in ldm.tables
        assert 'user' in ldm.orm
        assert ldm.archive_ready is True
        
        # Archive some data
        records = [
            {
                'table_name': 'user',
                'data': {'username': 'testuser', 'email': 'test@example.com'}
            }
        ]
        
        # This should work without errors
        ldm.archive_data(records)
        
        # Verify data was written (by checking the database directly)
        with Session(ldm.engine) as session:
            result = session.execute(sqlalchemy.text("SELECT COUNT(*) FROM user")).scalar()
            assert result == 1
            
    def test_inheritance_workflow(self):
        """Test workflow with table inheritance"""
        mock_parent = Mock()
        db_params = {'url': self.db_url}
        
        ldm = LocalDataManager(mock_parent, db_params)
        
        # Define base and child tables
        definitions = [
            {
                'name': 'base_entity',
                'columns': [
                    {'name': 'id', 'type': 'Integer', 'identity': True, 'kwargs': {'primary_key': True}},
                    {'name': 'created_at', 'type': 'DateTime'},
                    {'name': 'type', 'type': 'String', 'type_args': [50]}
                ]
            },
            {
                'name': 'user',
                'inherits': 'base_entity',
                'columns': [
                    {'name': 'id', 'type': 'Integer', 'kwargs': {'primary_key': True}, 'foreign_key': 'base_entity.id'},
                    {'name': 'username', 'type': 'String', 'type_args': [50]}
                ]
            }
        ]
        
        ldm.add_tables(definitions)
        ldm.init_archive()
        
        # Verify both tables exist
        assert 'base_entity' in ldm.tables
        assert 'user' in ldm.tables
        assert 'base_entity' in ldm.orm
        assert 'user' in ldm.orm
        
    def test_error_handling_integration(self):
        """Test error handling in integrated workflow"""
        mock_parent = Mock()
        
        # Use invalid database URL to test error handling
        db_params = {'url': 'invalid://database/url'}
        # Expect failure during LocalDataManager initialization due to invalid dialect
        with pytest.raises(Exception):
            LocalDataManager(mock_parent, db_params)


class TestDataManagerEdgeCases:
    """Test edge cases and error conditions"""
    
    def test_column_parsing_with_unknown_type(self):
        """Test column parsing with unknown type"""
        column_def = {
            'name': 'test_column',
            'type': 'UnknownType'
        }
        
        # Should handle unknown types gracefully
        column = DataManager._parse_column_def(column_def)
        assert column.name == 'test_column'
        
    def test_enum_column_with_missing_enumeration(self):
        """Test enum column with missing enumeration"""
        column_def = {
            'name': 'status',
            'enumeration': 'NonExistentEnum'
        }
        # Should handle missing enumeration gracefully (skips enum type)
        column = DataManager._parse_column_def(column_def)
        assert column.name == 'status'
            
    def test_table_definition_with_empty_columns(self):
        """Test table definition with empty columns list"""
        mock_parent = Mock()
        dm = DataManager(mock_parent)
        
        definition = {
            'name': 'empty_table',
            'columns': []
        }
        
        table = dm._parse_table_definition(definition)
        assert table.name == 'empty_table'
        assert len(table.columns) == 0
        
    def test_archival_queue_overflow(self):
        """Test archival queue behavior when it overflows"""
        mock_parent = Mock()
        dm = DataManager(mock_parent, archival_buffer_length=2)
        
        # Add more records than the buffer can hold
        for i in range(5):
            dm.archival_queue.append(f'record_{i}')
            
        # Only the last 2 records should remain
        assert len(dm.archival_queue) == 2
        assert 'record_3' in dm.archival_queue
        assert 'record_4' in dm.archival_queue
        
    def test_duplicate_table_addition(self):
        """Test adding the same table multiple times"""
        mock_parent = Mock()
        dm = DataManager(mock_parent)
        
        definition = {
            'name': 'duplicate_table',
            'columns': [
                {'name': 'id', 'type': 'Integer', 'kwargs': {'primary_key': True}}
            ]
        }
        
        definitions = [definition]
        
        # Add the same table twice
        dm.add_table('duplicate_table', definitions)
        dm.add_table('duplicate_table', definitions)
        
        # Should only have one entry
        assert len(dm.tables) == 1
        assert len(dm.orm) == 1


if __name__ == "__main__":
    pytest.main([__file__])