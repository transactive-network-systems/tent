"""
The archive says which vertex of a signal curve is the balance point: sd_curve.balance_point_record (archive schema
version 6) is the record number, among the vertex rows of that curve version, of the vertex the signal proposes as the
exchange with the neighbor, the same index the wire's balance_point carries. Until it existed the exchange a signal
proposed could not be read back from the archive (the vertex rows are numbered in price order, so record 0 is only the
cheapest vertex), and an analysis tool had to read the curves at the cleared price instead, which is not TENT's
convergence test.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text

from archivable import ARCHIVE_SCHEMA_TABLE, LocalDataManager

from tent.containers.sd_curve import SDMeasurement
from tent.containers.vertex import Vertex
from tent.data_manager import known_archive_definitions, prepare
from tent.data_manager.reader import open_archive
from tent.enumerations import MeasurementType, SignalDirection
from tent.market import Market
from tent.market.market_series import MarketSeries
from tent.neighbor import Neighbor
from tent.utils.timer import Timer
from tests.mock_tn import MockTN
from tests.stubs import FlexibleAsset

T0 = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
SERIES = dict(name='Day Ahead', duration_market_lead=300.0, duration_delivery_lead=300.0, interval_duration=3600.0,
              intervals_to_clear=3, market_clearing_interval=3600.0, default_price=0.05)


class RecordingDataManager:
    def __init__(self):
        self.records = []

    def archive_data(self, records):
        self.records.extend(records if isinstance(records, (list, tuple)) else [records])

    def by_table(self, name):
        return [r['data'] for r in self.records if r.get('table_name') == name]

    def curve_versions(self, measurement_id):
        """The archived versions of the curves of one measurement: (sd_curve row, its vertex rows by record), in the
        order written."""
        vertices = {}
        for row in self.by_table('vertex'):
            vertices.setdefault((row['curve_id'], row['curve_record_time']), {})[row['record']] = row
        return [(row, vertices.get((row['id'], row['record_time']), {}))
                for row in self.by_table('sd_curve') if row['measurement_id'] == measurement_id]


def build_node(name, peer, load_curve=None):
    """A node with a transactive neighbor `peer` and, with `load_curve`, a flexible load whose residual the neighbor's
    signal carries."""
    tn = MockTN(name=name)
    tn._data_manager = RecordingDataManager()
    tn.neighbors = [Neighbor(transactive_node=tn, name=peer, transactive=True, up_or_down='upstream',
                             default_vertices=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 200.0, True]],
                             minimum_power=0.0, maximum_power=200.0, loss_factor=0.0, demand_rate=0.0,
                             cost_parameters=[0.0, 0.0, 0.0])]
    if load_curve is not None:
        tn.local_assets = [FlexibleAsset(transactive_node=tn, name=f'{name} load', cost_parameters=[0.0, 0.0, 0.0],
                                         default_vertices=load_curve, minimum_power=-100.0, maximum_power=-20.0)]
    tn.market_series = [MarketSeries(transactive_node=tn, market_class=Market, **SERIES)]
    return tn


@pytest.fixture
def clock():
    Timer.simulation = False
    state = {'t': T0}
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: state['t']):
        yield state


@pytest.fixture
def pair(clock):
    """campus, with a flexible load (-100 kW at 0.04 $/kWh to -20 kW at 0.08), buying from city; the wire messages
    campus issues are captured in campus.wires and delivered to city by hand."""
    campus = build_node('campus', 'city', load_curve=[[0.04, 0.0, -100.0, True], [0.08, 0.0, -20.0, True]])
    city = build_node('city', 'campus')
    campus.wires = []
    campus.register_recipient('city', campus.wires.append)
    return campus, city


def market_of(tn):
    return tn.market_series[0].newest_market


def schedule_and_send(campus, scheduled_powers, prices):
    """Schedule the load and the city neighbor in every interval (the neighbor's power as seen from campus, positive when
    city supplies), prepare the signal by the market's rule (Market.BALANCE_POINT, 'scheduled_power') and send it. The
    sent snapshot, which is what the archive holds, is returned."""
    market, load, city = market_of(campus), campus.local_assets[0], campus.neighbors[0]
    for ti, price, power in zip(market.time_intervals, prices, scheduled_powers):
        market.marginal_prices.update(ti, price)
        city.scheduled_powers.update(ti, power)
    load.update_vertices(market)
    load.schedule_power(market)
    city.update_vertices(market)
    city.prep_transactive_signal(market)
    return city.send_transactive_signal(market, include_schedule=True)


def wire_intervals(wire):
    return {datetime.fromisoformat(k.replace('Z', '+00:00')): v for k, v in wire['intervals'].items()}


def assert_versions_match_the_wire(versions, wire, intervals_by_id, every_interval=True):
    """Every archived curve version names the vertex the wire's balance_point names, and the vertex row with that record
    number is that vertex. With every_interval, each interval of the signal has a version (a signal whose curves all
    changed); otherwise only the intervals whose curve or balance point changed have one."""
    on_the_wire = wire_intervals(wire)
    if every_interval:
        assert len(versions) == len(on_the_wire), 'one curve version per interval of the signal'
    for row, vertices in versions:
        sent = on_the_wire[intervals_by_id[row['time_interval_id']].start_time]
        assert row['balance_point_record'] == sent['balance_point']
        assert len(vertices) == len(sent['flexibility'])
        marked = vertices[row['balance_point_record']]
        on_wire = sent['flexibility'][sent['balance_point']]
        assert (marked['price'], marked['quantity']) == (on_wire['marginal_price'], on_wire['quantity'])


# ---------------------------------------------------------------- sent and received signals
def test_a_sent_signal_archives_the_wires_balance_point_in_every_interval(pair):
    campus, _ = pair
    market = market_of(campus)
    intervals = {ti.id: ti for ti in market.time_intervals}
    sent = schedule_and_send(campus, scheduled_powers=(60.0, 100.0, 20.0), prices=(0.05, 0.04, 0.08))
    wire = campus.wires[-1]
    versions = campus._data_manager.curve_versions(sent.id)
    assert_versions_match_the_wire(versions, wire, intervals)
    # The vertex named is the exchange: the power scheduled with the neighbor, in the neighbor's perspective.
    quantities = {intervals[row['time_interval_id']].start_time: vertices[row['balance_point_record']]['quantity']
                  for row, vertices in versions}
    assert quantities == {ti.start_time: -power for ti, power in zip(market.time_intervals, (60.0, 100.0, 20.0))}
    records = {row['balance_point_record'] for row, _ in versions}
    assert len(records) > 1, 'the point is not always the same record: the archive has to say which'
    assert all(row['direction'] is SignalDirection.sent for row in campus._data_manager.by_table('transactive_signal'))


def test_a_received_signal_archives_the_balance_point_the_neighbor_proposed(pair):
    campus, city = pair
    schedule_and_send(campus, scheduled_powers=(60.0, 100.0, 20.0), prices=(0.05, 0.04, 0.08))
    wire = campus.wires[-1]
    city.neighbors[0].receive_transactive_signal(None, None, wire)
    received = city.neighbors[0].last_received_signals[market_of(city)]
    assert received.direction is SignalDirection.received
    intervals = {ti.id: ti for ti in market_of(city).time_intervals}
    versions = city._data_manager.curve_versions(received.id)
    assert_versions_match_the_wire(versions, wire, intervals)
    for ti in market_of(city).time_intervals:
        row, vertices = next((r, v) for r, v in versions if r['time_interval_id'] == ti.id)
        assert vertices[row['balance_point_record']]['quantity'] == received.balance_point(ti).quantity


def test_a_balance_point_re_marked_by_the_markets_rule_is_a_new_version_and_an_unchanged_one_is_not(pair):
    campus, _ = pair
    market = market_of(campus)
    intervals = {ti.id: ti for ti in market.time_intervals}
    first = schedule_and_send(campus, scheduled_powers=(60.0, 60.0, 60.0), prices=(0.05, 0.05, 0.05))
    first_wire = campus.wires[-1]
    # The schedule moves: the market's rule marks the new scheduled powers, the curve itself does not change.
    second = schedule_and_send(campus, scheduled_powers=(80.0, 60.0, 40.0), prices=(0.05, 0.05, 0.05))
    second_wire = campus.wires[-1]
    assert_versions_match_the_wire(campus._data_manager.curve_versions(first.id), first_wire, intervals)
    second_versions = campus._data_manager.curve_versions(second.id)
    assert_versions_match_the_wire(second_versions, second_wire, intervals, every_interval=False)
    assert len(second_versions) == 2
    moved = [ti for ti in market.time_intervals
             if wire_intervals(first_wire)[ti.start_time]['balance_point']
             != wire_intervals(second_wire)[ti.start_time]['balance_point']
             or wire_intervals(first_wire)[ti.start_time]['flexibility']
             != wire_intervals(second_wire)[ti.start_time]['flexibility']]
    assert len(moved) == 2, 'the first and third intervals were re-marked; the second was not'
    unchanged = market.time_intervals[1]
    assert not any(row['time_interval_id'] == unchanged.id for row, _ in second_versions), \
        'a curve whose vertices and balance point are unchanged is not written again'
    # Re-sent as it is: a header, no curves.
    third = schedule_and_send(campus, scheduled_powers=(80.0, 60.0, 40.0), prices=(0.05, 0.05, 0.05))
    assert campus._data_manager.curve_versions(third.id) == []
    assert len(campus._data_manager.by_table('transactive_signal')) == 3


def test_the_balance_point_moving_on_the_same_vertices_is_a_new_version(pair):
    """The market's rule can move the point from one existing vertex to another without touching the vertices (it is
    reused when a vertex sits at the quantity and the price); that is a new version, since it is another exchange."""
    campus, _ = pair
    market = market_of(campus)
    ti = market.time_intervals[0]
    neighbor = campus.neighbors[0]
    sent = schedule_and_send(campus, scheduled_powers=(60.0, 60.0, 60.0), prices=(0.05, 0.05, 0.05))
    first_row, first_vertices = campus._data_manager.curve_versions(sent.id)[0]
    curve = sent.get_by_interval(ti)
    other = next(v for v in curve.vertices if v is not sent.balance_point(ti))
    sent.mark_balance_points_at_quantities({ti: (other.quantity, other.price)})
    assert sent.balance_point(ti) is other
    assert [(v.price, v.quantity) for v in curve.vertices] == \
           [(float(v['price']), float(v['quantity'])) for _, v in sorted(first_vertices.items())], 'same vertices'
    sent.archive()
    later = [row for row, _ in campus._data_manager.curve_versions(sent.id) if row['time_interval_id'] == ti.id]
    assert len(later) == 2 and later[0]['balance_point_record'] != later[1]['balance_point_record']
    assert later[1]['balance_point_record'] == list(curve.vertices).index(other)


# ---------------------------------------------------------------- curves that have no balance point
def test_curves_that_propose_no_exchange_have_no_balance_point_record(pair):
    campus, _ = pair
    market = market_of(campus)
    ti = market.time_intervals[0]
    system = SDMeasurement(market, MeasurementType.TestVertex)
    system.update(ti, [Vertex(price=0.1, quantity=0.0), Vertex(price=0.2, quantity=50.0)])
    assert campus._data_manager.curve_versions(system.id)[0][0]['balance_point_record'] is None
    assert campus._data_manager.curve_versions(system.id)[0][0]['owner_id'] == market.id
    neighbor = campus.neighbors[0]
    neighbor.update_vertices(market)
    for _, curve in neighbor.active_vertices.all:
        curve.archive()
    rows = [row for row, _ in campus._data_manager.curve_versions(neighbor.active_vertices.id)]
    assert rows and all(row['balance_point_record'] is None for row in rows), "an entity's active vertices"
    from tent.containers.transactive_signal import TransactiveSignal
    unmarked = TransactiveSignal(market=market, neighbor=neighbor, sequence=1)
    unmarked.update(ti, [Vertex(0.04, 0.0, -100.0), Vertex(0.08, 0.0, -20.0)], archive=False, replace=True)
    wire = unmarked.generate_signal()
    assert wire_intervals(wire)[ti.start_time]['balance_point'] is None
    unmarked.archive()
    assert campus._data_manager.curve_versions(unmarked.id)[0][0]['balance_point_record'] is None


# ---------------------------------------------------------------- the reader, and an archive from before the column
def _curve_rows(curve_id, record_time, balance_point_record):
    rows = [{'table_name': 'sd_curve', 'data': {'id': curve_id, 'record_time': record_time, 'measurement_id': None,
                                                  'owner_id': None, 'time_interval_id': None, 'timestamp': record_time,
                                                  'balance_point_record': balance_point_record}}]
    for record, (price, quantity) in enumerate([(0.04, -100.0), (0.05, -60.0), (0.08, -20.0)]):
        rows.append({'table_name': 'vertex', 'data': {'id': uuid.uuid4(), 'curve_id': curve_id,
                                                       'curve_record_time': record_time,
                                                       'price': price, 'quantity': quantity, 'cost': 0.0,
                                                       'continuity': True, 'uncertainty': 0.0, 'record': record}})
    return rows


def test_the_reader_returns_the_column_and_reads_an_older_archive_without_it(tmp_path):
    url = f'sqlite:///{tmp_path / "curves.sqlite"}'
    dm = prepare(LocalDataManager(None, {'url': url}))
    dm.add_tables(known_archive_definitions())
    dm.init_archive()
    curve_id = uuid.uuid4()
    dm.archive_data(_curve_rows(curve_id, T0, 1))
    curves = open_archive(url).curves(latest_only=False)
    assert len(curves) == 1 and curves[0]['balance_point_record'] == 1
    assert [v['quantity'] for v in curves[0]['vertices']] == [-100.0, -60.0, -20.0]
    assert curves[0]['vertices'][curves[0]['balance_point_record']]['quantity'] == -60.0

    # An archive written before version 6 has no such column; this TENT's reader still reads its curves, without it.
    with dm.engine.begin() as connection:
        connection.execute(text('ALTER TABLE sd_curve DROP COLUMN balance_point_record'))
        connection.execute(text(f'UPDATE {ARCHIVE_SCHEMA_TABLE} SET version = 5'))
    older = open_archive(url)
    assert older.schema_version == 5
    curves = older.curves(latest_only=False)
    assert len(curves) == 1 and 'balance_point_record' not in curves[0]
    assert [v['quantity'] for v in curves[0]['vertices']] == [-100.0, -60.0, -20.0]
