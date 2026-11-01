"""MeterPoint readings and their archive rows."""
import pytest

from archivable import LocalDataManager

import tent.data_manager
from tent.data_manager import known_archive_definitions
from tent.meter_point import MeterPoint
from tent.utils.timer import Timer
from tests.mock_tn import MockTN


@pytest.fixture
def node(tmp_path):
    Timer.simulation = False
    tn = MockTN()
    tn._data_manager = tent.data_manager.prepare(LocalDataManager(tn, {'url': f'sqlite:///{tmp_path / "meters.sqlite"}'}))
    tn.data_manager.add_tables(known_archive_definitions())
    tn.archive()
    tn.data_manager.init_archive()
    return tn


def test_two_readings_with_one_timestamp_are_both_kept(node):  # audit T-33
    meter = MeterPoint(transactive_node=node, name='Main', measurement_type='PowerReal', measurement_unit='kW')
    meter.archive()
    when = Timer.now().replace(microsecond=0)
    meter.set_meter_value(100.0, when)
    meter.set_meter_value(104.0, when)  # a repeated publication or a device with coarse timestamps
    rows = node.data_manager.query_data('meter_reading', {'meter_id': meter.id})
    assert sorted(r['value'] for r in rows) == [100.0, 104.0], 'versioned by record time, neither reading is lost'
    assert all(r['reading_time'] == when for r in rows) and len({r['record_time'] for r in rows}) == 2
    assert meter.buffer.get_values() == [100.0, 104.0]
    assert meter.mean_measurement == 102.0, 'both readings are in the current measurement interval'
