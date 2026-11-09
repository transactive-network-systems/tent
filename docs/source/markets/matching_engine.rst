.. _markets_matching_engine:

====================================
Matching Engine: Order Book Clearing
====================================

A ``MatchingEngine`` is a ``Market`` that clears a single node's system through an order book rather than by
interpolating the summed curve. It is configured like any market: a ``MarketSeries`` whose ``market_class_name`` is
``MatchingEngine``; its own settings are ``clearing`` (how the book clears an interval: ``batch``, the default, or
``continuous``; below), ``matching_strategy`` (the tie-break order among same-price orders: ``NONE``, ``ORDER_AGE``,
``ORDER_VOLUME``, ``PROPORTIONAL_VOLUME``), ``order_expiry`` (the lifetime in seconds of the orders the engine quotes
for its entities; null means they stand until the market clears) and ``order_archiving``, which only matters to a
market that rebuilds its book per balancing pass (see :ref:`negotiated books <markets_negotiated_books>`). The same book
is the clearing step of ``MatchingAuction`` and ``MatchingConsensusMarket`` (below), which carry the same settings.

Negotiation: the book
---------------------

Price discovery opens when the market enters Negotiation. Every local asset schedules itself, as an auction's assets do,
so that its curve exists. On every pass the engine quotes the local system into the book: each local asset's and each
neighbor's ``active_vertices`` curve for each interval becomes orders (``Order.from_curve``, one supply and one demand
order per entity and interval), revised only when the curve changes. Any caller may also ``submit_order`` a ready-made
``Order``. Orders expire at their ``expire_by``. Negotiation stays open for its whole window, so an order submitted late
is still in the book when it clears. How the book clears is the ``clearing`` setting.

**Batch clearing** (``clearing: batch``, the default) collects. Every pass quotes and revises, and nothing is matched
until Negotiation closes; then each interval's orders are cleared together, once: the supply and the demand are summed,
the price where they cross is found, and the fills are allocated there, every leg at that one price (a call auction).
The result does not depend on the order in which the entities were quoted or the orders arrived. The crossing is found
on the ladder of every breakpoint price the orders name: between two rungs every order's quantity is a straight line in
the price, so the net quantity is linear there; at a rung supply may jump (a limit order, an all-or-nothing vertex) and
just above it demand may drop off. Where the sides cross strictly inside a segment, every order fills its own quantity
at the interpolated price, which is what interpolation on the summed curve gives (a 100 kW inelastic load against an
offer rising from 0.04 at 0 kW to 0.08 at 1000 kW trades at 0.044, every leg at 0.044). Where they cross at a rung, the
short side fills in full; of the long side, what each order already offered beyond the rung is below the margin and
fills in full, and what it adds at the rung competes for the rest under the configured ``matching_strategy``: oldest
first, largest first, pro rata, or no ranking, an all-or-nothing addition taken whole or not at all. This is what the
ladder walk of continuous clearing does at a rung, applied once to the summed book rather than per aggressive order.
Market orders (inelastic quantities, which take any price) simply enter the sums, so a node's own inelastic supply and
load net against each other inside the batch and the internal match is priced at the crossing like every other leg
(the campus's solar and its buildings no longer meet as two market orders with no price between them). An inelastic
surplus or deficit the curves cannot absorb is a crossing no price makes: the side that can trade fills in full, the
market orders ration it, the rest of them stands as a market order, and the interval is priced at the end of the
ladder nearest the crossing, as the base market prices an interval whose curve never crosses zero at the curve end
nearest zero.

**Continuous clearing** (``clearing: continuous``) matches on arrival. Each pass matches every new or revised order
against the orders then standing, price first and then by the configured strategy; matched quantities become matched
legs (one per counterparty and side, at the match price), unmatched remainders stand. The configuration order of the
local assets and the neighbors is the arrival order: the local assets are quoted first and the neighbors after them,
each in configuration order, so whichever side is quoted first rests, and the resting orders' prices are the fill
prices (pay-as-rest). Matching walks a ladder of every breakpoint price in play. At a rung where the aggressive order
wants at least what the standing side offers, every standing order is taken at that rung. At a rung where the standing
side offers more, and both sides are continuous between the previous rung and this one, the fill is made inside the
segment at the balance price, where the standing side's quantity meets the aggressive order's (the linear
standing-order case): a sloped standing curve is filled at its own price for the quantity taken, as its owner stated it
(a 100 kW inelastic load taking from a resting offer that rises from 0.04 at 0 kW to 0.08 at 1000 kW trades at 0.044,
not at the 0.08 rung). Where a side is all-or-nothing between the rungs, the fill is made at the rung and the configured
strategy decides which standing orders are taken. The infinite rung is where market orders rest (they take any price):
a fill made there is priced by the side that has a curve, the aggressive order's price for the matched quantity. Two
market orders meeting have no price-forming participant; their fill keeps the infinite level, and the pass reports its
unpriced fills in one line (each interval and pair of actors once per market). ``order_expiry``, age priority and the
arrival order keep their meaning in this mode. Every booked and superseded order is archived in either mode.

Settlement: the book closes
---------------------------

At the market-lead time ``MatchingEngine.settle`` turns the matched legs into the market's result, per interval:

- **Scheduled powers.** Each entity's scheduled power is the signed sum of its matched legs (supply positive, demand
  negative). Unmatched remainders did not trade. The powers go through the market's cleared-power store, so
  ``Entity.market_power`` returns them and the entities schedule from them exactly as they do after an auction clears:
  the assets' schedules are set from the store where the interval settled, and the neighbors' ``schedule_power`` reads
  the same store.
- **Settlement is the fills.** Every leg is archived with its own price and quantity; what anyone pays or is paid is
  the sum over its legs.
- **The marginal price** of an interval is where the supply and demand the window saw cross, in both clearing modes:
  every order of the interval in its original quantities and prices, whether it was filled, partly filled, left
  standing, expired or cancelled (an order a revised quote replaced counts once, as its revision). In batch mode that is
  the clearing price, the price every leg was made at. In continuous mode the legs were made at the resting orders'
  prices and the crossing is what the same orders would have cleared at together; the last fill is not the marginal
  price (it is available as ``last_fill_price`` for reporting and is not archived). The volume-weighted average price
  of the interval's legs is recorded beside the marginal price, in the market's ``BlendedPrice`` measurement
  (``average_match_prices``); it is what the trades cost on average, not a marginal price, and in batch mode the two
  coincide. A settled price is the market's own and is not replaced by the prior market's price.
- **Intervals with no match** keep their opening price and are left to their entities' curves at that price; the market
  logs one line naming them. An interval whose orders name no price at all (market orders met by market orders, which
  can happen in a batch too when nothing on the node has a curve) has no discovered price either: its matched
  quantities are scheduled at the opening price, and it is logged likewise.

After settlement the market proceeds as any other: the price model is updated from the marginal prices and the assets
are told to actuate on entering Delivery (unless the series has ``actuate_assets`` false).

Actuation: the asset's own price
--------------------------------

An asset whose actuation manager turns a price into set points (``DirectRatioActuationManager`` on a
``ModelFrameAsset``: the price's position in the span of the curve the asset offered) actuates on the asset's
``actuation_price`` for the delivery interval: its own curve's price at the power the market cleared it to. For a market
that clears at one price the asset was scheduled from its curve at that price, so this is the market's marginal price
again and an auction's actuation is unchanged. For a book whose fills are at the resting orders' prices the asset's
cleared power is the sum of its legs, and its curve's price for that power is what the asset itself asked for what it
traded, where the market's marginal price may be another participant's. Where the curve has no unique price at the
cleared power (no curve or no cleared power for the interval, a single inelastic vertex, a vertical segment, a power off
the curve's ends) the market's marginal price is used. The change is in the actuation layer; the markets report their
prices as above.

Between nodes: MatchingAuction
------------------------------

``MatchingAuction`` is an ``Auction`` whose clearing step is the order book. The signal exchange is the auction's,
unchanged: in MarketLead a node collects its downstream neighbors' bids (their residual curves), schedules them,
aggregates its own residual and sends it upstream as its bid (record 0 at the predicted price, as an auction's bid); in
DeliveryLead, once the upstream signal is in, it clears and sends each downstream neighbor the result. Only the
clearing differs: instead of interpolating on the summed curve, ``MatchingAuction.clear`` quotes every local asset's
and every neighbor's current curve into the book (the upstream neighbor's from its signal, the downstream neighbors'
from their bids), matches, and settles, so the marginal prices, the cleared powers and the volume-weighted average price
come from the fills. With batch clearing (the default) every curve is quoted and each interval is cleared once at the
crossing of the summed supply and demand, every leg at that price, so the result does not depend on the order the
entities are quoted in and the market's marginal price is a price its residual curve passes through at the matched
quantity. The downstream signals then carry each neighbor's matched quantity as record 0, at the residual curve's own
price for that quantity, so the point lies on the curve the neighbor receives and the downstream node's clearing lands
on the matched quantity. On the deployed hierarchy the city's book clears at 0.03228 $/kWh in the first interval and
sends the campus 6489 kW; the campus clears onto the 6426 kW that arrive after the link's losses at 0.03292, the city's
residual curve's price there, which is the city's crossing seen through the link: the campus's price is the city's
divided by the link's marginal loss derivative, so both ends of the link state the same crossing for the same curves,
to the link's losses. With continuous clearing the book's prices are those of the orders taken (an aggressive order
pays the resting orders' prices), so the fills are not in general at a price the residual curve passes through: the
city's supply took the whole of the campus's resting bid rung by rung and its last fill was at 0.04025, a rung of the
campus's own bid, while the campus cleared onto the matched quantity at 0.03292; the reported marginal price is the
crossing in that mode too. Intervals with no match keep their opening price and are logged once, as settlement does.
The book is the ``OrderBook`` mixin that ``MatchingEngine`` uses; the three market classes share one implementation.

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
  them together. On the deployed hierarchy the campus's book clears at 0.03292 and the city's at 0.03228 in the first
  interval, the same prices and the same exchange (6489 kW sent, 6426 kW arriving; 27.05 kW sent to each office) as the
  one-engine deployment, and as the negotiated books below.

The ``matching_auction`` table extends ``auction`` with ``matching_strategy``; the orders are rows of the ``order``
table as for the single-node engine.

.. _markets_negotiated_books:

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
book rather than revising one whose matches were made against curves a neighbor has since replaced; which of those
books the archive holds is the ``order_archiving`` setting (below). The exchange proposed to a neighbor is the quantity
matched against its curve, marked on the residual curve at that curve's own price for it (record 0 at the matched
quantity, as the ``MatchingAuction``'s downstream signals), so the point lies on the curve the neighbor receives; the
neighbor books the curve, matches it against its own book and replies with its own matched quantity. With batch
clearing a node's inelastic assets net against each other inside the sums, so the quantity proposed is the net the node
needs from the neighbor: a campus with 30 kW of inelastic solar and 100 kW of inelastic load offers the city 70 kW, and
every leg, the solar's and the buildings' included, is at the crossing price. The local problem counts as balanced when
the book has cleared every interval: a standing market order left over is an inelastic quantity no price would have
cleared, recorded as the base market records a curve that never crosses zero and kept out of consensus, and the
scheduled powers sum to zero. A book has no duality gap to close (its result is the fills, not a price iterated to a
gap), so the pass is judged on the power balance, and the duality gap recorded is the largest imbalance.

The two ends of a link agree on the exchanged quantity; the price is not part of the agreement
(``BALANCE_POINT_AGREEMENT`` is ``'quantity'``, where a market that clears at one price asks for price and quantity).
The rule dates from continuous clearing, where a book fills at the resting orders' prices and the two books on a link
price the same exchange differently (the campus's book prices what it buys from the city at the city's offer, the
city's book what it sells to the campus at the campus's bid). With batch clearing both books clear at their crossing,
and the two crossings are the same price seen through the link's losses: on the deployed hierarchy the city's book
clears at 0.03228 $/kWh and the campus's at 0.03292 for the 6489 kW the city sends (6426 kW arriving), the prices of the
``MatchingAuction`` deployments. The price may therefore rejoin the agreement; that is an owner's decision after this
build, and the rule stays ``'quantity'`` for now. Two nodes whose curves are a 100 kW inelastic load and a generator
rising from 0.04 to 0.08 over 1000 kW agree on 100 kW at 0.044 on both sides within two exchanges of signals, the
consensus market's result; the deployed four-node hierarchy reaches full consensus on its real-time market in a few
ticks with both ends of every link agreeing on the exchange to the link's losses.

The ``matching_consensus`` table extends ``market`` with ``matching_strategy``; the orders are rows of the ``order``
table; the consensus flags are the market's ``FullConsensus`` measurement as for the consensus market. Which books reach
the ``order`` table is the ``order_archiving`` setting: by default (``final_book``) only the book standing when the
market leaves MarketLead, the one it clears on (a late signal absorbed in MarketLead rebuilds it once more), so a market
archives one book however many passes its negotiation took; the books of the passes a later signal superseded are not
written, as the signals themselves are the record of the negotiation. With ``every_pass`` the book standing at the end
of every balancing pass is archived, for debugging a negotiation (on the deployed campus about ten passes per market,
96 000 orders and 255 000 vertices in 25 minutes). ``MatchingEngine`` and ``MatchingAuction`` clear one book per market
and archive orders as they are booked, so the setting changes nothing for them. The deployment generator's
``negotiated`` variant runs it on every node with the consensus variant's negotiation windows.

Archive
-------

The ``matching_engine`` table extends ``market`` with ``matching_strategy`` (``matching_auction`` extends ``auction``
and ``matching_consensus`` extends ``market`` the same way). Orders are rows of the ``order`` table
(extending ``sd_curve``: actor, buyer, seller, parent, status, side, time stamp, expiry) under the market's ``Order``
measurement; every version of an order is a row, a batch's legs among them (one per counterparty, all at the crossing
price). The settled marginal prices (the crossings) are the market's ``MarginalPrice`` measurement values, the
volume-weighted averages its ``BlendedPrice`` values. The ``clearing`` setting, like the other settings, is part of the
node's configuration and not of the archive schema.
