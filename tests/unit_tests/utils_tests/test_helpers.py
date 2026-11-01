from datetime import datetime

import pytest

from tent.utils.helpers import is_heavyloadhour


@pytest.mark.parametrize('timestamp', ['2018-01-03 06:00:00', '2018-01-04 21:59:59', '2018-01-05 10:00:00'])
def test_weekday_hours_six_to_twenty_two_are_heavy_load(timestamp):
    assert is_heavyloadhour(datetime.fromisoformat(timestamp)) is True


@pytest.mark.parametrize('timestamp', [
    '2018-01-07 21:00:00',  # Sunday
    '2018-01-04 05:59:59',  # before 06:00
    '2018-01-04 22:00:00',  # from 22:00
    '2018-06-22 00:00:40',
])
def test_sundays_and_night_hours_are_light_load(timestamp):
    assert is_heavyloadhour(datetime.fromisoformat(timestamp)) is False


@pytest.mark.parametrize('day', ['2018-01-01', '2018-05-28', '2018-07-04', '2018-09-03', '2018-11-22', '2018-12-25'])
def test_listed_holidays_are_light_load_all_day(day):
    assert is_heavyloadhour(datetime.fromisoformat(f'{day} 12:00:00')) is False
