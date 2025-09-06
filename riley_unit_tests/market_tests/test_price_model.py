import pytest
from datetime import datetime
from tent.market.price_models.hourly_average_price_model import HourlyAveragePriceModel


def test_price_model_update_and_get_basic():
    model = HourlyAveragePriceModel(forgetting_factor=5)
    ts = datetime(2025, 1, 1, 9, 15, 0)
    for p in [0.05, 0.06, 0.07]:
        model.update(ts, p)
    mean, std = model.get(ts)
    assert 0.05 <= mean <= 0.07
    assert std >= 0.0


def test_price_model_forgetting_factor_ring_buffer():
    model = HourlyAveragePriceModel(forgetting_factor=3)
    ts = datetime(2025, 1, 1, 18, 0, 0)
    for p in [0.01, 0.02, 0.03, 0.04]:
        model.update(ts, p)
    # Only last 3 retained
    hour_buf = list(model.current_model[18])
    assert hour_buf == [0.02, 0.03, 0.04]


def test_price_model_statistics_error_handled(monkeypatch):
    model = HourlyAveragePriceModel(forgetting_factor=1)
    ts = datetime(2025, 1, 1, 12, 0, 0)
    # Force empty deque for hour -> triggers StatisticsError handling path
    model.current_model[12].clear()
    mean, std = model.get(ts)
    assert mean is None and std is None
