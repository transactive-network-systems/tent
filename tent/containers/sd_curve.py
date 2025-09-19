import logging

from sortedcontainers import SortedSet
try:  # Python <3.11 compatibility
    from typing import Self  # type: ignore
except ImportError:  # pragma: no cover
    from typing import TypeVar
    Self = TypeVar("Self")
from weakref import proxy

from tent.base import Archivable, Component
from tent.containers.measurement import Measurement
from tent.containers.time_interval import TimeInterval
from tent.containers.vertex import Vertex
from tent.enumerations import MeasurementType, MeasurementUnit
from tent.utils.helpers import safe_weak_proxy_callback

_log = logging.getLogger(__name__)


class SDCurve(Archivable):
    """
    Aggregates Vertex objects into their supply or demand curve.
    """

    # TODO: If there is only one vertex, should we:
    #  - overwrite the vertex price with inf on read?
    #  - Alternately, should we add check for single vertex to check_rationality(), update() or elsewhere?
    #       - The former would avoid storing a vertex with a price of inf, in case more vertices were added later.
    #  - A key question is, do we __always__ need to have a price of inf for a single vertex?
    #  - There are currently (at least) checks (which overwrite the value) in entity and in neighbor.update_vertices()
    def __init__(self,
                 measurement,
                 interval: TimeInterval | None,
                 vertices: list | None = None,
                 archive: bool = True
                 ):
        super(SDCurve, self).__init__()
        self.__interval: TimeInterval = proxy(interval, safe_weak_proxy_callback(self, 'interval')
                                              ) if interval else None
        self.__measurement: Measurement = proxy(measurement, safe_weak_proxy_callback(self, 'measurement')
                                                ) if measurement else None
        # TODO: This is a rather different default than is used in Entity.default_vertices....
        self.vertices: SortedSet = SortedSet(self._parse_vertices(vertices) if vertices
                                             else [Vertex(price=0.0, quantity=0.0, continuity=False, curve=self)])

    @property
    def empty(self):
        return True if not self.vertices else False

    @property
    def interval(self):
        return self.__interval

    @property
    def log_report(self):
        return [(v.price, v.quantity) for v in self.vertices]

    @property
    def measurement(self):
        return self.__measurement

    @property
    def costs(self):
        """Returns a list of the costs in the SDCurve."""
        return [v.cost for v in self.vertices]

    @property
    def prices(self):
        """Returns a list of the prices in the SDCurve."""
        return [v.price for v in self.vertices]

    @property
    def quantities(self):
        """Returns a list of the quantities in the SDCurve."""
        return [v.quantity for v in self.vertices]

    @property
    def max_price(self):
        """Returns the maximum price in the SDCurve."""
        return max(self.prices, default=None)

    @property
    def min_price(self):
        """Returns the minimum price in the SDCurve."""
        return min(self.prices, default=None)

    @property
    def max_price_vertex(self):
        """Returns the vertex with the maximum price in the SDCurve."""
        return max(self.vertices, key=lambda v: v.price, default=None)

    @property
    def min_price_vertex(self):
        """Returns the vertex with the minimum price in the SDCurve."""
        return min(self.vertices, key=lambda v: v.price, default=None)

    # TODO: Min an max quantity methods are attempting to compare floats and vertices.
    #  These really need to work with one or the other.  There can be a second function that works with the other.
    def max_quantity(self, limit: float = None) -> float:
        default = limit if limit is not None else 0.0 # TODO: Limit is a float, but default should be a Vertex or (possibly?) None.
        # TODO: max_vertex_quantity is a vertex, not a quantity. (BUG -> unless default is used -- then it is a float).
        max_vertex_quantity =  max(self.vertices, key=lambda v: v.quantity, default=default).quantity
        # The upper operational constraint must be less than or equal to the object's hard physical constraint.
        constrained_quantity = min(limit, max_vertex_quantity) if limit is not None else max_vertex_quantity
        return constrained_quantity

    def min_quantity(self, limit: float = None) -> float:
        default = limit if limit is not None else 0.0
        min_vertex_quantity =  min(self.vertices, key=lambda v: v.quantity, default=default).quantity
        # The upper operational constraint must be less than or equal to the object's hard physical constraint.
        constrained_quantity = max(limit, min_vertex_quantity) if limit is not None else min_vertex_quantity
        return constrained_quantity

    def has_vertex_at_quantity(self, quantity: float) -> bool:
        return bool(next(filter(lambda v: v.quantity == quantity, self.vertices), False))

    def get_vertices_at_quantity(self, quantity: float):  # -> SDCurve (annotated dynamically for py<3.11)
        return SDCurve(None, self.interval, [v.quantity for v in  self.vertices if v.quantity == quantity])

    @staticmethod
    def _parse_vertices(vertices):
        if all([isinstance(vertex, Vertex) for vertex in vertices]):
            return vertices
        elif all([isinstance(vertex, list) for vertex in vertices]):
            return [Vertex(*[float(i) for i in vertex]) for vertex in vertices]
        elif all([isinstance(vertex, dict) for vertex in vertices]):
            return [Vertex(**vertex) for vertex in vertices]
        else:
            raise ValueError(f'All vertices in a vertex list must all be vertices, lists, or dicts.'
                             f' Received: {vertices}')

    def apply_constraints_and_bounds(self, minimum_power: float = None, maximum_power: float = None):
        """ Applies constraints and bounds to the SDCurve vertices whereby:
                * The passed arguments are hard minimum and maximum power limits.
                * The maximum and minimum vertices in the SDCurve are soft constraints.
        """
        # # TODO: This seemed wrong. Shouldn't min_power, for instance, be max(self.min_power, min(vertex_powers))?
        #    I think this may have been the result of assuming demand was negative and that the limits provided for
        #    demand entities would be positive, but that is not going to always be the case.
        #    This line of thinking informs how the replacement lines below are done.
        #    If this was correct before, change it back, otherwise, delete this commented block.
        # vertex_powers = [x.quantity for x in self.vertices]  # [avg.kW]
        # min_power = max(-self.max_power, min(vertex_powers))  # [avg.kW]
        # max_power = min(-self.min_power, max(vertex_powers))  # [avg.kW]

        # Find the minimum and maximum powers from the SDCurve.
        #  These are soft constraints that represent a range of flexibility.
        min_power = self.min_quantity(limit=minimum_power)
        max_power = self.max_quantity(limit=maximum_power)

        # If flexibility is being offered (i.e., more than one vertex),
        #  ensure vertices exist at the extrema of the flexibility range.
        if min_power != max_power:
            if not self.has_vertex_at_quantity(min_power):
                self.update(Vertex(price=(self.price_from_vertices(min_power)), cost=0, quantity=min_power))
            if not self.has_vertex_at_quantity(max_power):
                self.update(Vertex(price=(self.price_from_vertices(max_power)), cost=0, quantity=max_power))
        elif len(self.vertices) == 1:
            self.vertices[0].price = float('inf')
            self.vertices[0].record = 0
        else:  # TODO: Does this correctly identify the neighbor when called from prep_transactive_signal?
               #  The SDCurve was created by a Market.sum_vertices()...
            _log.error(f'Unexpected flexibility logic in while bounding SDCurve in {self.measurement.source.name}. '
                       f'Maximum and minimum power are the same, but in separate vertices.')

        # Trim the list of vertices to remove any that are outside the soft flexibility range.
        self.vertices = [x for x in self.vertices if min_power <= x.quantity <= max_power]

    def check_rationality(self, action="None"):
        """The check_rationality method checks whether a list of vertices is rational, meaning that signed power is a
                non-decreasing function of marginal price.

                These possible actions are allowed:
                action=="fix_up": The powers of irrational points are made rational by increasing the powers to be
                                  equal to that corresponding to the next lower price.
                action=="fix_down": The powers of irrational points are made rational by decreasing the powers to
                                    be equal to that corresponding to the next higher price.
                action=="None": No action is taken, but the status is reported.
                action=="delete_up": Irrational vertices are deleted if their powers are less than that
                                     corresponding to the next lower price.
                action=="delete_down": Irrational vertices are deleted if their powers are greater than that
                                       corresponding to the next higher price.
        """
        v_temp = self.vertices.copy()
        v_temp.add(Vertex(float("-inf"), 0, self.vertices[0].quantity))
        v_temp.add(Vertex(float("inf"), 0, self.vertices[-1].quantity))
        ivf = 0                                         # count of irrational vertices found
        irrational_vertices = []                        # list of irrational vertices to be deleted

        if action in ["fix_up", "delete_up", "None"]:
            for lower, upper in zip(v_temp, v_temp[1:]):
                if upper.quantity < lower.quantity:     # if vertex power is decreasing with price
                    ivf = ivf + 1                       # increment the irrational vertex counter
                    if action == "fix_up":              # make irrational vertex power equal to that at next lower price
                        upper.quantity = lower.quantity
                    elif action == "delete_up":         # Append the index of the vertex to be deleted
                        irrational_vertices.append(upper)
        else:
            for lower, upper in zip(reversed(v_temp[:-1]), reversed(v_temp)):
                if lower.quantity > upper.quantity:
                    ivf = ivf + 1
                    if action == "fix_down":
                        lower.quantity = upper.quantity
                    elif action == "delete_down":
                        irrational_vertices.append(lower)

        v_temp.difference_update(irrational_vertices)   # Remove remaining irrational vertices from the SDCurve.
        # TODO: Should this update the SDCurve or return a new one? (This seems to do both).
        #  Perhaps make explicitly in-place, rename to "rationalize" and only return the irrational vertex count?
        # Return rationalized curve (without the extra infinite vertices) and the nuber of irrational vertices found.
        return SDCurve(self.measurement, self.interval, v_temp[1:-1]), ivf

    def prod_cost_from_vertices(self, power):
        # Infer production cost for a power from the
        # vertices that define an object's supply curve
        #
        # If the neighbor is not a "friend" (an insider that is owned by the same
        # business entity), it is probably represented by a production cost that
        # includes both production costs and profits. If, however, the neighbor is
        # a friend, it may offer a blended price that eliminates some, if not all,
        # local profit.
        #
        # PRESUMPTIONS:
        # - This method applies to NeighborModel and LocalAssetModel objects.
        # Method properties must be named identically in these object classes.
        # - A supply curve exists for the object, as defined by a set of active
        # vertices. The vertices are up-to-date. See struct Vertex().
        # - Vertex property "cost" defines the total, accurate production cost
        # for the object at the vertex's power. The marginal price and slope of
        # segment between successive vertices must be used to infer production
        # cost between vertices.
        # - Production costs must be accurate and meaningful. An ideal is that
        # the production costs estimate or displace the dynamic delivered cost
        # of electricity. If production costs are well-tracked, production
        # costs should be equivalent to electricity costs over time.
        #
        # INPUTS:
        # obj - the neighbor model 3object
        # ti - the active time interval
        # pwr - the average power at which the production cost is to be
        # calculated. This will be the scheduled power during scheduling.
        # It may be power at other active vertices for the calculation of
        # flexibility.
        #
        # OUTPUTS:
        # cost - production cost in the time interval ti [$]

        # We presume only generation and importation of electricity (i.e., p>0)
        # contribute to production costs
        if power < 0.0:
            return 0.0

        if len(self.vertices) == 0:  # No vertices were found in the given time interval
            _log.info(f'No active vertices exist for {self.measurement.source.name}. Unable to find production cost.')
            return None

        elif len(self.vertices) == 1:  # One vertex was found in the given time interval
            # There is no flexibility. Return the constant production as indicated by the lone vertex.
            return self.vertices[0].cost  # production cost [$]

        else:  # There is more than one vertex
            # Special case when neighbor is at its minimum power.
            if power <= self.vertices[0].quantity:
                # Production cost is known from the vertex cost.
                return self.vertices[0].cost  # production cost [$]

            # Special case when neighbor is at its maximum power.
            elif power >= self.vertices[-1].quantity:
                # Production cost may be inferred from the blended price at the maximum production vertex.
                return self.vertices[-1].cost  # production cost [$]

            else:  # Remaining case is that neighbor power is between defined production indices.
                for lower, upper in zip(self.vertices, self.vertices[1:]):
                    if lower.quantity <= power < upper.quantity:
                        # The power is found to lie between two of the vertices.

                        # Constant term (an integration constant from lower vertex
                        a0 = lower.cost  # [$]

                        # First-order term for the segment is based on the marginal price of the lower vertex
                        #  and the power exceeding that of the lower vertex. [$] = [$/kWh] * [$/h] * [h]
                        a1 = lower.price * (power - lower.quantity) * self.interval.duration.total_seconds() / 3600

                        # Second-order term is derived from the slope of the current segment of the supply curve
                        #  and the square of the power in excess of the lower vertex.
                        if upper.quantity == lower.quantity:
                            # An exception is needed for infinite slope to avoid
                            # division by zero
                            a2 = 0.0  # [$]

                        else:
                            # [$/kWh] / [$/kWh/kW]
                            a2 = (upper.price - lower.price) / (upper.quantity - lower.quantity)
                            a2 = a2 * (power - lower.quantity) ** 2  # [$/h]
                            a2 = a2 * self.interval.duration.total_seconds() / 3600  # [$]

                        # Finally, calculate the production cost for the time interval by summing the terms
                        return a0 + a1 + a2  # production cost [$]

    def power_from_vertices(self, price: float) -> float:
        if len(self.vertices) == 1:  # One active vertex was found in the given time interval.
            # Presume that using a single production vertex is shorthand for constant, inelastic production.
            return self.vertices[0].quantity  # [avg.kW]

        else:  # Multiple active vertices were found
            if price < self.vertices[0].price:
                # Special case where marginal price is before first vertex. The power is at its minimum.
                return self.vertices[0].quantity  # [kW]

            elif price >= self.vertices[-1].price:
                # Special case where marginal price is after the last vertex. The power is at its maximum.
                return self.vertices[-1].quantity  # [kW]

            else:  # The marginal price lies among the active vertices.
                for lower, upper in zip(self.vertices, self.vertices[1:]):
                    if lower.price <= price < upper.price:
                        # The marginal price falls between two vertices that are sloping upward to the right.
                        # Interpolate between the vertices to find the power production.
                        return (lower.quantity
                                + (price - lower.price) * (upper.quantity - lower.quantity)
                                / (upper.price - lower.price))  # [avg.kW]

                    elif price == lower.price == upper.price:
                        # The marginal price is the same as for two vertices that lie vertically at the same marginal
                        # price. Assign the power of the vertex having greater power.
                        return upper.quantity  # [kW]

                    elif price == lower.price:
                        # The marginal price is the same as the indexed active vertex. Use its power value.
                        return lower.quantity  # [kW]

    def price_from_vertices(self, power: float) -> float:
        """ Given a power [avg.kW], determine the corresponding marginal price [$/kWh] from the SDCurve.
        """
        # TODO: This and power_from_vertices assume a rational curve (power monotonic with price). Is this actually guaranteed?
        if power < self.vertices[0].quantity:
            # The power is below the first vertex. Marginal price is indeterminate.
            #  Return price of the first vertex and warn. (This should be an unlikely condition.)
            _log.warning('Indeterminate price: Power was lower than first vertex.')
            return self.vertices[0].price

        elif power >= self.vertices[-1].quantity:
            # The power is above the last vertex. Marginal price is indeterminate.
            # Return price of the last vertex and warn. (This should be an unlikely condition.)
            _log.warning('Indeterminate price: Power was greater than last vertex.')
            return self.vertices[-1].price

        # # The marginal power lies among the active vertices.
        for lower, upper in zip(self.vertices, self.vertices[1:]):
            if lower.quantity == power == upper.quantity:
                # The segment is horizontal. Marginal price is indefinite. Return the second vertex price and warn.
                _log.warning('Indeterminate price due to horizontal segment of SDCurve. Returning second price.')
                return upper.price
            elif lower.quantity <= power < upper.quantity:
                # The segment is not horizontal. Interpolate on the segment.
                slope = (upper.price - lower.price) / (upper.quantity - lower.quantity)  # [$/kWh/kW]
                return lower.price + (power - lower.quantity) * slope  # [$/kWh]

    @staticmethod
    def get_prices_from_curves(curves: list) -> list[float]:
        """
        Returns a simple list of the Vertex prices [$/kWh] from a list of SDCurves.

        Parameters:
            curves: list of SDCurve objects

        Returns: (list) of unique prices [$/kwh] found among order_list

        Notes:
        - Care is used to make sure a flat list of prices is returned.
        """
        # TODO: Is there actually any reason to return a list?
        return list({v.price for c in curves for v in c.vertices})

    @classmethod
    def aggregate_curves(cls, curves: list):
        """
        Return a new Order object that has been aggregated from a list of Order objects.
        Parameters:
             curves: list of Order objects
        Returns: New Order object that has been aggregated from the provided list of Order objects.
        """
        # TODO: Finish this method.
        # TODO: This does not seem to return a curve.
        # TODO: Perform unit testing on this method.
        return [x for x in (cls.get_prices_from_curves(curves)) if abs(x) != float('inf')]

    def __bool__(self):
        return bool(self.vertices)

    def __len__(self):
        return len(self.vertices)

    def update(self, value: Vertex | dict | list[float|int] | list[Vertex] | list[list[float|int]] | list[dict]):
        # TODO: Should this have a default value like in the constructor?
        if isinstance(value, (Vertex, dict)) or all(isinstance(v, (int, float)) for v in value):
            value = [value]
        new_vertices = self._parse_vertices(value)
        return self.vertices.update(new_vertices)

    @property
    def data_manager(self):
        return self.measurement.source.tn.data_manager

    @property
    def table_name(self):
        return 'sd_curve'

    def get_record(self) -> dict:
        record = super(SDCurve, self).get_record()
        record.update({
            "measurement_id": self.measurement.id if self.measurement else None,
            "owner_id": self.measurement.source.id if self.measurement else None,
            # "parent_curve_id": self.parent.id if self.measurement else None,
            "time_interval_id": self.interval.id if self.interval else None,
            "timestamp": self.interval.start_time if self.interval else None
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(SDCurve, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "sd_curve",
                "columns": [
                    {"name": "id", "type": "Uuid", "identity": True, "kwargs": {"primary_key": True}},
                    {"name": "measurement_id", "foreign_key": "measurement.id"},
                    {"name": "owner_id", "foreign_key": "component.id"},
                    # {"name": "parent_curve_id", "foreign_key": "sd_curve.id"}, # TODO: Is this needed for order?
                    {"name": "time_interval_id", "foreign_key": "time_interval.id"},
                    {"name": "timestamp", "type": "DateTime"}
                ]
            }
        ])
        return definitions


class SDMeasurement(Measurement):
    DATA_TYPE = SDCurve
    def __init__(self,
                 source: Component,
                 measurement_type: MeasurementType = MeasurementType.Vertex,
                 measurement_unit: MeasurementUnit = MeasurementUnit.kW):
                # TODO: Adding vertex_unit: MeasurementUnit would allow SDCurves for quantities other than power.
                #  This would require also adding the unit to the TransactiveSignal and making sure it is passed
                #  everywhere it needs to be.
        if not 'Vertex' in measurement_type.name:
            raise ValueError('SDMeasurement must contain vertex-type objects.')
        super(SDMeasurement, self).__init__(source, MeasurementType.Vertex, measurement_unit)

    @property
    def empty(self):
        return True if all(c.empty for c in self._data.values()) else False

    @property
    def log_report(self):
        return {ti.name: sdc.log_report for ti, sdc in self.all}

    @property
    def values(self):
        return (sdc for _, sdc, in self.all)

    def total(self, market=None):
        raise NotImplementedError

    def update(self, time_interval: TimeInterval,
               value: SDCurve | list[Vertex] | list[list[float]] | list[dict],
               archive: bool = True, replace: bool = False):
        value = value.vertices if isinstance(value, SDCurve) else value
        super(SDMeasurement, self).update(time_interval, value, archive, replace)
