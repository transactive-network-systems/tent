"""
The campus runs two auction series at once: a Day-Ahead auction that clears 24 hours once a day and a Real-Time auction
that clears 24 quarter-hours every 15 minutes, configured to refine the day-ahead series. Driven by a clock that ticks
every five minutes, each series spawns its own ladder of markets. Two strict xfails record that the configured
refinement does not take effect and that a real-time market does not open at the prices its predecessor cleared.
"""
from datetime import datetime, timedelta, timezone

from tent.enumerations import MarketState
from tests.integration_tests.conftest import DEFAULT_PRICE, T0, deliver_neighbor_signals, prices, step_to

FIVE_MINUTES = timedelta(minutes=5)


def tick_until(tn, clock, end):
    when = T0
    while when <= end:
        step_to(tn, clock, when)
        when += FIVE_MINUTES


def test_each_series_spawns_its_own_ladder_of_markets_as_the_clock_advances(campus, clock):
    day_ahead, real_time = campus.market_series
    assert real_time.duration.market_clearing_interval == timedelta(minutes=15)
    assert day_ahead.duration.market_clearing_interval == timedelta(days=1)
    tick_until(campus, clock, datetime(2017, 7, 1, 2, 30, tzinfo=timezone.utc))

    first_clearing = datetime(2017, 7, 1, 0, 10, tzinfo=timezone.utc)
    assert [m.transition_times.delivery_lead for m in real_time] == \
           [first_clearing + k * timedelta(minutes=15) for k in range(11)], 'one market per clearing, none expired yet'
    assert [m.market_state for m in real_time] == [MarketState.Delivery] * 10 + [MarketState.Inactive], \
        'every market whose delivery has begun is delivering; the newest waits for its active time'
    for market in real_time:
        assert [ti.start_time for ti in market.time_intervals] == \
               [market.transition_times.delivery + k * timedelta(minutes=15) for k in range(24)]
    for earlier, later in zip(list(real_time)[:-1], list(real_time)[1:]):
        assert later.prior_market_in_series.name == earlier.name

    assert [(m.transition_times.delivery_lead, m.market_state) for m in day_ahead] == [
        (datetime(2017, 7, 1, 1, 45, tzinfo=timezone.utc), MarketState.Delivery),
        (datetime(2017, 7, 2, 1, 45, tzinfo=timezone.utc), MarketState.Inactive)]
    assert all(len(m.time_intervals) == 24 for m in day_ahead) and \
           all(ti.duration == timedelta(hours=1) for ti in day_ahead.newest_market.time_intervals)


def test_a_real_time_market_refines_the_day_ahead_market_covering_its_delivery(campus, clock):
    day_ahead, real_time = campus.market_series
    assert real_time.refined_series is not None and real_time.refined_series.name == 'Day-Ahead_Auction'
    tick_until(campus, clock, datetime(2017, 7, 1, 2, 30, tzinfo=timezone.utc))
    da_market = day_ahead.markets[0]
    after_da_cleared = [m for m in real_time
                       if m.transition_times.delivery_lead > da_market.transition_times.delivery_lead]
    assert [m.transition_times.delivery_lead.strftime('%H:%M') for m in after_da_cleared] == \
           ['01:55', '02:10', '02:25', '02:40']
    for market in after_da_cleared:
        assert market.market_to_be_refined is not None and market.market_to_be_refined.name == da_market.name


def test_a_real_time_market_opens_at_the_prices_its_predecessor_cleared(campus, clock):
    _, real_time = campus.market_series
    first = real_time.newest_market
    deliver_neighbor_signals(campus, first)
    step_to(campus, clock, first.transition_times.active)  # schedules, bids and spawns the second market
    second = real_time.newest_market
    assert second is not first
    step_to(campus, clock, first.transition_times.delivery_lead)
    assert first.market_state is MarketState.DeliveryLead and DEFAULT_PRICE not in prices(first)
    step_to(campus, clock, second.transition_times.active)
    assert second.market_state is MarketState.MarketLead, 'the second market has scheduled and bid'
    cleared = {ti.start_time: price for ti, price in zip(first.time_intervals, prices(first))}
    shared = [ti for ti in second.time_intervals if ti.start_time in cleared]
    assert len(shared) == 23
    for time_interval in shared:
        assert second.marginal_prices.get_by_interval(time_interval).value == cleared[time_interval.start_time]
