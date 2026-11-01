"""deferred_archiving: containers updated inside the block are written once, when it ends, with their final value."""
from datetime import datetime, timezone

from tent.containers import archiving
from tent.containers.archiving import deferred_archiving
from tent.containers.sd_curve import SDCurve
from tent.containers.vertex import Vertex
from tent.local_asset import LocalAsset
from tests.mock_tn import MockTN
from tests.stubs import make_market


class RecordingDataManager:
    def __init__(self):
        self.records = []

    def archive_data(self, records):
        self.records.extend(records if isinstance(records, (list, tuple)) else [records])

    def rows(self, table):
        return [r['data'] for r in self.records if r['table_name'] == table]


def asset_and_interval():
    tn = MockTN()
    tn._data_manager = RecordingDataManager()
    market = make_market(tn, datetime(2017, 7, 1, tzinfo=timezone.utc))
    asset = LocalAsset(transactive_node=tn, name='load', cost_parameters=[0.0, 0.0, 0.0])
    return tn, asset, market.time_intervals[0]


def test_values_updated_in_the_block_are_written_once_with_the_last_value():
    tn, asset, ti = asset_and_interval()
    before = len(tn.data_manager.rows('measurement_value'))
    with deferred_archiving():
        assert archiving.is_deferring()
        for power in (-10.0, -20.0, -30.0):
            asset.scheduled_powers.update(ti, power)
        assert len(tn.data_manager.rows('measurement_value')) == before, 'nothing is written inside the block'
    assert not archiving.is_deferring()
    written = tn.data_manager.rows('measurement_value')[before:]
    assert len(written) == 1 and written[0]['value'] == -30.0


def test_curves_are_written_once_with_their_final_vertices():
    tn, asset, ti = asset_and_interval()
    with deferred_archiving():
        asset.active_vertices.update(ti, [Vertex(0.05, 0.0, -10.0)])
        asset.active_vertices.update(ti, [Vertex(0.05, 0.0, -10.0), Vertex(0.07, 0.0, -5.0)], replace=True)
        assert tn.data_manager.rows('sd_curve') == [] and tn.data_manager.rows('vertex') == []
    curves = tn.data_manager.rows('sd_curve')
    assert len(curves) == 1, 'the replaced curve was never written; the final one is'
    assert sorted(v['quantity'] for v in tn.data_manager.rows('vertex')) == [-10.0, -5.0]


def test_outside_a_block_every_update_is_written_as_before():
    tn, asset, ti = asset_and_interval()
    before = len(tn.data_manager.rows('measurement_value'))
    asset.scheduled_powers.update(ti, -10.0)
    asset.scheduled_powers.update(ti, -20.0)
    assert len(tn.data_manager.rows('measurement_value')) == before + 2


def test_nested_blocks_write_when_the_outermost_ends_and_a_failure_still_writes():
    tn, asset, ti = asset_and_interval()
    before = len(tn.data_manager.rows('measurement_value'))
    with deferred_archiving():
        with deferred_archiving():
            asset.scheduled_powers.update(ti, -10.0)
        assert len(tn.data_manager.rows('measurement_value')) == before, 'the inner block hands over to the outer'
        asset.scheduled_powers.update(ti, -15.0)
    assert [r['value'] for r in tn.data_manager.rows('measurement_value')[before:]] == [-15.0]
    try:
        with deferred_archiving():
            asset.scheduled_powers.update(ti, -99.0)
            raise RuntimeError('a balancing pass that raised')
    except RuntimeError:
        pass
    assert tn.data_manager.rows('measurement_value')[-1]['value'] == -99.0 and not archiving.is_deferring()
