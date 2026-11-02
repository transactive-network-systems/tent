.. _markets_matching_engine:

====================================
Matching Engine: Order Book Clearing
====================================

A ``MatchingEngine`` is a ``Market`` that clears a single node's system through an order book rather than by
interpolating the summed curve. It is configured like any market: a ``MarketSeries`` whose ``market_class_name`` is
``MatchingEngine``; its own settings are ``matching_strategy`` (the tie-break order among same-price orders: ``NONE``,
``ORDER_AGE``, ``ORDER_VOLUME``, ``PROPORTIONAL_VOLUME``) and ``order_expiry`` (the lifetime in seconds of the orders the
engine quotes for its entities; null means they stand until the market clears).

Negotiation: the book
---------------------

Price discovery opens when the market enters Negotiation. Every local asset schedules itself, as an auction's assets do,
so that its curve exists. On every pass the engine quotes the local system into the book: each local asset's and each
neighbor's ``active_vertices`` curve for each interval becomes orders (``Order.from_curve``, one supply and one demand
order per entity and interval), revised only when the curve changes. Any caller may also ``submit_order`` a ready-made
``Order``. Each new or revised order is matched against the standing book, price first and then by the configured
strategy; matched quantities become matched legs (one per counterparty and side, at the match price), unmatched
remainders stand, and orders expire at their ``expire_by``. Matching walks a ladder of every breakpoint price in play.
At a rung where the aggressive order wants at least what the standing side offers, every standing order is taken at
that rung. At a rung where the standing side offers more, and both sides are continuous between the previous rung and
this one, the fill is made inside the segment at the balance price, where the standing side's quantity meets the
aggressive order's (the linear standing-order case): a sloped standing curve is filled at its own price for the
quantity taken, as its owner stated it, and the cleared quantities lie on the curves at the match price, as they do
after interpolation (a 100 kW inelastic load taking from a resting offer that rises from 0.04 at 0 kW to 0.08 at
1000 kW trades at 0.044, not at the 0.08 rung). Where a side is all-or-nothing between the rungs, the fill is made at
the rung and the configured strategy decides which standing orders are taken. The infinite rung is where market orders
rest (they take any price): a fill made there is priced by the side that has a curve, the aggressive order's price for
the matched quantity (a 40 kW inelastic load against an offer rising from 0.04 at 0 kW to 0.08 at 100 kW trades at
0.056). Two market orders meeting have no price-forming participant; their fill keeps the infinite level, and the pass
reports its unpriced fills in one line (each interval and pair of actors once per market). Every booked and superseded
order is archived. Negotiation stays open for its whole window, so an order submitted late is still matched.

Settlement: the book closes
---------------------------

At the market-lead time ``MatchingEngine.settle`` turns the matched legs into the market's result, per interval:

- **Scheduled powers.** Each entity's scheduled power is the signed sum of its matched legs (supply positive, demand
  negative). Unmatched remainders did not trade. The powers go through the market's cleared-power store, so
  ``Entity.market_power`` returns them and the entities schedule from them exactly as they do after an auction clears:
  the assets' schedules are set from the store where the interval settled, and the neighbors' ``schedule_power`` reads
  the same store.
- **Prices.** The interval's marginal price is the price of the last match, the marginal trade. The volume-weighted
  average price of the interval's matches is recorded beside it, in the market's ``BlendedPrice`` measurement
  (``MatchingEngine.average_match_prices``), for reporting. A settled price is the market's own and is not replaced by
  the prior market's price.
- **Intervals with no match** keep their opening price and are left to their entities' curves at that price; the market
  logs one line naming them. An interval whose only matches are market orders against market orders has no discovered
  price either: its matched quantities are scheduled at the opening price, and it is logged likewise.

After settlement the market proceeds as any other: the price model is updated from the marginal prices and the assets
are told to actuate on entering Delivery (unless the series has ``actuate_assets`` false).

Between nodes: MatchingAuction
------------------------------

``MatchingAuction`` is an ``Auction`` whose clearing step is the order book. The signal exchange is the auction's,
unchanged: in MarketLead a node collects its downstream neighbors' bids (their residual curves), schedules them,
aggregates its own residual and sends it upstream as its bid (record 0 at the predicted price, as an auction's bid); in
DeliveryLead, once the upstream signal is in, it clears and sends each downstream neighbor the result. Only the
clearing differs: instead of interpolating on the summed curve, ``MatchingAuction.clear`` quotes every local asset's
and every neighbor's current curve into the book (the upstream neighbor's from its signal, the downstream neighbors'
from their bids), matches with the configured strategy, and settles, so the marginal prices, the cleared powers and the
volume-weighted average price come from the fills. The downstream signals then carry each neighbor's matched quantity
as record 0, at the residual curve's own price for that quantity, so the point lies on the curve the neighbor receives
and the downstream node's clearing lands on the matched quantity. The book's own prices are those of the orders taken
(an aggressive order pays the resting orders' prices, at the rung when it takes everything the rung offers and inside
the segment at the balance price otherwise), so the market's marginal price, the last fill's, is in general not a price
its residual curve passes through at the matched quantity: on the deployed hierarchy the city's supply takes the whole
of the campus's resting bid rung by rung, so its last fill is at 0.04025 $/kWh, a rung of the campus's own bid, while
the campus clears onto the matched 6489 kW (6426 kW after the link's losses) at 0.03292, the city's residual curve's
price there. The quantities agree on both sides of every link, to the link's losses; the two prices are different
things, the book's discriminatory fill price and the uniform price of the curve, and both are archived. Intervals with
no match keep their opening price and are logged once, as settlement does. The book is the ``OrderBook`` mixin that
``MatchingEngine`` uses; the three market classes share one implementation.

Two deployments use the class, from the same code and differing only in configuration:

- **One engine upstream** (the "exchange"): the top node runs a ``MatchingAuction`` and every node below it an
  ``Auction``. One book clears the whole system; the top node's archive holds every order, match, remainder and expiry.
  A downstream node clears against the top's residual curve with the matched point on it and lands on the matched
  quantity at the cleared price.
- **An engine on every node** (the "local books"): every node runs a ``MatchingAuction``, books its local assets and the
  curves its neighbors last stated, clears when its upstream price arrives, and passes its residual up and its cleared
  price down. The exchange with a neighbor is the match quantity against that neighbor's curve. The two books on either
  side of a link clear independently against stated curves, so their match quantities need not agree until the
  downstream node has cleared against the upstream price; the matched point in the downstream signal is what brings
  them together. The campus's book fills its offices' curves inside the city remainder's segment at that curve's own
  price for the quantity, 0.03292 on the deployed hierarchy, the price the campus clears onto in the one-engine
  deployment: both deployments give the campus the same price, and differ in the city's.

The ``matching_auction`` table extends ``auction`` with ``matching_strategy``; the orders are rows of the ``order``
table as for the single-node engine.

Negotiated books: MatchingConsensusMarket
-----------------------------------------

``MatchingConsensusMarket`` is a ``ConsensusMarket`` whose local balancing step is the order book. The negotiation is
the consensus market's, unchanged (see :ref:`markets_market_lifecycle`): on entering Negotiation, and on every tick that
brings a neighbor's new signal or finds the local problem unbalanced, the node balances locally, offers every
transactive neighbor the residual flexibility of the rest of its system with record 0 at the exchange it has scheduled
with that neighbor, and the signals go back and forth until both ends of every link agree within the convergence
threshold. Only the balancing step differs: instead of interpolating on the summed curve, each pass rebuilds the node's
book from every local asset's and every neighbor's current curve (the neighbors' from their last received signals),
matches and settles, so the marginal prices and every entity's cleared power come from the fills. A pass clears a fresh
book rather than revising one whose matches were made against curves a neighbor has since replaced; the book standing
at the end of the pass is what the archive holds. The exchange proposed to a neighbor is the quantity matched against
its curve, marked on the residual curve at that curve's own price for it (record 0 at the matched quantity, as the
``MatchingAuction``'s downstream signals), so the point lies on the curve the neighbor receives; the neighbor books the
curve, matches it against its own book and replies with its own matched quantity. The local problem counts as balanced
when the book has cleared every interval: a standing market order left over is an inelastic quantity no price would
have cleared, recorded as the base market records a curve that never crosses zero and kept out of consensus, and the
scheduled powers sum to zero. A book has no duality gap to close (its fill prices are the resting orders' own), so the
pass is judged on the power balance, and the duality gap recorded is the largest imbalance.

The two ends of a link agree on the exchanged quantity; the price is not part of the agreement
(``BALANCE_POINT_AGREEMENT`` is ``'quantity'``, where a market that clears at one price asks for price and quantity).
A book fills at the resting orders' prices, so the two books on a link price the same exchange differently: on the
deployed hierarchy the campus's book prices what it buys from the city at the city's offer (0.03292 $/kWh for 6489 kW)
and the city's book what it sells to the campus at the campus's bid (0.04025), where interpolation finds the one price
both would state. Two nodes whose curves are a 100 kW inelastic load and a generator rising from 0.04 to 0.08 over
1000 kW agree on 100 kW at 0.044 on both sides within two exchanges of signals, the consensus market's result; the
deployed four-node hierarchy reaches full consensus on its real-time market in a few ticks with both ends of every link
agreeing on the exchange to the link's losses.

The ``matching_consensus`` table extends ``market`` with ``matching_strategy``; the orders are rows of the ``order``
table, one book per balancing pass; the consensus flags are the market's ``FullConsensus`` measurement as for the
consensus market. The deployment generator's ``negotiated`` variant runs it on every node with the consensus variant's
negotiation windows.

Archive
-------

The ``matching_engine`` table extends ``market`` with ``matching_strategy`` (``matching_auction`` extends ``auction``
and ``matching_consensus`` extends ``market`` the same way). Orders are rows of the ``order`` table
(extending ``sd_curve``: actor, buyer, seller, parent, status, side, time stamp, expiry) under the market's ``Order``
measurement; every version of an order is a row. The settled marginal prices are the market's ``MarginalPrice``
measurement values, the volume-weighted averages its ``BlendedPrice`` values.
