"""
MatchingAuction: the Auction's signal exchange between nodes with an order book as the clearing step.

Two deployments use it, from the same code (planning: designs A and B of the multi-node matching engine):

- One engine upstream ("exchange"): the top node (the city) runs a MatchingAuction and every node below it an Auction.
  Bids rise as residual curves exactly as in the auction deployment; at the top the book clears them and the cleared
  price and each neighbor's matched quantity flow back down as the Auction's DeliveryLead signal.
- An engine on every node ("local books"): every node runs a MatchingAuction. Each books its local assets and the
  curves its neighbors last stated, clears locally when its upstream price arrives, and passes its residual up and its
  cleared price down. A neighbor's matched quantity in one node's book need not equal the counterpart's in the other
  node's book until the downstream node has cleared against the upstream price: the downstream signal carries the
  matched point as record 0, so the downstream clearing lands on it.

The flow is Auction.while_in_market_lead and Auction.while_in_delivery_lead unchanged; only Auction.clear differs: the
book quotes every local asset's and every neighbor's current curve, matches with the configured strategy, and settles
(OrderBook.settle), so the marginal prices, the cleared powers and the volume-weighted average price come from the
fills. Until the book has settled, a bid's record 0 is the residual curve at the predicted price, as an auction's; after
it, the downstream signals' record 0 is each neighbor's matched quantity at the cleared price.
"""
import logging

from tent.config import ConfigModel
from tent.enumerations import MarketTypes
from tent.market.auction import Auction
from tent.market.order_book import MatchingStrategy, OrderBook, OrderBookConfig

_log = logging.getLogger(__name__)


class MatchingAuction(OrderBook, Auction):
    """An Auction whose clearing step is an order book (see the module docstring)."""
    MARKET_TYPE = MarketTypes.matching_auction

    class Config(Auction.Config, OrderBookConfig):
        """An auction's settings and the book's."""

    def __init__(self, *args, config: ConfigModel | None = None, **kwargs):
        runtime, settings = self.split_keywords(kwargs)
        cfg = self._configure(config, settings)
        # Recorded by get_record(), so it must exist before Market.__init__ archives the market row.
        self.matching_strategy: list[MatchingStrategy] = list(cfg.matching_strategy)
        self._settled: bool = False
        super(MatchingAuction, self).__init__(*args, config=cfg, **runtime)
        self._init_book(cfg)

    @property
    def BALANCE_POINT(self) -> str:  # noqa: N802 (a class attribute on Market, read through the instance)
        """Record 0 of a signal: before the book has settled, the residual curve at the predicted price (the bid sent
        upstream, as an auction's); after it, the neighbor's matched quantity at the residual curve's own price for it
        (the signals sent downstream), which is where the auction's downstream signals land after balance(). The book's
        fill prices are those of the orders taken (an aggressive order pays the resting orders' prices), so the market's
        marginal price, the last fill's, is not in general a price the residual curve passes through at the matched
        quantity; the curve's own price keeps the point on the curve, and the downstream node clears onto it."""
        return 'matched_quantity' if self._settled else 'predicted_price'

    @property
    def table_name(self):
        return 'matching_auction'

    def get_record(self) -> dict:
        record = super(MatchingAuction, self).get_record()
        record.update(self.book_record())
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(MatchingAuction, cls).get_archive_definitions()
        definitions.extend(cls.book_table('matching_auction', 'auction'))
        return definitions

    def _quote_expiry(self):
        """Quoted orders stand until delivery: the book clears once, in DeliveryLead, when the upstream price is in."""
        return self.transition_times.delivery

    def clear(self) -> None:
        """The book as the clearing step: quote every entity's current curve (the upstream neighbor's from its signal,
        the downstream neighbors' from their bids, the local assets'), match, and settle the fills into prices and
        cleared powers. Intervals with no match keep their opening price and are logged once (see settle)."""
        self.quote_and_match()
        self.settle()
        self._settled = True
        _log.info(f'MARKET: {self.name} cleared its order book: {len(self.matchList)} matched legs, '
                  f'{len(self.orderList)} standing orders.')
