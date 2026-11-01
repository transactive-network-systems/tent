"""Engagement, holding and disengagement costs are charged per interval from the engagement schedule and added to the
asset's production cost."""
import pytest

from tent.enumerations import MarketState
from tent.local_asset import LocalAsset
from tent.market.auction import Auction
from tent.market.market_series import MarketSeries
from tent.utils.timer import Timer
from tests.mock_tn import MockTN


@pytest.fixture
def asset_and_market():
    Timer.simulation = False
    tn = MockTN()
    asset = LocalAsset(transactive_node=tn, name='Chiller', cost_parameters=[0.0, 0.0, 0.0], default_power=-50.0,
                       engagement_costs=[1.0, 2.0, 3.0])  # [disengagement, hold, engagement] in dollars
    tn.local_assets = [asset]
    tn.neighbors = []
    series = MarketSeries(transactive_node=tn, name='Series', market_class=Auction,
                          initial_market_state=MarketState.Negotiation, duration_negotiation=600.0,
                          duration_delivery_lead=300.0, interval_duration=3600.0, intervals_to_clear=3)
    tn.market_series = [series]
    return asset, series.newest_market


def test_schedule_assigns_transition_costs(asset_and_market):
    asset, market = asset_and_market
    asset.schedule(market)
    intervals = list(market.time_intervals)
    costs = [asset.transition_costs.get_by_interval(ti).value for ti in intervals]
    # The template schedule engages every interval; the first has no predecessor and counts as unchanged.
    assert costs == [2.0, 2.0, 2.0], 'holding cost in every interval'


def test_switching_on_pays_the_engagement_cost_once(asset_and_market):
    asset, market = asset_and_market
    t0, t1, t2 = list(market.time_intervals)
    asset.engagement_schedule.update(t0, False)
    asset.engagement_schedule.update(t1, True)
    asset.engagement_schedule.update(t2, True)
    asset.assign_transition_costs(market)
    assert asset.transition_costs.get_by_interval(t0).value == 2.0, 'first interval: no predecessor, unchanged'
    assert asset.transition_costs.get_by_interval(t1).value == 3.0, 'off to on: engagement cost'
    assert asset.transition_costs.get_by_interval(t2).value == 2.0, 'stays on: holding cost'
    asset.engagement_schedule.update(t2, False, replace=True)
    asset.assign_transition_costs(market)
    assert asset.transition_costs.get_by_interval(t2).value == 1.0, 'on to off: disengagement cost'


def test_transition_cost_is_added_to_production_cost(asset_and_market):
    asset, market = asset_and_market
    asset.schedule(market)
    t0 = list(market.time_intervals)[0]
    # A consuming asset has no production cost of its own, so the production cost is the transition cost alone.
    assert asset._get_production_costs(t0) == pytest.approx(2.0)
