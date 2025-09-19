import logging

from tent.meter_point import MeterPoint

_log = logging.getLogger(__name__)


class PushMeterPoint(MeterPoint):
    def __init__(self, *args, **kwargs):
        super(PushMeterPoint, self).__init__(*args, **kwargs)

        self.tn.register_subscription()

    def set_up_subscription(self):
        # Subscribe to pushed meter readings.
        #
        # MeterPoints are updated when pushes are received.
        # Properties have been defined to keep track of the time of the last
        # update and the interval between updates.
        #
        # While this seems easy, meters will be found to be diverse and may use diverse standards and protocols. Create
        # subclasses and redefine this function as needed to handle unique conditions.
        pass
