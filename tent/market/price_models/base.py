import abc

from datetime import datetime


class PriceModel(abc.ABC):
    """
    What a market series needs from a price model: an estimate of the price distribution for any time, learned from
    the prices its markets clear at. A series names its price model class in its `price_model` configuration (see
    tent.registry); the class is constructed with that configuration's other keys plus `default_price`.
    """

    @abc.abstractmethod
    def update(self, date_time: datetime, new_price: float) -> None:
        """Learn from a price cleared for the interval starting at date_time."""

    @abc.abstractmethod
    def get(self, date_time: datetime) -> tuple[float, float]:
        """The modelled (average, standard deviation) of the price for the interval starting at date_time."""
