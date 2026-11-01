"""OpenLoopLoadPredictor finds the node's temperature forecaster by type (audit T-05)."""
from tent.enumerations import MeasurementType, MeasurementUnit
from tent.information_service_model.temperature_forecast_model import TemperatureForecastModel
from tent.local_asset.openloop_load_predictor import OpenLoopLoadPredictor
from tests.mock_tn import MockTN


def test_predictor_picks_up_the_temperature_forecast_model():
    tn = MockTN()
    forecaster = TemperatureForecastModel(transactive_node=tn, name='Weather', weather_file_path=None)
    assert forecaster.information_type is MeasurementType.Temperature
    assert forecaster.information_units is MeasurementUnit.degF
    assert not hasattr(forecaster, 'informationType'), 'the legacy attribute is gone'
    tn.information_service_models = [forecaster]
    predictor = OpenLoopLoadPredictor(transactive_node=tn, name='Load', cost_parameters=[], information_services=['Weather'],
                                      dow_intercept=[0.0] * 7, season=[1] * 12, values=[[0] * 24])
    assert predictor.temperature_forecaster is forecaster


def test_configuration_cannot_override_the_forecast_type():
    tn = MockTN()
    forecaster = TemperatureForecastModel(transactive_node=tn, name='Weather', weather_file_path=None,
                                          information_type=MeasurementType.Unknown)
    assert forecaster.information_type is MeasurementType.Temperature


def test_forecast_model_without_a_file_has_no_source():
    import pytest
    tn = MockTN()
    forecaster = TemperatureForecastModel(transactive_node=tn, name='Weather', weather_file_path=None)
    with pytest.raises(RuntimeError, match='no weather file'):
        forecaster.update_information(market=None)


# ---------------------------------------------------------------------------------------------------------------------
# schedule_power: load = -(day-of-week intercept + row intercept + row factor * temperature), the row of the 144-row
# coefficient table chosen by hour, season and regime (row = 6 * hour + season + regime - 1). The regime is 1 only in a
# spring or fall season (season 1 or 4) at or below 56.6 degrees; everywhere else it is 0.
# ---------------------------------------------------------------------------------------------------------------------
from datetime import datetime, timedelta, timezone

import pytest

from tent.containers.time_interval import TimeInterval

DOW_INTERCEPT = 146119
DEFAULT_ROW = [18836, -1375]
SPECIAL_ROW = [-124095, 1048]
SEASON = [3, 3, 0, 0, 0, 1, 1, 1, 2, 2, 2, 3]  # November is season 2, June is season 1


def row(hour, season, regime):
    return 6 * hour + season + regime - 1


class _Market:
    def __init__(self, tn, start: datetime, hours: int):
        self.name = self.market_series_name = 'Test_Market'
        self.id = 'test-market-id'
        self.data_manager = tn.data_manager
        self.time_intervals = [TimeInterval(market=self, duration=timedelta(hours=1), start_time=start + timedelta(hours=h))
                               for h in range(hours)]


def predictor(tn, forecaster, special_rows=()):
    values = [DEFAULT_ROW[:] for _ in range(144)]
    for index in special_rows:
        values[index] = SPECIAL_ROW
    model = OpenLoopLoadPredictor(dow_intercept=[DOW_INTERCEPT] * 7, season=SEASON, values=values,
                                  default_temperature=56.6, scale_factor=1.0, transactive_node=tn, name='Predictor')
    model.temperature_forecaster = forecaster
    return model


def load(coefficients, temperature):
    return -(DOW_INTERCEPT + coefficients[0] + coefficients[1] * temperature)


def forecast(tn, market, temperatures):
    forecaster = TemperatureForecastModel(transactive_node=tn, weather_file_path=None)
    for interval, temperature in zip(market.time_intervals, temperatures):
        forecaster.predicted_values.update(interval, temperature, archive=False)
    return forecaster


def test_schedule_power_picks_the_row_for_each_interval_hour_and_season():
    tn = MockTN()
    market = _Market(tn, datetime(2017, 11, 1, 12, tzinfo=timezone.utc), hours=2)  # noon and 13:00 in season 2
    model = predictor(tn, forecast(tn, market, [20, 100]), special_rows=[row(13, 2, 0)])
    model.schedule_power(market)
    assert model.scheduled_powers.get_by_interval(market.time_intervals[0]).value == pytest.approx(load(DEFAULT_ROW, 20))
    assert model.scheduled_powers.get_by_interval(market.time_intervals[1]).value == pytest.approx(load(SPECIAL_ROW, 100))
    assert model.schedule_calculated is True


def test_a_cool_spring_hour_uses_the_heating_regime_row():
    tn = MockTN()
    market = _Market(tn, datetime(2017, 6, 1, 12, tzinfo=timezone.utc), hours=2)  # June is season 1
    model = predictor(tn, forecast(tn, market, [50, 80]), special_rows=[row(12, 1, 1), row(13, 1, 1)])
    model.schedule_power(market)
    assert model.scheduled_powers.get_by_interval(market.time_intervals[0]).value == pytest.approx(load(SPECIAL_ROW, 50))
    assert model.scheduled_powers.get_by_interval(market.time_intervals[1]).value == pytest.approx(load(DEFAULT_ROW, 80))


def test_schedule_power_uses_the_default_temperature_without_a_forecaster():
    tn = MockTN()
    market = _Market(tn, datetime(2017, 11, 1, 12, tzinfo=timezone.utc), hours=1)
    model = predictor(tn, None)
    model.schedule_power(market)
    assert model.scheduled_powers.get_by_interval(market.time_intervals[0]).value == pytest.approx(load(DEFAULT_ROW, 56.6))
