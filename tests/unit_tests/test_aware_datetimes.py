"""Naive datetimes are refused, or given the documented zone, at every boundary where one can enter TENT."""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from tent.containers import MarketTimings
from tent.containers.market_time import MarketDurations
from tent.containers.time_interval import IntervalSeries, TimeInterval
from tent.utils.helpers import require_aware
from tent.utils.timer import Timer

NAIVE = datetime(2026, 10, 31, 12, 0)
AWARE = NAIVE.replace(tzinfo=timezone.utc)


def test_require_aware():
    assert require_aware(AWARE, 'x') is AWARE
    assert require_aware(NAIVE, 'x', assume=ZoneInfo('UTC')) == AWARE
    with pytest.raises(ValueError, match='x must be timezone-aware'):
        require_aware(NAIVE, 'x')
    with pytest.raises(TypeError):
        require_aware('2026-10-31', 'x')


def test_market_timings_and_get_timings_reject_naive():
    with pytest.raises(ValueError, match='MarketTimings.active'):
        MarketTimings(*([NAIVE] * 7))
    durations = MarketDurations.new(0, 600, 300, 300, 0, 3600, 1, 3600, None)
    with pytest.raises(ValueError, match='clearing_time'):
        durations.get_timings('2026-10-31T12:00:00')
    assert durations.get_timings('2026-10-31T12:00:00+00:00').delivery_lead == AWARE


def test_time_interval_and_lookup_reject_naive():
    market = Mock(); market.name = 'M'
    with pytest.raises(ValueError, match='start_time'):
        TimeInterval(market, timedelta(hours=1), NAIVE)
    series = IntervalSeries(market, first_start_time=AWARE, duration=timedelta(hours=1), number_of_intervals=2)
    assert series.get_by_start_time(AWARE.isoformat()) is not None
    with pytest.raises(ValueError, match='start_time'):
        series.get_by_start_time('2026-10-31T12:00:00')


def test_timer_assumes_its_zone_for_a_naive_simulation_start():
    Timer.set(sim_one_hr_in_sec=100, sim_start_time=NAIVE, simulation=True, tz='America/Denver')
    try:
        assert Timer.sim_start_time.tzinfo == ZoneInfo('America/Denver')
        assert Timer.now().tzinfo is not None
    finally:
        Timer.set(simulation=False, tz='UTC')


def test_archive_normaliser_refuses_naive_datetimes(tmp_path):
    from tent.data_manager.local_data_manager import LocalDataManager
    from sqlalchemy import Column, DateTime
    with pytest.raises(ValueError, match='reading_time'):
        LocalDataManager._normalize_value(Column('reading_time', DateTime(timezone=True)), NAIVE)
    assert LocalDataManager._normalize_value(Column('reading_time', DateTime(timezone=True)), AWARE) == AWARE


def test_signal_with_naive_time_is_rejected_not_stored():
    from tent.containers.transactive_signal import TransactiveSignal
    tn = Mock()
    market = Mock(); market.name = 'M'
    tn.get_market_by_name.return_value = market
    tn.get_neighbors_by_attribute.return_value = Mock()
    message = {'market': 'M', 'neighbor': 'N', 'protocol_version': TransactiveSignal.PROTOCOL_VERSION,
               'signal_time': '2026-10-31T12:00:00', 'sequence': 1, 'in_reply_to': None, 'revised': True,
               'unit': 'kW', 'intervals': {}}
    assert TransactiveSignal.parse_signal(tn, message) is None
