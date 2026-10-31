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

from datetime import datetime, timedelta
from sortedcontainers import SortedDict
from weakref import proxy

from tent.base import Archivable
from tent.utils.helpers import safe_weak_proxy_callback


class TimeInterval(Archivable):
    """
    The TimeInterval is the Market time interval.
    """

    def __init__(self, market, duration, start_time):
        super(TimeInterval, self).__init__()
        self.duration: timedelta = duration if isinstance(duration, timedelta) else timedelta(seconds=duration)
        self.market = proxy(market, safe_weak_proxy_callback(self, 'market'))
        self.start_time: datetime = start_time
        # TODO: Should we have an owner attribute in addition to market?

        # The TimeInterval name is used by the transactive record, and must be unique to its market.
        self.name: str = f'{self.market.name}@{self.start_time.isoformat()}'

    @property
    def data_manager(self):
        return self.market.data_manager

    @property
    def end_time(self):
        return self.start_time + self.duration

    @property
    def table_name(self):
        return 'time_interval'

    def get_record(self) -> dict:
        record = super(TimeInterval, self).get_record()
        record.update({
            "duration": self.duration,
            "market_id": self.market.id,
            "name": self.name,
            "start_time": self.start_time
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(TimeInterval, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "time_interval",
                "columns": [
                    {"name": "id", "type": "Uuid", "identity": True, "kwargs": {"primary_key": True}},
                    {"name": "name", "type": "String", "type_args": [255]},
                    {"name": "market_id", "foreign_key": "market.id"},
                    {"name": "start_time", "type": "DateTime"},
                    {"name": "duration", "type": "Interval"}
                ],
                "indices": [
                    {"name": "idx_ti_instance", "columns": ["market_id", "start_time", "duration"], "unique": True}
                ]
            }
        ])
        return definitions


class IntervalSeries:
    def __init__(self, market, first_start_time: datetime, duration: timedelta, number_of_intervals: int):
        self.first_start_time = first_start_time
        self.interval_duration = duration
        self.market = proxy(market, safe_weak_proxy_callback(self, 'market'))
        # TODO: How to have intervals be unique to markets, but comparable between markets?
        # TODO: See the TODO notes in LocalAsset.get_extended_prices(). --- These concern TimeInterval handling.
        self.number_of_intervals = number_of_intervals
        self.total_duration = duration * number_of_intervals
        # Keys are datetime start times already comparable; no custom key function needed.
        self._intervals: dict[datetime, TimeInterval] = SortedDict()

        for i in range(number_of_intervals):
            # Pass original market object (not the already proxied self.market) to avoid proxy-of-proxy TypeError
            new_interval = TimeInterval(market, duration, first_start_time + duration * i)
            self._intervals[new_interval.start_time] = new_interval
            new_interval.archive()

    def __getitem__(self, index):
        l = tuple(self._intervals.values())
        return l[index]

    def __iter__(self):
        # Allows IntervalSeries to act as an iterator.
        return iter(self._intervals.values())

    def __len__(self):
        return len(self._intervals)

    @property
    def first(self):
        return self[0]

    @property
    def last(self):
        return self[-1]

    @property
    def start_times(self):
        return iter(self._intervals.keys())

    def get_by_start_time(self, start_time: datetime) -> TimeInterval:
        """Get the TimeInterval by its start time."""
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time)
        if not isinstance(start_time, datetime):
            raise TypeError("start_time must be a datetime or an ISO format string.")
        return self._intervals.get(start_time, None)

    def destroy(self):
        self._intervals = {}
