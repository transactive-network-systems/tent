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


class IntervalValue:
    """
    An IntervalValue instance is used to keep track of a value (measurement, quality, etc.)
    with its corresponding TimeInterval instance.
    
    This is a compatibility class for legacy tests. Modern code should use Measurement and MeasurementValue.
    """
    def __init__(self, calling_object, time_interval, market, measurement_type, value):
        self.version = 0
        self.scheduled = True  # islogical(scheduled)
        # self.associated_class = calling_object.__class__
        try:
            self.associated_class = type(calling_object).__name__
        except RuntimeWarning:
            self.associated_class = 'unknown'
        # self.associated_object = type(calling_object).__name__
        try:
            self.associated_object = calling_object.name
        except RuntimeWarning:
            self.associated_object = 'unknown'
        self.id = None
        self.interval = time_interval  # Modern API uses 'interval'
        self.market = market
        self.measurement_type = measurement_type
        self.value = value

    # Subdivide an IntervalValue into multiple identical IntervalValues.
    def subdivide(self):
        pass

    def get_dict(self):
        interval_value_dict = {
            "scheduled": self.scheduled,
            "associated_class": self.associated_class,
            "associated_object": self.associated_object,
            "time_interval_id": self.id,
            "time_interval": self.interval,
            "measurement_type": self.measurement_type,
            "value": self.value
        }
        return interval_value_dict
