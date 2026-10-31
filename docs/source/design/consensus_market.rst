Consensus markets
=================

A ``ConsensusMarket`` clears by agreement between neighboring nodes rather than by a central auction. This page
describes how the mechanism works under the TENT market state machine, what each node sends and checks, how an agent
wires it to a message bus, and what is known to be unfinished.

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
``max_balance_iterations`` (default 20); with piece-wise linear curves one pass suffices.

**Coordination (between nodes).** For each transactive neighbor the node prepares a transactive signal: the residual
system curve with that neighbor excluded, bounded to what the node can exchange with it, stated from the neighbor's
side (positive quantity means this node supplies the neighbor), with losses and demand charges that only apply locally
removed. The curve carries a *balance point*: the vertex at the power this node has scheduled with the neighbor, sign
flipped into the neighbor's perspective. The neighbor folds a received signal into its own balancing problem as that
neighbor's active vertices and replies in kind.

Convergence with a neighbor
---------------------------

Every signal a node sends to a neighbor for a market carries three acknowledgement fields: ``sequence`` (numbering the
signals sent to that neighbor for that market), ``in_reply_to`` (the sequence of the last signal received from the
neighbor when this one was prepared) and ``revised`` (false when the content is unchanged from the sender's previous
signal, so the signal is only an acknowledgement and needs no reply). ``Neighbor.check_for_convergence(market)``
decides two things per interval from the prepared, last sent and last received signals and records them in a
per-market ``CoordinationState`` (``is_converged(market)``, ``is_signal_due(market)``):

- A signal is **due** when nothing has been sent yet; or local conditions have changed so that the prepared signal
  differs from the sent one (``TransactiveSignal.conditions_have_changed``: a sent flexibility vertex has no
  counterpart within ``convergence_threshold`` among the prepared ones, or the balance point moved); or the neighbor,
  having acknowledged this node's latest revised signal, still disagrees at the balance point
  (``TransactiveSignal.balance_points_differ``, which flips the sign of the received quantity); or the neighbor's
  latest signal is revised and has not been answered. Answering a revised signal that changes nothing here resends the
  same content with ``revised`` false, which the neighbor need not answer; that is what ends an exchange.
- The interval is **converged** when the neighbor has been heard from, has acknowledged this node's latest revised
  signal (its ``in_reply_to`` is at least that sequence), agrees at the balance point, and local conditions have not
  changed. A neighbor that never replied is not in consensus, however settled this node's own position is.

Because ordering comes from the sequence numbers, no comparison of clocks between nodes is involved.

**Full consensus** for an interval is the market having balanced and every transactive neighbor being converged there.
It is recorded on every pass as ``MeasurementType.FullConsensus`` and is what the analysis tool's consensus strip plots.

Under the state machine
-----------------------

``Market.events`` drives every market type through the same states; ``ConsensusMarket`` fills in the Negotiation state.

- ``transition_from_active_to_negotiation``: initialise prices (in a rolling series, from the prior market's cleared
  prices for the intervals they share), balance locally, send an opening signal to every transactive neighbor (with its
  balance point), record consensus (none yet).
- ``while_in_negotiation`` (every tick): if any neighbor's signal arrived since the last pass, or the local problem is
  not balanced, balance locally. Then for each transactive neighbor prepare the signal, check convergence, and send
  only when a signal is due. Negotiation is never marked complete before the market-lead time, so a signal or a local
  change that arrives late in the window is still acted on. At the market-lead time the state completes cleanly when
  full consensus holds; otherwise the state machine's timeout moves the market on with a warning. (The matching engine
  follows the same rule, so orders submitted late in the window are still matched.)
- ``while_in_market_lead``: collects, does not negotiate. A signal that was sent before the deadline but arrives after
  it updates the neighbor's curve and the market rebalances locally once, so the schedule honours the neighbor's last
  word. An unchanged signal may still be sent as an acknowledgement, so the neighbor learns it is in consensus without
  any further exchange; a revised signal is withheld. Consensus is recorded again. Signals arriving in DeliveryLead or
  later are logged and left unused.
- ``on_local_change(entity)``: a local asset or neighbor model whose conditions changed (forecast, meter, constraint)
  calls this; the market rebalances on its next tick and tells the neighbors if that changes anything.
- ``transition_from_negotiation_to_market_lead``: record the final consensus state.

Because a sent signal is compared with later prepared ones, ``Neighbor.send_transactive_signal`` keeps a snapshot
(``TransactiveSignal.snapshot``) rather than the prepared object, which is revised in place on every pass.

Wiring an agent
---------------

Nothing in the library knows about a message bus. A ``TransactiveNode`` subclass implements ``issue_signal(recipient,
message)`` to deliver a serialised signal to the neighbor named ``recipient`` and ``register_subscription`` so that
``Neighbor.receive_transactive_signal`` is called with the serialised signals addressed to this node. Two naming
conventions make routing work: a neighbor's ``name`` is the remote node's ``name``, and the remote node resolves the
market by name, so every node that negotiates one consensus market must configure the series with the same ``name`` and
timing so the spawned markets share names (``<series name>_<clearing time>``). In a simulation every node should be configured with the same ``simulation_anchor_time`` (the real time at
which simulated time equals ``simulation_start_time``), so that nodes started at different wall-clock times agree on the
simulated time; a node started later joins the simulation in progress. ``tests/mock_tn.py`` ``MockTN.connect_peer`` wires two in-process
nodes this way and ``tests/integration_tests/test_consensus_two_nodes.py`` drives them: a two-node exchange that
reaches full consensus at the expected price and powers, a local change that reopens a settled negotiation, a peer that
never replies, a three-node star that clears at one price, and a loss model under which the two sides price the exchange
differently but agree on it.
