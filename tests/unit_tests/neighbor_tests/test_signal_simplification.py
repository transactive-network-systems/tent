"""
Simplification of the curves of a prepared transactive signal (T-59): Neighbor.signal_simplification_tolerance and
signal_max_vertices act in prep_transactive_signal, so the sent and archived signal is the simplified one; the receiving
side keeps what it is sent.
"""
import logging
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from tent.containers import Vertex
from tent.containers.sd_curve import SDCurve
from tent.local_asset import LocalAsset
from tent.market import Market
from tent.market.market_series import MarketSeries
from tent.neighbor import Neighbor
from tent.utils.timer import Timer
from tests.mock_tn import MockTN

T0 = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
SERIES = dict(name='Day Ahead', duration_market_lead=300.0, duration_delivery_lead=300.0, interval_duration=3600.0,
              intervals_to_clear=2, market_clearing_interval=3600.0, default_price=0.05)
STRAIGHT = [(0.02 + 0.01 * i, -1000.0 + 50.0 * i) for i in range(12)]  # a load curve the node offers its neighbor


class RecordingDataManager:
    def __init__(self):
        self.records = []

    def archive_data(self, records):
        self.records.extend(records if isinstance(records, (list, tuple)) else [records])


def build_node(name, peer, **neighbor_settings):
    tn = MockTN(name=name)
    tn._data_manager = RecordingDataManager()
    tn.neighbors = [Neighbor(transactive_node=tn, name=peer, transactive=True, up_or_down='upstream',
                             default_vertices=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]],
                             minimum_power=0.0, maximum_power=1000.0, loss_factor=0.0, demand_rate=0.0,
                             **neighbor_settings)]
    tn.local_assets = [LocalAsset(transactive_node=tn, name='Load', minimum_power=-1000.0, maximum_power=0.0)]
    tn.market_series = [MarketSeries(transactive_node=tn, market_class=Market, **SERIES)]
    for ti in tn.market_series[0].newest_market.time_intervals:
        tn.local_assets[0].active_vertices.update(ti, [Vertex(p, 0.0, q) for p, q in STRAIGHT], archive=False)
    return tn


@pytest.fixture
def clock():
    Timer.simulation = False
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: T0):
        yield


def flexibility_lengths(signal, market):
    """Vertices per interval other than the balance point, which prep_transactive_signal marks (interpolating a
    vertex) after the curve is simplified."""
    return [len(signal.flexibility_vertices(ti)) for ti in market.time_intervals]


def sent_curve_lengths(tn):
    neighbor, market = tn.neighbors[0], tn.market_series[0].newest_market
    neighbor.prep_transactive_signal(market)
    sent = neighbor.send_transactive_signal(market)
    return flexibility_lengths(sent, market)


def test_the_sent_and_archived_signal_is_the_simplified_one(clock, caplog):
    campus, city = build_node('campus', 'city', signal_simplification_tolerance=0.01), build_node('city', 'campus')
    campus.connect_peer(city)
    with caplog.at_level(logging.DEBUG, logger='tent.neighbor'):
        assert sent_curve_lengths(campus) == [2, 2], 'twelve collinear vertices went out as two'
    market = campus.market_series[0].newest_market
    prepared = campus.neighbors[0].prepared_signals[market]
    assert flexibility_lengths(prepared, market) == [2, 2], 'the prepared signal too'
    sent = campus.neighbors[0].last_sent_signals[market]
    assert all(len(sent.get_by_interval(ti)) == 3 for ti in market.time_intervals), 'two vertices and the balance point'
    sent_ids = {sent.get_by_interval(ti).id for ti in market.time_intervals}
    assert len([r for r in campus.data_manager.records if r['table_name'] == 'vertex'
                and r['data'].get('curve_id') in sent_ids]) == 6, 'three vertices per interval in the archive'
    city_market = city.market_series[0].newest_market
    received = city.neighbors[0].last_received_signals[city_market]
    assert flexibility_lengths(received, city_market) == [2, 2], 'the peer got two flexibility vertices per interval'
    assert any('simplified from 24 to 4 vertices over 2 intervals' in r.message for r in caplog.records), \
        'one DEBUG line per market names the counts'
    # What the neighbor would read at every original price is unchanged on a straight line (the flexibility vertices;
    # the balance point marked afterwards at the scheduled power may lie beyond the curve's last price).
    for ti in market.time_intervals:
        curve = SDCurve(None, ti, sent.flexibility_vertices(ti), archive=False)
        for price, quantity in STRAIGHT:
            assert curve.power_from_vertices(price) == pytest.approx(quantity, abs=1e-9)


def test_tolerance_zero_sends_every_vertex_and_the_receiving_side_is_untouched(clock):
    campus, city = build_node('campus', 'city'), build_node('city', 'campus', signal_simplification_tolerance=0.01)
    campus.connect_peer(city)
    assert sent_curve_lengths(campus) == [12, 12]
    market = city.market_series[0].newest_market
    received = city.neighbors[0].last_received_signals[market]
    assert flexibility_lengths(received, market) == [12, 12], \
        "a neighbor's own tolerance applies to what it sends, never to what it receives"


def test_a_cap_limits_the_vertices_of_each_sent_curve(clock):
    wavy = [(0.02 + 0.01 * i, -1000.0 + 50.0 * i + 30.0 * (i % 3)) for i in range(12)]
    campus = build_node('campus', 'city', signal_max_vertices=6)
    for ti in campus.market_series[0].newest_market.time_intervals:
        campus.local_assets[0].active_vertices.update(ti, [Vertex(p, 0.0, q) for p, q in wavy], archive=False,
                                                      replace=True)
    assert all(length <= 6 for length in sent_curve_lengths(campus))
    assert campus.neighbors[0].config.signal_max_vertices == 6


def test_the_settings_are_reconfigurable(clock):
    campus = build_node('campus', 'city')
    neighbor = campus.neighbors[0]
    assert {'signal_simplification_tolerance', 'signal_max_vertices'} <= Neighbor.Config.reconfigurable_fields()
    assert sent_curve_lengths(campus) == [12, 12]
    neighbor.reconfigure({'signal_simplification_tolerance': 0.01, 'signal_max_vertices': 8})
    assert neighbor.signal_simplification_tolerance == 0.01 and neighbor.signal_max_vertices == 8
    assert sent_curve_lengths(campus) == [2, 2]
    with pytest.raises(ValueError, match='signal_max_vertices: Input should be greater than or equal to 2'):
        neighbor.reconfigure({'signal_max_vertices': 1})
    with pytest.raises(ValueError, match='signal_simplification_tolerance: Input should be greater than or equal to 0'):
        Neighbor(campus, name='bad', signal_simplification_tolerance=-0.1)
