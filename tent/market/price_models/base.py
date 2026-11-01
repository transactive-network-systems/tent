import abc

from datetime import datetime

from tent.config import ConfigModel, Configurable, Setting


class PriceModel(Configurable, abc.ABC):
    """
    What a market series needs from a price model: an estimate of the price distribution for any time, learned from
    the prices its markets clear at. A series names its price model class in its `price_model` configuration (see
    tent.registry); the class is constructed with that configuration's other keys plus `default_price`, which its Config
    validates.
    """
    class Config(ConfigModel):
        default_price: float = Setting(0.035, ge=0, description='[$/kWh] price assumed before any market has cleared '
                                                                '(the series supplies its own default_price).')

    def __init__(self, config: ConfigModel | None = None, **kwargs):
        self._configure(config, kwargs)


    @abc.abstractmethod
    def update(self, date_time: datetime, new_price: float) -> None:
        """Learn from a price cleared for the interval starting at date_time."""

    @abc.abstractmethod
    def get(self, date_time: datetime) -> tuple[float, float]:
        """The modelled (average, standard deviation) of the price for the interval starting at date_time."""
