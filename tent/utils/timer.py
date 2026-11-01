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
import logging

from tent.utils.helpers import require_aware

from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)

class Timer(object):
    created_time = None
    sim_one_hr_in_sec = 1200
    sim_start_time = None
    simulation = False
    tz = timezone.utc

    @classmethod
    def now(cls, tz: tzinfo = None):
        """
        Calculate current time based on the amount of time has passed since this object is created
        :return:
        """
        tz = cls._as_tzinfo(tz) if tz is not None else cls.tz
        now = datetime.now(timezone.utc)
        if cls.simulation:
            ratio = 3600 / cls.sim_one_hr_in_sec
            time_diff = now - cls.created_time
            simulation_time_diff = time_diff * ratio
            try:
                now = cls.sim_start_time + simulation_time_diff
            except OverflowError:
                _log.critical(f"Simulation time overflowed (year 10000+). Congrats, you made it.")
                raise
        return now.astimezone(tz)

    @staticmethod
    def _as_tzinfo(tz) -> tzinfo:
        if isinstance(tz, tzinfo):
            return tz
        if isinstance(tz, str):
            return ZoneInfo(tz)
        raise TypeError(f'tz must be a tzinfo or an IANA zone name, not {type(tz).__name__}')

    @classmethod
    def set(cls, sim_one_hr_in_sec=None, sim_start_time=None, simulation=False, tz=None, anchor: datetime = None):
        """
        Configure the clock. `anchor` is the real time at which simulation time equals sim_start_time (default: now).
        TransactiveNode passes its own creation time, so the archived transactive_node.created value is the exact anchor
        from which any recorded simulation time can be converted back to real time. Parameters that are not given keep
        their current values, so Timer.set(simulation=False) pauses the simulated clock without forgetting its settings.
        """
        if tz is not None:
            cls.tz = cls._as_tzinfo(tz)
        cls.created_time = (anchor if anchor is not None else datetime.now(timezone.utc)).astimezone(cls.tz)
        if sim_one_hr_in_sec is not None:
            if sim_one_hr_in_sec <= 0:
                raise ValueError(f'sim_one_hr_in_sec must be positive; got {sim_one_hr_in_sec}')
            cls.sim_one_hr_in_sec = sim_one_hr_in_sec
        if sim_start_time is not None:
            # A naive start is taken to be in the clock's zone, the documented convention for configured values.
            cls.sim_start_time = require_aware(sim_start_time, 'sim_start_time', assume=cls.tz)
        if simulation and (not cls.sim_one_hr_in_sec or cls.sim_start_time is None):
            raise ValueError("sim_one_hr_in_sec and sim_start_time must be provided when simulation is True")
        cls.simulation = simulation
