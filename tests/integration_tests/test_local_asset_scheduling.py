"""
The campus assets schedule against the Day-Ahead auction: the building load from the temperature forecast, the solar
array from the hour of day, each offered to the market as an inelastic vertex; and a price-responsive asset added to
the campus is rescheduled at the cleared price once the auction balances.
"""
import pytest

from tent.enumerations import MarketState
from tests.integration_tests.conftest import (DEFAULT_PRICE, INF, asset_named, clear, deliver_neighbor_signals, powers,
                                              prices, step_to)
from tests.stubs import FlexibleAsset

FLEXIBLE_CURVE = [[0.03, 0.0, -1000.0, True], [0.06, 0.0, 0.0, True]]  # 1 MW of load at 0.03 $/kWh, none at 0.06


@pytest.fixture
def scheduled(campus, clock):
    """The campus after its assets have scheduled for the first Day-Ahead market (Active and Negotiation in one
    tick)."""
    market = campus.market_series[0].newest_market
    deliver_neighbor_signals(campus, market)
    step_to(campus, clock, market.transition_times.active)
    assert market.market_state is MarketState.MarketLead
    return campus, market


def test_the_building_is_a_load_in_every_hour_held_at_its_limit_through_the_afternoon(scheduled):
    campus, market = scheduled
    building = asset_named(campus, 'InelasticBuildings')
    load = dict(zip((ti.start_time.hour for ti in market.time_intervals), powers(building, market)))
    assert len(load) == 24 and all(building.minimum_power <= p < 0.0 for p in load.values())
    # 1 July 2017 at Pasco runs from 57 degF before dawn to 90 degF at 15:00-17:00. The campus regression (in degF)
    # asks for 16.6 to 19.0 MW from 11:00 (82 degF) to 17:00, more than the asset's configured 16.4 MW limit, so
    # LocalAsset.schedule holds those seven hours at the limit: the hottest hours cannot be told apart at the limit.
    assert [h for h in load if load[h] == building.minimum_power] == list(range(11, 18)), \
        'the regression exceeds the 16.4 MW limit from 11:00 to 17:00 and is held there'
    assert max(load, key=load.get) == 1 and -7500.0 < load[1] < -7000.0, \
        'the lightest hour is 01:00 (66 degF, before the morning rise), a 7.3 MW base load'
    assert load[11] / load[1] > 2.2, 'the limit is more than twice the night load'
    rise = [load[h] for h in range(5, 12)]
    assert rise == sorted(rise, reverse=True), 'the load grows every hour from 05:00 (57 degF) to the limit at 11:00'
    fall = [load[h] for h in (*range(17, 24), 0, 1)]
    assert fall == sorted(fall), 'and shrinks every hour from 17:00 (90 degF) to 01:00'
    assert building.schedule_calculated


def test_the_solar_array_follows_the_sun(scheduled):
    campus, market = scheduled
    solar = asset_named(campus, 'SolarPv')
    output = dict(zip((ti.start_time.hour for ti in market.time_intervals), powers(solar, market)))
    assert all(output[h] == 0.0 for h in (*range(18, 24), *range(0, 5))), 'nothing before 05:00 or after 18:00'
    assert all(output[h] > 0.0 for h in range(5, 18))
    assert output[11] == output[12] == max(output.values()) < solar.maximum_power, 'the clear-sky peak straddles noon'
    assert [output[h] for h in range(6, 12)] == sorted(output[h] for h in range(6, 12)), 'rising through the morning'
    assert all(output[h] == output[23 - h] for h in range(6, 12)), 'and mirrored in the afternoon'


def test_every_asset_offers_one_inelastic_vertex_at_its_scheduled_power(scheduled):
    campus, market = scheduled
    for asset in campus.local_assets:
        for time_interval in market.time_intervals:
            curve = asset.active_vertices.get_by_interval(time_interval)
            assert [(v.price, v.quantity) for v in curve.vertices] == \
                   [(INF, asset.scheduled_powers.get_by_interval(time_interval).value)], \
                (asset.name, time_interval.name)


def test_a_flexible_asset_is_rescheduled_at_the_cleared_price(campus, clock):
    flexible = FlexibleAsset(transactive_node=campus, name='FlexibleLoad', cost_parameters=[0.0, 0.0, 0.0],
                             default_vertices=FLEXIBLE_CURVE, minimum_power=-1000.0, maximum_power=0.0)
    campus.local_assets.append(flexible)
    market = campus.market_series[0].newest_market
    deliver_neighbor_signals(campus, market)
    step_to(campus, clock, market.transition_times.active)
    at_default = flexible.default_vertices.power_from_vertices(DEFAULT_PRICE)
    assert powers(flexible, market) == [at_default] * 24, 'bid with no price known: scheduled at the series default'
    assert at_default == pytest.approx(-1000.0 * (0.06 - DEFAULT_PRICE) / 0.03)
    cor = campus.neighbors[0]
    bid = cor.prepared_signals[market]
    for time_interval in market.time_intervals:
        inelastic = -sum(e.scheduled_powers.get_by_interval(time_interval).value
                         for e in campus.local_assets + campus.neighbors[1:] if e is not flexible)
        # The bid's flexibility vertices (record 0, the balance point at the predicted price, sits between them).
        # Bid quantities are what the supplier delivers (negative, its perspective); net of the transport losses they
        # are the campus demand at each of the flexible asset's breakpoints.
        breakpoints = bid.flexibility_vertices(time_interval)
        at_the_campus = [-v.quantity - cor.loss_factor * v.quantity ** 2 / cor.maximum_power for v in breakpoints]
        assert at_the_campus == pytest.approx([inelastic + 1000.0, inelastic], rel=1e-9), \
            'the bid to the supplier spans the flexible asset\'s range'
        assert breakpoints[0].price < 0.03 and breakpoints[0].price < breakpoints[1].price < 0.06, \
            'the breakpoint prices, restated at the supplier (before its losses)'
        assert bid.balance_point(time_interval).quantity == pytest.approx(
            bid.get_by_interval(time_interval).power_from_vertices(DEFAULT_PRICE)), 'record 0 at the predicted price'

    step_to(campus, clock, market.transition_times.delivery_lead)
    assert market.market_state is MarketState.DeliveryLead and market._stateIsCompleted
    on, off = [], []
    for time_interval, price in zip(market.time_intervals, prices(market)):
        scheduled = flexible.scheduled_powers.get_by_interval(time_interval).value
        assert scheduled == flexible.default_vertices.power_from_vertices(price) != at_default, time_interval.name
        # The flexible load is 1 MW at 0.03 $/kWh and nothing at 0.06. The campus clears between 0.047 at night and
        # 0.068 in the afternoon (a 16.5 MW import on an offer rising from 0.03 to 0.07 over 20 MW), so the load is
        # partly on through the night and priced out entirely while the building is at its limit.
        (off if price >= 0.06 else on).append(time_interval.start_time.hour)
        assert (scheduled == 0.0) if price >= 0.06 else (-1000.0 < scheduled < 0.0), time_interval.name
        # Within a watt of balance: sum_vertices offsets the duplicated 0.03 breakpoint by 1e-10 $/kWh, which tilts the
        # segment the balance is interpolated on by a few parts per billion.
        assert sum(e.scheduled_powers.get_by_interval(time_interval).value
                   for e in campus.local_assets + campus.neighbors) == pytest.approx(0.0, abs=1e-3)
    assert set(range(9, 19)) <= set(off) and set(range(2, 8)) <= set(on), \
        'off through the midday hours, partly on through the night and the shoulders'
    assert off == sorted(off) and all(b - a == 1 for a, b in zip(off, off[1:])), 'one contiguous block of off hours'
