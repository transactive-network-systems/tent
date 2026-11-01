import datetime
import logging

from tent.config import Coefficients24, Setting

from .base import Model

_log = logging.getLogger(__name__)


class UncontrolledLoad(Model):
    class Config(Model.Config):
        uncontrolled_load_schedule: Coefficients24 = Setting(..., reconfigurable=True,
                                                             description='[kW] load per hour of day.')

    def __init__(self, config, **kwargs):
        super(UncontrolledLoad, self).__init__(config, **kwargs)
        self.actuation_key = None
        self.load_schedule = list(self.config.uncontrolled_load_schedule)
        self.n_points = self.config.demand_curve_points

    def _apply_reconfiguration(self, cfg, changed: set[str]) -> None:
        super()._apply_reconfiguration(cfg, changed - {'uncontrolled_load_schedule'})
        if 'uncontrolled_load_schedule' in changed:
            self.load_schedule = list(cfg.uncontrolled_load_schedule)

    def predict_flexibility(self, params=None):
        power = self.predict_power(params)
        return [power]*self.n_points

    def predict_power(self, params, set_point=None):
        interval_time: datetime = params.get('interval_time')
        index = interval_time.hour
        power = self.load_schedule[index]
        return -power

    @staticmethod
    def set_point_range(interval_start_time: datetime):
        return None, None

    def update_data(self, data, now):
        pass  # an uncontrolled load is driven by its schedule, not by measurements