import logging

from datetime import datetime

from tent import registry
from tent.config import ConfigModel, ReconfigurationError, Setting, class_spec
from tent.containers.time_interval import TimeInterval
from tent.containers.vertex import Vertex
from tent.local_asset import LocalAsset
from tent.market import Market

from .models.base import Model, ModelFrame, ModelFrameConfigs
from ..actuation_manager import ActuationManager
from ..occupancy_manager import OccupancyManager

_log = logging.getLogger(__name__)


class ModelFrameAsset(LocalAsset, ModelFrame):
    class Config(LocalAsset.Config):
        model_configs: ModelFrameConfigs | None = Setting(
            None, reconfigurable=True,
            description='The device models of the frame (demand_curve_points and models). A running asset takes a '
                        'change that keeps the same models (by subscribe_key and class) and changes only their '
                        'reconfigurable settings; anything else rebuilds the node.')
        temperature_forecast_name: str = Setting(
            '', reconfigurable=True, reference='information_service_models',
            description='Name of the temperature forecast service (among information_services) the models use.')
        actuation_manager: class_spec('NullActuationManager') | None = Setting(
            None, spec_of='ActuationManager', spec_default='NullActuationManager',
            description='The actuation manager that writes cleared schedules to the devices; class_name defaults to '
                        'NullActuationManager. Null means no actuation manager.')
        occupancy_manager: class_spec('SimpleOccupancyManager') | None = Setting(
            None, spec_of='OccupancyManager', spec_default='SimpleOccupancyManager', reconfigurable=True,
            description='The occupancy manager (schedule) that tells the models whether the building is occupied; '
                        'class_name defaults to SimpleOccupancyManager. Null means always occupied.')

    def __init__(self, transactive_node, config: ConfigModel | None = None, **kwargs):
        cfg = self._configure(config, kwargs)
        ModelFrame.__init__(self, cfg.model_configs)
        LocalAsset.__init__(self, transactive_node, config=cfg)

        self.temperature_forecast_name = cfg.temperature_forecast_name

        # The occupancy and actuation managers name their classes like every other configured component (see
        # tent.registry); SimpleOccupancyManager and NullActuationManager apply when a configuration names none.
        self.occupancy_manager: OccupancyManager | None = self._build_occupancy_manager(cfg.occupancy_manager)

        self.actuation_manager: ActuationManager | None = None
        for subscribe_key in self.models:
            if 'Uncontrolled' in subscribe_key:
                continue
            self.tn.register_subscription(subscribe_key=subscribe_key, callback=self.new_model_data)

        if cfg.actuation_manager is not None:
            spec = cfg.actuation_manager
            am_class = registry.resolve(spec.class_name, ActuationManager, spec.module_name)
            self.actuation_manager = am_class(parent=self, occupancy_manager=self.occupancy_manager,
                                              transactive_node=self.tn, **spec.settings())
            _log.info(f'Configured actuator {am_class.__name__} for asset: {self.name}')

    @staticmethod
    def _build_occupancy_manager(spec) -> OccupancyManager | None:
        if spec is None:
            return None
        om_class = registry.resolve(spec.class_name, OccupancyManager, spec.module_name)
        return om_class(**spec.settings())

    # ------------------------------------------------------------------------------------------- reconfiguration

    def check_reconfigure(self, changes):
        new_config = super().check_reconfigure(changes)
        if 'model_configs' in changes:
            self._model_changes(new_config.model_configs)  # raises when the models cannot take the change in place
        return new_config

    def _model_changes(self, new_frame: ModelFrameConfigs | None) -> list[tuple[Model, dict]]:
        """The per-model changes a new model_configs block asks for, when every model keeps its class and subscribe_key,
        the frame keeps its demand_curve_points, and only reconfigurable model settings differ."""
        who = self.describe(name=self.name)
        current = self.config.model_configs
        if (new_frame is None) != (current is None):
            raise ReconfigurationError(f'{who}: model_configs cannot be added to or removed from a running asset.')
        if new_frame is None:
            return []
        if new_frame.demand_curve_points != current.demand_curve_points:
            raise ReconfigurationError(f'{who}: model_configs.demand_curve_points cannot be changed on a running asset.')
        current_entries = {entry.subscribe_key: entry for entry in current.models}
        new_entries = {entry.subscribe_key: entry for entry in new_frame.models}
        if set(current_entries) != set(new_entries):
            raise ReconfigurationError(f'{who}: the set of models changed (by subscribe_key: '
                                       f'{sorted(set(current_entries) ^ set(new_entries))}); a running asset keeps its '
                                       f'models.')
        changes = []
        for key, entry in new_entries.items():
            before = current_entries[key]
            if (entry.class_name, entry.module_name) != (before.class_name, before.module_name):
                raise ReconfigurationError(f'{who}: model {key!r} changed class; a running asset keeps its models.')
            model = self.models[key]
            new_model_config = model.validate_config({**entry.settings(), 'subscribe_key': key,
                                                      'demand_curve_points': new_frame.demand_curve_points},
                                                     name=key)
            changed = model.config.changed_fields(new_model_config)
            if changed:
                try:
                    model.check_reconfigure({name: getattr(new_model_config, name) for name in changed})
                except ReconfigurationError as error:
                    raise ReconfigurationError(f'{who}: {error}') from None
                changes.append((model, {name: getattr(new_model_config, name) for name in changed}))
        return changes

    def _apply_reconfiguration(self, cfg, changed: set[str]) -> None:
        super()._apply_reconfiguration(cfg, changed - {'model_configs', 'occupancy_manager'})
        if 'model_configs' in changed:
            for model, model_changes in self._model_changes(cfg.model_configs):
                model.reconfigure(model_changes)
        if 'occupancy_manager' in changed:
            self.occupancy_manager = self._build_occupancy_manager(cfg.occupancy_manager)
            if self.actuation_manager is not None:
                self.actuation_manager.set_occupancy_manager(self.occupancy_manager)

    def new_model_data(self, time_received: datetime, subscribe_key: str, message: dict):
        """Ingest new data for models in ModelFrame (subscription callback: time received, key, message)."""
        if subscribe_key in self.models:
            self.models[subscribe_key].update_data(message, time_received)

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
        if temp_forecast is not None and hasattr(temp_forecast, 'update_information'):
            temp_forecast.update_information(market)

        for time_interval in market.time_intervals:
            outside_air_temperature = None
            if temp_forecast is not None:
                outside_air_temperature = temp_forecast.predicted_values.get_by_interval(time_interval)

            if hasattr(outside_air_temperature, 'value'):
                modeled_value = self._get_scheduled_power_from_model(time_interval, outside_air_temperature.value)
            elif outside_air_temperature is not None and isinstance(outside_air_temperature, (int, float)):
                modeled_value = self._get_scheduled_power_from_model(time_interval, outside_air_temperature)
            else:
                modeled_value = self._get_scheduled_power_from_model(time_interval, None)

            value = modeled_value if modeled_value is not None else self.default_power
            self.scheduled_powers.update(time_interval, value)

        self.schedule_calculated = True

    def update_vertices(self, market):
        """Create vertices to represent the asset's flexibility"""
        temp_services = [x for x in self.information_services if x.name == self.temperature_forecast_name] if (self.information_services and self.temperature_forecast_name) else []
        temp_forecast = temp_services[0] if temp_services else None

        for time_interval in market.time_intervals:
            outside_air_temperature = None
            if temp_forecast is not None:
                outside_air_temperature = temp_forecast.predicted_values.get_by_interval(time_interval)

            if hasattr(outside_air_temperature, 'value'):
                oat_val = outside_air_temperature.value
            elif outside_air_temperature is not None and isinstance(outside_air_temperature, (int, float)):
                oat_val = outside_air_temperature
            else:
                oat_val = None

            power_flexibility = self._get_power_flexibility_from_model(time_interval, oat_val)
            if power_flexibility is None:
                power_flexibility = [self.default_power, self.default_power]
                _log.warning(f'No outside air temperature found for interval {time_interval.name}.'
                             f' Using default ({power_flexibility}) for power flexibility.')

            price_flexibility = self._get_price_flexibility(time_interval, market)
            self.active_vertices.update(time_interval, self._create_vertices(power_flexibility, price_flexibility),
                                        replace=True)

    def actuate(self, mkt: Market):
        if getattr(self, "actuation_manager", None) is not None:
            self.actuation_manager.actuate(mkt)
        else:
            for model in self.models.values():
                if hasattr(model, "actuate"):
                    model.actuate(self, mkt)
