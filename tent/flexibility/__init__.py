"""tent.flexibility: a simulator-independent contract for distributed energy resources.

The whole package answers one question, in a form that OCHRE, GridLAB-D, pvlib,
and real hardware can all answer and that market frameworks like TENT and TESP
can both consume:

    "What can this resource do between 2:00 and 2:15 PM?"

The answer is three numbers per interval -- see
:class:`~der_interface.schema.EnvelopePoint`.

This package contains **no physics, no market logic, and no orchestration**. It
is a translator. Physics stays in OCHRE, GridLAB-D, and pvlib. Markets stay in
TENT. Co-simulation stays in TESP.

Quick start
-----------
>>> from datetime import datetime, timezone
>>> from der_interface import Interval, ResourceIdentity, ResourceKind
>>> from der_interface import EnvelopePoint, FlexibilityEnvelope
>>>
>>> identity = ResourceIdentity("house_12_hvac", ResourceKind.THERMAL_LOAD)
>>> intervals = Interval.sequence(
...     datetime(2026, 8, 5, 14, tzinfo=timezone.utc), 900, 2
... )
>>> envelope = FlexibilityEnvelope(
...     identity,
...     tuple(EnvelopePoint(i, 0.0, 4.2, 2.8) for i in intervals),
... )
>>> envelope[0].up_flex_kw
1.4000000000000004
>>> envelope[0].down_flex_kw
2.8
"""

from __future__ import annotations

from .conventions import (
    ENERGY_UNIT,
    POWER_UNIT,
    Quality,
    SignConvention,
    interval_contains,
    require_aware,
    require_positive_duration,
    utc_now,
)
from .errors import (
    AdapterError,
    CapabilityError,
    ConventionError,
    DerInterfaceError,
    DispatchRejected,
    SchemaError,
)
from .protocols import (
    Capabilities,
    DispatchTarget,
    FlexibilityProvider,
    ForecastProvider,
    ResourceAdapter,
    StateProvider,
    capabilities_of,
)
from .schema import (
    DispatchCommand,
    DispatchResponse,
    EnvelopePoint,
    FlexibilityEnvelope,
    Forecast,
    ForecastPoint,
    Interval,
    Limits,
    ResourceIdentity,
    ResourceKind,
    ResourceState,
)

from .adapters.factory import PRESET_CONFIGS, from_config, load_adapter

FlexibilityModelAdapter = ResourceAdapter
FlexibilityModelInterfaceError = DerInterfaceError

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "from_config",
    "load_adapter",
    "PRESET_CONFIGS",
    "FlexibilityModelAdapter",
    "FlexibilityModelInterfaceError",
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
    "AdapterError",
    "CapabilityError",
    "DispatchRejected",
]
