import logging

from datetime import datetime

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
                 transactive_node,
                 model_configs: dict = None,
                 temperature_forecast_name: str = '',
                 actuation_manager: dict = None,
                 occupancy_manager: dict = None,
                 *args, **kwargs):
        model_configs = model_configs if model_configs else {}
        ModelFrame.__init__(self, model_configs, **kwargs)
        LocalAsset.__init__(self, transactive_node, *args, **kwargs)

        self.temperature_forecast_name = temperature_forecast_name

        # TODO: Occupation and ActuationManagers should probably be loaded from class methods on the base classes.
        # Initialize Scheduler
        if occupancy_manager:  # TODO: Implement default behavior if occupancy_manager is not specified (make an always occupied base class).
            om_class = get_class_from_spec(occupancy_manager, 'SimpleOccupancyManager', 'tent.local_asset.occupancy_manager')
            self.occupancy_manager: OccupancyManager = om_class(**occupancy_manager)

        for subscribe_key in self.models:
            if 'Uncontrolled' in subscribe_key:
                continue
            self.tn.register_subscription(subscribe_key=subscribe_key, callback=self.new_model_data)

        # Initialize Actuation Manager
        if actuation_manager:
            # TODO: The default module_prefix doesn't quite work with the default class.
            #  Need better detection logic in the helper function.
            am_class = get_class_from_spec(actuation_manager, 'NullActuationManager', transactive_node=self.tn,
                                           default_module_prefix='tent.local_asset.actuation_manager')
            self.actuation_manager = am_class(parent=self, occupancy_manager=self.occupancy_manager,
                                              **actuation_manager)
            _log.info(f'Configured actuator {am_class.__name__} for asset: {self.name}')

    def new_model_data(self, time_received: datetime, subscribe_key: str, message: dict):
        """Ingest new data for models in ModelFrame.
        """
        if subscribe_key in self.models:
            self.models[subscribe_key].update_data(message, time_received)

    def _get_scheduled_power_from_model(self, time_interval: TimeInterval, outside_air_temperature) -> float:
        """Return a schedule power value for one interval from a model."""
        occupied = self.occupancy_manager.check_schedule(time_interval.start_time)
        params = {'interval_time': time_interval.start_time, 'OAT': outside_air_temperature, 'occupied': occupied}
        return self.model_power(params=params)

    def _get_power_flexibility_from_model(self, time_interval: TimeInterval, outside_air_temperature) -> list[float]:
        occupied = self.occupancy_manager.check_schedule(time_interval.start_time)
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

        temperature_forecast = [x for x in self.information_services if x.name == self.temperature_forecast_name][0]
        for time_interval in market.time_intervals:
            outside_air_temperature = temperature_forecast.predicted_values.get_by_interval(time_interval)
            # Get new scheduled power value for this time interval:
            if hasattr(outside_air_temperature, 'value'):
                modeled_value = self._get_scheduled_power_from_model(time_interval, outside_air_temperature.value)
            else:
                modeled_value = self.default_power
                # TODO: Should this use a default temp instead? Should the temp model return a default instead?
                _log.warning(f'No outside air temperature found for interval {time_interval.name}.'
                             f' Using default: ({self.default_power}) for scheduled power.')
            value = modeled_value if modeled_value else self.default_power  # Use default if modeled values is not available.

            # Check whether a scheduled power already exists for the indexed time interval:
            self.scheduled_powers.update(time_interval, value)

        self.schedule_calculated = True

    def update_vertices(self, market):
        """Create vertices to represent the asset's flexibility"""
        # Get temperature forecast
        temperature_forecast = [x for x in self.information_services if x.name == self.temperature_forecast_name][0]

        for time_interval in market.time_intervals:
            outside_air_temperature = temperature_forecast.predicted_values.get_by_interval(time_interval)
            if hasattr(outside_air_temperature, 'value'):
                power_flexibility = self._get_power_flexibility_from_model(time_interval, outside_air_temperature)
            else:
                # TODO: making power inflexible is probably a bad idea. What to do by default instead?
                power_flexibility = [self.default_power, self.default_power]
                # TODO: Should this use a default temp instead? Should the temp model return a default instead?
                _log.warning(f'No outside air temperature found for interval {time_interval.name}.'
                             f' Using default ({[self.default_power, self.default_power]}) for power flexibility.')

            price_flexibility = self._get_price_flexibility(time_interval, market)
            self.active_vertices.update(time_interval, self._create_vertices(power_flexibility, price_flexibility))

    def actuate(self, mkt: Market):
        self.actuation_manager.actuate(mkt)
