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
import os
import sys
import unittest
from unittest.mock import MagicMock
import pandas as pd
from bacnet_meter_point import BACnetConfig, BACnetMeterPoint
from datetime import datetime, date, timedelta
from interval_value import IntervalValue
from neighbor_model import Neighbor
from time_interval import TimeInterval
from datetime import datetime, timedelta
from market import Market
from vertex import Vertex
from measurement_type import MeasurementType
from data_manager import *
from transactive_record import TransactiveRecord

import logging
import os
import gevent
import socket

BACNET_DEVICE_TOPIC = "devices/bacnet"
BACNET_TEST_IP = "BACNET_TEST_IP"

def DIVIDER():
    print(70*'*')


def ran():
    print('- the test ran without errors')


def failed(error):
    print('- THE TEST FAILED:', error)


class TestBACnetMeterPoint(unittest.TestCase):
    def setUp(self):
        """
        Creates a dummy meter csv file, mock market object, and initialize CSVMeterPoint object for testing.
        """
        device_address = socket.gethostbyname(socket.gethostname() + ".local")
        print(f"Device address for proxy agent for testing: {device_address}")
        bacnet_config = BACnetConfig(local_address=device_address,
                                     target_address=os.environ.get(BACNET_TEST_IP),
                                     description="BACnet Meter Device")
        
        # Mock market object creation
        self.test_market = Market(intervals_to_clear=(self.test_csv_length-1), market_clearing_time=self.test_date)
        self.test_market.check_intervals()

        # Initialize CSVMeterPoint object
        self.test_bacnet_meter = BACnetMeterPoint(bacnet_config)

    def tearDown(self):
        """
        Removes test csv file if it exists
        """
        pass

    def test_case1(self):
        """
        Read data from test BACnet device.
        """
        print("\nCASE 1: Read from BACnet device")
        register_values = [
        "3820a/Field Bus.3820A CHILLER.AHU-COIL-CHWR-T",
        "3820a/Field Bus.3820A CHILLER.CHW-FLOW",
        ]
        actual_values = self.test_bacnet_meter.read_meter()
        print(f"Result of read_meter: {actual_values}")

        for register in register_values:
            assert register in actual_values


if __name__ == '__main__':
    # Run using python's built-in unit test module
    # unittest.main()
    # Run manually
    DIVIDER()  # **********
    print('\nRunning tests in test_meter_bacnet.py')

    DIVIDER()  # **********
    test_fixture = TestBACnetMeterPoint()
    print('Running test fixture ' + str(test_fixture) + '.')
    
    # TEST CASE 1
    test_fixture.setUp()
    try:
        test_fixture.test_case1()
    except Exception as e:
        failed(e)
    test_fixture.tearDown()
    
    print('Fixture ' + str(test_fixture) + 'ran to completion.')
    DIVIDER()  # **********

    print("\nTests in test_meter_bacnet.py ran to completion.")
    DIVIDER()  # **********        