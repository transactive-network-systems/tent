"""
Deferred archiving: write each archivable once at the end of a unit of work instead of once per update.

A market tick touches hundreds of containers: a local balancing pass reschedules every entity several times
(ConsensusMarket.balance_locally iterates until the duality gap closes), an auction's clearing step updates every
entity's curve and power in every interval, and a spawned market creates a measurement for each quantity it tracks; each
scheduled power, cost, curve, measurement header and signal archives itself on the spot, in its own transaction, so one
pass on a node with a 21-model asset and 24 intervals wrote some five hundred rows per iteration, and one clearing step of
the campus auction over a thousand. Inside `with deferred_archiving():` an archivable's archive() registers the object
instead of writing; on leaving the block every registered object is archived once, in registration order, with the value
it holds then, in one archive_data call per data manager. Nothing is lost: the versions in between were never anything a
reader could use, since the unit of work had not finished. Market.events opens a block around each tick of a market, so
whatever a tick touches is written when the tick ends; TENT reads nothing back from the archive during a tick.

Deferral is process-wide (a module-level stack), as the archive itself is; nested blocks hand their registrations to the
enclosing one so that the write happens when the outermost block ends.
"""
import logging
from contextlib import contextmanager
from typing import Iterator

_log = logging.getLogger(__name__)

_pending: list[dict[object, tuple[object, list | None]]] = []


def defer(archivable, key=None, records: list[dict] | None = None) -> bool:
    """Register `archivable` to be archived when the current deferral block ends and return True; False when no block
    is active, in which case the caller archives now. `key` identifies what the object stands for (a measurement's value
    for an interval, say) so that an object replaced by another for the same thing is written once, as the latest one; by
    default the object itself is the key. `records` are the dependent rows the caller would have written with its own
    (see Archivable.archive); the latest registration's records are the ones written."""
    if not _pending:
        return False
    _pending[-1][id(archivable) if key is None else key] = (archivable, records)
    return True


def defer_records(data_manager, records: list[dict]) -> bool:
    """Register rows that belong to no archivable (a market's state change) to be written with the block's batch for
    `data_manager`, and return True; False when no block is active, in which case the caller writes them now."""
    if not _pending:
        return False
    holder = _Records(data_manager, records)
    _pending[-1][id(holder)] = (holder, None)
    return True


class _Records:
    """Rows registered by defer_records: written as they are, in registration order."""

    def __init__(self, data_manager, records):
        self.data_manager = data_manager
        self.records = list(records)

    def archive_records(self, records=None):
        return [*(records or []), *self.records]


def discard(archivable) -> None:
    """Forget a pending registration of `archivable` under its default key: a caller that writes the object's row
    within its own records (a component its measurements' headers) says so here, so the row is not written twice."""
    for block in _pending:
        block.pop(id(archivable), None)


@contextmanager
def deferred_archiving() -> Iterator[None]:
    """Archive the objects updated in this block once, when it ends (see the module docstring)."""
    _pending.append({})
    try:
        yield
    finally:
        block = _pending.pop()
        if _pending:
            _pending[-1].update(block)
        else:
            _flush(block.values())


def _flush(registrations) -> None:
    """Write the registered objects, one archive_data call per data manager (one transaction instead of one per row).
    An object that cannot say its records without writing them (no archive_records) archives itself."""
    batches: dict[int, tuple[object, list]] = {}
    for archivable, records in registrations:
        archive_records = getattr(archivable, 'archive_records', None)
        if archive_records is None:
            archivable.archive(records)
            continue
        try:
            rows = archive_records(records)
            manager = archivable.data_manager
        except Exception:
            _log.exception(f'Failed to prepare the deferred archive records of {type(archivable).__name__}.')
            continue
        batches.setdefault(id(manager), (manager, []))[1].extend(rows)
    for manager, rows in batches.values():
        try:
            manager.archive_data(rows)
        except Exception:
            _log.exception(f'Failed to archive a batch of {len(rows)} deferred records.')


def is_deferring() -> bool:
    return bool(_pending)
