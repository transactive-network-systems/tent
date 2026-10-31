import pytest
from datetime import datetime, timedelta, timezone
from weakref import ProxyType

import tent.containers.time_interval as mod
from tent.containers.time_interval import TimeInterval, IntervalSeries, timedelta as ti_timedelta


class DummyMarket:
    def __init__(self, id_="mkt-1", name="DA", data_manager=None):
        self.id = id_
        self.name = name
        self.market_series_name = name
        self.data_manager = object() if data_manager is None else data_manager


@pytest.fixture(autouse=True)
def stub_archivable_basics(monkeypatch):
    # Prevent side effects from base Archivable
    monkeypatch.setattr(mod.Archivable, "__init__", lambda self: None)
    monkeypatch.setattr(mod.Archivable, "archive", lambda self: None)


def test_timeinterval_initialization_with_seconds_duration_and_weakref(monkeypatch):
    captured = {}

    def cb_factory(self_obj, name):
        captured.setdefault("calls", []).append((self_obj, name))
        def on_gc(_):  # pragma: no cover
            pass
        return on_gc

    monkeypatch.setattr(mod, "safe_weak_proxy_callback", cb_factory)

    mkt = DummyMarket(id_="m-123", name="DayAhead")
    start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    ti = TimeInterval(market=mkt, duration=900, start_time=start)  # 900 seconds = 15 minutes

    # duration conversion and end_time
    assert isinstance(ti.duration, timedelta)
    assert ti.duration == timedelta(seconds=900)
    assert ti.end_time == start + timedelta(seconds=900)

    # weakref proxy to market and callback used
    assert isinstance(ti.market, ProxyType)
    assert ("calls" in captured) and captured["calls"][-1][0] is ti and captured["calls"][-1][1] == "market"

    # name format includes market name and ISO start_time
    assert ti.name == f"{mkt.name}@{start.isoformat()}"

    # data_manager property proxies to market.data_manager
    assert ti.data_manager is mkt.data_manager

    # table name constant
    assert ti.table_name == "time_interval"


def test_timeinterval_initialization_with_timedelta_duration():
    mkt = DummyMarket()
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    dur = timedelta(minutes=30)

    ti = TimeInterval(market=mkt, duration=dur, start_time=start)
    assert ti.duration is dur  # uses the same timedelta instance
    assert ti.end_time == start + dur


def test_timeinterval_get_record(monkeypatch):
    # Seed the base class get_record
    monkeypatch.setattr(mod.Archivable, "get_record", lambda self: {"id": "uuid-ti-1"})

    mkt = DummyMarket(id_="mkt-42", name="RT")
    start = datetime(2024, 5, 20, 6, 45, 0, tzinfo=timezone.utc)
    dur = timedelta(minutes=5)

    ti = TimeInterval(mkt, dur, start)
    rec = ti.get_record()

    assert rec["id"] == "uuid-ti-1"
    assert rec["market_id"] == "mkt-42"
    assert rec["name"] == f"{mkt.market_series_name}@{start.isoformat()}"
    assert rec["start_time"] == start
    assert rec["duration"] == dur


def test_timeinterval_get_archive_definitions_appends_table(monkeypatch):
    # Base returns empty list, ensure TimeInterval appends its own definition
    monkeypatch.setattr(mod.Archivable, "get_archive_definitions", classmethod(lambda cls: []))

    defs = TimeInterval.get_archive_definitions()
    assert any(d.get("name") == "time_interval" for d in defs)

    ti_def = next(d for d in defs if d["name"] == "time_interval")
    colnames = {c["name"] for c in ti_def["columns"]}
    expected_cols = {"id", "name", "market_id", "start_time", "duration"}
    assert expected_cols <= colnames

    # Ensure the unique index definition is present
    assert any(idx.get("name") == "idx_ti_instance" for idx in ti_def.get("indices", []))


def _install_simple_sorted_dict(monkeypatch):
    # A SortedDict stand-in that accepts any constructor args (including a positional key function),
    # and iterates in order of sorted keys (datetimes in this use case).
    class SimpleSortedDict(dict):
        def __init__(self, *args, **kwargs):
            # Accept a positional key function or any other arg and ignore it.
            # Allow mapping initialization if provided via kwargs/args.
            if args and not isinstance(args[0], (dict, list, tuple)):
                # args[0] might be a key function (callable) per code under test; ignore it.
                args = tuple()  # discard non-mapping positional arg
            super().__init__(*args, **kwargs)

        def values(self):
            for k in sorted(super().keys()):
                yield super().__getitem__(k)

        def keys(self):
            for k in sorted(super().keys()):
                yield k

    monkeypatch.setattr(mod, "SortedDict", SimpleSortedDict)


def _install_idempotent_proxy(monkeypatch):
    # Avoid double-proxying an already-proxied market (weakref.proxy(proxy(...)) raises TypeError).
    from weakref import ProxyType as _ProxyType, proxy as _real_proxy

    def idempotent_proxy(obj, callback):
        if isinstance(obj, _ProxyType):
            return obj
        return _real_proxy(obj, callback)

    monkeypatch.setattr(mod, "proxy", idempotent_proxy)


def test_intervalseries_construction_order_and_iteration(monkeypatch):
    _install_simple_sorted_dict(monkeypatch)
    _install_idempotent_proxy(monkeypatch)

    calls = []
    # Ensure TimeIntervals created by IntervalSeries would call archive (but we stubbed to no-op)
    monkeypatch.setattr(mod.Archivable, "archive", lambda self: calls.append(self))

    mkt = DummyMarket(name="Series")
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    dur = timedelta(minutes=15)

    series = IntervalSeries(market=mkt, first_start_time=start, duration=dur, number_of_intervals=4)

    # Correct number of intervals and archive calls
    assert len(tuple(series)) == 4
    assert len(calls) == 4

    # Order: first and last times
    assert series.first.start_time == start
    assert series.last.start_time == start + 3 * dur

    # __getitem__ by index
    assert series[0].start_time == start
    assert series[1].start_time == start + dur
    assert series[-1].start_time == start + 3 * dur

    # start_times iterator in sorted order
    times = list(series.start_times)
    assert times == [start + i * dur for i in range(4)]

    # get_by_start_time works with datetime
    ti_dt = series.get_by_start_time(start + 2 * dur)
    assert isinstance(ti_dt, TimeInterval)
    assert ti_dt.start_time == start + 2 * dur

    # get_by_start_time works with ISO string
    iso = (start + dur).isoformat()
    ti_iso = series.get_by_start_time(iso)
    assert ti_iso.start_time == start + dur

    # invalid type raises TypeError
    with pytest.raises(TypeError):
        series.get_by_start_time(12345)

    # total duration property
    assert series.total_duration == 4 * dur

    # Each interval should hold a weakref proxy to market
    for ti in series:
        assert isinstance(ti.market, ProxyType)


def test_intervalseries_uses_weakref_and_callback_for_market(monkeypatch):
    _install_simple_sorted_dict(monkeypatch)
    _install_idempotent_proxy(monkeypatch)

    captured = []

    def cb_factory(self_obj, name):
        captured.append((type(self_obj).__name__, name))
        def on_gc(_):  # pragma: no cover
            pass
        return on_gc

    monkeypatch.setattr(mod, "safe_weak_proxy_callback", cb_factory)

    mkt = DummyMarket(name="WeakRef")
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    dur = timedelta(minutes=10)

    series = IntervalSeries(market=mkt, first_start_time=start, duration=dur, number_of_intervals=2)

    # Series market is a weakref proxy
    assert isinstance(series.market, ProxyType)

    # Callback invoked for IntervalSeries at least
    names = [c[0] for c in captured]
    assert "IntervalSeries" in names

    # Each TimeInterval holds a proxy market
    for ti in series:
        assert isinstance(ti.market, ProxyType)


def test_intervalseries_destroy_clears_intervals(monkeypatch):
    _install_simple_sorted_dict(monkeypatch)
    _install_idempotent_proxy(monkeypatch)

    mkt = DummyMarket()
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    dur = timedelta(minutes=5)

    series = IntervalSeries(market=mkt, first_start_time=start, duration=dur, number_of_intervals=3)
    assert len(tuple(series)) == 3

    series.destroy()
    assert series._intervals == {}
    # Iteration yields nothing after destroy
    assert list(series) == []