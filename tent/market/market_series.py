import logging

from copy import deepcopy
from datetime import datetime, timedelta
from sortedcontainers import SortedList
from typing import Mapping, Type, Union
from weakref import proxy

from tent import registry
from tent.base import Component
from tent.config import (ConfigModel, Configurable, ConfigurationError, NamedConfig, NonNegativeSeconds,
                         PositiveSeconds, ReconfigurationError, Setting, TimeOfDay, class_spec, enum_by_name)
from tent.containers import MarketDurations
from tent.enumerations import MarketState, MarketTypes, Method
from tent.market import Market
from tent.market.price_models import PriceModel
from tent.utils.helpers import get_class_from_spec, safe_weak_proxy_callback, spec_kwargs


_log = logging.getLogger(__name__)


class MarketSeries(Component):
    """
    An object of this class contains all the critical timing and initialization properties of the market objects in a
    series of similar market objects. These properties were initially owned by the market objects and had to be passed
    from existing to succeeding market objects. Now, many of the properties have been converted to protected properties
    that are owned jointly by the MarketSeries and Market objects. Eventually, all the properties should be owned by the
    MarketSeries and not Market objects. This is easy to do because the base Market class now allows a weak reference to
    its MarketSeries object.
    Additionally, a MarketSeries constructs its first "dummy" market that jump starts the market state machine for all
    future markets in the series.
    """
    class Config(NamedConfig):
        commitment: bool = Setting(False, description='Whether cleared powers and prices are commitments.')
        default_price: float = Setting(0.05, ge=0, reconfigurable=True,
                                       description='[$/kWh] price assumed where no market has cleared one.')
        delivery_anchor_time: TimeOfDay | None = Setting(
            None, description='Time of day (HH:MM, node zone) at which a delivery period always starts; null means '
                              'the first market starts at once.')
        duality_gap_threshold: float = Setting(0.01, gt=0, reconfigurable=True,
                                               description='[dimensionless] relative duality gap below which a market '
                                                           'counts as converged; 0.01 is 1%.')
        duration_active: NonNegativeSeconds = Setting(0.0, description='[s] length of the Active period before '
                                                                       'negotiation.')
        duration_delivery_lead: NonNegativeSeconds = Setting(0.0, description='[s] time between clearing and delivery.')
        duration_market_lead: NonNegativeSeconds = Setting(3600.0, description='[s] length of the MarketLead period.')
        duration_negotiation: NonNegativeSeconds = Setting(0.0, description='[s] time allowed for negotiation.')
        duration_reconciliation: NonNegativeSeconds = Setting(0.0, description='[s] time allotted to reconciliation.')
        future_horizon: PositiveSeconds = Setting(86400, description='[s] forward window of price-discovery relevance '
                                                                    '(future use).')
        initial_market_state: enum_by_name(MarketState) = Setting('Inactive',
                                                                  description='State a new market starts in.')
        interval_duration: PositiveSeconds = Setting(3600, description='[s] length of one delivery interval.')
        intervals_to_clear: int = Setting(1, ge=1, description='Number of intervals each market clears.')
        market_clearing_interval: PositiveSeconds = Setting(3600.0, description='[s] time between successive market '
                                                                                'clearings.')
        market_order: int = Setting(1, ge=1, description='Order of this series among the node\'s series.')
        method: enum_by_name(Method) = Setting('Interpolation', description='Balancing method.')
        price_model: class_spec('HourlyAveragePriceModel') | None = Setting(
            None, spec_of='PriceModel', spec_default='HourlyAveragePriceModel',
            description='The price model (class_name, default HourlyAveragePriceModel, and its settings); the series '
                        'default_price is its default_price unless given here. Null means the default model.')
        refined_series: str | None = Setting(None, reference='market_series',
                                             description='Name of the series whose markets this series refines.')

    # Keys of a market_series configuration that are not settings of the series: its class and its market class.
    RUNTIME_SPEC_KEYS = frozenset({'class_name', 'module_name', 'market_class_name', 'market_module_name'})
    # Keyword arguments that spawn() supplies to the market class; see __init__(**market_kwargs).
    SPAWN_KEYWORDS = Market.RUNTIME_KEYWORDS

    def __init__(self, transactive_node, market_class: Type[Market] = Market, config: ConfigModel | None = None,
                 **kwargs):
        """
        `market_class` is the Market subclass this series spawns (the node resolves it from market_class_name). The
        keywords that are not settings of the series are settings of that market class, validated by its Config and
        forwarded verbatim by spawn(); the market class records them in its own extension table.
        """
        if not isinstance(market_class, type) or not issubclass(market_class, Market):
            raise ValueError(f'MarketSeries {kwargs.get("name", "")!r} received {market_class!r}, which is not a Market '
                             f'class.')
        series_settings, market_settings = self.split_settings(kwargs) if config is None else ({}, {})
        cfg = self._configure(config, series_settings)
        super(MarketSeries, self).__init__(cfg.description, cfg.name, transactive_node, config=cfg)
        self.market_class: Type[Market] = market_class
        self.market_config: ConfigModel = self._validate_market_settings(market_class, market_settings)
        self.market_kwargs: dict = self.market_config.model_dump()
        self.duration: MarketDurations = MarketDurations.new(cfg.duration_active, cfg.duration_negotiation,
                                                             cfg.duration_market_lead, cfg.duration_delivery_lead,
                                                             cfg.duration_reconciliation, cfg.interval_duration,
                                                             cfg.intervals_to_clear, cfg.market_clearing_interval,
                                                             cfg.delivery_anchor_time)
        self.markets = SortedList(key=lambda mkt: mkt.transition_times.delivery_lead)  # Sort markets by delivery lead time

        self.commitment: bool = cfg.commitment
        self.default_price: float = float(cfg.default_price)
        self.duality_gap_threshold: float = float(cfg.duality_gap_threshold)
        self.future_horizon: timedelta = cfg.future_horizon
        # TODO: Should initial_market_state just always be INITIAL for first object and INACTIVE afterwards?
        self.initial_market_state: MarketState = cfg.initial_market_state
        self.market_order: int = int(cfg.market_order)
        self.market_type: MarketTypes = self.market_class.MARKET_TYPE
        self.method: Method = cfg.method
        # The price model: class_name (HourlyAveragePriceModel unless the configuration says otherwise, module_name for
        # a class TENT does not ship) and the constructor keywords, default_price among them unless the configuration
        # overrides it. Resolved once; constructed at each spawn until a market exists to copy from.
        price_model_spec = cfg.price_model.spec() if cfg.price_model is not None else {'class_name': 'HourlyAveragePriceModel'}
        self.price_model_spec: dict = {'default_price': self.default_price, **price_model_spec}
        self.price_model_class: Type[PriceModel] = get_class_from_spec(self.price_model_spec, PriceModel)
        ref_series: MarketSeries = (self.tn.get_market_series_by_attribute('name', cfg.refined_series, first_only=True)
                                    if cfg.refined_series is not None else None)
        if cfg.refined_series is not None and not isinstance(ref_series, MarketSeries):
            raise ValueError(f'MarketSeries {self.name}: refined_series {cfg.refined_series!r} names no market series '
                             f'configured on the node (series are built in configuration order, so the refined series '
                             f'must come first).')
        self.refined_series: Union[MarketSeries, None] = (
            proxy(ref_series, safe_weak_proxy_callback(self, 'refined_series'))
            if isinstance(ref_series, MarketSeries) else None)

        # Instantiate the first market in the series.
        self.next_clearing_time: datetime = self.duration.get_first_clearing_time()
        # The series row must exist before its first market references it (foreign-key order on PostgreSQL).
        self.archive()
        self.spawn(MarketState.Initial)

    @classmethod
    def split_settings(cls, settings: Mapping) -> tuple[dict, dict]:
        """(settings of the series, settings of its market class) from one configuration's keywords."""
        series = {key: value for key, value in settings.items() if key in cls.Config.model_fields}
        market = {key: value for key, value in settings.items() if key not in cls.Config.model_fields}
        return series, market

    def _validate_market_settings(self, market_class: Type[Market], market_settings: Mapping) -> ConfigModel:
        reserved = self.SPAWN_KEYWORDS.intersection(market_settings)
        if reserved:
            raise TypeError(f'MarketSeries {self.name}: {sorted(reserved)} are set by the series when it spawns markets '
                            f'and cannot be configured as market keywords.')
        try:
            return market_class.validate_config(market_settings)
        except ConfigurationError as error:
            raise ConfigurationError(f'MarketSeries {self.name!r} (market settings of {error.owner})',
                                     error.problems) from None

    @classmethod
    def validate_spec(cls, spec: Mapping, market_class: Type[Market] | None = None) -> ConfigModel:
        """Validate a market_series configuration: the series settings through this Config, the remaining keys through
        the Config of the market class (`market_class`, or the one market_class_name names; Market when none)."""
        settings = {key: value for key, value in spec.items() if key not in cls.RUNTIME_SPEC_KEYS}
        series_settings, market_settings = cls.split_settings(settings)
        cfg = cls.validate_config(series_settings)
        cls.validate_nested_specs(cfg, cls.describe(settings))
        if market_class is None:
            market_class = (registry.resolve(spec['market_class_name'], Market, spec.get('market_module_name'))
                            if 'market_class_name' in spec else Market)
        if market_class.declares_config():
            try:
                market_class.validate_config(market_settings)
            except ConfigurationError as error:
                raise ConfigurationError(f'{cls.describe(settings)} (market settings of {error.owner})',
                                         error.problems) from None
        return cfg

    # ------------------------------------------------------------------------------------------- reconfiguration

    def check_reconfigure(self, changes: Mapping) -> ConfigModel:
        """Series settings and market settings may both be changed; each is checked against its own Config."""
        series_changes, market_changes = self.split_settings(changes)
        if market_changes:
            refused = sorted(set(market_changes) - self.market_class.Config.reconfigurable_fields())
            if refused:
                raise ReconfigurationError(
                    f'{self.describe(name=self.name)}: market settings {refused} cannot be changed on a running series '
                    f'(reconfigurable settings of {self.market_class.__name__} are '
                    f'{sorted(self.market_class.Config.reconfigurable_fields())}). Rebuild the node for other changes.')
            try:
                self._validate_market_settings(self.market_class, {**self.market_kwargs, **market_changes})
            except (ConfigurationError, TypeError) as error:
                raise ReconfigurationError(str(error)) from None
        return super().check_reconfigure(series_changes)

    def reconfigure(self, changes: Mapping) -> None:
        """Series settings are applied to the series; market settings to the live markets (each archives its own new
        version) and to every market spawned later. One configuration version records both."""
        series_changes, market_changes = self.split_settings(changes)
        new_config = self.check_reconfigure(changes)
        if market_changes:
            self.market_config = self._validate_market_settings(self.market_class,
                                                                {**self.market_kwargs, **market_changes})
            self.market_kwargs = self.market_config.model_dump()
            for market in list(self.markets):
                market.reconfigure(market_changes)
        self._apply_reconfiguration(new_config, set(series_changes))
        self.config = new_config
        self.config_version += 1
        _log.info(f'{self.describe(name=self.name)} reconfigured {sorted(changes)} (configuration version '
                  f'{self.config_version}).')
        self._configuration_changed(set(changes))

    def _apply_reconfiguration(self, cfg, changed: set[str]) -> None:
        super()._apply_reconfiguration(cfg, changed)
        if 'default_price' in changed:
            self.price_model_spec['default_price'] = self.default_price

    def configuration_record(self) -> dict:
        record = super().configuration_record()
        record['market_class_name'] = self.market_class.__name__
        record.update(self.market_config.model_dump(mode='json', fallback=repr))
        return record

    def diff_configuration(self, settings: Mapping) -> dict:
        """The changes (field -> new value) that would take this series from its configuration to `settings`, both the
        series settings and the market settings."""
        series_settings, market_settings = self.split_settings(settings)
        changes = Configurable.diff_configuration(self, series_settings)
        new_market = self._validate_market_settings(self.market_class, market_settings)
        for name in self.market_config.changed_fields(new_market):
            changes[name] = getattr(new_market, name)
        return changes

    def __iter__(self):
        """Allow iteration over the series' markets."""
        return iter(self.markets)

    def prune_expired(self):
        """Remove Expired markets to keep per-iteration cost bounded.

        Never removes the newest market (markets[-1]); spawn() relies on it for the
        prior_market_in_series chain. Live markets that held a weak proxy to a pruned
        market have that attribute auto-nulled by safe_weak_proxy_callback on GC, and
        check_marginal_prices treats None as 'fall through to the next pricing method'.
        """
        if len(self.markets) <= 1:
            return
        newest = self.markets[-1]
        expired = [m for m in list(self.markets)
                   if m is not newest and m.market_state == MarketState.Expired]
        for market in expired:
            self.markets.discard(market)

    def _build_price_model(self):
        """The first market's price model from the spec; every keyword in the spec other than the class and module names
        reaches the model's constructor, so a misspelt or unsupported keyword fails here rather than being ignored."""
        try:
            return self.price_model_class(**spec_kwargs(self.price_model_spec))
        except ConfigurationError as error:
            raise ConfigurationError(f'MarketSeries {self.name!r}: price_model {error.owner} rejected its keywords '
                                     f'{sorted(spec_kwargs(self.price_model_spec))}', error.problems) from None

    @property
    def intervals_to_clear(self):
        return self.duration.intervals_to_clear

    @property
    def newest_market(self):
        return self.markets[-1] if self.markets else None

    @newest_market.setter
    def newest_market(self, new_market):
        # SortedList does not implement append; use add to maintain ordering.
        try:
            self.markets.add(new_market)
        except AttributeError:
            # Fallback in case markets was replaced with a plain list in some contexts/tests.
            self.markets.add(new_market)

    def get_refined_market(self, clearing_time: datetime) -> Market:
        """Returns the market in the refined series with the last clearing time equal to or before "clearing_time"."""
        if not self.refined_series or not self.refined_series.markets:
            return None
        # markets is a SortedKeyList keyed by delivery-lead (clearing) time: find the last market cleared at or before.
        index = self.refined_series.markets.bisect_key_right(clearing_time)
        return self.refined_series.markets[index - 1] if index > 0 else None

    def spawn(self, initial_market_state: MarketState | None = None):
        """
        This method is called when a test determines that a new market object may be needed. The base method creates the
        new market object, as will be normal for systems having only one market. This method must be replaced or
        extended if
        (1) Still more markets should be instantiated, as may happen when market self is refined or corrected by another
        market series. If this is the case, not only the next needed market in this series, but also one or more markets
        in another market series must be instantiated.
        (2) The markets in this series are instantiated by another market series. In this case, this method should be
        replaced by a pass (no action).
        # TODO: Revise this docstring once the new architecture is finalized.
        :return: None
        """

        if self.newest_market:
            price_model_obj = deepcopy(self.newest_market.price_model)
        else:
            price_model_obj = self._build_price_model()
        new_market = self.market_class(
            transactive_node=self.tn,
            market_series=self,
            initial_market_state=initial_market_state if initial_market_state is not None else self.initial_market_state,
            transition_times=self.duration.get_timings(self.next_clearing_time),
            market_to_be_refined=self.get_refined_market(self.next_clearing_time),
            prior_market_in_series=self.newest_market if self.newest_market else None,
            price_model=price_model_obj,
            **self.market_kwargs
        )
        self.next_clearing_time += self.duration.market_clearing_interval
        self.newest_market = new_market
        self.tn.on_market_spawned(new_market)

    @property
    def table_name(self):
        return 'market_series'

    def get_record(self) -> dict:
        record = super(MarketSeries, self).get_record()
        record.update({
            'id': self.id,
            'market_class': self.market_class.__name__,
            'commitment': self.commitment,
            'default_price': self.default_price,
            'duality_gap_threshold': self.duality_gap_threshold,
            'future_horizon': self.future_horizon,
            'initial_market_state': self.initial_market_state,
            'market_clearing_interval': self.duration.market_clearing_interval,
            'market_interval_duration': self.duration.market_interval,
            'number_of_intervals_to_clear': self.intervals_to_clear,
            'market_order': self.market_order,
            'method': self.method,
            'delivery_anchor_time': self.duration.delivery_anchor_time,
            'active_duration': self.duration.active,
            'negotiation_duration': self.duration.negotiation,
            'market_lead_duration': self.duration.market_lead,
            'delivery_lead_duration': self.duration.delivery_lead,
            'delivery_duration': self.duration.delivery,
            'reconciliation_duration': self.duration.reconciliation
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(MarketSeries, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "market_series",
                "inherits": "component",
                "columns": [
                    {"name": "id", "foreign_key": "component.id", "kwargs": {"primary_key": True}},
                    {"name": "market_class", "type": "String", "type_args": [255]},
                    {"name": "commitment", "type": "Boolean"},
                    {"name": "default_price", "type": "Float"},
                    {"name": "duality_gap_threshold", "type": "Float"},
                    {"name": "future_horizon", "type": "Interval"},
                    {"name": "initial_market_state", "enumeration": "MarketState"},
                    {"name": "market_clearing_interval", "type": "Interval"},
                    {"name": "market_interval_duration", "type": "Interval"},
                    {"name": "number_of_intervals_to_clear", "type": "Integer"},
                    {"name": "market_order", "type": "Integer"},
                    {"name": "method", "enumeration": "Method"},
                    {"name": "delivery_anchor_time", "type": "Time"},
                    {"name": "active_duration", "type": "Interval"},
                    {"name": "negotiation_duration", "type": "Interval"},
                    {"name": "market_lead_duration", "type": "Interval"},
                    {"name": "delivery_lead_duration", "type": "Interval"},
                    {"name": "delivery_duration", "type": "Interval"},
                    {"name": "reconciliation_duration", "type": "Interval"}
                ]
            }
        ])
        return definitions