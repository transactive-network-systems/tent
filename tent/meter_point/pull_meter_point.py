import abc
import logging
from datetime import datetime, timedelta

from tent.meter_point import MeterPoint
from tent.utils.helpers import require_aware
from tent.utils.timer import Timer

_log = logging.getLogger(__name__)


class PullMeterPoint(MeterPoint):
    """
    A meter point that reads its source on a schedule, the counterpart of PushMeterPoint for meters that must be asked.

    Subclasses implement read_meter(), which talks to the device, service or file and returns one reading. The node's
    host calls poll() on every meter point once per state-machine iteration; this class's poll() reads when
    data_period_seconds have passed since the last reading (the first poll reads at once) and stores the reading through
    set_meter_value, which scales and archives it like a pushed reading. A read_meter() that raises is logged with its
    traceback and retried on the next period, so one unreachable meter does not stop the node's markets; a read_meter()
    that returns None is a poll with nothing to record and is also retried on the next period.
    """

    def __init__(self, *args, **kwargs):
        super(PullMeterPoint, self).__init__(*args, **kwargs)
        self.next_read_time: datetime | None = None  # None until the first poll, which reads at once

    @abc.abstractmethod
    def read_meter(self) -> float | tuple[float, datetime] | None:
        """
        Read the meter once. Return the value (stamped with the current time), a (value, time) pair when the source
        reports the time of the reading (aware datetime), or None when there is nothing to record this period.
        """

    def due(self, now: datetime | None = None) -> bool:
        """True when a reading is wanted at `now` (the current time by default)."""
        now = require_aware(now, 'now') if now is not None else Timer.now()
        return self.next_read_time is None or now >= self.next_read_time

    def poll(self, now: datetime | None = None) -> bool:
        """Read and store a value if one is due (the host's per-iteration call). Returns True when a reading was recorded."""
        now = require_aware(now, 'now') if now is not None else Timer.now()
        if not self.due(now):
            return False
        self.next_read_time = now + timedelta(seconds=self.data_period_seconds)
        try:
            reading = self.read_meter()
        except Exception:
            _log.exception(f'Meter point {self.name!r} failed to read; next attempt at {self.next_read_time.isoformat()}.')
            return False
        if reading is None:
            _log.debug(f'Meter point {self.name!r} had no reading to record.')
            return False
        value, when = reading if isinstance(reading, tuple) else (reading, now)
        self.set_meter_value(value, when)
        return True
