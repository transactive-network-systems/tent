"""Weather reaches the market: the configured TemperatureForecastModel reads its file, forecasts every market interval,
and the OpenLoopLoadPredictor schedules the building from those forecasts."""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tent.enumerations import MarketState
from tests.integration_tests.fixtures.sample_weather_data import temperature_at, write_weather_csv
from tests.mock_tn import MockTN
from tests.stubs import clock_at

SAMPLE_CONFIG = Path(__file__).resolve().parents[2] / 'sample_configs' / 'tn.campus.json'
SIMULATION_START = datetime(2017, 7, 1)  # the campus config's simulation_start_time, in its zone (UTC)


def configured_node(tmp_path, tag: str, temperature=temperature_at) -> MockTN:
    """The campus node with a 72-hour weather file written for this test and a fresh sqlite archive."""
    with open(SAMPLE_CONFIG) as f:
        configs = json.load(f)
    weather = tmp_path / f'weather_{tag}.csv'
    write_weather_csv(weather, SIMULATION_START, hours=72, temperature=temperature)
    for ism in configs['information_service_models']:
        ism['weather_file_path'] = str(weather)
    configs['data_manager']['db_params']['url'] = f'sqlite:///{tmp_path / f"{tag}.sqlite"}'
    tn = MockTN()
    tn.configure_node(configs)
    return tn


def forecast_model(tn):
    return next(ism for ism in tn.information_service_models if ism.name == 'TemperatureForecast')


def schedule_through_negotiation(tn, market):
    """Step the node until the market has scheduled its assets (Active -> Negotiation)."""
    for when in (market.transition_times.active, market.transition_times.active + timedelta(seconds=1)):
        clock_at(when, tn.tz)
        tn.step()
    assert market.market_state in (MarketState.Negotiation, MarketState.MarketLead), market.market_state


def test_the_configured_model_reads_the_whole_file(tmp_path):
    tn = configured_node(tmp_path, 'read')
    model = forecast_model(tn)
    assert len(model.weather_data) == 72
    assert model.weather_data[0]['Timestamp'] == tn.simulation_start_time


def test_every_interval_of_every_market_gets_the_hour_temperature(tmp_path):
    tn = configured_node(tmp_path, 'forecast')
    model = forecast_model(tn)
    markets = [series.newest_market for series in tn.market_series]
    assert len(markets) == 2 and all(len(m.time_intervals) == 24 for m in markets)
    for market in markets:
        model.update_information(market)
        for ti in market.time_intervals:
            assert model.predicted_values.get_by_interval(ti).value == temperature_at(ti.start_time), (market.name, ti.name)


@pytest.mark.xfail(strict=True, reason='Nothing in the auction flow calls TemperatureForecastModel.update_information for '
                                       'the OpenLoopLoadPredictor (only ModelFrameAsset updates its own named forecast), '
                                       'so predicted_values stay empty and schedule_power uses default_temperature for '
                                       'every interval: the configured weather file never reaches the market. Found in '
                                       'WP-7 while making the weather integration test assert; the fix belongs in '
                                       'local_asset (WP-4 scope).')
def test_the_building_schedule_follows_the_temperature(tmp_path):
    mild = configured_node(tmp_path, 'mild', temperature=lambda when: 65.0)
    hot = configured_node(tmp_path, 'hot', temperature=lambda when: 95.0)
    loads = {}
    for tag, tn in (('mild', mild), ('hot', hot)):
        market = tn.market_series[0].newest_market  # the day-ahead auction
        schedule_through_negotiation(tn, market)
        building = tn.get_assets_by_attribute('name', 'InelasticBuildings', first_only=True)
        powers = [building.scheduled_powers.get_by_interval(ti) for ti in market.time_intervals]
        assert all(p is not None and p.value < 0 for p in powers), f'{tag}: the building is a load in every interval'
        loads[tag] = [p.value for p in powers]
    assert all(h != m for h, m in zip(loads['hot'], loads['mild'])), 'the forecast temperature changes every hour\'s load'
    assert sum(loads['hot']) < sum(loads['mild']), 'a hotter day is a larger cooling load'
