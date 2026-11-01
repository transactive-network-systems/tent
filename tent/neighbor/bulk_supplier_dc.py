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

from datetime import date, datetime
from typing import Literal

from tent.config import ConfigModel, Setting
from tent.containers import SDCurve, Vertex
from tent.enumerations import MarketState, MeasurementType
from tent.neighbor import Neighbor
from tent.utils.const import bpa_demand_rate, bpa_energy_rate

_log = logging.getLogger(__name__)


class BulkSupplierDC(Neighbor):
    """
    A non-transactive bulk electricity supplier (modelled on BPA's relationship to a municipality) whose energy and
    demand rates depend on the month and on whether an hour is a heavy-load hour (HLH). HLH are 06:00 to 21:59 on every
    day but Sundays and the configured holidays; `holidays` is a list of ISO dates (the NERC holidays of the simulated
    years, for example "2018-01-01"). Without it no day is a holiday.
    """

    HEAVY_LOAD_HOURS = range(6, 22)

    class Config(Neighbor.Config):
        holidays: list[date] = Setting([], reconfigurable=True,
                                       description='ISO dates (YYYY-MM-DD) that are not heavy-load days.')
        transactive: Literal[False] = Setting(False, description='A bulk supplier is never transactive.')

    def __init__(self, transactive_node, config: ConfigModel | None = None, **kwargs):
        cfg = self._configure(config, kwargs)
        super(BulkSupplierDC, self).__init__(transactive_node, config=cfg)
        self.holidays: frozenset[date] = frozenset(cfg.holidays)

    def _apply_reconfiguration(self, cfg, changed: set[str]) -> None:
        super()._apply_reconfiguration(cfg, changed - {'holidays'})
        if 'holidays' in changed:
            self.holidays = frozenset(cfg.holidays)

    def is_heavy_load_hour(self, moment: datetime) -> bool:
        """True in a heavy-load hour: 06:00 to 21:59, not a Sunday, not a configured holiday."""
        if not isinstance(moment, datetime):
            raise TypeError(f'is_heavy_load_hour needs a datetime, not {type(moment).__name__}')
        return (moment.hour in self.HEAVY_LOAD_HOURS and moment.weekday() != 6
                and moment.date() not in self.holidays)

    def update_vertices(self, market):
        # Creates active vertices for a non-transactive neighbor, including demand charges.
        #
        # INPUTS:
        # mkt - Market object
        #
        # OUTPUTS:
        # - Updates self.active_vertices for active time intervals.
        #
        # 200804DJH: Method was revised to be more concise and consistent with Version-3 practices:
        #            - Active vertices are first created from the perspective of the bulk supplier.
        #            - New method 'include_marginal_losses()' is then used to modify the vertices to include the impacts
        #              of marginal losses. This reduces the available supply power and increases the effective marginal
        #              price to account for energy losses.
        #            - New method 'include_demand_charges()' is then used to include demand charges.
        #              - Correcting tracking of scheduled powers by demand-charge threshold, which had been turned off.

        # Get the maximum and minimum power for this neighbor [kW]
        maximum_power = self.maximum_power
        minimum_power = self.minimum_power

        # The maximum power property is meaningful for both imported (p>0) and exported (p<0) electricity, but this
        # formulation is intended for importation (power>0) from an electricity supplier. Warn the user and return if
        # the maximum power is negative.
        if maximum_power < 0 or minimum_power < 0:
            _log.warning(f'You should not use neighbor object {self.name}'
                         f' with class BulkSupplier_DC that represents a bulk supplier.')
            return

        # Cost coefficient a0. This is unavailable from a supply curve, so it must be determined directly from the
        # first, constant cost parameter. It does NOT affect marginal pricing [$/h].
        a0 = self.cost_parameters[0]

        # Initialize thresholds that will be used to apply demand charges.
        active_threshold = self.demand_threshold

        # Index through active time intervals.
        for time_interval in market.time_intervals:
            # This initialization belongs inside the time interval loop because subsequent values may
            #  be affected by prior ones when there are multiple market time intervals.

            # Find the month number for the indexed time interval start time. The month is needed for rate lookup
            # tables.
            month_number = time_interval.start_time.month

            if self.is_heavy_load_hour(time_interval.start_time):
                price = bpa_energy_rate[month_number - 1][0]  # HLH energy rate [$/kWh]
            else:
                price = bpa_energy_rate[month_number - 1][1]  # bpa_energy_rate(month_number, 2)

            # Initialize the SDCurve that is to become the active vertices for this interval.
            sd_curve = SDCurve(
                measurement=None,
                interval=time_interval,
                vertices=[
                    Vertex(price=price, quantity=minimum_power, cost=a0 + price * minimum_power),
                    Vertex(price=price, quantity=maximum_power, cost=a0 + price * maximum_power)
                ])

            # At this point the two vertices capture the remote supplier's perspective.

            # Modify the vertices to include the impacts of marginal losses.
            if self.loss_factor != 0:
                self.include_marginal_losses(sd_curve)

            # Modify the vertices to include the impacts of demand charges.
            # Make sure that the demand_rate is set to the current demand rate.
            # TODO: Should this really be changing an object attribute in a loop?
            self.demand_rate = bpa_demand_rate[month_number - 1][0]

            # Demand Charges only apply to HLH hours.
            if self.is_heavy_load_hour(time_interval.start_time) and self.demand_rate != 0:
                self.include_demand_charges(sd_curve=sd_curve, threshold=active_threshold)
                active_threshold = self._get_new_threshold(sd_curve, active_threshold, market, time_interval)

            # Update active vertices for the indexed time interval.
            self.active_vertices.update(time_interval, sd_curve, archive=False, replace=True)

        #self.active_vertices.archive()
