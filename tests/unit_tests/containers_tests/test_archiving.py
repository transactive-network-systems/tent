"""deferred_archiving: containers updated inside the block are written once, when it ends, with their final value."""
from datetime import datetime, timezone
from unittest.mock import patch

from tent.containers import archiving
from tent.enumerations import MarketState
from tent.utils.timer import Timer
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


def test_a_market_tick_writes_what_it_touched_in_one_batch():
    """Market.events opens a deferral block: the rows a tick produces reach the data manager in one archive_data call."""
    class CountingDataManager(RecordingDataManager):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def archive_data(self, records):
            self.calls += 1
            super().archive_data(records)

    tn = MockTN()
    tn._data_manager = manager = CountingDataManager()
    market = make_market(tn, datetime(2017, 7, 1, tzinfo=timezone.utc), delivery_lead=3600)
    asset = LocalAsset(transactive_node=tn, name='load', cost_parameters=[0.0, 0.0, 0.0])
    market.transition_from_market_lead_to_delivery_lead = lambda: [asset.scheduled_powers.update(t, -10.0)
                                                                   for t in market.time_intervals]
    market.while_in_delivery_lead = lambda: setattr(market, '_stateIsCompleted', True)
    market.market_state = MarketState.MarketLead  # one state-change row, written outside any tick
    market._stateIsCompleted = True
    manager.calls = 0
    with patch.object(Timer, 'now', return_value=market.transition_times.delivery_lead):
        market.events()
    assert market.market_state is MarketState.DeliveryLead
    assert manager.rows('market_state_change')[-1]['state'] is MarketState.DeliveryLead, 'the state change is in the batch'
    assert len(manager.rows('measurement_value')) >= len(market.time_intervals)
    assert manager.calls == 1, f'{manager.calls} archive_data calls for {len(manager.records)} records'


def test_a_component_built_in_a_block_writes_its_measurement_headers_once():
    """A component archives the headers of its measurements with its own row (the row they refer to); a measurement
    registered for deferral by its own __init__ is then not written a second time when the block ends."""
    tn = MockTN()
    tn._data_manager = RecordingDataManager()
    with deferred_archiving():
        asset = LocalAsset(transactive_node=tn, name='load', cost_parameters=[0.0, 0.0, 0.0])
        asset.archive()  # as TransactiveNode.configure_node does for every component it builds
        assert tn.data_manager.rows('measurement') == [], 'nothing is written inside the block'
    headers = tn.data_manager.rows('measurement')
    assert headers, 'the asset has measurements'
    assert len({h['id'] for h in headers}) == len(headers), 'each header once'
    assert all(h['source_id'] == asset.id for h in headers)


def test_an_archivable_with_dependent_records_keeps_them_when_deferred():
    """Entity.archive passes its meter-point and information-service rows with its own; deferred, they are written with
    it, not lost."""
    tn = MockTN()
    tn._data_manager = RecordingDataManager()
    asset = LocalAsset(transactive_node=tn, name='load', cost_parameters=[0.0, 0.0, 0.0])
    with deferred_archiving():
        asset.archive([{'table_name': 'entity_information_service',
                        'data': {'entity_id': asset.id, 'information_service_id': 'ism-1'}}])
        assert tn.data_manager.rows('entity_information_service') == []
    assert tn.data_manager.rows('entity_information_service') == [{'entity_id': asset.id,
                                                                   'information_service_id': 'ism-1'}]
    assert tn.data_manager.rows('local_asset'), 'the asset row went with it'
