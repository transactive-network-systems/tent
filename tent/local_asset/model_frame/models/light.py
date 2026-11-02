import logging

from datetime import datetime

from tent.utils.helpers import linspace

from tent.config import Coefficients24, Setting

from .base import Model

_log = logging.getLogger(__name__)


class Lighting(Model):
    class Config(Model.Config):
        rated_power: float = Setting(..., ge=0, reconfigurable=True, description='[kW] power at full output.')
        max_set_point_offset: float = Setting(0.1, ge=0, reconfigurable=True,
                                              description='[fraction of full output] largest dimming offset from the '
                                                          'scheduled level offered.')
        default_lighting_schedule: Coefficients24 = Setting(
            [1.0] * 24, reconfigurable=True,
            description='[fraction of full output] scheduled lighting level per hour of day.')
        actuation_key: str | None = Setting(None, description='Point the actuation manager writes the level to.')

    def __init__(self, config, **kwargs):
        super(Lighting, self).__init__(config, **kwargs)
        cfg = self.config
        self.rated_power = cfg.rated_power
        self.max_set_point_offset = cfg.max_set_point_offset
        self.n_points = cfg.demand_curve_points
        self.actuation_key = cfg.actuation_key
        self.lighting_schedule = list(cfg.default_lighting_schedule)

    def _apply_reconfiguration(self, cfg, changed: set[str]) -> None:
        super()._apply_reconfiguration(cfg, changed - {'default_lighting_schedule'})
        if 'default_lighting_schedule' in changed:
            self.lighting_schedule = list(cfg.default_lighting_schedule)

    def predict_flexibility(self, params=None):
        interval_time: datetime = params.get('interval_time')
        min_set_point, max_set_point = self.set_point_range(interval_time)
        csp_flex = linspace(min_set_point, max_set_point, num=self.n_points)
        return [self.predict_power(params, csp) for csp in csp_flex]

    def predict_power(self, params=None, set_point=None):
        interval_time: datetime = params.get('interval_time')
        index = interval_time.hour
        # TODO: should this consider current set point, if it is the current interval?
        if set_point is None:
            power = self.lighting_schedule[index]*self.rated_power
        else:
            power = set_point * self.rated_power
        return -power

    # TODO: Make nominal setpoint max and double default offset max.
    def set_point_range(self, interval_start_time: datetime):
        index = interval_start_time.hour
        set_point = self.lighting_schedule[index]
        return set_point - self.max_set_point_offset, set_point + self.max_set_point_offset

    def update_data(self, data, now):
        pass