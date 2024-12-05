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

from tent.enumerations import Direction, MarketTypes
from tent.market import Market
from tent.utils.helpers import production
from tent.utils.log import setup_logging

setup_logging()
_log = logging.getLogger(__name__)


class Auction(Market):
    """
    Auction class child of Market
    An Auction object may be a formal driver of myTransactiveNode's responsibilities within a formal auction. At least
    one Market must exist (see the firstMarket object) to drive the timing with which new TimeIntervals are created.
    """
    def __init__(self, *args, **kwargs):
        # Properties and methods inherited from Market class:
        super(Auction, self).__init__(market_type=MarketTypes.auction, *args, **kwargs)

    def transition_from_active_to_negotiation(self, my_transactive_node):
        """
        This method overwrites the inherited one from class Market.
        200520DJH: Corrected logic so assets are called to schedule their powers only once during this state transition.
        :param my_transactive_node: my transactive node agent object
        :return: None
        """
        _log.debug(f'{self.name} IS IN AUCTION.T_F_ACTIVE_TO_NEGOTIATION')
        # 200730DJH: This will initialize all the marginal price for each time interval using the best available
        #            strategy. I would have preferred not doing so, but so many logic exceptions have been created by
        #            not having existing marginal prices. Point 1: The auction object owns prices only for its active
        #            time intervals. Local assets may predict more future prices if they need to. Point 2: This initial
        #            marginal price should be replaced by the auction mechanisms.
        self.check_marginal_prices(my_transactive_node, return_prices=None)

        # Set the auction convergence flag false prior to the Negotiation market state.
        self.converged = False

        # In an auction, each asset is called upon once per market clearing time to schedule its power.
        for local_asset in my_transactive_node.local_assets:
            # Set each local asset's schedule flag false prior to the Negotiation market state.
            local_asset.schedule_calculated = False

            # Call on the local asset to schedule its power.
            local_asset.schedule(self)

        return None

    def while_in_negotiation(self, my_transactive_node):
        """
        The result of scheduling is that each local asset will create Vertex structs representing its inelastic or
        elastic power consumption. It may further provide at least two additional vertices that represent its price
        elasticity. In an auction, this scheduling is performed for only local assets. Exchanges with neighbor
        agents are determined in later states using time-coordinated transactive signals.
        These actions are conducted only until a satisfactory converged auction has been found.
        """
        #_log.debug(f'{self.name} IS IN AUCTION.W_I_NEGOTIATION')
        # This logic is only meaningful before the market convergence flag is set true.
        if self.converged is False:

            # An Auction should check that all LocalAsset objects have finished scheduling their powers.
            all_calculated = True
            for local_asset in my_transactive_node.local_assets:
                if not local_asset.schedule_calculated:
                    all_calculated = False
                    break

            # If, in fact, all the assets have scheduled their powers, the auction market has nothing left to do within
            # the Negotiation market state, and the market convergence flag is set true.
            if all_calculated is True:
                self.converged = True
                self._stateIsCompleted = True

        return None

    # TODO: Consider using TransactiveNode property "converged" to keep track of those downstream agents that have \
    #  received their transactive signals. This would require setting the convergence flag to False prior to this state.
    def while_in_market_lead(self, my_transactive_node):
        """
        For activities that should happen while a market object is in its "MarketLead" market state. This method may be
        overwritten by child classes of Market to create alternative market behaviors during this market state.
        :param my_transactive_node: my transactive node agent object
        :return: None
        """
        #_log.debug(f'{self.name} IS IN AUCTION.W_I_MARKET_LEAD')
        # TODO: This could use a convergence flag logic to assert that all bids are received and offers sent.
        # Identify the set of neighbor agents that are identified as "upstream" and "downstream".

        upstream_agents = [x for x in my_transactive_node.neighbors if x.up_or_down == Direction.upstream]

        downstream_agents = [x for x in my_transactive_node.neighbors if x.up_or_down == Direction.downstream]

        unassigned_agents = [x for x in my_transactive_node.neighbors if x.up_or_down != Direction.upstream
                             and x.up_or_down != Direction.downstream]

        for x in range(len(unassigned_agents)):
            _log.warning(f'Warning: Assigning neighbor {unassigned_agents[x].name} the downstream direction')
            unassigned_agents[x].up_or_down = Direction.downstream

        if len(upstream_agents) != 1:
            _log.warning('Warning: There should be precisely one upstream neighbor for an auction market')
            raise RuntimeWarning('There should be precisely one upstream neighbor for an auction market')

        # Initialize a flag true if all downstream bids have been received. An aggregate auction bid can be constructed
        # only after all bids have been received from downstream agents (and from local assets, of course):
        all_received = True                                                 # a local flag to this method
        missing_report = []
        # Index through the downstream agents.
        for downstream_agent in downstream_agents:
            # Establish a list for the time intervals for which records have been received by this downstream agent.
            received_time_interval_names = []

            for received_record in downstream_agent.received_signal:
                received_time_interval_names.append(received_record.time_interval)

            # Seek any active market time intervals that were not among the received transactive records.
            missing_interval_names = [x.name for x in self.time_intervals
                                      if x.name not in received_time_interval_names]
            # If any time intervals are found to be missing for this downstream agent,
            if len(missing_interval_names):
                all_received = False
                missing_report.append((downstream_agent.name, missing_interval_names))
                # and call on the downstream agent model to try and receive the signal again:
                # downstream_agent.receive_transactive_signal(my_transactive_node, self, downstream_agent.receivedCurves)
        _log.debug(f'WHILE IN MARKET LEAD, {self.name} HAS RECEIVED ALL RECORDS: {all_received}')
        _log.debug(f'MISSING: {missing_report}')
        # If all expected bids have been received from downstream agents, have the downstream neighbor models update
        # their vertices and schedule themselves. The result of this will be an updated set of active vertices for each
        # downstream agent.
        if all_received is True:

            # For each downstream agent,
            for downstream_agent in downstream_agents:
                # Have each downstream agent model schedule itself. (E.g., schedule power and schedule elasticity via
                # active vertices.
                downstream_agent.schedule(self)

            # Prepare an aggregated bid for the upstream agent if it is a transactive agent. If the upstream agent is
            # transactive,

            if upstream_agents is None or len(upstream_agents) == 0:
                _log.warning('Warning: There must exist one upstream neighbor agent in an auction.')
                raise Warning('There must exist one upstream neighbor agent in an auction.')
            else:
                upstream_agent = upstream_agents[0]                             # Clear indexing of lone upstream agent

                if upstream_agent.transactive is True:
                    # Call on the upstream agent model  to prepare its transactive signal.
                    upstream_agent.prep_transactive_signal(self, my_transactive_node)

                    # Send transactive signal (i.e., aggregated bid) to the upstream agent if it is a transactive agent.
                    upstream_agent.send_transactive_signal(self, my_transactive_node)

            # TODO: We don't have foolproof verification that the upstream agent was sent this signal,
            #  but this might be improved later. This seems like the right place because it's confirmed that
            #  all needed signals have been received.
            self._stateIsCompleted = True

    def while_in_delivery_lead(self, my_transactive_node):
        """
        For activities that should happen while a market object is in its "DeliveryLead" market state. This method may
        be overwritten by child classes of Market to create alternative market behaviors during this market state.
        mtn: my transactive node agent object
        """
        #_log.debug(f'{self.name} AUCTION.W_IN_DELIVERY_LEAD')
        # Identify the set of neighbor agents that is identified as "downstream" (i.e., toward demand side) and the set
        # that is "upstream" (i.e., toward generation).
        downstream_agents = [x for x in my_transactive_node.neighbors if x.up_or_down == Direction.downstream]
        upstream_agents = [x for x in my_transactive_node.neighbors if x.up_or_down == Direction.upstream]
        unassigned_agents = [x for x in my_transactive_node.neighbors if x.up_or_down != Direction.upstream
                             and x.up_or_down != Direction.downstream]
        for unassigned_agent in unassigned_agents:
            _log.warning('Warning: Assigning neighbor ' + unassigned_agent.name + ' the downstream direction')
            _log.warning('Assigning neighbor ' + unassigned_agent.name + ' the downstream direction')
            unassigned_agent.up_or_down = Direction.downstream

        if len(upstream_agents) != 1:
            _log.warning('Warning: There should be precisely one upstream neighbor for an auction market')

        # Initialize a flag true if all downstream bids have been received. An aggregate auction bid can be constructed
        # only after all bids have been received from downstream agents (and from local assets, of course):
        all_received = True                                                 # a local parameter to this method

        # Clarify that we are referencing the lone upstream agent model.
        upstream_agent = upstream_agents[0]

        if upstream_agent.transactive is True:
            # Create a list of the time interval names among the received transactive signal:
            received_time_intervals = []

            # TODO: Consider using property "converged" as a flag for upstream agents that have received their \
            #  transactive signals. The flag must be set false in the transition to this state.
            for received_record in upstream_agent.received_signal:
                received_time_intervals.append(received_record.time_interval)

            # Check whether any active market time intervals are not among the received record intervals.
            missing_time_intervals = [x.name for x in self.time_intervals if x.name not in received_time_intervals]

            # If time intervals are missing among the upstream agent's transactive records,
            if missing_time_intervals:
                all_received = False

                # Call on the upstream agent model to try and receive the signal again.
                # upstream_agent.receive_transactive_signal(my_transactive_node, self, upstream_agent.receivedCurves)

        # If offers have been received for all active market time intervals from the upstream agent,
        # 200618DJH: I think this is correct up to this point, but I had left out some very important steps of the
        #            auction market balancing.
        if all_received is True:
            _log.debug(f'WHILE IN DELIVERY LEAD, {self.name} HAS RECEIVED ALL UPSTREAM SIGNALS')
            # Update this Neighbor's active vertices, which is entirely completed from its recently received transactive
            # signal and its records.
            upstream_agent.schedule(self)

            # Sum all the agent's active local asset and neighbor vertices and determine the LMP at which local power is
            # balanced.
            self.balance(my_transactive_node)

            # Have the upstream agent neighbor model now schedule its power, based on the local agent's calculated LMPs.
            upstream_agent.schedule_power(self)

            # Re-schedule local asset powers now that the local market price is cleared. This may affect flexible assets
            # if the cleared price differs from the predicted one. Inelastic assets will not be affected.
            local_assets = [x for x in my_transactive_node.local_assets]
            for local_asset in local_assets:
                # local_asset.schedule_power(self) ** COMMENT OUT: TOO CUMBERSOME **
                for time_interval in self.time_intervals:
                    price = self.marginal_prices.get_by_interval(time_interval).value
                    # Interpolate among the asset's active vertices to find power at the cleared market clearing price.
                    power = production(local_asset, price, time_interval)
                    local_asset.scheduled_powers.update(time_interval, power)

            # For each downstream agent,
            for downstream_agent in downstream_agents:
                # Now that the LMP has been determined, power may be scheduled for this downstream neighbor.
                downstream_agent.schedule_power(self)

                # Prepare aggregated offer for the downstream agent, and send it a transactive signal (i.e., an offer).
                downstream_agent.prep_transactive_signal(self, my_transactive_node)
                downstream_agent.send_transactive_signal(self, my_transactive_node)

            self._stateIsCompleted = True
