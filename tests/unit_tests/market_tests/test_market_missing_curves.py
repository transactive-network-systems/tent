"""Market: system vertex summation."""
import pytest

from tent.local_asset import LocalAsset
from tent.market import Market
from tent.market.market_series import MarketSeries
from tent.utils.timer import Timer
from tests.mock_tn import MockTN


def test_sum_vertices_leaves_out_entities_without_a_curve_and_warns_once(caplog):  # audit T-18
    Timer.simulation = False
    tn = MockTN()
    scheduled = LocalAsset(transactive_node=tn, name='Load', default_power=-40.0)
    unscheduled = LocalAsset(transactive_node=tn, name='Late', default_power=-10.0)
    tn.local_assets = [scheduled, unscheduled]
    tn.neighbors = []
    series = MarketSeries(transactive_node=tn, name='Core', market_class=Market, intervals_to_clear=2)
    market = series.newest_market
    scheduled.schedule(market)
    ti = market.time_intervals[0]
    assert unscheduled.active_vertices.get_by_interval(ti) is None
    with caplog.at_level('WARNING'):
        curve = market.sum_vertices(ti)
        market.sum_vertices(ti)
        market.sum_vertices(market.time_intervals[1])
    assert [(v.price, v.quantity) for v in curve.vertices] == [(float('inf'), -40.0)], 'the scheduled asset alone'
    warnings = [r.message for r in caplog.records if 'left out of the system curve' in r.message]
    assert len(warnings) == 2, 'one warning per market and interval, not per call'
    assert all('Late' in w for w in warnings) and ti.name in warnings[0]
    unscheduled.schedule(market)
    curve = market.sum_vertices(ti)
    assert [(v.price, v.quantity) for v in curve.vertices] == [(float('inf'), -50.0)], 'both once both have curves'
