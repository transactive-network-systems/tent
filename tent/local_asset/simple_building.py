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

from tent.containers import Vertex
from tent.local_asset import LocalAsset


class SimpleBuilding(LocalAsset):

    def __init__(self, *args, **kwargs):
        self.off_peak_vertex = Vertex(price=float('Inf'), quantity=-10, cost=0)
        self.on_peak_vertex0 = Vertex(price=0.03, quantity=-12, cost=0)
        self.on_peak_vertex1 = Vertex(price=0.05, quantity=-8, cost=0)
        # TODO: Does it actually make sense for these to be default vertices?
        #  That would imply they are a single SDCurve, no? They don't appear to be....
        super(SimpleBuilding, self).__init__(
            default_vertices=[self.off_peak_vertex, self.on_peak_vertex0, self.on_peak_vertex1],
            *args, **kwargs)

    def schedule(self, market):
        self.schedule_power(market)
        self.update_vertices(market)
        self.schedule_calculated = True

    def schedule_power(self, market):
        # TODO: Why are we always scheduling the power of a default vertex? What is this supposed to achieve?
        #  Shouldn't this do the same as update_vertices, and base this on the hour of the day?
        for time_interval in market.time_intervals:
            # TODO: This previously put a vertex in scheduled_powers.
            #  Changed to put the quantity (power) of the vertex.
            #  Check this works as expected now?
            self.scheduled_powers.update(time_interval, self.off_peak_vertex.quantity)

    def update_vertices(self, market):
        for time_interval in market.time_intervals:
            if time_interval.start_time.hour in [0, 1, 2, 3, 4, 17, 18, 19, 20, 21, 22, 23]:
                self.active_vertices.update(time_interval, self.off_peak_vertex)
            elif time_interval.start_time.hour in [5, 6, 7, 8, 9, 10, 11, 12, 13, 14]:
                self.active_vertices.update(time_interval, [self.on_peak_vertex0, self.on_peak_vertex1])
            else:
                raise RuntimeError('Undefined hour of day.')
