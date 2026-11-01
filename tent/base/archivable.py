"""TENT's archivable: the library's (an identity, a creation time and a row in one table) that can defer its write."""
from archivable import Archivable as LibraryArchivable

from tent.base import archiving


class Archivable(LibraryArchivable):
    """
    An archivable whose archive() takes part in deferred archiving (tent.base.archiving): inside a deferred_archiving
    block the call registers the object, with any dependent records the caller passed, and the rows are written once
    when the block ends, through archive_records; outside a block it writes at once, as the library does.

    A subclass that stands for something replaceable (a value for an interval) names it in _deferral_key so that the
    latest object for that thing is the one written; one that writes dependent rows of its own (a curve its vertices)
    extends archive_records.
    """

    def archive(self, records: list[dict] = None):
        if archiving.defer(self, self._deferral_key(), records):
            return
        super(Archivable, self).archive(records)

    def archive_records(self, records: list[dict] | None = None) -> list[dict]:
        """The rows one archive() call would write (`records` and this object's own, which marks it archived), for a
        caller that writes them in a batch."""
        records = list(records) if records else []
        records.append(self.archive_record())
        return records

    def _deferral_key(self):
        """What this object stands for while archiving is deferred; None (the default) makes the object its own key."""
        return None
