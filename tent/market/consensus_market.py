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

from tent.enumerations import MarketTypes, Method
from tent.market import Market


class ConsensusMarket(Market):
    MARKET_TYPE = MarketTypes.consensus

    def __init__(self, *args, **kwargs):
        # The solution method is a MarketSeries setting (series.method); Market.__init__ does not take it.
        super(ConsensusMarket, self).__init__(*args, **kwargs)

    def while_in_negotiation(self):
        # Almost all consensus balancing activities will occur while the market is in its negotiation state.
        # This method replaces Market.while_in_negotiation().

        # If already converged from a prior call, signal completion and return immediately.
        # This prevents re-running the full iteration loop on every events() tick after convergence.
        if self.converged:
            self._stateIsCompleted = True
            return

        # Clean up or initialize marginal prices. This should not be repeated in process iterations.
        self.check_marginal_prices()

        # Set a flag to indicate solution has not converged.
        self.converged = False

        # Iterate to convergence. "Convergence" here refers to the status of the local convergence of (1) local supply
        # and demand and (2) dual costs. This local convergence says nothing about the additional convergence between
        # transactive neighbors and their calculations.

        # Initialize the iteration counter k
        k = 1

        while not self.converged and k < 100:

            self.balance(self.tn)

            # Invite all neighbors and local assets to schedule themselves based on current marginal prices
            # self.schedule(mtn)  # STAYS IN METHOD BALANCE.

            # Update the primal and dual costs for each time interval and altogether for the entire time horizon.
            # self.update_costs(mtn)  # STAYS IN METHOD BALANCE.

            # Update the total supply and demand powers for each time interval. These sums are needed for the
            # sub-gradient search and for the calculation of blended price.
            # self.update_supply_demand(mtn)  # STAYS IN METHOD BALANCE.

            # Check duality gap for convergence.
            # Calculate the duality gap, defined here as the relative difference between total production and dual
            # costs.

            if self.total_production_cost == 0:
                duality_gap = float("inf")
            else:
                duality_gap = self.total_production_cost - self.total_dual_cost  # [$]
                duality_gap = duality_gap / self.total_production_cost  # [dimensionless. 0.01 is 1#]

            # Display the iteration counter and duality gap. This may be commented out once we have confidence in the
            # convergence of the iterations.
            """
            _log.debug("Market balance iteration %i: (tpc: %f, tdc: %f, dg: %f)" %
                       (k, self.totalProductionCost, self.totalDualCost, dg))
            """

            # Check convergence condition
            if abs(duality_gap) <= self.series.duality_gap_threshold:  # Converged
                # 1.3.1 System has converged to an acceptable balance.
                self.converged = True

            # System is not converged. Iterate. The next code in this method revised the marginal prices in active
            # intervals to drive the system toward balance and convergence.

            # TimeIntervals
            if self.series.method == Method.Interpolation:  # == 2
                self.assign_system_vertices()
                # av = [(x.interval.name, x.value.marginalPrice, x.value.power) for x in self.activeVertices]
                # _log.debug("{} market active vertices are: {}".format(self.name, av))

            # Index through active time intervals.
            for time_interval in self.time_intervals:
                # Find the marginal price interval value for the corresponding indexed time interval.
                cleared_marginal_price = self.marginal_prices.get_by_interval(time_interval).value  # [$/kWh]

                if self.series.method == Method.Subgradient:  # == 1
                    # Find the net power corresponding to the indexed time interval.
                    net_power = self.net_powers.get_by_interval(time_interval)
                    total_generation = self.total_generation.get_by_interval(time_interval)
                    total_demand = self.total_demand.get_by_interval(time_interval)

                    net_power = net_power / (total_generation - total_demand)

                    # Update the cleared marginal price using sub-gradient search.
                    cleared_marginal_price = cleared_marginal_price - (net_power * 1e-1) / (10 + k)  # [$/kWh]

                elif self.series.method == Method.Interpolation:  # === 2
                    # Get the indexed active system vertices
                    active_vertices = [x.value for x in self.active_vertices
                                       if x.interval.start_time == time_interval.start_time]

                    # Order the system vertices in the indexed time interval
                    active_vertices = order_vertices(active_vertices)

                    try:
                        # Find the vertex that bookcases the balance point from the lower side.
                        # Fix a case where all intersection points are on X-axis by using < instead of <=
                        lower_vertex = [x for x in active_vertices if x.quantity < 0]
                        if len(lower_vertex) == 0:
                            err_msg = "At {}, there is no point having power < 0".format(time_interval.name)
                        else:
                            lower_vertex = lower_vertex[-1]

                        # Find the vertex that bookcases the balance point from the upper side.
                        upper_vertex = [x for x in active_vertices if x.quantity >= 0]
                        if len(upper_vertex) == 0:
                            err_msg = "At {}, there is no point having power >= 0".format(time_interval.name)
                        else:
                            upper_vertex = upper_vertex[0]

                        # Interpolate the marginal price in the interval using a principle of similar triangles.
                        power_range = upper_vertex.power - lower_vertex.power
                        marginal_price_range = upper_vertex.marginalPrice - lower_vertex.marginalPrice
                        if power_range == 0:
                            err_msg = "At {}, power range is 0".format(time_interval.name)
                        cleared_marginal_price = - marginal_price_range * lower_vertex.power / power_range \
                                                 + lower_vertex.marginalPrice
                    except RuntimeWarning as warning:
                        Warning('Failed to find balance point: ', warning)
                        """
                        _log.error(err_msg)
                        _log.error("{} failed to find balance point. "
                                   "Market active vertices: {}".format(mtn.name,
                                                                       [(tis[i].name, x.marginalPrice, x.power)
                                                                        for x in av]))
                        """

                        self.converged = False
                        return

                # Regardless of the method used, cleared_marginal_price should now hold the updated marginal price.
                # Assign it to the marginal price value for the indexed active time interval.
                self.marginal_prices.update(time_interval, cleared_marginal_price)  # [$/kWh]

            # Increment the iteration counter.
            k = k + 1
            if k == 100:
                self.converged = True

        self.record_consensus()
        # Signal that negotiation work is complete so the state machine can transition.
        self._stateIsCompleted = True

    def transition_from_negotiation_to_market_lead(self):
        # This is an opportunity to wrap up any loose negotiation ends before the market fully closes.
        # No big activities should be undertaken here, however, so we don't risk working into the delivery period.
        pass  # TBD
