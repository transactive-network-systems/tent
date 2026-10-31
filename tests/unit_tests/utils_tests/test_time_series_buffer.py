"""TimeSeriesBuffer: time-ordered whatever the arrival order, bounded, expiring, bisected range queries."""
from datetime import datetime, timedelta, timezone

import pytest

from tent.utils.time_series_buffer import PointRecord, TimeSeriesBuffer

T0 = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


class Clock:
    """A dt_class stand-in whose now() the test controls."""
    t = T0

    @classmethod
    def now(cls, tz=None):
        return cls.t.astimezone(tz) if tz else cls.t


@pytest.fixture(autouse=True)
def reset_clock():
    Clock.t = T0


def minutes(n):
    return T0 + timedelta(minutes=n)


def test_readings_are_ordered_by_time_not_arrival():
    buf = TimeSeriesBuffer(dt_class=Clock)
    buf.append(3.0, minutes(3))
    buf.append(1.0, minutes(1))
    buf.append(2.0, minutes(2))
    assert buf.get_values() == [1.0, 2.0, 3.0]
    assert buf.latest == PointRecord(3.0, minutes(3)) and buf.oldest.value == 1.0
    assert buf.last is buf.latest or buf.last == buf.latest  # deque-era name


def test_late_reading_lands_in_place_and_latest_is_by_time():
    buf = TimeSeriesBuffer(dt_class=Clock)
    buf.append(10.0, minutes(10))
    buf.append(5.0, minutes(5))  # arrives late
    assert buf.latest.value == 10.0, 'the newest reading by time, not the last to arrive'
    assert buf.get_values(since=minutes(6)) == [10.0]


def test_range_query_bounds_are_inclusive_and_bisected():
    buf = TimeSeriesBuffer(dt_class=Clock)
    buf.extend([(float(i), minutes(i)) for i in range(10)])
    assert buf.get_values(since=minutes(3), until=minutes(5)) == [3.0, 4.0, 5.0]
    assert buf.get_times(until=minutes(1)) == [minutes(0), minutes(1)]
    assert buf.get(since=minutes(20)) == [] and buf.get(since=minutes(20), columns=True) == []
    values, times = buf.get(since=minutes(8), columns=True)
    assert values == [8.0, 9.0] and times == [minutes(8), minutes(9)]


def test_maxlen_drops_the_oldest():
    buf = TimeSeriesBuffer(maxlen=3, dt_class=Clock)
    buf.extend([(float(i), minutes(i)) for i in range(5)])
    assert buf.get_values() == [2.0, 3.0, 4.0] and buf.maxlen == 3
    buf.append(0.5, minutes(0))  # older than everything kept: inserted, then dropped as the oldest
    assert buf.get_values() == [2.0, 3.0, 4.0]


def test_expiry_prunes_from_the_old_end_and_survives_emptying():
    buf = TimeSeriesBuffer(dt_class=Clock, expiry=timedelta(minutes=5))
    buf.append(1.0, minutes(0))
    buf.append(2.0, minutes(3))
    Clock.t = minutes(6)
    assert buf.get_values() == [2.0], 'the reading from minute 0 expired at minute 5'
    Clock.t = minutes(30)
    assert buf.get() == [] and buf.latest == PointRecord(None, None) and len(buf) == 0
    buf.append(9.0, minutes(1))  # already expired on arrival: not stored
    assert len(buf) == 0
    buf.append(10.0, minutes(30))
    assert buf.get_values() == [10.0]


def test_unstamped_reading_takes_the_clock_time_in_the_buffer_zone():
    buf = TimeSeriesBuffer(dt_class=Clock, tz='America/Los_Angeles')
    buf.append(7.0)
    assert buf.latest.d_time == T0 and buf.latest.d_time.tzinfo.key == 'America/Los_Angeles'


def test_naive_timestamps_are_rejected():
    buf = TimeSeriesBuffer(dt_class=Clock)
    with pytest.raises(ValueError):
        buf.append(1.0, datetime(2026, 10, 31, 12, 0))
    with pytest.raises(ValueError):
        buf.get(since=datetime(2026, 10, 31))
    with pytest.raises(ValueError):
        buf.extend([(1.0, datetime(2026, 10, 31))])
