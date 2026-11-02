"""
Simplification of a piece-wise linear supply/demand curve before it travels in a transactive signal.

A summed curve carries one vertex per distinct price among the participants, so a node with many actors would send and
archive long polylines that a receiver interpolates to the same powers as a much shorter one. simplify_vertices removes
the interior vertices that lie within a tolerance of the segment between the vertices kept on either side (the
Douglas-Peucker algorithm on the (price, quantity) polyline), keeping the first and last vertex always and leaving
vertices at an infinite price untouched.

Tolerance. Price and quantity are scaled by the curve's spans (the difference between its largest and smallest finite
price, and between its largest and smallest quantity), so one relative tolerance serves both axes. The distance of a
vertex to a segment is the larger of its quantity deviation at its own price and its price deviation at its own
quantity, both in scaled units: a vertex within tolerance t can be dropped only if reading the simplified curve at its
price gives a quantity within t * quantity span of it, and reading the price at its quantity gives one within t * price
span. (A perpendicular distance would not bound either reading on a steep segment.) For a vertex whose price or quantity
lies outside the segment's range, which a rational curve never has, the perpendicular distance is used.

Costs. A vertex's cost is the production cost at its quantity: the cost of the first vertex plus the integral of the
marginal price along the curve up to that quantity, over the interval's duration (SDCurve.prod_cost_from_vertices
evaluates the same integral between two vertices as lower.cost + lower.price * dq + 0.5 * dp * dq, times hours).
Removing an interior vertex changes that integral for every vertex after it by the area between the old and the new
polyline, so the costs of the kept vertices are recomputed from the simplified polyline, from the first vertex's cost,
so that prod_cost_from_vertices on the simplified curve is consistent with its own segments. What is preserved: the
first vertex entirely (price, quantity, cost); the price and quantity of every kept vertex, including the last; the
continuity and uncertainty flags of kept vertices; vertices at an infinite price (their recorded cost step over their
original neighbouring segment is kept, since an infinite price has no area). What changes: the cost of every kept
vertex after a removed one, by the area difference, which the tolerance bounds. Nothing is recomputed when no vertex is
removed.

Cap. With max_vertices, the tolerance is doubled (from the configured one, or from 1e-4 when that is 0) until the
simplified curve has at most that many vertices; the first and last vertex and the infinite-price vertices are never
removed, so a cap below their number yields them alone.
"""
from __future__ import annotations

import math
from typing import Sequence

from tent.containers.vertex import Vertex

__all__ = ['simplify_vertices']

SEED_TOLERANCE = 1e-4  # where the doubling starts when a cap is set and the configured tolerance is 0


def simplify_vertices(vertices: Sequence[Vertex], tolerance: float, hours: float,
                      max_vertices: int | None = None) -> tuple[list[Vertex], float]:
    """
    The vertices of `vertices` (in curve order) simplified as the module docstring describes, as new Vertex objects
    with recomputed costs, and the tolerance that was finally applied (the given one unless a cap raised it). `hours`
    is the interval duration the costs integrate over. A tolerance of 0 without a cap returns copies of every vertex.
    """
    if tolerance < 0:
        raise ValueError(f'the simplification tolerance must not be negative; got {tolerance!r}')
    if max_vertices is not None and max_vertices < 1:
        raise ValueError(f'max_vertices must be at least 1; got {max_vertices!r}')
    vertices = list(vertices)
    leading = [v for v in vertices if v.price == -math.inf]
    trailing = [v for v in vertices if v.price == math.inf]
    finite = [v for v in vertices if math.isfinite(v.price)]
    fixed = len(leading) + len(trailing)

    def kept_indices(tol: float) -> list[int]:
        if len(finite) <= 2:
            return list(range(len(finite)))
        return _douglas_peucker(finite, tol)

    applied = tolerance
    kept = kept_indices(applied)
    if max_vertices is not None and len(kept) + fixed > max_vertices:
        applied = applied or SEED_TOLERANCE
        while len(kept) + fixed > max_vertices and len(kept) > 2:
            applied *= 2
            kept = kept_indices(applied)
    if len(kept) == len(finite):
        return [_copy(v, v.cost) for v in vertices], applied
    kept_finite = [finite[i] for i in kept]
    ordered = leading + kept_finite + trailing
    return _with_recomputed_costs(ordered, vertices, hours), applied


def _douglas_peucker(points: Sequence[Vertex], tolerance: float) -> list[int]:
    """Indices (ascending) of the vertices to keep: the ends, and recursively the vertex farthest from the segment
    between two kept ends when it lies farther than the tolerance."""
    prices = [p.price for p in points]
    quantities = [p.quantity for p in points]
    price_span = (max(prices) - min(prices)) or 1.0
    quantity_span = (max(quantities) - min(quantities)) or 1.0
    scaled = [((p.price - min(prices)) / price_span, (p.quantity - min(quantities)) / quantity_span) for p in points]
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue
        farthest, distance = start, -1.0
        for index in range(start + 1, end):
            d = _distance(scaled[index], scaled[start], scaled[end])
            if d > distance:
                farthest, distance = index, d
        if distance > tolerance:
            keep.add(farthest)
            stack.append((start, farthest))
            stack.append((farthest, end))
    return sorted(keep)


def _distance(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """The larger of the point's axis-aligned deviations from segment ab (see the module docstring); the perpendicular
    distance when the point lies outside the segment's range on an axis."""
    (x, y), (ax, ay), (bx, by) = point, a, b
    deviations = []
    if min(ax, bx) <= x <= max(ax, bx):
        deviations.append(0.0 if ax == bx else abs(y - (ay + (x - ax) * (by - ay) / (bx - ax))))
    if min(ay, by) <= y <= max(ay, by):
        deviations.append(0.0 if ay == by else abs(x - (ax + (y - ay) * (bx - ax) / (by - ay))))
    if len(deviations) == 2:
        return max(deviations)
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    if length_squared == 0.0:
        return math.hypot(x - ax, y - ay)
    projection = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length_squared))
    return math.hypot(x - (ax + projection * dx), y - (ay + projection * dy))


def _segment_area(lower: Vertex, upper: Vertex, hours: float) -> float:
    """The cost [$] of moving from lower to upper along their segment: the integral of the linear marginal price, as
    SDCurve.prod_cost_from_vertices integrates it."""
    dq = upper.quantity - lower.quantity
    if dq == 0.0:
        return 0.0
    return (lower.price * dq + 0.5 * (upper.price - lower.price) * dq) * hours


def _with_recomputed_costs(kept: list[Vertex], original: list[Vertex], hours: float) -> list[Vertex]:
    result = [_copy(kept[0], kept[0].cost)]
    for lower, upper in zip(kept, kept[1:]):
        if math.isfinite(lower.price) and math.isfinite(upper.price):
            step = _segment_area(lower, upper, hours)
        else:  # an infinite price has no area: keep the cost step recorded over the vertex's original segment
            position = next(i for i, v in enumerate(original) if v is upper)
            step = upper.cost - original[position - 1].cost if position > 0 else 0.0
        result.append(_copy(upper, result[-1].cost + step))
    return result


def _copy(vertex: Vertex, cost: float) -> Vertex:
    return Vertex(price=vertex.price, cost=cost, quantity=vertex.quantity, continuity=vertex.continuity,
                  uncertainty=vertex.uncertainty)
