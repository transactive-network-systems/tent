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

from tent.utils.helpers import find_obj_by_ti
from tent.interval_value import IntervalValue
from tent.local_asset import LocalAsset
from tent.enumerations.measurement_type import MeasurementType
from tent.information_service_model.temperature_forecast_model import TemperatureForecastModel
from tent.enumerations.market_state import MarketState
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
                 scale_factor: float = 1,
                 *args, **kwargs):
        super(OpenLoopLoadPredictor, self).__init__(*args, **kwargs)
        self.dow_intercept = dow_intercept
        self.season = season
        self.values = values
        self.temperature_forecaster = self.informationServices[0]
        self.scale_factor = scale_factor

    def schedule_power(self, mkt):
        """
        Predict municipal load.
        This is a model of non-price-responsive load using an open-loop regression model.
        :param mkt:
        :return:
        """

        # Get the active time intervals.
        time_intervals = mkt.timeIntervals  # TimeInterval objects

        # Index through the active time intervals.
        # 200928DJH: Go back to basic indexing.
        # for time_interval in time_intervals:
        for time_interval in time_intervals:
            # Extract the start time from the indexed time interval.
            interval_start_time = time_interval.startTime

            if self.temperature_forecaster is None:
                # No appropriate information service was found, must use a default temperature value.
                temp = 56.6  # [deg.F]
            else:
                # An appropriate information service was found. Get the temperature that corresponds to the indexed time
                # interval.
                interval_value = find_obj_by_ti(self.temperature_forecaster.predictedValues, time_interval)

                if interval_value is None:
                    # No stored temperature was found. Assign a default value.
                    temp = 56.6  # [def.F]
                else:
                    # A stored temperature value was found. Use it.
                    temp = interval_value.value  # [def.F]

                if temp is None:
                    # The temperature value is not a number. Use a default value.
                    temp = 56.6  # [def.F]

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

            # Look for the scheduled power in the indexed time interval.
            interval_value = find_obj_by_ti(self.scheduledPowers, time_interval)

            if interval_value is None:
                # No scheduled power was found in the indexed time interval. Create one and store it.
                interval_value = IntervalValue(calling_object=self,
                                               time_interval=time_interval,
                                               market=mkt,
                                               measurement_type=MeasurementType.ScheduledPower,
                                               value=load
                                               )
                self.scheduledPowers.append(interval_value)

            else:
                # The interval value already exist. Simply reassign its value.
                interval_value.value = load
        self.scheduleCalculated = True

        # 200929DJH: Trim the list of scheduled powers for any that lie in expired markets.
        self.scheduledPowers = [x for x in self.scheduledPowers if x.market.marketState != MarketState.Expired]

    @classmethod
    def test_all(cls):
        # TEST_ALL() - test all the class methods
        print('Running OpenLoopRichlandLoadPredictor.test_all()')
        OpenLoopLoadPredictor.test_schedule_power()

    @classmethod
    def test_schedule_power(cls):
        from tent.market import Market
        from tent.time_interval import TimeInterval
        print('Running test_schedule_power()')
        pf = 'pass'

        # Create a test Market object.
        test_mkt = Market()

        # Create and store a couple TimeInterval objects at a known date and
        # time.
        dt = datetime(2017, 11, 1, 12, 0, 0)  # Wednesday Nov. 1, 2017 at noon
        at = dt
        dur = timedelta(hours=1)
        mkt = test_mkt
        mct = dt
        st = dt
        test_intervals = [TimeInterval(at, dur, mkt, mct, st)]

        st = st + dur  # 1p on the same day
        test_intervals.append(TimeInterval(at, dur, mkt, mct, st))

        test_mkt.timeIntervals = test_intervals

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
        test_forecast.predictedValues = [
            IntervalValue(test_forecast, test_intervals[0], test_mkt, MeasurementType.Temperature, 20),
            # Heating regime
            IntervalValue(test_forecast, test_intervals[1], test_mkt, MeasurementType.Temperature, 100)
            # Cooling regime
        ]
        test_obj = OpenLoopLoadPredictor(test_forecast)

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
        if any([abs(test_obj.scheduledPowers[i].value - LOAD[i]) > 5 for i in range(len(test_obj.scheduledPowers))]):
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
        )

        # Create market with some time intervals
        mkt = Market()
        analysis_time = parser.parse("2017-01-01 00:00:00")
        mkt.marketClearingTime = analysis_time
        mkt.nextMarketClearingTime = mkt.marketClearingTime + mkt.marketClearingInterval

        # Control steps using horizon
        mkt.futureHorizon = timedelta(days=365)

        mkt.check_intervals(analysis_time)

        # set time intervals
        forecaster.update_information(mkt)

        # schedule powers
        predictor = OpenLoopLoadPredictor(forecaster)
        predictor.schedule_power(mkt)

        powers = [(x.timeInterval.startTime, x.value) for x in predictor.scheduledPowers]
        total_power = sum([s[1] for s in powers])

        print(powers)
        print(total_power)


if __name__ == '__main__':
    OpenLoopLoadPredictor.predict_2017()
