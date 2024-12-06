import json
import logging
import os
import stat
import sys
import traceback
import warnings


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
        if level != logging.DEBUG:
            # TODO: Why do we need InsecureRequestWarning? Is this relevant to TENT?
            # import it here so that when urllib3 imports the requests package, ssl would already got
            # monkey patched by gevent.
            # and this warning is needed only when log level is not debug
            from urllib3.exceptions import InsecureRequestWarning
            warnings.filterwarnings("ignore", category=InsecureRequestWarning)
        root.addHandler(handler)
    root.setLevel(level)
