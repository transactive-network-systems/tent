import logging

from .base import ActuationManager

_log = logging.getLogger(__name__)


class NullActuationManager(ActuationManager):
    Config = ActuationManager.Config

    def actuate(self, mkt):
        pass

    def release(self, mkt):
        pass


