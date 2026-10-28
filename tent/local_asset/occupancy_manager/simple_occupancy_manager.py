import logging

from datetime import datetime
from tent.utils.helpers import parse_time, parse_weekday

from tent.utils.timer import Timer

_log = logging.getLogger(__name__)


class SimpleOccupancyManager:
    def __init__(self, schedule: dict):
        self.occupied = property(self.check_schedule)
        self.schedule = {}
        if schedule:
            self.always_occupied = False
            for day_str, schedule_info in schedule.items():
                _day = parse_weekday(day_str)
                if schedule_info not in ["always_on", "always_off"]:
                    start = parse_time(schedule_info["start"])
                    end = parse_time(schedule_info["end"])
                    self.schedule[_day] = {"start": start, "end": end}
                else:
                    self.schedule[_day] = schedule_info
        else:
            self.always_occupied = True

    def check_schedule(self, dt: datetime | None = None):
        if self.always_occupied:
            return True
        dt = dt if dt else Timer.now()
        current_schedule = self.schedule[dt.weekday()]
        if "always_on" in current_schedule:
            occupied = True
        elif "always_off" in current_schedule:
            occupied = False
        else:
            _start = current_schedule["start"]
            _end = current_schedule["end"]
            occupied = True if _start <= dt.time() < _end else False
        return occupied
