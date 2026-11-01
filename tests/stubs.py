"""Stand-ins shared by tests that build a Market without a MarketSeries."""
from datetime import datetime, timedelta, timezone

from uuid6 import uuid7

from tent.containers.market_time import MarketDurations
from tent.enumerations.market_state import MarketState
from tent.enumerations.method import Method
from tent.market import Market


class SimpleMarketSeries:
    """The attributes Market reads from its series, with no spawning behaviour. Durations are in seconds."""

    def __init__(self, name='TestMarket', intervals_to_clear=1, interval_duration=timedelta(hours=1),
                 default_price=0.05, delivery_lead=0, method=Method.Interpolation,
                 initial_market_state=MarketState.Inactive):
        self.name = name
        self.id = uuid7()
        self.description = name
        self.default_price = default_price
        self.market_order = 1
        self.method = method
        self.interval_duration = interval_duration
        self.duration = MarketDurations.new(
            active=0, negotiation=0, market_lead=0, delivery_lead=delivery_lead, reconciliation=0,
            market_interval=interval_duration, intervals_to_clear=intervals_to_clear,
            market_clearing_interval=interval_duration, delivery_anchor_time=None)
        self.intervals_to_clear = intervals_to_clear
        self.initial_market_state = initial_market_state


class ConstantPriceModel:
    """A price model that forecasts the same price for every time, with no uncertainty."""

    def __init__(self, price=0.05):
        self.price = price

    def get(self, _timestamp):
        return self.price, None


def make_market(transactive_node, clearing_time: datetime | None = None, price_model=None, **series_kwargs) -> Market:
    """A Market on a SimpleMarketSeries, cleared at `clearing_time` (now by default), registered on the node."""
    series = SimpleMarketSeries(**series_kwargs)
    clearing_time = clearing_time if clearing_time is not None else datetime.now(timezone.utc)
    market = Market(transactive_node, series, series.initial_market_state, series.duration.get_timings(clearing_time),
                    price_model=price_model)
    market._stub_series = series  # Market keeps only a weak proxy to its series; hold it for the market's lifetime
    transactive_node.markets = [market]
    return market
