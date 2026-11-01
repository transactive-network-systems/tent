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
    assert len(load) == 24 and all(building.minimum_power <= p < 0.0 for p in load.values())
    assert min(load, key=load.get) == 13, 'the hottest hour of 1 July 2017 at Pasco carries the largest cooling load'
    assert load[13] < -10000.0 and load[0] > -100.0, 'the regression swings from a 12 MW afternoon to a 0.1 MW night'
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
        curve = bid.get_by_interval(time_interval)
        # Bid quantities are what the supplier delivers (negative, its perspective); net of the transport losses they
        # are the campus demand at each of the flexible asset's breakpoints.
        at_the_campus = [-v.quantity - cor.loss_factor * v.quantity ** 2 / cor.maximum_power for v in curve.vertices]
        assert at_the_campus[:2] == pytest.approx([inelastic + 1000.0, inelastic], rel=1e-9), \
            'the bid to the supplier spans the flexible asset\'s range'
        assert curve.vertices[0].price < 0.03 and curve.vertices[0].price < curve.vertices[1].price < 0.06, \
            'the breakpoint prices, restated at the supplier (before its losses)'

    step_to(campus, clock, market.transition_times.delivery_lead)
    assert market.market_state is MarketState.DeliveryLead and market._stateIsCompleted
    for time_interval, price in zip(market.time_intervals, prices(market)):
        scheduled = flexible.scheduled_powers.get_by_interval(time_interval).value
        assert scheduled == flexible.default_vertices.power_from_vertices(price) != at_default, time_interval.name
        assert -1000.0 < scheduled < 0.0
        # Within a watt of balance: sum_vertices offsets the duplicated 0.03 breakpoint by 1e-10 $/kWh, which tilts the
        # segment the balance is interpolated on by a few parts per billion.
        assert sum(e.scheduled_powers.get_by_interval(time_interval).value
                   for e in campus.local_assets + campus.neighbors) == pytest.approx(0.0, abs=1e-3)
