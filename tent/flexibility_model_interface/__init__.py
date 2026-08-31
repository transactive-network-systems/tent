"""flexibility_model_interface (FMI): a simulator-independent contract for physical & flexible grid assets.

Renamed from der_interface to reflect that flexibility models represent not only DERs,
but distribution feeders, substation transformers, building thermal zones, and passive loads.
"""

from __future__ import annotations

import sys
from der_interface import (
    ENERGY_UNIT,
    POWER_UNIT,
    PRESET_CONFIGS,
    AdapterError,
    Capabilities,
    CapabilityError,
    ConventionError,
    DerInterfaceError,
    DispatchCommand,
    DispatchRejected,
    DispatchResponse,
    DispatchTarget,
    EnvelopePoint,
    FlexibilityEnvelope,
    FlexibilityProvider,
    Forecast,
    ForecastPoint,
    ForecastProvider,
    Interval,
    Limits,
    Quality,
    ResourceAdapter,
    ResourceIdentity,
    ResourceKind,
    ResourceState,
    SchemaError,
    SignConvention,
    StateProvider,
    __version__,
    capabilities_of,
    from_config,
    interval_contains,
    load_adapter,
    require_aware,
    require_positive_duration,
    utc_now,
)
from der_interface import adapters, conventions, errors, protocols, schema

# Modernized flexibility model naming aliases
FlexibilityModelAdapter = ResourceAdapter
FlexibilityModelInterfaceError = DerInterfaceError

__all__ = [
    "__version__",
    "from_config",
    "load_adapter",
    "PRESET_CONFIGS",
    "FlexibilityModelAdapter",
    "FlexibilityModelInterfaceError",
    "adapters",
    "conventions",
    "errors",
    "protocols",
    "schema",
    # conventions
    "SignConvention",
    "Quality",
    "POWER_UNIT",
    "ENERGY_UNIT",
    "require_aware",
    "require_positive_duration",
    "interval_contains",
    "utc_now",
    # schema
    "Interval",
    "ResourceKind",
    "ResourceIdentity",
    "ResourceState",
    "ForecastPoint",
    "Forecast",
    "Limits",
    "EnvelopePoint",
    "FlexibilityEnvelope",
    "DispatchCommand",
    "DispatchResponse",
    # protocols
    "Capabilities",
    "StateProvider",
    "ForecastProvider",
    "FlexibilityProvider",
    "DispatchTarget",
    "ResourceAdapter",
    "capabilities_of",
    # errors
    "DerInterfaceError",
    "SchemaError",
    "ConventionError",
    "CapabilityError",
    "DispatchRejected",
    "AdapterError",
]
