import pytest
from unittest.mock import Mock
from collections import deque
from datetime import datetime
from uuid import uuid4

from tent.data_manager.local_data_manager import LocalDataManager


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

    @staticmethod
    def _manager_with_table(tmp_path, ready=True):
        ldm = LocalDataManager(Mock(), {'url': f'sqlite:///{tmp_path / "data.sqlite"}'})
        ldm.add_tables([{
            'name': 'readings',
            'columns': [
                {'name': 'id', 'type': 'Integer', 'kwargs': {'primary_key': True}},
                {'name': 'name', 'type': 'String'},
            ],
        }])
        if ready:
            ldm.init_archive()
        return ldm

    def test_a_single_mapping_is_written_as_one_row(self, tmp_path):
        ldm = self._manager_with_table(tmp_path)
        ldm.archive_data({'table_name': 'readings', 'data': {'id': 1, 'name': 'one'}})
        assert ldm.query_data('readings') == [{'id': 1, 'name': 'one'}]

    def test_queued_records_are_written_before_new_ones(self, tmp_path):
        ldm = self._manager_with_table(tmp_path, ready=False)
        ldm.archive_data([{'table_name': 'readings', 'data': {'id': 1, 'name': 'queued'}},
                          {'table_name': 'readings', 'data': {'id': 2, 'name': 'queued'}}])
        assert len(ldm.archival_queue) == 2, 'held until the archive is ready'
        ldm.init_archive()
        ldm.archive_data([{'table_name': 'readings', 'data': {'id': 3, 'name': 'new'}}])
        assert ldm.archival_queue == deque()
        assert [(r['id'], r['name']) for r in ldm.query_data('readings', order_by=['id'])] == \
               [(1, 'queued'), (2, 'queued'), (3, 'new')]

    def test_init_archive_creates_the_tables_and_flushes_the_queue(self, tmp_path):
        ldm = self._manager_with_table(tmp_path, ready=False)
        ldm.archive_data({'table_name': 'readings', 'data': {'id': 7, 'name': 'early'}})
        assert ldm.archive_ready is False
        ldm.init_archive()
        assert ldm.archive_ready is True and ldm.archival_queue == deque()
        assert ldm.query_data('readings') == [{'id': 7, 'name': 'early'}]

    def test_upsert_and_query_data(self, tmp_path):
        ldm = LocalDataManager(self.mock_parent, {'url': f'sqlite:///{tmp_path / "data.sqlite"}'})
        ldm.add_tables([{
            'name': 'topics',
            'columns': [
                {'name': 'topic_id', 'type': 'Integer',
                 'kwargs': {'primary_key': True, 'autoincrement': True}},
                {'name': 'topic_name', 'type': 'String', 'kwargs': {'unique': True}},
                {'name': 'metadata', 'type': 'Text'},
            ],
        }])
        ldm.init_archive()

        inserted = ldm.upsert_data(
            'topics',
            {'topic_name': 'record/example', 'metadata': '{}'},
            ['topic_name'],
        )
        updated = ldm.upsert_data(
            'topics',
            {'topic_id': inserted['topic_id'], 'topic_name': 'record/example', 'metadata': '{"units":"kW"}'},
            ['topic_id'],
        )

        assert updated['topic_id'] == inserted['topic_id']
        assert ldm.query_data('topics', {'topic_name': 'record/example'}) == [updated]

    def test_query_data_supports_ranges_order_and_pagination(self, tmp_path):
        ldm = LocalDataManager(self.mock_parent, {'url': f'sqlite:///{tmp_path / "data.sqlite"}'})
        ldm.add_tables([{
            'name': 'readings',
            'columns': [
                {'name': 'id', 'type': 'Integer', 'kwargs': {'primary_key': True}},
                {'name': 'ts', 'type': 'DateTime'},
            ],
        }])
        ldm.init_archive()
        ldm.archive_data([
            {'table_name': 'readings', 'data': {'id': 1, 'ts': '2026-06-09T01:00:00+00:00'}},
            {'table_name': 'readings', 'data': {'id': 2, 'ts': '2026-06-09T02:00:00+00:00'}},
            {'table_name': 'readings', 'data': {'id': 3, 'ts': '2026-06-09T03:00:00+00:00'}},
        ])

        rows = ldm.query_data(
            'readings',
            {'ts': {'gte': '2026-06-09T01:00:00+00:00', 'lt': '2026-06-09T04:00:00+00:00'}},
            order_by=[('ts', 'desc')],
            limit=1,
            offset=1,
        )

        assert rows[0]['id'] == 2
        assert isinstance(rows[0]['ts'], datetime)

    def test_archive_data_normalizes_transport_uuid(self, tmp_path):
        ldm = LocalDataManager(self.mock_parent, {'url': f'sqlite:///{tmp_path / "data.sqlite"}'})
        ldm.add_tables([{
            'name': 'entities',
            'columns': [
                {'name': 'id', 'type': 'Uuid', 'kwargs': {'primary_key': True}},
            ],
        }])
        ldm.init_archive()
        entity_id = uuid4()

        ldm.archive_data({'table_name': 'entities', 'data': {'id': str(entity_id)}})

        assert ldm.query_data('entities')[0]['id'] == entity_id
