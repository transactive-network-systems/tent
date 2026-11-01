"""Market timings: the first clearing time of a series and the invariants of its durations."""
from datetime import datetime, time, timedelta, timezone
from unittest.mock import patch

import pytest

from tent.containers.market_time import MarketDurations
from tent.utils.timer import Timer

UTC = timezone.utc


def campus(anchor='02:00', **overrides):
    """The campus deployment's real-time series: 15-minute clearing, 5-minute market lead and delivery lead."""
    settings = dict(active=0.0, negotiation=0.0, market_lead=300, delivery_lead=300, reconciliation=0.0,
                    market_interval=900, intervals_to_clear=24, market_clearing_interval=900, delivery_anchor_time=anchor)
    settings.update(overrides)
    return MarketDurations.new(**settings)


@pytest.mark.parametrize('now', [datetime(2017, 7, 1, 0, 0, tzinfo=UTC), datetime(2017, 7, 1, 0, 7, 30, tzinfo=UTC),
                                 datetime(2017, 7, 1, 0, 10, tzinfo=UTC), datetime(2017, 7, 1, 0, 14, 59, tzinfo=UTC),
                                 datetime(2017, 7, 1, 13, 22, 1, tzinfo=UTC)])
def test_first_market_is_anchored_and_never_starts_in_the_past(now):  # audit T-15
    durations = campus()
    with patch.object(Timer, 'now', return_value=now):
        timings = durations.get_timings(durations.get_first_clearing_time())
    run_gap = durations.active + durations.negotiation + durations.market_lead + durations.delivery_lead
    assert timings.active >= now, 'the market must be able to run its active, negotiation and lead periods'
    assert timings.delivery - now < run_gap + durations.market_clearing_interval, 'the earliest aligned delivery'
    anchor = datetime.combine(now.date(), time(2, 0), UTC)
    assert (timings.delivery - anchor) % durations.market_clearing_interval == timedelta(0), 'aligned to the anchor'


def test_day_ahead_delivery_lands_on_the_anchor_hour():  # audit T-15
    durations = campus(market_lead=900, delivery_lead=900, market_interval=3600, market_clearing_interval=86400)
    now = datetime(2017, 7, 1, 1, 50, tzinfo=UTC)  # ten minutes before the anchor, less than the thirty-minute run gap
    with patch.object(Timer, 'now', return_value=now):
        timings = durations.get_timings(durations.get_first_clearing_time())
    assert timings.delivery == datetime(2017, 7, 2, 2, 0, tzinfo=UTC), 'today\'s 02:00 cannot be reached in time'
    assert timings.active == datetime(2017, 7, 2, 1, 30, tzinfo=UTC)


def test_without_an_anchor_the_first_market_starts_now():
    durations = campus(anchor=None)
    now = datetime(2017, 7, 1, 0, 7, 30, tzinfo=UTC)
    with patch.object(Timer, 'now', return_value=now):
        timings = durations.get_timings(durations.get_first_clearing_time())
    assert timings.active == now


@pytest.mark.parametrize('field, value, message', [
    ('market_clearing_interval', 0, 'market_clearing_interval must be positive'),
    ('market_interval', 0.0, 'market_interval must be positive'),
    ('intervals_to_clear', 0, 'intervals_to_clear must be at least 1'),
    ('market_lead', -1, 'market_lead must not be negative'),
])
def test_durations_refuse_timings_that_cannot_run(field, value, message):  # audit T-INC-07
    with pytest.raises(ValueError, match=message):
        campus(**{field: value})
