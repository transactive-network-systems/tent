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


def test_the_building_is_a_load_in_every_hour_peaking_in_the_afternoon(scheduled):
    campus, market = scheduled
    building = asset_named(campus, 'InelasticBuildings')
    load = dict(zip((ti.start_time.hour for ti in market.time_intervals), powers(building, market)))
    assert len(load) == 24 and all(building.minimum_power < p < 0.0 for p in load.values()), \
        'a load in every hour, nowhere held at the 24.2 MW limit'
    # 1 July 2017 at Pasco runs from 57 degF before dawn to 90 degF at 15:00-17:00. The campus regression (in degF)
    # peaks at 15:00: the hour coefficients give 15:00 the steepest temperature factor of the three 90 degF hours.
    assert min(load, key=load.get) == 15 and -19500.0 < load[15] < -18500.0, \
        'the hottest hour, 15:00 at 90 degF, carries the largest cooling load, 19.0 MW'
    assert max(load, key=load.get) == 1 and -7500.0 < load[1] < -7000.0, \
        'the lightest hour is 01:00 (66 degF, before the morning rise), a 7.3 MW base load'
    assert load[15] / load[1] > 2.5, 'the afternoon peak is more than two and a half times the night load'
    rise = [load[h] for h in range(5, 16)]
    assert rise == sorted(rise, reverse=True), 'the load grows every hour from 05:00 (57 degF) to the peak at 15:00'
    fall = [load[h] for h in (*range(15, 24), 0, 1)]
    assert fall == sorted(fall), 'and shrinks every hour from 15:00 to 01:00'
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
    link_delivers = cor.maximum_power * (1 - cor.loss_factor)  # what the 20 MW link lands at the campus at full load
    cut = []
    for time_interval in market.time_intervals:
        inelastic = -sum(e.scheduled_powers.get_by_interval(time_interval).value
                         for e in campus.local_assets + campus.neighbors[1:] if e is not flexible)
        # The bid's flexibility vertices (record 0, the balance point at the predicted price, sits between them).
        # Bid quantities are what the supplier delivers (negative, its perspective); net of the transport losses they
        # are the campus demand at each of the flexible asset's breakpoints. At the 15:00 and 16:00 peaks the inelastic
        # load plus the whole 1 MW exceeds what the link can land, so the bid is bounded at the link's limit there.
        breakpoints = bid.flexibility_vertices(time_interval)
        at_the_campus = [-v.quantity - cor.loss_factor * v.quantity ** 2 / cor.maximum_power for v in breakpoints]
        assert at_the_campus == pytest.approx([min(inelastic + 1000.0, link_delivers), inelastic], rel=1e-9), \
            'the bid to the supplier spans the flexible asset\'s range, up to what the link can carry'
        if inelastic + 1000.0 > link_delivers:
            cut.append(time_interval.start_time.hour)
            assert breakpoints[0].quantity == -cor.maximum_power and breakpoints[0].price < breakpoints[1].price < 0.06, \
                'the lower breakpoint is the link limit, priced on the flexible curve inside its range'
        else:
            assert breakpoints[0].price < 0.03 and breakpoints[0].price < breakpoints[1].price < 0.06, \
                'the breakpoint prices, restated at the supplier (before its losses)'
        assert bid.balance_point(time_interval).quantity == pytest.approx(
            bid.get_by_interval(time_interval).power_from_vertices(DEFAULT_PRICE)), 'record 0 at the predicted price'
    assert cut == [15, 16], 'only the two hottest hours would exceed the link with the flexible load fully on'

    step_to(campus, clock, market.transition_times.delivery_lead)
    assert market.market_state is MarketState.DeliveryLead and market._stateIsCompleted
    scheduled_by_hour = {}
    for time_interval, price in zip(market.time_intervals, prices(market)):
        scheduled = flexible.scheduled_powers.get_by_interval(time_interval).value
        assert scheduled == flexible.default_vertices.power_from_vertices(price) != at_default, time_interval.name
        # The flexible load is 1 MW at 0.03 $/kWh and nothing at 0.06. The campus clears between 0.042 at night and
        # 0.060 at the 15:00 peak (a 19.1 MW import on the supplier's offer, bounded to the 20 MW link), so the load is
        # partly on in every hour: 585 kW at 01:00 down to a few kW at 15:00.
        assert -1000.0 < scheduled < 0.0, time_interval.name
        scheduled_by_hour[time_interval.start_time.hour] = scheduled
        # Within a watt of balance: sum_vertices offsets the duplicated 0.03 breakpoint by 1e-10 $/kWh, which tilts the
        # segment the balance is interpolated on by a few parts per billion.
        assert sum(e.scheduled_powers.get_by_interval(time_interval).value
                   for e in campus.local_assets + campus.neighbors) == pytest.approx(0.0, abs=1e-3)
    assert max(scheduled_by_hour, key=scheduled_by_hour.get) == 15 and scheduled_by_hour[15] > -50.0, \
        'all but priced out at the 15:00 peak'
    assert min(scheduled_by_hour, key=scheduled_by_hour.get) == 1 and scheduled_by_hour[1] < -500.0, \
        'and more than half on at 01:00'
