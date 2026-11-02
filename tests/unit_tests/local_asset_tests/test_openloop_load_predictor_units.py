"""
T-63: the campus load predictor predicted generation in the evening because its weather file was in degrees Celsius
while the regression table (and TENT's forecast model) is in degrees Fahrenheit. These tests pin the table's behaviour
with Fahrenheit temperatures and the warning the forecast model gives for a Celsius-looking file.
"""
import csv
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tent.containers import TimeInterval
from tent.information_service_model.temperature_forecast_model import TemperatureForecastModel
from tent.local_asset.openloop_load_predictor import OpenLoopLoadPredictor
from tent.utils.timer import Timer
from tests.mock_tn import MockTN

REPO = Path(__file__).resolve().parents[3]
SAMPLE_CONFIG = REPO / 'sample_configs' / 'tn.campus.json'
# Pasco, WA, 2 July 2017, hourly dry-bulb temperatures in degF (the sample file's values, which are Celsius, converted).
PASCO_JULY_2_DEGF = [c * 9 / 5 + 32 for c in (20, 19, 18, 17, 15, 16, 17, 19, 21, 23, 24, 26,
                                               27, 26, 28, 28, 29, 29, 25, 23, 20, 18, 17, 18)]


class DummyMarket:
    BALANCE_POINT = 'scheduled_power'

    def __init__(self, tn):
        self.name, self.id, self.market_series_name, self.tn = 'M', 'M_id', 'MSeries', tn
        self.data_manager = tn.data_manager
        self.time_intervals = []


def campus_predictor_table():
    with open(SAMPLE_CONFIG) as f:
        assets = json.load(f)['local_assets']
    asset = next(a for a in assets if a['class_name'] == 'OpenLoopLoadPredictor')
    return {key: asset[key] for key in ('dow_intercept', 'season', 'values', 'scale_factor')}


class HourlyForecast(TemperatureForecastModel):
    """A temperature forecast from a dict of hour -> degF, standing in for the file."""
    def __init__(self, tn, temperatures):
        super().__init__(tn, name='Weather')
        self.temperatures = temperatures

    def update_information(self, market):
        for ti in market.time_intervals:
            self.predicted_values.update(ti, self.temperatures[ti.start_time.hour])


def test_the_campus_table_predicts_a_smooth_load_over_a_july_day_in_fahrenheit():
    Timer.simulation = False
    tn = MockTN()
    forecast = HourlyForecast(tn, PASCO_JULY_2_DEGF)
    tn.information_service_models = [forecast]
    predictor = OpenLoopLoadPredictor(tn, name='InelasticBuildings', information_services=['Weather'],
                                      minimum_power=-24200.0, **campus_predictor_table())
    market = DummyMarket(tn)
    start = datetime(2017, 7, 2, tzinfo=timezone.utc)  # a Sunday, like the container run
    market.time_intervals = [TimeInterval(market, timedelta(hours=1), start + timedelta(hours=h)) for h in range(24)]
    forecast.update_information(market)
    predictor.schedule_power(market)
    loads = [predictor.scheduled_powers.get_by_interval(ti).value for ti in market.time_intervals]
    assert all(load < 0 for load in loads), f'every hour of the day is a load: {loads}'
    assert -17500 < min(loads) and max(loads) < -7000, 'a campus of a few megawatts to seventeen'
    assert max(abs(b - a) for a, b in zip(loads, loads[1:])) < 3500, 'no jump between hours'
    assert all(-24200.0 <= load <= 0.0 for load in loads), 'within the configured limits'


def test_the_same_table_fed_celsius_predicts_generation_in_the_evening():
    """What the container run did (T-63): the Celsius file's 18 at 23:00 reads as 18 degF and flips the sign."""
    Timer.simulation = False
    tn = MockTN()
    forecast = HourlyForecast(tn, [(f - 32) * 5 / 9 for f in PASCO_JULY_2_DEGF])
    tn.information_service_models = [forecast]
    predictor = OpenLoopLoadPredictor(tn, name='InelasticBuildings', information_services=['Weather'],
                                      **campus_predictor_table())
    market = DummyMarket(tn)
    start = datetime(2017, 7, 2, tzinfo=timezone.utc)
    market.time_intervals = [TimeInterval(market, timedelta(hours=1), start + timedelta(hours=h)) for h in range(24)]
    forecast.update_information(market)
    predictor.schedule_power(market)
    at_23 = predictor.scheduled_powers.get_by_interval(market.time_intervals[23]).value
    assert at_23 == pytest.approx(645.4, abs=0.1), 'the generation the container run reported'


def write_weather(path, start, hours, values):
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Timestamp', 'Value'])
        for h, value in zip(range(hours), values):
            writer.writerow([(start + timedelta(hours=h)).strftime('%m/%d/%Y %H:%M'), value])


def test_a_weather_file_that_looks_like_celsius_is_reported(tmp_path, caplog):
    Timer.simulation = False
    tn = MockTN()
    hours = 24 * 120
    celsius = tmp_path / 'celsius.csv'
    write_weather(celsius, datetime(2017, 1, 1), hours, [10 + 25 * (h % 24) / 23 for h in range(hours)])  # tops at 35
    with caplog.at_level(logging.WARNING, logger='tent.information_service_model'):
        TemperatureForecastModel(tn, name='W', weather_file_path=str(celsius))
    assert any('look like degrees Celsius' in r.message and 'never exceed 35' in r.message for r in caplog.records)
    caplog.clear()
    fahrenheit = tmp_path / 'fahrenheit.csv'
    write_weather(fahrenheit, datetime(2017, 1, 1), hours, [50 + 45 * (h % 24) / 23 for h in range(hours)])
    short_cold = tmp_path / 'january.csv'
    write_weather(short_cold, datetime(2017, 1, 1), 24 * 10, [20 + (h % 24) for h in range(24 * 10)])  # ten days
    with caplog.at_level(logging.WARNING, logger='tent.information_service_model'):
        TemperatureForecastModel(tn, name='F', weather_file_path=str(fahrenheit))
        TemperatureForecastModel(tn, name='J', weather_file_path=str(short_cold))
    assert not [r for r in caplog.records if 'Celsius' in r.message], 'a Fahrenheit file, or a short cold one, is not'
