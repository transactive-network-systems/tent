"""
Copyright (c) 2022, Battelle Memorial Institute
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

from tent.utils.helpers import linspace

from tent.config import Coefficients24, Setting

from .base import Model

_log = logging.getLogger(__name__)


OAT = "OutdoorAirTemperature"
CSP = "ZoneCoolingTemperatureSetPoint"
HSP = "HSP"
TIN = "ZoneTemperature"


class Thermostat(Model):
    class Config(Model.Config):
        c1: Coefficients24 = Setting(..., reconfigurable=True,
                                     description='Regression coefficient of the cooling set point, per hour of day.')
        c2: Coefficients24 = Setting(..., reconfigurable=True,
                                     description='Regression coefficient of the room temperature, per hour of day.')
        c3: Coefficients24 = Setting(..., reconfigurable=True,
                                     description='Regression coefficient of the outdoor air temperature, per hour of '
                                                 'day.')
        c4: Coefficients24 = Setting(..., reconfigurable=True, description='Regression intercept, per hour of day.')
        rated_power: float = Setting(..., ge=0, reconfigurable=True,
                                     description='[kW] power of the unit at a duty cycle of 1.')
        nominal_setpoint: float = Setting(22.8, reconfigurable=True,
                                          description='[degC] cooling set point around which flexibility is offered.')
        unoccupied_set_point: float | None = Setting(None, reconfigurable=True,
                                                     description='[degC] set point while unoccupied; null means the '
                                                                 'nominal set point.')
        max_set_point_offset: float = Setting(2.0, ge=0, reconfigurable=True,
                                              description='[degC] largest offset from the nominal set point offered.')
        oat: float = Setting(0.0, description='[degC] outdoor air temperature before the first measurement.')
        csp: float = Setting(22.8, description='[degC] cooling set point before the first measurement.')
        room_temp: float = Setting(22.8, description='[degC] room temperature before the first measurement.')
        actuation_key: str | None = Setting(None, description='Point the actuation manager writes the set point to.')

    def __init__(self, config, **kwargs):
        super(Thermostat, self).__init__(config, **kwargs)
        cfg = self.config
        self.name = "Thermostat"
        self.c1 = list(cfg.c1)
        self.c2 = list(cfg.c2)
        self.c3 = list(cfg.c3)
        self.c4 = list(cfg.c4)
        self.nominal_set_point = cfg.nominal_setpoint
        self.unoccupied_set_point = (cfg.unoccupied_set_point if cfg.unoccupied_set_point is not None
                                     else self.nominal_set_point)
        self.max_set_point_offset = cfg.max_set_point_offset
        self.oat = cfg.oat
        self.csp = cfg.csp
        self.room_temp = cfg.room_temp
        self.current_time = None
        self.coefficients = {"c1", "c2", "c3", "c4"}
        self.rated_power = cfg.rated_power
        self.n_points = cfg.demand_curve_points
        self.subscribe_key = cfg.subscribe_key
        self.error = False
        self.actuation_key = cfg.actuation_key

    def _apply_reconfiguration(self, cfg, changed: set[str]) -> None:
        super()._apply_reconfiguration(cfg, changed - {'nominal_setpoint', 'unoccupied_set_point'})
        if changed & {'nominal_setpoint', 'unoccupied_set_point'}:
            self.nominal_set_point = cfg.nominal_setpoint
            self.unoccupied_set_point = (cfg.unoccupied_set_point if cfg.unoccupied_set_point is not None
                                         else self.nominal_set_point)

    def predict_flexibility(self, params=None):
        params = params if params else {}
        if not params.get('occupied'):
            min_set_point, max_set_point = self.unoccupied_set_point, self.unoccupied_set_point
        else:
            min_set_point, max_set_point = self.set_point_range()
        csp_flex = linspace(min_set_point, max_set_point, num=self.n_points)
        return [self.predict_power(params, csp) for csp in csp_flex]

    def predict_power(self, params=None, set_point=None):
        params = params if params else {}
        if not params.get('occupied'):
            csp = self.unoccupied_set_point
        else:
            csp = set_point if set_point else self.csp
        oat = self.oat if not params.get(OAT) else params.get(OAT)
        temp = self.room_temp if not params.get(TIN) else params.get(TIN)
        index = self.current_time.hour if not params.get('interval_time') else params.get('interval_time').hour
        duty_cycle = min([1, max([0, self._get_q(oat, temp, csp, index)])])
        power = duty_cycle * self.rated_power
        return -power

    def set_point_range(self, interval_start_time=None):
        set_point = self.nominal_set_point  # TODO: Extend for time_based schedule.
        return set_point - self.max_set_point_offset, set_point + self.max_set_point_offset

    def update_data(self, data, now):
        """
        Update current data measurements.
        """
        try:
            self.oat = data[OAT]
            self.csp = data[CSP]
            self.room_temp = data[TIN]
            self.current_time = now
            self.error = False
        except KeyError:
            _log.debug("Error for %s input data on subscribe_key %s", self.name, self.subscribe_key)
            self.error = True

    def _get_q(self, oat, temp, temp_stpt, index):
        # _log.debug(f'oat: {oat}, temp: {temp}, temp_stpt: {temp_stpt}, index: {index}')
        q = temp_stpt * self.c1[index] + temp * self.c2[index] + oat * self.c3[index] + self.c4[index]
        return q