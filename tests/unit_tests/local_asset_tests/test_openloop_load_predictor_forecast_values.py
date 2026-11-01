"""OpenLoopLoadPredictor finds the node's temperature forecaster by type (audit T-05)."""
from tent.enumerations import MeasurementType, MeasurementUnit
from tent.information_service_model.temperature_forecast_model import TemperatureForecastModel
from tent.local_asset.openloop_load_predictor import OpenLoopLoadPredictor
from tent.utils.timer import Timer
from tests.mock_tn import MockTN


def test_predictor_picks_up_the_temperature_forecast_model():
    Timer.simulation = False
    tn = MockTN()
    forecaster = TemperatureForecastModel(transactive_node=tn, name='Weather', weather_file_path=None)
    assert forecaster.information_type is MeasurementType.Temperature
    assert forecaster.information_units is MeasurementUnit.degF
    assert not hasattr(forecaster, 'informationType'), 'the legacy attribute is gone'
    tn.information_service_models = [forecaster]
    predictor = OpenLoopLoadPredictor(transactive_node=tn, name='Load', information_services=['Weather'],
                                      dow_intercept=[0.0] * 7, season=[1] * 12, values=[[0] * 24])
    assert predictor.temperature_forecaster is forecaster


def test_configuration_cannot_override_the_forecast_type():
    import pytest
    Timer.simulation = False
    tn = MockTN()
    with pytest.raises(ValueError, match="TemperatureForecastModel 'Weather': information_type: is always Temperature"):
        TemperatureForecastModel(transactive_node=tn, name='Weather', weather_file_path=None,
                                 information_type=MeasurementType.Unknown)
    forecaster = TemperatureForecastModel(transactive_node=tn, name='Weather', weather_file_path=None,
                                          information_type='Temperature', information_units='degF')
    assert forecaster.information_type is MeasurementType.Temperature


def test_forecast_model_without_a_file_has_no_source():
    import pytest
    Timer.simulation = False
    tn = MockTN()
    forecaster = TemperatureForecastModel(transactive_node=tn, name='Weather', weather_file_path=None)
    with pytest.raises(RuntimeError, match='no weather file'):
        forecaster.update_information(market=None)


def test_a_forecast_of_zero_degrees_is_used_not_the_default():
    from datetime import datetime, timedelta, timezone
    from unittest.mock import Mock
    from tent.containers.time_interval import TimeInterval
    Timer.simulation = False
    tn = MockTN()
    forecaster = TemperatureForecastModel(transactive_node=tn, name='Weather')
    tn.information_service_models = [forecaster]
    # dow intercepts of zero, every month in season 1, every table row (intercept 0, factor 1): load is -temperature.
    predictor = OpenLoopLoadPredictor(transactive_node=tn, name='Load', information_services=['Weather'],
                                      dow_intercept=[0.0] * 7, season=[1] * 12, values=[[0, 1]] * 144,
                                      default_temperature=56.6)
    mkt = Mock(); mkt.name = 'M'
    start = datetime(2017, 1, 15, 12, tzinfo=timezone.utc)
    mkt.time_intervals = [TimeInterval(mkt, timedelta(hours=1), start + timedelta(hours=n)) for n in range(3)]
    frozen, missing, warm = mkt.time_intervals
    forecaster.predicted_values.update(frozen, 0.0)
    forecaster.predicted_values.update(warm, 80.0)
    predictor.schedule_power(mkt)
    assert predictor.scheduled_powers.get_by_interval(frozen).value == 0.0, 'zero degrees is a forecast'
    assert predictor.scheduled_powers.get_by_interval(missing).value == -56.6, 'no forecast: the default temperature'
    assert predictor.scheduled_powers.get_by_interval(warm).value == -80.0
