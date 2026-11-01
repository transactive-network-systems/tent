"""
Read side of the TENT archive.

Opens an existing archive (SQLite file, PostgreSQL, ...) without a running TransactiveNode, registers every table TENT
knows how to write so column types (Uuid, Interval, enums) decode correctly, reflects any table it does not know (tables
created by agent-defined component classes), and answers the canonical questions the analysis tool, the historian, and
TENT itself ask. Plain SQLAlchemy Core; results are lists of dicts. No pandas in TENT.
"""
import logging
from datetime import datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import DateTime, Select, and_, func, inspect, select
from sqlalchemy.exc import ArgumentError

from tent.data_manager import ARCHIVE_SCHEMA_TABLE, ARCHIVE_SCHEMA_VERSION, archive_schema_definition

_log = logging.getLogger(__name__)


def known_archive_definitions() -> list[dict]:
    """Archive definitions of the node and of every class TENT ships (tent.registry.SHIPPED), plus the archive_schema
    table, de-duplicated by table name. This is the set ARCHIVE_SCHEMA_VERSION versions and archive_schema_fingerprint()
    hashes, so a shipped class that adds or changes a table changes the fingerprint and the version must be bumped."""
    # Imported lazily: these modules import the data manager package.
    import importlib
    from tent import registry
    from tent.transactive_node import TransactiveNode
    classes = [TransactiveNode]
    for class_name, location in sorted(registry.SHIPPED.items()):
        module_name, _, attribute = location.partition(':')
        cls = getattr(importlib.import_module(module_name), attribute)
        if hasattr(cls, 'get_archive_definitions'):
            classes.append(cls)
    definitions, seen = [], set()
    for cls in classes:
        for definition in cls.get_archive_definitions() or ():  # a class with nothing to archive returns None
            if definition['name'] not in seen:
                seen.add(definition['name'])
                definitions.append(definition)
    definitions.append(archive_schema_definition())
    return definitions

class ArchiveReader:
    """Mixin for DataManager implementations backed by a SQLAlchemy engine (self.engine, self.tables, self.definitions)."""

    read_only: bool = False

    # ---------------------------------------------------------------- opening an existing archive
    @classmethod
    def from_url(cls, url: str, reflect_unknown: bool = True):
        """
        Open an existing archive for reading. Every table TENT knows is registered from its definition (so types decode
        correctly) and, if `reflect_unknown`, any other table in the database is reflected as-is.
        """
        dm = cls(None, {'url': url})
        dm.add_tables(known_archive_definitions())
        if reflect_unknown:
            dm.reflect_unknown_tables()
        dm.read_only = True
        version = dm.schema_version
        if (version or 1) != ARCHIVE_SCHEMA_VERSION:
            _log.warning(f'Archive {url!r} was written with archive schema version {version or 1} (this TENT writes '
                         f'version {ARCHIVE_SCHEMA_VERSION}); tables whose definitions changed between the two may '
                         f'not read correctly.')
        return dm

    def archive_schema_record(self) -> dict | None:
        """The archive_schema row (version, fingerprint, tent_version, created) of the database, or None when the
        database has no archive_schema table or the table is empty: an archive written before the table existed
        (archive schema version 1), or an empty database."""
        if ARCHIVE_SCHEMA_TABLE not in self.tables or not self.has_table(ARCHIVE_SCHEMA_TABLE):
            return None
        table = self.tables[ARCHIVE_SCHEMA_TABLE]
        rows = self.execute(select(table).order_by(table.c.version.desc()))
        return rows[0] if rows else None

    @property
    def schema_version(self) -> int | None:
        """The archive schema version the database was written with; None when the database has no archive_schema
        table, which means version 1 (before 2 November 2026) or a database nothing has been written to yet."""
        record = self.archive_schema_record()
        return None if record is None else record['version']

    def reflect_unknown_tables(self, only: Iterable[str] | None = None) -> list[str]:
        """
        Reflect tables the database has but no registered definition describes (a historian's own tables, for example)
        and make them readable through query_data. `only` restricts the reflection to those table names (a name that is
        not in the database raises); by default every unknown table is reflected. Returns the names added.
        """
        metadata = self.registry.metadata
        known = set(metadata.tables)
        if only is None:
            metadata.reflect(bind=self.engine, only=lambda name, _meta: name not in known)
        else:
            wanted = [name for name in only if name not in known]
            if wanted:
                metadata.reflect(bind=self.engine, only=wanted)
        added = []
        for name, table in metadata.tables.items():
            if name in self.tables:
                continue
            self.tables[name] = table
            orm_name = ''.join(word.title() for word in name.split('_')) + 'Table'
            try:
                self.orm[name] = type(orm_name, (), {})
                self.registry.map_imperatively(self.orm[name], table)
            except ArgumentError:  # no primary key: Core access only
                self.orm.pop(name, None)
            added.append(name)
        return added

    @property
    def present_tables(self) -> set[str]:
        """Tables that actually exist in the database (known tables may be registered but never created)."""
        return set(inspect(self.engine).get_table_names())

    def has_table(self, name: str) -> bool:
        return name in self.present_tables

    # ---------------------------------------------------------------- generic access
    def select(self, table_name: str) -> Select:
        """A Select over a registered table, for callers that want to add their own joins and filters."""
        return select(self.tables[table_name])

    def execute(self, statement) -> list[dict]:
        columns = list(statement.selected_columns)
        with self.engine.connect() as connection:
            return [self.decode_row(dict(row._mapping), columns) for row in connection.execute(statement)]

    @staticmethod
    def decode_row(row: dict, columns) -> dict:
        """Archive datetimes are UTC; SQLite returns them naive, so reattach UTC for DateTime-typed columns."""
        for column in columns:
            name = getattr(column, 'name', None) or getattr(column, 'key', None)
            value = row.get(name)
            if isinstance(column.type, DateTime) and isinstance(value, datetime) and value.tzinfo is None:
                row[name] = value.replace(tzinfo=timezone.utc)
        return row

    # ---------------------------------------------------------------- inheritance (component -> extension tables)
    def _parent_table(self, name: str) -> str | None:
        return (self.definitions.get(name) or {}).get('inherits')

    def table_chain(self, name: str) -> list[str]:
        """[name, parent, grandparent, ...] following `inherits` declarations."""
        chain = [name]
        while (parent := self._parent_table(chain[-1])) is not None:
            chain.append(parent)
        return chain

    def tables_inheriting(self, root: str) -> list[str]:
        return [name for name in self.definitions if name != root and root in self.table_chain(name)]

    # ---------------------------------------------------------------- canonical queries
    def runs(self) -> list[dict]:
        """One row per TransactiveNode run (process start), oldest first."""
        tn = self.tables['transactive_node']
        return self.execute(select(tn).order_by(tn.c.created))

    def components(self, tn_id=None) -> list[dict]:
        """
        Every component with its concrete type: `type` is the deepest extension table holding the component's id, and
        that table's columns (and its ancestors') are merged into the row.
        """
        component = self.tables['component']
        statement = select(component)
        if tn_id is not None:
            statement = statement.where(component.c.tn_id == tn_id)
        rows = {row['id']: dict(row, type='component') for row in self.execute(statement)}
        present = self.present_tables
        extension_rows: dict[str, dict] = {}
        for name in self.tables_inheriting('component'):
            if name not in present:
                continue
            table = self.tables[name]
            key = list(table.primary_key.columns)[0].name  # 'id', or e.g. 'asset_id' on local_asset extensions
            extension_rows[name] = {r[key]: {k: v for k, v in r.items() if k != key} for r in self.execute(select(table))}
        depth = {name: len(self.table_chain(name)) for name in extension_rows}
        for name, by_id in extension_rows.items():
            for component_id in by_id:
                if component_id in rows and depth[name] > depth.get(rows[component_id]['type'], 1):
                    rows[component_id]['type'] = name
        for component_id, row in rows.items():
            for name in reversed(self.table_chain(row['type'])):
                if name in extension_rows and component_id in extension_rows[name]:
                    row.update(extension_rows[name][component_id])
        return list(rows.values())

    def market_series(self, tn_id=None) -> list[dict]:
        return [c for c in self.components(tn_id) if c['type'] in self.table_chain(c['type']) and
                'market_series' in self.table_chain(c['type'])]

    def markets(self, tn_id=None, series_ids: Sequence = None) -> list[dict]:
        """Markets with their series name, ordered by delivery start."""
        market, component, series = self.tables['market'], self.tables['component'], self.tables['market_series']
        series_component = component.alias('series_component')
        statement = (select(market, component.c.name, component.c.tn_id, series.c.market_class,
                            series_component.c.name.label('series_name'))
                     .join(component, component.c.id == market.c.id)
                     .join(series, series.c.id == market.c.market_series_id)
                     .join(series_component, series_component.c.id == series.c.id)
                     .order_by(market.c.delivery_start_time))
        if tn_id is not None:
            statement = statement.where(component.c.tn_id == tn_id)
        if series_ids:
            statement = statement.where(market.c.market_series_id.in_(list(series_ids)))
        return self.execute(statement)

    def state_changes(self, market_ids: Sequence = None) -> list[dict]:
        table = self.tables['market_state_change']
        statement = select(table).order_by(table.c.market_id, table.c.record_time)
        if market_ids:
            statement = statement.where(table.c.market_id.in_(list(market_ids)))
        return self.execute(statement)

    def intervals(self, market_ids: Sequence = None) -> list[dict]:
        table = self.tables['time_interval']
        statement = select(table).order_by(table.c.start_time)
        if market_ids:
            statement = statement.where(table.c.market_id.in_(list(market_ids)))
        return self.execute(statement)

    def measurements(self, source_ids: Sequence = None, measurement_types: Iterable = None) -> list[dict]:
        """Measurement headers with their source component's name."""
        measurement, component = self.tables['measurement'], self.tables['component']
        statement = (select(measurement, component.c.name.label('source_name'), component.c.tn_id)
                     .join(component, component.c.id == measurement.c.source_id, isouter=True))
        if source_ids:
            statement = statement.where(measurement.c.source_id.in_(list(source_ids)))
        if measurement_types:
            statement = statement.where(measurement.c.measurement_type.in_(list(measurement_types)))
        return self.execute(statement)

    def values(self, measurement_ids: Sequence = None, start: datetime = None, end: datetime = None,
               latest_only: bool = True, as_of: datetime = None) -> list[dict]:
        """
        measurement_value rows joined with their interval. With latest_only, only the newest version (greatest
        record_time, optionally no later than as_of) of each (measurement, interval) is returned; otherwise every
        version. start/end filter on the interval start time.
        """
        mv, ti = self.tables['measurement_value'], self.tables['time_interval']
        statement = (select(mv.c.measurement_id, mv.c.interval_id, mv.c.record_time, mv.c.value,
                            ti.c.start_time.label('interval_start'), ti.c.duration.label('interval_duration'),
                            ti.c.market_id)
                     .join(ti, ti.c.id == mv.c.interval_id))
        if latest_only:
            newest = select(mv.c.measurement_id, mv.c.interval_id, func.max(mv.c.record_time).label('record_time'))
            if as_of is not None:
                newest = newest.where(mv.c.record_time <= as_of)
            newest = newest.group_by(mv.c.measurement_id, mv.c.interval_id).subquery('newest')
            statement = statement.join(newest, and_(newest.c.measurement_id == mv.c.measurement_id,
                                                    newest.c.interval_id == mv.c.interval_id,
                                                    newest.c.record_time == mv.c.record_time))
        elif as_of is not None:
            statement = statement.where(mv.c.record_time <= as_of)
        if measurement_ids:
            statement = statement.where(mv.c.measurement_id.in_(list(measurement_ids)))
        if start is not None:
            statement = statement.where(ti.c.start_time >= start)
        if end is not None:
            statement = statement.where(ti.c.start_time < end)
        return self.execute(statement.order_by(mv.c.measurement_id, ti.c.start_time, mv.c.record_time))

    def curves(self, measurement_ids: Sequence = None, owner_ids: Sequence = None, interval_ids: Sequence = None,
               latest_only: bool = True, as_of: datetime = None) -> list[dict]:
        """
        sd_curve versions with their vertex rows attached under 'vertices' (ordered by record). With latest_only, one
        version per curve id (the newest, optionally no later than as_of); otherwise every version.
        """
        sd, vx, ti = self.tables['sd_curve'], self.tables['vertex'], self.tables['time_interval']
        statement = (select(sd, ti.c.start_time.label('interval_start'), ti.c.market_id)
                     .join(ti, ti.c.id == sd.c.time_interval_id, isouter=True))
        if latest_only:
            newest = select(sd.c.id, func.max(sd.c.record_time).label('record_time'))
            if as_of is not None:
                newest = newest.where(sd.c.record_time <= as_of)
            newest = newest.group_by(sd.c.id).subquery('newest')
            statement = statement.join(newest, and_(newest.c.id == sd.c.id, newest.c.record_time == sd.c.record_time))
        elif as_of is not None:
            statement = statement.where(sd.c.record_time <= as_of)
        if measurement_ids:
            statement = statement.where(sd.c.measurement_id.in_(list(measurement_ids)))
        if owner_ids:
            statement = statement.where(sd.c.owner_id.in_(list(owner_ids)))
        if interval_ids:
            statement = statement.where(sd.c.time_interval_id.in_(list(interval_ids)))
        curves = self.execute(statement.order_by(sd.c.id, sd.c.record_time))
        if not curves:
            return curves
        versions = {(c['id'], c['record_time']): c for c in curves}
        for c in curves:
            c['vertices'] = []
        vertex_rows = self.execute(select(vx).where(vx.c.curve_id.in_(list({c['id'] for c in curves})))
                                   .order_by(vx.c.curve_id, vx.c.curve_record_time, vx.c.record))
        for v in vertex_rows:
            curve = versions.get((v['curve_id'], v['curve_record_time']))
            if curve is not None:
                curve['vertices'].append(v)
        return curves
