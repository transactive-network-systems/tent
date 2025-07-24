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
import math
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


# TODO: Remove deprecated/commented block once references to these functions have been replaced.
######## BEGIN DEPRECATED
def find_objs_by_ti(items, ti):
    # 200922DJH: This is lacking in Version 3 because multiple markets can share the same start time. Changing to match
    #            the actual TimeInterval object, which is unique to a market.
    # found_items = [x for x in items if x.interval.startTime == ti.startTime]
    found_items = [x for x in items if x.interval == ti]
    return found_items
######## END DEPRECATED


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

def are_different1(s, r, threshold, calling_neighbor=''):
    # Returns true is two sets of TransactiveRecord objects,
    # representing sent and received messages in a time interval, are
    # significantly different.
    #
    # INPUTS:
    # s - sent TransactiveRecord object(s) (see struct TransactiveRecord)
    # r - received TransactiveRecord object(s)
    # threshold - relative error used as convergence criterion. Two messages
    # differ significantly if the relative distance between the
    # scheduled points (i.e., Record 0) differ by more than this
    # threshold.
    #
    # OUTPUTS:
    # tf - Boolean: true if relative distance between scheduled (i.e., Record
    # 0) (price,quantity) pairs in the two messages exceeds the threshold.

    # Pick out the scheduled sent and received records (i.e., the one where record = 0)
    s0 = [x for x in s if x.record == 0]
    s0 = s0[0]  # a TransactiveRecord
    r0 = [x for x in r if x.record == 0]
    r0 = r0[0]  # a TransactiveRecord

    # Calculate the difference dmp in scheduled marginal prices.
    dmp = abs(s0.marginal_price - r0.marginal_price)  # [$/kWh]

    # Calculate the average mp_avg of the two scheduled marginal prices.
    mp_avg = 0.5 * abs(s0.marginal_price + r0.marginal_price)  # [$/kWh]

    # Calculate the difference dq between the scheduled powers.
    dq = abs(-s0.power - r0.power)  # [avg. kW]

    # Calculate the average q_avg of the two scheduled average powers.
    q_avg = 0.5 * abs(r0.power + -s0.power)  # [avg. kW]

    # Calculate the relative Euclidian distance d (a relative error
    # criterion) between the two scheduled (price,quantity) points.
    # try:
    #     if len(s) == 1 or len(r) == 1:
    #         d = dq / q_avg  # dimensionless
    #     else:
    #         d = math.sqrt((dq / q_avg) ** 2 + (dmp / mp_avg) ** 2)  # dimensionless
    # except:
    #     raise Exception("TCC r0.power: {} s0.power: {} dq: {}".format(r0.power, s0.power, dq))

    _log.debug("TCC neighbor {} has q_avg {}, r0.power {}, s0.power {}, dq {}, dmp {}".format(
         calling_neighbor, q_avg, r0.power, s0.power, dq, dmp))
    _log.debug("TCC neighbor {} s is: {}".format(
         calling_neighbor, [(x.time_interval, x.record, x.power, x.marginal_price) for x in s]))
    _log.debug("TCC neighbor {} r is: {}".format(
         calling_neighbor, [(x.time_interval, x.record, x.power, x.marginal_price) for x in r]))

    if q_avg != 0:
        if len(s) == 1 or len(r) == 1:
            d = dq / q_avg  # dimensionless
        else:
            d = math.sqrt((dq / q_avg) ** 2 + (dmp / mp_avg) ** 2)  # dimensionless
    else:
        d = 0

    if d > threshold:
        # The distance, or relative error, between the two scheduled points
        # exceeds the threshold criterion. Return true to indicate that the
        # two messages are significantly different.
        is_diff = True

    else:
        # The distance, or relative error, between the two scheduled points
        # is less than the threshold criterion. Return false, meaning that
        # the two messages are not significantly different.
        is_diff = False
    _log.debug("TCC for {} are_different1 returns {}".format(calling_neighbor, is_diff))

    return is_diff


def are_different2(m, s, threshold, calling_neighbor=''):
    # Assess whether two TransactiveRecord messages, representing the calculated and sent messages in an active time
    # interval are significantly different from one another. If the signals are different, this indicates that local
    # conditions have changed, and a revised, updated transactive message should be sent to the Neighbor.
    #
    # INPUTS:
    # m - TransactiveRecord message representing the mySignal, the last message calculated for this transactiveNeighbor.
    # s - TransactiveRecord message representing the sentSignal, the last message that was sent to this transactive
    #     Neighbor.
    # threshold - a dimensionless, relative error that is used as a convergence
    # criterion.
    #
    # OUTPUTS:
    # tf - Boolean: true if the sent and recently calculated transactive messages are significantly different.
    if len(s) != len(m):
        return True

    is_diff = True
    if len(s) == 1 or len(m) == 1:
        # Either the sent or calculated message is a constant, (i.e., one Vertex) meaning its marginal price is
        # probably NOT meaningful. Use only the power in this case to determine whether they differ. Pick out the
        # scheduled values (i.e., Record 0) from mySignal and sentSignal records.
        s0 = [x for x in s if x.record == 0]
        s0 = s0[0]  # a TransactiveRecord
        m0 = [x for x in m if x.record == 0]
        m0 = m0[0]  # a TransactiveRecord

        # Calculate the difference dq between the scheduled powers in the two sets of records.
        dq = abs(m0.power - s0.power)  # [avg.kW]

        # Calculate the average scheduled power avg_q of the two sets of records.
        avg_q = 0.5 * abs(m0.power + s0.power)  # [avg.kW]

        _log.debug("TCC neighbor {} has q_avg {}, m0.power {}, s0.power {}, dq {}".format(
            calling_neighbor, avg_q, m0.power, s0.power, dq))
        _log.debug("TCC neighbor {} s is: {}".format(
            calling_neighbor, [(x.time_interval, x.record, x.power, x.marginal_price) for x in s]))
        _log.debug("TCC neighbor {} m is: {}".format(
            calling_neighbor, [(x.time_interval, x.record, x.power, x.marginal_price) for x in m]))

        # Calculate relative distance d between the two scheduled powers.
        # Avoid the unlikely condition that the average power is zero.
        if avg_q != 0:
            d = dq / avg_q
        else:
            d = 0

        if d > threshold:
            # The difference is greater than the criterion. Return true,
            # meaning that the difference is significant.
            is_diff = True
        else:
            # The difference is less than the criterion. Return false,
            # meaning the difference is not significant.
            is_diff = False

    else:
        # There are multiple records, meaning that the Neighbor is price-responsive.

        # Pick out the records that are NOT scheduled points, i.e., are not Record 0. Local convergence of the
        # coordination sub-problem does not require so much that the exact point has been determined as that the
        # flexibility is accurately conveyed to the Neighbor.
        s0 = [x for x in s if x.record != 0]
        m0 = [x for x in m if x.record != 0]

        _log.debug("TCC neighbor {} s is: {}".format(
            calling_neighbor, [(x.time_interval, x.record, x.power, x.marginal_price) for x in s]))
        _log.debug("TCC neighbor {} m is: {}".format(
            calling_neighbor, [(x.time_interval, x.record, x.power, x.marginal_price) for x in m]))

        # Index through the sent and calculated flexibility records. See if
        # any record cannot be matched with a corresponding member of
        # mySignal m0.
        for i in range(len(s0)):
            is_diff = True

            for j in range(len(m0)):
                # Calculate difference dmp between marginal prices .
                dmp = abs(s0[i].marginal_price - m0[j].marginal_price)  # [$/kWh]

                # Calculate average avg_mp of marginal price pair.
                avg_mp = 0.5 * (s0[i].marginal_price + m0[j].marginal_price)  # [$/kWh]

                # Calculate difference dq between power values in the two sets of records.
                dq = abs(s0[i].power - m0[j].power)  # [avg.kW]

                # Calculate average avg_q of power pairs in the two sets of records.
                avg_q = abs(s0[i].power + m0[j].power)  # [avg.kW]

                # If no pairing between the flexibility records of the two sets of records can be found within the
                # relative error criterion, things must have changed locally since the transactive message was last
                # sent.

                # [180904DJH-HUNG FOUND CASE WHERE AVERAGES IN DENOMINATORS BECOME ZERO. THE OUTCOME HAD BEEN AN
                # UNRELIABLE CONDITIONAL WITH NAN COMPARISONS. THIS CASE MUST BE AVOIDED WITH THIS CODE:
                # Avoid unlikely divide-by-zero case. If the average marginal price is zero, it is probable they are
                # BOTH zero:
                dmp = 0 if avg_mp == 0 else dmp / avg_mp
                dq = 0 if avg_q == 0 else dq / avg_q

                if math.sqrt(dmp ** 2 + dq ** 2) <= threshold:
                    # No pairing was found within the relative error criterion distance. Things must have changed
                    # locally since the transactive message was last sent to the transactive Neighbor. Set the flag
                    # true.
                    is_diff = False
                    continue

            if is_diff:
                break

    _log.debug("TCC for {} are_different2 returns {}".format(calling_neighbor, is_diff))
    return is_diff


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