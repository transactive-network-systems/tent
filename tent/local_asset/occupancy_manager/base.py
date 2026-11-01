from abc import ABC, abstractmethod
from datetime import datetime

from tent.config import Configurable


class OccupancyManager(Configurable, ABC):
    """Abstract base class for occupancy managers."""

    @abstractmethod
    def check_schedule(self, dt: datetime | None = None):
        pass
