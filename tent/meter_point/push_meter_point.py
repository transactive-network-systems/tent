import abc
import logging

from datetime import datetime
from typing import Any, Callable

from tent.config import ConfigModel, Setting
from tent.meter_point import MeterPoint

_log = logging.getLogger(__name__)


class PushMeterPoint(MeterPoint):
    class Config(MeterPoint.Config):
        subscribe_key: str = Setting(..., min_length=1,
                                     description='Key the node subscribes to for this meter\'s readings.')

    def __init__(self, transactive_node, config: ConfigModel | None = None, **kwargs):
        cfg = self._configure(config, kwargs)
        super(PushMeterPoint, self).__init__(transactive_node, config=cfg)
        self.subscribe_key = cfg.subscribe_key

        self.tn.register_subscription(subscribe_key=self.subscribe_key, callback=self.handle_subscription)

    @abc.abstractmethod
    def handle_subscription(self, time_received: datetime, subscribe_key: str, message: Any) -> None:
        # Subscribe to pushed meter readings.
        #
        # MeterPoints are updated when pushes are received.
        # Properties have been defined to keep track of the time of the last
        # update and the interval between updates.
        #
        # While this seems easy, meters will be found to be diverse and may use diverse standards and protocols. Create
        # subclasses and redefine this function as needed to handle unique conditions.
        pass
