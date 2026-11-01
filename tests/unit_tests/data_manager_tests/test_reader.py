"""open_archive / TentArchive: open an existing archive without a node and answer the canonical queries."""
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from tent.containers.sd_curve import SDMeasurement
from tent.containers.vertex import Vertex
from tent.data_manager import ARCHIVE_SCHEMA_VERSION
from tent.data_manager.reader import TentArchive, open_archive
from tent.enumerations import MarketState, MeasurementType
from tests.mock_tn import MockTN

SAMPLE_CONFIG = Path(__file__).resolve().parents[3] / 'sample_configs' / 'tn.campus.json'


@pytest.fixture(scope='module')
def archive(tmp_path_factory):
    """A database written by a configured node plus a few values, curves, and one table TENT knows nothing about."""
    url = f'sqlite:///{tmp_path_factory.mktemp("reader") / "archive.sqlite"}'
    with open(SAMPLE_CONFIG) as f:
        configs = json.load(f)
    configs['data_manager']['db_params']['url'] = url
    configs['actor_id'] = 'campus-A'
    tn = MockTN()
    tn.configure_node(configs)
    for _ in range(2):
        tn.step()
    market = tn.market_series[0].newest_market
    ti = market.time_intervals[0]
    market.marginal_prices.update(ti, 0.05)
    market.marginal_prices.update(ti, 0.07)             # second version of the same value
    market.marginal_prices.update(market.time_intervals[1], 0.06)
    m = SDMeasurement(market, MeasurementType.TestVertex)
    m.update(ti, [Vertex(price=0.1, quantity=0.0), Vertex(price=0.2, quantity=50.0)])
    m.update(ti, [Vertex(price=0.3, quantity=80.0)])  # second version with three vertices
    with tn.data_manager.engine.begin() as conn:
        conn.execute(text('CREATE TABLE agent_extra (id INTEGER PRIMARY KEY, note VARCHAR(20))'))
        conn.execute(text("INSERT INTO agent_extra VALUES (1, 'hello')"))
    return {'url': url, 'tn': tn, 'market': market, 'interval': ti, 'curve_measurement': m}


@pytest.fixture(scope='module')
def reader(archive):
    return open_archive(archive['url'])


def test_opens_known_and_unknown_tables(reader, archive):
    assert reader.read_only and isinstance(reader, TentArchive)
    assert {'transactive_node', 'market', 'measurement_value', 'sd_curve', 'vertex'} <= set(reader.tables)
    assert 'agent_extra' in reader.tables, 'tables TENT does not know are reflected'
    assert reader.execute(reader.select('agent_extra')) == [{'id': 1, 'note': 'hello'}]
    assert reader.has_table('market') and not reader.has_table('matching_engine')  # known but never created
    assert reader.schema_version == ARCHIVE_SCHEMA_VERSION, 'a node run writes the archive schema row'
    with pytest.raises(RuntimeError):
        reader.archive_data({'table_name': 'component', 'data': {}})


def test_runs_and_components(reader, archive):
    runs = reader.runs()
    assert len(runs) == 1 and runs[0]['id'] == archive['tn'].id and runs[0]['actor_id'] == 'campus-A'
    components = reader.components(tn_id=runs[0]['id'])
    assert components and all(c['type'] != 'component' for c in components), 'every component resolves to a concrete table'
    types = {c['type'] for c in components}
    assert 'market_series' in types and 'auction' in types, 'markets are typed by their deepest table'
    series = next(c for c in components if c['type'] == 'market_series')
    assert 'market_class' in series and series['market_class'] == 'Auction', 'extension columns are merged in'


def test_markets_and_state_changes(reader, archive):
    markets = reader.markets()
    assert len(markets) >= 2
    assert all(m['series_name'] and m['market_class'] == 'Auction' for m in markets)
    assert markets == sorted(markets, key=lambda m: m['delivery_start_time'])
    changes = reader.state_changes(market_ids=[m['id'] for m in markets])
    assert changes and all(c['state'] in set(MarketState) for c in changes)
    per_market = {}
    for c in changes:
        per_market.setdefault(c['market_id'], []).append(c['state'])
    assert all(states[0] in (MarketState.Initial, MarketState.Inactive) for states in per_market.values())
    assert archive['market'].id in per_market
    intervals = reader.intervals(market_ids=[archive['market'].id])
    assert len(intervals) == archive['market'].series.intervals_to_clear


def test_values_latest_and_history(reader, archive):
    market, ti = archive['market'], archive['interval']
    headers = reader.measurements(source_ids=[market.id], measurement_types=[MeasurementType.MarginalPrice])
    assert len(headers) == 1 and headers[0]['source_name'] == market.name
    mid = headers[0]['id']
    ti2 = market.time_intervals[1]
    latest = reader.values(measurement_ids=[mid])
    assert len(latest) == market.series.intervals_to_clear, 'the market pre-populates a default price per interval'
    by_interval = {v['interval_id']: v for v in latest}
    assert by_interval[ti.id]['value'] == 0.07 and by_interval[ti2.id]['value'] == 0.06
    assert by_interval[ti.id]['interval_start'] == ti.start_time, 'datetimes come back timezone-aware'
    history = [v for v in reader.values(measurement_ids=[mid], latest_only=False) if v['interval_id'] == ti.id]
    assert [v['value'] for v in history] == [market.series.default_price, 0.05, 0.07]
    assert reader.values(measurement_ids=[mid], as_of=history[1]['record_time'])
    as_of = {v['interval_id']: v['value'] for v in reader.values(measurement_ids=[mid], as_of=history[1]['record_time'])}
    assert as_of[ti.id] == 0.05
    windowed = reader.values(measurement_ids=[mid], start=ti2.start_time, end=market.time_intervals[2].start_time)
    assert [v['value'] for v in windowed] == [0.06]


def test_curves_with_vertices(reader, archive):
    m, ti = archive['curve_measurement'], archive['interval']
    latest = reader.curves(measurement_ids=[m.id])
    assert len(latest) == 1
    curve = latest[0]
    assert curve['interval_start'] == ti.start_time and curve['owner_id'] == archive['market'].id
    assert [(v['price'], v['quantity'], v['record']) for v in curve['vertices']] == [(0.1, 0.0, 0), (0.2, 50.0, 1), (0.3, 80.0, 2)]
    history = reader.curves(measurement_ids=[m.id], latest_only=False)
    assert [len(c['vertices']) for c in history] == [2, 3]
    assert reader.curves(owner_ids=[archive['market'].id], interval_ids=[ti.id])


def test_reflect_only_a_subset_of_unknown_tables(archive):
    with open_archive(archive['url']).engine.begin() as conn:
        conn.execute(text('CREATE TABLE IF NOT EXISTS agent_other (id INTEGER PRIMARY KEY)'))
    reader = open_archive(archive['url'], reflect_unknown=False)
    assert 'agent_extra' not in reader.tables and 'agent_other' not in reader.tables
    assert reader.reflect_unknown_tables(only=['agent_extra']) == ['agent_extra']
    assert 'agent_extra' in reader.tables and 'agent_other' not in reader.tables
    assert reader.execute(reader.select('agent_extra')) == [{'id': 1, 'note': 'hello'}]
    assert reader.reflect_unknown_tables(only=['agent_extra']) == [], 'already reflected: nothing added'
    with pytest.raises(Exception):
        reader.reflect_unknown_tables(only=['no_such_table'])
