"""
MatchingConsensusMarket: the consensus protocol between nodes with an order book as each node's balancing step (design C
of the multi-node matching engine, "negotiated books"). The negotiation is ConsensusMarket's; the local balancing step
rebuilds and clears the node's book, and the exchange proposed to a neighbor is the quantity matched against its curve.
"""
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tent.enumerations import MarketState, MarketTypes, MeasurementType
from tent.market import Market
from tent.market.consensus_market import ConsensusMarket
from tent.market.market_series import MarketSeries
from tent.market.matching_consensus import MatchingConsensusMarket
from tent.neighbor import Neighbor
from tent.registry import resolve
from tent.utils.timer import Timer
from tests.integration_tests.test_consensus_negotiation_robustness import deployed_node_config, deployed_office_config
from tests.integration_tests.test_matching_auction import check_exchanges
from tests.mock_tn import MockTN
from tests.stubs import FlexibleAsset, clock_at

T0 = datetime(2017, 7, 1, 0, 0, tzinfo=timezone.utc)
INF = float('inf')
SERIES = dict(name='Consensus', duration_active=300.0, duration_negotiation=600.0, duration_market_lead=300.0,
              duration_delivery_lead=300.0, interval_duration=3600.0, intervals_to_clear=2,
              market_clearing_interval=3600.0, method='Interpolation', duality_gap_threshold=0.01,
              default_price=0.05)
CITY_CURVE = [[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]]  # a generator from 0 kW at 0.04 to 1000 kW at 0.08


class RecordingDataManager:
    def __init__(self):
        self.records = []

    def archive_data(self, records):
        self.records.extend(records if isinstance(records, (list, tuple)) else [records])

    def by_table(self, name):
        return [r['data'] for r in self.records if r.get('table_name') == name]


@pytest.fixture
def clock():
    state = {'t': T0}
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: state['t']):
        yield state


def build_node(name, peer, asset_curve, asset_limits, neighbor_curve, neighbor_limits, up_or_down):
    tn = MockTN(name=name)
    tn._data_manager = RecordingDataManager()
    tn.local_assets = [FlexibleAsset(transactive_node=tn, name=f'{name} asset', cost_parameters=[0.0, 0.0, 0.0],
                                     default_vertices=asset_curve, minimum_power=asset_limits[0],
                                     maximum_power=asset_limits[1])]
    tn.neighbors = [Neighbor(transactive_node=tn, name=peer, transactive=True, up_or_down=up_or_down,
                             default_vertices=neighbor_curve, minimum_power=neighbor_limits[0],
                             maximum_power=neighbor_limits[1], loss_factor=0.0, demand_rate=0.0,
                             convergence_threshold=0.02, cost_parameters=[0.0, 0.0, 0.0])]
    tn.market_series = [MarketSeries(transactive_node=tn, market_class=MatchingConsensusMarket,
                                     matching_strategy=['ORDER_AGE'], **SERIES)]
    return tn


@pytest.fixture
def nodes(clock):
    # The campus has an inelastic 100 kW load (a lone vertex: a market order in a book) and buys from the city, whose
    # generator's price rises from 0.04 to 0.08 over 0 to 1000 kW. Each node's default view of the other is deliberately
    # wrong (the city expects 50 kW of load), so the negotiation has something to correct.
    campus = build_node('campus', 'city', asset_curve=[[INF, 0.0, -100.0, True]], asset_limits=(-100.0, 0.0),
                        neighbor_curve=CITY_CURVE, neighbor_limits=(0.0, 1000.0), up_or_down='upstream')
    city = build_node('city', 'campus', asset_curve=CITY_CURVE, asset_limits=(0.0, 1000.0),
                      neighbor_curve=[[INF, 0.0, -50.0, True]], neighbor_limits=(-1000.0, 0.0), up_or_down='downstream')
    campus.connect_peer(city)
    city.connect_peer(campus)
    return campus, city


def market_of(tn):
    return tn.market_series[0].newest_market


def step_all(nodes, clock, t):
    clock['t'] = t
    for tn in nodes:
        tn.step()


def negotiate(nodes, markets, clock, times, max_ticks=12):
    step_all(nodes, clock, times.active)
    step_all(nodes, clock, times.negotiation)
    for seconds in range(1, max_ticks + 1):
        step_all(nodes, clock, times.negotiation + timedelta(seconds=seconds))
        if all(m.in_consensus for m in markets):
            return seconds
    return None


def test_the_class_is_registered_and_shares_the_book():
    assert resolve('MatchingConsensusMarket', Market) is MatchingConsensusMarket
    assert issubclass(MatchingConsensusMarket, ConsensusMarket)
    assert MatchingConsensusMarket.MARKET_TYPE is MarketTypes.matching_consensus
    assert MatchingConsensusMarket.BALANCE_POINT == 'matched_quantity'
    assert set(MatchingConsensusMarket.Config.model_fields) == {'actuate_assets', 'max_balance_iterations',
                                                                'matching_strategy', 'order_expiry'}
    tables = {d['name']: d for d in MatchingConsensusMarket.get_archive_definitions()}
    assert tables['matching_consensus']['inherits'] == 'market'
    assert [c['name'] for c in tables['matching_consensus']['columns']] == ['id', 'matching_strategy']
    assert 'order' in tables, 'the order book rows'


def test_two_nodes_negotiate_their_books_to_full_consensus(nodes, clock, caplog):
    campus, city = nodes
    m_campus, m_city = market_of(campus), market_of(city)
    times = m_campus.transition_times
    with caplog.at_level(logging.WARNING, logger='tent.market'):
        ticks = negotiate(nodes, [m_campus, m_city], clock, times)
    assert ticks is not None and ticks <= 4, 'both books agree on the exchange within a few exchanges of signals'
    assert not [r for r in caplog.records if 'cannot balance' in r.getMessage()]
    for market in (m_campus, m_city):
        assert market.converged and market.in_consensus and not market.unbalanceable
        assert market.clearings >= 2, 'the opening pass and at least one more after the peer\'s signal'
        for ti in market.time_intervals:
            assert bool(market.full_consensus.get_by_interval(ti).value) is True
            assert len([leg for leg in market.matchList if leg.interval.name == ti.name]) == 2, 'one trade, two legs'
    # The exchange: the campus imports its 100 kW and the city supplies it, on both sides of the link.
    for ti in m_campus.time_intervals:
        assert campus.neighbors[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(100.0)
        assert campus.local_assets[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(-100.0)
        assert m_campus.cleared_power(campus.neighbors[0], ti) == pytest.approx(100.0)
    for ti in m_city.time_intervals:
        assert city.neighbors[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(-100.0)
        assert city.local_assets[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(100.0)
    # The balance points agree in each node's perspective, and the city's carries the price of its residual curve at
    # the matched quantity (0.04 + 0.04 * 100 / 1000); the campus's residual is its inelastic load, which has no price.
    for tn, market in ((campus, m_campus), (city, m_city)):
        neighbor = tn.neighbors[0]
        sent, received = neighbor.last_sent_signals[market], neighbor.last_received_signals[market]
        for ti in market.time_intervals:
            assert sent.balance_point(ti).quantity == pytest.approx(-received.balance_point(ti).quantity, abs=0.5)
    for ti in m_city.time_intervals:
        point = city.neighbors[0].last_sent_signals[m_city].balance_point(ti)
        assert point.quantity == pytest.approx(100.0) and point.price == pytest.approx(0.044)
    # Both books price the trade at the generator curve's price for 100 kW, 0.044, the consensus variant's price: in the
    # campus's book the load rests as a market order and the city's curve, the aggressive side, prices the fill; in the
    # city's book the generator's curve rests and the campus's market order is filled inside the curve's segment at the
    # balance price (the linear standing-order case), not at the rung above it.
    for market in (m_campus, m_city):
        for ti in market.time_intervals:
            assert market.marginal_prices.get_by_interval(ti).value == pytest.approx(0.044, abs=1e-6)
    # Nothing further is sent once consensus holds.
    sent_before = {tn.name: tn.neighbors[0].last_sent_signals[m].sequence for tn, m in ((campus, m_campus), (city, m_city))}
    step_all(nodes, clock, times.negotiation + timedelta(seconds=30))
    assert {tn.name: tn.neighbors[0].last_sent_signals[m].sequence for tn, m in ((campus, m_campus), (city, m_city))} == sent_before
    # The deadline moves both on cleanly; each archive holds the market row, its final book and the consensus flags.
    with caplog.at_level(logging.WARNING, logger='tent.market'):
        step_all(nodes, clock, times.market_lead)
    assert m_campus.market_state is MarketState.MarketLead and m_city.market_state is MarketState.MarketLead
    assert not any('timed out' in r.getMessage() for r in caplog.records)
    for tn, market in ((campus, m_campus), (city, m_city)):
        rows = [r for r in tn.data_manager.by_table('matching_consensus') if r['id'] == market.id]
        assert len(rows) == 1 and rows[0]['matching_strategy'] == ['ORDER_AGE']
        archived = {row['id'] for row in tn.data_manager.by_table('order')}
        assert all(leg.id in archived for leg in market.matchList), 'the final book\'s legs are archived'
        assert all(order.id in archived for order in market.orderList)
        headers = [h for h in tn.data_manager.by_table('measurement')
                   if h['measurement_type'] in (MeasurementType.FullConsensus, MeasurementType.FullConsensus.name)]
        assert headers, f'{tn.name} archived a FullConsensus measurement header'


def test_only_the_final_book_of_each_balancing_pass_is_archived(nodes, clock, monkeypatch):
    """Each balancing iteration clears a fresh book and only the book standing at the end of the pass is written, so the
    archive holds one book per pass (here one iteration each: the first iteration balances), never a book an iteration
    threw away."""
    campus, city = nodes
    m_campus, m_city = market_of(campus), market_of(city)
    flushed, resets = {m_campus: [], m_city: []}, {m_campus: 0, m_city: 0}
    original_flush, original_reset = MatchingConsensusMarket.flush_held_orders, MatchingConsensusMarket.reset_book

    def counting_flush(self):
        flushed[self].append(len(self._held_orders))
        original_flush(self)

    def counting_reset(self):
        resets[self] += 1
        original_reset(self)

    monkeypatch.setattr(MatchingConsensusMarket, 'flush_held_orders', counting_flush)
    monkeypatch.setattr(MatchingConsensusMarket, 'reset_book', counting_reset)
    times = m_campus.transition_times
    assert negotiate(nodes, [m_campus, m_city], clock, times) is not None
    for tn, market in ((campus, m_campus), (city, m_city)):
        passes = len(flushed[market])
        assert resets[market] == market.clearings >= passes >= 2, 'every iteration clears a fresh book; one flush per pass'
        rows = tn.data_manager.by_table('order')
        assert len(rows) == sum(flushed[market]), 'exactly the final book of every pass'
        assert all(leg.id in {row['id'] for row in rows} for leg in market.matchList)


def test_local_change_after_consensus_is_renegotiated(nodes, clock):
    campus, city = nodes
    m_campus, m_city = market_of(campus), market_of(city)
    times = m_campus.transition_times
    assert negotiate(nodes, [m_campus, m_city], clock, times) is not None
    campus_received_before = campus.neighbors[0].last_received_signals[m_campus].sequence
    # The city's generator becomes dearer: its curve shifts up, it reclears its book and must tell the campus.
    city.local_assets[0].default_vertices.vertices[0].price = 0.06
    city.local_assets[0].default_vertices.vertices[1].price = 0.10
    m_city.on_local_change(city.local_assets[0])
    clock['t'] = times.negotiation + timedelta(seconds=20)
    city.step()
    assert campus.neighbors[0].last_received_signals[m_campus].sequence > campus_received_before
    assert not m_campus.in_consensus, 'the campus has not yet answered the revised signal'
    for seconds in range(21, 30):
        step_all(nodes, clock, times.negotiation + timedelta(seconds=seconds))
        if m_campus.in_consensus and m_city.in_consensus:
            break
    assert m_campus.in_consensus and m_city.in_consensus
    for market in (m_campus, m_city):
        for ti in market.time_intervals:
            assert market.marginal_prices.get_by_interval(ti).value == pytest.approx(0.064, abs=1e-6)
            assert bool(market.full_consensus.get_by_interval(ti).value) is True
    for ti in m_campus.time_intervals:
        assert campus.neighbors[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(100.0)


def test_an_unfilled_market_order_keeps_the_interval_out_of_consensus(clock, caplog):
    """A 100 kW inelastic load against a neighbor curve of at most 50 kW: the book fills 50 kW and 50 kW of market
    order stands. No price clears it, so the interval is recorded as unbalanceable, the market is not converged, the
    signal still goes out and the deficit is said once."""
    campus = build_node('campus', 'city', asset_curve=[[INF, 0.0, -100.0, True]], asset_limits=(-100.0, 0.0),
                        neighbor_curve=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 50.0, True]], neighbor_limits=(0.0, 50.0),
                        up_or_down='upstream')
    sent = []
    campus.register_recipient('city', sent.append)  # the peer never answers
    market = market_of(campus)
    times = market.transition_times
    clock['t'] = times.active
    campus.step()
    with caplog.at_level(logging.WARNING, logger='tent.market'):
        clock['t'] = times.negotiation
        campus.step()
        for seconds in range(1, 4):
            clock['t'] = times.negotiation + timedelta(seconds=seconds)
            campus.step()
    assert market.unbalanceable == {ti.name: pytest.approx(-50.0) for ti in market.time_intervals}
    assert not market.in_consensus, 'an interval the book cannot balance is out of consensus'
    assert all(bool(v) is False for v in market.full_consensus.values)
    deficits = [r.getMessage() for r in caplog.records if 'cannot balance' in r.getMessage()]
    assert len(deficits) == len(market.time_intervals), 'once per interval, not once per pass'
    assert 'deficit of 50 kW of market orders' in deficits[0]
    assert len(sent) == 1 and sent[0]['revised'] is True, 'the opening signal went, and nothing new came to answer'
    for ti in market.time_intervals:
        assert campus.neighbors[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(50.0), 'what matched'
        assert campus.local_assets[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(-50.0)


# ---------------------------------------------------------------------------------------- the deployed hierarchy

@pytest.mark.skipif(deployed_office_config() is None, reason='the deployed consensus container configurations are not '
                                                             'beside this repository')
def test_the_deployed_hierarchy_negotiates_its_books_to_consensus(tmp_path, caplog):
    """Campus, city and the two offices from the deployed consensus configurations, every series a
    MatchingConsensusMarket, wired as the message bus wires them: every node's book clears, the nodes reach full
    consensus on the real-time market within a few ticks, and both ends of every link agree on the exchange."""
    clock_at(datetime(2017, 7, 3, 0, 0, tzinfo=timezone.utc))
    nodes = {}
    for node, file_name in (('campus', 'tn.campus.json'), ('city', 'tn.city.json'),
                            ('small_office_dr', 'tn.so.dr.json'), ('small_office_ilc', 'tn.so.ilc.json')):
        tn = MockTN()
        tn.configure_node(deployed_node_config(node, file_name, tmp_path, market_class_name='MatchingConsensusMarket'))
        nodes[tn.name] = tn
    campus = nodes['PNNL_Campus']
    for name, tn in nodes.items():
        if tn is not campus:
            campus.connect_peer(tn)
            tn.connect_peer(campus)
    markets = {name: next(s for s in tn.market_series if s.name == 'Real-Time_Auction').newest_market
               for name, tn in nodes.items()}
    assert all(isinstance(m, MatchingConsensusMarket) for m in markets.values())
    times = markets['PNNL_Campus'].transition_times
    with caplog.at_level(logging.WARNING, logger='tent.market'):
        for when in (times.active, times.negotiation):
            clock_at(when)
            for tn in nodes.values():
                tn.step()
        for tick in range(1, 13):
            clock_at(times.negotiation + timedelta(seconds=tick))
            for tn in nodes.values():
                tn.step()
            if all(m.in_consensus for m in markets.values()):
                break
        else:
            pytest.fail('no full consensus in 12 ticks: ' + ', '.join(
                f'{name}: converged {m.converged}, unbalanceable {sorted(m.unbalanceable)}, neighbors '
                f'{[(n.name, n.is_converged(m)) for n in m.transactive_neighbors]}' for name, m in markets.items()))
    for name, market in markets.items():
        assert market.matchList, f'{name} cleared its book'
        assert not market.unbalanceable, name
        assert all(bool(market.full_consensus.get_by_interval(ti).value) for ti in market.time_intervals), name
    traceback_free = [r for r in caplog.records if 'Traceback' in r.getMessage()]
    assert not traceback_free
    check_exchanges(nodes, markets)
