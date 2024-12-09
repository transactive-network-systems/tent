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
import unittest
import pandas as pd
from tent.meter_point.csv_meter_point import CSVMeterPoint
from tent.containers.interval_value import IntervalValue
from datetime import timedelta
from tent.market import Market
from tent.utils.data_manager import *


def divider():
    print(70*'*')


def ran():
    print('- the test ran without errors')


def failed(error):
    print('- THE TEST FAILED:', error)


class TestCSVMeterPoint(unittest.TestCase):
    def setUp(self):
        """
        Creates a dummy meter csv file, mock market object, and initialize CSVMeterPoint object for testing.
        """
        # Dummy meter csv file creation.
        self.test_csv_path = "tmp_csv_meter_point_test.csv"
        self.test_date = datetime(2020, 1, 1)  # Test date Jan 1, 2020
        self.test_csv_length = 100
        self.test_df = pd.DataFrame({
            "Timestamp": [(self.test_date + timedelta(hours=x)) for x in range(self.test_csv_length)],
            "energy (kW)": [x for x in range(self.test_csv_length)]
        })
        self.test_df.to_csv(self.test_csv_path, index=False)

        # Mock market object creation
        self.test_market = Market(intervals_to_clear=(self.test_csv_length-1), market_clearing_time=self.test_date)

        # Initialize CSVMeterPoint object
        self.test_csv_meter = CSVMeterPoint(name="TestCSVMeter", csv_path=self.test_csv_path)

    def tearDown(self):
        """
        Removes test csv file if it exists
        """
        if os.path.isfile(self.test_csv_path):
            os.remove(self.test_csv_path)

    def test_case1(self):
        """
        Read data from csv.
        """
        print("\nCASE 1: Read from CSV file")
        # Verify nothing is stored in meter_intervals
        assert len(self.test_csv_meter.meter_intervals) == 0
        # Read in all values in csv
        self.test_csv_meter.read_meter(self.test_market)
        # Verify that all values have been read in.
        # This value should be one less than the length of the csv file (intervals)
        assert len(self.test_csv_meter.meter_intervals) == (self.test_csv_length - 1)
        
        for x in range(self.test_csv_length-1):
            with self.subTest(x=x):
                # Verify that all values are IntervalValues
                assert isinstance(self.test_csv_meter.meter_intervals[x], IntervalValue)
                # Verify that timestamps match csv
                self.assertEqual(self.test_csv_meter.meter_intervals[x].time_interval.start_time,
                                 self.test_df["Timestamp"][x])
                # Verify that values match csv value intervals
                self.assertEqual(self.test_csv_meter.meter_intervals[x].value,
                                 (self.test_df["energy (kW)"][x+1] - self.test_df["energy (kW)"][x]))

    def test_case2(self):
        """
        Read data from csv when the timeInterval does not line up with all values from csv.
        """
        print("\nCASE 2: Read from CSV with unequal timestamps")
        # Verify nothing is stored in meter_intervals
        assert len(self.test_csv_meter.meter_intervals) == 0
        # 240229DJH: Property intervals_to_clear has become a protected property.
        self.test_market._intervals_to_clear += 1
        # Read in all values in csv
        self.test_csv_meter.read_meter(self.test_market)
        # Verify that all values have been read in.
        # This value should be one less than the length of the csv file (intervals)
        assert len(self.test_csv_meter.meter_intervals) == (self.test_csv_length - 1)
        
        for x in range(self.test_csv_length-1):
            with self.subTest(x=x):
                # Verify that all values are IntervalValues

                assert isinstance(self.test_csv_meter.meter_intervals[x], IntervalValue)
                # Verify that timestamps match csv
                self.assertEqual(self.test_csv_meter.meter_intervals[x].time_interval.start_time,
                                 self.test_df["Timestamp"][x])
                # Verify that values match csv value intervals
                self.assertEqual(self.test_csv_meter.meter_intervals[x].value,
                                 (self.test_df["energy (kW)"][x+1] - self.test_df["energy (kW)"][x]))


if __name__ == '__main__':
    # Run using python's built-in unit test module
    # unittest.main()
    # Run manually
    divider()  # **********
    print('\nRunning tests in test_meter_csv.py')

    divider()  # **********
    test_fixture = TestCSVMeterPoint()
    print('Running test fixture ' + str(test_fixture) + '.')
    
    # TEST CASE 1
    test_fixture.setUp()
    try:
        test_fixture.test_case1()
    except Exception as e:
        failed(e)
    test_fixture.tearDown()
    
    # TEST CASE 2
    test_fixture.setUp()
    try:
        test_fixture.test_case2()
    except Exception as e:
        failed(e)
    test_fixture.tearDown()

    print('Fixture ' + str(test_fixture) + 'ran to completion.')
    divider()  # **********

    print("\nTests in test_meter_csv.py ran to completion.")
    divider()  # **********
