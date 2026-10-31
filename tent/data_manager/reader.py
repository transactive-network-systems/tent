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

_log = logging.getLogger(__name__)


def known_archive_definitions() -> list[dict]:
    """Archive definitions of every component class in TENT, de-duplicated by table name."""
    # Imported lazily: these modules import the data manager package.
    from tent.transactive_node import TransactiveNode
    from tent.market.auction import Auction
    from tent.market.consensus_market import ConsensusMarket
    from tent.market.matching_engine import MatchingEngine
    from tent.local_asset import LocalAsset
    from tent.local_asset.model_frame.model_frame_asset import ModelFrameAsset
    from tent.local_asset.solar_pv_resource_model import SolarPvResource
    from tent.local_asset.openloop_load_predictor import OpenLoopLoadPredictor
    from tent.neighbor import Neighbor
    from tent.meter_point import MeterPoint
    from tent.information_service_model import InformationServiceModel
    from tent.information_service_model.temperature_forecast_model import TemperatureForecastModel
    classes = [TransactiveNode, Auction, ConsensusMarket, MatchingEngine, LocalAsset,
               ModelFrameAsset, SolarPvResource, OpenLoopLoadPredictor, Neighbor, MeterPoint, InformationServiceModel,
               TemperatureForecastModel]
    definitions, seen = [], set()
    for cls in classes:
        for definition in cls.get_archive_definitions():
            if definition['name'] not in seen:
                seen.add(definition['name'])
                definitions.append(definition)
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
        return dm

    def reflect_unknown_tables(self) -> list[str]:
        metadata = self.registry.metadata
        known = set(metadata.tables)
        metadata.reflect(bind=self.engine, only=lambda name, _meta: name not in known)
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
