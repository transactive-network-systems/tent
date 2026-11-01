import logging

from datetime import datetime
from tent.utils.helpers import parse_time, parse_weekday

from tent.utils.timer import Timer

from .base import OccupancyManager

_log = logging.getLogger(__name__)

_DAY_NAMES = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


class SimpleOccupancyManager(OccupancyManager):
    """
    Occupancy from a weekly schedule: for each weekday either "always_on", "always_off", or {"start": "HH:MM",
    "end": "HH:MM"} (occupied from start up to end). An empty schedule means always occupied; a schedule that names
    some days but not all is a configuration error, since the missing days would have no answer.
    """

    def __init__(self, schedule: dict):
        self.schedule: dict[int, dict | str] = {}
        self.always_occupied = not schedule
        if schedule:
            for day_str, schedule_info in schedule.items():
                day = parse_weekday(day_str)
                if schedule_info in ("always_on", "always_off"):
                    self.schedule[day] = schedule_info
                elif isinstance(schedule_info, dict) and {'start', 'end'} <= set(schedule_info):
                    self.schedule[day] = {"start": parse_time(schedule_info["start"]),
                                          "end": parse_time(schedule_info["end"])}
                else:
                    raise ValueError(f'Occupancy schedule for {day_str!r} must be "always_on", "always_off" or a dict '
                                     f'with "start" and "end" times; got {schedule_info!r}')
            missing = [name for index, name in enumerate(_DAY_NAMES) if index not in self.schedule]
            if missing:
                raise ValueError(f'Occupancy schedule does not cover every weekday; missing {missing}. Give each day '
                                 f'"always_on", "always_off" or start and end times.')

    @property
    def occupied(self) -> bool:
        return self.check_schedule()

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
