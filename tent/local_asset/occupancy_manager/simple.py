import logging

from datetime import datetime, time
from typing import Annotated, Mapping

from pydantic import PlainSerializer, PlainValidator, WithJsonSchema

from tent.config import ConfigModel, Setting, time_of_day
from tent.utils.helpers import parse_weekday
from tent.utils.timer import Timer

from .base import OccupancyManager

_log = logging.getLogger(__name__)

_DAY_NAMES = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


def _weekly_schedule(value) -> dict[int, str | dict[str, time]]:
    """Normalise a configured weekly schedule: weekday names (or indices, Monday 0) to "always_on", "always_off" or
    {"start": time, "end": time}. Empty means always occupied; otherwise every weekday must be given."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f'must be a dict of weekday to "always_on", "always_off" or {{"start", "end"}}; got {value!r}')
    schedule: dict[int, str | dict[str, time]] = {}
    for day_key, info in value.items():
        day = parse_weekday(day_key)
        if day in schedule:
            raise ValueError(f'{_DAY_NAMES[day]} is given twice')
        if info in ("always_on", "always_off"):
            schedule[day] = info
        elif isinstance(info, Mapping) and set(info) == {'start', 'end'}:
            schedule[day] = {"start": time_of_day(info["start"]), "end": time_of_day(info["end"])}
        else:
            raise ValueError(f'the schedule for {day_key!r} must be "always_on", "always_off" or a dict with "start" '
                             f'and "end" times; got {info!r}')
    missing = [name for index, name in enumerate(_DAY_NAMES) if index not in schedule]
    if schedule and missing:
        raise ValueError(f'does not cover every weekday; missing {missing}. Give each day "always_on", "always_off" '
                         f'or start and end times.')
    return schedule


def _weekly_schedule_json(schedule: dict) -> dict:
    return {_DAY_NAMES[day]: (info if isinstance(info, str)
                              else {'start': info['start'].isoformat(timespec='minutes'),
                                    'end': info['end'].isoformat(timespec='minutes')})
            for day, info in sorted(schedule.items())}


_DAY_SCHEMA = {'anyOf': [{'type': 'string', 'enum': ['always_on', 'always_off']},
                         {'type': 'object', 'required': ['start', 'end'], 'additionalProperties': False,
                          'properties': {'start': {'type': 'string', 'x-tent-format': 'HH:MM'},
                                         'end': {'type': 'string', 'x-tent-format': 'HH:MM'}}}]}
WeeklySchedule = Annotated[dict, PlainValidator(_weekly_schedule), PlainSerializer(_weekly_schedule_json, when_used='json'),
                           WithJsonSchema({'type': 'object', 'additionalProperties': False,
                                           'properties': {name: _DAY_SCHEMA for name in _DAY_NAMES}})]


class SimpleOccupancyManager(OccupancyManager):
    """
    Occupancy from a weekly schedule: for each weekday either "always_on", "always_off", or {"start": "HH:MM",
    "end": "HH:MM"} (occupied from start up to end). An empty schedule means always occupied; a schedule that names
    some days but not all is a configuration error, since the missing days would have no answer.
    """
    class Config(ConfigModel):
        schedule: WeeklySchedule = Setting(
            {}, reconfigurable=True,
            description='Weekday (monday ... sunday) to "always_on", "always_off" or {"start": "HH:MM", "end": "HH:MM"} '
                        '(occupied from start up to end). Empty means always occupied; otherwise every weekday is '
                        'given.')

    def __init__(self, schedule: Mapping | None = None, config: ConfigModel | None = None, **kwargs):
        if schedule is not None:
            kwargs = {**kwargs, 'schedule': schedule}
        cfg = self._configure(config, kwargs)
        self._set_schedule(cfg.schedule)

    def _set_schedule(self, schedule: dict) -> None:
        self.schedule: dict[int, dict | str] = dict(schedule)
        self.always_occupied = not schedule

    def _apply_reconfiguration(self, cfg, changed: set[str]) -> None:
        if 'schedule' in changed:
            self._set_schedule(cfg.schedule)

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
