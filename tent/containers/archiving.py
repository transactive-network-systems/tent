"""
Deferred archiving: write each container once at the end of a unit of work instead of once per update.

A local balancing pass reschedules every entity several times (ConsensusMarket.balance_locally iterates until the
duality gap closes), and every scheduled power, cost and curve archives itself on each update, so one pass on a node
with a 21-model asset and 24 intervals wrote some five hundred rows per iteration, each in its own transaction. Inside
`with deferred_archiving():` a container's archive() registers the container instead of writing; on leaving the block
every registered container is archived once, in registration order, with the value it holds then. Nothing is lost: the
versions in between were never anything a reader could use, since the pass had not finished.

Deferral is process-wide (a module-level stack), as the archive itself is; nested blocks hand their containers to the
enclosing one so that the write happens when the outermost block ends.
"""
import logging
from contextlib import contextmanager
from typing import Iterator

_log = logging.getLogger(__name__)

_pending: list[dict[object, object]] = []


def defer(archivable, key=None) -> bool:
    """Register `archivable` to be archived when the current deferral block ends and return True; False when no block
    is active, in which case the caller archives now. `key` identifies what the container stands for (a measurement's
    value for an interval, say) so that a container replaced by another for the same thing is written once, as the
    latest one; by default the object itself is the key."""
    if not _pending:
        return False
    _pending[-1][id(archivable) if key is None else key] = archivable
    return True


@contextmanager
def deferred_archiving() -> Iterator[None]:
    """Archive the containers updated in this block once, when it ends (see the module docstring)."""
    _pending.append({})
    try:
        yield
    finally:
        block = _pending.pop()
        if _pending:
            _pending[-1].update(block)
        else:
            _flush(block.values())


def _flush(archivables) -> None:
    """Write the containers, one archive_data call per data manager (one transaction instead of one per row). A
    container that cannot say its records without writing them (no archive_records) archives itself."""
    batches: dict[int, tuple[object, list]] = {}
    for archivable in archivables:
        records = getattr(archivable, 'archive_records', None)
        if records is None:
            archivable.archive()
            continue
        manager = archivable.data_manager
        batches.setdefault(id(manager), (manager, []))[1].extend(records())
    for manager, records in batches.values():
        try:
            manager.archive_data(records)
        except Exception:
            _log.exception(f'Failed to archive a batch of {len(records)} deferred records.')


def is_deferring() -> bool:
    return bool(_pending)
