import logging

from datetime import datetime, time, timedelta, timezone
from uuid import UUID

from sqlalchemy import create_engine, select, Interval
from sqlalchemy.schema import sort_tables
from sqlalchemy.orm import Session
from sqlalchemy.sql.sqltypes import DateTime, Uuid, Boolean, Float, Time
from typing import Mapping, Sequence

from tent.data_manager import DataManager
from tent.data_manager.reader import ArchiveReader
from tent.utils.helpers import parse_timedelta, require_aware

_log = logging.getLogger(__name__)


class LocalDataManager(ArchiveReader, DataManager):
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
        # arch_debug = []
        # for i in self.archival_queue:
        #     i['data'] = {k: str(v) for k, v in i['data'].items()}
        #     arch_debug.append(i)
        # _log.info(f'@@@@@ ARCHIVAL QUEUE HAS: {  arch_debug }')
        self.archive_data()

    def archive_data(self, records: Mapping|Sequence = None):
        # TODO: Would it be better to move db write logic into a periodic and always append here to the archival_queue?
        if self.read_only:
            raise RuntimeError('This data manager was opened for reading (LocalDataManager.from_url); it does not archive.')
        records = records if records else []
        records = [records] if isinstance(records, Mapping) else records
        #_log.info(f'@@@@@@@ Archiving data: {records}')
        if not self.archive_ready:
            self.archival_queue.extend(records)
            return

        # Drain the queue oldest-first (self-references such as market.prior_market_id point backwards in time), then
        # insert table by table in foreign-key dependency order. The ORM only orders INSERTs across mappers when
        # relationships are declared, and these imperative mappings declare none, so the order is made explicit here.
        pending = []
        while self.archival_queue:
            pending.append(self.archival_queue.popleft())
        pending.extend(records)
        by_table: dict[str, list] = {}
        for rec in pending:
            by_table.setdefault(rec['table_name'], []).append(rec)
        unknown = [name for name in by_table if name not in self.orm]
        for name in unknown:
            _log.warning(f'Attempted to archive data to non-existent table: {name}')
            by_table.pop(name)
        if not by_table:
            return
        # Records name their leaf table, and joined inheritance writes the ancestor rows (component, entity, ...) as
        # part of that insert. A table must therefore be flushed before anything that references any table in its
        # inheritance chain: rank each leaf by the earliest position of its chain in the dependency sort.
        position = {table.name: i for i, table in enumerate(sort_tables(self.registry.metadata.tables.values()))}
        def rank(name):
            chain = self.table_chain(name) if name in self.definitions else [name]
            return (min(position.get(t, len(position)) for t in chain), position.get(name, len(position)))
        order = sorted(by_table, key=rank)
        with Session(self.engine) as session:
            for table_name in order:
                table_orm = self.orm[table_name]
                for rec in by_table[table_name]:
                    # The archive is append-only: rows are inserted, never updated. Archivables whose state evolves
                    # (SDCurve, Order, MeasurementValue) carry record_time in their primary key.
                    session.add(table_orm(**self._normalize_data(table_name, rec['data'])))
                session.flush()
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
        orm = self._get_orm(table_name)

        if orm is None:
            return dict(data)
        return {
            key: self._normalize_value(getattr(orm, key), value)
            for key, value in data.items()
        }

    @staticmethod
    def _normalize_value(column, value):
        if value is None:
            return None
        if isinstance(column.type, Boolean):
            if value in ['T', 'True', 't', 'true', 'TRUE', '1', '1.0', 1, 1.0, 'y', 'Y', 'yes', 'Yes', 'YES']:
                return True
            elif value in ['F', 'False', 'FALSE', 'f', 'false', '0', '0.0', 0, 0.0, 'no', 'No', 'NO']:
                return False
        if isinstance(column.type, Float) and isinstance(value, bool):
            # Boolean-valued measurements (convergence flags, engagement schedules) share measurement_value.value; the
            # measurement header's unit says they are booleans. PostgreSQL will not coerce bool to double precision.
            return 1.0 if value else 0.0
        if isinstance(column.type, Time) and isinstance(value, str):
            return time.fromisoformat(value.replace('Z', '+00:00'))
        if isinstance(column.type, DateTime):
            if isinstance(value, str):
                value = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if isinstance(value, datetime):
                # One convention in the archive: UTC. A naive value would be read back as UTC by SQLite and as the
                # session zone by PostgreSQL, so it is refused rather than guessed at.
                return require_aware(value, f'{column.name}').astimezone(timezone.utc)
            return value
        if isinstance(column.type, Uuid) and isinstance(value, str):
            return UUID(value)
        if isinstance(column.type, Interval) and not isinstance(value, timedelta):
            return parse_timedelta(value)  # seconds
        return value

    def _record_to_dict(self, table_name: str, record) -> dict:
        return self.decode_row({column.name: getattr(record, column.name) for column in self.tables[table_name].columns},
                               self.tables[table_name].columns)
