"""
Copyright (c) 2020, Battelle Memorial Institute
All rights reserved.
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
The views and conclusions contained in the software and documentation are those
of the authors and should not be interpreted as representing official policies,
either expressed or implied, of the FreeBSD Project.
This material was prepared as an account of work sponsored by an agency of the
United States Government. Neither the United States Government nor the United
States Department of Energy, nor Battelle, nor any of their employees, nor any
jurisdiction or organization that has cooperated in th.e development of these
materials, makes any warranty, express or implied, or assumes any legal
liability or responsibility for the accuracy, completeness, or usefulness or
any information, apparatus, product, software, or process disclosed, or
represents that its use would not infringe privately owned rights.
Reference herein to any specific commercial product, process, or service by
trade name, trademark, manufacturer, or otherwise does not necessarily
constitute or imply its endorsement, recommendation, or favoring by the
United States Government or any agency thereof, or Battelle Memorial Institute.
The views and opinions of authors expressed herein do not necessarily state or
reflect those of the United States Government or any agency thereof.

PACIFIC NORTHWEST NATIONAL LABORATORY
operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
under Contract DE-AC05-76RL01830
"""


import importlib
import logging
import re

from datetime import datetime, time, timedelta
from uuid import UUID
from enum import Enum

_log = logging.getLogger(__name__)

first_cap_re = re.compile(r'(.)([A-Z][a-z]+)')
all_cap_re = re.compile(r'([a-z\d])([A-Z])')

_DATETIME_FORMATS = ('%m/%d/%Y %H:%M', '%m/%d/%Y %H:%M:%S', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M', '%Y/%m/%d %H:%M:%S')
_TIME_FORMATS = ('%H:%M', '%H:%M:%S', '%I:%M %p', '%I %p')
_WEEKDAYS = {name: i for i, name in enumerate(('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'))}
_WEEKDAYS.update({name[:3]: i for name, i in list(_WEEKDAYS.items())})


def parse_datetime(value) -> datetime:
    """Parse a datetime from a datetime, an ISO 8601 string, or one of a few explicit alternative formats
    (``m/d/Y H:M`` as used by the weather CSVs). Formats are explicit on purpose: ambiguous day/month guessing is not
    supported, so configurations should use ISO 8601."""
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f'Unrecognised datetime: {value!r}. Use ISO 8601 (YYYY-MM-DDTHH:MM:SS).')


def parse_time(value) -> time:
    """Parse a time of day from a time, an ISO 8601 string (``HH:MM[:SS]``), or ``H:MM`` / ``h:MM am|pm``."""
    if isinstance(value, time):
        return value
    text = str(value).strip()
    try:
        return time.fromisoformat(text)
    except ValueError:
        pass
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text.upper(), fmt).time()
        except ValueError:
            continue
    raise ValueError(f'Unrecognised time of day: {value!r}. Use HH:MM or HH:MM:SS.')


def parse_weekday(value) -> int:
    """Weekday index (Monday=0) from an int or an English weekday name or three-letter abbreviation."""
    if isinstance(value, int):
        if 0 <= value <= 6:
            return value
        raise ValueError(f'Weekday index out of range: {value}')
    try:
        return _WEEKDAYS[str(value).strip().lower()]
    except KeyError:
        raise ValueError(f'Unrecognised weekday: {value!r}')


def parse_timedelta(value) -> timedelta | None:
    """timedelta from a timedelta or a number of seconds (the form used in configuration files)."""
    if value is None or isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)):
        return timedelta(seconds=float(value))
    raise ValueError(f'Unrecognised duration: {value!r}. Give a number of seconds.')


def linspace(start: float, stop: float, num: int) -> list[float]:
    """Return num evenly spaced values from start to stop inclusive (pure-Python replacement for numpy.linspace)."""
    if num < 1:
        return []
    if num == 1:
        return [float(start)]
    step = (stop - start) / (num - 1)
    return [start + step * i for i in range(num)]


def camel_to_snake(name):
    s1 = first_cap_re.sub(r'\1_\2', name)
    return all_cap_re.sub(r'\1_\2', s1).lower()


def safe_weak_proxy_callback(obj: object, attribute: str):
    """ Safe Weak Proxy Callback
        Provides a callback function that will return a variable used by WeakRef Proxy to None if the reference dies."""
    def back_to_none(_):  # A Weak Reference is passed in, but it is dead, and therefore useless, so using "_".
        try:
            setattr(obj, attribute, None)
        except (AttributeError, TypeError) as e:
            # It's possible the attribute is read-only (property without setter) or the object is a frozen dataclass.
            # This can happen during interpreter shutdown or when objects have restrictions on setting attributes.
            # Swallow the exception and log at debug level to avoid noisy tracebacks during weakref callbacks.
            try:
                _log.debug('safe_weak_proxy_callback: could not set %s on %r: %s', attribute, obj, e)
            except Exception:
                # Avoid any errors from logging during teardown.
                pass
    return back_to_none


def validate_bool(value, name):
    if not (isinstance(value, bool) or isinstance(value, int) or isinstance(value, float)):
        raise ValueError(f'{name} must be bool, not {type(value)}')
    return bool(value)

def get_class_from_spec(spec:dict, default_class_name: str = None, default_module_prefix: str = None,
                        transactive_node=None):
    class_name = spec.pop('class_name', default_class_name)
    if not class_name:
        raise ValueError(f'Cannot load class --- Spec is missing class name: {spec}')
    # TODO: This could probably use logic that searches standard locations and a default user-extensions directory.
    module_name = spec.pop('module_name', '.'.join((default_module_prefix, camel_to_snake(class_name))))
    module = importlib.import_module(module_name)
    if transactive_node:
        spec['transactive_node'] = transactive_node
    return getattr(module, class_name)

def json_encoder(obj):
    """
    `default` hook for json.dumps covering the value types archive records and signals carry: datetimes and times as
    ISO 8601, timedeltas as seconds, UUIDs as their string form, enumerations by name. Anything else is an error, as
    json.dumps would report itself; returning obj.__dict__ (the former behaviour) silently produced wrong output.
    """
    if isinstance(obj, (datetime, time)):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.name
    raise TypeError(f'Object of type {type(obj).__name__} is not JSON serializable')

def is_heavyloadhour(datetime_value):
    """True if time is within a HLH hour
    """
    is_hlh = False
    if not isinstance(datetime_value, datetime):
        # noinspection PyStringException
        raise 'Input value has to be an instance of datetime'

    # TODO: Why do we have a function that only recognizes holidays in the US in 2018?
    # These holidays are always LLH. If New Year's Day, Independence Day, or
    # Labor Day fall on a Sunday, the following Monday is LLH. These dates
    holidays = [
        "2018-01-01",
        "2018-05-28",
        "2018-07-04",
        "2018-09-03",
        "2018-11-22",
        "2018-12-25"
    ]
    # Should be maintained far into the future.

    # The basic definition of HLH is based on hour and weekday memberships.
    h = datetime_value.hour
    d = datetime_value.weekday()
    d_str = datetime_value.date().isoformat()
    is_holiday = d_str in holidays
    is_in_hlh_hours = 6 <= h <= 21
    is_sunday = d == 6  # Sunday

    if is_in_hlh_hours and not is_sunday and not is_holiday:
        is_hlh = True

    return is_hlh

def read_spline(filename=None, xval=0, test=False):
    # Use a cubic spline table to interpolate an dependent variable y for a given independent variable xval.
    # The presumed file structure columns are ordered ['x', 'a', 'b', 'c', 'd'], where the cubic spline is calculated
    # as a*dx^3 + b*dx^2 + c*dx + d, where dx = xval - x.
    # TODO: This lookup function does not prohibit extrapolation of spine above the last, maximum independent variable
    #  value.
    # TODO: This lookup function presumes a column order. Can this be made more forgiving?
    import csv
    # filename = 'C:/Users/D3H843\Desktop/Active Projects/TSP-Read Capacity Value from Table/CapacityPriceSpline.csv'
    # xval = 12000
    y = []
    with open(filename, newline='') as csvfile:
        reader = csv.reader(csvfile, quoting=csv.QUOTE_NONNUMERIC)
        for row in reader:
            if isinstance(row[0], str):  # Case 1
                # print(str(row[0]), 'Case 1')
                continue
            elif xval >= row[0]:  # Case 2
                # print(str(row[0]), 'Case 2')
                dx = xval - row[0]
                a = row[1]
                b = row[2]
                c = row[3]
                d = row[4]
                y = a * dx ** 3 + b * dx ** 2 + c * dx + d
            else:  # Case 3
                # print(str(row[0]), 'Case 3')
                break
        # print(y)
        return y