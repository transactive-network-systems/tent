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
remainders stand, and orders expire at their ``expire_by``. Matching walks a ladder of the prices in play and a fill
made at a finite rung is priced at that rung. The infinite rung is where market orders rest (they take any price): a
fill made there is priced by the side that has a curve, the aggressive order's price for the matched quantity (a
40 kW inelastic load against an offer rising from 0.04 at 0 kW to 0.08 at 100 kW trades at 0.056). Two market orders
meeting have no price-forming participant; their fill keeps the infinite level and the orders are named in a warning
once per interval. Every booked and superseded order is archived. Negotiation
stays open for its whole window, so an order submitted late is still matched.

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
(an aggressive order pays the resting orders' prices; a sloped resting curve is filled at the ladder's rungs), so the
market's marginal price, the last fill's, is in general not a price its residual curve passes through at the matched
quantity: on the deployed hierarchy the city's last fill of the campus's bid is at 0.04025 $/kWh, a rung of the campus's
own bid, while the campus clears onto the matched 6489 kW (6426 kW after the link's losses) at 0.03292, the city's
residual curve's price there. The quantities agree on both sides of every link, to the link's losses; the two prices
are different things, the book's discriminatory fill price and the uniform price of the curve, and both are archived.
Intervals with no match keep their opening price and are logged once, as settlement does. The book is the ``OrderBook`` mixin that ``MatchingEngine`` uses; the three market classes share one
implementation.

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
  them together, and a residual difference is the downstream book's own matching of its local assets against the
  upstream curve at its rungs.

The ``matching_auction`` table extends ``auction`` with ``matching_strategy``; the orders are rows of the ``order``
table as for the single-node engine.

Archive
-------

The ``matching_engine`` table extends ``market`` with ``matching_strategy``. Orders are rows of the ``order`` table
(extending ``sd_curve``: actor, buyer, seller, parent, status, side, time stamp, expiry) under the market's ``Order``
measurement; every version of an order is a row. The settled marginal prices are the market's ``MarginalPrice``
measurement values, the volume-weighted averages its ``BlendedPrice`` values.
