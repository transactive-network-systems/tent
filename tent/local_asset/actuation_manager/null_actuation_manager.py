import logging

from .base import ActuationManager

_log = logging.getLogger(__name__)


class NullActuationManager(ActuationManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def actuate(self, mkt):
        pass

    def release(self, mkt):
        pass


