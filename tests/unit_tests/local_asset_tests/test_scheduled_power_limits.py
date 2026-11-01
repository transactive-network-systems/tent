"""LocalAsset.schedule holds scheduled powers within the configured limits."""
import pytest

from tent.local_asset import LocalAsset
from tent.market import Market
from tent.market.market_series import MarketSeries
from tent.utils.timer import Timer
from tests.mock_tn import MockTN


class Extrapolating(LocalAsset):
    """A model that predicts generation for a load in its first interval and a load beyond the limit in its second."""

    def schedule_power(self, market):
        for n, time_interval in enumerate(market.time_intervals):
            self.scheduled_powers.update(time_interval, [3000.0, -20000.0, -500.0][n])
        self.schedule_calculated = True


def market_with(tn, intervals=3):
    Timer.simulation = False
    series = MarketSeries(transactive_node=tn, name='Core', market_class=Market, intervals_to_clear=intervals)
    tn.market_series = [series]
    return series.newest_market


def test_powers_outside_the_limits_are_clamped_with_one_warning(caplog):
    tn = MockTN()
    asset = Extrapolating(transactive_node=tn, name='Buildings', cost_parameters=[0.0, 0.0, 0.0],
                          maximum_power=0.0, minimum_power=-16400.0)
    tn.local_assets = [asset]
    market = market_with(tn)
    with caplog.at_level('WARNING'):
        asset.schedule(market)
    t0, t1, t2 = market.time_intervals
    assert [asset.scheduled_powers.get_by_interval(ti).value for ti in (t0, t1, t2)] == [0.0, -16400.0, -500.0]
    assert [asset.active_vertices.get_by_interval(ti).vertices[0].quantity for ti in (t0, t1, t2)] == [0.0, -16400.0, -500.0], \
        'the curve is built from the clamped powers'
    warnings = [r.message for r in caplog.records if 'were clamped' in r.message]
    assert len(warnings) == 1, 'one line per asset and market, not per interval'
    assert '2 of 3 scheduled powers' in warnings[0] and 'Buildings' in warnings[0] and market.name in warnings[0]
    assert f'-20000.0 kW in {t1.name}, held at -16400.0 kW' in warnings[0], 'the worst excursion is named'


def test_an_asset_within_its_limits_is_untouched_and_silent(caplog):
    tn = MockTN()
    asset = LocalAsset(transactive_node=tn, name='Load', cost_parameters=[0.0, 0.0, 0.0], default_power=-50.0,
                       maximum_power=0.0, minimum_power=-100.0)
    tn.local_assets = [asset]
    market = market_with(tn)
    with caplog.at_level('WARNING'):
        asset.schedule(market)
    assert all(asset.scheduled_powers.get_by_interval(ti).value == -50.0 for ti in market.time_intervals)
    assert not [r for r in caplog.records if 'clamped' in r.message]


def test_without_a_configured_range_nothing_is_clamped(caplog):
    tn = MockTN()
    asset = LocalAsset(transactive_node=tn, name='Unbounded', cost_parameters=[0.0, 0.0, 0.0], default_power=-50.0)
    tn.local_assets = [asset]
    market = market_with(tn)
    with caplog.at_level('WARNING'):
        asset.schedule(market)
    assert all(asset.scheduled_powers.get_by_interval(ti).value == -50.0 for ti in market.time_intervals), \
        'the default limits (both 0.0) are no range and do not clamp'
    assert not [r for r in caplog.records if 'clamped' in r.message]
