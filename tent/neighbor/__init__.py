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
import json
import logging
from dataclasses import dataclass

from copy import deepcopy
from datetime import datetime
from itertools import chain
from typing import Union
from weakref import WeakKeyDictionary

from tent.base import Entity
from tent.containers import Measurement, SDCurve, TransactiveSignal, Vertex
from tent.enumerations import Direction, MeasurementType, MeasurementUnit
from tent.market import Market
from tent.utils.helpers import validate_bool
from tent.utils.timer import Timer

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _LegacySignalRecord:
    """Shim object providing backward-compatible access to received signals."""
    time_interval: str
    marginal_price: float | None
    power: float | None
    market_name: str | None = None


class Neighbor(Entity):
    # The Neighbor class manages the interface with a Neighbor object and represents it for the computational agent.
    # Members of the transactive network must be indicated by setting the "transactive" property true.

    def __init__(self,
                 transactive_node,
                 convergence_threshold=0.05,
                 cost_parameters=None,
                 default_vertices=None,
                 demand_month=datetime.today().month,
                 demand_rate=4.5,
                 demand_threshold=1e9,
                 demand_threshold_coef=1,
                 description='',
                 effective_impedance=0.0,
                 friend=False,
                 information_services=None,
                 location='',
                 loss_factor=0.01,
                 maximum_power=0.0,
                 mechanism='consensus',
                 meter_points=None,
                 minimum_power=0.0,
                 name='',
                 transactive=False,
                 up_or_down: Union[Direction, int, str] = Direction.unknown):

        super(Neighbor, self).__init__(cost_parameters, default_vertices, description, information_services, location,
                                       maximum_power, meter_points, minimum_power, name, transactive_node)

        self.convergence_threshold = float(convergence_threshold)      # [Small positive fraction]: May affect negotiations
        if cost_parameters is None:
            cost_parameters = [0.0, 0.0, 0.0]
        self.cost_parameters = [float(cp) for cp in cost_parameters]   # 3x[float]: Parameters of quadratic production cost
        self.demand_month = int(demand_month)                          # [month number]: Used to reset demand charges
        self.demand_rate = float(demand_rate)                          # [$ / kW (/h)]: rate determinant
        self.demand_threshold = float(demand_threshold)                # [kW]; current power above which demand charges accrue
        self.demand_threshold_coef = float(demand_threshold_coef)      # Factor by which threshold is reduced in new month
        self.description = str(description)                            # [text]
        self.effective_impedance = float(effective_impedance)          # [Ohms]: (future) may be used for loss effects
        self.friend = validate_bool(friend, 'friend')                  # [boolean]: True for collaborative, friendly Neighbor
        self.location = str(location)                                  # [text]
        self.loss_factor = float(loss_factor)                          # [dimensionless] 0.01 = 1% full-load loss
        self.maximum_power = float(maximum_power)                      # [avg.kW, signed] Object's physical "hard" constraint
        self.mechanism = mechanism                                     # future, unused
        self.minimum_power = float(minimum_power)                      # [avg.kW, signed] Object's physical "hard" constraint
        self.name = str(name)                                          # [text]
        self.transactive = validate_bool(transactive, 'transactive')   # [boolean]: True for transactive neighbor
        if isinstance(up_or_down, Direction):                          # 'upstream' or 'downstream' direction of this neighbor
            self.up_or_down = up_or_down
        elif isinstance(up_or_down, int):
            self.up_or_down = Direction(up_or_down)
        else:
            self.up_or_down = Direction[up_or_down]

        ##################################
        # Dynamically assigned attributes:
        ##################################
        self.converged = False                                          # [boolean]: True if converged
        self.convergence_flags: Measurement = Measurement(self, MeasurementType.ConvergenceFlag, MeasurementUnit.Boolean)
        self.prepared_signals: WeakKeyDictionary[Market, TransactiveSignal] = WeakKeyDictionary()  # Current signal, ready to send.
        self.last_received_signals: WeakKeyDictionary[Market, TransactiveSignal] = WeakKeyDictionary()  # Last records received.
        self.last_sent_signals: WeakKeyDictionary[Market, TransactiveSignal] = WeakKeyDictionary()  # Last records sent.
    # Internal guards to avoid repeated noisy warnings
        self._loss_warning_reported = False

        transactive_node.register_subscription(subscribe_key=f'{self.name}_incoming_transactive_signal',
                                               callback=self.receive_transactive_signal)

    @property
    def received_signal(self):
        """Backward-compatible view of received signals as iterable records.

        Legacy auction workflows expect Neighbor.received_signal to be iterable with
        objects exposing a ``time_interval`` attribute (string name), along with
        optional pricing metadata. This shim reconstructs that interface using the
        parsed ``TransactiveSignal`` instances stored in ``last_received_signals``.
        """

        legacy_records: list[_LegacySignalRecord] = []
        for market, signal in list(self.last_received_signals.items()):
            if signal is None:
                continue
            market_name = getattr(market, 'name', None)
            for time_interval, sd_curve in signal.all:
                balance_vertex = signal.balance_point(time_interval)
                if balance_vertex is not None:
                    legacy_records.append(
                        _LegacySignalRecord(
                            time_interval=time_interval.name,
                            marginal_price=getattr(balance_vertex, 'price', None),
                            power=getattr(balance_vertex, 'quantity', None),
                            market_name=market_name
                        )
                    )
                    continue

                # Fall back to using the first vertex from the curve if no balance point exists.
                first_vertex = next(iter(sd_curve.vertices), None)
                legacy_records.append(
                    _LegacySignalRecord(
                        time_interval=time_interval.name,
                        marginal_price=getattr(first_vertex, 'price', None),
                        power=getattr(first_vertex, 'quantity', None),
                        market_name=market_name
                    )
                )

        if legacy_records:
            return legacy_records

        # No transactive signal has been received yet. Provide a synthetic record based on default vertices so that
        # legacy auction workflows can continue to function prior to the first real message exchange.
        default_vertex = next(iter(getattr(self.default_vertices, 'vertices', [])), None)
        if default_vertex is None:
            return legacy_records

        market_iterables = []
        if hasattr(self.tn, 'market_series'):
            market_iterables.extend(getattr(series, 'markets', []) for series in self.tn.market_series)
        else:
            market_iterables.append(getattr(self.tn, 'markets', []))

        for market in chain.from_iterable(market_iterables):
            market_name = getattr(market, 'name', None)
            for time_interval in getattr(market, 'time_intervals', []):
                legacy_records.append(
                    _LegacySignalRecord(
                        time_interval=time_interval.name,
                        marginal_price=getattr(default_vertex, 'price', None),
                        power=getattr(default_vertex, 'quantity', None),
                        market_name=market_name
                    )
                )

        return legacy_records

    def check_for_convergence(self, market):
        # Qualifies state of convergence with a transactive Neighbor object by active time interval and globally.
        #
        # In respect to the coordination sub-problem, a Neighbor is not converged for a given time interval and a signal
        # should be sent to the transactive Neighbor if
        # - The balancing and scheduling sub-problems are converged, AND
        # - No signal has been sent, OR
        # - A signal has been received from the Neighbor, and no signal has been sent since the signal was received, but
        #   scheduled power and marginal price in the sent and received signals (i.e., Records 0) differ, OR
        # - A timer has elapsed since the last time a signal was sent, and the sent signal differs from one that would
        #   be sent again, based on current conditions.
        #
        # Inputs:
        # market - Market object
        #
        # Uses property convergenceThreshold as a convergence criterion.
        #
        # Compares TransactiveRecord messages in mySignal, sentSignal, and receivedSignal.
        #
        # Updates properties convergenceFlags and converged based on comparison of calculated, received, and sent
        # TransactiveRecord messages.

        # NOTE: this method should not be called unless the balancing sub-problem and all the scheduling sub-problems
        # have been calculated and have converged.

        for time_interval in market.time_intervals:
            # Initialize a flag true (converged) in this time interval until proven otherwise.
            flag = True

            # Find the TransactiveSignals in this indexed active time interval.
            sent_time = self.last_sent_signals[market].signal_time
            sent_signal_curve = self.last_sent_signals[market].get_by_interval(time_interval)
            received_time = self.last_received_signals[market].signal_time
            received_signal_curve = self.last_received_signals[market].get_by_interval(time_interval)
            prepared_signal_curve = self.prepared_signals[market].get_by_interval(time_interval)

            # Now, work through the convergence criteria.
            if not sent_signal_curve:
                # No signal has been sent in this time interval. This is the first convergence requirement.
                # Set the convergence flag false.
                _log.debug(f"Signal for time interval {time_interval.name}. Enable send flag.")
                flag = False

            # received and received AFTER last sent and there is a big diff b/w sent_signal and received_signal
            elif (received_signal_curve is not None and len(received_signal_curve) > 0 and received_time > sent_time
                  and TransactiveSignal.balance_points_differ(self.last_sent_signals[market], 
                                                              self.last_received_signals[market], time_interval,
                                                              self.convergence_threshold)):
                # One or more TransactiveRecord objects has been received in the indexed time interval, and it has been
                # received AFTER the last time a message was sent. These are preconditions for the second convergence
                # requirement. Function balance_points_differ() checks whether the sent and received signals differ
                # significantly. If all these conditions are true, the Neighbor is not converged.
                _log.debug(f"Received signal for time interval {time_interval.name} differs from sent signal.")
                flag = False

            # TODO: Find out why the timing does not work with t_threshold.
            # elif dt - sent_time >= t_threshold and conditions_have_changed(prepared_signal, sent_signal,
            #                                                                self.convergenceThreshold, self.name):
            elif TransactiveSignal.conditions_have_changed(self.prepared_signals[market], 
                                                           self.last_sent_signals[market], time_interval,
                                                           self.convergence_threshold):
                # Delay 5 min after last send AND More than 5 minutes have passed since the last time a signal was sent.
                # This is a precondition to the third convergence criterion.
                # Function conditions_have_changed() returns true if mySignal (prepared_signal)
                # and the sentSignal (sent_signal) differ significantly, meaning that local conditions have changed
                # enough that a new, revised signal should be sent.
                _log.debug(f"Local conditions have changed for time interval {time_interval.name}.")
                flag = False

            self.convergence_flags.update(time_interval, flag)

        # If any of the convergence flags in active time intervals is false, the overall convergence flag should be set
        # false, too. Otherwise, true, meaning the coordination sub-problem is converged with this Neighbor.
        # TODO: This works but is ugly. Measurement should be an Iterator to allow "any(self.convergent_flags)".
        self.converged = False if any(self.convergence_flags.values) else True

        _log.debug(f"Convergence flags for {self.name} are {self.convergence_flags.log_report}")
        _log.debug(f"Convergence flag for {self.name} is {self.converged}.")

    # SEALED - DO NOT MODIFY
    # Have object schedule its power in active time intervals
    def schedule(self, market):
        self.update_dc_threshold(market)

        # If the object is a Neighbor give its vertices priority
        self.update_vertices(market)
        self.schedule_power(market)

        # Have the objects estimate their available reserve margin
        self.calculate_reserve_margin(market)

    def schedule_power(self, market):
        # Calculate power for each time interval
        #
        # This is a basic method for calculating power generation of consumption in each active time interval. It infers
        # power generation or consumption from the supply or demand curves that are represented by the neighbor's active
        # vertices in the active time intervals.
        #
        # This strategy should is anticipated to work for most neighbor model objects. If additional features are
        # needed, child neighbor models must be created and must redefine this method.
        #
        # PRESUMPTIONS:
        # - All active vertices have been created and updated.
        # - Marginal prices have been updated and exist for all active intervals.
        #
        # INPUTS:
        # market - Market object
        #
        # OUTPUTS:
        # updates array self.scheduledPowers

        for time_interval in market.time_intervals:

            # Find the marginal price for the indexed time interval
            mp = market.marginal_prices.get_by_interval(time_interval)
            marginal_price = market.price_model.get(time_interval.start_time)[0] if mp is None else mp.value

            # Find the power that corresponds to the marginal price according to the set of active vertices in the
            # indexed time interval. Function Production() works for any power that is determined by its supply curve or
            # demand curve, as represented by the object's active vertices.
            self.scheduled_powers.update(time_interval, self.production(marginal_price, time_interval))  # [avg. kW]

    def update_dc_threshold(self, market):
        # Keep track of the month's demand-charge threshold
        #
        # Pseudocode:
        # 1. This method should be called prior to using the demand threshold. In reality, the threshold will change
        #    only during peak periods.
        # 2a. (preferred) Read a meter (see MeterPoint) that keeps track of an averaged power. For example, a
        #    determinant may be based on the average demand in a half hour period, so the MeterPoint would ideally track
        #    that average.
        # 2b. (if metering unavailable) Update the demand threshold based on the average power in the current time
        #    interval.

        # Find the MeterPoint that is configured to measure average demand for this Neighbor. The determination is
        # based on the meter's MeasurementType.
        # TODO: If we are supposed to have a specific meter point for this, why not just have a variable for it?
        mtr = [x for x in self.meter_points if x.measurement_type == MeasurementType.AverageDemandkW]
        mtr = mtr[0] if len(mtr) > 0 else None

        if mtr is None:
            # TODO: Would it be better to always take the max of 0.0, self.demand_threshold,
            #  mtr.current_measurement (or maybe max in its buffer), and prior_market_powers?
            #  If the prior market had a higher power than the meter, we would want to know that, right?
            # No appropriate MeterPoint was found. The demand threshold must be inferred.
            prior_market_ref = market.prior_market_in_series
            if prior_market_ref is None:
                return
            if callable(prior_market_ref):
                prior_market = prior_market_ref()
            else:
                prior_market = prior_market_ref
            if prior_market is None:
                return
            prior_mkt_pwrs = [v.value for k, v in self.scheduled_powers.all if k.market == prior_market]
            self.demand_threshold = max([0.0, self.demand_threshold, *prior_mkt_pwrs]) if prior_mkt_pwrs else float('inf')
            # TODO: Why do we go to inf if there are not prior market powers. There could still be a demand_threshold,
            #  and the logic seems rather different than maxing that with 0....
        else:
            # An appropriate MeterPoint was found. The demand threshold may be updated from the MeterPoint.
            self.demand_threshold = max([0.0, self.demand_threshold, mtr.current_measurement or 0.0])  # [avg.kW]

        # The demand threshold should be reset in a new month. First find the current month number mon.
        mon = Timer.now().month

        if mon != self.demand_month:
            # This must be the start of a new month. The demand threshold must be reset. For now, "resetting" means
            # using a fraction (e.g., 80%) of the final demand threshold in the prior month.
            self.demand_threshold = self.demand_threshold_coef * self.demand_threshold
            self.demand_month = mon

    def update_vertices(self, market):
        """ Update the active vertices that define Neighbors' residual flexibility in the form of supply/demand curves.

            The active vertices of non-transactive neighbors are relatively constant. Active vertices must be created
             for new active time intervals. Vertices may be affected by demand charges, too, as new demand-charge
             thresholds are becoming established.

            The active vertices of transactive neighbors are also relatively constant. New vertices must be created for
             new active time intervals. But active vertices must also be checked and updated whenever a new transactive
             signal is received.

            Active vertices are defined from received signals.
             Then methods are applied to include and remove the impacts of marginal losses and demand charges.

            PRESUMPTIONS:
            - time intervals are up-to-date
            - at least one default vertex has been defined, should all other efforts to establish meaningful vertices fail

            INPUTS:
                market - Market object

            OUTPUTS:
                Updates self.activeVertices - an array of IntervalValues that contain Vertex() structs
        """

        # Recall the current demand threshold that applies to this neighbor in this month, and assign it to the active
        #  threshold that may increase while indexing through the time intervals.
        active_threshold = self.demand_threshold

        for time_interval in market.time_intervals:
            # Check for transactive records in the indexed time interval and market.
            received_signal: TransactiveSignal = self.last_received_signals.get(market)
            received_sd_curve: SDCurve = (deepcopy(received_signal.get_by_interval(time_interval))
                                          if received_signal else None)

            if not self.transactive or not received_sd_curve:
                # Note: Where non-transactive neighbor boundary conditions are more sophisticated, it may be necessary
                #  or desirable to override this update_vertices method or make default_vertices a property.
                self.active_vertices.update(time_interval, self.default_vertices, archive=False, replace=True)
                if self.transactive:
                    # No records for an interval should be an abnormal condition except on startup of iterative markets.
                    _log.warning(f'No received transactive signal was found for transactive neighbor {self.name}'
                                 f' in time interval {time_interval.name}')

            else:  # The neighbor is transactive and at least one vertex has been received for this time interval.
                # Re-create active vertices from the received transactive records.
                received_sd_curve, _ = received_sd_curve.check_rationality(action="fix_up")

                # TODO: Should this block be added to check_rationality?
                # Check for and correct treatment of a lone default vertex marginal price.
                if len(received_sd_curve) == 1:
                    received_sd_curve.vertices[0].price = float('inf')
                self.active_vertices.update(time_interval, received_sd_curve, archive=False, replace=True)

            # At this point, active vertices have been created, regardless whether the neighbor is transactive.
            #  These will be acted upon to include the impacts of marginal losses and demand charges.
            active_sd_curve =self.active_vertices.get_by_interval(time_interval)

            # Include the impacts of marginal losses for power that is RECEIVED from this neighbor.
            # Note: This impact can be turned off by setting the neighbor's loss_factor = 0.
            if self.loss_factor != 0:
                self.include_marginal_losses(active_sd_curve)

            # Include the impacts of demand charges that are imposed on any power that is RECEIVED from this neighbor.
            # Note: This logic may be turned off by simply setting the neighbor's demand_rate = 0.
            if self.demand_rate != 0:
                self.include_demand_charges(sd_curve=active_sd_curve, threshold=active_threshold)
                active_threshold = self._get_new_threshold(active_sd_curve, active_threshold, market, time_interval)

        #self.active_vertices.archive()

    def _get_new_threshold(self, sd_curve, current_threshold, market, time_interval):
        # Update the active threshold with the current scheduled power.
        #   If the power is not yet scheduled (price is not known), the power must be inferred:
        if not (current_scheduled_power := self.scheduled_powers.get_by_interval(time_interval)):
            predicted_market_price = market.price_model.get(time_interval.start_time)[0]
            current_scheduled_power = sd_curve.power_from_vertices(predicted_market_price)
        # The temporary active threshold is updated within the series of time intervals in this market object.
        #    - NOTE: self.demand_threshold, itself, is updated from prior markets in update_dc_threshold().
        new_threshold = max(current_threshold, current_scheduled_power)
        return new_threshold

    def prep_transactive_signal(self, market):
        # Prepare transactive records to send to a transactive neighbor. The prepared transactive signal should
        # represent the residual flexibility offered to the transactive neighbor in the form of a supply or demand
        # curve.
        # NOTE: the flexibility of the prepared transactive signals refers to LOCAL value. Therefore, this method does
        # not make modifications for power losses or demand charges, both of which are being modeled as originating with
        # the RECIPIENT of power.
        # FUTURE: The numbers of vertices may be restricted to emulate various auction mechanisms.
        #
        # ASSUMPTIONS:
        # - The local system has converged, meaning that all asset and neighbor powers have been calculated
        # - Neighbor and asset demand and supply curves have been updated and are accurate. Active vertices will be used
        #   to prepare transactive records.
        #
        # INPUTS:
        # market - Market object
        # this_transactive_node - Agent's TransactiveNode object
        #
        # OUTPUTS:
        # - Updates mySignal property, which contains transactive records that are ready to send to the transactive
        # neighbor
        #
        # 200529DJH: Simplifying the structure for Version 3. The net market vertices representing the current balance
        #            point--indicated by Record #0--is not easily identified in an auction because marginal prices are
        #            speculative, and modeled neighbor objects do not truly schedule before a price is returned from an
        #            auction. It is still easy to find the soft extremes of the offered flexibility though. In this
        #            revision,
        #            (1) all the summed vertices in a viable range are turned into transactive records,
        #            (2) a lone vertex is presumed to be Record #0 and is assigned to infinity (i.e., no flexibility),
        #            (2) make sure that a vertex exists at the minimum soft constraint and assign Record #1
        #            (3) make sure that a vertex exists at the maximum soft constraint and assign Record #2
        #            (2) assign record numbers to other transactive records in range [3, 4, ...]. (Not worrying about
        #                numbering continuity and hoping this will not be problematic.
        # 200611DJH: Completed troubleshooting. Errors were found.
        #            (1) Check that residual vertices are in range should have tested powers, not prices.
        #            (2) Tests were found to have relied on scheduled powers, which dependency has been reduced. The
        #                method now makes the prepared transactive records much more like the residual active vertices.
        #                Tests were necessarily changed to achieve consistency.
        #            (3) There may have been an issue in original code that used hard constraints instead of the soft
        #                ones. The minimum and maximum powers will now be limited to either the hard constraint or the
        #                soft constraint from the active vertices, whichever is narrower.
        # 200803DJH: The effects of marginal losses and demand charges that were incorporated into this neighbor's
        #            active vertices should be removed during the creation of mySignal that is ready to send. The
        #            general principle is that all sent, prepared, and received transactive signals should remain always
        #            in the perspective of the remote neighbor.
        #            - Losses of local power due to inefficient importation should be re-added for the perspective of
        #              the remote neighbor as power is IMPORTED from the remote neighbor.
        #            - Increased marginal price of imported power from this remote neighbor should be restored again
        #              to lower prices at the remote neighbor. This refers now to energy that is IMPORTED from the
        #              remote neighbor.
        #            - Demand charges added to peak power supply must be removed from peak power to be imported from
        #              the remote neighbor.
        #            A general principle should be that an equilibrium point is unchanged by the importation and
        #            exportation of signals until an actual perturbation occurs. The steps are, in each time interval,
        #            (1) Use method 'sum_vertices()' to find the supplementary response vertices for the neighbor.
        #            (2) Act on the vertices to REMOVE impacts of demand charges and marginal losses in this copy.
        #            (3) Use the modified vertices to create transactive records stored in 'mySignal' ready to send to
        #                the remote neighbor.
        # Ensure that this is a transactive neighbor.
        if not self.transactive:
            return

        # An active threshold is initialized here and used during the assessment of demand charges.
        active_threshold = -self.demand_threshold

        for time_interval in market.time_intervals:

            # Create the vertices of the net supply or demand curve, EXCLUDING this transactive neighbor.
            #     This SDCurve should already be a deep copy of any vertices from which it was originated.
            #  NOTE: It is important that the transactive neighbor is excluded.
            sd_curve: SDCurve = market.sum_vertices(time_interval, object_to_exclude=self)

            # This should be rare, Warn if no vertices are found.
            if sd_curve is None:
                _log.warning(f'No summed vertices were found in method prep_transactive_signal for neighbor '
                             f'{self.name} in time interval {time_interval.name}')
                return

            # Apply the minimum and maximum power constraints to the vertices.
            # At this point, the vertices will include any flexibility, stated from the perspective of the local agent.
            sd_curve.apply_constraints_and_bounds(self.minimum_power, self.maximum_power)

            # Remove the impacts of any demand charges that were included at the local node but are not relevant at the
            # remote agent location.
            # NOTES:
            #   * The effects of demand charges may be ignored if the demand rate is set to 0.
            #   * This correction is done before correcting for marginal losses because demand charges are
            #       presumed to apply to actual metered demand.
            if self.demand_rate != 0:
                scheduled_power = self.scheduled_powers.get_by_interval(time_interval)
                if scheduled_power:
                    active_threshold = min(active_threshold, scheduled_power)
                self.remove_demand_charges(sd_curve=sd_curve, threshold=active_threshold)

            # Remove the impacts of marginal losses to place the vertices into the perspective of the remote neighbor to
            # which a transactive signal will be sent.
            # NOTE:
            #   * The effects of losses can be ignored by making the loss factor = 0.
            if self.loss_factor != 0:
                self.remove_marginal_losses(sd_curve)

            # The vertices are now suitable for creating transactive records representing the remote agent perspective.
            #  Store the corrected vertices in prepared_signals, where they are ready to send to the remote neighbor.
            if market not in self.prepared_signals:
                self.prepared_signals[market] = TransactiveSignal(market=market, neighbor=self)
            self.prepared_signals[market].update(time_interval, sd_curve)

    def send_transactive_signal(self, market, include_schedule=False):
        # Send transactive records to a transactive neighbor.
        #
        # Retrieves the current transactive records, formats them into a table, and "sends" them to a text file for the
        # transactive neighbor. The property mySignal is a storage location for the current transactive records, which
        # should capture at least the active time intervals' local marginal prices and the power that is scheduled to be
        # received from or sent to the neighbor.
        # Records can also capture flex vertices for this neighbor, which are the supply or demand curve, less any
        # contribution from the neighbor. Transactive record #0 is the scheduled power, and other record numbers are
        # flex vertices. This approach anticipates that transactive signal might not include all time intervals or
        # replace all records. The neighbor similarly prepares and sends transactive signals to this location.
        # this_transactive_node - Agent's TransactiveNode object
        # **************************************************************************************************************
        # 191212DJH: This appears to bypass most of my original code and dumps transactive records into some sort of
        # message that is probably meaningful to Volttron. This may work for a Volttron environment, but agents cannot
        # all be presumed to be run on Volttron platforms.

        # If neighbor is non-transactive, warn and return. Non-transactive neighbors do not communicate transactive
        # signals.
        if not self.transactive:
            """
            _log.warning(
                'Non-transactive neighbors do not send transactive signals. No signal is sent to %s.' % self.name)
            """
            return

        # Collect current transactive records concerning myTransactiveNode.
        prepared_signal = self.prepared_signals.get(market)
        if not prepared_signal or prepared_signal.empty:  # No signal records are ready to send
            _log.warning(f"No transactive records were found. No transactive signal can be sent to {self.name}.")
            return

        # Publish the signal (if TN subclass implements messaging).
        self.tn.issue_signal(self.name,
                             prepared_signal.generate_signal(self.scheduled_powers if include_schedule else None))

        # Save the sent signal as a copy of the calculated set that was drawn upon by this method (i.e., prepared_signal).
        self.last_sent_signals[market] = prepared_signal

        # Save the newly sent transactive records.
        self.last_sent_signals[market].archive()

        # Return transactive records to allow further processing by subclasses.
        return prepared_signal

    def receive_transactive_signal(self, serialized_signal: str):
        """Receive and save transactive records from a transactive Neighbor."""
        if not self.transactive:
            _log.warning('Transactive signals are not expected to be received from non-transactive neighbors. '
                         'No signal is read.')
            return

        signal_dict = json.loads(serialized_signal)
        parsed_signal = TransactiveSignal.parse_signal(self.tn, signal_dict)
        self.last_received_signals[parsed_signal.market] = parsed_signal

    def update_costs(self, market):
        """
        Have model object update and store its costs
        191217DJH: This method was originally in an abstract Model class. The class structure is being simplified. Class
        Model will be deleted.
        NOTE: THIS METHOD DRIVES THE UPDATING OF COSTS BY CALLING OTHER METHODS. THERE SHOULD BE NO REASON TO CHANGE
        THIS METHOD. CHANGES TO THIS METHOD MAY MAKE THE SYSTEM (EVEN MORE) UNSTABLE!
        :param self:
        :param market: Agent's Market object
        :return:
        """

        # Initialize sums of production and dual costs.
        self.total_production_cost = 0.0
        self.total_dual_cost = 0.0

        # Have object update and store its production and dual costs in each active time interval
        self.update_production_costs(market)
        self.update_dual_costs(market)

        # Sum total production and dual costs through all time intervals.
        self.total_production_cost = self.production_costs.total(market)
        self.total_dual_cost = self.dual_costs.total(market)

    def include_marginal_losses(self, sd_curve: SDCurve):
        # This method corrects supply from neighbors based on transport inefficiency and the impact of transport losses
        # the effective marginal price. While all transactive signals should be stated from the perspective of the
        # remote neighbor, the active vertices should reflect the following modifications:
        # (1) Supply power is reduced to remove transport energy losses.
        # (2) Marginal price in increased to include the impact of marginal losses.
        # INPUTS:
        # - vertices: A list of vertex struct objects. Typically, these would be a set of raw vertex objects that have
        #             been derived from received transactive records. The remote neighbor supplies these records from
        #             its own perspective at the border of its circuit, excluding transport. This method will modify and
        #             return updated vertices. NOTE: the set of vertices should be for precisely one time interval.
        # - threshold: Power demand threshold this time interval.
        #
        # USES:
        # - self.maximumPower:
        # - self.lossFactor
        #
        # OUTPUTS:
        # - corrected_vertices: Vertex objects as corrected by this method to have reduced supply power and increased
        #                       marginal price. The caller is responsible to store these values as active vertices.
        #
        # 200702DJH: I had originally included the implications of transport losses within the base neighbor class
        #            methods. I'm finding that a more modular approach may be useful if the template methods are to
        #            facilitate diverse market rules and procedures. Therefore, I am introducing methods to include and
        #            later remove implications of losses and demand charges. These effects do not exist upstream of the
        #            local transactive node agent, but they must be accounted in the local prices. I'm concluding that
        #            the effects should be removed for the upstream neighbor's perspective. Furthermore, the three local
        #            transactive signal representations ("my," "received," and "sent" signals) should always be stated
        #            in respect to the neighbor's, not the local agent's, perspective. These modular methods should be
        #            invoked to correct this neighbor model's active vertices, which should be stated in respect to the
        #            local transactive node and agent.
        #            The method is being defined as a function to help make sure it is not applied multiple times.
        #            This revision corrects some errors implemented in prior versions. The impact on LMP is now properly
        #            formulated from marginal losses.
        #            The price and power corrections are based on a careful review of this impacts in a white paper
        #            "Addressing the Marginal Value of Transport Losses."

        if self.maximum_power is None or self.maximum_power <= 0:
            if not getattr(self, '_loss_warning_reported', False):
                _log.warning('Method Neighbor.include_losses requires that a full maximum power value > 0 be '
                             'assigned for neighbor %s. No losses were applied.', getattr(self, 'name', 'unknown'))
                self._loss_warning_reported = True
            return

        if type(self.loss_factor) != float or self.loss_factor <= 0:
            _log.warning('Property loss_factor must be a small positive float for neighbor %s. No losses were applied.',
                         getattr(self, 'name', 'unknown'))
            return

        if not isinstance(sd_curve, SDCurve):
            _log.warning('This method acts on SDCurve objects. Received %s for neighbor %s. No losses were applied.',
                         type(sd_curve), getattr(self, 'name', 'unknown'))
            return

        # Get on with the business of reviewing and correcting the input vertices.
        for vertex in sd_curve.vertices:
            # Losses apply to only imported, not exported, power in the current practice. Calculate the effective
            # received power and price.
            # Assumptions:
            # - The supplier agrees with the value of lossFactor.
            # - corrected_vertex.quantity is currently the power delivered from the supply side.
            # - maximumPower is the full-load transport capacity, stated from the supplier's perspective.
            # - corrected_vertex.price is currently the LMP of delivered supply, excluding effects of transport losses.
            if vertex.quantity > 0:
                power = vertex.quantity * (1 - self.loss_factor * (vertex.quantity / self.maximum_power))
                # Make sure that the reduced power is not greater than the maximum capacity referred to the demand side.
                if power > self.maximum_power * (1 - self.loss_factor):  # TODO: Check that this only gets applied to supply (positive power).
                    power = self.maximum_power * (1 - self.loss_factor)
                # Correct the supplier's LMP to include marginal losses.
                # 210907DJH: Checked this next calculation of marginal loss price based on supply neighbor's marginal
                #            price and its stated power. If cost is taken as the product of supply neighbor's marginal
                #            price and supply energy, this marginal price at the recipient location is equivalent to the
                #            first variation of the cost with change in received power.
                vertex.price = vertex.price / (1 - 2 * (vertex.quantity / self.maximum_power) * self.loss_factor)
                vertex.quantity = power

    def remove_marginal_losses(self, sd_curve :SDCurve):
        # Remove impacts of marginal losses from method include_marginal_losses(). This is usually done while preparing
        # the transactive signal and its transactive records from the neighbor's active vertices. By practice,
        # transactive signals should always be sent and received using the remote neighbor's perspective. Impacts exist
        # only among vertices with demand (i.e., p < 0).

        for vertex in sd_curve.vertices:
            if vertex.quantity < 0:
                # ASSUMPTIONS:
                # - self.maximumPower is the transport actual constraint power magnitude from the supply perspective.
                # - self.lossFactor is the same for both supply and demand.
                # - vertex.power is received as the received demand-side power, excluding losses.
                # - vertex.marginalPrice is the local marginal price that has been increased to account for marginal
                #   losses.
                # Calculated the supplied power from the supply-side perspective. It is a negative value because of the
                # sign convention for demand.
                # 210907DJH: Confirmed that this calculated power indeed recovers the supply neighbor power as a
                #            function of local demand from this neighbor.
                power = -(self.maximum_power / (2 * self.loss_factor)) \
                    * (1 - (1 - 4 * (-vertex.quantity / self.maximum_power) * self.loss_factor) ** 0.5)

                if power < -self.maximum_power:
                    power = -self.maximum_power

                # 210907DJH: I found that this next function that finds a supply neighbor's marginal price from the
                #            local  agent's marginal price and received power does not perfectly recover the price and
                #            thereby remove all the impacts of losses. This may have been due to its use of the recently
                #            estimated neighbor supply. The discrepancy was minor, but it could accumulate in iterative
                #            systems.
                #            The replacement function was found to perfectly find the neighbor's original marginal
                #            price, less loss impacts. It uses the local import power instead of the estimated
                #            neighbor's export power.
                # vertex.marginalPrice = vertex.marginalPrice * (1 - 2 * (-power / self.maximumPower) * self.lossFactor)
                vertex.price = vertex.price * (1 + 4 * self.loss_factor * vertex.quantity / self.maximum_power) ** 0.5
                vertex.quantity = power

    def include_demand_charges(self, sd_curve: SDCurve, threshold: float):
        # 200731DJH: This method acts on and modifies a set of vertices to include demand charges.
        # Important: This method should be called no more than once. It will typically be called once as a transactive
        #            signal is being RECEIVED from a neighbor and applied to only imported supply powers.
        # Note: The impacts from demand charges are removed by method Neighbor.remove_demand_charges() which should be
        #       applied once while preparing transactive signals to send to the neighbor.
        # INPUTS:
        # - vertices: A set of Vertex objects in the same time interval.
        # - threshold: A power level at which demand charges will be applied to power that is received from a given
        #              neighbor object. [kW]
        # RETURNS:
        # - corrected_vertices: A set of corresponding Vertex objects that has demand charges applied to peak powers in
        #   this time interval.

        # If the demand threshold power lies above the maximum vertex power, this is all moot.
        #   Return the received vertices unchanged.
        if threshold > sd_curve.max_quantity():
            return

        # Demand charges only apply to supply vertices (i.e., quantity > 0) from this remote neighbor.
        if threshold < 0:
            threshold = 0

        # If any vertices exist on the threshold, a vertex will need to be created at the lowest price of these.
        if not (threshold_vertices := sd_curve.get_vertices_at_quantity(threshold)).empty:
            lower_threshold_price = threshold_vertices.min_price
            upper_threshold_price = threshold_vertices.max_price
            # Remove vertices from the SDCurve that lie on the threshold. These will be recreated.
            sd_curve.vertices -= threshold_vertices.vertices
        else:
            # Interpolate a price at the threshold power before any of their marginal prices are changed.
            lower_threshold_price = upper_threshold_price = sd_curve.price_from_vertices(threshold)

        # Add the demand rate to the marginal price of any active supply vertices that lie ABOVE the threshold.
        for vertex in sd_curve.vertices:
            if vertex.quantity > threshold:
                vertex.price += self.demand_rate

        # Create vertices at the threshold. The two criteria make sure that no redundant vertices are created.
        if sd_curve.min_quantity() < threshold <= sd_curve.max_quantity():
            # Add a lower threshold vertex unless the threshold is already at the minimum quantity.
            sd_curve.update(Vertex(price=lower_threshold_price, cost=0, quantity=threshold))

        if sd_curve.min_quantity() <= threshold < sd_curve.max_quantity():
            # Add an upper threshold vertex unless the threshold is already at the maximum quantity.
            sd_curve.update(Vertex(price=upper_threshold_price + self.demand_rate, cost=0, quantity=threshold))

    def remove_demand_charges(self, sd_curve: SDCurve, threshold):
        # 200804DJH: If the demand lies at or below the threshold, the demand rate is removed from the vertex's marginal
        #            price. This is done in preparation of transactive signals that are to be sent to the remote
        #            neighbor.
        #            Demand charges were included when the neighbor's supply to this agent exceeded a threshold power;
        #            Therefore, the demand charges must be removed from this agent's demand if it lies at or below the
        #            threshold.
        #            The threshold is always stated from the local agent's perspective.

        # This is all moot if the threshold is so low that it will not cause demand prices to be affected.
        #   Return with vertices unchanged.
        if threshold < sd_curve.min_quantity():
            return

        # Demand charges only apply to demand from this remote neighbor.
        if threshold > 0:
            threshold = 0

        # If any vertices exist on the threshold, a vertex will need to be created at the highest price of these.
        if not (threshold_vertices := sd_curve.get_vertices_at_quantity(threshold)).empty:
            lower_threshold_price = threshold_vertices.min_price
            upper_threshold_price = threshold_vertices.max_price
            # Remove vertices from the SDCurve that lie on the threshold. These will be recreated.
            sd_curve.vertices -= threshold_vertices.vertices
        else:
            # Interpolate a price at the threshold power before any of their marginal prices are changed.
            lower_threshold_price = upper_threshold_price = sd_curve.price_from_vertices(threshold)

        # Subtract the demand rate from the marginal price of any active demand vertices that lie BELOW the threshold.
        #   Note: the threshold should normally be negative, meaning it is a demand level.
        for vertex in sd_curve.vertices:
            if vertex.quantity < threshold:
                vertex.price -= self.demand_rate

        # Create vertices at the threshold. The two criteria make sure that no redundant vertices are created.
        if sd_curve.min_quantity() <= threshold < sd_curve.max_quantity():
            # Add an upper threshold vertex unless the threshold is already at the maximum quantity.
            sd_curve.update(Vertex(price=upper_threshold_price, cost=0, quantity=threshold))

        if sd_curve.min_quantity() < threshold <= sd_curve.max_quantity():
            # Add a lower threshold vertex unless the threshold is already at the minimum quantity.
            sd_curve.update(Vertex(price=lower_threshold_price - self.demand_rate, cost=0, quantity=threshold))

    @property
    def table_name(self):
        return 'neighbor'

    def get_record(self) -> dict:
        record = super(Neighbor, self).get_record()
        record.update({
            'id': self.id,
            # 'last_updated': self.last_updated, TODO: Did this come from old schema?
            'convergence_threshold': self.convergence_threshold,
            'demand_month': self.demand_month,
            'demand_rate': self.demand_rate,
            'demand_threshold': self.demand_threshold,
            'demand_threshold_coefficient': self.demand_threshold_coef,
            'effective_impedance': self.effective_impedance,
            'friend': self.friend,
            'loss_factor': self.loss_factor,
            'transactive': self.transactive,
            'up_or_down': self.up_or_down
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(Neighbor, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "neighbor",
                "inherits": "entity",
                "columns": [
                    {"name": "id", "foreign_key": "entity.id", "kwargs": {"primary_key": True}},
                    # {"name": "last_updated", "type": "DateTime"},
                    {"name": "convergence_threshold", "type": "Float"},
                    {"name": "demand_month", "type": "Integer"},
                    {"name": "demand_rate", "type": "Float"},
                    {"name": "demand_threshold", "type": "Float"},
                    {"name": "demand_threshold_coefficient", "type": "Float"},
                    {"name": "effective_impedance", "type": "Float"},
                    {"name": "friend", "type": "Boolean"},
                    {"name": "loss_factor", "type": "Float"},
                    {"name": "transactive", "type": "Boolean"},
                    {"name": "up_or_down", "enumeration": "Direction"}
                ]
            }
        ])
        return definitions
