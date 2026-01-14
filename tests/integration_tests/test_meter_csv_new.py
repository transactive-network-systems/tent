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
import pytest
import pandas as pd
from datetime import datetime, timedelta

from tent.meter_point.csv_meter_point import CSVMeterPoint
from tent.containers.interval_value import IntervalValue
from tent.market import Market
from tent.market.market_series import MarketSeries
from tests.mock_tn import MockTN


@pytest.fixture
def test_csv_path(tmp_path):
    """Temporary CSV file path."""
    return str(tmp_path / "tmp_csv_meter_point_test.csv")


@pytest.fixture
def test_date():
    """Test date Jan 1, 2020."""
    return datetime(2020, 1, 1)


@pytest.fixture
def test_csv_length():
    """Number of rows in test CSV."""
    return 100


@pytest.fixture
def test_df(test_csv_path, test_date, test_csv_length):
    """Create and populate test CSV file, return DataFrame."""
    df = pd.DataFrame({
        "Timestamp": [(test_date + timedelta(hours=x)) for x in range(test_csv_length)],
        "energy (kW)": [x for x in range(test_csv_length)]
    })
    df.to_csv(test_csv_path, index=False)
    yield df
    # Cleanup
    if os.path.isfile(test_csv_path):
        os.remove(test_csv_path)


@pytest.fixture
def mock_tn():
    """Mock transactive node."""
    return MockTN()


@pytest.fixture
def test_market(mock_tn, test_date, test_csv_length):
    """Create market with modern API."""
    market_series = MarketSeries(
        transactive_node=mock_tn,
        name="TestMarketSeries",
        intervals_to_clear=test_csv_length - 1
    )
    timings = market_series.duration.get_timings(test_date)
    return Market(
        transactive_node=mock_tn,
        market_series=market_series,
        transition_times=timings
    )


@pytest.fixture
def test_csv_meter(test_csv_path, mock_tn):
    """Create CSVMeterPoint with modern API."""
    meter = CSVMeterPoint(
        csv_path=test_csv_path,
        transactive_node=mock_tn,
        name="TestCSVMeter"
    )
    # Add meter_intervals property to collect IntervalValue objects
    meter.meter_intervals = []
    return meter

def test_case1(test_csv_meter, test_market, test_df, test_csv_length):
    """
    Read data from csv.
    """
    print("\nCASE 1: Read from CSV file")
    # Verify nothing is stored in meter_intervals
    assert len(test_csv_meter.meter_intervals) == 0
    # Read in all values in csv
    test_csv_meter.read_meter(test_market)
    # Verify that all values have been read in.
    # This value should be one less than the length of the csv file (intervals)
    assert len(test_csv_meter.meter_intervals) == (test_csv_length - 1)
    
    for x in range(test_csv_length-1):
        # Verify that all values are IntervalValues
        assert isinstance(test_csv_meter.meter_intervals[x], IntervalValue)
        # Verify that timestamps match csv
        assert test_csv_meter.meter_intervals[x].interval.start_time == test_df["Timestamp"][x]
        # Verify that values match csv value intervals
        assert test_csv_meter.meter_intervals[x].value == (test_df["energy (kW)"][x+1] - test_df["energy (kW)"][x])

def test_case2(test_csv_meter, test_market, test_df, test_csv_length, mock_tn, test_date):
    """
    Read data from csv when the time_interval does not line up with all values from csv.
    """
    print("\nCASE 2: Read from CSV with unequal timestamps")
    # Verify nothing is stored in meter_intervals
    assert len(test_csv_meter.meter_intervals) == 0
    
    # Create new market with one more interval
    market_series = MarketSeries(
        transactive_node=mock_tn,
        name="TestMarketSeries",
        intervals_to_clear=test_csv_length  # One more than CSV intervals
    )
    timings = market_series.duration.get_timings(test_date)
    test_market_mismatch = Market(
        transactive_node=mock_tn,
        market_series=market_series,
        transition_times=timings
    )
    
    # Read in all values in csv
    test_csv_meter.read_meter(test_market_mismatch)
    # Verify that all values have been read in.
    # This value should be one less than the length of the csv file (intervals)
    assert len(test_csv_meter.meter_intervals) == (test_csv_length - 1)
    
    for x in range(test_csv_length-1):
        # Verify that all values are IntervalValues
        assert isinstance(test_csv_meter.meter_intervals[x], IntervalValue)
        # Verify that timestamps match csv
        assert test_csv_meter.meter_intervals[x].interval.start_time == test_df["Timestamp"][x]
        # Verify that values match csv value intervals
        assert test_csv_meter.meter_intervals[x].value == (test_df["energy (kW)"][x+1] - test_df["energy (kW)"][x])


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
