"""Timer: zone handling and parameter retention (audit T-27)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tent.utils.timer import Timer

START = datetime(2017, 7, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def restore_timer():
    saved = {name: getattr(Timer, name) for name in ('created_time', 'sim_one_hr_in_sec', 'sim_start_time', 'simulation', 'tz')}
    yield
    for name, value in saved.items():
        setattr(Timer, name, value)


def test_now_honours_the_requested_zone_while_simulating(restore_timer):
    Timer.set(sim_one_hr_in_sec=3600, sim_start_time=START, simulation=True, tz='UTC')
    denver = Timer.now(ZoneInfo('America/Denver'))
    utc = Timer.now()
    assert denver.tzinfo == ZoneInfo('America/Denver') and utc.utcoffset() == timedelta(0)
    assert abs(denver - utc) < timedelta(seconds=1), 'the same instant in two zones'
    assert Timer.now('Asia/Tokyo').tzinfo == ZoneInfo('Asia/Tokyo'), 'a zone name is accepted too'


def test_pausing_the_simulation_keeps_its_parameters(restore_timer):
    Timer.set(sim_one_hr_in_sec=120, sim_start_time=START, simulation=True, tz='America/Denver')
    Timer.set(simulation=False)
    assert Timer.simulation is False
    assert Timer.sim_one_hr_in_sec == 120 and Timer.sim_start_time == START
    assert Timer.tz == ZoneInfo('America/Denver')
    Timer.set(simulation=True)  # resumes with the retained parameters
    assert Timer.now() >= START


def test_simulation_needs_a_start_and_a_ratio(restore_timer):
    Timer.sim_start_time = None
    with pytest.raises(ValueError, match='sim_one_hr_in_sec and sim_start_time'):
        Timer.set(simulation=True, sim_one_hr_in_sec=60)
    with pytest.raises(ValueError, match='sim_one_hr_in_sec must be positive'):
        Timer.set(sim_one_hr_in_sec=0)


def test_a_zone_must_be_a_tzinfo_or_a_name(restore_timer):
    with pytest.raises(TypeError, match='tz must be a tzinfo or an IANA zone name'):
        Timer.set(tz=-7)
    with pytest.raises(TypeError):
        Timer.now(tz=3600)
    Timer.set(tz=timezone(timedelta(hours=-7)))  # any tzinfo, not only ZoneInfo
    assert Timer.now().utcoffset() == timedelta(hours=-7)
