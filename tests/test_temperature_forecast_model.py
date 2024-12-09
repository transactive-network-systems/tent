from tent.information_service_model.temperature_forecast_model import TemperatureForecastModel
from tent.market import Market
from tent.utils.helpers import format_ts

# TODO: Move to tests directory and provide valid parameters to forecaster.
forecaster = TemperatureForecastModel(
    '/home/hngo/PycharmProjects/volttron-applications/pnnl/TNSAgent/campus_config.json')

# Create market with some time intervals
market = Market()
market.market_clearing_time = datetime.now().replace(minute=0, second=0, microsecond=0)
market.next_market_clearing_time = market.market_clearing_time + market.market_clearing_interval

# Test update_information
forecaster.update_information(market)

times = [format_ts(x.time_interval.start_time) for x in forecaster.predictedValues]

print(times)