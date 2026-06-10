import json
import logging

from datetime import datetime
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.sqltypes import DateTime, Uuid
from typing import Mapping, Sequence

from tent.data_manager import DataManager

_log = logging.getLogger(__name__)


class LocalDataManager(DataManager):
    def __init__(self, parent, db_params: dict, *args, **kwargs):
        super(LocalDataManager, self).__init__(parent, *args, **kwargs)
        # TODO: Should this just be checking for a db_type of local? Is this the same code for postgres or others?
        self.db_params = db_params if db_params else {}
        database_url = self.db_params.get('url', 'sqlite:///tent_db.sqlite')
        self.engine = create_engine(database_url)

    def init_archive(self):
        self.registry.metadata.create_all(self.engine, checkfirst=True)
        # TODO: Can we check if the database is actually successfully created and ready here?
        self.archive_ready = True
        self.archive_data()

    def archive_data(self, records: Mapping|Sequence = None):
        # TODO: Would it be better to move db write logic into a periodic and always append here to the archival_queue?
        records = records if records else []
        records = [records] if isinstance(records, Mapping) else records
        if not self.archive_ready:
            self.archival_queue.extend(records)
            return

        def _add_to_session(sess, rec):
            table_name = rec['table_name']
            table_orm = self.orm.get(table_name)
            if not table_orm:
                _log.warning(f'Attempted to archive data to non-existent table: {table_name}')
                # Early return so we don't attempt to call a NoneType (regression fix for unit test failure)
                return
            _log.debug(rec['data'])
            record_object = table_orm(**self._normalize_data(table_name, rec['data']))
            sess.add(record_object)

        with Session(self.engine) as session:
            while self.archival_queue:
                _add_to_session(session, self.archival_queue.pop())
            for record in records:
                _add_to_session(session, record)
            # TODO: Error handling, if this fails we should end up with everything still in the archival_queue.
            session.commit()

    def upsert_data(self, table_name: str, data: Mapping, match_columns: Sequence[str]) -> dict:
        """Insert a row or update the row identified by match_columns."""
        table_orm = self._get_orm(table_name)
        normalized = self._normalize_data(table_name, data)
        filters = {column: normalized[column] for column in match_columns}

        with Session(self.engine) as session:
            record = session.execute(select(table_orm).filter_by(**filters)).scalar_one_or_none()
            if record is None:
                record = table_orm(**normalized)
                session.add(record)
            else:
                for key, value in normalized.items():
                    setattr(record, key, value)
            session.commit()
            session.refresh(record)
            return self._record_to_dict(table_name, record)

    def query_data(self, table_name: str, conditions: Mapping = None, order_by: Sequence = None,
                   limit: int = None, offset: int = None) -> list[dict]:
        """Query a registered table using simple equality and range conditions."""
        table_orm = self._get_orm(table_name)
        table = self.tables[table_name]
        statement = select(table_orm)

        for column_name, condition in (conditions or {}).items():
            column = table.c[column_name]
            if isinstance(condition, Mapping):
                for operator, value in condition.items():
                    value = self._normalize_value(column, value)
                    comparisons = {
                        'eq': lambda: column == value,
                        'ne': lambda: column != value,
                        'gt': lambda: column > value,
                        'gte': lambda: column >= value,
                        'lt': lambda: column < value,
                        'lte': lambda: column <= value,
                    }
                    try:
                        statement = statement.where(comparisons[operator]())
                    except KeyError as exc:
                        raise ValueError(f'Unsupported query operator: {operator}') from exc
            else:
                statement = statement.where(column == self._normalize_value(column, condition))

        for item in order_by or []:
            column_name, direction = item if isinstance(item, (list, tuple)) else (item, 'asc')
            column = table.c[column_name]
            statement = statement.order_by(column.desc() if str(direction).lower() == 'desc' else column.asc())
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)

        with Session(self.engine) as session:
            return [self._record_to_dict(table_name, row) for row in session.scalars(statement)]

    def _get_orm(self, table_name: str):
        table_orm = self.orm.get(table_name)
        if table_orm is None:
            raise KeyError(f'Unknown table: {table_name}')
        return table_orm

    def _normalize_data(self, table_name: str, data: Mapping) -> dict:
        table = self.tables.get(table_name)
        if table is None:
            return dict(data)
        return {
            key: self._normalize_value(table.c[key], value)
            for key, value in data.items()
        }

    @staticmethod
    def _normalize_value(column, value):
        if value is None:
            return None
        if isinstance(column.type, DateTime) and isinstance(value, str):
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        if isinstance(column.type, Uuid) and isinstance(value, str):
            return UUID(value)
        return value

    def _record_to_dict(self, table_name: str, record) -> dict:
        return {
            column.name: getattr(record, column.name)
            for column in self.tables[table_name].columns
        }


# TODO: Move to TransactiveNodeAgent.
class VolttronDataManager(DataManager):
    def __init__(self, parent, historian_topic: str = 'TNS/Archive', historian_vip: str = 'tns.historian'):
        # NOTE: Parent is assumed to be a VOLTTRON Agent.
        super(VolttronDataManager, self).__init__(parent)
        # Store reference used by tests and internal methods
        self.historian_topic = historian_topic
        self.historian_vip = historian_vip

    def init_archive(self):
        # TODO: Should there be a key validation in header to create tables?
        # TODO: Should this really be using RPC?
        # TODO: Should it instead expose an RPC to allow the historian to collect tables?
        message = json.dumps(self.definitions)
        self.parent.rpc.call(self.historian_vip, 'create_tables', message)

    def archive_data(self, records: Mapping|Sequence):
        # TODO: This should use self.parent.vip.pubsub.publish directly.  Does anything need changed?
        # TODO: This should use self.parent.vip.pubsub.publish directly.  Does anything need changed?
        self.parent.publish(self.historian_topic, message=records)


# # TODO: This should end up elsewhere, but for now, a record of how to initialize a local db:
# def make_local_db():
#     from tent.utils.data_manager_new import LocalDataManager
#     from tent.utils.database_defs import all_tables
#     from tent.transactive_node import TransactiveNode
#     tn = TransactiveNode()
#     dm = LocalDataManager(tn, db_params={'db_type': 'sqlite', 'url': 'sqlite:///testent.sqlite'})
#     definitions = all_tables()
#     dm.add_tables(definitions)
#     dm.init_archive()
#     return dm


