"""
Copyright (c) 2020, Battelle Memorial Institute
All rights reserved.
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
The views and conclusions contained in the software and documentation are those
of the authors and should not be interpreted as representing official policies,
either expressed or implied, of the FreeBSD Project.
This material was prepared as an account of work sponsored by an agency of the
United States Government. Neither the United States Government nor the United
States Department of Energy, nor Battelle, nor any of their employees, nor any
jurisdiction or organization that has cooperated in th.e development of these
materials, makes any warranty, express or implied, or assumes any legal
liability or responsibility for the accuracy, completeness, or usefulness or
any information, apparatus, product, software, or process disclosed, or
represents that its use would not infringe privately owned rights.
Reference herein to any specific commercial product, process, or service by
trade name, trademark, manufacturer, or otherwise does not necessarily
constitute or imply its endorsement, recommendation, or favoring by the
United States Government or any agency thereof, or Battelle Memorial Institute.
The views and opinions of authors expressed herein do not necessarily state or
reflect those of the United States Government or any agency thereof.

PACIFIC NORTHWEST NATIONAL LABORATORY
operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
under Contract DE-AC05-76RL01830
"""

import logging

from tent.containers.measurement import Measurement
from tent.enumerations import MarketState, MarketTypes, MeasurementType, MeasurementUnit
from tent.market import Market

_log = logging.getLogger(__name__)


class ConsensusMarket(Market):
    """
    A market that clears by consensus with its transactive neighbors rather than by a central auction.

    Each node solves its own balancing problem (the "balancing sub-problem"): given the flexibility of its local assets
    and the flexibility its neighbors have offered in their last transactive signals, find the marginal price in every
    active time interval at which local net power is zero. It then offers every transactive neighbor the residual
    flexibility of the rest of the system (a transactive signal whose balance point is the power it has scheduled with
    that neighbor) and waits. A neighbor that receives such a signal folds it into its own balancing problem and replies
    in kind. Signals go back and forth (the "coordination sub-problem") until, for every neighbor and interval, the
    balance point this node sent agrees with the one the neighbor sent back and local conditions have not moved since:
    that is full consensus, recorded per interval as MeasurementType.FullConsensus.

    Mechanics under the Market state machine:

    - transition_from_active_to_negotiation: initialise prices, balance locally, send an opening signal to every
      transactive neighbor.
    - while_in_negotiation (every tick): if any neighbor's signal has arrived since the last pass, or the local problem is
      not yet balanced, balance locally again. When balanced, prepare each transactive neighbor's signal, run its
      convergence check, and send only to the neighbors that are not converged. When every neighbor is converged the
      state is complete; the interval flags are recorded on every pass.
    - on_signal_received (called by Neighbor.receive_transactive_signal): a reply that arrives after this market has
      declared consensus reopens negotiation, so the state machine evaluates the new information on its next tick.
    - transition_from_negotiation_to_market_lead: record the final consensus state, which may be "not reached" when the
      negotiation period ran out (the state machine's timeout forces the transition).

    Series settings used: method (Interpolation or Subgradient), duality_gap_threshold, default_price. Market keywords:
    max_balance_iterations (default 20) bounds the local balancing loop; Interpolation normally needs one pass.
    """
    MARKET_TYPE = MarketTypes.consensus

    def __init__(self, *args, max_balance_iterations: int = 20, **kwargs):
        # The solution method is a MarketSeries setting (series.method); Market.__init__ does not take it.
        super(ConsensusMarket, self).__init__(*args, **kwargs)
        self.max_balance_iterations: int = int(max_balance_iterations)
        # Per interval: this market converged AND every transactive neighbor agrees. See record_consensus().
        self.full_consensus: Measurement = Measurement(self, MeasurementType.FullConsensus, MeasurementUnit.Boolean)
        self.duality_gap: float | None = None  # relative gap of the last local balancing pass
        self.balance_iterations: int = 0        # iterations the last local balancing pass took
        self.negotiation_passes: int = 0        # while_in_negotiation passes that rebalanced
        self._signals_seen: dict = {}           # neighbor id -> signal_time of the last received signal processed
        self.opening_prices: dict = {}          # interval start -> price in force when negotiation opened

    # ------------------------------------------------------------------------------------------------ helpers
    @property
    def transactive_neighbors(self) -> list:
        return [n for n in getattr(self.tn, 'neighbors', []) if getattr(n, 'transactive', False)]

    def _entities(self) -> list:
        return list(self.tn.local_assets) + list(self.tn.neighbors)

    def _new_signals(self) -> list:
        """Transactive neighbors whose last received signal for this market has not been balanced against yet."""
        fresh = []
        for neighbor in self.transactive_neighbors:
            received = neighbor.last_received_signals.get(self)
            if received is None:
                continue
            if self._signals_seen.get(neighbor.id) != received.signal_time:
                fresh.append(neighbor)
        return fresh

    def _mark_signals_seen(self) -> None:
        for neighbor in self.transactive_neighbors:
            received = neighbor.last_received_signals.get(self)
            if received is not None:
                self._signals_seen[neighbor.id] = received.signal_time

    # ------------------------------------------------------------------------------------------------ balancing
    def balance_locally(self) -> bool:
        """
        Solve the local balancing sub-problem with the information currently held: every entity schedules against the
        current prices, the system curve is summed and a new price found per interval, powers are rescheduled at the new
        prices, and costs are updated. For Interpolation the price is exact for piece-wise linear curves, so the duality
        gap closes in one pass; the loop only matters for Subgradient or for assets whose curves depend on price.
        Sets self.converged and returns it.
        """
        self.converged = False
        self.duality_gap = None
        for k in range(1, self.max_balance_iterations + 1):
            self.balance_iterations = k
            # Every asset and neighbor refreshes its active vertices (neighbors from their last received signal) and
            # schedules against the current marginal prices.
            self.schedule()
            # Sum the system curve and solve for the marginal price. balance() sets converged False and returns on
            # failure (e.g. there is no supply at all), in which case there is nothing more to do this tick.
            self.balance(k)
            prices = [self.marginal_prices.get_by_interval(ti) for ti in self.time_intervals]
            if any(p is None or p.value is None for p in prices):
                _log.warning(f'{self.name}: no marginal price could be found in at least one interval.')
                return False
            # Reschedule powers at the cleared prices, then measure the duality gap.
            for entity in self._entities():
                entity.schedule_power(self)
            self.update_costs()
            self.update_supply_demand()
            if self.total_production_cost == 0:
                # Nothing costs anything: balance is judged on net power alone.
                self.duality_gap = max((abs(self.net_powers.get_by_interval(ti).value) for ti in self.time_intervals),
                                       default=0.0)
                gap_ok = self.duality_gap <= self.series.duality_gap_threshold
            else:
                self.duality_gap = (self.total_production_cost - self.total_dual_cost) / self.total_production_cost
                gap_ok = abs(self.duality_gap) <= self.series.duality_gap_threshold
            _log.debug(f'{self.name} balance iteration {k}: production {self.total_production_cost:.4f}, '
                       f'dual {self.total_dual_cost:.4f}, gap {self.duality_gap:.5f}')
            if gap_ok:
                self.converged = True
                break
        else:
            _log.warning(f'{self.name}: local balance did not close the duality gap in {self.max_balance_iterations} '
                         f'iterations (gap {self.duality_gap}); proceeding with the last prices.')
            self.converged = True
        self._mark_signals_seen()
        return self.converged

    def exchange_signals(self) -> bool:
        """
        Coordination sub-problem, one pass: offer every transactive neighbor the residual flexibility (with the balance
        point this node has scheduled with it), check convergence with that neighbor, and send only when it is not
        converged. Returns True when every transactive neighbor is converged.
        """
        all_converged = True
        for neighbor in self.transactive_neighbors:
            neighbor.prep_transactive_signal(self)
            neighbor.check_for_convergence(self)
            if neighbor.is_signal_due(self):
                neighbor.send_transactive_signal(self, include_schedule=True)
            if not neighbor.is_converged(self):
                all_converged = False
        return all_converged

    def record_consensus(self) -> None:
        """
        Record, per active time interval, whether full consensus holds: this market has converged AND every transactive
        neighbor's convergence flag for the interval is set (a neighbor with no flag yet counts as not converged).
        Consensus is a property of this market type only: Auction.converged means local scheduling finished and says
        nothing about agreement between nodes.
        """
        neighbors = self.transactive_neighbors
        for time_interval in self.time_intervals:
            flags = [n.convergence_flags.get_by_interval(time_interval) for n in neighbors]
            agreed = all(f is not None and bool(f.value) for f in flags)
            self.full_consensus.update(time_interval, bool(self.converged and agreed))

    # ------------------------------------------------------------------------------------------------ state machine
    def transition_from_active_to_negotiation(self):
        _log.debug(f'MARKET: {self.name} STATE IS NOW TRANSITIONING: Active to Negotiation')
        self.check_marginal_prices()
        # In a rolling series the prior market has cleared by now: start from its prices where the intervals overlap.
        self.opening_prices = self.refresh_prices_from_prior_market()
        self._signals_seen = {}
        for neighbor in self.transactive_neighbors:
            neighbor.reset_coordination(self)
        if self.balance_locally():
            self.negotiation_passes += 1
            # Open the negotiation: every transactive neighbor hears this node's position.
            for neighbor in self.transactive_neighbors:
                neighbor.prep_transactive_signal(self)
                neighbor.send_transactive_signal(self, include_schedule=True)
        self.record_consensus()

    def while_in_negotiation(self):
        if self._new_signals() or not self.converged:
            if not self.balance_locally():
                self.record_consensus()
                return  # No price yet (e.g. no supply offered). Wait for signals or the negotiation deadline.
            self.negotiation_passes += 1
        if self.exchange_signals():
            _log.debug(f'MARKET: {self.name} has reached full consensus.')
            self._stateIsCompleted = True
        self.record_consensus()

    def reopen(self, reason: str) -> None:
        """Negotiation is not over while new information can arrive: clear the completed flag so the next tick acts."""
        if self.market_state is MarketState.Negotiation and self._stateIsCompleted:
            _log.debug(f'MARKET: {self.name} reopens negotiation: {reason}.')
            self._stateIsCompleted = False

    def on_signal_received(self, neighbor) -> None:
        """A neighbor's reply during Negotiation is new information: the next tick rebalances against it."""
        self.reopen(f'signal from {neighbor.name}')

    def on_local_change(self, entity=None) -> None:
        """Local conditions moved (forecast, meter, constraint): rebalance and tell the neighbors if that changes
        anything. Legacy agents did this on a fixed reschedule interval; here the entity that changed says so."""
        self.converged = False
        self.reopen(f'local change in {getattr(entity, "name", entity)}')

    def transition_from_negotiation_to_market_lead(self):
        # Negotiation may have ended by timeout rather than consensus; record the consensus state either way.
        self.record_consensus()
        if not all(bool(v) for v in self.full_consensus.values):
            _log.warning(f'MARKET: {self.name} left Negotiation without full consensus in every interval.')
        super(ConsensusMarket, self).transition_from_negotiation_to_market_lead()
