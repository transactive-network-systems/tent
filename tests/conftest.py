"""Fixtures shared by every TENT test."""
from datetime import timezone

import pytest

from tent.base import archiving
from tent.utils.timer import Timer

_TIMER_DEFAULTS = {
    'created_time': None,
    'sim_one_hr_in_sec': 1200,
    'sim_start_time': None,
    'simulation': False,
    'tz': timezone.utc,
}


def _reset_timer():
    for name, value in _TIMER_DEFAULTS.items():
        setattr(Timer, name, value)


@pytest.fixture(autouse=True)
def real_time_clock():
    """Every test starts and ends with the class-level Timer in real time, UTC. Timer is process-wide state: a node
    configured with simulation settings in one test must not leak its clock into the next."""
    _reset_timer()
    yield
    _reset_timer()


@pytest.fixture(autouse=True)
def all_curves_archived():
    """Every test starts and ends archiving every changed curve version: the level is process-wide state, as the archive
    is, and a node configured with another level in one test must not leak it into the next."""
    archiving.set_curve_archiving('all')
    yield
    archiving.set_curve_archiving('all')
