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
import pandas as pd
from bacnet_meter_point import BACnetMeterPoint
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
from mock import MagicMock



BACNET_DEVICE_TOPIC = "devices/bacnet"
BACNET_TEST_IP = "BACNET_TEST_IP"

def test_scrape_all_should_succeed(bacnet_test_agent):
    register_values = [
        "3820a/Field Bus.3820A CHILLER.AHU-COIL-CHWR-T",
        "3820a/Field Bus.3820A CHILLER.CHW-FLOW",
    ]
    actual_values = bacnet_test_agent.vip.rpc.call(
        PLATFORM_DRIVER, "scrape_all", "bacnet"
    ).get(timeout=10)
    logger.info(f"Result of scrape_all: {actual_values}")

    for register in register_values:
        assert register in actual_values


def test_get_point_should_succeed(bacnet_test_agent):
    register_values = [
        "3820a/Field Bus.3820A CHILLER.AHU-COIL-CHWR-T",
        "3820a/Field Bus.3820A CHILLER.CHW-FLOW",
    ]
    for register in register_values:
        async_res = bacnet_test_agent.vip.rpc.call(
            PLATFORM_DRIVER, "get_point", "bacnet", register
        )
        value = async_res.get()
        logger.info(f"Value for point {register}: {value}")
        assert isinstance(value, float)


@pytest.fixture(scope="module")
def bacnet_proxy_agent(volttron_instance):
    device_address = socket.gethostbyname(socket.gethostname() + ".local")
    print(f"Device address for proxy agent for testing: {device_address}")
    bacnet_proxy_agent_config = {
        "device_address": device_address,
        # below are optional; values are set to show configuration options; values use the default values
        "max_apdu_length": 1024,
        "object_id": 599,
        "object_name": "Volttron BACnet driver",
        "vendor_id": 5,
        "segmentation_supported": "segmentedBoth",
    }
    bacnet_proxy_agent_uuid = volttron_instance.install_agent(
        agent_dir=get_services_core("BACnetProxy"),
        config_file=bacnet_proxy_agent_config,
    )
    gevent.sleep(1)
    volttron_instance.start_agent(bacnet_proxy_agent_uuid)
    assert volttron_instance.is_agent_running(bacnet_proxy_agent_uuid)

    yield bacnet_proxy_agent_uuid

    print("Teardown of bacnet_proxy_agent")
    volttron_instance.stop_agent(bacnet_proxy_agent_uuid)


@pytest.fixture(scope="module")
def bacnet_test_agent(bacnet_proxy_agent, config_store, volttron_instance):
    test_agent = volttron_instance.build_agent(identity="test-agent")

    # create a mock callback to use with a subscription to the driver's publish publishes
    test_agent.poll_callback = MagicMock(name="poll_callback")

    # subscribe to device topic results
    test_agent.vip.pubsub.subscribe(
        peer="pubsub",
        prefix=BACNET_DEVICE_TOPIC,
        callback=test_agent.poll_callback,
    ).get()

    # give the test agent the capability to modify the platform_driver's config store
    capabilities = {"edit_config_store": {"identity": PLATFORM_DRIVER}}
    volttron_instance.add_capabilities(test_agent.core.publickey, capabilities)

    # A sleep was required here to get the platform to consistently add the edit config store capability
    gevent.sleep(1)

    yield test_agent

    print("In teardown method of query_agent")
    test_agent.core.stop()


@pytest.fixture(scope="module")
def config_store_connection(volttron_instance):
    capabilities = [{"edit_config_store": {"identity": PLATFORM_DRIVER}}]
    connection = volttron_instance.build_connection(
        peer=CONFIGURATION_STORE, capabilities=capabilities
    )
    gevent.sleep(1)

    # Start the platform driver agent which would in turn start the bacnet driver
    platform_uuid = volttron_instance.install_agent(
        agent_dir=get_services_core("PlatformDriverAgent"),
        config_file={
            "publish_breadth_first_all": False,
            "publish_depth_first": False,
            "publish_breadth_first": False,
        },
        start=True,
    )
    gevent.sleep(2)  # wait for the agent to start and start the devices

    yield connection

    volttron_instance.stop_agent(platform_uuid)
    volttron_instance.remove_agent(platform_uuid)
    connection.kill()


@pytest.fixture(scope="module")
def config_store(config_store_connection):
    # this fixture will setup a the BACnet driver that will communicate with a live BACnet device located at PNNL campus in Richland at the given device_address
    device_address = os.environ.get(BACNET_TEST_IP)
    if os.system("ping -c 1 " + device_address) != 0:
        pytest.skip(f"BACnet device cannot be reached at {device_address} ")

    registry_config = "bacnet_test.csv"
    registry_string = f"""Reference Point Name,Volttron Point Name,Units,Unit Details,BACnet Object Type,Property,Writable,Index,Write Priority,Notes
        3820a/Field Bus.3820A CHILLER.AHU-COIL-CHWR-T,3820a/Field Bus.3820A CHILLER.AHU-COIL-CHWR-T,degreesFahrenheit,-50.00 to 250.00,analogInput,presentValue,FALSE,3000741,,Primary CHW Return Temp
        3820a/Field Bus.3820A CHILLER.CHW-FLOW,3820a/Field Bus.3820A CHILLER.CHW-FLOW,usGallonsPerMinute,-50.00 to 250.00,analogInput,presentValue,FALSE,3000744,,Chiller 1 CHW Flow"""

    # registry config
    config_store_connection.call(
        "manage_store",
        PLATFORM_DRIVER,
        registry_config,
        registry_string,
        config_type="csv",
    )

    # driver config
    driver_config = {
        "driver_config": {"device_address": device_address, "device_id": 506892},
        "driver_type": "bacnet",
        "registry_config": f"config://{registry_config}",
        "timezone": "US/Pacific",
        "interval": 15,
    }

    config_store_connection.call(
        "manage_store",
        PLATFORM_DRIVER,
        BACNET_DEVICE_TOPIC,
        driver_config,
    )

    yield config_store_connection

    print("Wiping out store.")
    config_store_connection.call("manage_delete_store", PLATFORM_DRIVER)
    gevent.sleep(0.1)


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
        # Dummy meter csv file creation.
        self.test_csv_path = "tmp_csv_meter_point_test.csv"
        self.test_date = datetime(2020, 1, 1) # Test date Jan 1, 2020
        self.test_csv_length = 100
        self.test_df = pd.DataFrame({
            "Timestamp": [(self.test_date + timedelta(hours=x)) for x in range(self.test_csv_length)],
            "energy (kW)": [x for x in range(self.test_csv_length)]
        })
        self.test_df.to_csv(self.test_csv_path, index=False)

        # Mock market object creation
        self.test_market = Market(intervals_to_clear=(self.test_csv_length-1), market_clearing_time=self.test_date)
        self.test_market.check_intervals()

        # Initialize CSVMeterPoint object
        self.test_csv_meter = BACnetMeterPoint()

    def tearDown(self):
        """
        Removes test csv file if it exists
        """
        if os.path.isfile(self.test_csv_path):
            os.remove(self.test_csv_path)

    def test_case1(self):
        """
        Read data from test BACnet device.
        """
        print("\nCASE 1: Read from BACnet device")
        # Verify nothing is stored in meter_intervals
        assert len(self.test_csv_meter.meter_intervals) == 0
        # Read in all values in csv
        self.test_csv_meter.read_meter(self.test_market, self.test_csv_path)
        # Verify that all values have been read in.
        # This value should be one less than the length of the csv file (intervals)
        assert len(self.test_csv_meter.meter_intervals) == (self.test_csv_length - 1)
        
        for x in range(self.test_csv_length-1):
            with self.subTest(x=x):
                # Verify that all values are IntervalValues
                assert isinstance(self.test_csv_meter.meter_intervals[x], IntervalValue)
                # Verify that timestamps match csv
                self.assertEqual(self.test_csv_meter.meter_intervals[x].timeInterval.startTime, self.test_df["Timestamp"][x])
                # Verify that values match csv value intervals
                self.assertEqual(self.test_csv_meter.meter_intervals[x].value, (self.test_df["energy (kW)"][x+1] - self.test_df["energy (kW)"][x]))


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
    
    # TEST CASE 2
    test_fixture.setUp()
    try:
        test_fixture.test_case2()
    except Exception as e:
        failed(e)
    test_fixture.tearDown()

    print('Fixture ' + str(test_fixture) + 'ran to completion.')
    DIVIDER()  # **********

    print("\nTests in test_meter_bacnet.py ran to completion.")
    DIVIDER()  # **********        