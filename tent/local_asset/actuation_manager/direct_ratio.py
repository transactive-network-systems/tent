import logging

from . import ActuationManager

_log = logging.getLogger(__name__)


class DirectRatioActuationManager(ActuationManager):
    Config = ActuationManager.Config

    def actuate(self, mkt):
        super(DirectRatioActuationManager, self).actuate(mkt)
        if self.actuation_active:
            # TODO: Is price assignment just trying to find the first price in delivery?
            #  If so, there is now a Measurement.first method. If not, this needs to be reworked.
            price = [v.value for ti, v in mkt.marginal_prices.all if ti.start_time == mkt.transition_times.delivery][0]
            interval = next((ti for ti, sdc in self.parent.active_vertices.all
                             if ti.start_time == mkt.transition_times.delivery and not sdc.empty), None)
            if interval is None:
                return  # the asset offered no curve for the delivery interval, so there is nothing to map the price on
            # The asset's one price-to-set-point mapping (ModelFrameAsset.set_points_at_price): the cleared price's
            # position in the span of the curve it offered, clamped to it, and each model's set point at that position
            # of its range. The curve and the schedule come from the same mapping, so the devices are set to produce
            # the scheduled power. Forward-acting (cooling) for every model; see the asset for the mapping's terms.
            set_points = self.parent.set_points_at_price(price, interval, mkt)
            for key, model in self.parent.models.items():  # TODO: Generalize this away from models in ModelFrameAsset.
                if model.actuation_key and set_points.get(key) is not None:
                    self.direct_actuate(model.actuation_key, set_points[key])

    def direct_actuate(self, target_id, new_set_point):
        pass

    def release(self, mkt):
        super(DirectRatioActuationManager, self).release(mkt)
        for model in self.parent.models.values():  # TODO: Generalize this away from models in ModelFrameAsset.
            if model.actuation_key:
                self.direct_release(model.actuation_key)

    def direct_release(self, target_id):
        pass
