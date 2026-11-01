import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock

from tent.market.market_series import MarketSeries
from tent.market import Market
from tests.mock_tn import MockTN


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
