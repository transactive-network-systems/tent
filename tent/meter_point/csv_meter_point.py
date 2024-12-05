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

import pandas as pd

from tent.containers import IntervalValue
from tent.enumerations import MeasurementType
from tent.meter_point.pull_meter_point import PullMeterPoint


# TODO: Add additional functionality based on measurement types, or create new children classes based on measurement
class CSVMeterPoint(PullMeterPoint):
    def __init__(self, csv_path, *args, **kwargs):
        super(CSVMeterPoint, self).__init__(*args, **kwargs)
        self.csv_path = csv_path

    def _csv_to_df(self):
        """
        Helper function to clean up csv for usable dataframe format
        within the context of the CSVMeter.
        CSV in format [Timestamp, Real Energy Total]
        Dataframe in format [index, Timestamp, Value]
        """
        meter_df = pd.read_csv(self.csv_path)
        meter_df.rename(columns={
            meter_df.columns[0]: "Timestamp",
            meter_df.columns[1]: "Metered_Energy"
            },
            inplace=True
        )

        meter_df['Timestamp'] = pd.to_datetime(meter_df['Timestamp'], format="%Y-%m-%d %H:%M:%S")
        meter_df.set_index('Timestamp', inplace=True)
        return meter_df

    def read_meter(self, market):
        # Read the meter point at scheduled intervals from csv.
        # CSV format: [timestamp, value]
        # MeterPoints are updated on a schedule. Properties have been defined to keep track of the time of the last
        # update and the interval between updates.
        # Read market
        # While this seems easy, meters will be found to be diverse and may use diverse standards and protocols. Create
        # subclasses and redefine this function as needed to handle unique conditions.
        # CSV Format
        print('Made it to MeterPoint.read_meter() for ' + market.name)

        meter_df = self._csv_to_df()
        # Gather active time intervals
        time_intervals = market.time_intervals
        for i in range(len(time_intervals)):
            start_time = time_intervals[i].start_time
            end_time = start_time + time_intervals[i].duration
            # Attempt to get value based on timeInterval.
            # If the timeInterval does not exist in the csv data, skip.
            try:
                start_value = meter_df["Metered_Energy"][start_time]
                end_value = meter_df["Metered_Energy"][end_time]
                interval_value = end_value - start_value
                # TODO: This behavior no longer matches the functionality of the base class.
                #  The MeterPoint class does not use TimeIntervals and IntervalValues for its data,
                #  since metered data is more find-grained.
                interval = IntervalValue(self, time_intervals[i], market, MeasurementType.EnergyReal, interval_value)
                self.meter_intervals.append(interval)
            except KeyError:
                pass
        return self.meter_intervals

    def store(self, db="csvMeter.db", time_intervals=None):
        """
        Store last measurement into historian
        The default approach here could be to append a text record file. If the file is reserved for one MeterPoint,
        little or no metadata need be repeated in records. Minimum content should be reading time and datum.
        Implementers will be found to have diverse practices for historians.

        """
        super(CSVMeterPoint, self).store(db, time_intervals)
        return

    def read_database(self, db="csvMeter.db", key=None):
        # If key is specified, Start_Time in database will be checked
        # If a value matching the Start_Time exists, it will be returned
        return super(CSVMeterPoint, self).read_database(db, key)

    @property
    def table_name(self):
        return 'csv_meter_point'

    def get_record(self) -> dict:
        record = super(CSVMeterPoint, self).get_record()
        record.update({
            'meter_id': self.id,
            'csv_path': self.csv_path
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(CSVMeterPoint, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "csv_meter_point",
                "inherits": "meter_point",
                "columns": [
                    {"name": "meter_id", "foreign_key": "meter_point.id", "kwargs": {"primary_key": True}},
                    {"name": "csv_path", "type": "String", "type_args": [255]}
                ]
            }
        ])
        return definitions
