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
        step(tn, clock, times.negotiation)  # the opening pass: balance fails, the signal is sent regardless
    assert not market.converged
    assert any('Failed to balance' in r.getMessage() for r in caplog.records)
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
    assert market.converged, 'with 40 kW of load the neighbor curve can balance'
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


def office_node(tmp_path, data_manager: dict | None):
    """The deployed small office (21 model-frame models, a transactive campus neighbor, two consensus series) with the
    host package's meter point and actuator replaced by shipped classes and a generated weather file."""
    configs = _without_host_keys(json.load(open(deployed_office_config())))  # the agent keeps its own keys
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
