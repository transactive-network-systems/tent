import json
import logging
import os
import stat
import sys
import traceback


class JsonFormatter(logging.Formatter):
    def format(self, record):
        dct = record.__dict__.copy()
        dct["msg"] = record.getMessage()
        dct.pop('args')
        exc_info = dct.pop('exc_info', None)
        if exc_info:
            dct['exc_text'] = ''.join(traceback.format_exception(*exc_info))
        return json.dumps(dct)


def is_a_pipe(fd):
    fd = getattr(fd, 'fileno', lambda: fd)()
    return stat.S_ISFIFO(os.fstat(fd).st_mode)


def setup_logging(level=logging.DEBUG, console=False):
    """ This function can be used to set up logging in non-VOLTTRON agent similar to how it is done in VOLTTRON.
         (This file is largely copied from the VOLTTRON source code with minor simplifications.)
         This function should not be used within the TENT library itself, as this would override the log handling
         specified at the application layer. It should instead be called from the entry-point file of agent-level code.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()

        if is_a_pipe(sys.stderr) and '_LAUNCHED_BY_PLATFORM' in os.environ:
            handler.setFormatter(JsonFormatter())
        elif console:
            # Below format is more readable for console
            handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        else:
            fmt = '%(asctime)s %(name)s %(levelname)s: %(message)s'
            handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(handler)
    root.setLevel(level)
