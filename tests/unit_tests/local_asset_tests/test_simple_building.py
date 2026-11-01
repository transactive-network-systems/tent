"""SimpleBuilding schedules every hour of the day (audit T-11) without mutating its default vertices (audit T-07)."""
from tent.local_asset.simple_building import SimpleBuilding
from tent.market import Market
from tent.market.market_series import MarketSeries
from tests.mock_tn import MockTN


def _hourly_market(tn):
    series = MarketSeries(transactive_node=tn, name='Hourly', market_class=Market, intervals_to_clear=24,
                          interval_duration=3600.0, market_clearing_interval=86400.0)
    tn.market_series = [series]
    return series.newest_market


def test_update_vertices_covers_all_24_hours():
    tn = MockTN()
    market = _hourly_market(tn)
    building = SimpleBuilding(transactive_node=tn, name='Building')
    tn.local_assets = [building]
    building.update_vertices(market)
    hours = set()
    for ti in market.time_intervals:
        hour = ti.start_time.hour
        hours.add(hour)
        curve = building.active_vertices.get_by_interval(ti)
        assert curve is not None, f'no curve for hour {hour}'
        if 5 <= hour <= 16:
            assert sorted(v.quantity for v in curve.vertices) == [-12.0, -8.0], f'hour {hour} is on-peak'
        else:
            assert [v.quantity for v in curve.vertices] == [-10.0], f'hour {hour} is off-peak'
    assert hours == set(range(24))


def test_default_vertices_are_not_shared_with_active_curves():
    tn = MockTN()
    market = _hourly_market(tn)
    building = SimpleBuilding(transactive_node=tn, name='Building')
    tn.local_assets = [building]
    defaults = [(v.price, v.quantity) for v in building.default_vertices.vertices]
    building.update_vertices(market)
    for ti in market.time_intervals:
        for v in building.active_vertices.get_by_interval(ti).vertices:
            assert all(v is not d for d in (building.off_peak_vertex, building.on_peak_vertex0, building.on_peak_vertex1))
            v.quantity = v.quantity * 2  # a correction applied to the active curve (losses, demand charges, ...)
    building.update_vertices(market)
    assert [(v.price, v.quantity) for v in building.default_vertices.vertices] == defaults
    ti = market.time_intervals[0]
    assert sorted(abs(v.quantity) for v in building.active_vertices.get_by_interval(ti).vertices) in ([10.0], [8.0, 12.0])
