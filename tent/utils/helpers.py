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

from datetime import datetime

from tent.utils.log import setup_logging

setup_logging()
_log = logging.getLogger(__name__)

first_cap_re = re.compile('(.)([A-Z][a-z]+)')
all_cap_re = re.compile('([a-z\d])([A-Z])')

def camel_to_snake(name):
    s1 = first_cap_re.sub(r'\1_\2', name)
    return all_cap_re.sub(r'\1_\2', s1).lower()


def safe_weak_proxy_callback(obj: object, attribute: str):
    """ Safe Weak Proxy Callback
        Provides a callback function that will return a variable used by WeakRef Proxy to None if the reference dies."""
    def back_to_none(_):  # A Weak Reference is passed in, but it is dead, and therefore useless, so using "_".
        setattr(obj, attribute, None)
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
    if isinstance(obj, datetime):
        return obj.isoformat()
    else:
        return obj.__dict__

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