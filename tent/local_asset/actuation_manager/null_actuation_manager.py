import logging

from .base import ActuationManager

_log = logging.getLogger(__name__)


class NullActuationManager(ActuationManager):
    def __init__(self, *args, **kwargs):
        #super().__init__(*args, **kwargs)
        pass

    def actuate(self, mkt):
        _log.info('NullActuationManager: actuate called, but no actuation is performed.')

    def release(self, mkt):
        _log.info('NullActuationManager: release called, but no actuation is performed.')


