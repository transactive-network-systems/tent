import logging

from collections import deque
from datetime import datetime
from statistics import mean, stdev, StatisticsError
from typing import Dict

from tent.utils.log import setup_logging

setup_logging()
_log = logging.getLogger(__name__)


# TODO: Long run, might be better to have a factory method for PriceModel that builds one based on MarketSeries.
# TODO: There should be a method to output a new PriceModel descended from this (w/ deepcopy of the internal data).
# TODO: There should be a base PriceModel class.
# TODO: Update MarketSeries to simplify the logic around this when all the above are done.
class HourlyAveragePriceModel:
    """
    Tracks ths mean and standard deviation of prices for the market series on an hourly basis.
    Note: In order for this to work, the Market.priceModel table must be initialized from its preceding Market
          object of the same type, i.e., sharing the same market name.
    """
    def __init__(self, initial_model: Dict[int, deque] = None, forgetting_factor: int = 14, default_price=0.035):
        """
        :param: initial_model (optional): Pre-trained model.  If not provided, model will be built over time.
        :param: forgetting_factor: [integer] Number of prior values over which model is averaged.
        """
        if initial_model:
            if (not isinstance(initial_model, dict) or len(initial_model) != 24
                or any([len(initial_model.get(h, [])) != forgetting_factor for h in range(0,24)])):
                _log.warning(f'Initial model is not valid for BasePriceModel with forgetting factor {forgetting_factor}.'
                             ' Starting from empty model.')
                self.current_model = self._init_model(forgetting_factor=forgetting_factor, default_price=default_price)
            else:
                self.current_model = initial_model
        else:
            self.current_model = self._init_model(forgetting_factor=forgetting_factor, default_price=default_price)

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
