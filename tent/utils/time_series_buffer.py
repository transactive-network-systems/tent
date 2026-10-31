"""
A bounded, time-ordered buffer of readings.

Readings are kept sorted by their timestamp whatever order they arrive in, so late or out-of-order data (a retried
publish, a second source) lands in the right place. The newest and oldest readings are at the ends, a time-range query
bisects into the sorted list, expired readings are dropped from the old end, and a maximum length drops the oldest when
exceeded. Built on sortedcontainers.SortedKeyList; the earlier deque-based buffer assumed arrival order equalled time
order and was wrong whenever it did not.
"""
from collections import namedtuple
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Iterable, Union
from zoneinfo import ZoneInfo

from sortedcontainers import SortedKeyList

from tent.utils.helpers import require_aware
from tent.utils.timer import Timer

PointRecord = namedtuple('PointRecord', ['value', 'd_time'])
_EMPTY = PointRecord(None, None)


class TimeSeriesBuffer:
    def __init__(self, iterable: Iterable = (), maxlen: int | None = None, tz: Union[tzinfo, str] = 'UTC',
                 dt_class=Timer, expiry: timedelta | None = None):
        """
        maxlen: keep at most this many readings, dropping the oldest. expiry: drop readings older than this, measured
        against dt_class.now() (the simulation clock by default). tz: zone in which a reading stamped by the buffer
        itself (no d_time given) is expressed; timestamps supplied by the caller must be timezone-aware.
        """
        self.tz: tzinfo = tz if isinstance(tz, tzinfo) else ZoneInfo(tz)
        self.dt_class = dt_class
        self.expiry: timedelta | None = expiry
        self.maxlen: int | None = maxlen
        self._records: SortedKeyList = SortedKeyList(key=lambda record: record.d_time)
        self.extend(iterable)

    # ------------------------------------------------------------------------------------------------ writing
    def _now(self) -> datetime:
        return self.dt_class.now(timezone.utc).astimezone(self.tz)

    def _record(self, value, d_time=None) -> PointRecord:
        if isinstance(value, PointRecord):
            return PointRecord(value.value, require_aware(value.d_time, 'd_time'))
        d_time = self._now() if d_time is None else require_aware(d_time, 'd_time')
        return PointRecord(value, d_time)

    def add(self, value, d_time: datetime | None = None) -> None:
        """Insert one reading at its time. Expired readings are not inserted; the buffer is pruned and bounded."""
        record = self._record(value, d_time)
        self._prune_expired()
        if not self._expired(record):
            self._records.add(record)
            self._enforce_maxlen()

    append = add  # the deque name, kept because it reads naturally for a stream of readings

    def extend(self, values: Iterable) -> None:
        """Insert many readings: PointRecords or (value, time) pairs, in any order."""
        values = list(values)
        if not values:
            return
        records = []
        for item in values:
            if isinstance(item, PointRecord):
                records.append(self._record(item))
            elif isinstance(item, (tuple, list)) and len(item) == 2 and isinstance(item[1], datetime):
                records.append(self._record(item[0], item[1]))
            else:
                raise ValueError('Values must be PointRecords or (value, datetime) pairs.')
        self._prune_expired()
        self._records.update(record for record in records if not self._expired(record))
        self._enforce_maxlen()

    def clear(self) -> None:
        self._records.clear()

    # ------------------------------------------------------------------------------------------------ reading
    @property
    def latest(self) -> PointRecord:
        """The reading with the newest timestamp (PointRecord(None, None) when empty)."""
        self._prune_expired()
        return self._records[-1] if self._records else _EMPTY

    @property
    def oldest(self) -> PointRecord:
        self._prune_expired()
        return self._records[0] if self._records else _EMPTY

    last = latest  # the deque buffer's name for the newest reading

    def get(self, since: datetime | None = None, until: datetime | None = None, columns: bool = False) -> list:
        """Readings with since <= time <= until (either bound optional), oldest first. columns=True returns
        ([values], [times]) instead of a list of records (empty list when there are none)."""
        self._prune_expired()
        if since is not None:
            since = require_aware(since, 'since')
        if until is not None:
            until = require_aware(until, 'until')
        lo = self._records.bisect_key_left(since) if since is not None else 0
        hi = self._records.bisect_key_right(until) if until is not None else len(self._records)
        selected = list(self._records[lo:hi]) if lo < hi else []
        if not columns:
            return selected
        return [list(column) for column in zip(*selected)] if selected else []

    def get_values(self, since: datetime | None = None, until: datetime | None = None) -> list:
        selected = self.get(since, until, columns=True)
        return selected[0] if selected else []

    def get_times(self, since: datetime | None = None, until: datetime | None = None) -> list:
        selected = self.get(since, until, columns=True)
        return selected[1] if selected else []

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self):
        return iter(self._records)

    def __getitem__(self, index):
        return self._records[index]

    def __bool__(self) -> bool:
        return bool(self._records)

    # ------------------------------------------------------------------------------------------------ housekeeping
    def _expired(self, record: PointRecord) -> bool:
        return bool(self.expiry) and record.d_time + self.expiry <= self._now()

    def _prune_expired(self) -> None:
        if not self.expiry or not self._records:
            return
        cutoff = self._now() - self.expiry
        # Everything with time <= cutoff has expired; they are the oldest, at the front of the sorted list.
        del self._records[:self._records.bisect_key_right(cutoff)]

    def _enforce_maxlen(self) -> None:
        if self.maxlen is not None and len(self._records) > self.maxlen:
            del self._records[:len(self._records) - self.maxlen]
