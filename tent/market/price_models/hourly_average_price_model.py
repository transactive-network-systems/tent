import logging

from collections import deque
from datetime import datetime
from statistics import mean, stdev, StatisticsError
from pydantic import model_validator

from tent.config import ConfigModel, Setting
from tent.market.price_models.base import PriceModel

_log = logging.getLogger(__name__)


# TODO: Long run, might be better to have a factory method for PriceModel that builds one based on MarketSeries.
# TODO: There should be a method to output a new PriceModel descended from this (w/ deepcopy of the internal data).
# TODO: Update MarketSeries to simplify the logic around this when all the above are done.
class HourlyAveragePriceModel(PriceModel):
    """
    Tracks ths mean and standard deviation of prices for the market series on an hourly basis.
    Note: In order for this to work, the Market.priceModel table must be initialized from its preceding Market
          object of the same type, i.e., sharing the same market name.
    """
    class Config(PriceModel.Config):
        forgetting_factor: int = Setting(14, ge=1, description='Number of prior prices averaged per hour of day.')
        initial_model: dict[int, list[float]] | None = Setting(
            None, description='A pre-trained model: hour of day (0 to 23) to the forgetting_factor most recent prices '
                              '[$/kWh]; null starts from default_price.')

        @model_validator(mode='after')
        def _initial_model_is_complete(self):
            if self.initial_model is not None:
                hours = sorted(self.initial_model)
                if hours != list(range(24)):
                    raise ValueError(f'initial_model must have the hours 0 to 23 as keys; got {hours}')
                short = [hour for hour, prices in self.initial_model.items() if len(prices) != self.forgetting_factor]
                if short:
                    raise ValueError(f'initial_model must hold forgetting_factor ({self.forgetting_factor}) prices per '
                                     f'hour; hours {short} do not')
            return self

    def __init__(self, config: ConfigModel | None = None, **kwargs):
        """
        :param: initial_model (optional): Pre-trained model.  If not provided, model will be built over time.
        :param: forgetting_factor: [integer] Number of prior values over which model is averaged.
        """
        super(HourlyAveragePriceModel, self).__init__(config, **kwargs)
        cfg = self.config
        if cfg.initial_model is not None:
            self.current_model = {hour: deque(prices, maxlen=cfg.forgetting_factor)
                                  for hour, prices in cfg.initial_model.items()}
        else:
            self.current_model = self._init_model(forgetting_factor=cfg.forgetting_factor,
                                                  default_price=cfg.default_price)

    @staticmethod
    def _init_model(forgetting_factor: int, default_price):
        """
        :param: forgetting_factor: [integer] Number of prior values over which model is averaged.
        """
        new_model = {}
        for h in range(0, 24):
            new_model[h] = deque(maxlen=forgetting_factor)
            new_model[h].extend([default_price, default_price])
        return new_model

    def update(self, date_time: datetime, new_price: float):
        """
        Given a new price, the price model is updated using this price for the hour of the given datetime.
            :param: date_time: The datetime of the update.
            :param: new_price: [$/kWh] New price provided to update the model for the hour of the given datetime.
        TODO: 240207DJH: Unlike the original model, this dictionary of hourly prices appears to grow indefinitely. This
              is not sustainable and should be fixed.
        """
        hourly_model = self.current_model[int(date_time.hour)]
        hourly_model.append(new_price)

    def get(self, date_time: datetime):
        """
        Returns the average and standard deviation prices for the hour of the provided datetime in this market.
        :param: date_time: The datetime for which to retrieve modeled values.

        :return: avg_price: [$/kWh] average model price for the hour of the given datetime
        :return: sd_price: [$/kWh] standard price deviation for the hour of the given datetime
        240207DJH: I observe that the price model has been greatly simplified, but the new price model lost most the
                   elegance and sophistication of the original. (Using a dictionary is fine, however.)
                   TODO: This change should be reverted to the original approach. Assign to Don.
        """
        hourly_model = self.current_model[int(date_time.hour)]
        try:
            return mean(hourly_model), max(stdev(hourly_model), 0.001)  # Require some minimum stdev to avoid zero flex.
        except StatisticsError as e:
            _log.warning(f'A price could not be found from the price model: {e}')
            return None, None
