import abc
import logging

from datetime import datetime, timezone
from uuid6 import uuid7, UUID

_log = logging.getLogger(__name__)

class Archivable(abc.ABC):
    def __init__(self):
        self.__id: UUID = uuid7()
        self.__real_time_created: datetime = datetime.now(timezone.utc).astimezone()

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
        records.extend([
                {
                    'table_name': self.table_name,
                    'data': self.get_record()
                }
            ])
        self.data_manager.archive_data(records)

    @abc.abstractmethod
    def get_record(self) -> dict:
        return {
            "id": self.id
        }

    @classmethod
    @abc.abstractmethod
    def get_archive_definitions(cls) -> list[dict]:
        return []
