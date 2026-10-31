"""Energy-coupled envelope construction for storage resources.

Why this module exists
----------------------
An envelope point is a *per-interval* power band, but storage is constrained by
a *cumulative* energy budget. Computing each interval independently against the
present state of charge produces an envelope that is individually feasible and
jointly impossible::

    12.5 kWh battery, 5 kW inverter, SOC 50%, 24 one-hour intervals
    per-interval headroom -> q_max = 5 kW in every interval
    total advertised      -> 120 kWh of charging into a 12.5 kWh pack

``FlexibilityEnvelope.energy_bounds_kwh`` warns about precisely this and states
that enforcing the coupling is the adapter's job. This module is that
enforcement, factored out so the OCHRE and GridLAB-D storage adapters cannot
drift apart.

What "jointly achievable" means here
------------------------------------
``q_max`` for interval *k* is the most the resource can charge in *k* **given
that it already charged at ``q_max`` in intervals 0..k-1**. ``q_min`` is the
mirror image along the discharge trajectory. Each bound is therefore the
envelope of a distinct trajectory, and the two are individually -- not
simultaneously -- realizable. That is the correct semantics for a band: a
dispatcher may follow any path inside it, and the sum along any single monotone
edge is now physically bounded.

Round-trip efficiency is applied in the direction it physically acts: charging
puts ``P * eta`` into the cell, discharging draws ``P / eta`` out of it.
"""

from __future__ import annotations

from typing import Sequence

from ..schema import Interval

__all__ = ["soc_coupled_power_bounds"]


def soc_coupled_power_bounds(
    intervals: Sequence[Interval],
    soc: float,
    capacity_kwh: float,
    rated_kw: float,
    min_soc: float,
    max_soc: float,
    efficiency: float = 1.0,
) -> list[tuple[float, float]]:
    """Return per-interval ``(q_min_kw, q_max_kw)`` honouring the energy budget.

    Bounds are in the canonical load-positive convention: charging is a
    positive number, discharging negative.

    Args:
        intervals: Horizon, in order. Durations may vary between intervals.
        soc: Current state of charge as a fraction in ``[0, 1]``.
        capacity_kwh: Usable cell capacity.
        rated_kw: Inverter power limit, applied symmetrically.
        min_soc: Lower SOC bound the resource must not cross.
        max_soc: Upper SOC bound the resource must not cross.
        efficiency: One-way conversion efficiency in ``(0, 1]``.

    Returns:
        One ``(q_min_kw, q_max_kw)`` tuple per interval, same order as input.
    """
    capacity_kwh = max(float(capacity_kwh), 0.0)
    rated_kw = max(float(rated_kw), 0.0)
    eta = float(efficiency)
    if not 0.0 < eta <= 1.0:
        eta = 1.0

    # Two independent trajectories: one charging as hard as possible, one
    # discharging as hard as possible. Tracking them separately is what makes
    # each bound achievable rather than merely instantaneous.
    soc_charge = float(soc)
    soc_discharge = float(soc)

    bounds: list[tuple[float, float]] = []
    for interval in intervals:
        dur_h = interval.hours

        if capacity_kwh <= 0.0 or dur_h <= 0.0:
            bounds.append((0.0, 0.0))
            continue

        # Charging: AC energy needed to lift the cell to max_soc is
        # (headroom / eta), since only eta of it lands in the cell.
        headroom_kwh = max(0.0, (max_soc - soc_charge) * capacity_kwh)
        q_max = min(rated_kw, (headroom_kwh / eta) / dur_h)
        q_max = max(q_max, 0.0)

        # Discharging: AC energy deliverable from the usable cell energy is
        # (usable * eta).
        usable_kwh = max(0.0, (soc_discharge - min_soc) * capacity_kwh)
        q_min = -min(rated_kw, (usable_kwh * eta) / dur_h)
        q_min = min(q_min, 0.0)

        bounds.append((q_min, q_max))

        # Advance each trajectory by what it just committed to.
        soc_charge = min(max_soc, soc_charge + (q_max * eta * dur_h) / capacity_kwh)
        soc_discharge = max(
            min_soc, soc_discharge - ((-q_min) / eta * dur_h) / capacity_kwh
        )

    return bounds
