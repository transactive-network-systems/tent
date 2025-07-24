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
from weakref import proxy

from tent.base import Archivable
from tent.utils.helpers import safe_weak_proxy_callback, validate_bool

# TODO: Refactor argument order.
#  - Curve shouldn't have a default.
#  - Order.Vertex redid the argument order to deprioritize cost:
#     def __init__(self, price: float, order_id: int or None = None, timestamp: datetime = datetime.now(),
#                  continuity: bool = True, quantity: float = 0.0, cost: float = 0.0):
#     Find a signature that works all the time with correct priorities for arguments.
#     All calls to Vertex will need to be checked for the correct signature.
class Vertex(Archivable):
    def __init__(self,
                 price: float,
                 cost: float = 0.0,
                 quantity: float = 0.0,
                 continuity: bool = True,
                 uncertainty: float = 0.0,
                 record: int = 0, # TODO: Remove this and move to SDCurve.
                 curve = None
                 ):  # TODO: Order.Vertex had a timestamp argument. Should this or should it be in SDCurve?
                     #  Maybe dt_created, dt_modified, etc. in SDCurve?
                     #  Order.Vertex had this explanation: This should be updated by the market agent to reflect the time that the Vertex is
                     #    received by the market agent. Defaults to datetime.datetime.now().
        super(Vertex, self).__init__()
        self.curve = proxy(curve, safe_weak_proxy_callback(self, 'measurement')) if curve else None
        # TODO: Clean up explanations.
        # Production cost. A dynamic representation of the delivered cost. An ideal is that the cost of electricity
        # using this price should be equivalent to revenue payments over extended time periods. Local assets and
        # neighbor "friends" (meaning belonging to the same business entity) may be offered the "recovery cost" that
        # covers actual costs, but includes no profits. Others can be offered a cost that additionally includes
        # production surplus (aka "profit"). NOTE that this is stated as absolute dollar ($), not dollars per hour
        # ($/h).
        # (Order.Vertex explanation) Needed for historical inheritance from class Vertex. Defaults to 0.0 $.

        self.cost: float = float(cost)  # [$] Real & >=0.0

        # The signal. The unit price upon which resource decisions should be based.
        # (Order.Vertex explanation) Price at which an inflection occurs in a piecewise linear supply or demand curve.
        self.price: float = float(price)  # [$/kWh] Real & >=0.0

        # The averaged interval power
        # POWER > 0: generation or importation of real power
        # POWER > 0: consumption or exportation
        # (Order.Vertex explanation) The average power that, together with its price,
        #   defines an inflection in a supply of demand curve. Defaults to 0.0 avg. kW.
        self.quantity: float = float(quantity)  # [avg.kW] Real

        # Relative uncertainty of the included power value. This is not a required property, but it will be useful in
        # future versions for the treatment of reliability and resiliency. [dimensionless, 0.01 = 1#]
        self.uncertainty: float = float(uncertainty)  # Real & >=0.0

        # Logical continuity flag. This flag should be true if a continuum of power generation, importation,
        #  consumption, or importation exists on both sides of the vertex, or if there is only one vertex. Set false if
        #  production is disallowed between this vertex and either of its neighbor vertices. (This property is not
        #  required and may not be used in early implementations.)
        # (Order.Vertex explanation) Set True to indicate that quantity is defined between this Vertex and another nearest
        #  Vertex of the same SDCurve object having less quantity magnitude.
        self.continuity: bool = validate_bool(continuity, 'continuity')  # boolean

    # TODO: Do we need this property, or can we just rely on finding this in the SDCurve?
    @property
    def record(self):
        return self.curve.vertices.index(self)

    def get_dict(self):
        vertex_dict = {
            "marginalPrice": self.price,
            "power": self.quantity,
            "record": self.record
        }
        return vertex_dict

    def __lt__(self, other):
        return (self.price, self.quantity) < (other.price, other.quantity)

    def __gt__(self, other):
        return (self.price, self.quantity) > (other.price, other.quantity)

    def __le__(self, other):
        return (self.price, self.quantity) <= (other.price, other.quantity)

    def __ge__(self, other):
        return (self.price, self.quantity) >= (other.price, other.quantity)

    def __eq__(self, other):
        return (self.price, self.quantity) == (other.price, other.quantity)

    # TODO: Check that if we update other properties, that we get the updated version, not the original.
    def __hash__(self):
        return hash((self.price, self.quantity))

    @property
    def data_manager(self):
        return self.curve.data_manager

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

    # TODO: Order.Vertex had this method. Is this really achieving anything that copy would not on its own?
    #  If so, does it need further update for the new version of this Vertex?
    #  Should it have more properties?
    # def __copy__(self):
    #     """
    #     Returns a copy of this Vertex object.
    #     This is a redefinition of method copy.copy() that will be evoked when a Vertex object is to be copied.
    #
    #     Parameters: None
    #
    #     Returns: (Vertex) A copy of this Vertex object.
    #     """
    #     return Vertex(
    #         price=self.price,
    #         curve=self.curve,
    #         # timestamp=self.time_stamp,
    #         continuity=self.continuity,
    #         quantity=self.quantity,
    #         cost=self.cost
    #     )