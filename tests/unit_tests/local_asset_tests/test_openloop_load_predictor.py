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
    predictor = OpenLoopLoadPredictor(transactive_node=tn, name='Load', cost_parameters=[], information_services=['Weather'],
                                      dow_intercept=[0.0] * 7, season=[1] * 12, values=[[0] * 24])
    assert predictor.temperature_forecaster is forecaster


def test_configuration_cannot_override_the_forecast_type():
    Timer.simulation = False
    tn = MockTN()
    forecaster = TemperatureForecastModel(transactive_node=tn, name='Weather', weather_file_path=None,
                                          information_type=MeasurementType.Unknown)
    assert forecaster.information_type is MeasurementType.Temperature


def test_forecast_model_without_a_file_has_no_source():
    import pytest
    Timer.simulation = False
    tn = MockTN()
    forecaster = TemperatureForecastModel(transactive_node=tn, name='Weather', weather_file_path=None)
    with pytest.raises(RuntimeError, match='no weather file'):
        forecaster.update_information(market=None)
