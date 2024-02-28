import weakref

from datetime import time, timedelta
from typing import Union

from tent.enumerations.market_state import MarketState
from tent.enumerations.market_types import MarketTypes
from tent.enumerations.method import Method
from tent.utils.helpers import validate_bool
from tent.market.price_models.hourly_average_price_model import HourlyAveragePriceModel
from tent.market import Market


# 240228DJH: TODO: Make MarketSeries reference another market series that is to be revised or corrected. If this is
#                  done, then individual markets in the series can easily find the specific market object that is to be
#                  revised or corrected. (This is pretty low priority)


class MarketSeries:
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
                 activation_lead_time: Union[timedelta, float, int] = timedelta(hours=0),
                 commitment: bool = False,
                 default_price: float = 0.05,
                 delivery_anchor_time=None,
                 delivery_lead_time: Union[timedelta, float, int] = timedelta(hours=0),
                 duality_gap_threshold=0.01,
                 future_horizon: Union[timedelta, float, int] = timedelta(hours=24),
                 initial_market_state: Union[MarketState, str, int] = MarketState.Inactive,
                 interval_duration: Union[timedelta, float, int] = timedelta(hours=1),
                 intervals_to_clear: int = 1,
                 market_class: Market = Market,
                 market_clearing_interval: Union[timedelta, float, int] = timedelta(hours=1),
                 market_lead_time: Union[timedelta, float, int] = timedelta(hours=0),
                 market_order=1,
                 market_series_name='Market Series',
                 market_type: Union[MarketTypes, str, int] = MarketTypes.unknown,
                 method: Union[Method, str, int] = Method.Interpolation,
                 negotiation_lead_time: Union[timedelta, float, int] = timedelta(hours=0),
                 transactive_node=None):
        # Weak reference to myTransactiveNode object:
        self.tn = weakref.ref(transactive_node) if transactive_node and transactive_node() else None
        self._activationLeadTime = activation_lead_time if isinstance(activation_lead_time, timedelta) \
            else timedelta(seconds=activation_lead_time)  # [timedelta] Time in market state "Active"
        self.commitment = validate_bool(commitment, 'commitment')  # [Boolean] If true, scheduled power & price are
        #                                                            commitments
        self.defaultPrice = float(default_price)  # [$/kWh] Static default price assignment
        # deliveryAnchorTime [time] A time of day a market will always start (start immediately if None)
        self.deliveryAnchorTime = time.fromisoformat(delivery_anchor_time) if isinstance(delivery_anchor_time, str) \
            else delivery_anchor_time
        self._deliveryLeadTime = delivery_lead_time if isinstance(delivery_lead_time, timedelta) \
            else timedelta(seconds=delivery_lead_time)  # [timedelta] Time in market state "DeliveryLead"
        self.dualityGapThreshold = float(duality_gap_threshold)  # [dimensionless]; 0.01 = 1%
        self.futureHorizon = future_horizon if isinstance(future_horizon, timedelta) \
            else timedelta(seconds=future_horizon)  # Future functionality: future of price-discovery relevance
        self._initialMarketState = initial_market_state if isinstance(initial_market_state, MarketState) \
            else MarketState[initial_market_state]  # [MarketState] New market's initial state
        self._intervalDuration = interval_duration if isinstance(interval_duration, timedelta) \
            else timedelta(seconds=interval_duration)  # [timedelta] Duration of this market's time intervals
        self._intervalsToClear = int(intervals_to_clear)  # [int] Market intervals to be cleared by this market object
        self._marketClearingInterval = market_clearing_interval if isinstance(market_clearing_interval, timedelta) \
            else timedelta(seconds=market_clearing_interval)  # [timedelta] Time between successive market clearings
        self._marketLeadTime = market_lead_time if isinstance(market_lead_time, timedelta) \
            else timedelta(seconds=market_lead_time)  # [timedelta] Time in market state "MarketLead"
        self.marketOrder = int(market_order)  # [pos. integer] Ordering of sequential markets  (Unused)
        self.marketSeriesName = str(market_series_name)  # Name of this class object. Replaces use of a market series
        #                                                  name to bind markets
        self.marketType = market_type if isinstance(market_type, MarketTypes) \
            else MarketTypes[market_type]  # [MarketTypes] enumeration
        if isinstance(method, Method):  # Solution method {1: subgradient, 2: interpolation}
            self.method = method
        elif isinstance(method, int):
            self.method = Method(method)
        else:
            self.method = Method[method]
        self.negotiationLeadTime = negotiation_lead_time if isinstance(negotiation_lead_time, timedelta) \
            else timedelta(seconds=negotiation_lead_time)  # [timedelta] Time in market state "Negotiation"

        # TODO: Does isNewestMarket need a setting for instantiation of some markets at startup?
        # 240206DJH: ANSWER: This is best handled by configuration of the initial market and its treatement by method
        #            spawn(). The "newest market" flag is held by the market that determines whether new markets must be
        #            spawned, based on comparison of a calculated actcivation time and the current time.
        #            A corrected assignment of isNewestMarket must always be checked as new markets are spawned.
        self.priceModel: HourlyAveragePriceModel = HourlyAveragePriceModel(forgetting_factor=14,
                                                                           default_price=self.defaultPrice)
        self.marketClass: Market = market_class  # %%% I think we need this pointer to the market class of this series.
        # 240222DJH: The intention here is to create a "sacrificial" market having the right market class. This is
        #           "sacrificial" because its market clearing time is assigned so that it will immediately prompt the
        #           spawning of a new and future markets using the market state machine. The sacrificial market
        #           is probably not meaningful because it will not have properly cleared. It might be within its
        #           delivery period, however.
        #           It appears that no market clearing time should be sent so that the market constructor will find an
        #           acceptable first market clearing time.
        initial_market: Market = self.marketClass(
            activation_lead_time=self.activationLeadTime,
            commitment=self.commitment,
            default_price=self.defaultPrice,
            delivery_anchor_time=self.deliveryAnchorTime,
            delivery_lead_time=self.deliveryLeadTime,
            duality_gap_threshold=self.dualityGapThreshold,
            future_horizon=self.futureHorizon,
            initial_market_state=self.initialMarketState,
            interval_duration=self.intervalDuration,
            intervals_to_clear=self.intervalsToClear,
            market_clearing_interval=self.marketClearingInterval,
            market_lead_time=self.marketLeadTime,
            market_order=self.marketOrder,
            market_series_name=self.marketSeriesName,
            market_type=self.marketType,
            method=self.method,
            negotiation_lead_time=self.negotiationLeadTime,
            transactive_node=self.tn,
            market_series=self,
            market_clearing_time=None
        )
        # 240221DJH: The market series, not myTransactiveNode, should keep track of its own acctive markets. We
        #            might have to manage BOTH places until this new architecture is proven and stable.
        self.activeMarkets = [initial_market]

        # 240228DJH: Also append this MarketSeries to this transactive node's list of markets if the transactive node
        #            has been referenced.
        if self.tn and self.tn():
            tn = self.tn()
            tn.markets.append(self)

    @property
    def activationLeadTime(self):
        return self._activationLeadTime

    @property
    def deliveryLeadTime(self):
        return self._deliveryLeadTime

    @property
    def intervalDuration(self):
        return self._intervalDuration

    @property
    def intervalsToClear(self):
        return self._intervalsToClear

    @property
    def marketClearingInterval(self):
        return self._marketClearingInterval

    @property
    def marketLeadTime(self):
        return self._marketLeadTime

    @property
    def initialMarketState(self):
        return self._initialMarketState
