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

Remaining work: plan (31 October 2026)
--------------------------------------

The items below are ordered so that each one needs only what precedes it. Sizes are relative: S is an afternoon, M a
day or two, L several days including a container run. Each item names its tests; everything stays under the existing
suites (``pytest tests``) and the two-node harness.

1. **Acknowledged signals instead of timestamp ordering (M).** Today "a reply arrived at or after my last send" is
   decided by comparing the sender's ``signal_time`` with the local send time. In a simulation the clocks are one;
   across agents they are not, and whoever sends last never learns that the other side accepted it. Give every signal
   a per-neighbor, per-market ``sequence`` and an ``in_reply_to``: the sequence of the last signal received from that
   neighbor when this one was prepared. The second convergence criterion becomes "the neighbor has seen my latest
   (``received.in_reply_to >= my last sequence``) and still disagrees at the balance point"; *converged* becomes "the
   neighbor has seen my latest and agrees, and nothing changed locally". A reply to an older signal is neither due nor
   converged: wait. Bump ``PROTOCOL_VERSION`` to 1.1; ``parse_signal`` accepts 1.0 without the fields and falls back to
   the timestamp rule. Archive both fields on ``transactive_signal``. Tests: the two-node harness with the city's
   signals delayed one tick (a reply to a stale signal must not close consensus); clocks deliberately skewed between
   the two nodes (consensus still reached). Decision needed: this is a protocol change that every deployed agent must
   carry at once.

2. **Neighbor coordination state per market (S).** ``Neighbor.converged``, ``signal_due`` and the sequence counters
   from item 1 become ``WeakKeyDictionary`` entries keyed by market, like the three signal dictionaries already are, with
   ``converged_in(market)`` and ``signal_due_in(market)`` accessors. ``ConsensusMarket`` and ``record_consensus`` read
   the per-market values. Keep the bare attributes as "the market last checked" for the single-market case and the
   existing tests. Test: one node with two consensus series negotiating at once; flags of one must not leak into the
   other.

3. **Periodic reconsideration (S).** Add a base hook ``Market.on_tick()`` that ``events()`` calls once per iteration in
   every state (default no-op), so a market type can act while a state is complete without changing the state machine.
   ``ConsensusMarket`` takes a market keyword ``reconsider_interval`` (seconds, default ``None`` = off) and, when set,
   reopens Negotiation from ``on_tick`` once that long has passed since its last balancing pass. ``on_local_change``
   remains the event-driven path; the agent's asset models (``ModelFrameAsset``, the open-loop load predictor) call it
   when a forecast or meter update changes their schedule. Tests: a settled negotiation reopens after the interval; an
   asset's ``schedule`` that changes its vertices triggers a resend.

4. **Rolling, overlapping markets (M; needs 2).** Run the harness with ``intervals_to_clear`` greater than one and a
   clearing interval shorter than the horizon, so two or three markets of one series negotiate concurrently and later
   markets start from the prices of earlier ones (``check_marginal_prices`` method 2). Signals are already per market;
   item 2 makes the neighbor state follow. Check that the horizon heatmap in the analysis tool shows prices firming
   across successive markets from such a run. Test: three overlapping markets reach consensus independently and the
   later ones open at the earlier ones' cleared prices.

5. **Subgradient: exercise or retire (M).** ``balance_locally`` keeps the Subgradient path but it has never run: the
   subgradient step reads ``net_powers`` that are not computed until after the first ``balance``. Either (a) order the
   first pass so ``update_supply_demand`` precedes ``balance``, make the step size a series setting instead of the
   hard-coded ``1e-1 / (10 + k)``, and add a two-node test that converges within ``max_balance_iterations``; or (b)
   remove ``Method.Subgradient`` from the market and the series. Decision needed; the recommendation is (b) unless there
   is a planned use, since Interpolation is exact for the piece-wise linear curves every TENT entity produces.

6. **One simulation clock for all agents (S).** ``TransactiveNode._setup_timer`` anchors simulated time to the node's
   own creation time, so agents started seconds apart disagree on the simulated hour (at 100 real seconds per simulated
   hour a 10 s start gap is 6 simulated minutes) and may even name their markets differently. Add an optional config
   key ``simulation_anchor_time`` (real, ISO 8601) that every agent in a run shares; when absent, keep the current
   behaviour. The container compose files set it once for the whole run. ``transactive_node.created`` continues to
   record the actual start, so the archive can still convert simulation time to real time per run. Test: two
   ``MockTN`` nodes created at different real times with the same anchor report the same ``Timer.now()``.

7. **Run it in the containers (L; needs 1, 2, 6).** Add a consensus series to the campus and city node configs under
   ``containers/tent`` with identical name and timing (the auctions stay for comparison), bring the stack up with
   ``docker-compose-tent.yml``, and confirm in the central historian database that both nodes archive
   ``FullConsensus`` rows and matching sent and received signals. The agent needs no new code paths for routing, but
   the following must be checked on a real run: the gevent loop keeps up with ``loop_sleep_duration`` while signals
   arrive asynchronously; ``VolttronDataManager`` carries the new measurement type and the signal columns to the
   historian; the legacy ``negotiation_record`` topic is dropped from the agent now that consensus is archived. Output:
   a tent-analysis report over the historian database with the negotiation trace, signal pairs and consensus strip from
   real rounds (the current samples use synthetic archives).

8. **Analysis tool follow-up (S; after 7).** Add the acknowledgement fields to ``load_signals`` and a small "rounds to
   consensus" metric per market and neighbor, and refresh the OneDrive samples from the container run.

Decisions for the user before starting: the protocol change in item 1; keep or retire Subgradient in item 5; whether
periodic reconsideration (item 3) is wanted at all or event-driven reopening is enough.
