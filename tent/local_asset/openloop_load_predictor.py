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

from datetime import timedelta
from typing import List


from tent.enumerations import MeasurementType
from tent.information_service_model.temperature_forecast_model import TemperatureForecastModel
from tent.local_asset import LocalAsset
from tent.utils.log import setup_logging

setup_logging()
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

    def __init__(self,
                 dow_intercept: List[float],
                 season: List[int],
                 values: List[List[int]],
                 default_temperature: float = 56.6,
                 scale_factor: float = 1,
                 *args, **kwargs):
        super(OpenLoopLoadPredictor, self).__init__(*args, **kwargs)
        self.default_temperature = default_temperature
        self.dow_intercept = dow_intercept
        self.season = season
        self.values = values
        # TODO: This should probably allow configuration of a named ism instead.
        self.temperature_forecaster = next((s for s in self.information_services
                                            if s.information_type == MeasurementType.Temperature), None)
        self.scale_factor = scale_factor

    def schedule_power(self, mkt):
        """
        Predict municipal load.
        This is a model of non-price-responsive load using an open-loop regression model.
        :param mkt:
        :return:
        """

        # Get the active time intervals.
        time_intervals = mkt.time_intervals  # TimeInterval objects

        # Index through the active time intervals.
        for time_interval in time_intervals:
            # Extract the start time from the indexed time interval.
            interval_start_time = time_interval.start_time

            if self.temperature_forecaster is None:
                # No appropriate information service was found, must use a default temperature value.
                temp = self.default_temperature  # [deg.F]
            else:
                # An appropriate information service was found.
                # Get the temperature that corresponds to the indexed time interval.
                interval_value = self.temperature_forecaster.predictedValues.get_by_interval(time_interval)
                temp = interval_value.value  if interval_value and interval_value.value else self.default_temperature

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


    # TODO: Update tests and move out of this file.
    @classmethod
    def test_all(cls):
        # TEST_ALL() - test all the class methods
        print('Running OpenLoopRichlandLoadPredictor.test_all()')
        OpenLoopLoadPredictor.test_schedule_power()

    @classmethod
    def test_schedule_power(cls):
        from datetime import datetime
        from tent.market import Market
        from tent.containers import TimeInterval
        print('Running test_schedule_power()')
        pf = 'pass'

        # Create a test Market object.
        test_mkt = Market()

        # Create and store a couple TimeInterval objects at a known date and
        # time.
        dt = datetime(2017, 11, 1, 12, 0, 0)  # Wednesday Nov. 1, 2017 at noon
        dur = timedelta(hours=1)
        mkt = test_mkt
        st = dt
        test_intervals = [TimeInterval(mkt, dur,st)]

        st = st + dur  # 1p on the same day
        test_intervals.append(TimeInterval(mkt, dur, st))

        test_mkt.time_intervals = test_intervals

        # Create a test TemperatureForecastModel object and give it some
        # temperature values in the test TimeIntervals.
        # test_forecast = TemperatureForecastModel
        # # The information type should be specified so the test object will
        # # correctly identity it.
        # test_forecast.informationType = 'temperature'
        # # test_forecast.update_information(test_mkt)
        # test_forecast.predictedValues(1) = IntervalValue(test_forecast, test_intervals(1), test_mkt,
        # 'Temperature', 20)  # Heating regime
        # test_forecast.predictedValues(2) = IntervalValue(test_forecast, test_intervals(2), test_mkt,
        # 'Temperature', 100)  # Cooling regime
        # test_obj.informationServiceModels = {test_forecast}
        # Create a OpenLoopRichlandLoadPredictor test object.
        test_forecast = TemperatureForecastModel()
        test_forecast.informationType = 'temperature'
        test_forecast.predicted_values.update(test_intervals[0], 20)  # Heating regime
        test_forecast.predicted_values.update(test_intervals[1], 100) # Cooling regime
        test_obj = OpenLoopLoadPredictor(test_forecast)  # TODO: Wrong signature. Test will need updated to run.

        # Manually evaluate from the lookup table and the above categorical inputs
        # DOW = Wed. ==>
        intercept1 = 146119
        intercept2 = 18836
        intercept3 = -124095
        factor1 = -1375
        factor2 = 1048
        temperature1 = 20
        temperature2 = 100

        LOAD = [
            -(intercept1 + intercept2 + factor1 * temperature1),
            -(intercept1 + intercept3 + factor2 * temperature2)]

        try:
            test_obj.schedule_power(test_mkt)
            print('- the method ran without errors')
        except:
            pf = 'fail'
            # _log.warning('- the method had errors when called')

        # if any(abs([test_obj.scheduledPowers(1: 2).value] - [LOAD])) > 5
        if any([abs(test_obj.scheduled_powers[i].value - LOAD[i]) > 5 for i in range(len(test_obj.scheduled_powers))]):
            pf = 'fail'
            # _log.warning('- the calculated powers were not as expected')
        else:
            print('- the calculated powers were as expected')

        # Success
        print('- the test ran to completion')
        print('Result: #s\n\n', pf)

    @classmethod
    def predict_2017(cls):
        from tent.market import Market
        from dateutil import parser

        # TODO: This path will not exist.
        forecaster = TemperatureForecastModel(
            '/home/hngo/PycharmProjects/volttron-applications/pnnl/TNSAgent/campus_config'
        )  # TODO: Wrong signature. Test will need updated to run.

        # Create market with some time intervals
        mkt = Market()
        analysis_time = parser.parse("2017-01-01 00:00:00")
        mkt.market_clearing_time = analysis_time
        mkt.next_market_clearing_time = mkt.market_clearing_time + mkt.market_clearing_interval

        # Control steps using horizon
        mkt.future_horizon = timedelta(days=365)

        mkt.check_intervals(analysis_time)   # TODO: Wrong signature. Test will need updated to run.

        # set time intervals
        forecaster.update_information(mkt)

        # schedule powers
        predictor = OpenLoopLoadPredictor(forecaster)
        predictor.schedule_power(mkt)

        powers = [(x.time_interval.start_time, x.value) for x in predictor.scheduled_powers]
        total_power = sum([s[1] for s in powers])

        print(powers)
        print(total_power)

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


if __name__ == '__main__':
    OpenLoopLoadPredictor.predict_2017()
