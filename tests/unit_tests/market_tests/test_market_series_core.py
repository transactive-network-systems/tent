import pytest
from datetime import datetime
from unittest.mock import Mock

from tent.market.market_series import MarketSeries
from tent.market import Market


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
