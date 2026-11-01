# Save this as test_measurement.py and run with:
#   pytest -q
#
# These tests assume the "tent" package is on your PYTHONPATH.

from datetime import datetime, timedelta

import pytest

import tent.containers.measurement as mod
from tent.containers.measurement import Measurement, MeasurementValue
from tent.enumerations import MeasurementType, MeasurementUnit


class DummySource:
    def __init__(self, id_="src-1", data_manager=None):
        self.id = id_
        self.data_manager = object() if data_manager is None else data_manager


class DummyInterval:
    def __init__(self, name, start_time, duration, market=None, id_="ti-1"):
        self.name = name
        self.start_time = start_time
        self.duration = duration
        self.market = market
        self.id = id_


@pytest.fixture(autouse=True)
def stub_archivable_and_helpers(monkeypatch):
    # Prevent side effects from base Archivable (no DB, no IDs)
    monkeypatch.setattr(mod.Archivable, "__init__", lambda self: None)
    monkeypatch.setattr(mod.Archivable, "archive", lambda self: None)

    # Use a simple weak proxy callback (no-op) to avoid unexpected side effects
    def noop_cb(_self, _name):
        def on_gc(_):  # pragma: no cover
            pass
        return on_gc

    monkeypatch.setattr(mod, "safe_weak_proxy_callback", noop_cb)


def test_measurementvalue_update_and_ops(monkeypatch):
    # Stub base get_record to be predictable when used later
    monkeypatch.setattr(mod.Archivable, "get_record", lambda self: {})

    src = DummySource()
    m = Measurement(source=src)

    ti1 = DummyInterval("I1", datetime(2024, 1, 1, 0, 0, 0), timedelta(minutes=5))
    ti2 = DummyInterval("I2", datetime(2024, 1, 1, 0, 5, 0), timedelta(minutes=5))

    mv1 = MeasurementValue(m, ti1, 10.0)
    mv2 = MeasurementValue(m, ti2, 4.0)

    # update
    mv1.update(12.0)
    assert mv1.value == 12.0

    # arithmetic
    assert mv1 + mv2 == 16.0
    assert mv1 - mv2 == 8.0
    assert mv1 * mv2 == 48.0
    assert mv1 / mv2 == 3.0
    assert mv1 // mv2 == 3.0
    assert mv1 % mv2 == 0.0

    # comparisons
    assert (mv1 == mv2) is False
    assert (mv1 != mv2) is True
    assert (mv2 < mv1) is True
    assert (mv1 > mv2) is True
    assert (mv2 <= mv1) is True
    assert (mv1 >= mv2) is True

    # data_manager property proxies through measurement.source
    assert mv1.data_manager is src.data_manager


def test_measurement_update_add_replace_and_archive(monkeypatch):
    calls = []

    def archive_spy(self):
        calls.append(self)

    monkeypatch.setattr(mod.Archivable, "archive", archive_spy)

    src = DummySource()
    # Use enums that are guaranteed to exist
    m = Measurement(
        source=src,
        measurement_type=MeasurementType.Unknown,
        measurement_unit=MeasurementUnit.Unknown,
    )

    # Measurement.__init__ calls archive once
    base_calls = len(calls)

    ti = DummyInterval("I", datetime(2024, 1, 1, 0, 0, 0), timedelta(minutes=15))
    m.update(ti, 5.0)  # archive=True default
    assert len(m) == 1
    assert len(calls) == base_calls + 1

    # Updating same interval without replace updates existing MeasurementValue
    mv_before = m.get_by_interval(ti)
    m.update(ti, 7.5, archive=False)  # no new archive
    mv_after = m.get_by_interval(ti)
    assert mv_before is mv_after
    assert mv_after.value == 7.5
    assert len(calls) == base_calls + 1  # unchanged

    # Replace=True creates a new MeasurementValue instance
    m.update(ti, 9.0, archive=True, replace=True)
    mv_replaced = m.get_by_interval(ti)
    assert mv_replaced is not mv_after
    assert mv_replaced.value == 9.0
    assert len(calls) == base_calls + 2  # one more archive call


def test_measurement_sorting_all_first_last_and_iterators():
    src = DummySource()
    m = Measurement(source=src)

    dur = timedelta(minutes=10)
    t0 = DummyInterval("I0", datetime(2024, 1, 1, 0, 0, 0), dur)
    t2 = DummyInterval("I2", datetime(2024, 1, 1, 0, 20, 0), dur)
    t1 = DummyInterval("I1", datetime(2024, 1, 1, 0, 10, 0), dur)

    m.update(t0, 1.0)
    m.update(t2, 3.0)
    m.update(t1, 2.0)

    # Sorted items by start_time
    items = m.all
    assert [ti.name for ti, _ in items] == ["I0", "I1", "I2"]

    # first and last
    assert m.first.value == 1.0
    assert m.last.value == 3.0

    # start_times and values iterators align with sorted order
    assert list(m.start_times) == [t0.start_time, t1.start_time, t2.start_time]
    assert list(m.values) == [1.0, 2.0, 3.0]

    # truthiness and len
    assert bool(m) is True
    assert len(m) == 3


def test_measurement_getters_and_prior_behavior():
    src = DummySource()
    m = Measurement(source=src)

    dur = timedelta(minutes=5)
    t0 = DummyInterval("I0", datetime(2024, 1, 1, 0, 0, 0), dur)
    t1 = DummyInterval("I1", datetime(2024, 1, 1, 0, 5, 0), dur)
    t2 = DummyInterval("I2", datetime(2024, 1, 1, 0, 10, 0), dur)

    m.update(t0, 10)
    m.update(t1, 20)
    m.update(t2, 30)

    # get_by_interval returns the MeasurementValue
    assert m.get_by_interval(t1).value == 20

    # get_by_start_time returns the MeasurementValue, matching its annotation and get_by_interval.
    assert m.get_by_start_time(t1.start_time).value == 20
    # The TimeInterval key is available separately when needed.
    assert m.get_interval_by_start_time(t1.start_time) is t1

    # get_prior_by_interval delegates to get_by_start_time, so it too returns the value.
    assert m.get_prior_by_interval(t2).value == 20


def test_measurement_total_with_and_without_market_filter():
    src = DummySource()
    m = Measurement(source=src)

    dur = timedelta(minutes=15)
    market_a = object()
    market_b = object()

    ta1 = DummyInterval("A1", datetime(2024, 1, 1, 0, 0, 0), dur, market=market_a)
    ta2 = DummyInterval("A2", datetime(2024, 1, 1, 0, 15, 0), dur, market=market_a)
    tb1 = DummyInterval("B1", datetime(2024, 1, 1, 0, 30, 0), dur, market=market_b)

    m.update(ta1, 5.0)
    m.update(ta2, 10.0)
    m.update(tb1, 20.0)

    assert m.total() == 35.0
    assert m.total(market=market_a) == 15.0
    assert m.total(market=market_b) == 20.0


def test_measurement_get_record_and_metadata(monkeypatch):
    # Ensure base get_record contributes a known dict
    monkeypatch.setattr(mod.Archivable, "get_record", lambda self: {"id": "meas-uuid"})

    src = DummySource(id_="component-7")
    m = Measurement(source=src, measurement_type=MeasurementType.Unknown, measurement_unit=MeasurementUnit.Unknown)

    rec = m.get_record()
    assert rec["id"] == "meas-uuid"
    assert rec["source_id"] == "component-7"
    assert rec["measurement_type"] == MeasurementType.Unknown.name
    assert rec["measurement_unit"] == MeasurementUnit.Unknown.name

    # metadata helpers
    assert m.data_manager is src.data_manager
    assert m.table_name == "measurement"


def test_measurementvalue_get_record_and_metadata(monkeypatch):
    # Stub base get_record and Timer.now to be deterministic
    monkeypatch.setattr(mod.Archivable, "get_record", lambda self: {})
    fixed_now = datetime(2024, 1, 1, 12, 34, 56)
    monkeypatch.setattr(mod.Timer, "now", classmethod(lambda cls: fixed_now))

    # Use a Measurement subclass that provides a read-only id property without needing a setter
    class MeasurementWithId(Measurement):
        @property
        def id(self):
            return "meas-1"

    src = DummySource()
    m = MeasurementWithId(source=src)

    ti = DummyInterval("I", datetime(2024, 1, 1, 0, 0, 0), timedelta(minutes=5), id_="ti-99")
    mv = MeasurementValue(m, ti, 42.5)

    rec = mv.get_record()
    assert rec["measurement_id"] == "meas-1"
    assert rec["interval_id"] == "ti-99"
    assert rec["record_time"] == fixed_now
    assert rec["value"] == 42.5

    # metadata helpers
    assert mv.data_manager is src.data_manager
    assert mv.table_name == "measurement_value"


def test_get_archive_definitions_append(monkeypatch):
    # Measurement
    monkeypatch.setattr(mod.Archivable, "get_archive_definitions", classmethod(lambda cls: []))
    defs_m = Measurement.get_archive_definitions()
    m_def = next(d for d in defs_m if d["name"] == "measurement")
    cols_m = {c["name"] for c in m_def["columns"]}
    assert {"id", "source_id", "measurement_type", "measurement_unit"} <= cols_m

    # MeasurementValue
    monkeypatch.setattr(mod.Archivable, "get_archive_definitions", classmethod(lambda cls: []))
    defs_mv = MeasurementValue.get_archive_definitions()
    mv_def = next(d for d in defs_mv if d["name"] == "measurement_value")
    cols_mv = {c["name"] for c in mv_def["columns"]}
    assert {"id", "measurement_id", "interval_id", "record_time", "value"} <= cols_mv


def test_copy_creates_independent_values():
    src = DummySource()
    m = Measurement(source=src)

    dur = timedelta(minutes=10)
    t0 = DummyInterval("I0", datetime(2024, 1, 1, 0, 0, 0), dur)
    t1 = DummyInterval("I1", datetime(2024, 1, 1, 0, 10, 0), dur)

    m.update(t0, 1.0)
    m.update(t1, 2.0)

    m_copy = m.copy()

    # Change original; copy should retain old values
    m.update(t1, 5.0)
    assert m.get_by_interval(t1).value == 5.0

    # Copy still has prior value for t1
    assert m_copy.get_by_interval(t1).value == 2.0

def test_measurementvalue_numeric_protocol(monkeypatch):  # audit T-08
    monkeypatch.setattr(mod.Archivable, "get_record", lambda self: {})
    m = Measurement(source=DummySource())
    ti = DummyInterval("I1", datetime(2024, 1, 1, 0, 0, 0), timedelta(minutes=5))
    mv = MeasurementValue(m, ti, 4.0)
    assert float(mv) == 4.0 and int(mv) == 4
    assert -mv == -4.0 and +mv == 4.0 and abs(MeasurementValue(m, ti, -4.0)) == 4.0
    assert mv ** 2 == 16.0 and 2 ** mv == 16.0
    assert 1 / mv == 0.25 and 9 // mv == 2
    assert round(MeasurementValue(m, ti, 4.5678), 2) == 4.57
    assert hash(mv) == hash(mv) and len({mv, mv}) == 1, 'hashable by identity: the value is mutable'
    assert sum([mv, mv]) == 8.0 and max(mv, 3.0) is mv
