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
        self._price_spans: dict[str, tuple[float, float]] = {}  # interval name -> the price span its curve was built with

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

    # ------------------------------------------------------------------------------ the price to set-point mapping
    # The asset owns one mapping from a price to its models' set points, and uses it three times: the curve it offers
    # (update_vertices: the summed model power at the set points of each sweep ratio, at the price of that ratio), the
    # power it schedules at the market's price (schedule_power), and the set points the actuation manager writes for the
    # cleared price (set_points_at_price). The three therefore agree: the scheduled power is the curve read at the
    # price, and the devices are set to produce it. Each model's set point moves with the price in the direction in
    # which its consumption falls (ModelFrame.set_points_at_ratio), so the curve never consumes more at a higher price.

    def price_span(self, time_interval: TimeInterval, market: Market) -> tuple[float, float]:
        """[minimum, maximum] price of this asset's curve for the interval: the span the curve was built with when it
        exists (update_vertices keeps it, so a price model updated since does not move the mapping away from the curve
        in the book), otherwise the price model's average plus and minus one standard deviation."""
        span = self._price_spans.get(time_interval.name)
        if span is None:
            span = tuple(self._get_price_flexibility(time_interval, market))
        return span

    def price_ratio(self, price: float, time_interval: TimeInterval, market: Market) -> float:
        """Where `price` lies in the interval's price span, clamped to [0, 1]: 0 at or below the minimum price, 1 at
        or above the maximum. A span of no width (the price model reports no spread) maps every price to 0.5."""
        low, high = self.price_span(time_interval, market)
        if high <= low:
            return 0.5
        return (min(max(price, low), high) - low) / (high - low)

    def set_points_at_price(self, price: float, time_interval: TimeInterval, market: Market) -> dict:
        """Each model's set point for `price` in the interval, by subscribe key (see ModelFrame.set_points_at_ratio).
        With a single flexibility point the asset is inelastic and the set points are those of ratio 0 whatever the
        price, as its one vertex is."""
        ratio = 0.0 if self.demand_curve_points == 1 else self.price_ratio(price, time_interval, market)
        outside_air_temperature = self._outside_air_temperature(self._temperature_forecast(), time_interval)
        return self.set_points_at_ratio(ratio, self._model_params(time_interval, outside_air_temperature))

    def _market_price(self, time_interval: TimeInterval, market: Market) -> float:
        """The market's marginal price for the interval, or the series default price while it has none."""
        marginal_price = market.marginal_prices.get_by_interval(time_interval)
        return market.series.default_price if marginal_price is None or marginal_price.value is None \
            else marginal_price.value

    def actuation_price(self, time_interval: TimeInterval, market: Market) -> float:
        """
        The price this asset actuates on for the interval [$/kWh]: its own curve's price at the power the market cleared
        it to, so that the set points written are the ones the curve promised for what the asset traded. For a market
        that clears at one price the asset was scheduled from its curve at that price, so this is the market's marginal
        price again (to rounding, and clamped to the curve's span as the mapping clamps it). For an order book whose
        fills are at the resting orders' prices (continuous clearing) the asset's cleared power is the sum of its legs,
        and the curve's price for that power is what the asset itself asked for it, where the market's marginal price
        may be another participant's. The market's marginal price (or the series default) is used when the curve has no
        unique price at the cleared power: no curve or no cleared power for the interval, a single inelastic vertex, a
        vertical segment (two vertices at that quantity), or a power off the curve's ends.
        """
        fallback = self._market_price(time_interval, market)
        curve = self.active_vertices.get_by_interval(time_interval)
        power = market.cleared_power(self, time_interval)
        if curve is None or curve.empty or power is None or len(curve.vertices) < 2:
            return fallback
        vertices = list(curve.vertices)
        if power < vertices[0].quantity and not curve.is_at_end_vertex(power, vertices[0]):
            return fallback
        if power > vertices[-1].quantity and not curve.is_at_end_vertex(power, vertices[-1]):
            return fallback
        if any(a.quantity == power == b.quantity for a, b in zip(vertices, vertices[1:])):
            return fallback  # vertical there: several prices for the one quantity
        return curve.price_from_vertices(power, warn=False)

    def _temperature_forecast(self):
        services = [x for x in self.information_services if x.name == self.temperature_forecast_name] \
            if (self.information_services and self.temperature_forecast_name) else []
        return services[0] if services else None

    @staticmethod
    def _outside_air_temperature(temp_forecast, time_interval: TimeInterval):
        if temp_forecast is None:
            return None
        forecast = temp_forecast.predicted_values.get_by_interval(time_interval)
        if hasattr(forecast, 'value'):
            return forecast.value
        return forecast if isinstance(forecast, (int, float)) else None

    def _model_params(self, time_interval: TimeInterval, outside_air_temperature) -> dict:
        occupied = self.occupancy_manager.check_schedule(time_interval.start_time) if self.occupancy_manager else True
        return {'interval_time': time_interval.start_time, 'OAT': outside_air_temperature, 'occupied': occupied}

    def _get_scheduled_power_from_model(self, time_interval: TimeInterval, outside_air_temperature,
                                        market: Market) -> float:
        """The summed model power for the interval at the set points the market's price maps to."""
        params = self._model_params(time_interval, outside_air_temperature)
        set_points = self.set_points_at_price(self._market_price(time_interval, market), time_interval, market)
        return self.model_power_at(params, set_points)

    def _get_power_flexibility_from_model(self, time_interval: TimeInterval, outside_air_temperature) -> list[float]:
        """The summed model power at each sweep ratio (demand_curve_points values; see ModelFrame.sweep_ratios)."""
        return self.model_flexibility(params=self._model_params(time_interval, outside_air_temperature))

    def _get_price_flexibility(self, time_interval: TimeInterval,   market: Market,
                               multiplier: float = 1.0) -> list[float]:
        """[minimum, maximum] price of the curve: the price model's average minus and plus `multiplier` standard
        deviations at the interval's start."""
        start_time = time_interval.start_time
        average_price, standard_deviation = market.price_model.get(start_time)
        min_price = average_price - (multiplier * standard_deviation)
        max_price = average_price + (multiplier * standard_deviation)
        return [min_price, max_price]

    def _create_vertices(self, powers: list[float], price_flexibility: list[float]) -> list[Vertex]:
        """One vertex per sweep ratio: the price at that ratio of the price span and the summed power there. With two
        points these are the curve's ends; with one, a single (inelastic) vertex at the minimum price."""
        low, high = price_flexibility
        return [Vertex(price=low + ratio * (high - low), cost=0.0, quantity=power)
                for ratio, power in zip(self.sweep_ratios(), powers)]

    def schedule_power(self, market):
        """Schedule, for every interval, the summed model power at the set points the market's marginal price (the
        series default price while there is none) maps to: the same mapping the curve is built from and the actuation
        manager writes, so the schedule is the curve read at the price."""
        temp_forecast = self._temperature_forecast()
        if temp_forecast is not None and hasattr(temp_forecast, 'update_information'):
            temp_forecast.update_information(market)
        for time_interval in market.time_intervals:
            outside_air_temperature = self._outside_air_temperature(temp_forecast, time_interval)
            modeled_value = self._get_scheduled_power_from_model(time_interval, outside_air_temperature, market)
            value = modeled_value if modeled_value is not None else self.default_power
            self.scheduled_powers.update(time_interval, value)
        self.schedule_calculated = True

    def update_vertices(self, market):
        """Build the asset's curve for every interval: demand_curve_points vertices, each the summed model power at the
        set points of a sweep ratio, at the price of that ratio within the price model's span (kept per interval for
        price_span)."""
        temp_forecast = self._temperature_forecast()
        for time_interval in market.time_intervals:
            outside_air_temperature = self._outside_air_temperature(temp_forecast, time_interval)
            price_flexibility = self._get_price_flexibility(time_interval, market)
            self._price_spans[time_interval.name] = (price_flexibility[0], price_flexibility[1])
            powers = self._get_power_flexibility_from_model(time_interval, outside_air_temperature)
            self.active_vertices.update(time_interval, self._create_vertices(powers, price_flexibility), replace=True)

    def actuate(self, mkt: Market):
        if getattr(self, "actuation_manager", None) is not None:
            self.actuation_manager.actuate(mkt)
        else:
            for model in self.models.values():
                if hasattr(model, "actuate"):
                    model.actuate(self, mkt)
