"""Regression tests for sign-convention handling at the dispatch boundary.

conventions.py exists because TENT is generation-positive while every physical
simulator is load-positive, and a missed conversion inverts power silently --
nothing raises, and a market clears backwards while looking plausible.

The OCHRE adapters read ``command.power_kw`` raw, ignoring ``command.sign``,
while FlexibilityModelFrameAdapter sends GENERATION_POSITIVE commands. Every
OCHRE dispatch therefore ran backwards. These tests pin the contract for all
adapters that accept a power target.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tent.flexibility.conventions import SignConvention
from tent.flexibility.schema import (
    DispatchCommand,
    Interval,
    ResourceIdentity,
    ResourceKind,
)

UTC = timezone.utc


@pytest.fixture
def interval() -> Interval:
    return Interval(datetime(2026, 8, 5, 14, 0, tzinfo=UTC), 3600)


class TestSignConventionRoundTrip:
    """The conversion helpers themselves."""

    def test_generation_positive_consumption_becomes_load_positive(self):
        # TENT expresses "consume 5 kW" as -5.0 (generation-positive).
        assert SignConvention.GENERATION_POSITIVE.to_canonical(-5.0) == 5.0

    def test_generation_positive_generation_becomes_load_positive(self):
        # TENT expresses "generate 3 kW" as +3.0.
        assert SignConvention.GENERATION_POSITIVE.to_canonical(3.0) == -3.0

    def test_load_positive_is_identity(self):
        assert SignConvention.LOAD_POSITIVE.to_canonical(5.0) == 5.0

    def test_to_and_from_canonical_are_involutive(self):
        for sign in SignConvention:
            for value in (-7.5, -0.1, 0.0, 0.1, 7.5):
                assert sign.from_canonical(sign.to_canonical(value)) == pytest.approx(value)


class TestDispatchCommandSignIsHonoured:
    """A commanded battery charge must not become a discharge."""

    def _identity(self) -> ResourceIdentity:
        return ResourceIdentity("batt", ResourceKind.STORAGE, source="test")

    def test_tent_charge_command_is_positive_in_canonical(self, interval):
        """The exact bug: TENT sends -5.0 meaning charge; canonical must be +5.0."""
        cmd = DispatchCommand.for_power(
            self._identity(), interval, -5.0,
            sign=SignConvention.GENERATION_POSITIVE,
        )
        canonical = cmd.sign.to_canonical(cmd.power_kw)

        assert canonical == 5.0, "charge command must be load-positive +5 kW"
        assert canonical > 0, "reading power_kw raw would discharge instead"

    def test_tent_discharge_command_is_negative_in_canonical(self, interval):
        cmd = DispatchCommand.for_power(
            self._identity(), interval, 4.0,
            sign=SignConvention.GENERATION_POSITIVE,
        )
        assert cmd.sign.to_canonical(cmd.power_kw) == -4.0

    def test_load_positive_command_passes_through_unchanged(self, interval):
        cmd = DispatchCommand.for_power(
            self._identity(), interval, 5.0,
            sign=SignConvention.LOAD_POSITIVE,
        )
        assert cmd.sign.to_canonical(cmd.power_kw) == 5.0

    def test_raw_power_kw_differs_from_canonical_for_tent_commands(self, interval):
        """Guard against a future refactor reintroducing the raw read."""
        cmd = DispatchCommand.for_power(
            self._identity(), interval, -5.0,
            sign=SignConvention.GENERATION_POSITIVE,
        )
        assert cmd.power_kw != cmd.sign.to_canonical(cmd.power_kw)


class TestOchreAdaptersConvertSign:
    """Source-level guard: every OCHRE dispatch must call to_canonical."""

    @pytest.mark.parametrize(
        "module",
        ["battery", "dwelling", "ev", "hvac", "water_heater"],
    )
    def test_dispatch_normalizes_command_sign(self, module):
        import importlib
        import inspect

        mod = importlib.import_module(f"tent.flexibility.adapters.ochre.{module}")
        adapter = next(
            obj for name, obj in inspect.getmembers(mod, inspect.isclass)
            if name.startswith("Ochre") and hasattr(obj, "dispatch")
        )
        source = inspect.getsource(adapter.dispatch)

        assert "to_canonical" in source, (
            f"{adapter.__name__}.dispatch must normalize command.sign into the "
            "canonical load-positive convention before actuating"
        )

    @pytest.mark.parametrize(
        "module",
        ["battery", "dwelling", "ev", "hvac", "water_heater"],
    )
    def test_dispatch_reports_actual_in_caller_convention(self, module):
        import importlib
        import inspect

        mod = importlib.import_module(f"tent.flexibility.adapters.ochre.{module}")
        adapter = next(
            obj for name, obj in inspect.getmembers(mod, inspect.isclass)
            if name.startswith("Ochre") and hasattr(obj, "dispatch")
        )
        source = inspect.getsource(adapter.dispatch)

        assert "from_canonical" in source, (
            f"{adapter.__name__}.dispatch must report actual power back in the "
            "convention the caller used"
        )
