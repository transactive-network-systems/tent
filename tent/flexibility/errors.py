"""Exception types.

Deliberately few. Adapters should let genuinely unexpected errors propagate
rather than wrapping everything, so that a broken simulator connection looks
like a broken simulator connection instead of a generic adapter failure.
"""

from __future__ import annotations

__all__ = [
    "DerInterfaceError",
    "SchemaError",
    "ConventionError",
    "AdapterError",
    "CapabilityError",
    "DispatchRejected",
]


class DerInterfaceError(Exception):
    """Base class for everything this package raises."""


class SchemaError(DerInterfaceError, ValueError):
    """A data object was constructed with inconsistent or invalid fields.

    Subclasses ``ValueError`` so existing ``except ValueError`` handlers around
    dataclass construction keep working.
    """


class ConventionError(DerInterfaceError, ValueError):
    """A unit, sign, or timezone rule was violated."""


class AdapterError(DerInterfaceError):
    """An adapter could not talk to its underlying model or device."""


class CapabilityError(DerInterfaceError, NotImplementedError):
    """A resource was asked to do something it does not support.

    Raised, for example, when something attempts to dispatch a PV forecast
    adapter. Prefer checking capabilities up front over catching this.
    """


class DispatchRejected(DerInterfaceError):
    """A dispatch command was refused outright.

    Note this is for *refusal to attempt* -- a malformed command, an offline
    device, a safety interlock. A command that was attempted but only partially
    achieved is NOT an error: return a
    :class:`~der_interface.schema.DispatchResponse` with ``accepted=True`` and
    the actual values, so the caller can reconcile the difference.
    """
