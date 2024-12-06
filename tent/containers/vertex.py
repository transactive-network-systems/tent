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


from tent import Archivable
from tent.utils.helpers import validate_bool, order_vertices


class Vertex(Archivable):
    def __init__(self, marginal_price, prod_cost, power, continuity=True, power_uncertainty=0.0, record=0):
        super(Vertex, self).__init__()
        # Production cost. A dynamic representation of the delivered cost. An ideal is that the cost of electricity
        # using this price should be equivalent to revenue payments over extended time periods. Local assets and
        # neighbor "friends" (meaning belonging to the same business entity) may be offered the "recovery cost" that
        # covers actual costs, but includes no profits. Others can be offered a cost that additionally includes
        # production surplus (aka "profit"). NOTE that this is stated as absolute dollar ($), not dollars per hour
        # ($/h).
        self.cost = float(prod_cost)  # [$] Real & >=0.0

        # The signal. The unit price upon which resource decisions should be based.
        self.marginal_price = float(marginal_price)  # [$/kWh] Real & >=0.0

        # The averaged interval power
        # POWER > 0: generation or importation of real power
        # POWER > 0: consumption or exportation
        self.power = float(power)  # [avg.kW] Real

        # Relative uncertainty of the included power value. This is not a required property, but it will be useful in
        # future versions for the treatment of reliability and resiliency. [dimensionless, 0.01 = 1#]
        self.power_uncertainty = float(power_uncertainty)  # Real & >=0.0

        # Logical continuity flag. This flag should be true if a continuum of power generation, importation,
        # consumption, or importation exists on both sides of the vertex, or if there is only one vertex. Set false if
        # production is disallowed between this vertex and either of its neighbor vertices. (This property is not
        # required and may not be used in early implementations.)
        self.continuity = validate_bool(continuity, 'continuity')  # boolean

        self.record = int(record)  # an integer

    @classmethod
    def vertex_list(cls, vertices):
        if all([isinstance(vertex, cls) for vertex in vertices]):
            return vertices
        elif all([isinstance(vertex, list) for vertex in vertices]):
            return [cls(*[float(i) for i in vertex]) for vertex in vertices]
        elif all([isinstance(vertex, dict) for vertex in vertices]):
            return [cls(**vertex) for vertex in vertices]
        else:
            raise ValueError(f'All vertices in a vertex list must all be vertices, lists, or dicts.'
                             f' Received: {vertices}')

    def get_dict(self):
        vertex_dict = {
            "marginalPrice": self.marginal_price,
            "power": self.power,
            "record": self.record
        }
        return vertex_dict

    @staticmethod
    def check_rationality(v, action="None"):
        # 240206DJH TODO: Delete this try-except, this is not needed once order_vertices is corrected among import
        #           statements.
        # try:
        #     from helpers import order_vertices
        # except ModuleNotFoundError:
        #     from .helpers import order_vertices
        # 211207DJH: This method checks whether a list of vertices is rational, meaning that signed power is a
        #            non-decreasing function of marginal price. These possible actions are allowed:
        #            action=="fix_up": The powers of irrational points are made rational by increasing the powers to be
        #                              equal to that corresponding to the next lower price.
        #            action=="fix_down": The powers of irrational points are made rational by decreasing the powers to
        #                                be equal to that corresponding to the next higher price.
        #            action=="None": No action is taken, but the status is reported.
        #            action=="delete_up": Irrational vertices are deleted if their powers are less than that
        #                                 corresponding to the next lower price.
        #            action=="delete_down": Irrational vertices are deleted if their powers are greater than that
        #                                   corresponding to the next higher price.
        v_star = order_vertices(v)                                               # order the vertices by price and power
        v_temp = [Vertex(float("-inf"), 0, v_star[0].power)]
        v_temp.extend(v_star)
        v_temp.append(Vertex(float("inf"), 0, v_star[-1].power))
        ivf = 0                                                                     # count of irrational vertices found
        tbd = []                                                                         # list of indices to be deleted

        if action in ["fix_up", "delete_up", "None"]:
            for x in range(len(v_star)):                                             # increasing index through vertices
                if v_temp[x+1].power < v_temp[x].power:                       # if vertex power is decreasing with price
                    ivf = ivf + 1                                              # increment the irrational vertex counter
                    if action == "fix_up":              # make irrational vertex power equal to that at next lower price
                        v_temp[x+1].power = v_temp[x].power
                    elif action == "delete_up":                           # Append the index of the vertex to be deleted
                        tbd.append(x)
        else:
            for x in range(len(v_star), 0, -1):                                      # decreasing index through vertices
                if v_temp[x].power > v_temp[x+1].power:
                    ivf = ivf + 1
                    if action == "fix_down":
                        v_temp[x].power = v_temp[x+1].power
                    elif action == "delete_down":
                        tbd.append(x-1)

        v_star = v_temp[1:len(v_star)+1]  # Remove the two extended vertices

        v_star = [v_star[x] for x in range(len(v_star)) if x not in tbd]  # Delete any irrational vertices

        v_star = order_vertices(v_star)

        return v_star[:], ivf

    @property
    def data_manager(self):
        return self.sd_curve.market.data_manager
        # TODO: Need references to sd_curve?

    @property
    def table_name(self):
        return 'vertex'

    def get_record(self) -> dict:
        # TODO: Need get_record implemented.
        raise NotImplementedError

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(Vertex, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "vertex",
                "columns": [
                    {"name": "curve_id", "foreign_key": "sd_curve.id"},
                    {"name": "id", "type": "Uuid", "identity": True, "kwargs": {"primary_key": True}},
                    {"name": "marginal_price", "type": "Float"},
                    {"name": "power", "type": "Float"},
                    {"name": "prod_cost", "type": "Float"},
                    {"name": "continuity", "type": "Boolean"},
                    {"name": "power_uncertainty", "type": "Float"},
                    {"name": "record", "type": "Integer"}
                ]
            }
        ])
        return definitions
