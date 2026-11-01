"""TemperatureForecastModel: the file is validated when configured and looked up by the hour (audit T-25, T-35, T-SEC-04)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from tent.containers.time_interval import TimeInterval
from tent.information_service_model.temperature_forecast_model import TemperatureForecastModel
from tent.utils.timer import Timer
from tests.mock_tn import MockTN

ROWS = ['Timestamp,Value'] + [f'7/1/2017 {hour}:00,{60 + hour}' for hour in range(0, 24) if hour != 5]


@pytest.fixture
def tn():
    Timer.simulation = False
    node = MockTN()
    node.tz = 'America/Los_Angeles'
    return node


@pytest.fixture
def weather_file(tmp_path):
    path = tmp_path / 'weather.csv'
    path.write_text('\n'.join(ROWS) + '\n')
    return path


def market_at(*starts, duration=timedelta(hours=1)):
    mkt = Mock()
    mkt.name = 'M'
    mkt.time_intervals = [TimeInterval(market=mkt, duration=duration, start_time=start) for start in starts]
    return mkt


def test_a_missing_configured_file_fails_at_configuration(tn, tmp_path):
    with pytest.raises(FileNotFoundError, match="weather file 'nowhere/weather.csv'.*does not exist"):
        TemperatureForecastModel(transactive_node=tn, name='W', weather_file_path='nowhere/weather.csv')
    with pytest.raises(FileNotFoundError, match='fallback.csv'):
        TemperatureForecastModel(transactive_node=tn, name='W', tmy_fallback_path=str(tmp_path / 'fallback.csv'))


def test_a_wrong_header_or_a_bad_row_names_the_problem(tn, tmp_path):
    bad_header = tmp_path / 'bad_header.csv'
    bad_header.write_text('Time,Temperature\n7/1/2017 0:00,60\n')
    with pytest.raises(ValueError, match=r"has columns \['Time', 'Temperature'\]; it needs \['Timestamp', 'Value'\]"):
        TemperatureForecastModel(transactive_node=tn, name='W', weather_file_path=str(bad_header))
    bad_row = tmp_path / 'bad_row.csv'
    bad_row.write_text('Timestamp,Value\n7/1/2017 0:00,60\n7/1/2017 1:00,warm\n')
    with pytest.raises(ValueError, match='line 3 cannot be read'):
        TemperatureForecastModel(transactive_node=tn, name='W', weather_file_path=str(bad_row))


def test_service_settings_are_kept_with_or_without_a_file(tn, weather_file):
    with_file = TemperatureForecastModel(transactive_node=tn, name='W', weather_file_path=str(weather_file),
                                         location={'lat': 46.2}, max_gap_time=7200, temperature_point_name='oat')
    without = TemperatureForecastModel(transactive_node=tn, name='S', location={'lat': 46.2}, max_gap_time=7200)
    for model in (with_file, without):
        assert model.location == [{'lat': 46.2}] and model.max_gap_time == timedelta(hours=2)
    assert with_file.oat_point_name == 'oat' and without.oat_point_name == 'OutdoorAirTemperature'
    assert without.weather_file_path is None and without.weather_data == []


def test_the_path_is_resolved_and_archived_in_full(tn, weather_file, monkeypatch):  # audit T-SEC-04
    monkeypatch.chdir(weather_file.parent)
    model = TemperatureForecastModel(transactive_node=tn, name='W', weather_file_path='weather.csv')
    assert model.weather_file_path == str(weather_file.resolve())
    assert model.get_record()['weather_file_path'] == str(weather_file.resolve())
    column = next(c for d in TemperatureForecastModel.get_archive_definitions() if d['name'] == 'temperature_forecast_model'
                  for c in d['columns'] if c['name'] == 'weather_file_path')
    assert column['type'] == 'Text', 'a resolved path is not bounded by 255 characters'


def test_intervals_are_matched_by_hour_across_zones_and_offsets(tn, weather_file):  # audit T-35
    model = TemperatureForecastModel(transactive_node=tn, name='W', weather_file_path=str(weather_file))
    # 7/1/2017 10:00 in Los Angeles is 17:00 UTC; the interval starts a quarter past, with seconds.
    mkt = market_at(datetime(2017, 7, 1, 17, 15, 30, 250, tzinfo=timezone.utc),
                    datetime(2017, 7, 1, 11, 45, tzinfo=ZoneInfo('America/Los_Angeles')), duration=timedelta(minutes=15))
    model.update_information(mkt)
    assert [model.predicted_values.get_by_interval(ti).value for ti in mkt.time_intervals] == [70.0, 71.0]


def test_a_missing_hour_is_filled_from_a_neighbour_and_an_absent_one_is_an_error(tn, weather_file):  # audit T-35
    model = TemperatureForecastModel(transactive_node=tn, name='W', weather_file_path=str(weather_file))
    la = ZoneInfo('America/Los_Angeles')
    mkt = market_at(datetime(2017, 7, 1, 5, tzinfo=la))  # 05:00 is not in the file
    model.update_information(mkt)
    assert model.predicted_values.get_by_interval(mkt.time_intervals[0]).value == 66.0, 'the next hour is tried first'
    far = market_at(datetime(2017, 8, 1, 5, tzinfo=la))
    with pytest.raises(LookupError, match='no weather data for interval M@2017-08-01T05:00:00-07:00'):
        model.update_information(far)


def test_a_rewritten_file_is_reloaded(tn, weather_file):
    model = TemperatureForecastModel(transactive_node=tn, name='W', weather_file_path=str(weather_file))
    assert len(model.weather_data) == 23
    import os, time
    weather_file.write_text('Timestamp,Value\n7/1/2017 0:00,-40\n')
    os.utime(weather_file, (time.time() + 5, time.time() + 5))
    model.update_information(market_at(datetime(2017, 7, 1, 0, tzinfo=ZoneInfo('America/Los_Angeles'))))
    assert len(model.weather_data) == 1
