import abc
import logging

from datetime import datetime, timezone
from uuid6 import uuid7, UUID

_log = logging.getLogger(__name__)

class Archivable(abc.ABC):
    def __init__(self):
        self.__id: UUID = uuid7()
        self.__real_time_created: datetime = datetime.now(timezone.utc).astimezone()
        self.__last_archived_record: dict | None = None

    @property
    def id(self) -> UUID:
        return self.__id

    @property
    def real_time_created(self) -> datetime:
        return self.__real_time_created

    @property
    @abc.abstractmethod
    def data_manager(self):
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def table_name(self):
        raise NotImplementedError

    def archive(self, records: list[dict] = None):
        records = records if records else []

        base_record = {
            'table_name': self.table_name,
            'data': self.get_record()
        }

        # Warn if this exact record (same id, same data) was already archived.
        if self.__last_archived_record is not None and base_record == self.__last_archived_record:
            _log.warning(f'Duplicate archive: {self.table_name} (id={self.id}) archived with identical data.')

        records.append(base_record)
        self.__last_archived_record = base_record

        try:
            self.data_manager.archive_data(records)
        except Exception:
            _log.exception(f'Failed to archive {self.table_name} (id={self.id}).')

    @abc.abstractmethod
    def get_record(self) -> dict:
        return {
            "id": self.id
        }

    @classmethod
    @abc.abstractmethod
    def get_archive_definitions(cls) -> list[dict]:
        return []