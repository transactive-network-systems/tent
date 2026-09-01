"""Regression tests for energy-coupled storage envelopes.

Guards the defect where per-interval SOC headroom was computed against a
single frozen state of charge, letting a battery advertise many times its
own capacity across a multi-interval horizon.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tent.flexibility.adapters.storage_envelope import soc_coupled_power_bounds
from tent.flexibility.schema import Interval

UTC = timezone.utc


def horizon(count: int, duration_s: int = 3600) -> tuple[Interval, ...]:
    return Interval.sequence(datetime(2026, 8, 5, tzinfo=UTC), duration_s, count)


class TestSocCoupling:
    def test_total_charge_energy_cannot_exceed_headroom(self):
        """The regression: 24h of charging must not exceed the pack's headroom."""
        ivs = horizon(24)
        capacity, min_soc, max_soc, soc = 13.5, 0.10, 0.95, 0.50

        bounds = soc_coupled_power_bounds(
            ivs, soc=soc, capacity_kwh=capacity,
            rated_kw=5.0, min_soc=min_soc, max_soc=max_soc,
        )

        advertised = sum(iv.energy_kwh(q_max) for iv, (_, q_max) in zip(ivs, bounds))
        headroom = (max_soc - soc) * capacity

        assert advertised == pytest.approx(headroom, rel=1e-9)
        # Before the fix this was 120.0 kWh into a 13.5 kWh battery.
        assert advertised < capacity

    def test_total_discharge_energy_cannot_exceed_usable(self):
        ivs = horizon(24)
        capacity, min_soc, soc = 13.5, 0.10, 0.50

        bounds = soc_coupled_power_bounds(
            ivs, soc=soc, capacity_kwh=capacity,
            rated_kw=5.0, min_soc=min_soc, max_soc=0.95,
        )

        advertised = sum(iv.energy_kwh(q_min) for iv, (q_min, _) in zip(ivs, bounds))
        usable = -(soc - min_soc) * capacity

        assert advertised == pytest.approx(usable, rel=1e-9)

    def test_efficiency_applied_in_correct_direction(self):
        """Charging needs headroom/eta of AC energy; discharging yields usable*eta."""
        ivs = horizon(24)
        capacity, soc, min_soc, max_soc, eta = 12.5, 0.5, 0.1, 0.95, 0.95

        bounds = soc_coupled_power_bounds(
            ivs, soc=soc, capacity_kwh=capacity, rated_kw=5.0,
            min_soc=min_soc, max_soc=max_soc, efficiency=eta,
        )

        charge = sum(iv.energy_kwh(q) for iv, (_, q) in zip(ivs, bounds))
        discharge = sum(iv.energy_kwh(q) for iv, (q, _) in zip(ivs, bounds))

        assert charge == pytest.approx((max_soc - soc) * capacity / eta, rel=1e-9)
        assert discharge == pytest.approx(-(soc - min_soc) * capacity * eta, rel=1e-9)

    def test_inverter_rating_still_caps_early_intervals(self):
        """Coupling must not relax the instantaneous power limit."""
        bounds = soc_coupled_power_bounds(
            horizon(4), soc=0.5, capacity_kwh=1000.0,
            rated_kw=5.0, min_soc=0.0, max_soc=1.0,
        )
        for q_min, q_max in bounds:
            assert q_max <= 5.0 + 1e-9
            assert q_min >= -5.0 - 1e-9

    def test_headroom_is_consumed_progressively(self):
        """Later intervals must offer no more than earlier ones while filling."""
        bounds = soc_coupled_power_bounds(
            horizon(6), soc=0.5, capacity_kwh=5.0,
            rated_kw=5.0, min_soc=0.1, max_soc=0.95,
        )
        charges = [q_max for _, q_max in bounds]
        assert charges == sorted(charges, reverse=True)
        assert charges[-1] == pytest.approx(0.0)

    def test_full_battery_offers_no_charging(self):
        bounds = soc_coupled_power_bounds(
            horizon(3), soc=0.95, capacity_kwh=13.5,
            rated_kw=5.0, min_soc=0.1, max_soc=0.95,
        )
        assert all(q_max == pytest.approx(0.0) for _, q_max in bounds)

    def test_empty_battery_offers_no_discharging(self):
        bounds = soc_coupled_power_bounds(
            horizon(3), soc=0.10, capacity_kwh=13.5,
            rated_kw=5.0, min_soc=0.1, max_soc=0.95,
        )
        assert all(q_min == pytest.approx(0.0) for q_min, _ in bounds)

    def test_zero_capacity_is_inflexible(self):
        bounds = soc_coupled_power_bounds(
            horizon(2), soc=0.5, capacity_kwh=0.0,
            rated_kw=5.0, min_soc=0.1, max_soc=0.95,
        )
        assert bounds == [(0.0, 0.0), (0.0, 0.0)]

    def test_sub_hour_intervals_respect_same_energy_budget(self):
        """Interval resolution must not change the total energy budget."""
        capacity, soc, max_soc = 13.5, 0.5, 0.95
        expected = (max_soc - soc) * capacity

        for duration_s, count in ((900, 96), (1800, 48), (3600, 24)):
            ivs = horizon(count, duration_s)
            bounds = soc_coupled_power_bounds(
                ivs, soc=soc, capacity_kwh=capacity,
                rated_kw=5.0, min_soc=0.1, max_soc=max_soc,
            )
            total = sum(iv.energy_kwh(q) for iv, (_, q) in zip(ivs, bounds))
            assert total == pytest.approx(expected, rel=1e-9)

    def test_bounds_are_returned_one_per_interval_in_order(self):
        ivs = horizon(7)
        bounds = soc_coupled_power_bounds(
            ivs, soc=0.5, capacity_kwh=13.5,
            rated_kw=5.0, min_soc=0.1, max_soc=0.95,
        )
        assert len(bounds) == len(ivs)
        for q_min, q_max in bounds:
            assert q_min <= q_max
