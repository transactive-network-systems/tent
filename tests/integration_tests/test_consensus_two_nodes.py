"""
Two nodes negotiate a ConsensusMarket to full consensus in one process.

"campus" has an inelastic 100 kW load and buys from "city"; "city" has a flexible generator whose price rises from 0.04
to 0.08 $/kWh over 0 to 1000 kW. Each node is a MockTN with one transactive neighbor representing the other, wired
together with MockTN.connect_peer so a sent signal lands in the peer's receive callback, as a message bus would deliver
it. Both nodes share the Timer, which the test drives through the market state machine: Active, Negotiation (opening
signals, replies, convergence), then MarketLead.

The expected equilibrium: the city supplies 100 kW at 0.04 + 0.04 * 100 / 1000 = 0.044 $/kWh, both nodes clear at that
price, campus schedules +100 kW from its city neighbor and city schedules -100 kW to its campus neighbor, and every
interval records full consensus on both sides.
"""
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tent.enumerations import MarketState, MeasurementType
from tent.market.consensus_market import ConsensusMarket
from tent.market.market_series import MarketSeries
from tent.neighbor import Neighbor
from tent.utils.timer import Timer
from tests.mock_tn import MockTN
from tests.stubs import FlexibleAsset

T0 = datetime(2017, 7, 1, 0, 0, tzinfo=timezone.utc)
INF = float('inf')


class RecordingDataManager:
    def __init__(self):
        self.records = []

    def archive_data(self, records):
        self.records.extend(records if isinstance(records, (list, tuple)) else [records])

    def by_table(self, name):
        return [r['data'] for r in self.records if r.get('table_name') == name]


SERIES = dict(name='Consensus', duration_active=300.0, duration_negotiation=600.0, duration_market_lead=300.0,
              duration_delivery_lead=300.0, interval_duration=3600.0, intervals_to_clear=2,
              market_clearing_interval=3600.0, method='Interpolation', duality_gap_threshold=0.01,
              default_price=0.05)


def build_node(name, peer, asset_curve, asset_limits, neighbor_curve, neighbor_limits, up_or_down, loss_factor=0.0):
    return build_node_multi(name, asset_curve, asset_limits,
                            [dict(peer=peer, curve=neighbor_curve, limits=neighbor_limits, up_or_down=up_or_down,
                                  loss_factor=loss_factor)])


def build_node_multi(name, asset_curve, asset_limits, neighbors):
    tn = MockTN(name=name)
    tn._data_manager = RecordingDataManager()
    asset = FlexibleAsset(transactive_node=tn, name=f'{name} asset', cost_parameters=[0.0, 0.0, 0.0],
                          default_vertices=asset_curve, minimum_power=asset_limits[0], maximum_power=asset_limits[1])
    tn.local_assets = [asset]
    tn.neighbors = [Neighbor(transactive_node=tn, name=n['peer'], transactive=True, up_or_down=n['up_or_down'],
                             default_vertices=n['curve'], minimum_power=n['limits'][0], maximum_power=n['limits'][1],
                             loss_factor=n.get('loss_factor', 0.0), demand_rate=0.0, convergence_threshold=0.02,
                             cost_parameters=[0.0, 0.0, 0.0]) for n in neighbors]
    tn.market_series = [MarketSeries(transactive_node=tn, market_class=ConsensusMarket, **SERIES)]
    return tn


def negotiate(all_nodes, markets, clock, times, max_ticks=12):
    """Drive every node through Active and into Negotiation, then tick until all markets declare consensus."""
    step_both(all_nodes, clock, times.active)
    step_both(all_nodes, clock, times.negotiation)
    for seconds in range(1, max_ticks + 1):
        step_both(all_nodes, clock, times.negotiation + timedelta(seconds=seconds))
        if all(m.in_consensus for m in markets):
            return seconds
    return None


@pytest.fixture
def clock():
    state = {'t': T0}
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: state['t']):
        yield state


@pytest.fixture
def nodes(clock):
    # Vertex lists are [price, cost, quantity, continuity].
    campus = build_node('campus', 'city',
                        asset_curve=[[INF, 0.0, -100.0, True]], asset_limits=(-100.0, 0.0),
                        neighbor_curve=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]],
                        neighbor_limits=(0.0, 1000.0), up_or_down='upstream')
    city = build_node('city', 'campus',
                      asset_curve=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]], asset_limits=(0.0, 1000.0),
                      neighbor_curve=[[INF, 0.0, -50.0, True]], neighbor_limits=(-1000.0, 0.0),
                      up_or_down='downstream')
    campus.connect_peer(city)
    city.connect_peer(campus)
    return campus, city


def market_of(tn):
    return tn.market_series[0].newest_market


def step_both(nodes, clock, t):
    clock['t'] = t
    for tn in nodes:
        tn.step()


def test_two_nodes_reach_full_consensus(nodes, clock, caplog):
    campus, city = nodes
    m_campus, m_city = market_of(campus), market_of(city)
    assert m_campus.name == m_city.name, 'a signal is routed to the market of the same name on the peer'
    times = m_campus.transition_times
    assert m_city.transition_times == times

    step_both(nodes, clock, times.active)
    assert m_campus.market_state is MarketState.Active and m_city.market_state is MarketState.Active

    # Entering Negotiation: each node balances with its neighbor's default curve and sends an opening signal, which is
    # delivered synchronously to the peer, whose market reopens negotiation on receipt.
    step_both(nodes, clock, times.negotiation)
    assert m_campus.market_state is MarketState.Negotiation and m_city.market_state is MarketState.Negotiation
    for tn, market in ((campus, m_campus), (city, m_city)):
        neighbor = tn.neighbors[0]
        assert market in neighbor.last_sent_signals, f'{tn.name} sent an opening signal'
        assert market in neighbor.last_received_signals, f'{tn.name} received the peer\'s opening signal'

    # A few ticks of negotiation: absorb the peer's signal, rebalance, reply only while not converged.
    for seconds in range(1, 8):
        step_both(nodes, clock, times.negotiation + timedelta(seconds=seconds))
        if m_campus.in_consensus and m_city.in_consensus:
            break
    assert m_campus.in_consensus and m_city.in_consensus, 'both nodes reached full consensus'
    assert not m_campus._stateIsCompleted, 'negotiation stays open until its deadline'

    for market in (m_campus, m_city):
        assert market.converged
        for ti in market.time_intervals:
            assert market.marginal_prices.get_by_interval(ti).value == pytest.approx(0.044, abs=1e-6)
            assert bool(market.full_consensus.get_by_interval(ti).value) is True
    for ti in m_campus.time_intervals:
        assert campus.neighbors[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(100.0, abs=0.5)
        assert campus.local_assets[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(-100.0)
    for ti in m_city.time_intervals:
        assert city.neighbors[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(-100.0, abs=0.5)
        assert city.local_assets[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(100.0, abs=0.5)

    # Sent and received signals agree at the balance point, in each node's own perspective.
    for tn, market in ((campus, m_campus), (city, m_city)):
        neighbor = tn.neighbors[0]
        sent, received = neighbor.last_sent_signals[market], neighbor.last_received_signals[market]
        for ti in market.time_intervals:
            assert sent.balance_point(ti) is not None and received.balance_point(ti) is not None
            assert sent.balance_point(ti).quantity == pytest.approx(-received.balance_point(ti).quantity, abs=0.5)
        assert neighbor.is_converged(market)

    # Nothing further is sent once consensus holds.
    sent_before = {tn.name: tn.neighbors[0].last_sent_signals[m].id for tn, m in ((campus, m_campus), (city, m_city))}
    step_both(nodes, clock, times.negotiation + timedelta(seconds=30))
    assert {tn.name: tn.neighbors[0].last_sent_signals[m].id for tn, m in ((campus, m_campus), (city, m_city))} == sent_before

    # The negotiation deadline moves both markets on cleanly (no forced completion), and the consensus history is in
    # the archive.
    with caplog.at_level(logging.WARNING, logger='tent.market'):
        step_both(nodes, clock, times.market_lead)
    assert m_campus.market_state is MarketState.MarketLead and m_city.market_state is MarketState.MarketLead
    assert not any('timed out' in r.getMessage() for r in caplog.records)
    for tn in nodes:
        headers = [h for h in tn.data_manager.by_table('measurement')
                   if h['measurement_type'] in (MeasurementType.FullConsensus, MeasurementType.FullConsensus.name)]
        assert headers, f'{tn.name} archived a FullConsensus measurement header'
        values = tn.data_manager.by_table('measurement_value')
        consensus_values = [v['value'] for v in values if v['measurement_id'] == headers[0]['id']]
        assert consensus_values and consensus_values[-1] in (True, 1.0)


def test_local_change_after_consensus_is_renegotiated(nodes, clock):
    campus, city = nodes
    m_campus, m_city = market_of(campus), market_of(city)
    times = m_campus.transition_times
    step_both(nodes, clock, times.active)
    step_both(nodes, clock, times.negotiation)
    for seconds in range(1, 8):
        step_both(nodes, clock, times.negotiation + timedelta(seconds=seconds))
        if m_campus.in_consensus and m_city.in_consensus:
            break
    assert m_campus.in_consensus and m_city.in_consensus
    campus_received_before = campus.neighbors[0].last_received_signals[m_campus].sequence

    # The city's generator becomes more expensive: its curve shifts up, it rebalances and must tell the campus.
    city.local_assets[0].default_vertices.vertices[0].price = 0.06
    city.local_assets[0].default_vertices.vertices[1].price = 0.10
    m_city.on_local_change(city.local_assets[0])  # the asset model reports that its conditions changed
    clock['t'] = times.negotiation + timedelta(seconds=10)
    city.step()
    assert campus.neighbors[0].last_received_signals[m_campus].sequence > campus_received_before, \
        'the city told the campus about its change'
    assert not m_campus.in_consensus, 'the campus has not yet answered the revised signal'
    for seconds in range(11, 20):
        step_both(nodes, clock, times.negotiation + timedelta(seconds=seconds))
        if m_campus.in_consensus and m_city.in_consensus:
            break
    assert m_campus.in_consensus and m_city.in_consensus
    for market in (m_campus, m_city):
        for ti in market.time_intervals:
            assert market.marginal_prices.get_by_interval(ti).value == pytest.approx(0.064, abs=1e-6)
            assert bool(market.full_consensus.get_by_interval(ti).value) is True


def test_no_reply_leaves_consensus_unreached_at_the_deadline(clock):
    campus = build_node('campus', 'city',
                        asset_curve=[[INF, 0.0, -100.0, True]], asset_limits=(-100.0, 0.0),
                        neighbor_curve=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]],
                        neighbor_limits=(0.0, 1000.0), up_or_down='upstream')
    campus.register_recipient('city', lambda message: None)  # the peer never answers
    market = market_of(campus)
    times = market.transition_times
    clock['t'] = times.active
    campus.step()
    clock['t'] = times.negotiation
    campus.step()
    for seconds in range(1, 5):
        clock['t'] = times.negotiation + timedelta(seconds=seconds)
        campus.step()
    assert market.converged, 'the local problem balances against the neighbor\'s default curve'
    assert not market.in_consensus, 'but without a reply there is no consensus'
    assert all(bool(v) is False for v in market.full_consensus.values)
    clock['t'] = times.market_lead
    campus.step()
    assert market.market_state is MarketState.MarketLead
    assert all(bool(v) is False for v in market.full_consensus.values)


def test_three_node_star_reaches_one_price(clock):
    """A city serving two campuses: every node clears at the city's marginal cost for the total load."""
    city_curve = [[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]]
    campus_a = build_node('campus_a', 'city', asset_curve=[[INF, 0.0, -100.0, True]], asset_limits=(-100.0, 0.0),
                          neighbor_curve=city_curve, neighbor_limits=(0.0, 1000.0), up_or_down='upstream')
    campus_b = build_node('campus_b', 'city', asset_curve=[[INF, 0.0, -200.0, True]], asset_limits=(-200.0, 0.0),
                          neighbor_curve=city_curve, neighbor_limits=(0.0, 1000.0), up_or_down='upstream')
    city = build_node_multi('city', asset_curve=city_curve, asset_limits=(0.0, 1000.0), neighbors=[
        dict(peer='campus_a', curve=[[INF, 0.0, -50.0, True]], limits=(-1000.0, 0.0), up_or_down='downstream'),
        dict(peer='campus_b', curve=[[INF, 0.0, -50.0, True]], limits=(-1000.0, 0.0), up_or_down='downstream')])
    for a, b in ((campus_a, city), (campus_b, city)):
        a.connect_peer(b)
        b.connect_peer(a)
    all_nodes = [campus_a, campus_b, city]
    markets = [market_of(tn) for tn in all_nodes]
    times = markets[0].transition_times

    ticks = negotiate(all_nodes, markets, clock, times)
    assert ticks is not None, 'all three nodes declared full consensus'

    expected_price = 0.04 + 0.04 * 300.0 / 1000.0  # the city supplies 300 kW
    for market in markets:
        for ti in market.time_intervals:
            assert market.marginal_prices.get_by_interval(ti).value == pytest.approx(expected_price, abs=1e-6)
            assert bool(market.full_consensus.get_by_interval(ti).value) is True
    m_city = markets[2]
    for ti in m_city.time_intervals:
        assert city.local_assets[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(300.0, abs=0.5)
        by_name = {n.name: n.scheduled_powers.get_by_interval(ti).value for n in city.neighbors}
        assert by_name['campus_a'] == pytest.approx(-100.0, abs=0.5)
        assert by_name['campus_b'] == pytest.approx(-200.0, abs=0.5)
    # Each campus sees the city's residual after the other campus: campus A's neighbor curve is the city curve shifted
    # by campus B's 200 kW, and it still schedules its own 100 kW import.
    for campus, market, load in ((campus_a, markets[0], 100.0), (campus_b, markets[1], 200.0)):
        for ti in market.time_intervals:
            assert campus.neighbors[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(load, abs=0.5)


def test_consensus_with_line_losses(clock, caplog):
    """With a loss factor on the campus side the two nodes price the exchange differently but still agree on it: the
    campus states its balance point as the power the city sends, which is what the city schedules."""
    campus = build_node('campus', 'city', asset_curve=[[INF, 0.0, -100.0, True]], asset_limits=(-100.0, 0.0),
                        neighbor_curve=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]],
                        neighbor_limits=(0.0, 1000.0), up_or_down='upstream', loss_factor=0.03)
    city = build_node('city', 'campus', asset_curve=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]],
                      asset_limits=(0.0, 1000.0), neighbor_curve=[[INF, 0.0, -50.0, True]],
                      neighbor_limits=(-1000.0, 0.0), up_or_down='downstream')
    campus.connect_peer(city)
    city.connect_peer(campus)
    all_nodes = [campus, city]
    markets = [market_of(tn) for tn in all_nodes]
    times = markets[0].transition_times

    with caplog.at_level(logging.WARNING):
        ticks = negotiate(all_nodes, markets, clock, times)
    assert ticks is not None, 'both nodes declared full consensus despite the loss model'
    assert not any('Indeterminate price' in r.getMessage() for r in caplog.records)
    m_campus, m_city = markets
    for market in markets:
        assert all(bool(v) is True for v in market.full_consensus.values)
    # Intervals are per market object, so pair them by start time.
    for ti_campus, ti_city in zip(m_campus.time_intervals, m_city.time_intervals):
        assert ti_campus.start_time == ti_city.start_time
        # The campus must import its load plus losses; the city supplies what the campus asks for.
        imported = campus.neighbors[0].scheduled_powers.get_by_interval(ti_campus).value
        supplied = -city.neighbors[0].scheduled_powers.get_by_interval(ti_city).value
        sent_by_city = -campus.neighbors[0].balance_point_quantities(m_campus)[ti_campus]
        assert imported == pytest.approx(100.0), 'the campus load arrives in full'
        assert supplied == pytest.approx(sent_by_city, abs=0.5), 'and the city sends what the campus said it must'
        assert supplied > imported, 'the losses are supplied too'
        # The campus pays the city's price plus the marginal loss adder.
        assert (m_campus.marginal_prices.get_by_interval(ti_campus).value
                > m_city.marginal_prices.get_by_interval(ti_city).value)


def test_two_consensus_series_keep_separate_coordination_state(clock):
    """Two consensus series negotiate at once on both nodes; each neighbor tracks them independently."""
    second = dict(SERIES, name='Consensus B')
    campus = build_node('campus', 'city', asset_curve=[[INF, 0.0, -100.0, True]], asset_limits=(-100.0, 0.0),
                        neighbor_curve=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]],
                        neighbor_limits=(0.0, 1000.0), up_or_down='upstream')
    city = build_node('city', 'campus', asset_curve=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]],
                      asset_limits=(0.0, 1000.0), neighbor_curve=[[INF, 0.0, -50.0, True]],
                      neighbor_limits=(-1000.0, 0.0), up_or_down='downstream')
    for tn in (campus, city):
        tn.market_series.append(MarketSeries(transactive_node=tn, market_class=ConsensusMarket, **second))
    campus.connect_peer(city)
    city.connect_peer(campus)
    all_nodes = [campus, city]
    markets = [series.newest_market for tn in all_nodes for series in tn.market_series]
    times = markets[0].transition_times
    assert all(m.transition_times == times for m in markets)

    ticks = negotiate(all_nodes, markets, clock, times)
    assert ticks is not None, 'all four markets (two series on two nodes) declared full consensus'
    for tn in all_nodes:
        neighbor = tn.neighbors[0]
        own = [m for m in markets if m.tn.name == tn.name]
        assert len(neighbor._coordination) == 2 and all(neighbor.is_converged(m) for m in own)
        # Forgetting one market's state leaves the other's untouched.
        neighbor.reset_coordination(own[0])
        assert not neighbor.is_converged(own[0]) and neighbor.is_signal_due(own[0])
        assert neighbor.is_converged(own[1]) and not neighbor.is_signal_due(own[1])


def test_rolling_markets_open_at_the_prior_cleared_price(clock):
    """Overlapping markets of one series: the second market negotiates on its own and starts from the first market's
    cleared prices for the intervals they share."""
    rolling = dict(SERIES, intervals_to_clear=3)
    campus = build_node_multi('campus', asset_curve=[[INF, 0.0, -100.0, True]], asset_limits=(-100.0, 0.0),
                              neighbors=[dict(peer='city', curve=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]],
                                              limits=(0.0, 1000.0), up_or_down='upstream')])
    city = build_node_multi('city', asset_curve=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]],
                            asset_limits=(0.0, 1000.0),
                            neighbors=[dict(peer='campus', curve=[[INF, 0.0, -50.0, True]], limits=(-1000.0, 0.0),
                                            up_or_down='downstream')])
    for tn in (campus, city):
        tn.market_series = [MarketSeries(transactive_node=tn, market_class=ConsensusMarket, **rolling)]
    campus.connect_peer(city)
    city.connect_peer(campus)
    all_nodes = [campus, city]
    first = [market_of(tn) for tn in all_nodes]
    t1 = first[0].transition_times
    assert len(list(first[0].time_intervals)) == 3

    assert negotiate(all_nodes, first, clock, t1) is not None
    for m in first:
        assert all(p == pytest.approx(0.044, abs=1e-6) for p in m.marginal_prices.values)
    # The series spawned the second market when the first became Active; it overlaps the first on two intervals.
    second = [tn.market_series[0].newest_market for tn in all_nodes]
    assert all(s is not f for s, f in zip(second, first))
    shared = {ti.start_time for ti in first[0].time_intervals} & {ti.start_time for ti in second[0].time_intervals}
    assert len(shared) == 2
    t2 = second[0].transition_times
    assert t2.negotiation > t1.market_lead

    # Move the first market through MarketLead, DeliveryLead and into Delivery, then open the second negotiation.
    for t in (t1.market_lead, t1.delivery_lead, t1.delivery):
        step_both(all_nodes, clock, t)
    assert all(m.market_state is MarketState.Delivery for m in first)
    step_both(all_nodes, clock, t2.active)
    step_both(all_nodes, clock, t2.negotiation)
    assert all(m.market_state is MarketState.Negotiation for m in second)
    # When the second market opened, the intervals it shares with the first carried the first's cleared prices, not the
    # series default, and the new interval carried the default.
    for m in second:
        opened = m.opening_prices
        for ti in m.time_intervals:
            expected = 0.044 if ti.start_time in shared else SERIES['default_price']
            assert opened[ti.start_time] == pytest.approx(expected, abs=1e-6), ti.name
    for seconds in range(1, 8):
        step_both(all_nodes, clock, t2.negotiation + timedelta(seconds=seconds))
        if all(m.in_consensus for m in second):
            break
    assert all(m.in_consensus for m in second)
    for m in second:
        assert all(bool(v) for v in m.full_consensus.values)
        assert all(p == pytest.approx(0.044, abs=1e-6) for p in m.marginal_prices.values)


def test_late_signal_is_absorbed_in_market_lead_without_a_reply(nodes, clock):
    """A signal the city sends at the deadline reaches the campus after it entered MarketLead: the campus rebalances to
    honour it, sends nothing, and records that consensus no longer holds for the changed intervals."""
    campus, city = nodes
    m_campus, m_city = market_of(campus), market_of(city)
    times = m_campus.transition_times
    assert negotiate(nodes, [m_campus, m_city], clock, times) is not None
    # The campus hits the deadline first and moves to MarketLead.
    clock['t'] = times.market_lead
    campus.step()
    assert m_campus.market_state is MarketState.MarketLead
    sent_before = campus.neighbors[0].last_sent_signals[m_campus].sequence
    # The city, still in its last negotiation tick, learns its generator got dearer and sends a revised signal.
    city.local_assets[0].default_vertices.vertices[0].price = 0.06
    city.local_assets[0].default_vertices.vertices[1].price = 0.10
    m_city.on_local_change(city.local_assets[0])
    clock['t'] = times.market_lead + timedelta(seconds=1)
    city.step()
    assert m_city.market_state is MarketState.MarketLead
    assert campus.neighbors[0].last_received_signals[m_campus].revised
    assert not m_campus.in_consensus, 'an unprocessed signal means no consensus yet'
    # The campus absorbs it on its next tick: new price, and because the exchange quantity is unchanged (the campus load
    # is inelastic) its answer is an acknowledgement only: the same content, revised=False, which the city need not answer.
    campus.step()
    for ti in m_campus.time_intervals:
        assert m_campus.marginal_prices.get_by_interval(ti).value == pytest.approx(0.064, abs=1e-6)
    acknowledgement = campus.neighbors[0].last_sent_signals[m_campus]
    assert acknowledgement.sequence == sent_before + 1 and acknowledgement.revised is False
    assert m_campus.in_consensus and not campus.neighbors[0].is_signal_due(m_campus)
    # The city, in MarketLead, receives the acknowledgement, records consensus, and sends nothing back.
    city_sent_before = city.neighbors[0].last_sent_signals[m_city].sequence
    clock['t'] = times.market_lead + timedelta(seconds=2)
    city.step()
    assert m_city.in_consensus
    assert city.neighbors[0].last_sent_signals[m_city].sequence == city_sent_before
    # A signal arriving in DeliveryLead is stored but changes nothing.
    for t in (times.delivery_lead,):
        step_both(nodes, clock, t)
    assert m_campus.market_state is MarketState.DeliveryLead
    price_before = m_campus.marginal_prices.get_by_interval(m_campus.time_intervals[0]).value
    city.local_assets[0].default_vertices.vertices[0].price = 0.07
    m_city.on_local_change(city.local_assets[0])
    clock['t'] = times.delivery_lead + timedelta(seconds=1)
    city.step()
    campus.step()
    assert m_campus.marginal_prices.get_by_interval(m_campus.time_intervals[0]).value == price_before


def test_signal_for_a_market_not_yet_spawned_is_held_until_it_spawns(clock):
    """A peer whose clock (or start-up) runs ahead quotes a market this node has not created yet: the signal is held
    and delivered when the series spawns that market, instead of being dropped (audit T-42)."""
    city = build_node('city', 'campus',
                      asset_curve=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]], asset_limits=(0.0, 1000.0),
                      neighbor_curve=[[INF, 0.0, -50.0, True]], neighbor_limits=(-1000.0, 0.0),
                      up_or_down='downstream')
    clock['t'] = T0 + timedelta(hours=1)  # the campus starts an hour of simulated time later
    campus = build_node('campus', 'city',
                        asset_curve=[[INF, 0.0, -100.0, True]], asset_limits=(-100.0, 0.0),
                        neighbor_curve=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]],
                        neighbor_limits=(0.0, 1000.0), up_or_down='upstream')
    campus.connect_peer(city)
    city.connect_peer(campus)
    ahead = market_of(campus)
    assert city.get_market_by_name(ahead.name) is None, 'the city has not spawned the market the campus is in'
    city_neighbor = city.neighbors[0]  # the city's view of the campus

    times = ahead.transition_times
    for t in (times.active, times.negotiation, times.negotiation + timedelta(seconds=1)):
        clock['t'] = t
        campus.step()  # only the campus runs: it sends its first signal for `ahead`
    assert ahead.name in city_neighbor.held_signals, 'held, not dropped'
    assert not any(m.name == ahead.name for m in city_neighbor.last_received_signals)

    city.market_series[0].spawn()
    spawned = city.get_market_by_name(ahead.name)
    assert spawned is not None
    assert city_neighbor.held_signals == {}
    received = city_neighbor.last_received_signals.get(spawned)
    assert received is not None and received.market is spawned and received.sequence == 1


def test_signal_for_a_market_that_will_never_spawn_is_not_held(nodes, clock):
    campus, city = nodes
    city_neighbor = city.neighbors[0]

    def message(market_name):  # complete and valid, so that only the market name decides what happens to it
        return {'market': market_name, 'neighbor': 'campus', 'protocol_version': 1.0,
                'signal_time': clock['t'].isoformat(), 'sequence': 1, 'in_reply_to': None, 'revised': True,
                'unit': 'kW', 'intervals': {}}

    city_neighbor.receive_transactive_signal(None, None, message('Consensus_2016-01-01T00:00:00+00:00'))
    assert city_neighbor.held_signals == {}, 'a clearing time already passed cannot spawn'
    city_neighbor.receive_transactive_signal(None, None, message('Other_Series_2017-07-02T00:00:00+00:00'))
    assert city_neighbor.held_signals == {}, 'no series of this node names markets like that'
    assert not city_neighbor.last_received_signals, 'dropped, not stored'
