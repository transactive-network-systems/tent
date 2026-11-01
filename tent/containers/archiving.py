"""Deferred archiving lives in tent.base.archiving (every archivable defers through it, not only the containers); this
name remains for the callers that learnt it there."""
from tent.base.archiving import defer, defer_records, deferred_archiving, discard, is_deferring  # noqa: F401

__all__ = ['defer', 'defer_records', 'deferred_archiving', 'discard', 'is_deferring']
