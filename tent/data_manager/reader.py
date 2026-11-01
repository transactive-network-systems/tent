"""
Read side of the TENT archive: open an existing archive (SQLite file, PostgreSQL, ...) without a running
TransactiveNode and answer the canonical questions the analysis tool, the historian and TENT itself ask.

``open_archive(url)`` registers every table TENT knows how to write (so column types decode correctly), reflects any
table it does not know (tables created by agent-defined component classes, a historian's own tables) and returns a
``TentArchive``: the library's LocalDataManager, read-only, with TENT's queries. Plain SQLAlchemy Core; results are
lists of dicts. No pandas in TENT.
"""
import logging
from datetime import datetime
from typing import Iterable, Sequence

from sqlalchemy import and_, func, select

from archivable import LocalDataManager

from tent.data_manager import archive_schema, known_archive_definitions, known_enumerations

_log = logging.getLogger(__name__)


def open_archive(url: str, reflect_unknown: bool = True) -> 'TentArchive':
    """
    Open an existing TENT archive for reading. Every table TENT knows is registered from its definition and, if
    `reflect_unknown`, any other table in the database is reflected as-is. An archive of another archive schema version
    is opened with a warning (tables whose definitions changed may not read correctly).
    """
    return TentArchive.from_url(url, known_archive_definitions(), known_enumerations(), schema=archive_schema(),
                                reflect_unknown=reflect_unknown)


class TentArchive(LocalDataManager):
    """A LocalDataManager over a TENT archive with the canonical queries over TENT's tables."""

    def runs(self) -> list[dict]:
        """One row per TransactiveNode run (process start), oldest first."""
        tn = self.tables['transactive_node']
        return self.execute(select(tn).order_by(tn.c.created))

    def components(self, tn_id=None) -> list[dict]:
        """
        Every component with its concrete type: `type` is the deepest extension table holding the component's id, and
        that table's columns (and its ancestors') are merged into the row.
        """
        return self.typed_rows('component', {'tn_id': tn_id} if tn_id is not None else None)

    def market_series(self, tn_id=None) -> list[dict]:
        return [c for c in self.components(tn_id) if 'market_series' in self.table_chain(c['type'])]

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
