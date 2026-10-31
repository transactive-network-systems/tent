"""Shared fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from tent.flexibility import (
    EnvelopePoint,
    FlexibilityEnvelope,
    Interval,
    ResourceIdentity,
    ResourceKind,
)

UTC = timezone.utc


@pytest.fixture
def t0() -> datetime:
    return datetime(2026, 8, 5, 14, 0, tzinfo=UTC)


@pytest.fixture
def intervals(t0):
    """Four 15-minute intervals."""
    return Interval.sequence(t0, 900, 4)


@pytest.fixture
def hvac_identity() -> ResourceIdentity:
    return ResourceIdentity(
        resource_id="house_12_hvac",
        kind=ResourceKind.THERMAL_LOAD,
        source="test",
    )


@pytest.fixture
def hvac_envelope(hvac_identity, intervals) -> FlexibilityEnvelope:
    """A flexible load: 0 to 4.2 kW, baseline 2.8 kW."""
    return FlexibilityEnvelope(
        hvac_identity,
        tuple(EnvelopePoint(i, 0.0, 4.2, 2.8) for i in intervals),
    )
