"""
Copyright (c) 2020, Battelle Memorial Institute
All rights reserved.
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
The views and conclusions contained in the software and documentation are those
of the authors and should not be interpreted as representing official policies,
either expressed or implied, of the FreeBSD Project.
This material was prepared as an account of work sponsored by an agency of the
United States Government. Neither the United States Government nor the United
States Department of Energy, nor Battelle, nor any of their employees, nor any
jurisdiction or organization that has cooperated in th.e development of these
materials, makes any warranty, express or implied, or assumes any legal
liability or responsibility for the accuracy, completeness, or usefulness or
any information, apparatus, product, software, or process disclosed, or
represents that its use would not infringe privately owned rights.
Reference herein to any specific commercial product, process, or service by
trade name, trademark, manufacturer, or otherwise does not necessarily
constitute or imply its endorsement, recommendation, or favoring by the
United States Government or any agency thereof, or Battelle Memorial Institute.
The views and opinions of authors expressed herein do not necessarily state or
reflect those of the United States Government or any agency thereof.

PACIFIC NORTHWEST NATIONAL LABORATORY
operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
under Contract DE-AC05-76RL01830
"""

import logging



from tent.config import ConfigModel, Setting
from tent.enumerations import MeasurementType
from tent.local_asset import LocalAsset

_log = logging.getLogger(__name__)


class OpenLoopLoadPredictor(LocalAsset):
    """
    Predict electrical load using hour-of-day, season, heating/cooling regime, and
    forecasted Fahrenheit temperature.

    # Predictor formula
    # LOAD = DOW_Intercept(DOW)
    #     + HOUR_SEASON_REGIME_Intercept(HOUR,SEASON,REGIME)
    #     + Factor(HOUR,SEASON,REGIME) * TEMP
    #   DOW_Intercept - average kW - Addend that is a function of categorical
    # day-of-week.
    #   HOUR - Categorical hour of day in the range [1, 24]
    #   HOUR_SEASON_REGIME_Factor - avg.kW / deg.F - Factor of TEMP. A function
    # of categoricals HOUR, SEASON, and REGIME.
    #   HOUR_SEASON_REGIME_Intercept - average kW - Addend that is a function
    # of categoricals HOUR, SEASON, and REGIME.
    #   LOAD - average kW - Predicted hourly Richland, WA electric load
    #   REGIME - Categorical {"Cool", "Heat", or "NA"}. Applies only in seasons
    # Spring and Fall. Not to be used for Summer or Winter seasons.
    #   SEASON - Categorical season
    # "Spring" - [Mar, May]
    # "Summer" - [Jun, Aug]
    # "Fall"   - [Sep, Nov]
    # "Winter" - [Dec, Feb]
    #   TEMP - degrees Fahrenheit - a predicted hourly temperature forecast.
    """

    class Config(LocalAsset.Config):
        dow_intercept: list[float] = Setting(..., min_length=7, max_length=7, reconfigurable=True,
                                             description='[avg.kW] regression intercept per weekday, Monday first.')
        season: list[int] = Setting(..., min_length=12, max_length=12, reconfigurable=True,
                                    description='Season regime index per month, January first (a row group of values).')
        values: list[list[float]] = Setting(
            ..., reconfigurable=True,
            description='Regression rows [intercept avg.kW, temperature factor avg.kW/degF], one per hour of the day '
                        'and season regime.')
        default_temperature: float = Setting(56.6, reconfigurable=True,
                                             description='[degF] temperature used when no forecast is available.')
        scale_factor: float = Setting(1.0, reconfigurable=True,
                                      description='[dimensionless] factor applied to the predicted load.')

    def __init__(self, transactive_node, config: ConfigModel | None = None, **kwargs):
        cfg = self._configure(config, kwargs)
        super(OpenLoopLoadPredictor, self).__init__(transactive_node, config=cfg)
        self.default_temperature = cfg.default_temperature
        self.dow_intercept = list(cfg.dow_intercept)
        self.season = list(cfg.season)
        self.values = [list(row) for row in cfg.values]
        self.scale_factor = cfg.scale_factor
        # TODO: This should probably allow configuration of a named ism instead.
        self.temperature_forecaster = self._find_temperature_forecaster()

    def _find_temperature_forecaster(self):
        return next((s for s in self.information_services if s.information_type == MeasurementType.Temperature), None)

    def _apply_reconfiguration(self, cfg, changed: set[str]) -> None:
        super()._apply_reconfiguration(cfg, changed - {'dow_intercept', 'season', 'values'})
        if 'dow_intercept' in changed:
            self.dow_intercept = list(cfg.dow_intercept)
        if 'season' in changed:
            self.season = list(cfg.season)
        if 'values' in changed:
            self.values = [list(row) for row in cfg.values]
        if 'information_services' in changed:
            self.temperature_forecaster = self._find_temperature_forecaster()

    def schedule_power(self, mkt):
        """
        Predict municipal load.
        This is a model of non-price-responsive load using an open-loop regression model.
        :param mkt:
        :return:
        """

        # Index through the active time intervals.
        for time_interval in mkt.time_intervals:
            # Extract the start time from the indexed time interval.
            interval_start_time = time_interval.start_time

            if self.temperature_forecaster is None:
                # No appropriate information service was found, must use a default temperature value.
                temp = self.default_temperature  # [deg.F]
            else:
                # An appropriate information service was found.
                # Get the temperature that corresponds to the indexed time interval.
                interval_value = self.temperature_forecaster.predicted_values.get_by_interval(time_interval)
                temp = (interval_value.value if interval_value is not None and interval_value.value is not None
                        else self.default_temperature)  # a forecast of 0.0 is a reading, not a gap

            # Determine the dow_intercept.
            # The dow_Intercept is a function of categorical day-of-week number
            # dow_n. Calculate the weekday number dow_n.
            dow_n = interval_start_time.weekday()  # weekday(interval_start_time)

            # Look up the DOW_intercept from the short table that is among the
            # class's constant properties.
            dow_intercept = self.dow_intercept[dow_n]

            # Determine categorical hour of the indexed time interval. This will
            # be needed to mine the hour_season_regime_intercept lookup table.
            # The hour is incremented by 1 because the lookup table uses hours
            # [1,24], not [0,23].
            hour = interval_start_time.hour  # + 1

            # Determine the categorical season of the indexed time interval.
            # season is a function of month, so start by determining the month of
            # the indexed time interval.
            month = interval_start_time.month  # month = month(interval_start_time)

            # Property season provides an index for use with the
            # hour_season_regime_intercept lookup table.
            season = self.season[month - 1]  # obj.season(month);

            # Determine categorical regime, which is also an index for use with
            # the hour_season_regime_intercept lookup table.
            regime = 0  # The default assignment
            if (season == 1 or season == 4) and temp <= 56.6:  # (Spring  OR Fall season) AND Heating regime
                regime = 1

            # Calculate the table row. Add final 1 because of header row.
            row = 6 * hour + season + regime  # 6 * (hour - 1) + season + regime

            # Matlab is 1-based vs. python 0-based.
            row = row - 1

            # Assign the Intercept and Factor values that were found.
            hour_season_regime_intercept = self.values[row][0]
            hour_season_regime_factor = self.values[row][1]

            # Finally, predict the city load.
            load = dow_intercept + hour_season_regime_intercept + hour_season_regime_factor * temp  # [avg.kW]

            # Scale for whole campus
            load *= self.scale_factor

            # The table defined electric load as a positive value. The network
            # model defines load as a negative value.
            load = -load  # [avg.kW]

            # Create or store a scheduled power with this load.
            self.scheduled_powers.update(time_interval, load)

        self.schedule_calculated = True

    @property
    def table_name(self):
        return 'open_loop_load_predictor'

    def get_record(self) -> dict:
        record = super(OpenLoopLoadPredictor, self).get_record()
        record.update({
            'asset_id': self.id,
            'default_temperature': self.default_temperature,
            'dow_intercept': self.dow_intercept,
            'season': self.season,
            'values': self.values,
            'scale_factor': self.scale_factor
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(OpenLoopLoadPredictor, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "open_loop_load_predictor",
                "inherits": "local_asset",
                "columns": [
                    {"name": "asset_id", "foreign_key": "local_asset.id", "kwargs": {"primary_key": True}},
                    {"name": "default_temperature", "type": "Float"},
                    {"name": "dow_intercept", "type": "JSON"},
                    {"name": "season", "type": "JSON"},
                    {"name": "values", "type": "JSON"},
                    {"name": "scale_factor", "type": "Float"}
                ]
            }
        ])
        return definitions
