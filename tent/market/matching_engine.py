import logging

from tent.config import ConfigModel
from tent.enumerations import MarketTypes
from tent.market import Market
from tent.market.order_book import INF, MatchingStrategy, OrderBook, OrderBookConfig, Quote, Role  # noqa: F401 (re-exported)
from tent.utils.timer import Timer

_log = logging.getLogger(__name__)


class MatchingEngine(OrderBook, Market):
    """
    A MatchingEngine is a Market child class that facilitates a price discovery process much like that used for stock
    trading. Typically, a market or limit order is matched from among standing orders in an "order book." New orders
    can be formulated, sent, and received asynchronously at any time within the Negotiation market state.

    Construction, timing, and the market lifecycle are inherited from Market and driven by the owning MarketSeries,
    exactly as for Auction: configure a MarketSeries with ``market_class`` set to this class. Extra keywords on the
    series configuration are forwarded to every spawned market, so ``matching_strategy`` is configured there, e.g.
    ``"matching_strategy": ["ORDER_VOLUME", "ORDER_AGE"]`` (names, values, or MatchingStrategy members).

    Orders enter the book in two ways. Any caller may submit_order() a ready-made Order. In addition, on every pass of
    while_in_negotiation() the engine quotes the local system itself: each local asset's and neighbor's active_vertices
    curve for each interval is turned into orders (Order.from_curve), so assets' scheduled flexibility and neighbors'
    received transactive signals are offered into the book without those classes knowing about orders. A quote is
    revised only when its curve changes, detected by the curve's archived version (SDCurve.last_record_time) or, for
    curves that are never archived, by their vertex values.

    How the book clears is the ``clearing`` setting (OrderBookConfig). In batch mode (the default) the window collects:
    every pass quotes and revises, nothing is matched, and when Negotiation closes each interval's book is cleared once
    at the crossing of its summed supply and demand, every leg at that price (a call auction; an order submitted at any
    time in the window counts the same). In continuous mode every pass matches each new or revised order on arrival
    against the orders then resting, at the resting orders' prices, so the arrival order decides which side rests: the
    local assets are quoted first and the neighbors after them, each in configuration order.

    Attributes:
        orderList: (list) of standing Order objects, aka the "order book".
        matchList: (list) of Order objects that have been matched (transacted).
        clearing: (str) 'batch' or 'continuous' (see OrderBookConfig).
        matching_strategy: (list of MatchingStrategy) Tie-break order for same-price orders.
        order_expiry: (timedelta | None) Lifetime of quoted orders; None means they expire at market clearing.
        order_measurement: (Measurement) Archival header under which every Order of this market is recorded.
        quotes: (dict) (entity id, interval id) -> Quote for the orders the engine has placed on entities' behalf.

    Notes:
    - A MatchingEngine market does not formally have a market clearing or market clearing time. The clearing time
      inherited from Market marks the end of the Negotiation period within its market state engine.
    """
    MARKET_TYPE = MarketTypes.matching_engine

    class Config(Market.Config, OrderBookConfig):
        """A market's settings and the book's."""

    def __init__(self, *args, config: ConfigModel | None = None, **kwargs):
        runtime, settings = self.split_keywords(kwargs)
        cfg = self._configure(config, settings)
        # Recorded by get_record(), so it must exist before Market.__init__ archives the market row.
        self.matching_strategy: list[MatchingStrategy] = list(cfg.matching_strategy)
        super(MatchingEngine, self).__init__(*args, config=cfg, **runtime)
        self._init_book(cfg)

    @property
    def table_name(self):
        return 'matching_engine'

    def get_record(self) -> dict:
        record = super(MatchingEngine, self).get_record()
        record.update(self.book_record())
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(MatchingEngine, cls).get_archive_definitions()
        definitions.extend(cls.book_table('matching_engine', 'market'))
        return definitions

    def transition_from_active_to_negotiation(self):
        """Price discovery opens: every local asset schedules itself so that its curve can be quoted into the book (as
        an auction's assets do), then the inherited price refresh."""
        for local_asset in self.tn.local_assets:
            local_asset.schedule(self)
        super(MatchingEngine, self).transition_from_active_to_negotiation()

    def transition_from_negotiation_to_market_lead(self):
        """The book closes. In batch mode the window's orders are cleared now, once: the curves are quoted a last time
        and every interval is cleared at its crossing (quote_and_match). Then settle the matched legs into prices and
        schedules, and the inherited price refresh for the intervals settlement did not price."""
        if self.clearing == 'batch':
            self.quote_and_match()
        self.settle()
        super(MatchingEngine, self).transition_from_negotiation_to_market_lead()

    # ------------------------------------------------------------------------------------------------ settlement
    def while_in_negotiation(self):
        """
        In this market state, actors may submit from zero to many offer or bid orders to secure a position in this
        market object's delivery period. In continuous mode each pass matches any new (or newly revised) orders against
        the order book; in batch mode each pass only quotes and revises, and the book is cleared when the state closes
        (transition_from_negotiation_to_market_lead). Negotiation stays open for its whole window so that orders
        submitted later are still matched; the state is marked complete at the market-lead time.
        """
        if self.clearing == 'batch':
            self.quote_book()
        else:
            self.quote_and_match()
        if Timer.now() >= self.transition_times.market_lead:
            self._stateIsCompleted = True
        return None
