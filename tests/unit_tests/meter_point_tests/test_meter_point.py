"""MeterPoint readings, the pushed and the pulled kinds."""
import logging
from datetime import datetime, timedelta, timezone

import pytest

from tent.enumerations import MeasurementType, MeasurementUnit
from tent.meter_point import MeterPoint
from tent.meter_point.pull_meter_point import PullMeterPoint
from tent.meter_point.push_meter_point import PushMeterPoint
from tent.utils.timer import Timer
from tests.mock_tn import MockTN

T0 = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


class RecordingDataManager:
    def __init__(self):
        self.records = []

    def archive_data(self, records):
        self.records.extend([records] if isinstance(records, dict) else records)

    def add_tables(self, definitions):
        return None

    def init_archive(self):
        return None


@pytest.fixture
def tn():
    node = MockTN()
    node._data_manager = RecordingDataManager()
    return node


def readings(tn):
    return [(r['data']['value'], r['data']['reading_time']) for r in tn.data_manager.records
            if r['table_name'] == 'meter_reading']


# ---------------------------------------------------------------------------------------------------------------- base

def test_set_meter_value_scales_stamps_and_archives_the_reading(tn):
    meter = MeterPoint(tn, name='Main', measurement_type='PowerReal', measurement_unit='kW', scale_factor=-1.0)
    meter.set_meter_value(12.5, T0)
    assert meter.current_measurement == -12.5 and meter.last_updated == T0
    assert readings(tn) == [(-12.5, T0)]
    assert meter.measurement_type is MeasurementType.PowerReal and meter.measurement_unit is MeasurementUnit.kW


def test_mean_measurement_covers_the_measurement_interval_only(tn, monkeypatch):
    meter = MeterPoint(tn, name='Main', measurement_interval=600)
    for minutes, value in ((0, 10.0), (5, 20.0), (9, 30.0)):
        meter.set_meter_value(value, T0 + timedelta(minutes=minutes))
    monkeypatch.setattr(Timer, 'now', classmethod(lambda cls, tz=None: T0 + timedelta(minutes=10)))
    assert meter.mean_measurement == 20.0, 'all three readings fall in the last ten minutes'
    monkeypatch.setattr(Timer, 'now', classmethod(lambda cls, tz=None: T0 + timedelta(minutes=16)))
    assert meter.mean_measurement == 30.0, 'only the reading at T0 + 9 min is inside the window'
    monkeypatch.setattr(Timer, 'now', classmethod(lambda cls, tz=None: T0 + timedelta(minutes=40)))
    assert meter.mean_measurement is None, 'nothing in the last ten minutes'


# ---------------------------------------------------------------------------------------------------------------- push

class PointMeter(PushMeterPoint):
    def handle_subscription(self, time_received, subscribe_key, message):
        self.set_meter_value(message['kW'], time_received)


def test_push_meter_point_registers_its_subscription_and_records_what_arrives(tn):
    meter = PointMeter(subscribe_key='METERS', transactive_node=tn, name='Pushed')
    assert tn.subscriptions == {'METERS': meter.handle_subscription}
    meter.handle_subscription(T0, 'METERS', {'kW': 7.0})
    assert readings(tn) == [(7.0, T0)] and meter.current_measurement == 7.0


def test_push_meter_point_needs_a_handler():
    with pytest.raises(TypeError):
        PushMeterPoint(subscribe_key='METERS', transactive_node=MockTN(), name='Abstract')


# ---------------------------------------------------------------------------------------------------------------- pull

class ScriptedMeter(PullMeterPoint):
    """read_meter returns the next scripted reading; a BaseException subclass in the script is raised."""

    def __init__(self, script, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.script = list(script)
        self.reads = 0

    def read_meter(self):
        self.reads += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_pull_meter_point_needs_read_meter():
    with pytest.raises(TypeError):
        PullMeterPoint(transactive_node=MockTN(), name='Abstract')


def test_first_poll_reads_at_once_and_then_every_data_period(tn):
    meter = ScriptedMeter([1.0, 2.0, 3.0], transactive_node=tn, name='Pulled', data_period_seconds=10)
    assert meter.due(T0) and meter.poll(T0) is True
    assert meter.next_read_time == T0 + timedelta(seconds=10)
    assert meter.poll(T0 + timedelta(seconds=5)) is False and meter.reads == 1, 'not due yet'
    assert meter.poll(T0 + timedelta(seconds=10)) is True
    assert meter.poll(T0 + timedelta(seconds=25)) is True, 'a late poll reads and reschedules from the poll time'
    assert meter.next_read_time == T0 + timedelta(seconds=35)
    assert readings(tn) == [(1.0, T0), (2.0, T0 + timedelta(seconds=10)), (3.0, T0 + timedelta(seconds=25))]
    assert meter.current_measurement == 3.0


def test_poll_defaults_to_the_node_clock(tn, monkeypatch):
    meter = ScriptedMeter([5.0], transactive_node=tn, name='Pulled', data_period_seconds=10)
    monkeypatch.setattr(Timer, 'now', classmethod(lambda cls, tz=None: T0))
    assert meter.poll() is True
    assert readings(tn) == [(5.0, T0)] and meter.next_read_time == T0 + timedelta(seconds=10)


def test_a_source_that_reports_its_own_time_is_stored_at_that_time(tn):
    reported = T0 - timedelta(seconds=3)
    meter = ScriptedMeter([(4.0, reported)], transactive_node=tn, name='Pulled', scale_factor=2.0)
    meter.poll(T0)
    assert readings(tn) == [(8.0, reported)] and meter.last_updated == reported


def test_a_reading_of_none_records_nothing_and_waits_for_the_next_period(tn):
    meter = ScriptedMeter([None, 6.0], transactive_node=tn, name='Pulled', data_period_seconds=10)
    assert meter.poll(T0) is False and readings(tn) == []
    assert meter.poll(T0 + timedelta(seconds=1)) is False and meter.reads == 1, 'None does not shorten the period'
    assert meter.poll(T0 + timedelta(seconds=10)) is True and readings(tn) == [(6.0, T0 + timedelta(seconds=10))]


def test_a_failing_read_is_logged_and_retried_next_period(tn, caplog):
    meter = ScriptedMeter([ConnectionError('modbus timeout'), 9.0], transactive_node=tn, name='Flaky',
                          data_period_seconds=10)
    with caplog.at_level(logging.ERROR, logger='tent.meter_point.pull_meter_point'):
        assert meter.poll(T0) is False
    assert any('Flaky' in r.message and r.exc_info for r in caplog.records), 'the traceback is logged'
    assert meter.poll(T0 + timedelta(seconds=10)) is True and readings(tn) == [(9.0, T0 + timedelta(seconds=10))]


def test_poll_requires_an_aware_time(tn):
    meter = ScriptedMeter([1.0], transactive_node=tn, name='Pulled')
    with pytest.raises(ValueError, match='now'):
        meter.poll(datetime(2026, 10, 31, 12, 0))
