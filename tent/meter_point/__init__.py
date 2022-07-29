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

# A MeterPoint may correlate directly with a meter. It necessarily
# corresponds to one measurement type (see MeasurementType enumeration) and
# measurement location within the circuit. Therefore, a single physical
# meter might be the source of more than one MeterPoint.

import sys
import pandas as pd
import sqlite3
import weakref

from datetime import timedelta
from tent.interval_value import IntervalValue
from tent.market import Market
from tent.market_state import MarketState
from math import ceil
from statistics import mean
from typing import Union

from tent.helpers import find_obj_by_ti
from tent.measurement_type import MeasurementType
from tent.measurement_unit import MeasurementUnit
from tent.timer import Timer
from tent.utils.time_series_buffer import TimeSeriesBuffer


class MeterPoint(TimeSeriesBuffer):
    def __init__(self,
                 data_period_seconds: float = 10,
                 description: str = '',
                 max_buffer_seconds: float = 3600,
                 # TODO: Is measurement_interval intended as the market interval or the polling period?
                 #  2022-03-28: Assuming that this is set to the interval to be considered by the
                 #  mean_measurements function (i.e used for DC interval in neighbor_model). Is this correct?
                 measurement_interval: Union[timedelta, int] = timedelta(hours=1),
                 measurement_type: Union[MeasurementType, str, int] = MeasurementType.Unknown,
                 measurement_unit: Union[MeasurementUnit, str, int] = MeasurementUnit.Unknown,
                 name: str = '',
                 scale_factor: float = 1.0,
                 transactive_node=None
                 ):
        max_len = ceil(max_buffer_seconds / data_period_seconds) + 1
        super(MeterPoint, self).__init__(maxlen=max_len, dt_class=Timer)
        self.tn = weakref.ref(transactive_node) if transactive_node else None

        # These are static properties that may be passed as parameters:
        self.description = str(description)
        self.measurementInterval = measurement_interval if isinstance(measurement_interval, timedelta) \
            else timedelta(seconds=measurement_interval)

        if isinstance(measurement_type, MeasurementType):
            self.measurementType = measurement_type
        elif isinstance(measurement_type, int):
            self.measurementType = MeasurementType(measurement_type)
        else:
            self.measurementType = MeasurementType[measurement_type]

        if isinstance(measurement_unit, MeasurementUnit):
            self.measurementUnit = measurement_unit
        elif isinstance(measurement_unit, int):
            self.measurementUnit = MeasurementUnit(measurement_unit)
        else:
            self.measurementUnit = MeasurementUnit[measurement_unit]
        self.name = str(name)
        self.scale_factor = float(scale_factor)  # Use -1 to reverse the sign of the meter.

        # These following properties are dynamically assigned and should not be assigned during meter configuration:
        self.meter_intervals = []

    @property
    def current_interval_measurements(self) -> list:
        """Last hour of measurements."""
        return self.get(since=(self.dt_class.now() - self.measurementInterval))

    @property
    def mean_measurement(self):
        """Average or some other aggregate metric."""
        return mean(self.current_interval_measurements)

    # TODO: Looks like this should be time, not value.
    # TODO: PEP8 this name.
    @property
    def last_update(self):
        """Most recent measurement."""
        return self.last

    @property
    def current_measurement(self):
        """Most recent measurement."""
        return self.last

    def set_meter_value(self, value, last_update=None):
        last_update = last_update if last_update else Timer.get_cur_time()
        self.append(self.scale_factor * value, last_update)
        self.last = last_update

    # TODO: Remove update_avg, but first investigate its use: make sure no functionality lost.
    # def update_avg(self):
    #     if len(self.current_hour_measurements) > 30:
    #         self.current_measurement = sum(self.current_hour_measurements) / len(self.current_hour_measurements)
    #         self.current_hour_measurements = []
    #     return self.current_measurement

    def read_meter(self, obj):
        # Read the meter point at scheduled intervals
        #
        # MeterPoints are updated on a schedule. Properties have been defined to keep track of the time of the last
        # update and the interval between updates.
        #
        # While this seems easy, meters will be found to be diverse and may use diverse standards and protocols. Create
        # subclasses and redefine this function as needed to handle unique conditions.
        print('Made it to MeterPoint.read_meter() for ' + obj.name)

    # TODO: PEP8 names in this function.
    def _intervalValue_to_dataFrame(self, intVal):
        """
        Helper method to convert IntervalValues into dataframes.
        Intended for writing to database. Format based on data_manager.py
        """
        DATE_FORMAT = "%Y-%m-%d"  # Simple date format like '2021-01-26'.
        TIME_FORMAT = "%H:%M:%S"  # Simple time format like '13:14:15'.
        TIMESTAMP = format(datetime.now())  # TODO: Get rid of explicit datetime.
        SOURCE_TYPE = intVal.associatedClass
        SOURCE_NAME = intVal.associatedObject
        MARKET_SERIES = intVal.market.marketSeriesName
        MARKET_CLEARING_DATE = intVal.market.marketClearingTime.strftime(DATE_FORMAT)
        MARKET_CLEARING_TIME = intVal.market.marketClearingTime.strftime(TIME_FORMAT)
        INTERVAL_STARTING_DATE = intVal.timeInterval.startTime.strftime(DATE_FORMAT)
        INTERVAL_STARTING_TIME = intVal.timeInterval.startTime.strftime(TIME_FORMAT)
        MEASUREMENT_TYPE = MeasurementType.get(intVal.measurementType)
        VALUE_1 = intVal.value
        mini_df = pd.DataFrame({
            "TIMESTAMP": TIMESTAMP,
            "SOURCE_TYPE": SOURCE_TYPE,
            "SOURCE_NAME": SOURCE_NAME,
            "MARKET_SERIES": MARKET_SERIES,
            "MARKET_CLEARING_DATE": MARKET_CLEARING_DATE,
            "MARKET_CLEARING_TIME": MARKET_CLEARING_TIME,
            "INTERVAL_STARTING_DATE": INTERVAL_STARTING_DATE,
            "INTERVAL_STARTING_TIME": INTERVAL_STARTING_TIME,
            "MEASUREMENT_TYPE": MEASUREMENT_TYPE,
            "VALUE_1": VALUE_1
        })
        return mini_df

    # TODO: Do we need to recreate the market class from the database,
    #       or will that exist and we can pass the market in here? Is this needed/useful?
    def _dataFrame_to_intervalValue(self, df):
        """
        Helper method to convert dataframes into IntervalValues.
        Intended for reading from database.
        """
        DATE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
        market_date_time = df["MARKET_CLEARING_DATE"] + " " + df["MARKET_CLEARING_TIME"]
        tmpMart = Market(market_series_name=df["MARKET_SERIES"], market_clearing_time=market_date_time)
        intVal_date_time = df["INTERVAL_STARTING_DATE"] + " " + df["INTERVAL_STARTING_TIME"]
        intVal = IntervalValue(calling_object=getattr(sys.modules[__name__], df["SOURCE_TYPE"]),
                               market=tmpMart,
                               measurement_type=df["MEASUREMENT_TYPE"],
                               value=df["VALUE_1"]
                               )

    def store(self, db=None, timeIntervals=None):
        """
        Store last measurement into historian
        The default approach here could be to append a text record file. If the file is reserved for one meterpoint,
        little or no metadata need be repeated in records. Minimum content should be reading time and datum.
        Implementers will be found to have diverse practices for historians.

        """
        conn = sqlite3.connect(db)
        # When specific value is specified, store in database.
        # Disregards whether or not the value is in meter interval list or not
        # Force command
        if timeIntervals:
            for ti in timeIntervals:
                intervalValueObj = find_obj_by_ti(self.meter_intervals, ti)
                if intervalValueObj:
                    self.meter_intervals.pop(self.meter_intervals.index(intervalValueObj))
                    mini_df = self._intervalValue_to_dataFrame(intervalValueObj)
                    mini_df.to_sql("meter", conn, index=False, if_exists="append")
        # If nothing is specified, use the default behaviour
        # Only remove value from list if market has expired for intervalValue
        else:
            for interval in self.meter_intervals:
                if interval.market.marketState == MarketState.Expired:
                    mini_df = self._intervalValue_to_dataFrame(interval)
                    mini_df.to_sql("meter", conn, index=False, if_exists="append")
                    self.meter_intervals.pop(self.meter_intervals.index(interval))
        return

    # TODO: Should we track by TIMESTAMP vs. intervalValue startTime
    def read_database(self, db=None, key=None):
        # If key is specified, Start_Time in database will be checked
        # If a value matching the Start_Time exists, it will be returned
        conn = sqlite3.connect(db)
        if key:
            DATE_FORMAT = "%Y-%m-%d"  # Simple date format like '2021-01-26'.
            TIME_FORMAT = "%H:%M:%S"  # Simple time format like '13:14:15'.
            key_date = key.strftime(DATE_FORMAT)
            key_time = key.strftime(TIME_FORMAT)
            mini_df = pd.read_sql(
                f"SELECT * FROM meter WHERE INTERVAL_STARTING_DATE = {key_date} AND INTERVAL_STARTING_TIME = {key_time}",
                conn)
            return mini_df["VALUE_1"][0]
        # If no key is specified, return all values found in the database
        # These values will be returned as a list
        else:
            mini_df = pd.read_sql("SELECT * FROM meter", conn)
            return mini_df["VALUE_1"].to_list()

    @classmethod
    def get_meters_by_name(cls, meter_list, name):
        return [m for m in meter_list if m.name == name and isinstance(m, cls)]

    @classmethod
    def get_meter_by_name(cls, meter_list, name):
        return MeterPoint.get_meters_by_name(meter_list, name)[0]

    @classmethod
    def get_meters_by_measurement_type(cls, meter_list, measurement_type):
        return [m for m in meter_list if m.measurement_type == measurement_type and isinstance(m, cls)]

    @classmethod
    def get_meter_by_measurement_type(cls, meter_list, measurement_type):
        return MeterPoint.get_meters_by_measurement_type(meter_list, measurement_type)[0]
