"""
MatchingAuction: the Auction's signal exchange between nodes with an order book as the clearing step. One class serves
two deployments, differing only in configuration: the book at the top node alone ("exchange", design A) or on every
node ("local books", design B).
"""
import logging
from copy import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tent.containers.vertex import Vertex
from tent.enumerations import MarketState, MarketTypes
from tent.local_asset import LocalAsset
from tent.market import Market
from tent.market.auction import Auction
from tent.market.matching_auction import MatchingAuction
from tent.market.market_series import MarketSeries
from tent.neighbor import Neighbor
from tent.registry import resolve
from tent.utils.timer import Timer
from tests.integration_tests.test_consensus_negotiation_robustness import deployed_node_config, deployed_office_config
from tests.mock_tn import MockTN
from tests.stubs import clock_at

T0 = datetime(2017, 7, 1, 0, 0, tzinfo=timezone.utc)
INF = float('inf')


class RecordingDataManager:
    def __init__(self):
        self.records = []

    def archive_data(self, records):
        self.records.extend(records if isinstance(records, (list, tuple)) else [records])

    def by_table(self, name):
        return [r['data'] for r in self.records if r.get('table_name') == name]


class Generator(LocalAsset):
    """A local supplier whose flexibility is its default_vertices curve and that schedules at the market price."""

    def update_vertices(self, market):
        for time_interval in market.time_intervals:
            self.active_vertices.update(time_interval, [copy(v) for v in self.default_vertices.vertices],
                                        archive=False, replace=True)

    def schedule_power(self, market):
        for time_interval in market.time_intervals:
            if market.cleared_power(self, time_interval) is not None:
                power = self.market_power(market, time_interval)  # the book's settlement
            else:
                mp = market.marginal_prices.get_by_interval(time_interval)
                price = market.series.default_price if mp is None or mp.value is None else mp.value
                power = self.default_vertices.power_from_vertices(price)
            self.scheduled_powers.update(time_interval, power)
        self.schedule_calculated = True


SERIES = dict(name='Real-Time Auction', duration_active=300.0, duration_negotiation=600.0, duration_market_lead=300.0,
              duration_delivery_lead=300.0, interval_duration=3600.0, intervals_to_clear=2,
              market_clearing_interval=3600.0, default_price=0.05)


@pytest.fixture
def clock():
    state = {'t': T0}
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: state['t']):
        yield state


def build_city():
    """The top node: a generator offering 0 kW at 0.04 up to 100 kW at 0.08, a bulk supplier that offers nothing (so
    that there is the one upstream neighbor an auction needs), and the campus downstream. Its market is the book."""
    tn = MockTN(name='CoR')
    tn._data_manager = RecordingDataManager()
    tn.local_assets = [Generator(transactive_node=tn, name='plant', cost_parameters=[0.0, 0.0, 0.0],
                                 default_vertices=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 100.0, True]],
                                 minimum_power=0.0, maximum_power=100.0)]
    tn.neighbors = [Neighbor(transactive_node=tn, name='PNNL_Campus', transactive=True, up_or_down='downstream',
                             default_vertices=[[INF, 0.0, -10.0, True]], minimum_power=-1000.0, maximum_power=0.0,
                             loss_factor=0.0, demand_rate=0.0, cost_parameters=[0.0, 0.0, 0.0]),
                    Neighbor(transactive_node=tn, name='BPA', transactive=False, up_or_down='upstream',
                             default_vertices=[[INF, 0.0, 0.0, True]], minimum_power=0.0, maximum_power=0.0,
                             loss_factor=0.0, demand_rate=0.0, cost_parameters=[0.0, 0.0, 0.0])]
    tn.market_series = [MarketSeries(transactive_node=tn, market_class=MatchingAuction, matching_strategy=['ORDER_AGE'],
                                     **SERIES)]
    return tn


def build_campus():
    """A downstream node running a plain Auction: an inelastic 40 kW load, the city upstream."""
    tn = MockTN(name='PNNL_Campus')
    tn._data_manager = RecordingDataManager()
    tn.local_assets = [LocalAsset(transactive_node=tn, name='buildings', cost_parameters=[0.0, 0.0, 0.0],
                                  default_power=-40.0)]
    tn.neighbors = [Neighbor(transactive_node=tn, name='CoR', transactive=True, up_or_down='upstream',
                             default_vertices=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]],
                             minimum_power=0.0, maximum_power=1000.0, loss_factor=0.0, demand_rate=0.0,
                             cost_parameters=[0.0, 0.0, 0.0])]
    tn.market_series = [MarketSeries(transactive_node=tn, market_class=Auction, **SERIES)]
    return tn


def market_of(tn):
    return tn.market_series[0].newest_market


def test_the_class_is_registered_and_shares_the_book():
    assert resolve('MatchingAuction', Market) is MatchingAuction
    assert issubclass(MatchingAuction, Auction)
    assert MatchingAuction.MARKET_TYPE is MarketTypes.matching_auction
    assert set(MatchingAuction.Config.model_fields) == {'actuate_assets', 'matching_strategy', 'order_expiry'}
    tables = {d['name']: d for d in MatchingAuction.get_archive_definitions()}
    assert tables['matching_auction']['inherits'] == 'auction'
    assert [c['name'] for c in tables['matching_auction']['columns']] == ['id', 'matching_strategy']
    assert 'order' in tables, 'the order book rows'


def test_a_city_book_clears_a_campus_auction_bid(clock):
    city, campus = build_city(), build_campus()
    city.connect_peer(campus)
    campus.connect_peer(city)
    m_city, m_campus = market_of(city), market_of(campus)
    assert m_city.name == m_campus.name
    assert m_city.BALANCE_POINT == 'predicted_price', 'before the book settles the bid rule is the auction\'s'
    times = m_city.transition_times
    for t in (times.active, times.negotiation, times.negotiation + timedelta(seconds=1)):
        clock['t'] = t
        campus.step()
        city.step()
    # Bids rise: the campus sends its residual (the inelastic 40 kW) to the city in MarketLead.
    clock['t'] = times.market_lead
    campus.step()
    city.step()
    assert m_campus.market_state is MarketState.MarketLead and m_city.market_state is MarketState.MarketLead
    assert city.neighbors[0].last_received_signals[m_city] is not None, 'the city has the campus\'s bid'
    # The city clears its book in DeliveryLead and the result flows down.
    clock['t'] = times.delivery_lead
    city.step()
    campus.step()
    assert m_city.market_state is MarketState.DeliveryLead and m_campus.market_state is MarketState.DeliveryLead
    assert m_city._settled and m_city.BALANCE_POINT == 'matched_quantity'
    for ti in m_city.time_intervals:
        legs = [l for l in m_city.matchList if l.interval.name == ti.name]
        assert sum(abs(l.vertices[-1].quantity) for l in legs) / 2 == pytest.approx(40.0), 'one 40 kW trade'
        # The campus's market order rested in the book and the plant's offer hit it at the first rung with enough
        # supply, 0.08: the book's price.
        assert m_city.marginal_prices.get_by_interval(ti).value == pytest.approx(0.08)
        assert m_city.cleared_power(city.neighbors[0], ti) == pytest.approx(-40.0)
        assert city.local_assets[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(40.0)
        assert city.neighbors[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(-40.0)
    # The downstream signal carries the matched quantity at the city's residual curve's own price for it, 0.056 (the
    # plant's offer at 40 kW): the point lies on the curve the campus receives, so the campus clears onto the matched
    # quantity. The book's fill price, 0.08, is the resting order's rung (the campus's market order took any price), a
    # pay-as-bid price that is the city's marginal price but not a point of its residual curve.
    sent = city.neighbors[0].last_sent_signals[m_city]
    for ti in m_city.time_intervals:
        point = sent.balance_point(ti)
        assert point.quantity == pytest.approx(40.0) and point.price == pytest.approx(0.056)
    for ti in m_campus.time_intervals:
        assert campus.neighbors[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(40.0), 'the campus imports the matched quantity'
        assert m_campus.marginal_prices.get_by_interval(ti).value == pytest.approx(0.056, abs=1e-9), 'at the curve\'s price for it'
    # The engine node's archive holds every order and leg of the book.
    archived = {row['id'] for row in city.data_manager.by_table('order')}
    assert all(leg.id in archived for leg in m_city.matchList) and all(o.id in archived for o in m_city.orderList)
    rows = [r for r in city.data_manager.by_table('matching_auction') if r['id'] == m_city.id]
    assert len(rows) == 1 and rows[0]['matching_strategy'] == ['ORDER_AGE'] and rows[0]['actuate_assets'] is True
    assert not campus.data_manager.by_table('order'), 'a plain auction node books nothing'


# ------------------------------------------------------------------------------- the deployed hierarchy, A and B

def sent_power(tn, neighbor_name, market):
    neighbor = next(n for n in tn.neighbors if n.name == neighbor_name)
    return neighbor, [neighbor.scheduled_powers.get_by_interval(ti).value for ti in market.time_intervals]


def arriving(sent: float, neighbor) -> float:
    """What arrives at a neighbor's node of the power this node sends it, after the link's losses (the receiving
    neighbor model's include_marginal_losses, applied with the receiver's loss factor and link capacity)."""
    return sent * (1 - neighbor.loss_factor * sent / neighbor.maximum_power) if neighbor.loss_factor else sent


def run_hierarchy(tmp_path, engines: set[str]):
    clock_at(datetime(2017, 7, 3, 0, 0, tzinfo=timezone.utc))
    nodes = {}
    for node, file_name in (('small_office_dr', 'tn.so.dr.json'), ('small_office_ilc', 'tn.so.ilc.json'),
                            ('campus', 'tn.campus.json'), ('city', 'tn.city.json')):
        name = {'small_office_dr': 'SMALL_OFFICE_DR', 'small_office_ilc': 'SMALL_OFFICE_ILC', 'campus': 'PNNL_Campus',
                'city': 'CoR'}[node]
        tn = MockTN()
        tn.configure_node(deployed_node_config(node, file_name, tmp_path, variant='tent-auction',
                                               market_class_name='MatchingAuction' if name in engines else None))
        nodes[tn.name] = tn
    campus, city = nodes['PNNL_Campus'], nodes['CoR']
    offices = [nodes['SMALL_OFFICE_DR'], nodes['SMALL_OFFICE_ILC']]
    for tn in nodes.values():
        if tn is not campus:
            campus.connect_peer(tn)
            tn.connect_peer(campus)
    markets = {name: next(s for s in tn.market_series if s.name == 'Real-Time_Auction').newest_market
               for name, tn in nodes.items()}
    times = markets['PNNL_Campus'].transition_times
    upward, downward = [*offices, campus, city], [city, campus, *offices]
    for when, order in ((times.active, upward), (times.negotiation, upward), (times.market_lead, upward),
                        (times.delivery_lead, downward), (times.delivery, downward)):
        clock_at(when)
        for tn in order:
            tn.step()
    assert all(m.market_state is MarketState.Delivery for m in markets.values())
    return nodes, markets


def check_exchanges(nodes, markets):
    """Both ends of every link agree on the exchange once the downstream node has cleared: what the upstream node sends
    arrives, less the link's losses, as what the downstream node imports, in every interval."""
    campus, city = nodes['PNNL_Campus'], nodes['CoR']
    link_campus_at_city, city_sends = sent_power(city, 'PNNL_Campus', markets['CoR'])
    link_city_at_campus, campus_imports = sent_power(campus, 'CoR', markets['PNNL_Campus'])
    for sent, imported in zip(city_sends, campus_imports):
        assert sent < 0 and imported > 0
        assert imported == pytest.approx(arriving(-sent, link_city_at_campus), rel=1e-6), (sent, imported)
    for office in (nodes['SMALL_OFFICE_DR'], nodes['SMALL_OFFICE_ILC']):
        link_office_at_campus, campus_sends = sent_power(campus, office.name, markets['PNNL_Campus'])
        link_campus_at_office, office_imports = sent_power(office, 'PNNL_Campus', markets[office.name])
        for sent, imported in zip(campus_sends, office_imports):
            assert sent < 0 and imported > 0
            assert imported == pytest.approx(arriving(-sent, link_campus_at_office), rel=1e-6), (sent, imported)
    return city_sends, campus_imports


@pytest.mark.skipif(deployed_office_config() is None, reason='the deployed container configurations are not beside '
                                                             'this repository')
def test_design_a_one_book_at_the_city_clears_the_deployed_hierarchy(tmp_path):
    nodes, markets = run_hierarchy(tmp_path, engines={'CoR'})
    m_city, m_campus = markets['CoR'], markets['PNNL_Campus']
    assert isinstance(m_city, MatchingAuction) and type(m_campus) is Auction
    assert m_city.matchList and not m_city.unbalanceable
    city_sends, campus_imports = check_exchanges(nodes, markets)
    assert city_sends[0] == pytest.approx(-6489.48, abs=0.01) and campus_imports[0] == pytest.approx(6426.31, abs=0.01)
    # The book's fill prices are those of the orders taken: the campus's bid rested at its own prices (its offices'
    # flexibility span seen through the campus), so the city's last fill, its marginal price, is a rung of that bid,
    # 0.04025 in the first interval, while the campus clears onto the matched point at the city's residual curve's own
    # price for it, 0.03292. Both are recorded; the quantities agree.
    assert m_city.marginal_prices.get_by_interval(m_city.time_intervals[0]).value == pytest.approx(0.04025, abs=1e-5)
    assert m_campus.marginal_prices.get_by_interval(m_campus.time_intervals[0]).value == pytest.approx(0.03292, abs=1e-5)


@pytest.mark.skipif(deployed_office_config() is None, reason='the deployed container configurations are not beside '
                                                             'this repository')
def test_design_b_a_book_on_the_city_and_the_campus_clears_the_deployed_hierarchy(tmp_path):
    nodes, markets = run_hierarchy(tmp_path, engines={'CoR', 'PNNL_Campus'})
    m_city, m_campus = markets['CoR'], markets['PNNL_Campus']
    assert isinstance(m_city, MatchingAuction) and isinstance(m_campus, MatchingAuction)
    assert m_city.matchList and m_campus.matchList and not m_campus.unbalanceable
    office_legs = [l for l in m_campus.matchList if l.actor is not None and l.actor.name.startswith('SMALL_OFFICE')]
    assert office_legs, 'the campus\'s book matched the offices\' curves against the city\'s'
    # The limitation the design states: each book clears independently against stated curves, so the city's match with
    # the campus and the campus's match with the city need not agree until the campus has cleared against the city's
    # price. The matched point on the downstream signal brings them together: after the campus clears, both ends of
    # every link agree, to the link's losses.
    city_sends, campus_imports = check_exchanges(nodes, markets)
    assert city_sends[0] == pytest.approx(-6489.48, abs=0.01) and campus_imports[0] == pytest.approx(6426.31, abs=0.01)
    # Each book's own last fill sets its marginal price: the city's at the campus bid's rung, the campus's at the city
    # remainder's rung its offices were filled at.
    assert m_city.marginal_prices.get_by_interval(m_city.time_intervals[0]).value == pytest.approx(0.04025, abs=1e-5)
    assert m_campus.marginal_prices.get_by_interval(m_campus.time_intervals[0]).value == pytest.approx(0.03444, abs=1e-5)
