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

Curve archiving is also decided here, in one place, so that a deployment's archive volume is a setting rather than a
property of each container. Curves (sd_curve and vertex rows: an entity's active vertices, a market's system curve, the
curves of a sent or received transactive signal) are by far the largest part of an archive, and a negotiation rebuilds
and re-sends them on every tick whether or not they changed. Two rules apply, through curve_version_due:

- A curve is archived only when it differs from the last version archived for the same thing (the same measurement and
  interval; for a signal, the same neighbor, market, direction and interval): the same vertices, each with the same
  price, quantity, cost and continuity, are not written again. A signal re-sent unchanged still writes its
  transactive_signal row (sequence, timestamps) but no curves: a reader takes, per interval, the latest curve archived
  for that neighbor, market and direction at or before the signal's time, as it takes the latest version of any value.
- The node's curve_archiving setting (set_curve_archiving) says which versions are written at all: 'all' (every
  changed version), 'final' (only the versions a market archives when it leaves Negotiation and when it clears, within
  a final_curves block: the last system curve and the last signal exchanged with each neighbor per interval), or 'none'
  (no curve rows; the measurement and signal headers are still written).
"""
import logging
from contextlib import contextmanager
from typing import Iterator, Literal
from weakref import WeakKeyDictionary

_log = logging.getLogger(__name__)

_pending: list[dict[object, tuple[object, list | None]]] = []

CurveArchiving = Literal['all', 'final', 'none']
CURVE_ARCHIVING_LEVELS: tuple[str, ...] = ('all', 'final', 'none')
_curve_archiving: str = 'all'
_final_depth: int = 0
# The fingerprint of the last curve version archived, per interval (weakly held: a market's intervals die with it) and
# per lineage key (what the curve stands for, see Measurement.curve_lineage).
_last_curves: WeakKeyDictionary = WeakKeyDictionary()


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


def set_curve_archiving(level: str) -> None:
    """Which curve versions are written: 'all', 'final' or 'none' (the node's curve_archiving setting; see the module
    docstring)."""
    if level not in CURVE_ARCHIVING_LEVELS:
        raise ValueError(f'curve_archiving must be one of {CURVE_ARCHIVING_LEVELS}, not {level!r}.')
    global _curve_archiving
    _curve_archiving = level


def curve_archiving() -> str:
    return _curve_archiving


@contextmanager
def final_curves() -> Iterator[None]:
    """Within this block curves are archived at the 'final' level too: a market opens it when it leaves Negotiation and
    when it clears, to write the last version of its system curve and of the signals exchanged (see Market.archive_final_curves)."""
    global _final_depth
    _final_depth += 1
    try:
        yield
    finally:
        _final_depth -= 1


def in_final_curves() -> bool:
    """Whether a final_curves block is open (a curve registered for deferred archiving inside one is written as final
    when the block that defers it ends, which may be after the final_curves block closed)."""
    return _final_depth > 0


def curve_version_due(interval, lineage, fingerprint, final: bool = False) -> bool:
    """
    Whether a curve version with this `fingerprint` (its vertices as (price, quantity, cost, continuity) tuples) is to be
    archived for `lineage` (what the curve stands for, see Measurement.curve_lineage) in `interval`, and, when it is, note
    it as the last version archived. False when the node archives no curves, when it archives final curves only and this
    is not one (`final`: archived from within a final_curves block), or when the fingerprint is that of the last version
    archived for the same lineage and interval. A curve without an interval has no lineage to compare with and is due
    whenever the level allows.
    """
    if _curve_archiving == 'none' or (_curve_archiving == 'final' and not (final or _final_depth)):
        return False
    if interval is None or lineage is None:
        return True
    try:
        last = _last_curves.setdefault(interval, {})
    except TypeError:  # an interval that cannot be weakly referenced or hashed: nothing to compare with
        return True
    if last.get(lineage) == fingerprint:
        return False
    last[lineage] = fingerprint
    return True
