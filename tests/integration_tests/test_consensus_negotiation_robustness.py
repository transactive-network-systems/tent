"""
A consensus node whose local balance cannot close must still negotiate, and must not spin.

The small offices of the consensus container run never sent a signal: their only supply was the campus neighbor's
default curve, the local balance did not close, and the market neither opened the negotiation nor exchanged on later
ticks, while each tick ran the balancing loop to its 20-iteration limit (every iteration identical, every scheduled
value archived again). The campus and city then waited for signals that never came.
"""
import json
import logging
from copy import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tent.enumerations import MarketState
from tent.local_asset import LocalAsset
from tent.market.consensus_market import ConsensusMarket
from tent.market.market_series import MarketSeries
from tent.neighbor import Neighbor
from tent.utils.timer import Timer
from tests.integration_tests.fixtures.sample_weather_data import write_weather_csv
from tests.mock_tn import MockTN
from tests.stubs import clock_at
from tests.unit_tests.test_configuration_models import CONTAINER_CONFIGS, _without_host_keys

T0 = datetime(2017, 7, 1, 0, 0, tzinfo=timezone.utc)
INF = float('inf')
SERIES = dict(name='Consensus', duration_active=300.0, duration_negotiation=600.0, duration_market_lead=300.0,
              duration_delivery_lead=300.0, interval_duration=3600.0, intervals_to_clear=2,
              market_clearing_interval=3600.0, method='Interpolation', duality_gap_threshold=0.01,
              default_price=0.05)


class InelasticAsset(LocalAsset):
    """A load with one inelastic vertex per interval, scheduling a constant power that may differ from its curve."""

    def __init__(self, *args, curve_power: float, scheduled_power: float | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.curve_power = curve_power
        self.constant_power = curve_power if scheduled_power is None else scheduled_power

    def update_vertices(self, market):
        for time_interval in market.time_intervals:
            self.active_vertices.update(time_interval, [Vertex(INF, 0.0, self.curve_power)], archive=False, replace=True)

    def schedule_power(self, market):
        for time_interval in market.time_intervals:
            self.scheduled_powers.update(time_interval, self.constant_power)
        self.schedule_calculated = True


from tent.containers.vertex import Vertex  # noqa: E402  (after the class that uses it only at call time)


@pytest.fixture
def clock():
    state = {'t': T0}
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: state['t']):
        yield state


def build_office(asset: LocalAsset, neighbor_maximum: float):
    tn = MockTN(name='office')
    asset.tn = tn
    tn.local_assets = [asset]
    tn.neighbors = [Neighbor(transactive_node=tn, name='campus', transactive=True, up_or_down='upstream',
                             default_vertices=[[0.045, 0.0, 0.0, True], [0.048, 0.0, neighbor_maximum, True]],
                             minimum_power=0.0, maximum_power=neighbor_maximum, loss_factor=0.0, demand_rate=0.0,
                             convergence_threshold=0.05, cost_parameters=[0.0, 0.0, 0.0])]
    tn.market_series = [MarketSeries(transactive_node=tn, market_class=ConsensusMarket, **SERIES)]
    sent = []
    tn.register_recipient('campus', sent.append)
    return tn, sent


def step(tn, clock, t):
    clock['t'] = t
    tn.step()


def test_a_node_whose_local_balance_cannot_close_still_sends_its_signal_on_tick_one(clock, caplog):
    """A 100 kW inelastic load against a neighbor default curve of at most 50 kW: no price balances, the signal goes."""
    tn, sent = build_office(InelasticAsset(transactive_node=None, name='load', cost_parameters=[0.0, 0.0, 0.0],
                                           curve_power=-100.0), neighbor_maximum=50.0)
    market = tn.market_series[0].newest_market
    neighbor = tn.neighbors[0]
    times = market.transition_times
    step(tn, clock, times.active)
    with caplog.at_level(logging.WARNING, logger='tent.market'):
        step(tn, clock, times.negotiation)  # the opening pass: no crossing, the signal is sent regardless
    assert market.unbalanceable == {ti.name: pytest.approx(-50.0) for ti in market.time_intervals}, '50 kW short'
    assert not market.in_consensus
    assert any('cannot balance' in r.getMessage() and 'deficit of 50 kW' in r.getMessage() for r in caplog.records)
    assert len(sent) == 1 and sent[0]['sequence'] == 1 and sent[0]['revised'] is True
    assert market in neighbor.last_sent_signals
    for interval in sent[0]['intervals'].values():
        assert interval['flexibility'], 'the residual curve is offered'
    # Later ticks with nothing new resend nothing.
    step(tn, clock, times.negotiation + timedelta(seconds=1))
    step(tn, clock, times.negotiation + timedelta(seconds=2))
    assert len(sent) == 1
    # The local curve changes: a revised signal goes out on the next tick.
    tn.local_assets[0].curve_power = -40.0
    tn.local_assets[0].constant_power = -40.0
    market.on_local_change(tn.local_assets[0])
    step(tn, clock, times.negotiation + timedelta(seconds=3))
    assert len(sent) == 2 and sent[1]['sequence'] == 2 and sent[1]['revised'] is True
    assert market.converged and market.unbalanceable == {}, 'with 40 kW of load the neighbor curve can balance'
    quantities = [v['quantity'] for v in list(sent[1]['intervals'].values())[0]['flexibility']]
    assert any(q == pytest.approx(-40.0) for q in quantities), 'the new residual, in the neighbor\'s perspective'


def test_the_balancing_loop_stops_when_an_iteration_leaves_the_prices_where_they_were(clock, caplog):
    """An asset that schedules 70 kW while its curve says 60 kW never balances; the loop notices after one repeat
    instead of running to the iteration limit, and says why."""
    tn, sent = build_office(InelasticAsset(transactive_node=None, name='load', cost_parameters=[0.0, 0.0, 0.0],
                                           curve_power=-60.0, scheduled_power=-70.0), neighbor_maximum=120.0)
    market = tn.market_series[0].newest_market
    times = market.transition_times
    step(tn, clock, times.active)
    with caplog.at_level(logging.DEBUG, logger='tent.market'):
        step(tn, clock, times.negotiation)  # the opening pass, then the first while_in_negotiation pass, in one tick
    assert not market.converged
    messages = [r.getMessage() for r in caplog.records]
    iterations = [m.split('balance iteration ')[1][0] for m in messages if 'balance iteration' in m]
    assert iterations == ['1', '2', '1'], ('the opening pass: iteration 1 moves the prices off the defaults, iteration 2 '
                                           'repeats them and stops; the tick\'s pass: nothing moves in iteration 1')
    assert any('prices did not move in iteration 2' in m for m in messages)
    assert any('is not balanced in 2 of 2 intervals' in m and 'net power -10' in m for m in messages)
    assert len(sent) == 1, 'and the signal still went out'
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger='tent.market'):
        step(tn, clock, times.negotiation + timedelta(seconds=1))
    assert sum('balance iteration' in r.getMessage() for r in caplog.records) == 1
    assert market.balance_iterations == 1, 'the prices had already settled: one iteration shows nothing moves'


# ---------------------------------------------------------------------------- the deployed small office configuration

def deployed_office_config() -> Path | None:
    if not CONTAINER_CONFIGS:
        return None
    containers = Path(CONTAINER_CONFIGS[0]).parents[3]  # .../containers/tent/<node>/agent_configs/tn.*.json
    path = containers / 'tent-consensus' / 'small_office_dr' / 'agent_configs' / 'tn.so.dr.json'
    return path if path.is_file() else None


def office_node(tmp_path, data_manager: dict | None, demand_curve_points: int | None = None):
    """The deployed small office (21 model-frame models, a transactive campus neighbor, two consensus series) with the
    host package's meter point and actuator replaced by shipped classes and a generated weather file; optionally with
    another number of flexibility points than the deployed configuration's."""
    configs = _without_host_keys(json.load(open(deployed_office_config())))  # the agent keeps its own keys
    if demand_curve_points is not None:
        configs['local_assets'][0]['model_configs']['demand_curve_points'] = demand_curve_points
    configs.pop('meter_points')
    configs['neighbors'][0]['meter_points'] = None
    configs['local_assets'][0]['actuation_manager'] = {'class_name': 'NullActuationManager'}
    weather = tmp_path / 'weather.csv'
    write_weather_csv(weather, datetime(2017, 7, 2), hours=96)
    configs['information_service_models'][0].update(weather_file_path=str(weather), location=None)
    if data_manager is None:
        configs.pop('data_manager')
    else:
        configs['data_manager'] = data_manager
    tn = MockTN()
    tn.configure_node(configs)
    return tn


@pytest.mark.skipif(deployed_office_config() is None, reason='the deployed consensus container configurations are not '
                                                             'beside this repository')
def test_the_deployed_small_office_opens_its_negotiation_in_one_pass(tmp_path, caplog):
    # The clock runs (clock_at), as in the containers: a constant clock would give every version of a value archived
    # within one tick the same record_time, which the archive refuses.
    clock_at(datetime(2017, 7, 3, 0, 0, tzinfo=timezone.utc))
    tn = office_node(tmp_path, {'class_name': 'LocalDataManager',
                                'db_params': {'url': f'sqlite:///{tmp_path / "office.sqlite"}'}})
    sent = []
    tn.register_recipient('PNNL_Campus', sent.append)
    market = next(s for s in tn.market_series if s.name == 'Real-Time_Auction').newest_market
    times = market.transition_times

    def rows():
        return sum(len(tn.data_manager.query_data(t)) for t in ('measurement_value', 'sd_curve', 'vertex'))

    # The series has no Active window, so the step at the active time also opens the negotiation (the opening pass).
    before = rows()
    with caplog.at_level(logging.DEBUG, logger='tent.market.consensus_market'):
        clock_at(times.active)
        tn.step()
        clock_at(times.negotiation)
        tn.step()
    assert len(sent) == 1 and sent[0]['market'] == market.name, 'the opening signal went to the campus on tick one'
    assert len(sent[0]['intervals']) == 24
    iterations = [r for r in caplog.records if 'balance iteration' in r.getMessage()]
    assert 1 <= len(iterations) <= 4, 'two passes, not the 20-iteration limit on identical iterations'
    written = rows() - before
    assert 0 < written < 3000, f'{written} rows for the opening pass and one more: each container is written once'
    before = rows()
    clock_at(times.negotiation + timedelta(seconds=1))
    tn.step()
    assert len(sent) == 1, 'nothing new to say: no second signal'
    assert rows() - before < 1500


@pytest.mark.skipif(deployed_office_config() is None, reason='the deployed consensus container configurations are not '
                                                             'beside this repository')
@pytest.mark.parametrize('points', [2, 5])
def test_the_deployed_small_office_closes_its_local_balance(tmp_path, caplog, points):
    """With the model-frame asset's curve, schedule and set points coming from one price-to-set-point mapping, the
    office's local balance closes: before, the model scheduled 16.12 kW where its curve said 13.38 kW at the cleared
    price (17 percent, above the 15 percent threshold) in 4 of 24 intervals, and the market never converged."""
    clock_at(datetime(2017, 7, 3, 0, 0, tzinfo=timezone.utc))
    tn = office_node(tmp_path, None, demand_curve_points=points)
    tn.register_recipient('PNNL_Campus', lambda message: None)
    market = next(s for s in tn.market_series if s.name == 'Real-Time_Auction').newest_market
    times = market.transition_times
    with caplog.at_level(logging.WARNING, logger='tent.market'):
        clock_at(times.active)
        tn.step()
        clock_at(times.negotiation)
        tn.step()
    assert market.converged, [r.getMessage() for r in caplog.records if 'not balanced' in r.getMessage()]
    assert not any('is not balanced' in r.getMessage() for r in caplog.records)
    assert abs(market.duality_gap) <= market.series.duality_gap_threshold
    worst = max(abs(market.net_powers.get_by_interval(ti).value) for ti in market.time_intervals)
    assert worst < 1e-6, f'net power {worst} kW: the asset schedules what its curve says at the cleared price'


class GenerationPredictor(InelasticAsset):
    """A load predictor that extrapolates generation (a positive power) in the first interval, as the campus's open-loop
    predictor did in hot hours, with limits that forbid it. Its curve is built from the clamped schedule."""

    def schedule_power(self, market):
        for n, time_interval in enumerate(market.time_intervals):
            self.scheduled_powers.update(time_interval, 501.4 if n == 0 else self.constant_power)
        self.schedule_calculated = True

    def update_vertices(self, market):
        for time_interval in market.time_intervals:
            power = self.scheduled_powers.get_by_interval(time_interval).value
            self.active_vertices.update(time_interval, [Vertex(INF, 0.0, power)], archive=False, replace=True)


def test_a_rescheduled_power_is_held_within_the_limits_the_curve_was_built_from(clock, caplog):
    """The campus case (T-60): the predictor's clamped 0 kW went into the curve, but the reschedule after balancing
    took the unclamped 501.4 kW and the market reported itself 501.4 kW out of balance in that interval."""
    tn, sent = build_office(GenerationPredictor(transactive_node=None, name='buildings', cost_parameters=[0.0, 0.0, 0.0],
                                                curve_power=-60.0, minimum_power=-24200.0, maximum_power=0.0),
                            neighbor_maximum=120.0)
    market = tn.market_series[0].newest_market
    times = market.transition_times
    step(tn, clock, times.active)
    with caplog.at_level(logging.WARNING, logger='tent.market'):
        step(tn, clock, times.negotiation)
    first, second = market.time_intervals
    assert tn.local_assets[0].scheduled_powers.get_by_interval(first).value == 0.0, 'held at the limit after the reschedule'
    assert tn.local_assets[0].scheduled_powers.get_by_interval(second).value == -60.0
    assert market.net_powers.get_by_interval(first).value == pytest.approx(0.0, abs=1e-9)
    assert market.converged and market.unbalanceable == {}
    assert not any('is not balanced' in r.getMessage() for r in caplog.records)


def test_a_surplus_the_neighbor_cannot_absorb_is_out_of_consensus_without_spinning(clock, caplog):
    """A node whose own generation exceeds its load, against a neighbor that only supplies (the campus against the
    city's bulk-supplier curve): no price balances. The market schedules at the curve end nearest zero (the neighbor
    at 0 kW, its lowest price), records the surplus, says so once per interval, stays out of consensus and does not
    rebalance every tick. A neighbor that may take power (a negative minimum_power and a curve reaching into negative
    quantities) balances it."""
    tn, sent = build_office(InelasticAsset(transactive_node=None, name='solar', cost_parameters=[0.0, 0.0, 0.0],
                                           curve_power=100.0), neighbor_maximum=120.0)
    market = tn.market_series[0].newest_market
    times = market.transition_times
    step(tn, clock, times.active)
    with caplog.at_level(logging.WARNING, logger='tent.market'):
        step(tn, clock, times.negotiation)
        for seconds in range(1, 5):
            step(tn, clock, times.negotiation + timedelta(seconds=seconds))
    assert market.unbalanceable == {ti.name: pytest.approx(100.0) for ti in market.time_intervals}
    assert market.converged and not market.in_consensus
    assert market.negotiation_passes == 1, 'the opening pass only: an unbalanceable interval does not spin'
    cannot = [r.getMessage() for r in caplog.records if 'cannot balance' in r.getMessage()]
    assert len(cannot) == 2 and all('surplus of 100 kW' in m and 'absorb it' in m for m in cannot)
    for ti in market.time_intervals:
        assert market.marginal_prices.get_by_interval(ti).value == pytest.approx(0.045), "the neighbor curve's lowest price"
        assert tn.neighbors[0].scheduled_powers.get_by_interval(ti).value == pytest.approx(0.0)
        assert bool(market.full_consensus.get_by_interval(ti).value) is False
    assert len(sent) == 1, 'the residual (all supply) still went to the neighbor'
    with caplog.at_level(logging.WARNING, logger='tent.market'):
        step(tn, clock, times.market_lead)
    assert any('cannot be balanced with the available counterparties' in r.getMessage()
               and 'largest: 100 kW' in r.getMessage() for r in caplog.records)

    # The remedy is configuration: a neighbor that can take the export.
    tn2, _ = build_office(InelasticAsset(transactive_node=None, name='solar', cost_parameters=[0.0, 0.0, 0.0],
                                         curve_power=100.0), neighbor_maximum=120.0)
    taker = tn2.neighbors[0]
    taker.minimum_power = -120.0
    taker.default_vertices.update([Vertex(0.045, 0.0, -120.0)], archive=False)  # it buys up to 120 kW at 0.045 too
    market2 = tn2.market_series[0].newest_market
    step(tn2, clock, market2.transition_times.active)
    step(tn2, clock, market2.transition_times.negotiation)
    assert market2.unbalanceable == {} and market2.converged
    for ti in market2.time_intervals:
        assert taker.scheduled_powers.get_by_interval(ti).value == pytest.approx(-100.0)


def deployed_node_config(node: str, file_name: str, tmp_path):
    """A deployed consensus container configuration made runnable here: host keys stripped, meter points and the data
    manager removed, the host actuator replaced, the repository's weather file."""
    import glob
    containers = Path(CONTAINER_CONFIGS[0]).parents[3] / 'tent-consensus'
    configs = _without_host_keys(json.load(open(containers / node / 'agent_configs' / file_name)))
    configs.pop('meter_points', None)
    configs.pop('data_manager', None)
    for neighbor in configs['neighbors']:
        neighbor['meter_points'] = None
    weather = sorted(glob.glob(str(Path(__file__).resolve().parents[2] / 'sample_configs' / '**' / '*.csv'),
                               recursive=True))[0]
    for service in configs.get('information_service_models', []):
        service.update(weather_file_path=weather, location=None)
    for asset in configs['local_assets']:
        if 'actuation_manager' in asset:
            asset['actuation_manager'] = {'class_name': 'NullActuationManager'}
    return configs


@pytest.mark.skipif(deployed_office_config() is None, reason='the deployed consensus container configurations are not '
                                                             'beside this repository')
def test_the_deployed_hierarchy_reaches_full_consensus_in_a_few_exchanges(tmp_path):
    """Campus, city and the two offices from the deployed consensus configurations, wired as the message bus wires them,
    reach full consensus on the real-time market within a few ticks, with every link settling after a handful of signals
    (no acknowledgement chain). Run 22 never reached consensus: the campus's balance point carried a price read off a
    vertical residual curve, 25 percent from the city's, and both nodes answered each other's acknowledgements without
    end (the city sent its day-ahead signal 164 times in ten minutes)."""
    clock_at(datetime(2017, 7, 3, 0, 0, tzinfo=timezone.utc))
    nodes = {}
    for node, file_name in (('campus', 'tn.campus.json'), ('city', 'tn.city.json'),
                            ('small_office_dr', 'tn.so.dr.json'), ('small_office_ilc', 'tn.so.ilc.json')):
        tn = MockTN()
        tn.configure_node(deployed_node_config(node, file_name, tmp_path))
        nodes[tn.name] = tn
    campus = nodes['PNNL_Campus']
    for name, tn in nodes.items():
        if tn is not campus:
            campus.connect_peer(tn)
            tn.connect_peer(campus)
    markets = {name: next(s for s in tn.market_series if s.name == 'Real-Time_Auction').newest_market
               for name, tn in nodes.items()}
    assert len({m.name for m in markets.values()}) == 1, 'one market name on every node'
    times = markets['PNNL_Campus'].transition_times
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
        pytest.fail({name: (m.in_consensus, m.converged, m.unbalanceable) for name, m in markets.items()})
    assert tick <= 6, f'full consensus took {tick} ticks'
    sequences = {f'{tn.name}->{n.name}': n.last_sent_signals[markets[tn.name]].sequence
                 for tn in nodes.values() for n in tn.neighbors if n.transactive and markets[tn.name] in n.last_sent_signals}
    assert all(sequence <= 5 for sequence in sequences.values()), sequences
    # Every link agrees at the balance point, price included, with the flags set on both sides.
    for tn in nodes.values():
        market = markets[tn.name]
        for neighbor in tn.neighbors:
            if not neighbor.transactive:
                continue
            assert neighbor.is_converged(market), f'{tn.name} with {neighbor.name}'
            sent, received = neighbor.last_sent_signals[market], neighbor.last_received_signals[market]
            for ti in market.time_intervals:
                assert sent.balance_point(ti).quantity == pytest.approx(-received.balance_point(ti).quantity, abs=0.5)
                assert sent.balance_point(ti).price == pytest.approx(received.balance_point(ti).price, rel=1e-3)
    # Nothing more is sent once consensus holds.
    for tick in range(13, 18):
        clock_at(times.negotiation + timedelta(seconds=tick))
        for tn in nodes.values():
            tn.step()
    assert {f'{tn.name}->{n.name}': n.last_sent_signals[markets[tn.name]].sequence
            for tn in nodes.values() for n in tn.neighbors if n.transactive and markets[tn.name] in n.last_sent_signals} == sequences
    assert all(m.in_consensus for m in markets.values())


@pytest.mark.skipif(deployed_office_config() is None, reason='the deployed consensus container configurations are not '
                                                             'beside this repository')
def test_the_deployed_hierarchy_converges_with_signals_arriving_a_tick_late(tmp_path):
    """As the message bus delivers them: every signal reaches its peer at the peer's next step but one, the nodes step
    in a different order each tick, and the day-ahead market negotiates alongside the real-time one. Before the change
    rule compared curves as functions, the campus revised its signal to every neighbor on every tick and needed seven
    ticks and seventeen signals per real-time market here (and never settled in the containers' six); now four ticks
    and eleven signals, the leaves three each."""
    import random
    clock_at(datetime(2017, 7, 3, 0, 40, tzinfo=timezone.utc))
    nodes = {}
    for node, file_name in (('campus', 'tn.campus.json'), ('city', 'tn.city.json'),
                            ('small_office_dr', 'tn.so.dr.json'), ('small_office_ilc', 'tn.so.ilc.json')):
        tn = MockTN()
        tn.configure_node(deployed_node_config(node, file_name, tmp_path))
        nodes[tn.name] = tn
    campus = nodes['PNNL_Campus']
    queue, tick, sent = [], [0], {name: 0 for name in nodes}

    def wire(sender, receiver):
        mine = next(n for n in sender.neighbors if n.name == receiver.name)
        theirs = next(n for n in receiver.neighbors if n.name == sender.name)

        def issue(message, _theirs=theirs, _receiver=receiver.name, _sender=sender.name):
            sent[_sender] += 1
            queue.append((tick[0] + 1, _receiver, _theirs, message))
        sender.register_recipient(mine.name, issue)

    for tn in nodes.values():
        if tn is not campus:
            wire(campus, tn)
            wire(tn, campus)
    rng = random.Random(7)

    def step_all(now):
        tick[0] += 1
        clock_at(now)
        order = list(nodes.values())
        rng.shuffle(order)
        for tn in order:
            due = [q for q in queue if q[1] == tn.name and q[0] <= tick[0]]
            for q in due:
                queue.remove(q)
                q[2].receive_transactive_signal(None, None, q[3])
            tn.step()

    def real_time_markets():
        return {name: next(m for s in tn.market_series if s.name == 'Real-Time_Auction' for m in s
                           if m.market_state is MarketState.Negotiation)
                for name, tn in nodes.items()}

    # Run until every node has a real-time market in Negotiation (the day-ahead one is negotiating too by then).
    now = datetime(2017, 7, 3, 0, 40, tzinfo=timezone.utc)
    while True:
        now += timedelta(seconds=3)
        step_all(now)
        try:
            markets = real_time_markets()
        except StopIteration:
            continue
        if len({m.name for m in markets.values()}) == 1:
            break
    day_ahead = {name: next(m for s in tn.market_series if s.name == 'Day-Ahead_Auction' for m in s) for name, tn in nodes.items()}
    assert all(m.market_state is MarketState.Negotiation for m in day_ahead.values()), 'the day-ahead negotiates alongside'
    market_name = next(iter(markets.values())).name
    first_tick, sent_before = tick[0], dict(sent)
    for _ in range(12):
        now += timedelta(seconds=3)
        step_all(now)
        if all(m.in_consensus for m in markets.values()):
            break
    else:
        pytest.fail({name: (m.in_consensus, m.converged) for name, m in markets.items()})
    ticks = tick[0] - first_tick
    assert ticks <= 6, f'{market_name}: full consensus took {ticks} ticks with one-tick-late delivery'
    campus_signals = sum(n.last_sent_signals[markets['PNNL_Campus']].sequence for n in campus.neighbors if n.transactive)
    assert campus_signals <= 15, f'the campus sent {campus_signals} signals for one market'
    for name, tn in nodes.items():
        if tn is not campus:
            assert tn.neighbors[0].last_sent_signals[markets[name]].sequence <= 5
