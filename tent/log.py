import os
from enum import Enum

VOLTTRON_ENV = os.environ.get('VOLTTRON_ENV', False)
_log = None

if VOLTTRON_ENV:
    import logging
    from volttron.platform.agent import utils
    utils.setup_logging()
    _log = logging.getLogger(__name__)


class LogLevels(Enum):
    critical = 50
    error = 40
    warning = 30
    info = 20
    debug = 10
    notset = 0


def log_statement(message, level=LogLevels.debug):
    if VOLTTRON_ENV:
        if level == LogLevels.critical:
           _log.critical(message)
        elif level == LogLevels.error:
            _log.error(message)
        elif level == LogLevels.warning:
            _log.warning(message)
        elif level == LogLevels.info:
            _log.info(message)
        elif level == LogLevels.debug:
            _log.debug(message)
        else:
            _log.debug(message)
    else:
        if level == LogLevels.warning or level == LogLevels.error:
            Warning(message)
        else:
            print(message)

