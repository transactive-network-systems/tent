"""TemperatureForecastModel: the file-backed hourly temperature forecast."""
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tent.information_service_model.temperature_forecast_model import TemperatureForecastModel
from tests.integration_tests.fixtures.sample_weather_data import temperature_at, write_weather_csv
from tests.mock_tn import MockTN
from tests.stubs import make_market

START = datetime(2017, 7, 1)  # naive in the file; the model reads it in the node's zone


@pytest.fixture
def weather_file(tmp_path):
    path = tmp_path / 'weather.csv'
    write_weather_csv(path, START, hours=48)
    return path


def model(tn, path, **kwargs):
    return TemperatureForecastModel(transactive_node=tn, name='Weather', weather_file_path=str(path), **kwargs)


def test_rows_are_read_as_aware_hourly_timestamps_in_the_node_zone(weather_file):
    tn = MockTN()
    tn.tz = 'America/Los_Angeles'
    forecaster = model(tn, weather_file)
    assert len(forecaster.weather_data) == 48
    first = forecaster.weather_data[0]
    assert first['Timestamp'] == START.replace(tzinfo=ZoneInfo('America/Los_Angeles'))
    assert first['Value'] == temperature_at(START)
    assert all(row['Timestamp'].minute == 0 for row in forecaster.weather_data)


def test_update_information_fills_every_interval_from_the_matching_hour(weather_file):
    tn = MockTN()
    market = make_market(tn, datetime(2017, 7, 1, 6, tzinfo=timezone.utc), intervals_to_clear=24)
    forecaster = model(tn, weather_file)
    forecaster.update_information(market)
    for ti in market.time_intervals:
        assert forecaster.predicted_values.get_by_interval(ti).value == temperature_at(ti.start_time)


def test_a_missing_hour_falls_back_to_the_nearest_hour_within_a_day(tmp_path):
    path = tmp_path / 'gappy.csv'
    rows = write_weather_csv(path, START, hours=48)
    kept = [r for r in rows if r[0].hour != 12]  # drop both noon rows
    path.write_text('Timestamp,Value\n' + ''.join(f'{w:%Y-%m-%d %H:%M:%S},{v}\n' for w, v in kept))
    tn = MockTN()
    market = make_market(tn, datetime(2017, 7, 1, 11, tzinfo=timezone.utc), intervals_to_clear=3)  # 11:00, 12:00, 13:00
    forecaster = model(tn, path)
    forecaster.update_information(market)
    noon = market.time_intervals[1]
    assert forecaster.predicted_values.get_by_interval(noon).value == temperature_at(datetime(2017, 7, 1, 13)), \
        'the nearest later hour (delta -1 is tried first) stands in for the missing one'


def test_an_interval_outside_the_file_is_an_error(weather_file):
    tn = MockTN()
    market = make_market(tn, datetime(2017, 8, 1, tzinfo=timezone.utc))
    forecaster = model(tn, weather_file)
    with pytest.raises(Exception, match='No weather data for time'):
        forecaster.update_information(market)


def test_a_changed_file_is_reread_on_the_next_update(weather_file):
    tn = MockTN()
    market = make_market(tn, datetime(2017, 7, 1, 6, tzinfo=timezone.utc), intervals_to_clear=2)
    forecaster = model(tn, weather_file)
    forecaster.update_information(market)
    write_weather_csv(weather_file, START, hours=48, temperature=lambda when: 99.0)
    os.utime(weather_file, (0, os.path.getmtime(weather_file) + 10))  # a distinct modification time
    forecaster.update_information(market)
    assert [forecaster.predicted_values.get_by_interval(ti).value for ti in market.time_intervals] == [99.0, 99.0]


def test_a_file_that_does_not_exist_leaves_the_model_without_a_source(tmp_path):
    tn = MockTN()
    forecaster = model(tn, tmp_path / 'absent.csv')
    assert forecaster.weather_file_path is None
    with pytest.raises(RuntimeError, match='no weather file'):
        forecaster.update_information(make_market(tn))


def test_the_tmy_fallback_path_is_the_file_for_a_service_backed_model(weather_file):
    tn = MockTN()
    forecaster = TemperatureForecastModel(transactive_node=tn, name='Weather', location={'lat': 46.2, 'lon': -119.1},
                                          max_gap_time=7200, temperature_point_name='OAT', tmy_fallback_path=str(weather_file))
    assert forecaster.weather_file_path == str(weather_file) and len(forecaster.weather_data) == 48
    assert forecaster.location == [{'lat': 46.2, 'lon': -119.1}]
    assert forecaster.max_gap_time == timedelta(hours=2) and forecaster.oat_point_name == 'OAT'


@pytest.mark.xfail(strict=True, reason='TemperatureForecastModel.init_weather_data swallows a parse error, logs a warning '
                                       'and continues with no data, so a malformed weather file surfaces only when a '
                                       'market asks for a forecast. A file that cannot be parsed is a configuration '
                                       'error and should fail at construction. Found in WP-7; the fix is in '
                                       'information_service_model (not this work package).')
def test_a_malformed_file_is_rejected_at_construction(tmp_path):
    path = tmp_path / 'bad.csv'
    path.write_text('Time,Temperature\n2017-07-01 00:00:00,70\n')  # the legacy fixture's columns
    with pytest.raises(Exception):
        model(MockTN(), path)
