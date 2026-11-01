"""
An auction node bids to its upstream supplier in MarketLead. Regression: when the node's residual curve showed a surplus
(the load predictor's regression gave generation in hot hours), bounding it to the supplier's limits [-maximum_power, 0]
left an empty curve, and marking the balance point on it raised IndexError on every state-machine iteration.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tent.containers.transactive_signal import TransactiveSignal
from tent.containers.vertex import Vertex
from tent.enumerations import MarketState, SignalDirection
from tent.local_asset import LocalAsset
from tent.market.auction import Auction
from tent.market.market_series import MarketSeries
from tent.neighbor import Neighbor
from tent.utils.timer import Timer
from tests.mock_tn import MockTN

T0 = datetime(2017, 7, 3, 0, 0, tzinfo=timezone.utc)
INF = float('inf')


class PredictedLoad(LocalAsset):
    """The open-loop predictor's shape: one inelastic vertex per interval at the predicted power. In the first interval
    the regression predicts generation (a positive power), as the campus table does above 90 degF."""

    def schedule_power(self, market):
        for n, time_interval in enumerate(market.time_intervals):
            self.scheduled_powers.update(time_interval, 3000.0 if n == 0 else -3000.0)
        self.schedule_calculated = True


@pytest.fixture
def clock():
    state = {'t': T0}
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: state['t']):
        yield state


@pytest.fixture
def campus(clock):
    tn = MockTN(name='PNNL_Campus')
    tn.local_assets = [PredictedLoad(transactive_node=tn, name='InelasticBuildings', cost_parameters=[0.0, 0.0, 0.0],
                                     maximum_power=0.0, minimum_power=-16400.0)]
    supplier = Neighbor(transactive_node=tn, name='CoR', transactive=True, up_or_down='upstream',
                        default_vertices=[[0.046, 160.0, 0.0, True], [0.048, 1100.0, 20000.0, True]],
                        minimum_power=0.0, maximum_power=20000.0, loss_factor=0.03, demand_rate=0.0,
                        cost_parameters=[0.0, 0.0, 0.0])
    offices = [Neighbor(transactive_node=tn, name=name, transactive=True, up_or_down='downstream',
                        default_vertices=[[INF, 0.0, -60.0, True]], minimum_power=-120.0, maximum_power=0.0,
                        loss_factor=0.01, demand_rate=4.5, cost_parameters=[0.0, 0.0, 0.0])
               for name in ('SMALL_OFFICE_ILC', 'SMALL_OFFICE_DR')]
    tn.neighbors = [supplier, *offices]
    tn.market_series = [MarketSeries(transactive_node=tn, name='Real-Time Auction', market_class=Auction,
                                     duration_active=300.0, duration_negotiation=600.0, duration_market_lead=300.0,
                                     duration_delivery_lead=300.0, interval_duration=3600.0, intervals_to_clear=2,
                                     market_clearing_interval=3600.0)]
    return tn


def received_demand(market, office):
    """What a small office sends: its demand only (negative quantities), one inelastic vertex per interval."""
    signal = TransactiveSignal(market=market, neighbor=office, direction=SignalDirection.received)
    for time_interval in market.time_intervals:
        signal.update(time_interval, [Vertex(price=INF, quantity=-55.0)], archive=False)
    return signal


def test_a_surplus_residual_curve_bids_zero_to_the_supplier_instead_of_crashing(campus, clock, caplog):
    market = campus.market_series[0].newest_market
    times = market.transition_times
    supplier, offices = campus.neighbors[0], campus.neighbors[1:]
    for office in offices:
        office.last_received_signals[market] = received_demand(market, office)
    for t in (T0, times.active, times.negotiation):
        clock['t'] = t
        campus.step()
    assert market.market_state is MarketState.Negotiation
    clock['t'] = times.market_lead
    with caplog.at_level('WARNING'):
        campus.step()  # while_in_market_lead: schedule the offices, prepare and send the bid to CoR
    assert market.market_state is MarketState.MarketLead and market._stateIsCompleted, 'the bid was prepared and sent'

    bid = supplier.prepared_signals[market]
    surplus, deficit = market.time_intervals
    assert [(v.price, v.quantity) for v in bid.get_by_interval(surplus).vertices] == [(INF, 0.0)], \
        'a surplus the supplier cannot take back is offered as an inelastic zero'
    assert bid.balance_point(surplus) is bid.get_by_interval(surplus).vertices[0]
    assert all(-20000.0 <= v.quantity < 0 for v in bid.get_by_interval(deficit).vertices), \
        'the deficit interval asks the supplier for power within its limits'
    assert bid.get_by_interval(deficit).vertices[0].quantity == pytest.approx(-(3000.0 + 2 * 55.0), rel=0.05), \
        'the load plus the offices, in the supplier\'s perspective (losses restored)'
    assert supplier.last_sent_signals[market] is not None
    assert any('CoR' in r.message and 'lies entirely outside its limits [-20000.0, -0.0]' in r.message
               for r in caplog.records), 'the collapse is reported, naming the neighbor'
    assert not any('IndexError' in r.message for r in caplog.records)
