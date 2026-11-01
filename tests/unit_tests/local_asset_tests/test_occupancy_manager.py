"""SimpleOccupancyManager: the weekly schedule (audit T-INC-06)."""
from datetime import datetime, timezone

import pytest

from tent.local_asset.occupancy_manager import OccupancyManager, SimpleOccupancyManager

MONDAY_NOON = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
SUNDAY_NOON = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def test_an_empty_schedule_is_always_occupied():
    manager = SimpleOccupancyManager({})
    assert isinstance(manager, OccupancyManager)
    assert manager.always_occupied and manager.check_schedule(SUNDAY_NOON) and manager.occupied is True


def test_a_full_week_answers_for_every_day():
    week = {day: {'start': '08:00', 'end': '18:00'} for day in ('mon', 'tue', 'wed', 'thu', 'fri')}
    week.update(sat='always_off', sun='always_off')
    manager = SimpleOccupancyManager(week)
    assert manager.check_schedule(MONDAY_NOON) is True
    assert manager.check_schedule(MONDAY_NOON.replace(hour=19)) is False
    assert manager.check_schedule(SUNDAY_NOON) is False


def test_a_partial_week_is_a_configuration_error():
    with pytest.raises(ValueError, match=r"missing \['saturday', 'sunday'\]"):
        SimpleOccupancyManager({day: 'always_on' for day in ('monday', 'tuesday', 'wednesday', 'thursday', 'friday')})
    with pytest.raises(ValueError, match="'monday' must be"):
        SimpleOccupancyManager({'monday': 'sometimes'})
