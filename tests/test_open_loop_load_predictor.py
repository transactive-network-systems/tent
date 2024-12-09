from datetime import timedelta

from tent.information_service_model.temperature_forecast_model import TemperatureForecastModel
from tent.local_asset.openloop_load_predictor import OpenLoopLoadPredictor

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
    test_intervals = [TimeInterval(mkt, dur, st)]

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
    test_forecast.predicted_values.update(test_intervals[1], 100)  # Cooling regime
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
    # TODO: What was this doing? It had the wrong signature, and now this function is removed. Test won't work....
    mkt.check_intervals(analysis_time)

    # set time intervals
    forecaster.update_information(mkt)

    # schedule powers
    predictor = OpenLoopLoadPredictor(forecaster)
    predictor.schedule_power(mkt)

    powers = [(x.time_interval.start_time, x.value) for x in predictor.scheduled_powers]
    total_power = sum([s[1] for s in powers])

    print(powers)
    print(total_power)
