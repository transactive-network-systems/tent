from datetime import time, timedelta
from typing import Union

from tent import Component
from tent.enumerations import MarketState, MarketTypes, Method
from tent.market import Market
from tent.market.price_models.hourly_average_price_model import HourlyAveragePriceModel
from tent.utils.helpers import validate_bool


# 240228DJH: TODO: Make MarketSeries reference another market series that is to be revised or corrected. If this is
#                  done, then individual markets in the series can easily find the specific market object that is to be
#                  revised or corrected. (This is pretty low priority)


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
    def __init__(self,
                 transactive_node,
                 activation_lead_time: Union[timedelta, float, int] = timedelta(hours=0),
                 commitment: bool = False,
                 default_price: float = 0.05,
                 delivery_anchor_time=None,
                 delivery_lead_time: Union[timedelta, float, int] = timedelta(hours=0),
                 description='',
                 duality_gap_threshold=0.01,
                 future_horizon: Union[timedelta, float, int] = timedelta(hours=24),
                 initial_market_state: Union[MarketState, str, int] = MarketState.Inactive,
                 interval_duration: Union[timedelta, float, int] = timedelta(hours=1),
                 intervals_to_clear: int = 1,
                 market_class: type = Market,
                 market_clearing_interval: Union[timedelta, float, int] = timedelta(hours=1),
                 market_lead_time: Union[timedelta, float, int] = timedelta(hours=0),
                 market_order=1,
                 market_type: Union[MarketTypes, str, int] = MarketTypes.unknown,
                 method: Union[Method, str, int] = Method.Interpolation,
                 name='Market Series',
                 negotiation_lead_time: Union[timedelta, float, int] = timedelta(hours=0)):
        super(MarketSeries, self).__init__(description, name, transactive_node)
        self._activation_lead_time = activation_lead_time if isinstance(activation_lead_time, timedelta) \
            else timedelta(seconds=activation_lead_time)  # [timedelta] Time in market state "Active"
        self.commitment = validate_bool(commitment, 'commitment')  # [Boolean] If true, scheduled power & price are
        #                                                            commitments
        self.default_price = float(default_price)  # [$/kWh] Static default price assignment
        # deliveryAnchorTime [time] A time of day a market will always start (start immediately if None)
        self.delivery_anchor_time = time.fromisoformat(delivery_anchor_time) if isinstance(delivery_anchor_time, str) \
            else delivery_anchor_time
        self._delivery_lead_time = delivery_lead_time if isinstance(delivery_lead_time, timedelta) \
            else timedelta(seconds=delivery_lead_time)  # [timedelta] Time in market state "DeliveryLead"
        self.duality_gap_threshold = float(duality_gap_threshold)  # [dimensionless]; 0.01 = 1%
        self.future_horizon = future_horizon if isinstance(future_horizon, timedelta) \
            else timedelta(seconds=future_horizon)  # Future functionality: future of price-discovery relevance
        self._initial_market_state = initial_market_state if isinstance(initial_market_state, MarketState) \
            else MarketState[initial_market_state]  # [MarketState] New market's initial state
        self._interval_duration = interval_duration if isinstance(interval_duration, timedelta) \
            else timedelta(seconds=interval_duration)  # [timedelta] Duration of this market's time intervals
        self._intervals_to_clear = int(intervals_to_clear)  # [int] Market intervals to be cleared by this market object
        self._market_clearing_interval = market_clearing_interval if isinstance(market_clearing_interval, timedelta) \
            else timedelta(seconds=market_clearing_interval)  # [timedelta] Time between successive market clearings
        self._market_lead_time = market_lead_time if isinstance(market_lead_time, timedelta) \
            else timedelta(seconds=market_lead_time)  # [timedelta] Time in market state "MarketLead"
        self.market_order = int(market_order)  # [pos. integer] Ordering of sequential markets  (Unused)
        self.market_series_name = str(name)  # Name of this class object. Replaces use of a market series
        #                                                  name to bind markets
        self.market_type = market_type if isinstance(market_type, MarketTypes) \
            else MarketTypes[market_type]  # [MarketTypes] enumeration
        if isinstance(method, Method):  # Solution method {1: subgradient, 2: interpolation}
            self.method = method
        elif isinstance(method, int):
            self.method = Method(method)
        else:
            self.method = Method[method]
        self.negotiation_lead_time = negotiation_lead_time if isinstance(negotiation_lead_time, timedelta) \
            else timedelta(seconds=negotiation_lead_time)  # [timedelta] Time in market state "Negotiation"

        # TODO: Does isNewestMarket need a setting for instantiation of some markets at startup?
        # 240206DJH: ANSWER: This is best handled by configuration of the initial market and its treatment by method
        #            spawn(). The "newest market" flag is held by the market that determines whether new markets must be
        #            spawned, based on comparison of a calculated activation time and the current time.
        #            A corrected assignment of isNewestMarket must always be checked as new markets are spawned.
        self.price_model: HourlyAveragePriceModel = HourlyAveragePriceModel(forgetting_factor=14,
                                                                            default_price=self.default_price)
        self.market_class: type = market_class  # %%% I think we need this pointer to the market class of this series.
        # 240222DJH: The intention here is to create a "sacrificial" market having the right market class. This is
        #           "sacrificial" because its market clearing time is assigned so that it will immediately prompt the
        #           spawning of a new and future markets using the market state machine. The sacrificial market
        #           is probably not meaningful because it will not have properly cleared. It might be within its
        #           delivery period, however.
        #           It appears that no market clearing time should be sent so that the market constructor will find an
        #           acceptable first market clearing time.
        initial_market: Market = self.market_class(
            activation_lead_time=self.activation_lead_time,
            commitment=self.commitment,
            default_price=self.default_price,
            delivery_anchor_time=self.delivery_anchor_time,
            delivery_lead_time=self.delivery_lead_time,
            duality_gap_threshold=self.duality_gap_threshold,
            future_horizon=self.future_horizon,
            initial_market_state=self.initial_market_state,
            interval_duration=self.interval_duration,
            intervals_to_clear=self.intervals_to_clear,
            market_clearing_interval=self.market_clearing_interval,
            market_lead_time=self.market_lead_time,
            market_order=self.market_order,
            market_series_name=self.market_series_name,
            market_type=self.market_type,
            method=self.method,
            negotiation_lead_time=self.negotiation_lead_time,
            transactive_node=self.tn,
            market_series=self,
            market_clearing_time=None
        )
        # 240221DJH: The market series, not myTransactiveNode, should keep track of its own active markets. We
        #            might have to manage BOTH places until this new architecture is proven and stable.
        self.active_markets = [initial_market]

        # TODO: Investigate removing tn.markets (All references will go through MarketSeries.markets).
        #  Alternately, have tn.markets be a WeakSet or WeakValueDict.
        self.tn.markets.append(self)

    @property
    def activation_lead_time(self):
        return self._activation_lead_time

    @property
    def delivery_lead_time(self):
        return self._delivery_lead_time

    @property
    def interval_duration(self):
        return self._interval_duration

    @property
    def intervals_to_clear(self):
        return self._intervals_to_clear

    @property
    def market_clearing_interval(self):
        return self._market_clearing_interval

    @property
    def market_lead_time(self):
        return self._market_lead_time

    @property
    def initial_market_state(self):
        return self._initial_market_state

    @property
    def table_name(self):
        return 'market_series'

    def get_record(self) -> dict:
        record = super(MarketSeries, self).get_record()
        record.update({
            'id': self.id,
            'market_class': self.market_class,
            'commitment': self.commitment,
            'default_price': self.default_price,
            'duality_gap_threshold': self.duality_gap_threshold,
            'future_horizon': self.future_horizon,
            'initial_market_state': self.initial_market_state,
            'market_clearing_interval': self.market_clearing_interval,
            'interval_duration': self.interval_duration,
            'number_of_intervals_to_clear': self.intervals_to_clear,
            'market_order': self.market_order,
            'method': self.method,
            'delivery_anchor_time': self.delivery_anchor_time,
            'active_duration': self.active_duration,
            'negotiation_duration': self.negotiation_lead_time,
            'market_lead_duration': self.market_lead_time,
            'delivery_lead_duration': self.delivery_lead_time,
            'delivery_duration': self.delivery_lead_time,
            'reconciliation_duration': self.reconciliation_duration
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
                    {"name": "future_horizon", "type": "DateTimeRangeType"},
                    {"name": "initial_market_state", "enumeration": "MarketState"},
                    {"name": "market_clearing_interval", "type": "DateTimeRangeType"},
                    {"name": "interval_duration", "type": "DateTimeRangeType"},
                    {"name": "number_of_intervals_to_clear", "type": "Integer"},
                    {"name": "market_order", "type": "Integer"},
                    {"name": "method", "enumeration": "Method"},
                    {"name": "delivery_anchor_time", "type": "Time"},
                    {"name": "active_duration", "type": "DateTimeRangeType"},
                    {"name": "negotiation_duration", "type": "DateTimeRangeType"},
                    {"name": "market_lead_duration", "type": "DateTimeRangeType"},
                    {"name": "delivery_lead_duration", "type": "DateTimeRangeType"},
                    {"name": "delivery_duration", "type": "DateTimeRangeType"},
                    {"name": "reconciliation_duration", "type": "DateTimeRangeType"}
                ]
            }
        ])
        return definitions