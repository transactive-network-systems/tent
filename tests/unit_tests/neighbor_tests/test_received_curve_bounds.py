"""A received transactive signal is bounded to the neighbor's configured power limits, interpolating the price at the
limit, before losses are applied: an offer larger than the link can carry is priced at the limit, not at the offer's
top (found when the campus supplier's offer was raised from 20 to 30 MW on a 20 MW link)."""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from tent.containers import Vertex
from tent.containers.transactive_signal import TransactiveSignal
from tent.enumerations import SignalDirection
from tent.market import Market
from tent.market.market_series import MarketSeries
from tent.neighbor import Neighbor
from tent.utils.timer import Timer
from tests.mock_tn import MockTN

T0 = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
OFFER = [(0.03, 0.0), (0.07, 3000.0)]  # the supplier offers 3 MW on a link rated for 1 MW


def node(**neighbor_settings):
    tn = MockTN(name='campus')
    tn.neighbors = [Neighbor(transactive_node=tn, name='supplier', transactive=True, up_or_down='upstream',
                             **neighbor_settings)]
    tn.market_series = [MarketSeries(transactive_node=tn, market_class=Market, name='Core', intervals_to_clear=1)]
    market = tn.market_series[0].newest_market
    signal = TransactiveSignal(market=market, neighbor=tn.neighbors[0], direction=SignalDirection.received)
    for ti in market.time_intervals:
        signal.update(ti, [Vertex(price=p, quantity=q) for p, q in OFFER], archive=False)
    tn.neighbors[0].last_received_signals[market] = signal
    return tn, market


@pytest.fixture
def clock():
    Timer.simulation = False
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: T0):
        yield


def test_a_received_offer_beyond_the_link_limit_is_bounded_at_the_interpolated_price(clock):
    tn, market = node(minimum_power=0.0, maximum_power=1000.0, loss_factor=0.0, demand_rate=0.0)
    tn.neighbors[0].update_vertices(market)
    curve = tn.neighbors[0].active_vertices.get_by_interval(market.time_intervals[0])
    assert [(v.price, v.quantity) for v in curve.vertices] == [(0.03, 0.0), (pytest.approx(0.03 + 0.04 / 3), 1000.0)], \
        'the offer is cut at 1 MW where it is priced 0.0433, not at its 0.07 top'


def test_losses_then_apply_to_the_bounded_curve(clock):
    tn, market = node(minimum_power=0.0, maximum_power=1000.0, loss_factor=0.03, demand_rate=0.0)
    tn.neighbors[0].update_vertices(market)
    curve = tn.neighbors[0].active_vertices.get_by_interval(market.time_intervals[0])
    top = curve.vertices[-1]
    assert top.quantity == pytest.approx(1000.0 * (1 - 0.03)), 'the link delivers 970 kW of the 1 MW at full load'
    assert 0.0433 < top.price < 0.05, 'the loss markup is on the price at the limit (0.0433), not on 0.07'


def test_a_neighbor_without_a_configured_range_takes_the_offer_as_received(clock):
    tn, market = node(loss_factor=0.0, demand_rate=0.0)  # minimum_power == maximum_power == 0.0: no range
    tn.neighbors[0].update_vertices(market)
    curve = tn.neighbors[0].active_vertices.get_by_interval(market.time_intervals[0])
    assert [(v.price, v.quantity) for v in curve.vertices] == OFFER
