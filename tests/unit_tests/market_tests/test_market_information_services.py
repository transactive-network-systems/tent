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


from datetime import datetime, timezone
from unittest.mock import patch

from tent.enumerations import MarketState
from tent.information_service_model import InformationServiceModel
from tent.information_service_model.temperature_forecast_model import TemperatureForecastModel
from tent.local_asset.openloop_load_predictor import OpenLoopLoadPredictor
from tent.market.auction import Auction

START = datetime(2017, 7, 1, 0, 0, tzinfo=timezone.utc)


class BrokenService(InformationServiceModel):
    def update_information(self, market):
        raise RuntimeError('the service is down')


def _node_with_forecast(tmp_path, extra_services=()):
    Timer.simulation = False
    tn = MockTN()
    weather = tmp_path / 'weather.csv'
    weather.write_text('Timestamp,Value\n' + ''.join(f'7/1/2017 {h}:00,{60 + h}\n' for h in range(24)))
    forecaster = TemperatureForecastModel(transactive_node=tn, name='Weather', weather_file_path=str(weather))
    tn.information_service_models = [forecaster, *extra_services(tn)] if extra_services else [forecaster]
    # dow intercepts of zero, every month in season 1, every table row (intercept 0, factor 1): load is -temperature.
    predictor = OpenLoopLoadPredictor(transactive_node=tn, name='City', information_services=['Weather'],
                                      dow_intercept=[0.0] * 7, season=[1] * 12, values=[[0, 1]] * 144,
                                      default_temperature=56.6)
    tn.local_assets, tn.neighbors = [predictor], []
    with patch.object(Timer, 'now', return_value=START):
        series = MarketSeries(transactive_node=tn, name="RT", market_class=Auction, duration_market_lead=600.0,
                              duration_negotiation=600.0,
                              interval_duration=3600.0, intervals_to_clear=2, market_clearing_interval=3600.0)
    tn.market_series = [series]
    return tn, series, forecaster, predictor


def _run_to_negotiation(tn, market):
    with patch.object(Timer, 'now', return_value=market.transition_times.negotiation):
        for _ in range(6):
            tn.step()
            if market.market_state == MarketState.Negotiation:
                break
    assert market.market_state == MarketState.Negotiation


def test_information_services_are_updated_when_a_market_becomes_active(tmp_path):
    tn, series, forecaster, predictor = _node_with_forecast(tmp_path)
    market = series.newest_market
    assert forecaster.predicted_values.get_by_interval(market.time_intervals[0]) is None, 'nothing before Active'
    _run_to_negotiation(tn, market)
    hours = [ti.start_time.hour for ti in market.time_intervals]
    assert [forecaster.predicted_values.get_by_interval(ti).value for ti in market.time_intervals] == [60 + h for h in hours]
    assert [predictor.scheduled_powers.get_by_interval(ti).value for ti in market.time_intervals] == [-(60 + h) for h in hours], \
        'the predictor scheduled against the forecast, not its default temperature'
    assert market._information_services_updated


def test_a_failing_information_service_does_not_stop_the_market(tmp_path, caplog):
    tn, series, forecaster, predictor = _node_with_forecast(
        tmp_path, extra_services=lambda node: [BrokenService(node, name='Down', information_type='Temperature')])
    market = series.newest_market
    with caplog.at_level('ERROR'):
        _run_to_negotiation(tn, market)
    failures = [r for r in caplog.records if "information service 'Down' failed" in r.message]
    assert len(failures) == 1 and failures[0].exc_info is not None, 'logged once, with the traceback'
    assert [forecaster.predicted_values.get_by_interval(ti).value for ti in market.time_intervals] == \
        [60 + ti.start_time.hour for ti in market.time_intervals], 'the healthy service was still updated'
    assert predictor.scheduled_powers.get_by_interval(market.time_intervals[0]).value is not None
