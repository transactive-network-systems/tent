"""Shared fixtures."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
import pytest

from tent import flexibility
from tent.flexibility import adapters, conventions, errors, protocols, schema

# Register compatibility module aliases for tests
for mod_prefix in ('der_interface', 'flexibility_model_interface'):
    sys.modules[mod_prefix] = flexibility
    sys.modules[f'{mod_prefix}.adapters'] = adapters
    sys.modules[f'{mod_prefix}.adapters.factory'] = adapters.factory
    sys.modules[f'{mod_prefix}.adapters.pvlib'] = adapters.pvlib
    sys.modules[f'{mod_prefix}.adapters.ochre'] = adapters.ochre
    sys.modules[f'{mod_prefix}.adapters.gridlabd'] = adapters.gridlabd
    sys.modules[f'{mod_prefix}.adapters.replay'] = adapters.replay
    sys.modules[f'{mod_prefix}.conventions'] = conventions
    sys.modules[f'{mod_prefix}.errors'] = errors
    sys.modules[f'{mod_prefix}.protocols'] = protocols
    sys.modules[f'{mod_prefix}.schema'] = schema

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
