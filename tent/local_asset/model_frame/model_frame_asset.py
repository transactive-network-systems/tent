import logging

from tent.containers.time_interval import TimeInterval
from tent.containers.vertex import Vertex
from tent.local_asset import LocalAsset
from tent.market import Market
from tent.utils.helpers import get_class_from_spec

from .models.base import ModelFrame
from ..occupancy_manager import OccupancyManager

_log = logging.getLogger(__name__)


class ModelFrameAsset(LocalAsset, ModelFrame):
    def __init__(self,
                 model_configs: dict = None,
                 temperature_forecast_name: str = '',
                 actuation_manager: dict = None,
                 occupancy_manager: dict = None,
                 *args, **kwargs):
        model_configs = model_configs if model_configs else {}
        ModelFrame.__init__(self, model_configs, **kwargs)
        LocalAsset.__init__(self, *args, **kwargs)

        self.temperature_forecast_name = temperature_forecast_name
        # self.actuation_method = actuation_method
        # self.actuator_identity = actuator_identity
        # self.ilc_target_topic = ilc_target_topic

        # Initialize Scheduler
        self.occupancy_manager: OccupancyManager | None = None
        if occupancy_manager:  # TODO: Implement default behavior if occupancy_manager is not specified (make an always occupied base class).
            om_class = get_class_from_spec(occupancy_manager, 'OccupancyManager', 'tent.local_asset.occupancy_manager')
            self.occupancy_manager = om_class(**occupancy_manager)

        self.actuation_manager = None
        for device_topic in self.models:
            if 'Uncontrolled' in device_topic:
                continue
            _log.info("Subscribing to " + device_topic)
            self.tn.register_subscription(subscribe_key=device_topic, callback=self.new_model_data)

            # Initialize Actuation Manager
            if actuation_manager:
                # TODO: The default module_prefix doesn't quite work with the default class.
                #  Need better detection logic in the helper function.
                am_class = get_class_from_spec(actuation_manager, 'ActuationManager', transactive_node=self.tn,
                                               default_module_prefix='tent.local_asset.actuation_manager')
                self.actuation_manager = am_class(parent=self, occupancy_manager=self.occupancy_manager,
                                                  **actuation_manager)

    def new_model_data(self, message: dict):
        """Ingest new data for models in ModelFrame.
            data: {'datetime': <datetime>, 'data': {<key>: <value>}}
        """
        now = message['datetime']
        for key, data in message['data'].items():
            if key in self.models:
                self.models[key].update_data(data, now)

    def _get_scheduled_power_from_model(self, time_interval: TimeInterval, outside_air_temperature) -> float:
        """Return a schedule power value for one interval from a model."""
        occupied = self.occupancy_manager.check_schedule(time_interval.start_time) if self.occupancy_manager else True
        params = {'interval_time': time_interval.start_time, 'OAT': outside_air_temperature, 'occupied': occupied}
        return self.model_power(params=params)

    def _get_power_flexibility_from_model(self, time_interval: TimeInterval, outside_air_temperature) -> list[float]:
        occupied = self.occupancy_manager.check_schedule(time_interval.start_time) if self.occupancy_manager else True
        params = {'interval_time': time_interval.start_time, 'OAT': outside_air_temperature, 'occupied': occupied}
        flexibility = self.model_flexibility(params=params)
        min_power = min(flexibility)
        max_power = max(flexibility)
        if min_power == max_power:
            min_power = min_power - 1e-10
        return [min_power, max_power]

    def _get_price_flexibility(self, time_interval: TimeInterval,   market: Market,
                               multiplier: float = 1.0) -> list[float]:
        # interval_price = market.marginalPrices.get_by_interval(time_interval)
        # effective_price = interval_price.value if interval_price else market.defaultPrice
        # min_price = 0.8 * effective_price
        # max_price = 1.2 * effective_price
        start_time = time_interval.start_time
        average_price, standard_deviation = market.price_model.get(start_time)
        min_price = average_price - (multiplier * standard_deviation)
        max_price = average_price + (multiplier * standard_deviation)
        return [min_price, max_price]

    def _create_vertices(self, power_flexibility: list[float], price_flexibility: list[float]) -> list[Vertex]:
        # Create the vertex that can represent this (lack of) flexibility
        # Vertex(marginal_price=, prod_cost=0.0, power=)
        lower_vertex = Vertex(price=price_flexibility[0], cost=0.0, quantity=power_flexibility[0])
        upper_vertex = Vertex(price=price_flexibility[1], cost=0.0, quantity=power_flexibility[1])
        return [lower_vertex, upper_vertex]

    def schedule_power(self, market):
        # Determine powers of an asset in active time intervals.
        # - Updates self.scheduledPowers - the schedule of power consumed
        temp_services = [x for x in self.information_services if x.name == self.temperature_forecast_name] if (self.information_services and self.temperature_forecast_name) else []
        temp_forecast = temp_services[0] if temp_services else None
        default_value = self.default_power

        for time_interval in market.time_intervals:
            outside_air_temperature = None
            if temp_forecast is not None:
                val = temp_forecast.predicted_values.get_by_interval(time_interval)
                if val is not None:
                    outside_air_temperature = val.value

            # Get new scheduled power value for this time interval:
            modeled_value = self._get_scheduled_power_from_model(time_interval, outside_air_temperature)
            value = modeled_value if modeled_value is not None else default_value  # Use default if modeled values is not available.

            # Check whether a scheduled power already exists for the indexed time interval:
            self.scheduled_powers.update(time_interval, value)

        self.schedule_calculated = True

    def update_vertices(self, market):
        """Create vertices to represent the asset's flexibility"""
        temp_services = [x for x in self.information_services if x.name == self.temperature_forecast_name] if (self.information_services and self.temperature_forecast_name) else []
        temp_forecast = temp_services[0] if temp_services else None

        for time_interval in market.time_intervals:
            outside_air_temperature = None
            if temp_forecast is not None:
                val = temp_forecast.predicted_values.get_by_interval(time_interval)
                if val is not None:
                    outside_air_temperature = val.value

            power_flexibility = self._get_power_flexibility_from_model(time_interval, outside_air_temperature)
            price_flexibility = self._get_price_flexibility(time_interval, market)
            self.active_vertices.update(time_interval, self._create_vertices(power_flexibility, price_flexibility))

    def actuate(self, mkt: Market):
        if getattr(self, "actuation_manager", None) is not None:
            self.actuation_manager.actuate(mkt)
        else:
            for model in self.models.values():
                if hasattr(model, "actuate"):
                    model.actuate(self, mkt)
