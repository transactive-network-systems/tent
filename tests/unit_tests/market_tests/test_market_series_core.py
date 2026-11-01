import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from tent.market.market_series import MarketSeries
from tent.enumerations import MarketState
from tent.market import Market
from tests.mock_tn import MockTN
from tent.utils.timer import Timer


def test_market_series_spawns_first_market():
    tn = Mock(); tn.data_manager = Mock(); tn.id = 'x'; tn.get_market_series_by_attribute = Mock(return_value=None)
    series = MarketSeries(transactive_node=tn, market_class=Market, name='Core')
    assert series.markets and series.newest_market is not None


def test_market_series_spawn_additional_market():
    tn = Mock(); tn.data_manager = Mock(); tn.id = 'x'; tn.get_market_series_by_attribute = Mock(return_value=None)
    series = MarketSeries(transactive_node=tn, market_class=Market, name='Core')
    first = series.newest_market
    series.spawn()
    assert series.newest_market != first


def test_market_series_record_keys():
    tn = Mock(); tn.data_manager = Mock(); tn.id = 'x'; tn.get_market_series_by_attribute = Mock(return_value=None)
    series = MarketSeries(transactive_node=tn, market_class=Market, name='Core')
    rec = series.get_record()
    assert {'name','market_class','default_price'}.issubset(rec.keys())


def test_refined_series_resolves_by_name_and_refined_market_lookup(): # audit T-10
    tn = MockTN()
    real_time = MarketSeries(transactive_node=tn, name='Real-Time', market_class=Market, market_clearing_interval=3600.0,
                             interval_duration=3600.0, duration_delivery_lead=0.0, duration_market_lead=600.0)
    tn.market_series = [real_time]
    real_time.spawn()
    first, second = list(real_time.markets)
    assert first.transition_times.delivery_lead < second.transition_times.delivery_lead
    day_ahead = MarketSeries(transactive_node=tn, name='Day-Ahead', market_class=Market, refined_series='Real-Time',
                             market_clearing_interval=86400.0, intervals_to_clear=24, interval_duration=3600.0)
    tn.market_series.append(day_ahead)
    assert day_ahead.refined_series.name == 'Real-Time', 'a name resolves to the series itself, not a list'
    before = first.transition_times.delivery_lead - timedelta(seconds=1)
    assert day_ahead.get_refined_market(before) is None, 'nothing has cleared yet'
    assert day_ahead.get_refined_market(first.transition_times.delivery_lead) is first
    between = first.transition_times.delivery_lead + (second.transition_times.delivery_lead - first.transition_times.delivery_lead) / 2
    assert day_ahead.get_refined_market(between) is first
    assert day_ahead.get_refined_market(second.transition_times.delivery_lead + timedelta(hours=5)) is second
    assert real_time.get_refined_market(first.transition_times.delivery_lead) is None, 'no refined series configured'


def _node():
    Timer.simulation = False
    tn = Mock(); tn.data_manager = Mock(); tn.id = 'x'; tn.get_market_series_by_attribute = Mock(return_value=None)
    return tn


@pytest.mark.parametrize('settings, message', [
    ({'market_clearing_interval': 0}, 'market_clearing_interval must be positive'),
    ({'interval_duration': 0}, 'interval_duration must be positive'),
    ({'intervals_to_clear': 0}, 'intervals_to_clear must be at least 1'),
    ({'duration_negotiation': -5}, 'duration_negotiation must not be negative'),
])
def test_series_timings_are_validated_by_field(settings, message):  # audit T-INC-07
    with pytest.raises(ValueError, match=f'MarketSeries Core: {message}'):
        MarketSeries(transactive_node=_node(), market_class=Market, name='Core', **settings)


def test_price_model_spec_keywords_reach_the_model_and_the_spec_is_not_consumed():  # audit T-INC-08
    spec = {'class_name': 'HourlyAveragePriceModel', 'module_name': 'tent.market.price_models', 'forgetting_factor': 3}
    given = dict(spec)
    series = MarketSeries(transactive_node=_node(), market_class=Market, name='Core', default_price=0.07, price_model=spec)
    model = series.newest_market.price_model
    assert model.current_model[0].maxlen == 3, 'the forgetting factor from the spec'
    assert list(model.current_model[0]) == [0.07, 0.07], 'the series default price unless the spec gives one'
    assert spec == given, 'the configured dict is left as it was'
    assert series.price_model_spec['class_name'] == 'HourlyAveragePriceModel'
    series.spawn()
    assert series.newest_market.price_model.current_model[0].maxlen == 3
    assert spec == given


def test_price_model_spec_with_an_unknown_keyword_fails_at_construction():  # audit T-INC-08
    with pytest.raises(TypeError, match="price_model HourlyAveragePriceModel rejected its keywords.*'forgetting_factr'"):
        MarketSeries(transactive_node=_node(), market_class=Market, name='Core',
                     price_model={'class_name': 'HourlyAveragePriceModel', 'module_name': 'tent.market.price_models',
                                  'forgetting_factr': 3})


def test_markets_expiring_in_one_tick_are_pruned_after_the_iteration():  # audit T-19
    tn = MockTN()
    series = MarketSeries(transactive_node=tn, name='Core', market_class=Market, market_clearing_interval=60.0,
                          interval_duration=60.0, duration_market_lead=10.0)
    tn.market_series = [series]
    series.spawn(); series.spawn()
    first, second, newest = list(series.markets)
    for market in (first, second):
        market._market_state = MarketState.Expired
        market._stateIsCompleted = False
    tn.step()  # events() for every market, then prune_expired(); iterating the series must not be disturbed
    assert list(series.markets) == [newest], 'both expired markets removed, the newest kept for the series chain'
    assert len(first.time_intervals) == 0 and len(second.time_intervals) == 0, 'their intervals were destroyed'
    assert first._stateIsCompleted and second._stateIsCompleted
    newest._market_state = MarketState.Expired
    newest._stateIsCompleted = False
    tn.step()
    assert list(series.markets) == [newest], 'the newest market is never pruned, even when expired'


def _two_series(refining_first: bool) -> dict:
    day_ahead = {'class_name': 'MarketSeries', 'name': 'Day-Ahead', 'market_clearing_interval': 86400,
                 'intervals_to_clear': 24, 'interval_duration': 3600, 'duration_market_lead': 600}
    real_time = {'class_name': 'MarketSeries', 'name': 'Real-Time', 'refined_series': 'Day-Ahead',
                 'market_clearing_interval': 3600, 'interval_duration': 3600, 'duration_market_lead': 600}
    return {'tz': 'UTC', 'data_manager': {'class_name': 'NullDataManager'},
            'market_series': [real_time, day_ahead] if refining_first else [day_ahead, real_time]}


@pytest.mark.parametrize('refining_first', [False, True], ids=['refined series first', 'refining series first'])
def test_a_series_configured_to_refine_another_by_name_resolves_it_in_either_configuration_order(refining_first):
    tn = MockTN()
    with patch.object(Timer, 'now', return_value=datetime(2017, 7, 1, tzinfo=timezone.utc)):
        tn.configure_node(_two_series(refining_first))
    day_ahead = tn.get_market_series_by_attribute('name', 'Day-Ahead', first_only=True)
    real_time = tn.get_market_series_by_attribute('name', 'Real-Time', first_only=True)
    assert real_time.refined_series.name == 'Day-Ahead' and day_ahead.refined_series is None
    assert len(day_ahead.markets) == len(real_time.markets) == 1, 'each series has its first market once configured'
    first = real_time.newest_market
    assert first.market_state is MarketState.Initial
    assert first.market_to_be_refined is not None and first.market_to_be_refined.name == day_ahead.newest_market.name, \
        'the first real-time market refines the day-ahead market cleared at the same instant'


def test_a_refined_series_name_matching_no_series_is_a_configuration_error():
    configs = _two_series(refining_first=False)
    configs['market_series'][1]['refined_series'] = 'Day-After'
    with pytest.raises(ValueError, match="MarketSeries Real-Time: refined_series 'Day-After' names no market series"):
        MockTN().configure_node(configs)
