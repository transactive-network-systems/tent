from copy import deepcopy
from datetime import datetime, timedelta
from sortedcontainers import SortedList
from typing import Type, Union
from weakref import proxy

from tent.base import Component
from tent.containers import MarketDurations
from tent.enumerations import MarketState, MarketTypes, Method
from tent.market import Market
from tent.utils.helpers import get_class_from_spec, safe_weak_proxy_callback, validate_bool


class MarketSeries(Component):
    # Keyword arguments that spawn() supplies to the market class; see __init__(**market_kwargs).
    SPAWN_KEYWORDS = frozenset({'transactive_node', 'market_series', 'initial_market_state', 'transition_times',
                                'market_to_be_refined', 'prior_market_in_series', 'price_model'})

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
    def __init__(self,
                 transactive_node,
                 name: str,
                 commitment: bool = False,                  # [Boolean] Are scheduled power & price commitments?
                 default_price: float = 0.05,               # [$/kWh] Static default price assignment
                 delivery_anchor_time: str | None = None,   # A time of day a market will always start (immediate if None)
                 description: str = '',
                 duality_gap_threshold: float = 0.01,       # [dimensionless] 0.01 = 1%
                 duration_active: float = 0.0,              # [seconds] Length of time between activation and negotiation.
                 duration_delivery_lead: float = 0.0,       # [seconds] Length of time between clearing and delivery.
                 duration_market_lead: float = 3600.0,      # [seconds] Length of the market_lead period.
                 duration_negotiation: float = 0.0,         # [seconds] Length of time allowed for negotiation.
                 duration_reconciliation: float = 0.0,      # [seconds] Length of time allotted to reconciliation.
                 future_horizon: Union[timedelta, float] = timedelta(hours=24),  # [seconds or timedelta] Forward window of price-discovery relevance. (Future Use.)
                 initial_market_state: Union[MarketState, str, int] = MarketState.Inactive, # New market's initial state
                 interval_duration: Union[timedelta, float] = timedelta(hours=1), # [seconds or timedelta] Length of 1 interval during delivery.
                 intervals_to_clear: int = 1,               # Number of market intervals to be cleared by each market.
                 market_class: Type[Market] = Market,       # The subclass of Market spawned by this series.
                 market_clearing_interval: float = 3600.0,  # [seconds] Time between successive market clearings.
                 market_order=1,                            # [pos. integer] Ordering of sequential markets  (Unused)
                 method: Union[Method, str, int] = Method.Interpolation,  # Balancing method (Interpolation is the only one)
                 price_model: dict = None,                  # Configuration spec for a PriceModel class.
                 refined_series=None,
                 **market_kwargs                            # Any further keywords are passed to market_class on spawn.
                 ):
        super(MarketSeries, self).__init__(description, name, transactive_node)
        # Keywords that are not MarketSeries parameters belong to the configured market class (e.g. MatchingEngine's
        # matching_strategy). They are stored as configured and forwarded verbatim by spawn(); the market class parses
        # them and records them in its own extension table. Names that spawn() sets itself cannot be configured.
        reserved = self.SPAWN_KEYWORDS.intersection(market_kwargs)
        if reserved:
            raise TypeError(f'MarketSeries {name}: {sorted(reserved)} are set by the series when it spawns markets '
                            f'and cannot be configured as market keywords.')
        self.market_kwargs: dict = dict(market_kwargs)
        self._validate_timings(name, duration_active=duration_active, duration_negotiation=duration_negotiation,
                               duration_market_lead=duration_market_lead, duration_delivery_lead=duration_delivery_lead,
                               duration_reconciliation=duration_reconciliation, interval_duration=interval_duration,
                               intervals_to_clear=intervals_to_clear, market_clearing_interval=market_clearing_interval)
        self.duration: MarketDurations = MarketDurations.new(duration_active, duration_negotiation,
                                                             duration_market_lead, duration_delivery_lead,
                                                             duration_reconciliation, interval_duration,
                                                             intervals_to_clear, market_clearing_interval,
                                                             delivery_anchor_time)
        self.markets = SortedList(key=lambda mkt: mkt.transition_times.delivery_lead)  # Sort markets by delivery lead time

        self.commitment: bool = validate_bool(commitment, 'commitment')
        self.default_price: float = float(default_price)
        self.duality_gap_threshold: float = float(duality_gap_threshold)
        self.future_horizon: timedelta = (future_horizon if isinstance(future_horizon, timedelta)
                                          else timedelta(seconds=future_horizon))
        # TODO: Should initial_market_state just always be INITIAL for first object and INACTIVE afterwards?
        self.initial_market_state: MarketState = (
            initial_market_state if isinstance(initial_market_state, MarketState)
            else MarketState(initial_market_state) if isinstance(initial_market_state, int)
            else MarketState[initial_market_state])
        if not issubclass(market_class, Market):
            raise ValueError(f'MarketSeries {self.name} received {market_class}, which is not a valid Market class.')
        self.market_class: Type[Market] = market_class
        self.market_order: int = int(market_order)
        self.market_type: MarketTypes = self.market_class.MARKET_TYPE
        self.method: Method = (method if isinstance(method, Method)
                               else Method(method) if isinstance(method, int)
                               else Method[method.title()])
        # The price model spec names the class (class_name, module_name) and carries its constructor keywords; the
        # series' default price is supplied unless the spec gives its own. The configured dict is not modified.
        self.price_model_spec: dict = {'class_name': 'HourlyAveragePriceModel', 'module_name': 'tent.market.price_models',
                                       'default_price': self.default_price}
        if price_model:
            if not isinstance(price_model, dict):
                raise TypeError(f'MarketSeries {name}: price_model must be a dict naming the class and its keywords, '
                                f'not {type(price_model).__name__}')
            self.price_model_spec.update(price_model)
        ref_series: MarketSeries = (self.tn.get_market_series_by_attribute('name', refined_series, first_only=True)
                                    if isinstance(refined_series, str) else refined_series)
        self.refined_series: Union[MarketSeries, None] = (
            proxy(ref_series, safe_weak_proxy_callback(self, 'refined_series'))
            if isinstance(ref_series, MarketSeries) else None)

        # Instantiate the first market in the series.
        self.next_clearing_time: datetime = self.duration.get_first_clearing_time()
        # The series row must exist before its first market references it (foreign-key order on PostgreSQL).
        self.archive()
        self.spawn(MarketState.Initial)

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
        spec = dict(self.price_model_spec)  # get_class_from_spec consumes class_name and module_name
        model_cls = get_class_from_spec(spec, 'HourlyAveragePriceModel', 'tent.market.price_models')
        try:
            return model_cls(**spec)
        except TypeError as error:
            raise TypeError(f'MarketSeries {self.name}: price_model {model_cls.__name__} rejected its keywords '
                            f'{sorted(spec)}: {error}') from error

    @staticmethod
    def _validate_timings(name: str, **timings):
        """Refuse timings that cannot make a working series, naming the configuration field."""
        def seconds(value):
            return value.total_seconds() if isinstance(value, timedelta) else float(value)
        for field in ('duration_active', 'duration_negotiation', 'duration_market_lead', 'duration_delivery_lead',
                      'duration_reconciliation'):
            if seconds(timings[field]) < 0:
                raise ValueError(f'MarketSeries {name}: {field} must not be negative; got {timings[field]!r}')
        for field in ('interval_duration', 'market_clearing_interval'):
            if seconds(timings[field]) <= 0:
                raise ValueError(f'MarketSeries {name}: {field} must be positive; got {timings[field]!r}')
        if int(timings['intervals_to_clear']) < 1:
            raise ValueError(f'MarketSeries {name}: intervals_to_clear must be at least 1; '
                             f'got {timings["intervals_to_clear"]!r}')

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