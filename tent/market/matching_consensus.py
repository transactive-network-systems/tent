"""
MatchingConsensusMarket: the consensus protocol between nodes with an order book as each node's balancing step
(planning: design C of the multi-node matching engine, "negotiated books").

The negotiation is ConsensusMarket's, unchanged: on entering Negotiation and on every tick that brings a neighbor's new
signal or finds the local problem unbalanced, the node balances locally, offers every transactive neighbor the residual
flexibility of the rest of its system with record 0 at the exchange it has scheduled with that neighbor, and the
signals go back and forth until both ends of every link agree at the balance point within the convergence threshold.
Only the local balancing step differs: instead of interpolating on the summed curve, the node rebuilds its order book
from every local asset's and every neighbor's current curve (the neighbors' from their last received signals), matches
with the configured strategy and settles, so its marginal prices and every entity's cleared power come from the fills.
The exchange proposed to a neighbor is the quantity matched against that neighbor's curve, placed on the residual curve
at that curve's own price for it (the BALANCE_POINT rule 'matched_quantity'), so the point lies on the curve the
neighbor receives; the neighbor books the curve, matches it against its own book and replies with its own matched
quantity. Agreement is reached when the two books match the same quantity across the link; the price is not part of
it (BALANCE_POINT_AGREEMENT 'quantity'): a book fills at the resting orders' own prices, so the two books on a link
price the same exchange differently (the campus's book prices what it buys from the city at the city's offer, the
city's book what it sells to the campus at the campus's bid), where interpolation finds the one price both would state.

The local problem counts as balanced when the book has cleared every interval: no standing market order is left over
(an inelastic quantity the book could not fill is a deficit or surplus no price would have cleared, recorded in
`unbalanceable` as the base market records a curve that never crosses zero) and the scheduled powers sum to zero. The
duality gap of the interpolation does not apply to a book (its fill prices are the resting orders' own, not one
uniform price), so convergence is judged on the power balance. Each balancing pass clears a fresh book: orders are
not revised against a book whose matches were made with curves a neighbor has since replaced. What the archive holds is
set by `order_archiving` (OrderBookConfig): by default only the book standing when the market leaves MarketLead, the
one it clears on (a late signal absorbed in MarketLead rebuilds it once more); the books of the passes a later signal
superseded are not written, as the signals themselves are the record of the negotiation. With 'every_pass' the book
standing at the end of every balancing pass is archived, for debugging a negotiation.
"""
import logging

from tent.config import ConfigModel
from tent.enumerations import MarketTypes
from tent.market.consensus_market import ConsensusMarket
from tent.market.order_book import MatchingStrategy, OrderBook, OrderBookConfig

_log = logging.getLogger(__name__)


class MatchingConsensusMarket(OrderBook, ConsensusMarket):
    """A ConsensusMarket whose local balancing step is an order book (see the module docstring)."""
    MARKET_TYPE = MarketTypes.matching_consensus
    # Record 0 of a signal: the quantity matched with the neighbor at the residual curve's own price for it (see
    # Market.BALANCE_POINT). The book has run before any signal is prepared, so the rule holds from the opening signal.
    BALANCE_POINT = 'matched_quantity'
    # The two ends of a link agree on the exchanged quantity; each book's prices are its own (see Market).
    BALANCE_POINT_AGREEMENT = 'quantity'

    class Config(ConsensusMarket.Config, OrderBookConfig):
        """A consensus market's settings and the book's."""

    def __init__(self, *args, config: ConfigModel | None = None, **kwargs):
        runtime, settings = self.split_keywords(kwargs)
        cfg = self._configure(config, settings)
        # Recorded by get_record(), so it must exist before Market.__init__ archives the market row.
        self.matching_strategy: list[MatchingStrategy] = list(cfg.matching_strategy)
        super(MatchingConsensusMarket, self).__init__(*args, config=cfg, **runtime)
        self._init_book(cfg)
        self.clearings: int = 0  # balancing iterations that cleared the book

    @property
    def table_name(self):
        return 'matching_consensus'

    def get_record(self) -> dict:
        record = super(MatchingConsensusMarket, self).get_record()
        record.update(self.book_record())
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(MatchingConsensusMarket, cls).get_archive_definitions()
        definitions.extend(cls.book_table('matching_consensus', 'market'))
        return definitions

    def _quote_expiry(self):
        """Quoted orders stand for the pass that quoted them; the book is rebuilt on the next (see reset_book)."""
        return self.transition_times.delivery

    # ------------------------------------------------------------------------------------------ the balancing step
    def balance_locally(self) -> bool:
        """One local balancing pass (ConsensusMarket.balance_locally) with the book as its solve step. The orders of the
        pass's iterations are held (each iteration's reset_book drops the previous iteration's) so that only the final
        iteration's book can be archived: at the end of the pass with order_archiving 'every_pass', or, by default
        ('final_book'), when the market leaves MarketLead, by which time the next pass's reset has replaced it with its
        own book, so what is written is the book the market clears on."""
        self.hold_order_archiving()
        try:
            return super(MatchingConsensusMarket, self).balance_locally()
        finally:
            if self.order_archiving == 'every_pass':
                self.flush_held_orders()

    def transition_from_market_lead_to_delivery_lead(self):
        """The market clears on the book now standing (while_in_market_lead may have rebuilt it once on a late signal):
        write it if it is held (order_archiving 'final_book') and resume archiving as orders are booked. The state
        machine's MarketLead timeout calls this same transition, so a market forced on at its deadline writes it too.
        With 'every_pass' nothing is held and this only confirms the archiving state."""
        super(MatchingConsensusMarket, self).transition_from_market_lead_to_delivery_lead()
        self.flush_held_orders()

    def _solve_locally(self) -> bool:
        """Clear a fresh book of every entity's current curve and settle it: prices from the fills, every entity's
        cleared power from its matched legs (OrderBook.settle). An interval with a standing market order left over
        could not be balanced: no price clears an inelastic quantity nobody matched. Always returns True, as settlement
        leaves every interval priced (an interval with no priced match keeps its opening price)."""
        self.reset_book()
        self._cleared_powers.clear()
        self.unbalanceable.clear()
        self.quote_and_match()
        self.settle()
        self.clearings += 1
        for time_interval in self.time_intervals:
            unfilled = sum(order.vertices[-1].quantity for order in self.standing_market_orders(time_interval))
            if abs(unfilled) > 1e-9:
                self.unbalanceable[time_interval.name] = unfilled
                if time_interval.name not in self._no_crossing_reported:
                    self._no_crossing_reported.add(time_interval.name)
                    kind, remedy = (('surplus', 'absorb') if unfilled > 0 else ('deficit', 'supply'))
                    _log.warning(f'MARKET: {self.name} cannot balance {time_interval.name}: a {kind} of '
                                 f'{abs(unfilled):.4g} kW of market orders stands unmatched in the book; the matched '
                                 f'quantities are scheduled. No counterparty can {remedy} it: check the neighbors\' '
                                 f'power limits and curves.')
        return True

    def _reschedule_entities(self) -> None:
        """Settlement has scheduled every entity from its matched legs (the assets from the cleared-power store, the
        neighbors through schedule_power, which reads it); an asset's own scheduling model is not asked again, as it
        would schedule from its curve at the last fill's price rather than from what it traded. Limits still apply."""
        for entity in self._entities():
            entity.clamp_scheduled_powers(self)

    def _gap_closes(self) -> bool:
        """A book has no duality gap to close (see the module docstring): the pass is judged on the net power of the
        intervals that could balance, recorded as the gap for the archive's reader."""
        self.duality_gap = self._largest_imbalance()
        return self.duality_gap <= self.series.duality_gap_threshold
