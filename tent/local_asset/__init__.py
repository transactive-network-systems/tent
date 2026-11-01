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
from tent.base import Entity
from tent.config import ConfigModel, Setting
from tent.containers import Measurement, TimeInterval, Vertex
from tent.enumerations import MeasurementType, MeasurementUnit
from tent.utils.timer import Timer

_log = logging.getLogger(__name__)


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

    class Config(Entity.Config):
        default_power: float = Setting(0.0, reconfigurable=True,
                                       description='[avg.kW, signed, generation positive] power scheduled when the asset '
                                                   'has no better estimate.')
        engagement_costs: tuple[float, float, float] = Setting(
            (0.0, 0.0, 0.0), reconfigurable=True,
            description='[$] transition costs [disengagement, hold, engagement]; see engagement_cost().')

    def __init__(self, transactive_node, config: ConfigModel | None = None, **kwargs):
        cfg = self._configure(config, kwargs)
        super(LocalAsset, self).__init__(transactive_node, config=cfg)

        # These following static properties may be assigned as parameters:
        self.default_power: float = float(cfg.default_power)       # [avg.kW, signed] Assignable default power value(s)
        self.engagement_costs: list[float] = list(cfg.engagement_costs)  # [disengagement, hold, engagement] transition costs [$]; see engagement_cost()

        # These following properties are dynamically managed and should not normally be manually configured:
        self.engagement_schedule: Measurement = Measurement(self, MeasurementType.EngagementSchedule, MeasurementUnit.Boolean, archive_now=False)  # True = engaged
        self.schedule_calculated: bool = False            # Flag set True after asset has scheduled power
        self.transition_costs: Measurement = Measurement(self, MeasurementType.TransitionCost, MeasurementUnit.Monetary, archive_now=False)

    def _apply_reconfiguration(self, cfg, changed: set[str]) -> None:
        super()._apply_reconfiguration(cfg, changed - {'engagement_costs'})
        if 'engagement_costs' in changed:
            self.engagement_costs = list(cfg.engagement_costs)

    # SEALED - DO NOT MODIFY
    def schedule(self, market):
        """
        Have object schedule its power in active time intervals
        :param market: Market object
        :return:
        """
        # But give power scheduling priority for a LocalAsset
        self.schedule_power(market)
        self.clamp_scheduled_powers(market)  # the configured limits bound whatever the scheduling model produced

        self.schedule_engagement(market)  # only applied to LocalAsset class
        self.assign_transition_costs(market)  # engagement / holding / disengagement costs per interval

        # Update vertices and calculate reserve margins if schedule powers have been calculated
        if self.schedule_calculated:
            self.update_vertices(market)

            # Have the objects estimate their available reserve margin.
            self.calculate_reserve_margin(market)

    def clamp_scheduled_powers(self, market):
        """
        Hold every scheduled power of this market within [minimum_power, maximum_power]. The limits are the asset's
        configured physical constraints; a scheduling model that extrapolates beyond them (a load regression predicting
        generation in a hot hour) must not offer power the asset cannot deliver, so the vertices are built from the
        clamped powers. Applies only when a range is configured (minimum_power < maximum_power; both default to 0.0).
        One warning per asset and market reports how many intervals were clamped and the worst excursion.
        """
        if not self.minimum_power < self.maximum_power:
            return
        clamped, worst = 0, None  # worst: (excursion beyond the limit, scheduled, limit, interval name)
        for time_interval in market.time_intervals:
            scheduled = self.scheduled_powers.get_by_interval(time_interval)
            if scheduled is None:
                continue
            power = scheduled.value
            limit = (self.minimum_power if power < self.minimum_power
                     else self.maximum_power if power > self.maximum_power else None)
            if limit is None:
                continue
            clamped += 1
            excursion = abs(power - limit)
            if worst is None or excursion > worst[0]:
                worst = (excursion, power, limit, time_interval.name)
            self.scheduled_powers.update(time_interval, limit)
        if clamped:
            excursion, power, limit, name = worst
            _log.warning(f'{self.name}: {clamped} of {len(market.time_intervals)} scheduled powers in market '
                         f'{market.name} lay outside the configured limits [{self.minimum_power}, {self.maximum_power}] '
                         f'kW and were clamped; the worst was {power:.1f} kW in {name}, held at {limit:.1f} kW.')

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

        # Index through the active time intervals ti
        for time_interval in market.time_intervals:
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
            _log.warning(f'Input value to engagement_cost (difference) must be in the set {{-1,0,1}}. Received: {dif}')
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

        # Index through the active time intervals. The first interval of a market
        # normally has no prior engagement state, and is treated as unchanged.
        for time_interval in market.time_intervals:
            # Find the current and prior engagement states based on the current indexed time interval
            current_engagement_state = self.engagement_schedule.get_by_interval(time_interval)
            prior_engagement_state = self.engagement_schedule.get_prior_by_interval(time_interval)

            # Without a current engagement state there is no transition to price.
            if current_engagement_state is None:
                continue

            # Calculate the state transition
            # - -1:Disengaging
            # -  0:Unchanged
            # -  1:Engaging
            # A missing prior state is treated as unchanged rather than as an
            # engagement, so an asset is not charged an engagement cost merely
            # because a market's first interval has no predecessor.
            dif = current_engagement_state - prior_engagement_state if prior_engagement_state is not None else 0

            # Assign the corresponding transition cost
            transition_cost = self.engagement_cost(dif)
            self.transition_costs.update(time_interval, transition_cost)

    def _get_production_costs(self, time_interval):
            production_cost = super(LocalAsset, self)._get_production_costs(time_interval)
            # Check for a transition cost in the indexed time interval.
            # (NOTE: this differs from neighbor models, which do not possess the concept of commitment and engagement.)
            transition_cost = self.transition_costs.get_by_interval(time_interval)
            transition_cost = 0.0 if transition_cost is None else transition_cost.value  # [$]
            # Add the transition cost to the production cost
            return production_cost + transition_cost

    def update_vertices(self, market):
        # Create vertices to represent the asset's flexibility
        #
        # For the base local asset model, a single, inelastic power is needed. There is no flexibility. The constant
        # power may be represented by a single (price, power) point (See Vertex container).
        for time_interval in market.time_intervals:
            # Find the scheduled power for the indexed time interval.
            scheduled_power = self.scheduled_powers.get_by_interval(time_interval)  # avg. kW
            # Create the vertex that can represent this (lack of) flexibility
            self.active_vertices.update(time_interval,
                                        Vertex(float("inf"), 0.0, scheduled_power.value, True),
                                        archive=False, replace=True)

        _log.debug(f"{self.name} asset model active vertices are: {self.active_vertices.log_report}")

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
        self.total_production_cost = self.production_costs.total()  # [$]
        self.total_dual_cost = self.dual_costs.total()

    def actuate(self, mkt):
        """
        Can be used by subclasses to set the actuation of scheduled powers when called from market.
        This is called in the base Market base class from while_in_delivery_lead().
        """
        pass

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
                    {"name": "engagement_cost_2", "type": "Float"}
                ]
            }
        ])
        return definitions
