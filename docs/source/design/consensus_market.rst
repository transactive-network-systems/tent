Consensus markets
=================

A ``ConsensusMarket`` clears by agreement between neighboring nodes rather than by a central auction. This page
describes how the mechanism works under the TENT market state machine, what each node sends and checks, how an agent
wires it to a message bus, and what is known to be unfinished. It records the design that landed on 30 October 2026,
which combined the loop structure of the ``consensus`` branch (2023 to 2024) and the legacy VOLTTRON campus agent with
the current ``Neighbor``, ``TransactiveSignal`` and ``Market`` API.

The two sub-problems
--------------------

Every node solves two problems at once.

**Balancing (local).** Given the flexibility of its local assets and the flexibility each neighbor offered in its last
transactive signal, find the marginal price in every active time interval at which local net power is zero. With the
``Interpolation`` method this is exact for piece-wise linear curves: the system curve is the sum of all entity curves
and the price is read where it crosses zero. ``ConsensusMarket.balance_locally`` runs ``schedule`` (every asset and
neighbor refreshes its active vertices and schedules against the current prices), ``balance`` (sum and solve), a
re-schedule of powers at the new prices, ``update_costs`` and ``update_supply_demand``, and judges convergence by the
duality gap ``(production cost - dual cost) / production cost`` against ``series.duality_gap_threshold``. When nothing in
the system has a cost, the net power itself is the gap. The loop is bounded by the market keyword
``max_balance_iterations`` (default 20); Interpolation needs one pass, Subgradient several.

**Coordination (between nodes).** For each transactive neighbor the node prepares a transactive signal: the residual
system curve with that neighbor excluded, bounded to what the node can exchange with it, stated from the neighbor's
side (positive quantity means this node supplies the neighbor), with losses and demand charges that only apply locally
removed. The curve carries a *balance point*: the vertex at the power this node has scheduled with the neighbor, sign
flipped into the neighbor's perspective. The neighbor folds a received signal into its own balancing problem as that
neighbor's active vertices and replies in kind.

Convergence with a neighbor
---------------------------

``Neighbor.check_for_convergence(market)`` decides two things per interval from the prepared, last sent and last received
signals:

- A signal is **due** when nothing has been sent yet; or a signal received at or after the last send disagrees with it
  at the balance point beyond ``convergence_threshold`` (``TransactiveSignal.balance_points_differ``, which flips the
  sign of the received quantity); or local conditions have changed so that the prepared signal differs from the sent
  one (``TransactiveSignal.conditions_have_changed``: a sent flexibility vertex has no counterpart within the threshold
  among the prepared ones, or the balance point moved). ``signal_due`` is what decides whether to send.
- The interval is **converged** when no signal is due *and* the neighbor has been heard from for that interval. A
  neighbor that never replied is not in consensus, however settled this node's own position is.

**Full consensus** for an interval is the market having balanced and every transactive neighbor being converged there.
It is recorded on every pass as ``MeasurementType.FullConsensus`` and is what the analysis tool's consensus strip plots.

Under the state machine
-----------------------

``Market.events`` drives every market type through the same states; ``ConsensusMarket`` fills in the Negotiation state.

- ``transition_from_active_to_negotiation``: initialise prices, balance locally, send an opening signal to every
  transactive neighbor (with its balance point), record consensus (none yet).
- ``while_in_negotiation`` (every tick): if any neighbor's signal arrived since the last pass, or the local problem is
  not balanced, balance locally. Then for each transactive neighbor prepare the signal, check convergence, and send
  only when a signal is due. When every neighbor is converged the state is complete.
- ``on_signal_received(neighbor)``: a reply that arrives after the market declared consensus reopens the state, so the
  next tick evaluates the new information. Without this hook the state machine would ignore anything that arrived
  after the first agreement, because it stops calling a completed state.
- ``on_local_change(entity)``: a local asset or neighbor model whose conditions changed (forecast, meter, constraint)
  calls this; the market rebalances and tells the neighbors if that changes anything. The legacy agents re-ran the loop
  on a fixed reschedule interval instead; here the entity that changed says so.
- ``transition_from_negotiation_to_market_lead``: record the final consensus state. The state machine's timeout moves
  the market on at the market lead time whether or not consensus was reached, with a warning.

Because a sent signal is compared with later prepared ones, ``Neighbor.send_transactive_signal`` keeps a snapshot
(``TransactiveSignal.snapshot``) rather than the prepared object, which is revised in place on every pass.

Wiring an agent
---------------

Nothing in the library knows about a message bus. A ``TransactiveNode`` subclass implements ``issue_signal(recipient,
message)`` to deliver a serialised signal to the neighbor named ``recipient`` and ``register_subscription`` so that
``Neighbor.receive_transactive_signal`` is called with the serialised signals addressed to this node. Two naming
conventions make routing work: a neighbor's ``name`` is the remote node's ``name``, and the remote node resolves the
market by name, so every node that negotiates one consensus market must configure the series with the same ``name`` and
timing so the spawned markets share names (``<series name>_<clearing time>``). Both nodes read the same ``Timer``,
which in a simulation is the shared simulation clock. ``tests/mock_tn.py`` ``MockTN.connect_peer`` wires two in-process
nodes this way and ``tests/integration_tests/test_consensus_two_nodes.py`` drives them: a two-node exchange that
reaches full consensus at the expected price and powers, a local change that reopens a settled negotiation, a peer that
never replies, a three-node star that clears at one price, and a loss model under which the two sides price the exchange
differently but agree on it.

Defects repaired on the way
---------------------------

These were all latent in the pieces that the consensus loop depends on and would have stopped any consensus run.

- ``TransactiveSignal.conditions_have_changed`` reported a change whenever *any* pair of sent and prepared flexibility
  vertices differed, which is always true for a curve with two or more vertices; a node with flexibility resent every
  tick. It now reports a change only when a sent vertex has no counterpart (or the balance point moved).
- The balance point was added to a signal only when it was sent, so the prepared signal always had one vertex fewer
  than the sent one. ``Neighbor.prep_transactive_signal`` now marks it (``TransactiveSignal.mark_balance_points``).
- The balance vertex was built with the scheduled-power object instead of its value, in the wrong perspective (no sign
  flip), and without a reference to its curve.
- ``TransactiveSignal.parse_signal`` compared the JSON string key of each vertex with the integer balance-point index, so
  no received signal ever had a balance point.
- ``SDCurve.check_rationality`` added sentinel vertices at infinite prices; a lone inelastic vertex (which sits at an
  infinite price) collided with the sentinel and the received curve came back empty.
- ``Neighbor.check_for_convergence`` indexed the sent and received signal dictionaries directly and raised before the
  first exchange.
- ``Market.while_in_negotiation`` and ``Auction`` passed the node where ``balance`` expects an iteration counter.
- The relative-distance criterion divided by the average price even when both prices were infinite or zero.

What is still open
------------------

- **Subgradient** has not been exercised; the loop supports it but every test uses Interpolation.
- **Convergence per market.** ``Neighbor.converged`` and ``signal_due`` are single flags per neighbor, computed for the
  market last checked. A node with two consensus series negotiating at once would need them keyed by market.
- **Periodic reconsideration.** The legacy agents re-ran balancing on a timer. Here a local change must be reported
  through ``on_local_change``; nothing re-runs on its own while a market has declared consensus.
- **Clock skew.** "Received at or after sent" compares the sender's ``signal_time`` with the local send time. In a
  simulation the clocks are one; across real agents they are not, and a skew larger than a negotiation round could hide
  or invent disagreement. A sequence number per signal would be more robust than a timestamp.
- **Multiple intervals from rolling markets.** The tests use one market with two intervals. Overlapping rolling markets
  (``intervals_to_clear > 1`` with a shorter clearing interval) have not been run through consensus.
- **The VOLTTRON agent** (``TransactiveNodeAgent``) still carries the auction wiring only; its ``issue_signal`` and
  subscription setup need the naming conventions above, and the ``full_consensus`` publication the 2023 analyses read
  from the ``negotiation_record`` topic is now an archived measurement instead.
