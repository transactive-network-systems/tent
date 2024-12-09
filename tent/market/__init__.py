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
import os
import sys

from copy import deepcopy
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Union
from warnings import warn
from weakref import proxy

from tent import Component
from tent.containers import IntervalSeries, IntervalValue, Measurement, TimeInterval, Vertex
from tent.enumerations import MarketState, MarketTypes, MeasurementType, MeasurementUnit, Method
from tent.market.price_models import HourlyAveragePriceModel
from tent.utils.helpers import (find_objs_by_ti, format_timestamp, order_vertices, production,
                                prod_cost_from_vertices, safe_weak_proxy_callback, validate_bool)
from tent.utils.log import setup_logging
from tent.utils.timer import Timer

setup_logging()
_log = logging.getLogger(__name__)


class Market(Component):
    # Market Base Class
    # At least one Market must exist (see the firstMarket object) to drive the timing with which new TimeIntervals are
    # created.

    def __init__(self,
                 transactive_node,
                 market_series=None,
                 activation_lead_time: Union[timedelta, float, int] = timedelta(hours=0),
                 commitment=False,
                 default_price=0.05,
                 delivery_anchor_time=None,
                 delivery_lead_time: Union[timedelta, float, int] = timedelta(hours=0),
                 duality_gap_threshold=0.01,
                 first_market_in_series: bool = False,
                 future_horizon: Union[timedelta, float, int] = timedelta(hours=24),
                 initial_market_state: Union[MarketState, str, int] = MarketState.Inactive,
                 interval_duration: Union[timedelta, float, int] = timedelta(hours=1),
                 intervals_to_clear=1,
                 market_clearing_interval: Union[timedelta, float, int] = timedelta(hours=1),
                 market_clearing_time=None,
                 market_lead_time: Union[timedelta, float, int] = timedelta(hours=0),
                 market_order=1,
                 market_series_name='Market Series',
                 market_to_be_refined=None,
                 market_type: Union[MarketTypes, str, int] = MarketTypes.unknown,
                 method: Union[Method, str, int] = Method.Interpolation,
                 negotiation_lead_time: Union[timedelta, float, int] = timedelta(hours=0),
                 prior_market_in_series=None,
                 real_time_duration: Union[timedelta, float, int] = timedelta(minutes=15)
                 ):

        # TODO: Remove this SHIM once Market Series is correctly integrated in configuration.
        ####### BEGIN SHIM
        from tent.market.market_series import MarketSeries
        self.tn = transactive_node
        market_series = market_series if market_series else MarketSeries(self.tn)
        ####### END SHIM
        self.market_series = proxy(market_series, safe_weak_proxy_callback(self, 'market_series'))

        # 240222DJH: This is a model of how I propose to handle the many time durations of the market state machine:
        #   (1) Assign these simply as protected properties at first. Doing so allows existing methods to work as
        #       before.
        #   (2) All such time durations have been duplicated in new class TimeSeries. Each market object now has a weak
        #       reference to its TimeSeries object.
        #   (3) Manage these properties by having the market return the corresponding TimeSeries protected property
        #       whenever there exists the weak reference, the local protected property otherwise.
        #   (4) In the future we might delete the market classes' local protected time durations, as these will more
        #       appropriately reside with each market's market series.
        self._activation_lead_time = activation_lead_time if isinstance(activation_lead_time, timedelta) else \
            timedelta(seconds=activation_lead_time)  # [timedelta] Time in market state "Active"
        self.commitment = validate_bool(commitment, 'commitment')  # [Boolean] If true, scheduled power & price are
        #                                                            commitments
        self.default_price = float(default_price)  # [$/kWh] Static default price assignment
        # deliveryAnchorTime [time] A time of day a market will always start (start immediately if None)
        self.delivery_anchor_time = time.fromisoformat(delivery_anchor_time) if isinstance(delivery_anchor_time, str)\
            else delivery_anchor_time
        self._delivery_lead_time = delivery_lead_time if isinstance(delivery_lead_time, timedelta)\
            else timedelta(seconds=delivery_lead_time)  # [timedelta] Time in market state "DeliveryLead"
        self.duality_gap_threshold = float(duality_gap_threshold)  # [dimensionless]; 0.01 = 1%
        self.future_horizon = future_horizon if isinstance(future_horizon, timedelta)\
            else timedelta(seconds=future_horizon)  # Future functionality: future of price-discovery relevance
        self._initial_market_state = initial_market_state if isinstance(initial_market_state, MarketState)\
            else MarketState[initial_market_state]  # [MarketState] New market's initial state
        self._interval_duration = interval_duration if isinstance(interval_duration, timedelta)\
            else timedelta(seconds=interval_duration)  # [timedelta] Duration of this market's time intervals
        self._intervals_to_clear = int(intervals_to_clear)  # [int] Market intervals to be cleared by this market object
        self._market_clearing_interval = market_clearing_interval if isinstance(market_clearing_interval, timedelta)\
            else timedelta(seconds=market_clearing_interval)  # [timedelta] Time between successive market clearings
        self._market_lead_time = market_lead_time if isinstance(market_lead_time, timedelta)\
            else timedelta(seconds=market_lead_time)  # [timedelta] Time in market state "MarketLead"
        self.market_order = int(market_order)  # [pos. integer] Ordering of sequential markets  (Unused)
        self._market_series_name = str(market_series_name)  # Successive market series objects share this name root
        # [Market] Pointer to market to be refined or corrected
        if isinstance(market_to_be_refined, Market):
            self.market_to_be_refined = proxy(market_to_be_refined,
                                              safe_weak_proxy_callback(self, 'market_to_be_refined'))
        elif market_to_be_refined:
            self.market_to_be_refined = proxy(self.tn.get_market_by_name(market_to_be_refined),
                                              safe_weak_proxy_callback(self, 'market_to_be_refined'))
        else:
            self.market_to_be_refined = None
        self.market_type = market_type if isinstance(market_type, MarketTypes)\
            else MarketTypes[market_type]  # [MarketTypes] enumeration
        if isinstance(method, Method): # Solution method {1: subgradient, 2: interpolation}
            self.method = method
        elif isinstance(method, int):
            self.method = Method(method)
        else:
            self.method = Method[method]
        self.negotiation_lead_time = negotiation_lead_time if isinstance(negotiation_lead_time, timedelta)\
            else timedelta(seconds=negotiation_lead_time)  # [timedelta] Time in market state "Negotiation"
        if isinstance(prior_market_in_series, Market):
            # [Market] Reference to preceding market in this market series
            self.prior_market_in_series = proxy(prior_market_in_series,
                                                safe_weak_proxy_callback(self, 'prior_market_in_series'))
        elif prior_market_in_series:
            self.prior_market_in_series = proxy(self.tn.get_market_by_name(prior_market_in_series),
                                                safe_weak_proxy_callback(self, 'prior_market_in_series'))
        else:
            self.prior_market_in_series = None
        # 240222DJH: TODO: Depreciate, halt usage of real_time_duration.
        self.real_time_duration = real_time_duration if isinstance(real_time_duration, timedelta)\
            else timedelta(seconds=real_time_duration)
        self.market_clearing_time = market_clearing_time if market_clearing_time else self._calculate_clearing_time()

        # Super is called after initializing variables so that everything has been calculated to get the market name.
        # TODO: This logic should belong to the MarketSeries.
        market_name = self.get_market_name()
        description = self.market_series.description if self.market_series else market_name
        super(Market, self).__init__(description, market_name, transactive_node)

        # These are dynamic properties that are assigned in code and should not be manually configured:
        self.active_vertices = []  # [IntervalValue]; values are [vertices]
        self.blended_prices1 = Measurement(self, MeasurementType.BlendedPrice, MeasurementUnit.Monetary)  # (future functionality)
        self.blended_prices2 = Measurement(self, MeasurementType.BlendedPrice, MeasurementUnit.Monetary)  # (future functionality)
        self.converged = False
        self.dual_costs = Measurement(self, MeasurementType.DualCost, MeasurementUnit.Monetary)  # Values are [$]
        # TODO: Does isNewestMarket need a setting for instantiation of some markets at startup?
        # 240206DJH: ANSWER: This is best handled by configuration of the initial market and its treatment by method
        #            spawn(). The "newest market" flag is held by the market that determines whether new markets must be
        #            spawned, based on comparison of a calculated activation time and the current time.
        #            A corrected assignment of isNewestMarket must always be checked as new markets are spawned.
        self.is_newest_market = True  # [Boolean] Flag held by only the newest market in market series
        self.market_state = self.initial_market_state if self.initial_market_state else MarketState.Initial
        self.marginal_prices = Measurement(self, MeasurementType.MarginalPrice, MeasurementUnit.Monetary) # [$/kWh]
        self.net_powers = Measurement(self, MeasurementType.NetPower, MeasurementUnit.kW)  # [avg.kW]
        self.next_market_clearing_time = self.market_clearing_time + self.market_clearing_interval
        self.price_model: HourlyAveragePriceModel = HourlyAveragePriceModel(forgetting_factor=14,
                                                                            default_price=self.default_price)
        self.production_costs = Measurement(self, MeasurementType.ProductionCost, MeasurementUnit.Monetary)  # [$]
        self.reconciled = False  # [Boolean] Convergence flag
        self.time_intervals = IntervalSeries(self,  # [TimeInterval] Current list of active time intervals
                                             first_start_time=self.market_clearing_time + self.delivery_lead_time,
                                             duration=self.interval_duration,
                                             number_of_intervals=self.intervals_to_clear)
        self.total_demand = Measurement(self, MeasurementType.TotalDemand, MeasurementUnit.kW)  # [avg.kW]
        self.total_dual_cost = 0.0  # [$]
        self.total_generation = Measurement(self, MeasurementType.TotalGeneration, MeasurementUnit.kW)  # [avg.kW]
        self.total_production_cost = 0.0  # [$]

        # Set up market
        self.check_marginal_prices()
        for neighbor in self.tn.neighbors:
            neighbor.schedule(self)

        # This flag should be made False in each transition.
        # It should be made True after all the tasks have been completed in the ensuing market state.
        self._stateIsCompleted = False

    @property
    def activation_lead_time(self):
        return self.market_series.activation_lead_time if self.market_series else self._activation_lead_time

    @property
    def delivery_lead_time(self):
        return self.market_series.delivery_lead_time if self.market_series else self._delivery_lead_time

    @property
    def interval_duration(self):
        return self.market_series.interval_duration if self.market_series else self._interval_duration

    @property
    def intervals_to_clear(self):
        return self.market_series.intervals_to_clear if self.market_series else self._intervals_to_clear

    @property
    def market_clearing_interval(self):
        return self.market_series.market_clearing_interval if self.market_series else self._market_clearing_interval

    @property
    def market_lead_time(self):
        return self.market_series.market_lead_time if self.market_series else self._market_lead_time

    @property
    def initial_market_state(self):
        return self.market_series.initial_market_state if self.market_series else self._initial_market_state

    @property
    def market_series_name(self):  # 240222DJH: I chose not to protect this at class MarketState.
        return self.market_series.market_series_name if self.market_series else self._market_series_name

    def _calculate_clearing_time(self):
        # 240221DJH: TODO: Move this method to new market series class.
        # 240221DJH: TODO: Ensure that this base method begins delivery on major temporal transitions.
        now = Timer.get_cur_time()
        run_gap = (self.delivery_lead_time + self.market_lead_time + self.negotiation_lead_time
                   + self.activation_lead_time)
        if not self.delivery_anchor_time:
            delivery_start = now + run_gap
        else:
            delivery_anchor_today = datetime.combine(now.date(), self.delivery_anchor_time)
            delivery_start = now + ((delivery_anchor_today - now - run_gap) % self.market_clearing_interval)
        return delivery_start - self.delivery_lead_time

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

        # EVENT 0: % FIRST INSTANTIATION OF A MARKET SERIES ************************************************************
        # First time setup of a new market series.
        if self.market_state == MarketState.Initial:
            self.transition_from_initial_to_inactive()
            self.market_state = MarketState.Inactive

            # 210118DJH: New flag. Set false upon transition to new market state.
            self._stateIsCompleted = False

        # EVENT 1A: % A NEW MARKET OBJECT BECOMES ACTIVE ***************************************************************
        # This is the instantiation of a market object in market state "Active." This transition occurs at a time when
        # a new market object is needed, as specified relative to its market clearing time. Specifically, a new market
        # object is instantiated in its "Inactive" state a specified negotiation lead time and market lead time prior to
        # the market's clearing time. Note that this logic seems a little backward because the state's start must be
        # determined *before* the needed market object exists.

        # 191212DJH: This logic is simplified greatly by the introduction of flag isNewestMarket. The potential need for
        # new market objects is triggered only by the newest market in any series.

        if self.is_newest_market is True:
            future_clearing_time = current_time + self.activation_lead_time \
                                   + self.negotiation_lead_time + self.market_lead_time
            if self.next_market_clearing_time < future_clearing_time:
                self.spawn_markets(self.next_market_clearing_time)
                self.is_newest_market = False
                # # 210118DJH: New flag. Set true when responsibilities in market state are completed.
                # self._stateIsCompleted = True

        # EVENT 1B: TRANSITION FROM INACTIVE TO ACTIVE STATE ***********************************************************
        # 210118DJH: Adding flag. No state transition should be allowed unless the prior state is done.
        if self.market_state == MarketState.Inactive:  # and self._stateIsCompleted is True:

            activation_start_time = self.market_clearing_time - self.market_lead_time - self.negotiation_lead_time \
                                    - self.activation_lead_time

            if current_time >= activation_start_time:
                # Change the market state to "Active."
                self.market_state = MarketState.Active
                _log.debug(f'MARKET: {self.name} STATE IS NOW: MarketState.Active')

                # Call this replaceable method where appropriate actions can be taken.
                self.transition_from_inactive_to_active()

                # 210118DJH: New flag. Set false upon transition to new market state.
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

        # EVENT 2A: TRANSITION FROM ACTIVE TO NEGOTIATION STATE ********************************************************
        # This is the transition from "Active" to "Negotiation" market states. Market state "Negotiation" begins at a
        # time specified before an upcoming market clearing of a market object. Specifically, it begins a specified
        # market lead time, less another negotiation lead time, prior to the clearing of the market object.

        # 210118DJH. New flag. Do not transition out of a state until it is completed.
        if self.market_state == MarketState.Active and self._stateIsCompleted is True:

            negotiation_start_time = self.market_clearing_time - self.market_lead_time - self.negotiation_lead_time

            if current_time >= negotiation_start_time:
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

        # EVENT 3A: TRANSITION FROM NEGOTIATION TO MARKET LEAD STATE ***************************************************
        # This is the transition from "Negotiation" to "MarketLead" market states.
        # The transition occurs at a time relative to the market object's market clearing time. Specifically, it starts
        # a defined lead time prior to the market clearing time.
        # 210118DJH: Adding flag. No state transition should be allowed unless the prior state is completed.
        if self.market_state == MarketState.Negotiation and self._stateIsCompleted is True:

            market_lead_start_time = self.market_clearing_time - self.market_lead_time

            if current_time >= market_lead_start_time:
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

        # EVENT 4A: TRANSITION FROM MARKET LEAD TO DELIVERY LEAD STATE *************************************************
        # This is the transition from "MarketLead" to "DeliveryLead" market states.
        # 210118DJH: Adding flag. No state transition should be allowed unless the prior state is done.
        if self.market_state == MarketState.MarketLead and self._stateIsCompleted is True:

            # This transition is simply the market clearing time.
            delivery_lead_start_time = self.market_clearing_time

            if current_time >= delivery_lead_start_time:
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

        # EVENT 5A: TRANSITION FROM DELIVERY LEAD TO DELIVERY **********************************************************
        # This is the transition from "DeliveryLead" to "Delivery" market states. The start of market state "Delivery"
        # is timed relative to the market object's market clearing time. Specifically, it begins a delivery lead time
        # after the market has cleared.
        # 210118DJH: Adding flag. No state transition should be allowed unless the prior state is done.
        if self.market_state == MarketState.DeliveryLead and self._stateIsCompleted is True:

            delivery_start_time = self.market_clearing_time + self.delivery_lead_time

            if current_time >= delivery_start_time:
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

        # EVENT 6A: TRANSITION FROM DELIVERY TO RECONCILE **************************************************************
        # This is the transition from "Delivery" to "Reconcile" market states. The Reconcile market state begins at a
        # time referenced from the market object's clearing time. Specifically, reconciliation begins after all the
        # market object's market intervals and an additional delivery lead time have expired after the market clears.
        # 210118DJH: Adding flag. No state transition should be allowed unless the prior state is completed.
        if self.market_state == MarketState.Delivery and self._stateIsCompleted is True:

            reconcile_start_time = self.market_clearing_time + self.delivery_lead_time \
                                   + self.intervals_to_clear * self.interval_duration

            if current_time >= reconcile_start_time:

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

            # Remove the expired market.
            self.tn.markets[:] = [x for x in self.tn.markets if x != self]

            # NOTE: We let garbage collection finally delete the object once it is entirely out of scope.

    def spawn_markets(self, new_market_clearing_time):
        """
        This method is called when a test determines that a new market object may be needed. The base method creates the
        new market object, as will be normal for systems having only one market. This method must be replaced or
        extended if
        (1) Still more markets should be instantiated, as may happen when market self is refined or corrected by another
        market series. If this is the case, not only the next needed market in this series, but also one or more markets
        in another market series must be instantiated.
        (2) The markets in this series are instantiated by another market series. In this case, this method should be
        replaced by a pass (no action).

        :param: new_market_clearing_time: new market objects market clearing time
        :return: None
        """
        _log.debug(f'{self.name} IS IN SPAWN_MARKETS')
        # A new market object must be instantiated. Many of its properties are the same as that of the called market
        # or can be derived therefrom. The new market should typically be initialized from the same class as the calling
        # market (i.e., self).
        new_market = self.__class__(
            activation_lead_time=self.activation_lead_time,
            commitment=self.commitment,
            default_price=self.default_price,
            delivery_lead_time=self.delivery_lead_time,
            future_horizon=self.future_horizon,
            initial_market_state=self.initial_market_state,
            interval_duration=self.interval_duration,
            intervals_to_clear=self.intervals_to_clear,
            market_clearing_interval=self.market_clearing_interval,
            market_clearing_time=new_market_clearing_time,
            market_lead_time=self.market_lead_time,
            market_order=self.market_order,
            market_series_name=self.market_series_name,  # This often points to self.marketSeries.market_series_name
            market_to_be_refined=self.market_to_be_refined,
            method=self.method,
            negotiation_lead_time=self.negotiation_lead_time,
            prior_market_in_series=self,
            market_series=self.market_series,  # 240228DJH: new weak reference to the market's market series object.
            transactive_node=self.tn
        )
        new_market.market_type = self.market_type if isinstance(self.market_type, MarketTypes) else MarketTypes.unknown

        new_market.price_model = deepcopy(self.price_model)  # It must be clear that this is a copy, not a reference.

        if new_market.initial_market_state:
            new_market.market_state = new_market.initial_market_state
        else:
            new_market.market_state = MarketState.Initial

        # Append the new market object to the list of market objects that is maintained by the agent.
        self.tn.markets.append(new_market)

        # 240228DJH: If this market references its MarketSeries object, then also place the market into its list of
        #            active markets.
        if new_market.market_series and new_market.market_series():
            new_market.market_series().activeMarkets.append(new_market)

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
            self.balance(self.tn)  # A consensus method conducts negotiations while in negotiation state.

        else:
            pass
            # 210118DJH: NOTE: Flag "_stateIsComplete" must be set true after the market's responsibilities have been
            #            completed in this next method or its replacements.
            self._stateIsCompleted = True

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
        self.balanced_price_publish()
        return None

    def balanced_price_publish(self):
        scheduled_powers = defaultdict(dict)
        for entity in self.tn.neighbors + self.tn.local_assets:
            for time_interval, scheduled_power in entity.scheduled_powers.all:
                if time_interval.market is self:
                    scheduled_powers[format_timestamp(time_interval.start_time)][entity.name] = scheduled_power.value
        self.tn.publish(
            'balanced_price_record', {
                'tnt_market_name': self.name,
                'balanced_prices': {format_timestamp(k.start_time): v.value for k, v in self.marginal_prices.all},
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
        # Save market object data:
        # TODO: This report should be optional or handled by a query to the database at a later time.
        #  We can't create files for every market object on a production system.
        # Create a table for market object data.
        data = []
        # Append data for active market vertices:
        for x in range(len(self.active_vertices)):
            data.append([self.name,
                         self.active_vertices[x].time_interval.name,
                         self.active_vertices[x].value.marginal_price,
                         self.active_vertices[x].value.power])

        # Append data for local assets:
        for local_asset in self.tn.local_assets:
            vertices = [y for y in local_asset.active_vertices
                        if y.time_interval.market == self]

            for vertex in vertices:
                datum = [local_asset.name,
                         vertex.time_interval.name,
                         vertex.value.marginal_price,
                         vertex.value.power]
                data.append(datum)

        # Append data for neighbor data:
        for neighbor in self.tn.neighbors:
            vertices = [y for y in neighbor.active_vertices
                        if y.time_interval.market == self]

            for vertex in vertices:
                datum = [neighbor.name,
                         vertex.time_interval.name,
                         vertex.value.marginal_price,
                         vertex.value.power]
                data.append(datum)

        # Write the vertex data into a csv file based on the current working directory.

        filename = self.market_series_name + ".csv"
        data_folder = os.getcwd()
        data_folder = data_folder + "\\.."
        data_folder = data_folder + "\\Market_Data\\"
        full_filename = data_folder + filename

        import csv

        my_file = open(full_filename, 'w+')
        with my_file:
            writer = csv.writer(my_file)
            writer.writerows(data)

        # Gather simpler marginal price data:
        price_data = []

        for time_interval, marginal_price in self.marginal_prices.all:  #
            price_data.append([self.name, time_interval.start_time, marginal_price.value])

        filename = self.name.replace(':', '_') + ".csv" if sys.platform == 'win32' else self.name + '.csv'
        full_filename = data_folder + filename

        my_file = open(full_filename, 'w+')
        with my_file:
            writer = csv.writer(my_file)
            writer.writerows(price_data)
        return None

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
            # Find and delete existing aggregate active vertices in the indexed time interval. They will be recreated.
            self.active_vertices = [x for x in self.active_vertices
                                    if x.time_interval.start_time != time_interval.start_time]

            # Call the utility sum_vertices to recreate the aggregate vertices in the indexed time interval. (This
            # method is separated out because it will be used by other methods.)
            summed_vertices = self.sum_vertices(self.tn, time_interval)

            # Create and store an interval value for each vertex.
            for vertex in summed_vertices:
                self.active_vertices.append(IntervalValue(calling_object=self,
                                                          time_interval=time_interval,
                                                          market=self,
                                                          measurement_type=MeasurementType.SystemVertex,
                                                          value=vertex
                                                          )
                                            )

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
        if self.method == Method.Interpolation:
            self.assign_system_vertices()
            av = [(x.time_interval.name, x.value.marginal_price, x.value.power) for x in self.active_vertices]
            _log.debug("{} market active vertices are: {}".format(self.name, av))

        # Index through active time intervals.
        for time_interval in self.time_intervals:
            # Find the marginal price interval value for the corresponding indexed time interval.
            marginal_price = self.marginal_prices.get_by_interval(time_interval)

            # Extract its marginal price value as a trial clearing price (that may be replaced).
            cleared_marginal_price = self.default_price if marginal_price is None else marginal_price.value  # [$/kWh]

            if self.method == Method.Subgradient:
                # Find the net power corresponding to the indexed time interval.
                net_power = self.net_powers.get_by_interval(time_interval)
                total_generation = self.total_generation.get_by_interval(time_interval)
                total_demand = self.total_demand.get_by_interval(time_interval)

                net_power = net_power.value / (total_generation.value - total_demand.value)

                # Update the marginal price using sub-gradient search.
                cleared_marginal_price = cleared_marginal_price - (net_power * 1e-1) / (10 + k)  # [$/kWh]

            elif self.method == Method.Interpolation:
                # Get the indexed active system vertices.
                active_vertices = [x.value for x in self.active_vertices
                                   if x.time_interval.start_time == time_interval.start_time]

                # Order the system vertices in the indexed time interval by price and power.
                active_vertices = order_vertices(active_vertices)

                err_msg = ''
                try:
                    # Find the vertex that bookcases the balance point from the lower side.
                    lower_active_vertex = [x for x in active_vertices if x.power < 0]
                    if len(lower_active_vertex) == 0:
                        _log.warning(f'No load demand cases were found in {time_interval.name}')
                        err_msg = f"At {time_interval.name}, there is no point having power < 0"
                    else:
                        lower_active_vertex = lower_active_vertex[-1]

                    # Find the vertex that bookcases the balance point from the upper side.
                    upper_active_vertex = [x for x in active_vertices if x.power >= 0]
                    if len(upper_active_vertex) == 0:
                        _log.warning(f'No supply power cases were found in {time_interval.name}')
                        err_msg = f"At {time_interval.name}, there is no point having power >= 0 "
                    else:
                        upper_active_vertex = upper_active_vertex[0]

                    # Interpolate the marginal price in the interval using a principle of similar triangles.
                    power_range = upper_active_vertex.power - lower_active_vertex.power
                    marginal_price_range = upper_active_vertex.marginalPrice - lower_active_vertex.marginalPrice
                    if power_range == 0:
                        _log.warning(f'There is no power range to interpolate.'
                                     f' Marginal price is not unique in {time_interval.name}')
                        err_msg = f"At {time_interval.name}, power range is 0"
                    cleared_marginal_price = - marginal_price_range * lower_active_vertex.power / power_range \
                                             + lower_active_vertex.marginalPrice
                    # TODO: Consider adding a feature to find each asset's and neighbor's cleared power at this point.
                    #       This would require interpolation of the cleared average power, and the implication must be
                    #       interpreted for each asset and neighbor.

                except RuntimeWarning as w:
                    _log.warning(f'No balance point was found in {time_interval.name}: {w}')
                    _log.error(err_msg)

                    self.converged = False
                    return
            # Regardless of the method used, assign the cleared marginal price to the marginal price value for the
            # indexed active time interval.
            self.marginal_prices.update(time_interval, cleared_marginal_price)

    # def old_balance(self, mtn):
    #     """
    #     Balance current market
    #     :param mtn: my transactive node object
    #     :return:
    #     """
    #     # TODO: Consider having the balance method solve for both marginal price AND quantity. The quantity is unique to
    #     #  the local assets and neighbors. This improvement would facilitate current convergence issues that occur when
    #     #  cost functions are linear, not quadratic. Partial quantities are allowed even though quantity is not a proper
    #     #  function of marginal price. Note that this introduces new issues for assets that cannot be throttled down.
    #     self.new_data_signal = False
    #
    #     # Check and update the time intervals at the beginning of the process. This should not need to be repeated in
    #     # process iterations.
    #     # TODO: This check should be done within the market state machine, not here. Typically, it would be done once
    #     #  for each clearing to check that market intervals are proper.
    #     self.check_intervals()
    #
    #     # TODO: This check should be done within the market state machine, not here. Typically, it would be done once
    #     #  for each clearing to check that marginal prices exist for the market's forward time intervals.
    #     # Clean up or initialize marginal prices. This should not be repeated in process iterations.
    #     self.check_marginal_prices(mtn)
    #
    #     # TODO: Move this to the market state machine. Iterations to convergence, if any, should be moved to the state
    #     #  machine methods.
    #     # Set a flag to indicate an unconverged condition.
    #     self.converged = False
    #
    #     # Iterate to convergence. "Convergence" here refers to the status of the local convergence of (1) local supply
    #     # and demand and (2) dual costs. This local convergence says nothing about the additional convergence between
    #     # transactive neighbors and their calculations.
    #
    #     # TODO: Move iteration of the market balancing process to the market state machine, not here. Not all markets
    #     #  iterate. A typical auction state machine will invoke one balancing operation. Others must iterate.
    #     # Initialize the iteration counter k
    #     k = 1
    #
    #     while not self.converged and k < 100:
    #         # 200205DJH: I'm not sure of this logic that was introduced by new_data_signal. I think it might be unique
    #         # to the PNNL building model and should no longer be needed.
    #         # TODO: Clean up logic introduced by new_data_signal.
    #         # if self.new_data_signal:
    #         #     self.converged = False
    #         #     return
    #
    #         # Invite all neighbors and local assets to schedule themselves based on current marginal prices
    #         self.schedule(mtn)
    #
    #         # Update the primal and dual costs for each time interval and altogether for the entire time horizon.
    #         self.update_costs(mtn)
    #
    #         # Update the total supply and demand powers for each time interval. These sums are needed for the
    #         # sub-gradient search and for the calculation of blended price.
    #         self.update_supply_demand(mtn)
    #
    #         # Check duality gap for convergence.
    #         # Calculate the duality gap, defined here as the relative difference between total production and dual
    #         # costs.
    #         if self.total_production_cost == 0:
    #             dg = float("inf")
    #         else:
    #             dg = self.total_production_cost - self.total_dual_cost  # [$]
    #             dg = dg / self.total_production_cost  # [dimensionless. 0.01 is 1#]
    #
    #         # Display the iteration counter and duality gap. This may be commented out once we have confidence in the
    #         # convergence of the iterations.
    #         """
    #         _log.debug("Market balance iteration %i: (tpc: %f, tdc: %f, dg: %f)" %
    #                    (k, self.totalProductionCost, self.totalDualCost, dg))
    #         """
    #
    #         # Check convergence condition
    #         if abs(dg) <= self.duality_gap_threshold:  # Converged
    #             # 1.3.1 System has converged to an acceptable balance.
    #             self.converged = True
    #
    #         # System is not converged. Iterate. The next code in this method revised the marginal prices in active
    #         # intervals to drive the system toward balance and convergence.
    #
    #         # A parameter is used to determine how the computational agent searches for marginal prices.
    #         #
    #         # Method 1: Subgradient Search - This is the most general solution technique to be used on
    #         #           non-differentiable solution spaces. It uses the difference between primal costs (mostly
    #         #           production costs, in this case) and dual costs (which are modified using gross profit or
    #         #           consumer cost) to estimate the magnitude of power imbalance in each active time interval. Under
    #         #           certain conditions, a solution is guaranteed. Many iterations may be needed. The method can be
    #         #           fooled, so I've found, by interim oscillatory solutions. This method may fail when large assets
    #         #           have linear, not quadratic, cost functions.
    #         #
    #         # Method 2: Interpolation - If certain requirements are met, the solution might be greatly accelerated by
    #         #           interpolating between the inflection points of the net power curve.
    #         #           Requirements:
    #         #           1. All Neighbors and LocalAssets are represented by linear or quadratic cost functions, thus
    #         #              ensuring that the net power curve is perfectly linear between its inflection points.
    #         #           2: All Neighbors and Assets update their active vertices in a way that represents their
    #         #              residual flexibility, which can be none, thus ensuring a meaningful connection between
    #         #              balancing in time intervals and scheduling of the individual Neighbors and LocalAssets.
    #         #              This method might fail when many assets do complex scheduling of their flexibility.
    #
    #         if self.method == 2:
    #             self.assign_system_vertices(mtn)
    #             # av = [(x.timeInterval.name, x.value.marginalPrice, x.value.power) for x in self.activeVertices]
    #             # _log.debug("{} market active vertices are: {}".format(self.name, av))
    #
    #         # Index through active time intervals.
    #         for ti in self.time_intervals:
    #             # Find the marginal price interval value for the corresponding indexed time interval.
    #             mp = self.marginal_prices.get_by_interval(ti)
    #
    #             # Extract its  marginal price value.
    #             x_lambda = mp.value  # [$/kWh]
    #
    #             if self.method == 1:
    #                 # Find the net power corresponding to the indexed time interval.
    #                 np = self.net_powers.get_by_interval(ti)
    #                 tg = self.total_generation.get_by_interval(ti)
    #                 td = self.total_demand.get_by_interval(ti)
    #
    #                 np = np.value / (tg.value - td.value)
    #
    #                 # Update the marginal price using subgradient search.
    #                 x_lambda = x_lambda - (np * 1e-1) / (10 + k)  # [$/kWh]
    #
    #             elif self.method == 2:
    #                 # Get the indexed active system vertices
    #                 av = [x.value for x in self.active_vertices if x.timeInterval.start_time == ti.start_time]
    #                 err_msg = ''
    #
    #                 # Order the system vertices in the indexed time interval
    #                 av = order_vertices(av)
    #
    #                 try:
    #                     # Find the vertex that bookcases the balance point from the lower side.
    #                     # Fix a case where all intersection points are on X-axis by using < instead of <=
    #                     lower_av = [x for x in av if x.power < 0]
    #                     if len(lower_av) == 0:
    #                         err_msg = "At {}, there is no point having power < 0".format(ti.name)
    #                     else:
    #                         lower_av = lower_av[-1]
    #
    #                     # Find the vertex that bookcases the balance point from the upper side.
    #                     upper_av = [x for x in av if x.power >= 0]
    #                     if len(upper_av) == 0:
    #                         err_msg = "At {}, there is no point having power >= 0".format(ti.name)
    #                     else:
    #                         upper_av = upper_av[0]
    #
    #                     # Interpolate the marginal price in the interval using a principle of similar triangles.
    #                     power_range = upper_av.power - lower_av.power
    #                     mp_range = upper_av.marginalPrice - lower_av.marginalPrice
    #                     if power_range == 0:
    #                         err_msg = "At {}, power range is 0".format(ti.name)
    #                     x_lambda = - mp_range * lower_av.power / power_range + lower_av.marginalPrice
    #                 except:
    #                     _log.warning(err_msg)
    #                     _log.warning(f"{mtn.name} failed to find balance point. "
    #                                  f"Market active vertices: {[(ti.name, x.marginalPrice, x.power) for x in av]}")
    #                     self.converged = False
    #                     return
    #
    #             # Regardless of the method used, variable "x_lambda" should now hold the updated marginal price. Assign it
    #             # to the marginal price value for the indexed active time interval.
    #             mp.value = x_lambda  # [$/kWh]
    #
    #         # Increment the iteration counter.
    #         k = k + 1
    #         if k == 100:
    #             self.converged = True
    #
    #         if self.new_data_signal:
    #             self.converged = False
    #             return

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
            warn('Production costs have not been calculated.')
            #            _log.warning('Primal costs have not yet been calculated.')
            return

        for time_interval in self.time_intervals:
            # Calculate a blended price for this market time interval.
            production_cost = self.production_costs.get_by_interval(time_interval)  # [$]
            total_generation = self.total_generation.get_by_interval(time_interval)  # [kWh]
            blended_price = production_cost / total_generation  # [$/kWh]

            # Create or update blended price entry for this time interval.
            self.blended_prices1.update(time_interval, blended_price)

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
           populates list of active marginal prices (see class IntervalValue)
        """

        for time_interval in self.time_intervals:

            # METHOD #1: If the market interval already has a price, you're done. Otherwise, try other methods.
            if not self.marginal_prices.get_by_interval(time_interval):

                # METHOD #2. If the same time interval exists from the prior market clearing, its price may be used.
                #   This can be the case where if more than one time interval is cleared by each market and
                #   similar successive markets' delivery periods overlap, e.g., a rolling window of 24 hours.

                if self.prior_market_in_series and self.intervals_to_clear > 1:
                    marginal_price = None
                    for prior_interval, refined_marginal_price in self.prior_market_in_series.marginal_prices.all:
                        # If the currently indexed time interval lies within the prior market's interval,
                        #   capture its value for the new marginal price in this time interval.
                        prior_interval_end_time = prior_interval.start_time + prior_interval.duration
                        if prior_interval.start_time <= time_interval.start_time < prior_interval_end_time:
                            marginal_price = refined_marginal_price.value

                    if marginal_price is not None:
                        self.marginal_prices.update(time_interval, marginal_price)
                        continue

                # METHOD #3. If this market corrects another prior market, its cleared price should be used,
                #   e.g., a real-time market that corrects day-ahead market periods.

                if self.market_to_be_refined:
                    marginal_price = None
                    for refined_interval, refined_marginal_price in self.market_to_be_refined.marginal_prices.all:
                        # If the currently indexed time interval lies within the refined market's interval,
                        #   capture its value as the marginal price in this time interval.
                        refined_interval_end_time = refined_interval.start_time + refined_interval.duration
                        if refined_interval.start_time <= time_interval.start_time < refined_interval_end_time:
                            marginal_price = refined_marginal_price.value

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
                if self.default_price is not None:
                    self.marginal_prices.update(time_interval, self.default_price)
                    continue

                # METHOD 6: If all the above logic fails to assign the marginal price, assign as None.
                self.marginal_prices.update(time_interval, None)

    def schedule(self):
        """
        Process called to
        (1) invoke all models to update the scheduling of their resources, loads, or neighbor
        (2) converge to system balance using sub-gradient search.

        """
        # Call each local asset to schedule itself.
        for local_asset in self.tn.local_assets:
            local_asset.schedule(self)

        # Call each neighbor to schedule itself.
        for neighbor in self.tn.neighbors:
            neighbor.schedule(self)

    def sum_vertices(self, time_interval, object_to_exclude=None):
        """
        Create system vertices with system information for a single time interval. An optional argument allows the
        exclusion of a transactive neighbor object, which is useful for transactive records and their corresponding
        demand or supply curves. This utility method should be used for creating transactive signals (by excluding the
        neighbor object), and for visualization tools that review the local system's net supply/demand curve.

        :param: time_interval:
        :param: object_to_exclude:
        :returns: vertices: list of Vertex objects in the given time interval.
        """
        # Initialize a list of marginal prices mps at which vertices will be created.
        marginal_price_list = []
        for neighbor in self.tn.neighbors:

            # Jump out of this iteration if neighbor model happens to be the "object to exclude"
            if object_to_exclude is not None and neighbor == object_to_exclude:
                continue

            # Find the neighbor model's active vertices in this time interval
            interval_values = find_objs_by_ti(neighbor.active_vertices, time_interval)  #

            if len(interval_values) > 0:
                # At least one active vertex was found in the time interval. Extract the vertices from the interval
                # values.
                vertices = [x.value for x in interval_values]  #

                if len(vertices) == 1:
                    # There is one vertex. This means the power is constant for this neighbor. Enforce the policy of
                    # assigning infinite marginal price to constant vertices.
                    marginal_prices = [float("inf")]  # [$/kWh]

                else:
                    # There are multiple vertices. Use the marginal price values from the vertices themselves.
                    marginal_prices = [x.marginal_price for x in vertices]  # [$/kWh]

                marginal_price_list.extend(marginal_prices)  # marginal prices [$/kWh]

        for asset in self.tn.local_assets:

            # Jump out of this iteration if local asset model nm happens to be
            # the "object to exclude"
            if object_to_exclude is not None and asset == object_to_exclude:
                continue

            # Find the local asset model's active vertices in this time interval.
            interval_values = find_objs_by_ti(asset.active_vertices, time_interval)

            if len(interval_values) > 0:
                # At least one active vertex was found in the time interval. Extract the vertices from the interval
                # values.
                vertices = [x.value for x in interval_values]  #
                # Extract the marginal prices from the vertices.
                if len(vertices) == 1:
                    # There is one vertex. This means the power is constant for this local asset. Enforce the policy of
                    # assigning infinite marginal price to constant vertices.
                    marginal_prices = [float("inf")]  # [$/kWh]

                else:
                    # There are multiple vertices. Use the marginal price values from the vertices themselves.
                    marginal_prices = [x.marginal_price for x in vertices]  # marginal prices [$/kWh]

                marginal_price_list.extend(marginal_prices)  # [$/kWh]

        # A list of vertex marginal prices have been created.
        # Sort the marginal prices from least to greatest.
        marginal_price_list.sort()  # [$/kWh]
        # Ensure that no more than two vertices will be created at the same marginal price. The third output of function
        # unique() is useful here because it is the index of unique entries in the original vector.
        # [~, ~, ind] = unique(mps)  # index of unique vector contents

        # Create a new vector of marginal prices. The first two entries are accepted because they cannot violate the
        # two-duplicates rule. The vector is padded with zeros, which should be computationally efficient. A counter is
        # used and should be incremented with new vector entries.
        if len(marginal_price_list) >= 3:
            marginal_price_list_new = [marginal_price_list[0], marginal_price_list[1]]
        else:
            marginal_price_list_new = list(marginal_price_list)

        # Index through the indices and append the new list only when there are fewer than three duplicates.
        for i in range(2, len(marginal_price_list)):
            if marginal_price_list[i] != marginal_price_list[i - 1] \
                    or marginal_price_list[i - 1] != marginal_price_list[i - 2]:
                marginal_price_list_new.append(marginal_price_list[i])

        # 180907DJH: THIS CONDITIONAL (COMMENTED OUT) WAS NOT QUITE RIGHT. A MARGINAL PRICE AT INFINITY IS MEANINGFUL
        #             ONLY IF THERE IS EXACTLY ONE VERTEX-NO FLEXIBILITY. OTHERWISE, IT IS SUPERFLUOUS AND SHOULD BE
        #             ELIMINATED. THIS MUCH SIMPLER APPROACH ENSURES THAT INFINITY IS RETAINED ONLY IF THERE IS A SINGLE
        #             MARGINAL PRICE. OTHERWISE, INFINITY MARGINAL PRICES ARE TRIMMED FROM THE SET.
        marginal_price_list = [marginal_price_list_new[0]]
        for i in range(1, len(marginal_price_list_new)):
            if marginal_price_list_new[i] != float('inf'):
                marginal_price_list.append(marginal_price_list_new[i])

        # A clean list of marginal prices has been created
        # Correct assignment of vertex power requires a small offset of any duplicate values. Index through the new list
        # of marginal prices again.
        for i in range(1, len(marginal_price_list)):
            if marginal_price_list[i] == marginal_price_list[i - 1]:
                # A duplicate has been found. Offset the first of the two by a very small number.
                marginal_price_list[i - 1] = marginal_price_list[i - 1] - 1e-10  # [$/kWh]

        # Create vertices at the marginal prices. Initialize the list of vertices.
        vertices = []
        # Index through the cleaned list of marginal prices
        for i in range(len(marginal_price_list)):
            # Create a vertex at the indexed marginal price value
            interval_value = Vertex(marginal_price_list[i], 0, 0)

            # Initialize the net power pwr and total production cost pc at the indexed vertex
            vertex_power = 0.0  # [avg.kW]
            vertex_production_cost = 0.0  # [$]

            # Include power and production costs from neighbor models. Index through the active neighbor models.
            for neighbor in self.tn.neighbors:
                if neighbor == object_to_exclude:
                    continue

                # Calculate the indexed neighbor model's power at the indexed marginal price and time interval. NOTE:
                # This must not corrupt the "scheduled power" at the converged system's marginal price.
                neighbors_power = production(neighbor, marginal_price_list[i], time_interval)  # [avg.kW]

                # Calculate the neighbor model's production cost at the indexed marginal price and time interval, and
                # add it to the sum production cost pc. NOTE: This must not corrupt the "scheduled" production cost for
                # this neighbor model.
                vertex_production_cost = vertex_production_cost \
                                         + prod_cost_from_vertices(neighbor, time_interval, neighbors_power)

                # Add the neighbor model's power to the sum net power at this vertex.
                vertex_power = vertex_power + neighbors_power  # [avg.kW]

            # Include power and production costs from local asset models. Index through the local asset models.
            for asset in self.tn.local_assets:
                if asset == object_to_exclude:
                    continue

                # Calculate the power for the indexed local asset model at the indexed marginal price and time interval.
                assets_power = production(asset, marginal_price_list[i], time_interval)  # [avg.kW]

                # Find the indexed local asset model's production cost and add it to the sum of production cost pc for
                # this vertex.
                vertex_production_cost = vertex_production_cost \
                                         + prod_cost_from_vertices(asset, time_interval, assets_power)  # [$]

                # Add local asset power p to the sum net power pwr for this vertex.
                vertex_power = vertex_power + assets_power  # [avg.kW]

            # Save the sum production cost pc into the new vertex iv
            interval_value.cost = vertex_production_cost  # [$]

            # Save the net power pwr into the new vertex iv
            interval_value.power = vertex_power  # [avg.kW]

            # Append Vertex iv to the list of vertices
            vertices.append(interval_value)

        return vertices

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

    # def view_net_vertices(self):
    #     """
    #     If within an operating system that supports graphical data representations, this method plots a market's active
    #     vertices.
    #     """
    #     # TODO: This needs to be reviewed. This should plot vertex power as a function of vertex price for a single
    #     #       market time interval. I don't see that such a plot can be had from this method. Methods in the base
    #     #       Transactive Network Template should be independent of platform. This depends on Volttron.
    #     # 200211DJH: See method see_net_curve that provides a superior representation.
    #     time_intervals = self.timeIntervals
    #
    #     def by_start_times(time_interval_list):
    #         return time_interval_list.start_time
    #
    #     time_intervals.sort(key=by_start_times)  # 200207DJH: Is this actually used by the method??
    #
    #     import plotly_express as px
    #
    #     import pandas as pd
    #     df = pd.read_csv('https://raw.githubusercontent.com/plotly/datasets/master/finance-charts-apple.csv')
    #
    #     fig = px.line(df, x='Date', y='AAPL.High')
    #     fig.show()
    #
    # def see_net_curve(self, time_interval=None, show=True):
    #     """
    #     Visualize the net supply/demand curve in this market for a given market period.
    #     This should remain independent from any implementation platform, which probably means it must be called by an
    #     active display interface.
    #     :param time_interval:
    #     :param show: [Boolean] Set False to not show figure (useful for testing)
    #     :return:
    #     """
    #     positive_infinity = float('Inf')
    #     negative_infinity = float('-Inf')
    #     price_extension = 0.1  # [$/kWh]
    #
    #     if time_interval is None:
    #         time_interval = self.time_intervals[0]
    #
    #     vertices = [x.value for x in self.active_vertices if x.timeInterval == time_interval]
    #
    #     vertices = order_vertices(vertices)
    #
    #     # Extend the curve toward negative infinity marginal price.
    #     if vertices[0].marginalPrice == negative_infinity:
    #         vertices[0].marginalPrice = -price_extension
    #     elif vertices[0].marginalPrice == positive_infinity:
    #         vertices = [Vertex(-price_extension, 0, vertices[0].power)] + vertices
    #     else:
    #         vertices = [Vertex(vertices[0].marginalPrice - price_extension, 0, vertices[0].power)] + vertices
    #
    #     # Extend the curve toward positive infinity marginal price.
    #     if vertices[-1].marginalPrice == positive_infinity:
    #         vertices[-1].marginalPrice = price_extension
    #     else:
    #         vertices = vertices + [Vertex(vertices[-1].marginalPrice + price_extension, 0, vertices[-1].power)]
    #
    #     marginal_prices = [x.marginalPrice for x in vertices]
    #     net_powers = [x.power for x in vertices]
    #
    #     fig, ax = plt.subplots()
    #     ax.plot(marginal_prices, net_powers)
    #
    #     ax.set(xlabel='marginal price [$/kWh]',
    #            ylabel='average power [kW]',
    #            title=(self.name + 'Net Supply/Demand Curve ' + time_interval.name)
    #            )
    #     ax.grid()
    #
    #     if show is True:
    #         plt.show()
    #
    # def see_marginal_prices(self, show=True):
    #     """
    #     Visualize this market's current marginal prices for its active time intervals.
    #     This should remain independent from any implementation platform, which probably means it must be called by an
    #     active display interface.
    #     :param show: [Boolean] Set False to not show figure (useful for testing)
    #     :return:
    #     """
    #
    #     start_times, marginal_prices = zip(*[(k.start_time, v.value) for k, v in self.marginal_prices.all])
    #     marginal_prices.append(marginal_prices[-1])
    #     start_times.append(start_times[-1] + self.interval_duration)
    #
    #     fig, ax = plt.subplots()
    #     ax.step(start_times, marginal_prices, where='post')
    #
    #     ax.set(xlabel='interval starting times',
    #            ylabel='marginal prices [$/kWh]',
    #            title=(self.name + ' Marginal Prices')
    #            )
    #     ax.grid()
    #
    #     if show is True:
    #         plt.show()

    def get_dict(self):
        market_dict = {
            "market_name": self.name,
            "marketSeriesId": self.market_series_name,
            "totalDemand": self.total_demand,
            "totalDualCost": self.total_dual_cost,
            "totalGeneration": self.total_generation,
            "totalProductionCost": self.total_production_cost
        }
        return market_dict

    def get_market_name(self):
        dt = str(self.market_clearing_time)
        market_name = self.market_series_name.replace(' ', '_') + '_' + dt[:19]
        return market_name

    @property
    def table_name(self):
        return 'market'

    def get_record(self) -> dict:
        prior_market_id = self.prior_market_in_series.id if self.prior_market_in_series else None
        record = super(Market, self).get_record()
        record.update({
            'prior_market_id': prior_market_id,
            'market_to_be_refined_id': self.market_to_be_refined.id if self.market_to_be_refined else None,
            'market_type': self.market_type,
            'activation_time': datetime.now(), #self.activationTime,  # TODO: Need to have actual times, not lead intervals.
            'negotiation_start_time': datetime.now(), #self.negotiationStartTime,  # TODO: Need to have actual times, not lead intervals.
            'market_lead_time': datetime.now(),  # self.market_lead_time,  # TODO: Need to have actual times, not lead intervals.
            'market_clearing_time': self.market_clearing_time,
            'reconciliation_time': datetime.now(),#self.reconciliation_time,  # TODO: Need to have actual times, not lead intervals.
            'expiration_time': datetime.now(), #self.expiration_time,  # TODO: Need to have actual times, not lead intervals.
            'market_series_id': self.market_series.id if self.market_series else None  # TODO: Should there ever not be a marketSeries?
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
                    {"name": "activation_time", "type": "DateTime"},
                    {"name": "negotiation_start_time", "type": "DateTime"},
                    {"name": "market_lead_time", "type": "DateTime"},
                    {"name": "market_clearing_time", "type": "DateTime"},
                    {"name": "reconciliation_time", "type": "DateTime"},
                    {"name": "expiration_time", "type": "DateTime"},
                    {"name": "market_series_id", "foreign_key": "market_series.id"}
                ]
            }
        ])
        return definitions
