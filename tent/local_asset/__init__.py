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

from datetime import timedelta
from typing import Union

from tent import Entity
from tent.containers import IntervalValue, Measurement, TimeInterval, Vertex
from tent.enumerations import MarketState, MeasurementType, MeasurementUnit
from tent.utils.helpers import find_objs_by_ti, format_timestamp, prod_cost_from_vertices
from tent.utils.log import setup_logging
from tent.utils.timer import Timer

setup_logging()
_log = logging.getLogger(__name__)

# 191218DJH: This class originally inherited from class Model. Model is being deleted. Its properties and methods are
# being moved into this class.


class LocalAsset(Entity):
    # A local asset model manages and represents a local asset object, meaning that it
    # (1) determines a power schedule across all active time intervals,
    # (2) calculates costs that are needed by system optimization, and
    # (3) models flexibility, if any, that is available from the control of this asset in active time intervals.
    #
    # This base class provides many of the properties and methods that will be needed to manage local assets--generation
    # and demand alike. However, it schedules only the simplest, constant power throughout active time intervals.
    # Subclassing will be required to perform dynamic power scheduling, especially where scheduling is highly
    # constrained or invokes optimizations. Even then, implementers might need further subclassing to model their unique
    # assets.
    #
    # Available subclasses that inherit from this base class: This taxonomy is influenced by the thesis (Kok 2013).
    # Inelastic - dynamic scheduling independent of prices
    # Shiftable - schedule a fixed utility over a time range
    # Buffering - schedule power while managing a (thermal) buffer
    # Storage - optimize revenue, less cost
    # Controllable - unconstrained scheduling based on a polynomial production or utility def

    def __init__(self,
                 transactive_node,
                 cost_parameters=(0.0, 0.0, 0.0),
                 default_power=0.0,
                 default_vertices=None,
                 description='',
                 engagement_costs=(0.0, 0.0, 0.0),
                 information_services=None,
                 location='',
                 maximum_power=0.0,
                 minimum_power=0.0,
                 meter_points=None,
                 name='',
                 scheduling_horizon: Union[timedelta, float, int] = timedelta(hours=24),
                 subclass=None):
        super(LocalAsset, self).__init__(cost_parameters, default_vertices, description, information_services, location,
                                         maximum_power, meter_points, minimum_power, name, transactive_node)

        # These following static properties may be assigned as parameters:
        self.default_power = float(default_power)           # [avg.kW, signed] Assignable default power value(s)
        self.engagement_costs = [float(ec) for ec in engagement_costs]  # {engagement, [$]; hold, [$]; disengagement, [$]} Transition costs
        self.scheduling_horizon = scheduling_horizon if isinstance(scheduling_horizon, timedelta)\
            else timedelta(seconds=scheduling_horizon) # [time duration] Future that price and energy shift are relevant
        self.subclass = subclass                    # Future, unused

        # These following properties are dynamically managed and should not normally be manually configured:
        self.activeVertices = []                    # [IntervalValue] Values are [Vertex]: Demand/Supply curves
        self.dual_costs = Measurement(self, MeasurementType.DualCost, MeasurementUnit.Monetary)
        self.engagement_schedule = Measurement(self, MeasurementType.EngagementSchedule, MeasurementUnit.Boolean)  # True = engaged
        self.production_costs = Measurement(self, MeasurementType.ProductionCost, MeasurementUnit.Monetary)
        self.reserve_margins = Measurement(self, MeasurementType.ReserveMargin, MeasurementUnit.kW) # [avg.kW, signed]
        self.schedule_calculated = False            # Flag set True after asset has scheduled power
        self.scheduled_powers = Measurement(self, MeasurementType.ScheduledPower, MeasurementUnit.kW)  # [avg.kW, signed]
        self.total_dual_cost = 0.0                    # [$] Sum of dual costs in forward time intervals
        self.total_production_cost = 0.0              # [$, non-negative] Sum of production costs in forward intervals
        self.transition_costs = Measurement(self, MeasurementType.TransitionCost, MeasurementUnit.Monetary)

    def cost(self, power=0):
        # Calculate production (consumption) cost at the given power level.
        #
        # INPUTS:
        # power - power production (consumption) for which production (consumption) costs are to be calculated [kW]. By
        #         convention, imported and generated power is positive exported or consumed power is negative.
        #
        # RETURN:
        # production_cost - calculated production (consumption) cost [$/h]
        #
        # LOCAL:
        # a - array of production cost coefficients that must be ordered [a0 a1 a2], such that
        #                              cost = a0 + a1*power + a2*power ** 2 [$/h].

        # Extract the production cost coefficients for the given object
        a = self.cost_parameters

        # Calculate the production (consumption) cost for the given power
        production_cost = a[0] + a[1] * power + a[2] * power ** 2  # [$/h]

        return production_cost

    # SEALED - DO NOT MODIFY
    def schedule(self, market):
        """
        Have object schedule its power in active time intervals
        :param market: Market object
        :return:
        """
        # But give power scheduling priority for a LocalAsset
        self.schedule_power(market)

        # Log if possible
        """
        if self.mtn is not None and self.power_topic != '':
            sp = [(x.timeInterval.name, x.value) for x in self.scheduledPowers]
            self.mtn.vip.pubsub.publish(peer='pubsub',
                                        topic=self.power_topic,
                                        message={'power': sp})
        """

        self.schedule_engagement(market)  # only applied to LocalAsset class

        # Update vertices and calculate reserve margins if schedule powers have been calculated
        if self.schedule_calculated:
            self.update_vertices(market)

            # Have the objects estimate their available reserve margin.
            self.calculate_reserve_margin(market)

    def schedule_power(self, market):
        # Determine powers of an asset in active time intervals. NOTE that this method may be redefined by subclasses if
        # more features are needed. NOTE that this method name is common for all asset and neighbor models to facilitate
        # its redefinition.
        #
        # PRESUMPTIONS:
        # - Active time intervals exist and have been updated
        # - Marginal prices exist and have been updated.
        #   NOTE: Marginal prices are used only for elastic assets. This base class is provided for the simplest asset
        #         having constant power. It MUST be extended to represent dynamic and elastic asset behaviors.
        #   NOTE: (1911DJH) This requirement is loosened upon recognizing that auction markets have no forward prices
        #         available at the time an asset is called to schedule its power, i.e., to prepare its bid. This can be
        #         resolved by letting an asset model use its markets' price forecast models for forward intervals in
        #         which locational marginal prices have not yet been discovered.
        # - Transition costs, if relevant, are applied during the scheduling of assets.
        # - An engagement schedule, if used, is applied during an asset's power scheduling.
        # - Scheduled power and engagement schedule must be self-consistent at the end of this method. That is, power
        #   should not be scheduled while the asset is disengaged (uncommitted).
        #
        # INPUTS:
        # market - Market object
        #
        # OUTPUTS:
        # - Updates self.scheduledPowers - the schedule of power consumed
        # - Updates self.engagementSchedule - an array that states whether the
        #   asset is engaged (committed) (true) or not (false) in the time interval

        # Gather and sort the active time intervals
        time_intervals = market.time_intervals
        time_intervals.sort(key=lambda x: x.start_time)

        # Index through the active time intervals ti
        for time_interval in time_intervals:
            # TODO: Eliminate or fix parameter default_powers, which has been defined independent of market period.
            #  Reassign default value if there is power value for this time interval
            #  No, no, and no. Do not assign default values apart from specific times. The real world does not always
            #  begin at the same time. This method slides with the market periods, which is not correct!
            #  if len(self.default_powers) > i:
            #    default_value = self.default_powers[i] if self.default_powers[i] is not None else self.default_power
            #  default_value would then be used instead of self.default_power on next line.
            self.scheduled_powers.update(time_interval, self.default_power)

        # 210803DJH: Auctions use this flag, so it should probably be set after every successful scheduling of power.
        self.schedule_calculated = True

        _log.debug(f"{self.name} asset scheduled_powers: {self.scheduled_powers.log_report}")

    def schedule_engagement(self, market):
        # To assign engagement, or commitment, which is relevant to some local assets (supports future capabilities).
        # NOTE: The assignment of engagement schedule, if used, may be assigned during the scheduling of power, not
        # separately as demonstrated here. Commitment and engagement are closely aligned with the optimal production
        # costs of schedulable generators and utility def of engagements (e.g., demand responses).

        # NOTE: Because this is a future capability, Implementers might choose to simply return from the call until
        # LocalAsset behaviors are found to need commitment or engagement.

        # Index through the active time intervals.
        for time_interval in market.time_intervals:
            # NOTE: this template currently assigns engagement value as true (i.e., engaged).
            engagement_flag = True  # Asset is committed or engaged
            self.engagement_schedule.update(time_interval, engagement_flag)

    def calculate_reserve_margin(self, market):
        # Estimate available (spinning) reserve margin for this asset.
        #
        # NOTES:
        #   This method works with the simplest base classes that have constant power and therefore provide no spinning
        #   reserve. This method may be redefined by subclasses of the local asset model to add new features or
        #   capabilities.
        #   This calculation will be more meaningful and useful after resource commitments and uncertainty estimates
        #   become implemented. Until then, reserve margins may be tracked, even if they are not used.
        #
        # PRESUMPTIONS:
        # - Active time intervals exist and have been updated
        # - The asset's maximum power is a meaningful and accurate estimate of the maximum power level that can be
        #   achieved on short notice, i.e., spinning reserve.
        #
        # INPUTS:
        # market - Market object
        #
        # OUTPUTS:
        # Modifies self.reserveMargins - an array of estimated (spinning) reserve margins in active time intervals

        # Index through active time intervals ti
        for time_interval in market.time_intervals:
            # Calculate the reserve margin for the indexed interval. This is the non-negative difference between the
            # maximum asset power and the scheduled power. In principle, generation may be increased or demand decreased
            # by this quantity to act as spinning reserve.

            # Find the scheduled power in the indexed time interval
            scheduled_power = self.scheduled_powers.get_by_interval(time_interval)

            # Calculate the reserve margin in the indexed time interval. The reserve margin is the difference between
            #  the maximum operational power value in the interval and the scheduled power. The operational maximum
            #  should be less than the object's hard physical power constraint, so a check is in order. Start with the
            #  hard physical constraint (self.maximum_power).
            # Calculate the operational maximum constraint, which is the highest point on the supply/demand curve (i.e.,
            #  the vertex) that represents the residual flexibility of the asset in the time interval. If there are no
            #  active vertices, use the hard physical constraint (maximum power).
            operational_constraint = max(find_objs_by_ti(self.activeVertices, time_interval),
                                         key=lambda av: av.value.power, default=self.maximum_power)  # [avg.kW]

            # The upper operational power constraint must be less than or equal to the object's hard physical constraint.
            soft_maximum = min(self.maximum_power, operational_constraint)  # [avg.kW]

            # And finally calculate the reserve margin.
            reserve_margin = max(0, soft_maximum - scheduled_power.value)  # [avg. kW]

            self.reserve_margins.update(time_interval, reserve_margin)

    def engagement_cost(self, dif):
        # Assigns engagement cost based on difference in engagement status in the current minus prior time intervals.
        #
        # INPUTS:
        # dif - difference (current interval engagement - prior interval engagement), which assumes integer values
        #       [-1,0,1] that should correspond to the three engagement costs.
        # USES:
        # self.engagementSchedule
        # self.engagementCost
        #
        # OUTPUTS:
        # cost - transition cost
        # diff - cost table as a def of current and prior engagement states:
        #    \  current |   false   |  true
        # prior false   |  0:ec(2)  | 1:ec(3)
        #       true    | -1:ec(1)  | 0:ec(2)

        # Check that dif is a feasible difference between two logical values
        if dif not in [-1, 0, 1]:
            print('Input value must be in the set {-1,0,1}.')
            cost = None
            return cost

        # Assign engagement cost by indexing the three values of engagement cost
        # 1 - transition from false to true - engagement cost
        # 2 - no change in engagement - no cost
        # 3 - transition from true to false - disengagement cost
        cost = self.engagement_costs[1 + dif]

        return cost

    def assign_transition_costs(self, market):
        # Assign the cost of changing engagement state from the prior to the current time interval
        #
        # PRESUMPTIONS:
        # - Time intervals exist and have been updated
        # - The engagement schedule exists and has been updated. Contents are logical [true/false].
        # - Engagement costs have been accurately assigned for [disengagement, unchanged, engagement]
        #
        # INPUTS:
        # market - Market object
        #
        # USES:
        # - self.engagementCost - three costs that correspond to [disengagement, unchanged, engagement) transitions
        # - self.engagement_cost() - assigns appropriate cost from self.engagementCost property
        # - self.engagementSchedule - engagement states (true/false) for the asset in active time intervals
        #
        # OUTPUTS:
        # Assigns values to self.transition_costs

        # Gather active time intervals and sort by time interval start times.
        time_intervals = market.time_intervals
        time_intervals.sort(key=lambda x: x.start_time)

        # Index through all but the first time interval.
        for time_interval in time_intervals:
            # Find the current and prior engagement states based on the current indexed time interval
            current_engagement_state = self.engagement_schedule.get_by_interval(time_interval)
            prior_engagement_state = self.engagement_schedule.get_prior_by_interval(time_interval)

            # Calculate the state transition
            # - -1:Disengaging
            # -  0:Unchanged
            # -  1:Engaging
            # TODO: Is the calculation of transition cost is right for the first time interval?
            #  Should it be unchanged or engaging?
            dif = current_engagement_state - prior_engagement_state if prior_engagement_state is not None else 0

            # Assign the corresponding transition cost
            transition_cost = self.engagement_cost(dif)
            self.transition_costs.update(time_interval, transition_cost)

    def update_dual_costs(self, market):
        # Update the dual cost for all active time intervals
        # (NOTE: Choosing not to separate this def from the base class because cost might need to be handled differently
        # and redefined in subclasses.)

        # Index through the time intervals.
        # NOTE 1911DJH: The range in this for-loop has been corrected and now passes unit testing. I believe the code
        # had been modified to avoid revising the schedule for the first hour in Version 2. This is corrected by the
        # market state transition model in Version 3, which should be configured to NOT allow negotiation of prices and
        # quantities after the respective market has cleared or entered its delivery period.
        for time_interval in market.time_intervals:

            # Find the marginal price, scheduled power, and production cost for the indexed time interval.
            marginal_price = market.marginal_prices.get_by_interval(time_interval)  # a marginal price [$/kWh]
            scheduled_power = self.scheduled_powers.get_by_interval(time_interval) # [avg.kW]
            production_cost = self.production_costs.get_by_interval(time_interval) # [$]

            # Dual cost in the time interval is calculated as production cost, minus the product of marginal price,
            # scheduled power, and the duration of the time interval.
            duration = time_interval.duration.total_seconds() / 3600
            dual_cost = production_cost - (marginal_price * scheduled_power * duration)  # [$]

            self.dual_costs.update(time_interval, dual_cost)

        # Sum the total dual cost and save the value.
        # 200929DJH: This is corrected to sum only the dual costs in the current market
        # TODO: If there are two markets operating simultaneously, won't the second assignment clobber the first?
        self.total_dual_cost = self.dual_costs.total(market)

        _log.debug(f"{self.name} asset model dual costs are: {self.dual_costs.log_report}")

    def update_production_costs(self, market):
        # Calculate the costs of generated energies.
        # (NOTE: Cost might need to be handled differently and redefined in subclasses.)

        # Index through the active time intervals.
        for time_interval in market.time_intervals:
            # Get the scheduled power in the indexed time interval.
            scheduled_power = self.scheduled_powers.get_by_interval(time_interval)  # [avg.kW]

            # Call on def that calculates production cost pc based on the vertices of the supply or demand curve
            production_cost = prod_cost_from_vertices(self, time_interval, scheduled_power.value)  # [$]

            # Check for a transition cost in the indexed time interval.
            # (NOTE: this differs from neighbor models, which do not possess the concept of commitment and engagement.)
            transition_cost = self.transition_costs.get_by_interval(time_interval)
            transition_cost = 0.0 if transition_cost is None else transition_cost.value  # [$]

            # Add the transition cost to the production cost
            production_cost = production_cost + transition_cost

            self.production_costs.update(time_interval, production_cost)

        # Sum the total production cost.
        # 200929DJH: In Version 3, this sum must be made only within the current market object.
        # TODO: If there are two markets operating simultaneously, won't the second assignment clobber the first?
        self.total_production_cost = self.production_costs.total(market)  # [$]

        _log.debug(f"{self.name} asset model production costs are: {self.production_costs.log_report}")

    def update_vertices(self, market):
        # Create vertices to represent the asset's flexibility
        #
        # For the base local asset model, a single, inelastic power is needed. There is no flexibility. The constant
        # power may be represented by a single (price, power) point (See struct Vertex).

        # Index through active time intervals.
        for time_interval in market.time_intervals:

            # Find the scheduled power for the indexed time interval. Extract the scheduled power value.
            # TODO: Make this next line tolerant upon finding scheduled_power = None.
            scheduled_power = self.scheduled_powers.get_by_interval(time_interval)  # avg. kW

            # Create the vertex that can represent this (lack of) flexibility
            value = Vertex(float("inf"), 0.0, scheduled_power.value, True)

            # TODO: Update this block once SDCurve is integrated.
            ##### BEGIN BLOCK
            # Check to see if the active vertex already exists for this indexed time interval.
            interval_value = find_objs_by_ti(self.activeVertices, time_interval)[0]
            # If the active vertex does not exist, a new interval value must be created and stored.
            if interval_value is None:
                # Create the interval value and place the active vertex in it
                interval_value = IntervalValue(self, time_interval, market, MeasurementType.ActiveVertex, value)
                # Append the interval value to the list of active vertices.
                self.activeVertices.append(interval_value)
            else:
                # Otherwise, simply reassign the active vertex value to the existing listed interval value.
                # NOTE that this base local asset model unnecessarily reassigns constant values, but the reassignment
                # is allowed because it teaches how a more dynamic assignment may be maintained.
                interval_value.value = value

        # 200929DJH: Trim the list of active vertices so that it will not grow indefinitely.
        self.activeVertices = [x for x in self.activeVertices if x.market.market_state != MarketState.Expired]

        av = [(x.time_interval.name, x.value.marginal_price, x.value.power) for x in self.activeVertices]
        _log.debug(f"{self.name} asset model active vertices are: {av}")
        ##### END BLOCK

    def get_extended_prices(self, market):
        """
        This method is available to facilitate estimation of opportunity cost for local assets that have a scheduling
        horizon that extends farther into the future than supplied by the market in forward time intervals. For example,
        a market may offer only one forward time interval with its price offer. Even in this example, auction markets do
        not provide market prices at the times bids are to be formulated by the local asset. Local assets having states
        (e.g., controlled interior temperature, battery charge state) must somehow calibrate prices farther into the
        future as a foundation for understanding the opportunity cost.
        This base method uses the following prioritization to populate the price horizon:
        1. Actual marginal prices offered by the market object.
        2. Actual marginal prices in prior sequential markets that are similar to the market object.
        3. Actual marginal prices in prior markets that are being corrected by the market object.
        4. Modeled prices using the market's price model
        5. The market's default price value.
        6. Nothing
        NOTE: This method is similar to Market.check_marginal_prices(), but that method's purpose is to specifically
              estimate marginal prices for active time intervals, not throughout the scheduling horizon.
        :param market: The Market object for which these prices are relevant

        :return: price_horizon, which may be placed into property self.schedulingHorizon by caller
        """

        # The scheduling horizon is a distance into the future. The price horizon is initialized, then prices are found
        # from now until the end of the scheduling horizon.
        end_of_calculated_horizon = Timer.get_cur_time()
        end_of_scheduling_horizon = end_of_calculated_horizon + self.scheduling_horizon
        price_horizon = Measurement(self, MeasurementType.MarginalPrice, MeasurementUnit.Monetary)

        # Case 1: Actual marginal prices offered by the market object that has requested the asset to schedule.
        if market.marginal_prices:
            price_horizon = market.marginal_prices.copy()
            end_of_calculated_horizon = market.marginal_prices.last.start_time + market.interval_duration

            if end_of_calculated_horizon >= end_of_scheduling_horizon:
                return price_horizon

        # Case 2: Actual marginal prices in prior sequential markets that are similar to the market object.
        if market.prior_market_in_series and market.prior_market_in_series() and market.prior_market_in_series().marginal_prices:
            # Get the marginal prices from the latest similar market.

            # Find any market prices from the corrected market to add to the price horizon
            for time_interval, marginal_price in market.prior_market_in_series().marginal_prices:
                if time_interval.end_time > end_of_calculated_horizon:
                    price_horizon.update(time_interval, marginal_price)
                    end_of_calculated_horizon = time_interval.end_time

            if end_of_calculated_horizon >= end_of_scheduling_horizon:
                return price_horizon

        # Case 3. Actual marginal prices in prior markets that are being corrected by the market object.
        # Start by gathering similar prior markets that are in the market series being corrected
        if market.priorRefinedMarket and market.priorRefinedMarket() and market.priorRefinedMarket().marginal_prices:
            # Find any market prices from the latest similar market to add to the price horizon
            # TODO: If the refined market has different starts/durations than ours, are these TimeIntervals correct?
            for time_interval, marginal_price in market.priorRefinedMarket.marginal_prices.all:
                if time_interval.end_time > end_of_calculated_horizon:
                    price_horizon.update(time_interval, marginal_price)
                    end_of_calculated_horizon = time_interval.end_time

            if end_of_calculated_horizon >= end_of_scheduling_horizon:
                return price_horizon

        # Case 4. Modeled prices using the market's price model
        if market.price_model:
            while end_of_scheduling_horizon > end_of_calculated_horizon:
                # 240206DJH: This next line fails unit testing. I see that market.priceModel should now be equivalent to
                #            an object of class HourlyAveragePriceModel, but it is being made equivalent to the model
                #            itself instead.
                price = market.price_model.get(end_of_calculated_horizon)
                start_time = end_of_calculated_horizon.replace(minute=0, second=0)
                end_time = start_time + timedelta(hours=1)  # TODO: Why are we doing specifically hours now?
                duration = end_time - start_time
                price_horizon.update(TimeInterval(market, duration, start_time), price[0])
                end_of_calculated_horizon = end_time
            return price_horizon

        # Case 5. The market's default price value.
        price = market.default_price
        while end_of_scheduling_horizon > end_of_calculated_horizon:
            start_time = end_of_calculated_horizon
            end_time = start_time + timedelta(hours=1)  # TODO: Why are we doing hours here?
            end_time.replace(minute=0, second=0, microsecond=0)
            duration = end_time - start_time
            price_horizon.update(TimeInterval(market, duration, start_time), price)
            end_of_calculated_horizon = end_time
        return price_horizon

    def update_costs(self, market):
        """
        191218DJH: This method was originally inherited from class (Abstract)Model. The purpose was to prescribe the set
        of cost calculation that every asset (and neighbor) must perform. The class structure is being simplified. Class
        Model is being deleted, and its properties and methods are being moved to its children.
        :param market:
        :return:
        """

        # Initialize sums of production and dual costs.
        self.total_production_cost = 0.0
        self.total_dual_cost = 0.0

        # Have object update and store its production and dual costs in each active time interval.
        self.update_production_costs(market)
        self.update_dual_costs(market)

        # Sum total production and dual costs through all time intervals.
        # TODO: Why is this different from the versions in update_production_costs and update_dual_costs?
        #  This does not take markets into account, where those do (for example, from update_production_costs):
        #     self.total_production_cost = sum([v.value for k, v in self.production_costs.all if k.market == market])  # [$]
        #  Further, this seems to just save over the sums which were calculated in those two functions....
        self.total_production_cost = sum([v.value for k, v in self.production_costs.all])  # [$]
        self.total_dual_cost = sum([v.value for k, v in self.dual_costs.all])

    def actuate(self, mkt):
        """
        Can be used by subclasses to set the actuation of scheduled powers when called from market.
        This is called in the base Market base class from while_in_delivery_lead().
        """
        pass

    def get_dict(self):
        scheduled_powers = [(format_timestamp(k.start_time), v.value) for k, v in self.scheduled_powers.all]
        vertices = [(format_timestamp(x.time_interval.start_time), x.value.marginal_price, x.value.power) for x in
                    self.activeVertices]
        local_asset_dict = {
            "name": self.name,
            "scheduled_power": scheduled_powers,
            "vertices": vertices
        }
        return local_asset_dict

    @property
    def table_name(self):
        return 'local_asset'

    def get_record(self) -> dict:
        record = super(LocalAsset, self).get_record()
        record.update({
            'default_power': self.default_power,
            'engagement_cost_0': self.engagement_costs[0],
            'engagement_cost_1': self.engagement_costs[1],
            'engagement_cost_2': self.engagement_costs[2],
            # 'holding_cost': self.holding_cost, TODO: Did this come from old schema?
            # 'disengagement_cost': self.disengagement_cost, TODO: Did this come from old schema?
            'scheduling_horizon': self.scheduling_horizon,
            'subclass': self.subclass
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(LocalAsset, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "local_asset",
                "inherits": "entity",
                "columns": [
                    {"name": "id", "foreign_key": "entity.id", "kwargs": {"primary_key": True}},
                    {"name": "default_power", "type": "Float"},
                    {"name": "engagement_cost_0", "type": "Float"},
                    {"name": "engagement_cost_1", "type": "Float"},
                    {"name": "engagement_cost_2", "type": "Float"},
                    #{"name": "holding_cost", "type": "Float"},
                    #{"name": "disengagement_cost", "type": "Float"},
                    {"name": "scheduling_horizon", "type": "DateTimeRangeType"},
                    {"name": "subclass", "type": "String", "type_args": [255]}
                ]
            }
        ])
        return definitions
