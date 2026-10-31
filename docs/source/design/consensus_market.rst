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
  next tick evaluates the new information.
- ``on_local_change(entity)``: a local asset or neighbor model whose conditions changed (forecast, meter, constraint)
  calls this; the market rebalances and tells the neighbors if that changes anything.
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
