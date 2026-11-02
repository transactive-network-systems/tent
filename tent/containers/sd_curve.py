import logging

from copy import copy
from sortedcontainers import SortedSet
from typing import Self
from weakref import ProxyType, proxy

from tent.base import archiving
from tent.base import Archivable, Component
from tent.containers.measurement import Measurement
from tent.containers.time_interval import TimeInterval
from tent.containers.vertex import Vertex
from tent.enumerations import MeasurementType, MeasurementUnit
from tent.containers.curve_simplification import simplify_vertices
from tent.utils.helpers import validate_bool, safe_weak_proxy_callback
from tent.utils.timer import Timer

_log = logging.getLogger(__name__)


class SDCurve(Archivable):
    """
    Aggregates Vertex objects into their supply or demand curve.
    """
    # A quantity this close (relative to the end vertex's quantity, at least 1 kW absolute) beyond either end of the
    # curve is read as that end vertex, silently: a scheduled power and the curve it was read from differ by rounding
    # (a consensus balance point landed 0.07 percent past a near-inelastic residual curve on every pass). Further out
    # the price is indeterminate and a warning says so.
    END_VERTEX_TOLERANCE = 1e-3

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
        # Guard against double-wrapping: if the object is already a weakref proxy, use it as-is.
        if interval and not isinstance(interval, ProxyType):
            self.__interval: TimeInterval = proxy(interval, safe_weak_proxy_callback(self, 'interval'))
        else:
            self.__interval = interval
        if measurement and not isinstance(measurement, ProxyType):
            self.__measurement: Measurement = proxy(measurement, safe_weak_proxy_callback(self, 'measurement'))
        else:
            self.__measurement = measurement
        # TODO: This is a rather different default than is used in Entity.default_vertices....
        self.vertices: SortedSet = SortedSet()
        if vertices is not None:
            self.vertices.update(self._parse_vertices(vertices))
        else:
            self.vertices.add(Vertex(price=0.0, quantity=0.0, continuity=False, curve=self))
        if archive and self.__measurement is not None:
            self.archive()

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
        default = limit if limit is not None else 0.0
        # Handle empty vertices list
        if not self.vertices:
            return default
        max_vertex_quantity = max(self.vertices, key=lambda v: v.quantity).quantity
        # The upper operational constraint must be less than or equal to the object's hard physical constraint.
        constrained_quantity = min(limit, max_vertex_quantity) if limit is not None else max_vertex_quantity
        return constrained_quantity

    def min_quantity(self, limit: float = None) -> float:
        default = limit if limit is not None else 0.0
        # Handle empty vertices list
        if not self.vertices:
            return default
        min_vertex_quantity = min(self.vertices, key=lambda v: v.quantity).quantity
        # The upper operational constraint must be less than or equal to the object's hard physical constraint.
        constrained_quantity = max(limit, min_vertex_quantity) if limit is not None else min_vertex_quantity
        return constrained_quantity

    def has_vertex_at_quantity(self, quantity: float) -> bool:
        return bool(next(filter(lambda v: v.quantity == quantity, self.vertices), False))

    def get_vertices_at_quantity(self, quantity: float):  # -> SDCurve (annotated dynamically for py<3.11)
        # Return an SDCurve containing the Vertex objects that match the given quantity.
        return SDCurve(None, self.interval, [v for v in self.vertices if v.quantity == quantity])

    @staticmethod
    def _parse_vertices(vertices):
        # Handle empty vertex list
        if not vertices:
            return []
        if all([isinstance(vertex, Vertex) for vertex in vertices]):
            # Copy: a curve owns its vertices. Sharing Vertex objects between curves (an entity's default vertices and
            # its active vertices, a received signal and the neighbor's active curve) let in-place corrections such as
            # losses and demand charges compound across intervals and markets.
            return [copy(vertex) for vertex in vertices]
        elif all([isinstance(vertex, list) for vertex in vertices]):
            # Filter out empty lists
            return [Vertex(*[float(i) for i in vertex]) for vertex in vertices if vertex]
        elif all([isinstance(vertex, dict) for vertex in vertices]):
            return [Vertex(**vertex) for vertex in vertices]
        else:
            raise ValueError(f'All vertices in a vertex list must all be vertices, lists, or dicts.'
                             f' Received: {vertices}')

    def simplified(self, tolerance: float, max_vertices: int | None = None) -> tuple['SDCurve', float]:
        """
        A new curve (same measurement and interval, not archived) with the interior vertices that lie within
        `tolerance` (a fraction of the curve's price and quantity spans) of the simplified polyline removed, and at most
        `max_vertices` vertices when a cap is given; with it, the tolerance finally applied. The first and last vertex
        and vertices at an infinite price are always kept; the costs of the kept vertices are recomputed from the
        simplified polyline. See tent.containers.curve_simplification for the rules and what is preserved.
        """
        hours = self.interval.duration.total_seconds() / 3600 if self.interval is not None else None
        vertices = list(self.vertices)
        if hours is None and len(vertices) > 2:
            raise ValueError(f'simplified: the curve for {self._owner_description()} has no interval; vertex costs '
                             f'integrate over its duration and cannot be recomputed.')
        simplified, applied = simplify_vertices(vertices, tolerance, hours if hours is not None else 1.0, max_vertices)
        return SDCurve(self.measurement, self.interval, simplified, archive=False), applied

    def apply_constraints_and_bounds(self, minimum_power: float = None, maximum_power: float = None):
        """ Applies constraints and bounds to the SDCurve vertices whereby:
                * The passed arguments are hard minimum and maximum power limits.
                * The maximum and minimum vertices in the SDCurve are soft constraints.
        """
        if not self.vertices:
            raise ValueError(f'apply_constraints_and_bounds: the curve for {self._owner_description()} has no vertices.')
        # A curve lying entirely outside the hard limits (every vertex above the maximum, or below the minimum) has no
        # feasible range: the only quantity the limits allow is the bound nearest the curve, and the curve becomes that
        # lone inelastic vertex. (Intersecting the limits with such a curve used to leave an empty set, which broke
        # everything downstream.) This happens, for example, when a node's residual curve towards a supplier shows a
        # surplus the supplier cannot take back.
        curve_min, curve_max = self.min_quantity(), self.max_quantity()
        bound = None
        if maximum_power is not None and curve_min > maximum_power:
            bound = maximum_power
        elif minimum_power is not None and curve_max < minimum_power:
            bound = minimum_power
        if bound is not None:
            bound = float(bound) + 0.0  # -0.0 becomes 0.0
            _log.warning(f'The curve for {self._owner_description()} lies entirely outside its limits '
                         f'[{minimum_power}, {maximum_power}] (quantities {curve_min} to {curve_max}); it is replaced by '
                         f'an inelastic vertex at {bound}.')
            cost = self.prod_cost_from_vertices(bound)
            self.vertices = SortedSet([Vertex(price=float('inf'), cost=cost if cost is not None else 0.0,
                                              quantity=bound, curve=self)])
            return None

        # Find the minimum and maximum powers from the SDCurve.
        #  These are soft constraints that represent a range of flexibility.
        min_power = self.min_quantity(limit=minimum_power)
        max_power = self.max_quantity(limit=maximum_power)

        # If flexibility is being offered (i.e., more than one vertex),
        #  ensure vertices exist at the extrema of the flexibility range.
        if min_power != max_power:
            if not self.has_vertex_at_quantity(min_power):
                self.update(Vertex(price=(self.price_from_vertices(min_power)), cost=0, quantity=min_power),
                            archive=False)
            if not self.has_vertex_at_quantity(max_power):
                self.update(Vertex(price=(self.price_from_vertices(max_power)), cost=0, quantity=max_power),
                            archive=False)
        elif len(self.vertices) == 1:
            self.replace_vertex(self.vertices[0], price=float('inf'))
        else:
            _log.warning(f'Unexpected flexibility logic while bounding SDCurve in {self._owner_description()}. '
                       f'Maximum and minimum power are the same, but in separate vertices.')

        # Trim the list of vertices to remove any that are outside the soft flexibility range.
        # Preserve the SortedSet type to keep downstream set-like operations working.
        self.vertices = SortedSet([x for x in self.vertices if min_power <= x.quantity <= max_power])
        return None

    def _owner_description(self) -> str:
        """Who this curve belongs to and which interval, for messages."""
        owner = getattr(getattr(self.measurement, 'source', None), 'name', None)
        interval = getattr(self.interval, 'name', None)
        return f'{owner or "an unowned curve"} in {interval or "no interval"}'

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
        # Work on copies of the vertices, in price order: the fix-up actions change quantities, and a vertex inside a
        # curve must not change (see replace_vertex). This curve is left as it is; the result is a new curve. (Sentinel
        # vertices at +/-inf used to be added here; they add no check, since they repeat the end quantities, and a lone
        # inelastic vertex at an infinite price collided with the +inf sentinel and vanished from the result.)
        v_temp = [copy(vertex) for vertex in self.vertices]
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

        v_temp = [vertex for vertex in v_temp if not any(vertex is irrational for irrational in irrational_vertices)]
        # Return the rationalised curve and the number of irrational vertices found.
        return SDCurve(self.measurement, self.interval, v_temp, archive=False), ivf

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
                            a2 = 0.5 * a2 * (power - lower.quantity) ** 2  # [$/h]: integral of the linear marginal price
                            a2 = a2 * self.interval.duration.total_seconds() / 3600  # [$]

                        # Finally, calculate the production cost for the time interval by summing the terms
                        return a0 + a1 + a2  # production cost [$]

    def power_from_vertices(self, price: float) -> float:
        if not self.vertices:
            raise ValueError(f'power_from_vertices: the curve for {self._owner_description()} has no vertices.')
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

    @classmethod
    def is_at_end_vertex(cls, quantity: float, end_vertex: Vertex) -> bool:
        """True when quantity is within END_VERTEX_TOLERANCE (relative, at least 1 kW) of the end vertex's quantity."""
        return abs(quantity - end_vertex.quantity) <= cls.END_VERTEX_TOLERANCE * max(1.0, abs(end_vertex.quantity))

    def price_from_vertices(self, quantity: float) -> float:
        """ Given a power [avg.kW], determine the corresponding marginal price [$/kWh] from the SDCurve.
        """
        # TODO: This and power_from_vertices assume a rational curve (power monotonic with price). Is this actually guaranteed?
        if not self.vertices:
            raise ValueError(f'price_from_vertices: the curve for {self._owner_description()} has no vertices.')
        first, last = self.vertices[0], self.vertices[-1]
        if quantity < first.quantity:
            # Below the first vertex: within END_VERTEX_TOLERANCE it is the first vertex; further out the marginal price
            # is indeterminate, the first vertex's price is returned and a warning says so.
            if not self.is_at_end_vertex(quantity, first):
                _log.warning(f'Indeterminate price: Quantity provided ({quantity})'
                             f' is lower than that of first vertex ({first.quantity}).')
            return first.price

        elif quantity >= last.quantity:
            # At or above the last vertex: at it, or within END_VERTEX_TOLERANCE beyond it, the last vertex's price;
            # further out indeterminate, with the same price and a warning.
            if not self.is_at_end_vertex(quantity, last):
                _log.warning(f'Indeterminate price: Quantity provided ({quantity})'
                             f' is greater than that of last vertex ({last.quantity}).')
            return last.price

        # # The marginal power lies among the active vertices.
        for lower, upper in zip(self.vertices, self.vertices[1:]):
            if lower.quantity == quantity == upper.quantity:
                # The segment is horizontal. Marginal price is indefinite. Return the second vertex price and warn.
                _log.warning('Indeterminate price due to horizontal segment of SDCurve. Returning second price.')
                return upper.price
            elif lower.quantity <= quantity < upper.quantity:
                # The segment is not horizontal. Interpolate on the segment.
                slope = (upper.price - lower.price) / (upper.quantity - lower.quantity)  # [$/kWh/kW]
                return lower.price + (quantity - lower.quantity) * slope  # [$/kWh]

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

    def __copy__(self):
        """
        Returns a copy of this SDCurve object.
        This is a redefinition of method copy.copy() that will be evoked when a SDCurve object is to be copied.
        The copy will have the same properties as this SDCurve, but will not have the same Archivable id nor timestamp.
        The new curve will not be archived on creation.

        Parameters: None
        """
        return SDCurve(
            measurement=self.measurement,
            interval=self.interval,
            vertices=[copy(v) for v in self.vertices],
            archive=False
        )

    def update(self, value: Vertex | dict | list[float|int] | list[Vertex] | list[list[float|int]] | list[dict], archive: bool = True):
        # TODO: Should this have a default value like in the constructor?
        # TODO: Add a batch_update method that defers archiving until a batch of updates is complete.
        #  Currently each update() call triggers archive(), which is wasteful when updating many intervals
        #  in a loop (e.g. during scheduling). A batch_update should accept a list of (time_interval, value)
        #  pairs, update all of them, then archive once at the end.
        if isinstance(value, (Vertex, dict)) or all(isinstance(v, (int, float)) for v in value):
            value = [value]
        new_vertices = self._parse_vertices(value)
        result = self.vertices.update(new_vertices)
        if archive and self.measurement is not None:
            self.archive()
        return result

    def replace_vertex(self, vertex: Vertex, **changes) -> Vertex:
        """
        Replace one of this curve's vertices by a copy with the given attributes changed (price, quantity, cost,
        continuity, uncertainty) and return the copy. Vertices are hashed and ordered by (price, quantity), so a vertex
        inside the curve's SortedSet must never be modified in place: the set would keep it under a stale hash, so
        membership tests and removals would miss it and the order could be wrong. Changing a vertex means replacing it.
        A replacement equal in price and quantity to another vertex of the curve merges with it, as update() would.
        """
        unknown = set(changes) - {'price', 'quantity', 'cost', 'continuity', 'uncertainty'}
        if unknown:
            raise TypeError(f'replace_vertex: a vertex has no attribute(s) {sorted(unknown)}')
        self.vertices.remove(vertex)  # KeyError when the vertex is not in this curve: that is a caller's error
        replacement = copy(vertex)
        for name, value in changes.items():
            setattr(replacement, name, validate_bool(value, name) if name == 'continuity' else float(value))
        self.vertices.add(replacement)
        return replacement

    @property
    def data_manager(self):
        return self.measurement.source.tn.data_manager

    @property
    def table_name(self):
        return 'sd_curve'

    def archive(self, records: list[dict] = None):
        """
        Write one version of this curve: the sd_curve row plus one vertex row per vertex, all stamped with the same
        record_time. Timer.now() is the canonical clock everywhere (simulation time when simulating); real time is
        recoverable from the run's anchor and ratio.
        """
        if archiving.in_final_curves():
            self._final_version = True  # a final version even if the deferred write happens after the block closed
        if archiving.defer(self, self._deferral_key(), records):
            return  # written once when the enclosing deferred_archiving block ends, the latest curve for its interval
        if not self.version_due():
            return  # the same curve as the last version archived, or a level that does not archive it (see archiving)
        super(SDCurve, self).archive(self._version_records(records))

    def archive_records(self, records: list[dict] | None = None) -> list[dict]:
        """One version of this curve as archive rows (`records`, the vertex rows and the curve row), marked as archived,
        for a caller that writes them in a batch (see tent.base.archiving); no rows when no version is due."""
        if not self.version_due():
            return list(records) if records else []
        return [*self._version_records(records), self.archive_record()]

    VERSION_IDENTITY = frozenset({'id', 'record_time', 'measurement_id'})  # row columns that name a version, not content

    def fingerprint(self) -> tuple:
        """What a version of this curve says: the vertices as (price, quantity, cost, continuity) tuples, in curve order,
        and the curve's own row apart from what identifies the version rather than its content (the curve's id and
        record time, its measurement's id: a replaced curve and a re-sent signal are new objects standing for the same
        thing, see Measurement.curve_lineage). An order's status is part of it. Two curves with the same fingerprint are
        the same version to the archive and the second is not written."""
        row = tuple(sorted(((k, v) for k, v in self.get_record().items() if k not in self.VERSION_IDENTITY),
                           key=lambda kv: kv[0]))
        return tuple((v.price, v.quantity, v.cost, v.continuity) for v in self.vertices), row

    def version_due(self) -> bool:
        """Whether archiving this curve now writes a version: the node archives curves at this point (its curve_archiving
        level) and the curve differs from the last version archived for what it stands for (its measurement's lineage in
        its interval). Noting the version as archived is part of asking, so ask once per write."""
        try:
            measurement, interval = self.__measurement, self.__interval
            if interval is not None:
                interval = getattr(interval, 'referent', interval)  # the interval itself, not the proxy, as a key
            if measurement is None or interval is None:
                lineage = None
            elif hasattr(measurement, 'curve_lineage'):
                lineage = measurement.curve_lineage(interval)
            else:  # a stand-in measurement (tests): the curve stands for that object's curve in the interval
                lineage = ('measurement', getattr(measurement, 'id', id(measurement)))
        except ReferenceError:
            lineage, interval = None, None
        final, self._final_version = getattr(self, '_final_version', False), False
        return archiving.curve_version_due(interval, lineage, self.fingerprint(), final=final)

    def _version_records(self, records: list[dict] | None) -> list[dict]:
        self.last_record_time = Timer.now()
        records = list(records) if records else []
        records.extend({'table_name': 'vertex', 'data': self._vertex_record(i, vertex)}
                       for i, vertex in enumerate(self.vertices))
        return records

    def _deferral_key(self):
        """What this curve stands for while archiving is deferred: its measurement's curve for its interval, when it
        has both (a curve replaced by another for the same interval is then written once), otherwise itself."""
        try:
            if self.__measurement is not None and self.__interval is not None:
                return ('sd_curve', self.__measurement.id, self.__interval.name)
        except ReferenceError:
            pass
        return None

    def _vertex_record(self, index: int, vertex: Vertex) -> dict:
        return {
            "curve_id": self.id,
            "curve_record_time": self.last_record_time,
            "id": vertex.id,
            "price": vertex.price,
            "quantity": vertex.quantity,
            "cost": vertex.cost,
            "continuity": vertex.continuity,
            "uncertainty": vertex.uncertainty,
            "record": index
        }

    def get_record(self) -> dict:
        record = super(SDCurve, self).get_record()
        if getattr(self, 'last_record_time', None) is None:
            self.last_record_time = Timer.now()
        record.update({
            "record_time": self.last_record_time,
            # NOTE: identity checks, not truthiness: Measurement.__bool__ is False while it holds no values, which would
            # silently drop the link for every curve archived before its measurement is populated.
            "measurement_id": self.measurement.id if self.measurement is not None else None,
            "owner_id": self.measurement.source.id if self.measurement is not None else None,
            "time_interval_id": self.interval.id if self.interval is not None else None,
            "timestamp": self.interval.start_time if self.interval is not None else None
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
                    {"name": "record_time", "type": "DateTime", "kwargs": {"primary_key": True}},
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
                 measurement_unit: MeasurementUnit = MeasurementUnit.kW,
                 archive_now: bool = True):
                # TODO: Adding vertex_unit: MeasurementUnit would allow SDCurves for quantities other than power.
                #  This would require also adding the unit to the TransactiveSignal and making sure it is passed
                #  everywhere it needs to be.
        if not 'Vertex' in measurement_type.name:
            raise ValueError('SDMeasurement must contain vertex-type objects.')
        super(SDMeasurement, self).__init__(source, measurement_type, measurement_unit, archive_now)

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
               value: SDCurve | list[Vertex] | Vertex | list[list[float]] | list[dict],
               archive: bool = True, replace: bool = False):
        if isinstance(value, SDCurve):
            processed_value = list(value.vertices)  # Convert SortedSet to list
        elif isinstance(value, Vertex):
            processed_value = [value]
        else:
            processed_value = value
        super(SDMeasurement, self).update(time_interval, processed_value, archive, replace)
