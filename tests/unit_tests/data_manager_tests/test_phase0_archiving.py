"""Phase 0 archive coverage: node row, market rows, state changes, versioned curves with vertex rows."""
import json
from datetime import timedelta
from pathlib import Path

import pytest

from tent.containers.vertex import Vertex
from tent.enumerations import MarketState, MeasurementType, SignalDirection
from tent.containers.sd_curve import SDMeasurement
from tent.utils.timer import Timer
from tests.mock_tn import MockTN

SAMPLE_CONFIG = Path(__file__).resolve().parents[3] / 'sample_configs' / 'tn.campus.json'


@pytest.fixture
def configured(tmp_path):
    Timer.simulation = False
    with open(SAMPLE_CONFIG) as f:
        configs = json.load(f)
    configs['data_manager']['db_params']['url'] = f'sqlite:///{tmp_path / "phase0.sqlite"}'
    configs['actor_id'] = 'campus-A'
    configs['organization_id'] = 'PNNL'
    tn = MockTN()
    tn.configure_node(configs)
    return tn


def test_node_row_carries_identity_and_simulation_anchor(configured):
    rows = configured.data_manager.query_data('transactive_node')
    assert len(rows) == 1
    row = rows[0]
    assert row['id'] == configured.id
    assert row['actor_id'] == 'campus-A' and row['organization_id'] == 'PNNL'
    assert row['created'] is not None and row['simulation_start_time'] is not None
    assert row['tz'] == configured.tz


def test_markets_and_initial_states_are_archived(configured):
    dm = configured.data_manager
    markets = dm.query_data('market')
    assert len(markets) == len(configured.market_series), 'one initial market per series'
    market_ids = {m['id'] for m in markets}
    intervals = dm.query_data('time_interval')
    assert intervals and all(ti['market_id'] in market_ids for ti in intervals), 'no dangling interval references'
    states = dm.query_data('market_state_change')
    assert {(s['market_id'], s['state']) for s in states} == {(m, MarketState.Initial) for m in market_ids}


def test_state_transitions_append_rows(configured):
    dm = configured.data_manager
    before = len(dm.query_data('market_state_change'))
    for _ in range(3):
        configured.step()
    after = dm.query_data('market_state_change')
    assert len(after) > before
    assert {MarketState.Initial, MarketState.Inactive} <= {s['state'] for s in after}
    # Append-only: one row per (market, record_time), never updated in place.
    assert len({(s['market_id'], s['record_time']) for s in after}) == len(after)


def test_curve_versions_carry_vertex_rows(configured):
    dm = configured.data_manager
    market = configured.market_series[0].newest_market
    ti = market.time_intervals[0]
    m = SDMeasurement(market, MeasurementType.TestVertex)
    m.update(ti, [Vertex(price=0.1, quantity=0.0), Vertex(price=0.2, quantity=50.0)])
    m.update(ti, [Vertex(price=0.3, quantity=80.0)])
    curve = m.get_by_interval(ti)
    versions = [c for c in dm.query_data('sd_curve') if c['id'] == curve.id]
    assert len(versions) == 2, 'each update is a new curve version'
    assert all(c['measurement_id'] == m.id and c['time_interval_id'] == ti.id for c in versions)
    vertices = [v for v in dm.query_data('vertex') if v['curve_id'] == curve.id]
    by_version = {}
    for v in vertices:
        by_version.setdefault(v['curve_record_time'], []).append(v)
    assert sorted(len(vs) for vs in by_version.values()) == [2, 3], 'vertex rows belong to one curve version each'
    latest = by_version[max(by_version)]
    assert sorted((v['price'], v['quantity'], v['record']) for v in latest) == [(0.1, 0.0, 0), (0.2, 50.0, 1), (0.3, 80.0, 2)]
    assert all(set(v) >= {'price', 'quantity', 'cost', 'continuity', 'uncertainty'} for v in vertices)


def consensus_market(tn):
    """Full consensus is a ConsensusMarket concept; the campus config only has auctions, so add a consensus series."""
    from tent.market.consensus_market import ConsensusMarket
    from tent.market.market_series import MarketSeries
    series = MarketSeries(transactive_node=tn, name='Consensus Series', market_class=ConsensusMarket,
                          initial_market_state=MarketState.Negotiation, duration_negotiation=3600.0,
                          duration_delivery_lead=900.0, interval_duration=3600.0, intervals_to_clear=4)
    tn.market_series.append(series)
    return series.newest_market


def test_auction_has_no_consensus(configured):
    auction = configured.market_series[0].newest_market
    assert not hasattr(auction, 'full_consensus') and not hasattr(auction, 'record_consensus')


def test_full_consensus_recorded_per_interval(configured):
    dm = configured.data_manager
    market = consensus_market(configured)
    transactive = [n for n in configured.neighbors if n.transactive]
    assert transactive, 'the campus config has transactive neighbors'
    ti0, ti1 = market.time_intervals[0], market.time_intervals[1]
    for n in transactive:
        n.convergence_flags.update(ti0, True)
        n.convergence_flags.update(ti1, False)
    market.converged = True
    market.record_consensus()
    headers = dm.query_data('measurement', conditions={'source_id': market.id})
    consensus = [h for h in headers if h['measurement_type'] is MeasurementType.FullConsensus]
    assert len(consensus) == 1
    rows = dm.query_data('measurement_value', conditions={'measurement_id': consensus[0]['id']})
    by_interval = {r['interval_id']: r['value'] for r in rows}
    assert by_interval[ti0.id] == 1.0 and by_interval[ti1.id] == 0.0
    assert all(by_interval[ti.id] == 0.0 for ti in market.time_intervals[2:]), 'intervals without flags are not consensus'
    market.converged = False
    market.record_consensus()
    rows = dm.query_data('measurement_value', conditions={'measurement_id': consensus[0]['id'], 'interval_id': ti0.id})
    assert sorted(r['value'] for r in rows) == [0.0, 1.0], 'append-only: both versions kept'


def test_transactive_signal_archives_its_curves(configured):
    from tent.containers.transactive_signal import TransactiveSignal
    dm = configured.data_manager
    market = configured.market_series[0].newest_market
    neighbor = next(n for n in configured.neighbors if n.transactive)
    ti = market.time_intervals[0]
    signal = TransactiveSignal(market=market, neighbor=neighbor, direction=SignalDirection.sent)
    signal.update(ti, [Vertex(price=0.05, quantity=-10.0), Vertex(price=0.10, quantity=40.0)], archive=False)
    signal.archive()
    rows = dm.query_data('transactive_signal', conditions={'id': signal.id})
    assert len(rows) == 1 and rows[0]['market_id'] == market.id and rows[0]['direction'] is SignalDirection.sent
    curves = dm.query_data('sd_curve', conditions={'measurement_id': signal.id})
    assert len(curves) == 1 and curves[0]['time_interval_id'] == ti.id
    vertices = dm.query_data('vertex', conditions={'curve_id': curves[0]['id']})
    assert sorted((v['price'], v['quantity']) for v in vertices) == [(0.05, -10.0), (0.10, 40.0)]


def test_shared_simulation_anchor(tmp_path):
    """Agents in one simulation share simulation_anchor_time, so their simulated clocks agree whatever their start times."""
    import time
    from datetime import datetime, timezone
    with open(SAMPLE_CONFIG) as f:
        configs = json.load(f)
    configs['data_manager']['db_params']['url'] = f'sqlite:///{tmp_path / "anchor.sqlite"}'
    anchor = datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc)
    configs['simulation_anchor_time'] = anchor.isoformat()
    first = MockTN()
    first.configure_node(json.loads(json.dumps(configs)))
    t_first = Timer.now()
    time.sleep(0.05)
    second = MockTN()
    second.configure_node(json.loads(json.dumps(configs)))  # configure_node consumes parts of the dict it is given
    t_second = Timer.now()
    assert first.simulation_anchor_time == anchor and second.simulation_anchor_time == anchor
    assert Timer.created_time == anchor
    # Both derive simulated time from the same anchor: the second node did not restart the simulated clock at its own
    # start (the config runs at 100 real seconds per simulated hour, so real configuration time shows up 36-fold).
    assert timedelta(0) <= t_second - t_first < timedelta(minutes=1)
    assert t_second - first.simulation_start_time > timedelta(hours=1), 'joined the simulation in progress'
    row = first.data_manager.query_data('transactive_node')[0]
    assert row['simulation_anchor_time'] == anchor and row['created'] != anchor


def test_bad_and_duplicate_records_are_quarantined_without_losing_the_batch(configured, caplog):  # audit T-01
    dm = configured.data_manager
    rows = dm.query_data('market_state_change')
    template = dict(rows[0])
    good = {'table_name': 'market_state_change', 'data': {**template, 'record_time': template['record_time'] + timedelta(seconds=1)}}
    unparsable = {'table_name': 'market_state_change', 'data': {**template, 'record_time': 'not a time'}}
    duplicate = {'table_name': 'market_state_change', 'data': dict(template)}
    also_good = {'table_name': 'market_state_change', 'data': {**template, 'record_time': template['record_time'] + timedelta(seconds=2)}}
    dm.archive_data([unparsable, good, duplicate, also_good])
    after = dm.query_data('market_state_change')
    assert len(after) == len(rows) + 2, 'both valid rows are written; the unparsable and duplicate ones are quarantined'
    quarantined = [r.message for r in caplog.records if 'Quarantined' in r.message]
    assert len(quarantined) == 2 and all('market_state_change' in m for m in quarantined)


def test_unknown_table_is_skipped_and_other_records_written(configured):
    dm = configured.data_manager
    rows = dm.query_data('market_state_change')
    template = dict(rows[0])
    dm.archive_data([{'table_name': 'no_such_table', 'data': {'id': 1}},
                     {'table_name': 'market_state_change',
                      'data': {**template, 'record_time': template['record_time'] + timedelta(seconds=1)}}])
    assert len(dm.query_data('market_state_change')) == len(rows) + 1


def test_queue_before_archive_ready_is_unbounded_and_warns_once(caplog):  # audit T-INC-03
    from tent.data_manager import DataManager
    from tent.data_manager.local_data_manager import LocalDataManager
    ldm = LocalDataManager.__new__(LocalDataManager)  # no database: only the pre-init_archive queueing is exercised
    DataManager.__init__(ldm, parent=None, archival_buffer_length=3)
    ldm.read_only = False
    for i in range(5):
        ldm.archive_data({'table_name': 't', 'data': {'i': i}})
    assert len(ldm.archival_queue) == 5, 'nothing is dropped'
    assert [r['data']['i'] for r in ldm.archival_queue] == [0, 1, 2, 3, 4]
    assert sum('archive is not ready' in r.message for r in caplog.records) == 1
