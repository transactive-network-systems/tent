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

from collections import defaultdict
from sortedcontainers import SortedList, SortedSet
from typing import TYPE_CHECKING
from weakref import proxy

from tent.base import Component
from tent.containers import IntervalSeries, MarketTimings, Measurement, SDCurve, SDMeasurement, Vertex
from tent.enumerations import MarketState, MarketTypes, MeasurementType, MeasurementUnit, Method
from tent.market.price_models import HourlyAveragePriceModel
from tent.utils.helpers import safe_weak_proxy_callback
from tent.utils.timer import Timer

if TYPE_CHECKING:
    from tent.market.market_series import MarketSeries

_log = logging.getLogger(__name__)


class Market(Component):
    # Market Base Class
    # At least one Market must exist (see the firstMarket object) to drive the timing with which new TimeIntervals are
    # created.
    MARKET_TYPE = MarketTypes.unknown

    def __init__(self,
                 transactive_node,
                 market_series,
                 initial_market_state: MarketState,
                 transition_times: MarketTimings,
                 market_to_be_refined=None,
                 prior_market_in_series=None,
                 price_model=None
                 ):
        # Build market name BEFORE calling Component.__init__ (self.name not yet set)
        market_name = '_'.join((*market_series.name.split(' '), transition_times.delivery_lead.isoformat(timespec='seconds')))
        super(Market, self).__init__(market_series.description, market_name, transactive_node)

        self.series: MarketSeries = proxy(market_series, safe_weak_proxy_callback(self, 'market_series'))
        self.transition_times: MarketTimings = transition_times

        #### References to other markets: ####
        # Market to be refined or corrected
        self.market_to_be_refined: Market = (proxy(market_to_be_refined,
                                              safe_weak_proxy_callback(self, 'market_to_be_refined'))
                                     if isinstance(market_to_be_refined, Market) else None)
        # [Market] Reference to preceding market in this market series
        self.prior_market_in_series: Market = (proxy(prior_market_in_series,
                                             safe_weak_proxy_callback(self, 'prior_market_in_series'))
                                       if isinstance(prior_market_in_series, Market) else None)

        # The market row must exist before anything that references it (measurements, time intervals, state changes).
        # NOTE for subclasses: any attribute your get_record() reports must be set BEFORE calling Market.__init__.
        self._market_state: MarketState = initial_market_state
        self.archive()
        self.record_state(initial_market_state)

        # These are dynamic properties that are assigned in code and should not be manually configured:
        self.active_vertices: SDMeasurement = SDMeasurement(self, measurement_type=MeasurementType.SystemVertex)
        self.blended_prices1: Measurement = Measurement(self, MeasurementType.BlendedPrice, MeasurementUnit.Monetary)  # (future functionality)
        self.blended_prices2: Measurement = Measurement(self, MeasurementType.BlendedPrice, MeasurementUnit.Monetary)  # (future functionality)
        self.converged: bool = False
        self.dual_costs: Measurement = Measurement(self, MeasurementType.DualCost, MeasurementUnit.Monetary)  # Values are [$]
        self.marginal_prices: Measurement = Measurement(self, MeasurementType.MarginalPrice, MeasurementUnit.Monetary) # [$/kWh]
        self.net_powers: Measurement = Measurement(self, MeasurementType.NetPower, MeasurementUnit.kW)  # [avg.kW]
        self.price_model = price_model
        self.production_costs: Measurement = Measurement(self, MeasurementType.ProductionCost, MeasurementUnit.Monetary)  # [$]
        self.reconciled: bool = False  # [Boolean] Convergence flag
        self.time_intervals: IntervalSeries = IntervalSeries(self,  # [TimeInterval] Current list of active time intervals
                                                             first_start_time=self.transition_times.delivery,
                                                             duration=self.series.duration.market_interval,
                                                             number_of_intervals=self.series.intervals_to_clear)
        self.total_demand: Measurement = Measurement(self, MeasurementType.TotalDemand, MeasurementUnit.kW)  # [avg.kW]
        self.total_dual_cost: float = 0.0  # [$]
        self.total_generation: Measurement = Measurement(self, MeasurementType.TotalGeneration, MeasurementUnit.kW)  # [avg.kW]
        self.total_production_cost: float = 0.0  # [$]

        # Set up market
        self.check_marginal_prices()
        neighbors = getattr(self.tn, 'neighbors', [])
        # Avoid iterating over Mock or non-iterable placeholders
        if not isinstance(neighbors, (list, tuple, set)):
            neighbors = []
        for neighbor in neighbors:
            schedule_fn = getattr(neighbor, 'schedule', None)
            if callable(schedule_fn):
                schedule_fn(self)

        # This flag should be made False in each transition.
        # It should be made True after all the tasks have been completed in the ensuing market state.
        self._stateIsCompleted = False

    @property
    def market_state(self) -> MarketState:
        return self._market_state

    @market_state.setter
    def market_state(self, new_state: MarketState):
        """Every state change is recorded in market_state_change (append-only), including the initial state."""
        if new_state != self._market_state:
            self._market_state = new_state
            self.record_state(new_state)

    def record_state(self, state: MarketState) -> None:
        self.data_manager.archive_data({'table_name': 'market_state_change',
                                        'data': {'market_id': self.id, 'state': state, 'record_time': Timer.now()}})

    def events(self):
        """
        This is the market state machine. Activities should be assigned to state transition events and to the states
        themselves using the supplied methods. This state machine should not itself be modified by implementers because
        doing so may affect alternative market methods' state models.

        :return: None

        ================================================================================================================
        MarketStates    Market Events               Intervals               State Machine Events
        ============    =============               =========               ====================
        Initial ------ (market gets constructed) -------------------------- transition_from_initial_to_inactive
        |                                           (immediate)
        Inactive -------------------------------------------------------------------------------------------------------
        |                                           (indeterminate)
        Active -------- (activation_start_time) --------------------------- transition_from_inactive_to_active
        |                                           activation_lead_time      while_in_active
        Negotiation --- (negotiation_start_time) -------------------------- transition_from_active_to_negotiation
        |                                           negotiationLeadTime     while_in_negotiation
        MarketLead ---- (market_lead_start_time) -------------------------- transition_from_negotiation_to_market_lead
        |                                           market_lead_time          while_in_market_lead
        DeliveryLead -- marketClearingTime -------------------------------- transition_from_market_lead_to_delivery_lead
        |                                           delivery_lead_time        while_in_delivery_lead
        Delivery ------ (delivery_start_time) ----------------------------- transition_from_delivery_lead_to_delivery
        |                                           interval_duration        while_in_delivery
        Reconcile ----- (reconcile_start_time) ---------------------------- transition_from_delivery_to_reconcile
        |                                                                   while_in_reconcile
        Expired ------- (reconciliation complete) ------------------------- transition_from_reconcile_to_expire
        |                                           (indefinite)
        ================================================================================================================
            Note: Items in (parentheses) are calculated or inferred, not explicit variables.
            Note: Market states are shown at their starting times or starting events.
        ================================================================================================================
        """
        current_time = Timer.now()

        # ── HARD EXPIRATION BACKSTOP ─────────────────────────────────────────────────────────────────────────────────────
        # If the expiration time has passed, force the market to Expired regardless of its current state.
        # This prevents markets from hanging indefinitely in intermediate states (e.g., waiting for signals
        # that never arrive) and leaking memory.
        #
        # MarketState.Delivery is excluded because when duration_reconciliation == 0,
        # expiration_start == reconciliation_start. A market legitimately in Delivery at that moment
        # is NOT stuck — it is about to enter Reconcile on this same tick via the normal
        # Delivery→Reconcile transition. The Delivery soft timeout below handles any genuinely
        # stuck Delivery state, making the backstop exclusion safe.
        if (self.market_state not in (MarketState.Initial, MarketState.Delivery, MarketState.Expired)
                and current_time >= self.transition_times.expiration):
            _log.warning(
                f'MARKET: {self.name} force-expired from state {self.market_state.name}. '
                f'Expiration time {self.transition_times.expiration} has passed.')
            self.market_state = MarketState.Expired
            self._stateIsCompleted = False
            # Fall through to the Expired handler at the bottom of this method.

        # TODO: Is the INITIAL state is used to set up the first market in a series.
        #  This should probably be used to create priors which this market will use so that it is not warning
        #  about steps yet to be completed. Even with the market series, it may still be useful because
        #  different market types may have different setup requirements.
        # EVENT 0: % FIRST INSTANTIATION OF A MARKET SERIES ************************************************************
        # First time setup of a new market series.
        if self.market_state == MarketState.Initial:
            self.transition_from_initial_to_inactive()
            self.market_state = MarketState.Inactive

            # Set false upon transition to new market state.
            self._stateIsCompleted = False

        # EVENT 1A: % A NEW MARKET OBJECT BECOMES ACTIVE ***************************************************************
        # This is the instantiation of a market object in market state "Active." This transition occurs at a time when
        # a new market object is needed, as specified relative to its market clearing time. Specifically, a new market
        # object is instantiated in its "Inactive" state a specified negotiation lead time and market lead time prior to
        # the market's clearing time. Note that this logic seems a little backward because the state's start must be
        # determined *before* the needed market object exists.

        # TODO: Rework comments to reflect use of self.transition_times.

        # EVENT 1B: TRANSITION FROM INACTIVE TO ACTIVE STATE ***********************************************************.
        if self.market_state == MarketState.Inactive:  # and self._stateIsCompleted is True:
            if current_time >= self.transition_times.active:
                # Change the market state to "Active."
                self.market_state = MarketState.Active
                _log.debug(f'MARKET: {self.name} STATE IS NOW: MarketState.Active')
                self.series.spawn()
                # Call this replaceable method where appropriate actions can be taken.
                self.transition_from_inactive_to_active()

                self._stateIsCompleted = False

        # EVENT 1C: ACTIONS WHILE IN THE ACTIVE STATE ******************************************************************
        # These are actions to be taken while the market object is in its initial "Active" market state.
        # 210118DJH. New flag. No state should be entered until the prior one is done.
        if self.market_state == MarketState.Active and self._stateIsCompleted is not True:
            # Place actions to be taken in this state in the following method. The method may be overwritten by child
            # classes of class Market.
            # 210118DJH: NOTE: Flag "_stateIsComplete" must be set true after the market's responsibilities have been
            #            completed in this next method or its replacements.
            self.while_in_active()

            # ── SOFT TIMEOUT: Active state ──
            if self._stateIsCompleted is not True and current_time >= self.transition_times.negotiation:
                _log.warning(f'MARKET: {self.name} Active state timed out, forcing completion.')
                self._stateIsCompleted = True

        # EVENT 2A: TRANSITION FROM ACTIVE TO NEGOTIATION STATE ********************************************************
        # This is the transition from "Active" to "Negotiation" market states. Market state "Negotiation" begins at a
        # time specified before an upcoming market clearing of a market object. Specifically, it begins a specified
        # market lead time, less another negotiation lead time, prior to the clearing of the market object.

        # 210118DJH. New flag. Do not transition out of a state until it is completed.
        if self.market_state == MarketState.Active and self._stateIsCompleted is True:

            if current_time >= self.transition_times.negotiation:
                # Change the market state to "Negotiation."
                self.market_state = MarketState.Negotiation
                _log.debug(f'MARKET: {self.name} STATE IS NOW: MarketState.Negotiation')

                # Place other transition actions in this following method. The method may be replaced.
                # of class Market.
                self.transition_from_active_to_negotiation()

                # 210118DJH: New flag. Set false upon entering a new market state.
                self._stateIsCompleted = False

        # EVENT 2B: ACTIONS WHILE IN MARKET STATE NEGOTIATION **********************************************************
        # These are the actions while in the "Negotiation" market state.

        if self.market_state == MarketState.Negotiation and self._stateIsCompleted is not True:

            # Place actions to be completed during this market state in the following method. The method may be
            # overwritten by child classes of class Market. Note that the actions during this state may be made
            # dependent upon a convergence flag.
            # 210118DJH: NOTE: Flag "_stateIsComplete" must be set true after the market's responsibilities have been
            #            completed in this next method or its replacements.
            self.while_in_negotiation()

            # ── SOFT TIMEOUT: Negotiation state ──
            if self._stateIsCompleted is not True and current_time >= self.transition_times.market_lead:
                _log.warning(f'MARKET: {self.name} Negotiation state timed out, forcing completion.')
                self.converged = True
                self._stateIsCompleted = True

        # EVENT 3A: TRANSITION FROM NEGOTIATION TO MARKET LEAD STATE ***************************************************
        # This is the transition from "Negotiation" to "MarketLead" market states.
        # The transition occurs at a time relative to the market object's market clearing time. Specifically, it starts
        # a defined lead time prior to the market clearing time.
        # 210118DJH: Adding flag. No state transition should be allowed unless the prior state is completed.
        if self.market_state == MarketState.Negotiation and self._stateIsCompleted is True:

            if current_time >= self.transition_times.market_lead:
                # Change the market state to "MarketLead."
                self.market_state = MarketState.MarketLead
                _log.debug(f'MARKET: {self.name} STATE IS NOW: MarketState.MarketLead')

                #  Place other transition actions in this following method. The method may be replaced.
                #  of class Market.
                self.transition_from_negotiation_to_market_lead()

                # 210118DJH: New flag. Set false prior to transition to new market state.
                self._stateIsCompleted = False

        # EVENT 3B: ACTIONS WHILE IN THE MARKET LEAD STATE *************************************************************
        # These are the actions while in the "MarketLead" market state.

        if self.market_state == MarketState.MarketLead and self._stateIsCompleted is not True:
            #  Specify actions for the market state "MarketLead" in this following method. The method may be
            #  overwritten by child classes of class Market.
            # 210118DJH: NOTE: Flag "_stateIsComplete" must be set true after the market's responsibilities have been
            #            completed in this next method or its replacements.
            self.while_in_market_lead()

            # ── SOFT TIMEOUT: MarketLead state ──
            if self._stateIsCompleted is not True and current_time >= self.transition_times.delivery_lead:
                _log.warning(f'MARKET: {self.name} MarketLead state timed out, forcing completion.')
                self._stateIsCompleted = True

        # EVENT 4A: TRANSITION FROM MARKET LEAD TO DELIVERY LEAD STATE *************************************************
        # This is the transition from "MarketLead" to "DeliveryLead" market states.
        if self.market_state == MarketState.MarketLead and self._stateIsCompleted is True:

            if current_time >= self.transition_times.delivery_lead:
                # Set the market state to "DeliveryLead."
                self.market_state = MarketState.DeliveryLead
                _log.debug(f'MARKET: {self.name} STATE IS NOW: MarketState.DeliveryLead')

                # Place other transition actions here. This following method may be replaced.
                self.transition_from_market_lead_to_delivery_lead()

                # 210118DJH: New flag. Set false prior to new market state.
                self._stateIsCompleted = False

        # EVENT 4B: ACTIONS WHILE IN MARKET STATE DELIVERY LEAD ********************************************************
        # These are the actions while in the "DeliveryLead" market state.

        if self.market_state == MarketState.DeliveryLead and self._stateIsCompleted is not True:
            # Place actions in this following method if they are to occur during market state "DeliveryLead." This
            # method may be overwritten by child classes of class Market.
            # 210118DJH: NOTE: Flag "_stateIsComplete" must be set true after the market's responsibilities have been
            #            completed in this next method or its replacements.
            self.while_in_delivery_lead()

            # ── SOFT TIMEOUT: DeliveryLead state ──
            if self._stateIsCompleted is not True and current_time >= self.transition_times.delivery:
                _log.warning(f'MARKET: {self.name} DeliveryLead state timed out, forcing completion.')
                self._stateIsCompleted = True

        # EVENT 5A: TRANSITION FROM DELIVERY LEAD TO DELIVERY **********************************************************
        # This is the transition from "DeliveryLead" to "Delivery" market states. The start of market state "Delivery"
        # is timed relative to the market object's market clearing time. Specifically, it begins a delivery lead time
        # after the market has cleared.
        # 210118DJH: Adding flag. No state transition should be allowed unless the prior state is done.
        if self.market_state == MarketState.DeliveryLead and self._stateIsCompleted is True:

            if current_time >= self.transition_times.delivery:
                # Change the market state from "DeliverLead" to "Delivery."
                self.market_state = MarketState.Delivery
                _log.debug(f'MARKET: {self.name} STATE IS NOW: MarketState.Delivery')

                # Other actions for this transition should be placed in the following method, which can be replaced.
                self.transition_from_delivery_lead_to_delivery()

                # 210118DJH: New flag. Set false prior to each new market state.
                self._stateIsCompleted = False

        # EVENT 5B: ACTIONS WHILE IN MARKET STATE DELIVERY *************************************************************
        # These are the actions while in the "Delivery" market state.

        if self.market_state == MarketState.Delivery and self._stateIsCompleted is not True:
            # Place any actions to be conducted in market state "Delivery" in this following method. The method may be
            # overwritten by child classes of class Market.
            # 210118DJH: NOTE: Flag "_stateIsComplete" must be set true after the market's responsibilities have been
            #            completed in this next method or its replacements.
            self.while_in_delivery()

            # ── SOFT TIMEOUT: Delivery state ──
            if self._stateIsCompleted is not True and current_time >= self.transition_times.reconciliation:
                _log.warning(f'MARKET: {self.name} Delivery state timed out, forcing completion.')
                self._stateIsCompleted = True

        # EVENT 6A: TRANSITION FROM DELIVERY TO RECONCILE **************************************************************
        # This is the transition from "Delivery" to "Reconcile" market states. The "Reconcile" market state begins at a
        # time referenced from the market object's clearing time. Specifically, reconciliation begins after all the
        # market object's market intervals and an additional delivery lead time have expired after the market clears.
        # 210118DJH: Adding flag. No state transition should be allowed unless the prior state is completed.
        if self.market_state == MarketState.Delivery and self._stateIsCompleted is True:

            if current_time >= self.transition_times.reconciliation:

                # Change the market state to "Reconcile."
                self.market_state = MarketState.Reconcile
                _log.debug(f'MARKET: {self.name} STATE IS NOW: MarketState.Reconcile')

                # Other transition actions may be placed in this method.
                self.transition_from_delivery_to_reconcile()

                # 210118DJH: New flag. Set flat false prior to every new market state.
                self._stateIsCompleted = False

        # EVENT 6A: ACTIONS WHILE IN MARKET STATE RECONCILE ************************************************************
        # These are the actions while in the "DeliveryLead" market state.

        if self.market_state == MarketState.Reconcile and self._stateIsCompleted is not True:

            # Place actions in this following method if they should occur during market state "Reconcile." This method
            # may be overwritten by children of the Market class.
            # 210118DJH: NOTE: Flag "_stateIsComplete" must be set true after the market's responsibilities have been
            #            completed in this next method or its replacements.
            self.while_in_reconcile()

            # ── SOFT TIMEOUT: Reconcile state ──
            if self._stateIsCompleted is not True and current_time >= self.transition_times.expiration:
                _log.warning(f'MARKET: {self.name} Reconcile state timed out, forcing completion.')
                self.reconciled = True
                self._stateIsCompleted = True

        # EVENT 7A: TRANSITION FROM RECONCILE TO EXPIRED ***************************************************************
        # This is the transition from "Reconcile" to "Expired" market states.
        # 210118DJH: Adding flag. No state transition should be allowed unless the prior state is completed.
        if self.market_state == MarketState.Reconcile and self._stateIsCompleted is True:

            # 210118DJH: The use of flag "reconciled" is redundant now that flag "stateIsCompleted" is available.
            #            TODO: Replace instanced of flag "reconciled" by new flag "stateIsCompleted."
            if self.reconciled is True:
                # Change the market state to "Expired".
                self.market_state = MarketState.Expired
                _log.debug(f'MARKET: {self.name} STATE IS NOW: MarketState.Expired')

                # Replace this method for other transitional actions.
                self.transition_from_reconcile_to_expired()

                # 210118DJH: New flag. Set false prior to any new state.
                self._stateIsCompleted = False

        # EVENT 7B: WHILE EXPIRED **************************************************************************************
        # These are the actions while in the "Expired" market state. It should be pretty standard that market objects
        # are deleted after they expire, so it is unlikely that alternative actions will be needed by child Market
        # classes.

        if self.market_state == MarketState.Expired and self._stateIsCompleted is not True:

            # Delete market intervals that are defined by this market object.
            self.time_intervals.destroy()

            # Remove the expired market from the containing Market Series.
            expired_markets = [x for x in self.series.markets if x == self]
            for expired in expired_markets:
                _log.info(f'Removing expired market: {expired}')
                self.series.markets.discard(expired)
            # Mark completed so this block is not re-entered on subsequent events() calls.
            # NOTE: Removal from series.markets is NOT done here because events() may be called
            # during iteration over series.markets. The series should prune expired markets
            # separately (e.g., in MarketSeries or _state_machine_iteration).
            self._stateIsCompleted = True

    def transition_from_initial_to_inactive(self):
        """
        Steps for initialization of the first market in a new market series which cannot be completed by __init__()

        :return: None
        """
        _log.debug(f'MARKET: {self.name} STATE IS NOW TRANSITIONING: Initial to Inactive')
        return None

    def transition_from_inactive_to_active(self):
        """
        These actions, if any are taken as a market transitions from its inactive to its active market state.

        :return: None
        """
        _log.debug(f'MARKET: {self.name} STATE IS NOW TRANSITIONING: Inactive to Active')
        return None

    def while_in_active(self):
        """
        For activities that should happen while a market object is in its initial "Active" market state. This method
        may be overwritten by child classes of Market to create alternative market behaviors during this market state.
        It will be rare for a market object to have actions in its Active state. It usually will immediately enter its
        Negotiation state.

        :return: None
        """
        #_log.debug(f'{self.name} IS IN W_IN_ACTIVE')

        # pass
        # 210118DJH: NOTE: Flag "_stateIsComplete" must be set true after the market's responsibilities have been
        #            completed in this next method or its replacements.
        self._stateIsCompleted = True
        return None

    def transition_from_active_to_negotiation(self):
        """
        For activities that should accompany a market object's transition from market state "Active" to "Negotiation."
        This method may be overwritten by child classes of Market to create alternative market behaviors during this
        transition.

        :return: None
        """
        _log.debug(f'MARKET: {self.name} STATE IS NOW TRANSITIONING: Active to Negotiation')
        return None

    def while_in_negotiation(self):
        """
        For activities that should happen while a market object is in its "Negotiation" market state. This method may
        be overwritten by child classes of Market to create alternative market behaviors during this market state.

        :return: None
        """
        #_log.debug(f'{self.name} IS IN W_IN_NEGOTIATION')

        # A convergence flag is available to distinguish actions to be undertaken while actively negotiating and others
        # while convergence has been obtained.
        if not self.converged:
            self.balance()

        else:
            pass
            # 210118DJH: NOTE: Flag "_stateIsComplete" must be set true after the market's responsibilities have been
            #            completed in this next method or its replacements.
            self._stateIsCompleted = True

        return None

    def on_local_change(self, entity=None) -> None:
        """
        Called when a local asset or neighbor model has new information that may move this market's solution (a revised
        forecast, a new meter reading, a changed constraint). Market types that negotiate (see ConsensusMarket) use it
        to reopen the Negotiation state; the base market needs nothing here.
        """
        return None

    def transition_from_negotiation_to_market_lead(self):
        """
        For activities that should accompany a market object's transition from market state "Negotiation" to
        "MarketLead." This method may be overwritten by child classes of Market to create alternative market behaviors
        during this transition.

        :return: None
        """
        _log.debug(f'MARKET: {self.name} STATE IS NOW TRANSITIONING: Negotiation to Market Lead')
        return None

    def while_in_market_lead(self):
        """
        For activities that should happen while a market object is in its "MarketLead" market state. This method may
        be overwritten by child classes of Market to create alternative market behaviors during this market state.

        :return: None
        """
        #_log.debug(f'{self.name} IS IN W_IN_MARKET_LEAD')
        self._stateIsCompleted = True
        return None

    def transition_from_market_lead_to_delivery_lead(self):
        """
        For activities that should accompany a market object's transition from market state "MarketLead" to
        "DeliveryLead," (i.e., the clearing of the market). This method may be overwritten by child classes of Market
        to create alternative market behaviors during this transition.

        :return: None
        """
        _log.debug(f'MARKET: {self.name} STATE IS NOW TRANSITIONING: Market Lead to Delivery Lead')
        return None

    def while_in_delivery_lead(self):
        """
        For activities that should happen while a market object is in its "DeliveryLead" market state. This method may
        be overwritten by child classes of Market to create alternative market behaviors during this market state.

        :return: None
        """
        #_log.debug(f'{self.name} IS IN W_IN_DELIVERY_LEAD')
        self._stateIsCompleted = True
        return None

    def transition_from_delivery_lead_to_delivery(self):
        """
        For activities that should accompany a market object's transition from market state "DeliveryLead" to
        "Delivery." This method may be overwritten by child classes of Market to create alternative market behaviors
        during this transition.

        :return: None
        """
        _log.debug(f'{self.name} IS IN T_F_DELIVERY_LEAD_TO_DELIVERY')
        # A good practice upon entering the delivery period is to update the market's price model using the final
        # marginal prices.
        _log.debug(f'Final prices are: {self.marginal_prices.log_report}')
        for time_interval, final_price in self.marginal_prices.all:
            self.price_model.update(time_interval.start_time, final_price.value)

        # Tell Local Assets it is time to actuate.
        self._actuate_assets()
        # self.balanced_price_publish() # TODO: Is this something that should still exist?
        return None

    def balanced_price_publish(self):  # TODO: Should this be replaced with archival functionality?
        scheduled_powers = defaultdict(dict)
        for entity in self.tn.neighbors + self.tn.local_assets:
            for time_interval, scheduled_power in entity.scheduled_powers.all:
                if time_interval.market is self:
                    scheduled_powers[time_interval.start_time.isoformat()][entity.name] = scheduled_power.value
        self.tn.publish(
            'balanced_price_record', {
                'tnt_market_name': self.name,
                'balanced_prices': {k.start_time.isoformat(): v.value for k, v in self.marginal_prices.all},
                'schedule_powers': scheduled_powers
            })

    def _actuate_assets(self):
        for local_asset in self.tn.local_assets:
            _log.debug(f'MARKET {self.name} is ACTUATING: {local_asset.name}')
            local_asset.actuate(self)

    def while_in_delivery(self):
        """
        For activities that should happen while a market object is in its "Delivery" market state. This method may be
        overwritten by child classes of Market to create alternative market behaviors during this market state.

        :return: None
        """
        #_log.debug(f'{self.name} IS IN W_IN_DELIVERY')
        # TBD: These actions will be common to most transactive systems during the delivery market state:
        # - monitor and meter assets and power exchanges
        # - control assets to negotiated average power
        self._stateIsCompleted = True
        return None

    def transition_from_delivery_to_reconcile(self):
        """
        For activities that should accompany a market object's transition from market state "Delivery" to "Reconcile."
        This method may be overwritten by child classes of Market to create alternative market behaviors during this
        transition.

        :return: None
        """
        _log.debug(f'MARKET: {self.name} STATE IS NOW TRANSITIONING: Delivery to Reconcile')
        return None

    def while_in_reconcile(self):
        """
        For activities that should happen while a market object is in its "Reconcile" market state. This method may be
        overwritten by child classes of Market to create alternative market behaviors during this market state.

        :return: None
        """
        #_log.debug(f'{self.name} IS IN W_IN_RECONCILE')
        self.reconciled = True
        self._stateIsCompleted = True
        return None

    def transition_from_reconcile_to_expired(self):
        """
        For activities that should accompany a market object's transition from market state "Reconcile" to "Expired."
        This method may be overwritten by child classes of Market to create alternative market behaviors during this
        transition.

        :return: None
        """
        _log.debug(f'MARKET: {self.name} STATE IS NOW TRANSITIONING: Reconcile to Expired')

    def assign_system_vertices(self):
        """
        Collect active vertices from neighbor and asset models and reassign them with aggregate system information
        for all active time intervals.

        ASSUMPTIONS:
        - Active time intervals exist and are up-to-date
        - Local convergence has occurred, meaning that power balance, marginal price, and production costs have been
          adequately resolved from the
        local agent's perspective
        - The active vertices of local assets exist and are up-to-date.
        - The vertices represent available power flexibility.
        - The vertices include meaningful, accurate production-cost information.
        - There is agreement locally and in the network concerning the format and content of transactive records.

        Calls method sum_vertices in each time interval.

        OUTPUTS:
        - Updates property activeVertices - vertices that define the net system balance and flexibility. The meaning of
          the vertex properties are
        - marginalPrice: marginal price [$/kWh]
        - cost: total production cost at the vertex [$]. (A locally meaningful blended electricity price is (total
          production cost / total production)).
        - power: system net power at the vertex (The system "clears" where system net power is zero.)
        """
        for time_interval in self.time_intervals:
            # Call the utility sum_vertices to recreate the aggregate vertices in the indexed time interval.
            self.active_vertices.update(time_interval, self.sum_vertices(time_interval), replace=True)

    def balance(self, k=1):
        """
        Balance current market

        :param k: iteration counter
        :return:
        """
        # TODO: A test is badly needed for method balance().
        # 202015DJH: The base balance method is being greatly simplified. Whereas it originally iterated to find a
        #            marginal price in each active time interval, it now simply performs a single iteration using either
        #            the iterative sub-gradient or interpolation approach. Iteration and preconditions may need to be
        #            completed within the market's state machine.
        # TODO: Consider having the balance method solve for both marginal price AND quantity.
        if self.series.method == Method.Interpolation:
            self.assign_system_vertices()
            _log.debug(f"{self.name} market active vertices are: {self.active_vertices.log_report}")

        # Index through active time intervals.
        for time_interval in self.time_intervals:
            # Find the marginal price interval value for the corresponding indexed time interval.
            marginal_price = self.marginal_prices.get_by_interval(time_interval) # [$/kWh]

            # Extract its marginal price value [$/kWh] as a trial clearing price (that may be replaced).
            cleared_marginal_price = self.series.default_price if marginal_price is None else marginal_price.value

            if self.series.method == Method.Subgradient:
                # Find the net power corresponding to the indexed time interval.
                net_power = self.net_powers.get_by_interval(time_interval)
                total_generation = self.total_generation.get_by_interval(time_interval)
                total_demand = self.total_demand.get_by_interval(time_interval)

                net_power = net_power.value / (total_generation.value - total_demand.value)

                # Update the marginal price using sub-gradient search.
                cleared_marginal_price = cleared_marginal_price - (net_power * 1e-1) / (10 + k)  # [$/kWh]

            elif self.series.method == Method.Interpolation:
                # Get the indexed active system vertices.
                active_sd_curve: SDCurve = self.active_vertices.get_by_interval(time_interval)

                try:
                    # Find the vertex that bookcases the balance point from the lower side.
                    if not (lower_active_vertex := max(filter(lambda v: v.quantity < 0, active_sd_curve.vertices),
                                                       key=lambda v: v.quantity, default=None)):
                        raise ValueError(f'No load demand vertices (quantity < 0) were found.')

                    # Find the vertex that bookcases the balance point from the upper side.
                    if not (upper_active_vertex := min(filter(lambda v: v.quantity >= 0, active_sd_curve.vertices),
                                                       key=lambda v: v.quantity, default=None)):
                        raise ValueError(f'No supply vertices (quantity >= 0) were found.')

                    if (power_range := upper_active_vertex.quantity - lower_active_vertex.quantity) == 0:
                        _log.warning(f'There is no quantity range is 0. Marginal price is not unique.')

                    # Interpolate the marginal price in the interval using a principle of similar triangles.
                    marginal_price_range = upper_active_vertex.price - lower_active_vertex.price
                    cleared_marginal_price = (- marginal_price_range * lower_active_vertex.quantity / power_range
                                             + lower_active_vertex.price)
                    # TODO: Consider adding a feature to find each asset's and neighbor's cleared power at this point.
                    #       This would require interpolation of the cleared average power, and the implication must be
                    #       interpreted for each asset and neighbor.
                except ValueError as e:
                    _log.warning(f'Failed to balance {self.name} in time interval {time_interval.name}: {e}')
                    self.converged = False
                    return

            # Regardless of the method used, assign the cleared marginal price to the marginal price value for the
            # indexed active time interval.
            self.marginal_prices.update(time_interval, cleared_marginal_price)

    def calculate_blended_prices(self):
        """
        Calculate the blended prices for active time intervals.

        The blended price is the averaged weighted price of all locally generated and imported energies. A sum is made
        of all costs of generated and imported energies, which are prices weighted by their corresponding energy. This
        sum is divided by the total generated and imported energy to get the average.

        The blended price does not include supply surplus and may therefore be a preferred representation of price for
        local loads and friendly neighbors, for which myTransactiveNode is not competitive and profit-seeking.
        """
        # Perform checks on interval primal production costs to ensure smooth calculations.
        # NOTE: This does not check the veracity of the primal costs.
        if not self.production_costs:
            _log.warning('Production costs have not been calculated.')
            return

        for time_interval in self.time_intervals:
            # Calculate a blended price for this market time interval.
            production_cost = self.production_costs.get_by_interval(time_interval)  # [$]
            total_generation = self.total_generation.get_by_interval(time_interval)  # [kWh]
            blended_price = production_cost / total_generation  # [$/kWh]

            # Create or update blended price entry for this time interval.
            self.blended_prices1.update(time_interval, blended_price)

    @staticmethod
    def _extract_price_from_other_market(other_market, time_interval):
        marginal_price = None
        for other_mkt_ti, other_mkt_mp in other_market.marginal_prices.all:
            # If the currently indexed time interval lies within the refined market's interval,
            #   capture its value as the marginal price in this time interval.
            other_interval_end_time = other_mkt_ti.start_time + other_mkt_ti.duration
            if other_mkt_ti.start_time <= time_interval.start_time < other_interval_end_time:
                marginal_price = other_mkt_mp.value
        return marginal_price

    def check_marginal_prices(self):
        """
        191212DJH: Much of the logic may be simplified upon the introduction of isNewestMarket flag and assertion that
        priorRefinedMarket points to the specific market object that is being refined or corrected.

        Check that marginal prices exist for active time intervals.

        Updated Oct. 2019. Focuses now on initializing marginal prices for the market's time intervals. A priority is
        established for the best available prices from which to initialize new market periods:
        1. If the market interval already has a price, stop. 
        2. If the same time interval exists from a prior market clearing, its price may be used. This can be the case
           where similar successive markets' delivery periods overlap, e.g., a rolling window of 24 hours.
        3. If this market corrects another prior market, its cleared price should be used, e.g., a real-time market
           that corrects day-ahead market periods.
        4. If there exists a price forecast model for this market, this model should be used to forecast price. See
           method Market.model_prices().
        5. If all above methods fail, market periods should be assigned the market's default price. See property
           Market.defaultPrice.

        OUTPUTS:
           populates list of active marginal prices (see Measurement)
        """

        for time_interval in self.time_intervals:

            # METHOD #1: If the market interval already has a price, you're done. Otherwise, try other methods.
            if not self.marginal_prices.get_by_interval(time_interval):

                # METHOD #2. If the same time interval exists from the prior market clearing, its price may be used.
                #   This can be the case where if more than one time interval is cleared by each market and
                #   similar successive markets' delivery periods overlap, e.g., a rolling window of 24 hours.

                if self.prior_market_in_series and self.series.intervals_to_clear > 1:
                    marginal_price = self._extract_price_from_other_market(self.prior_market_in_series, time_interval)
                    if marginal_price is not None:
                        self.marginal_prices.update(time_interval, marginal_price)
                        continue

                # METHOD #3. If this market corrects another prior market, its cleared price should be used,
                #   e.g., a real-time market that corrects day-ahead market periods.

                if self.market_to_be_refined:
                    marginal_price = self._extract_price_from_other_market(self.market_to_be_refined, time_interval)
                    if marginal_price is not None:
                        self.marginal_prices.update(time_interval, marginal_price)
                        continue

                # METHOD #4. Use the price model to predict marginal price.

                if self.price_model is not None:
                    marginal_price = self.price_model.get(time_interval.start_time)[0]
                    if marginal_price is not None:
                        self.marginal_prices.update(time_interval, marginal_price)
                        continue

                # METHOD 5. If all above methods fail, market periods should be assigned the market's default price.
                if self.series.default_price is not None:
                    self.marginal_prices.update(time_interval, self.series.default_price)
                    continue

                # METHOD 6: If all the above logic fails to assign the marginal price, assign as None.
                self.marginal_prices.update(time_interval, None)

    def refresh_prices_from_prior_market(self) -> dict:
        """
        Re-seed this market's marginal prices from the prior market in the series for the intervals they share.
        check_marginal_prices() runs at construction, which for a rolling series is before the prior market has cleared,
        so the prices it copied were the prior market's own opening prices. Call this when negotiation starts and the
        prior market has cleared. Intervals the prior market does not cover keep their current price. Returns the
        prices in force after the refresh, keyed by interval start time.
        """
        prior = self.prior_market_in_series
        if prior is not None:
            for time_interval in self.time_intervals:
                price = self._extract_price_from_other_market(prior, time_interval)
                if price is not None:
                    self.marginal_prices.update(time_interval, price)
        return {ti.start_time: self.marginal_prices.get_by_interval(ti).value for ti in self.time_intervals
                if self.marginal_prices.get_by_interval(ti) is not None}

    def schedule(self):
        """
        Process called to
        (1) invoke all models to update the scheduling of their resources, loads, or neighbor
        (2) converge to system balance using sub-gradient search.

        """
        # TODO: This method no longer appears to be used, at least in the base market class.
        #  Should it be used or removed?
        # Call each local asset to schedule itself.
        for local_asset in self.tn.local_assets:
            local_asset.schedule(self)

        # Call each neighbor to schedule itself.
        for neighbor in self.tn.neighbors:
            neighbor.schedule(self)

    def sum_vertices(self, time_interval, object_to_exclude=None, enclosing_measurement: Measurement | None = None):
        """
        Create system vertices with system information for a single time interval. An optional argument allows the
        exclusion of a transactive neighbor object, which is useful for transactive records and their corresponding
        demand or supply curves. This utility method should be used for creating transactive signals (by excluding the
        neighbor object), and for visualization tools that review the local system's net supply/demand curve.

        :param: time_interval:
        :param: object_to_exclude:
        :returns: vertices: list of Vertex objects in the given time interval.
        """
        # Build a list of marginal prices mps at which vertices will be created. For each neighbor and asset:
        #    - Where there is one vertex, assign infinite marginal price because quantity is constant.
        #    - Where there are multiple vertices. Use the marginal price values from the vertices themselves.
        marginal_price_list = SortedList()
        for entity in self.tn.neighbors + self.tn.local_assets:
            # Jump out of this iteration if neighbor model happens to be the "object to exclude"
            if object_to_exclude is not None and entity == object_to_exclude:
                continue

            sd_curve = entity.active_vertices.get_by_interval(time_interval)

            if not sd_curve.empty:
                marginal_price_list.update([float("inf")] if len(sd_curve) == 1 else sd_curve.prices)  # [$/kWh]
            else:
                _log.warning(f'No vertices found for {entity.name} in time interval {time_interval.name}.')

        # Keep only two (near) duplicates of any marginal price.
        #   Where there are two instances of a price, offset one by a small number so the SDCurve remains a function.
        for mp in (marginal_price_set := SortedSet(marginal_price_list)):
            if marginal_price_list.count(mp) > 1:
                marginal_price_set.add(mp - 1e-10)

        # An infinite marginal price is meaningful only if there is exactly one vertex (no flexibility). Remove others.
        if len(marginal_price_set) > 1:
            marginal_price_set.discard(float('inf'))

        # A clean set of marginal prices has been created. Create vertices at the marginal prices.
        # TODO: Reevaluate passing vertices=[] here. Without it, SDCurve defaults to a dummy (0,0) vertex.
        #  Is it worth creating an SDCurve with empty vertices, or should we handle the no-prices case earlier?

        # TODO: Added archive=False here to avoid second write when sending transactive signals.
        #  Do other areas of code need to handle archival now?
        summed_curve = SDCurve(enclosing_measurement, time_interval, vertices=[], archive=False)
        # Index through the cleaned list of marginal prices
        for mp in marginal_price_set:
            # Create a vertex at the indexed marginal price value
            vertex = Vertex(mp, 0, 0)

            # Include power and production costs from neighbor models. Index through the active neighbor models.
            for entity in self.tn.neighbors + self.tn.local_assets:
                if entity == object_to_exclude:
                    continue

                # Add the entity's production quantity & cost at this price and interval to those of the vertex.
                entity_sd_curve = entity.active_vertices.get_by_interval(time_interval)
                entity_production = entity.production(mp, time_interval)  # [avg.kW]
                vertex.cost += entity_sd_curve.prod_cost_from_vertices(entity_production)
                vertex.quantity += entity_production  # [avg.kW]

            # Append Vertex iv to the list of vertices
            summed_curve.update(vertex, archive=False)  # TODO: Set archive false to prevent second write on prep_signal. Does this change usage elsewhere?
        return summed_curve

    def update_costs(self):
        """
        Sum the production and dual costs from all modeled local resources, local loads, and neighbors, and then sum
        them for the entire duration of the time horizon being calculated.

        PRESUMPTIONS:
        - Dual costs have been created and updated for all active time intervals for all neighbor objects
        - Production costs have been created and updated for all active time intervals for all asset objects

        OUTPUTS:
        - Updates Market.productionCosts - an array of total production cost in each active time interval
        - Updates Market.totalProductionCost - the sum of production costs for the entire future time horizon of
          active time intervals
        - Updates Market.dualCosts - an array of dual cost for each active time interval
        - Updates Market.totalDualCost - the sum of all the dual costs for the entire future time horizon of active
          time intervals
        """
        # Call each LocalAsset to update its costs.
        for local_asset in self.tn.local_assets:
            local_asset.update_costs(self)

        # Call each Neighbor to update its costs.
        for neighbor in self.tn.neighbors:
            neighbor.update_costs(self)

        for time_interval in self.time_intervals:
            sum_dual_cost = 0.0  # [$]
            sum_production_cost = 0.0  # [$]

            for local_asset in self.tn.local_assets:
                sum_dual_cost += local_asset.dual_costs.get_by_interval(time_interval)  # [$]
                sum_production_cost += local_asset.production_costs.get_by_interval(time_interval)  # [$]

            for neighbor in self.tn.neighbors:
                sum_dual_cost += neighbor.dual_costs.get_by_interval(time_interval)  # [$]
                sum_production_cost += neighbor.production_costs.get_by_interval(time_interval)  # [$]

            # Create or update dual cost and production cost entries for this market in the indexed time interval.
            self.dual_costs.update(time_interval, sum_dual_cost)
            self.production_costs.update(time_interval, sum_production_cost)

        # Sum total dual cost for the entire forward horizon.
        self.total_dual_cost = self.dual_costs.total()  # [$]

        # Sum total primal cost for the entire time horizon
        self.total_production_cost = self.production_costs.total()  # [$]

    def update_supply_demand(self):
        """
        For each time interval, sum the power that is generated, imported, consumed, or exported for all modeled local
        resources, neighbors, and local load.
        """
        for time_interval in self.time_intervals:
            total_generation = 0.0  # [avg.kW]
            total_demand = 0.0  # [avg.kW]

            for local_asset in self.tn.local_assets:
                scheduled_power = local_asset.scheduled_powers.get_by_interval(time_interval)  # [avg.kW]

                if scheduled_power > 0:  # Generation case: Add positive powers to total generation.
                    total_generation += scheduled_power  # [avg.kW]

                else:  # Demand case: Add negative powers to total demand.
                    total_demand += scheduled_power  # [avg.kW]

            for neighbor in self.tn.neighbors:
                scheduled_power = neighbor.scheduled_powers.get_by_interval(time_interval)  # [avg.kW]

                if scheduled_power > 0:  # Generation case: Add positive power to total generation.
                    total_generation += scheduled_power  # [avg.kW]

                else:  # Demand case: Add negative power to total demand.
                    total_demand += scheduled_power  # [avg.kW]

            # At this point, total generation and importation and total demand and exportation have been calculated for
            #   the indexed time interval.
            # NOTE that this formulation includes both consumption and exportation among total load.

            # Save the total generation and demand in the indexed time interval.
            self.total_generation.update(time_interval, total_generation)
            self.total_demand.update(time_interval, total_demand)

            # Update net power for the interval. Net power is the sum of total generation and total load. By convention,
            # generation power is positive and consumption is negative.

            self.net_powers.update(time_interval, total_generation + total_demand)

        _log.debug(f"{self.name} market netPowers are: {self.net_powers.log_report}")

    @property
    def table_name(self):
        return 'market'

    def get_record(self) -> dict:
        record = super(Market, self).get_record()
        record.update({
            'prior_market_id': self.prior_market_in_series.id if self.prior_market_in_series else None,
            'market_to_be_refined_id': self.market_to_be_refined.id if self.market_to_be_refined else None,
            'market_type': self.MARKET_TYPE,
            'active_start_time': self.transition_times.active,
            'negotiation_start_time': self.transition_times.negotiation,
            'market_lead_start_time': self.transition_times.market_lead,
            'market_clearing_time': self.transition_times.delivery_lead,
            'delivery_start_time': self.transition_times.delivery,
            'reconciliation_start_time': self.transition_times.reconciliation,
            'expiration_time': self.transition_times.expiration,
            'market_series_id': self.series.id
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(Market, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "market",
                "inherits": "component",
                "columns": [
                    {"name": "id", "foreign_key": "component.id", "kwargs": {"primary_key": True}},
                    {"name": "prior_market_id", "foreign_key": "market.id", "nullable": True},
                    {"name": "market_to_be_refined_id", "foreign_key": "market.id", "nullable": True},
                    {"name": "market_type", "enumeration": "MarketTypes"},
                    {"name": "active_start_time", "type": "DateTime"},
                    {"name": "negotiation_start_time", "type": "DateTime"},
                    {"name": "market_lead_start_time", "type": "DateTime"},
                    {"name": "market_clearing_time", "type": "DateTime"},
                    {"name": "delivery_start_time", "type": "DateTime"},
                    {"name": "reconciliation_start_time", "type": "DateTime"},
                    {"name": "expiration_time", "type": "DateTime"},
                    {"name": "market_series_id", "foreign_key": "market_series.id"}
                ]
            },
            {
                "name": "market_state_change",
                "columns": [
                    {"name": "market_id", "foreign_key": "market.id", "kwargs": {"primary_key": True}},
                    {"name": "record_time", "type": "DateTime", "kwargs": {"primary_key": True}},
                    {"name": "state", "enumeration": "MarketState"}
                ]
            }
        ])
        return definitions
