"""
Fixtures for the integration tests that run the campus sample node, sample_configs/tn.campus.json: a PNNL campus with
an inelastic building load (OpenLoopLoadPredictor fed by the temperature forecast file), a 120 kW solar array, an
upstream supplier (CoR) and two downstream small offices, trading in a Day-Ahead and a Real-Time Auction series.

The campus config's paths are relative to the repository root; the fixtures resolve them so the tests do not depend on
the working directory, and replace the sample's sqlite archive with a NullDataManager. The clock is Timer.now patched to
a dict the tests move along the market's transition times.

A single-node auction needs its neighbors' signals to progress: the offices' bids before MarketLead can send the campus
bid to the supplier, and the supplier's offer before DeliveryLead can balance the market. deliver_neighbor_signals
provides both as received signals; clear drives the market to a cleared DeliveryLead.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tent.containers.transactive_signal import TransactiveSignal
from tent.containers.vertex import Vertex
from tent.enumerations import MarketState, SignalDirection
from tent.utils.timer import Timer
from tests.mock_tn import MockTN

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMPUS_CONFIG = REPO_ROOT / 'sample_configs' / 'tn.campus.json'
T0 = datetime(2017, 7, 1, 0, 0, tzinfo=timezone.utc)  # the campus config's simulation start time
INF = float('inf')
DEFAULT_PRICE = 0.0428  # default_price of both campus series
OFFICE_BID = [(INF, -55.0)]  # each small office bids 55 kW of inelastic demand
SUPPLIER_OFFER = [(0.03, 0.0), (0.07, 20000.0)]  # CoR offers from 0.03 $/kWh at no load to 0.07 at its 20 MW limit


def campus_config(**series_overrides) -> dict:
    """The campus sample configuration made independent of the working directory and of the sample's database.
    series_overrides (e.g. duration_negotiation=600) are applied to every market series."""
    with open(CAMPUS_CONFIG, encoding='utf-8') as f:
        configs = json.load(f)
    for ism in configs['information_service_models']:
        ism['weather_file_path'] = str(REPO_ROOT / ism['weather_file_path'])
    configs['data_manager'] = {'class_name': 'NullDataManager'}
    for series in configs['market_series']:
        series.update(series_overrides)
    return configs


@pytest.fixture
def clock():
    """Timer.now returns clock['t']. Starts at the campus simulation start; tests set it to the transition times."""
    state = {'t': T0}
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: state['t']):
        yield state


def configure_campus(**series_overrides) -> MockTN:
    tn = MockTN()
    tn.configure_node(campus_config(**series_overrides))
    return tn


@pytest.fixture
def campus(clock):
    """The campus node, configured at T0. Its Day-Ahead auction's first market clears at 01:45 for delivery from
    02:00."""
    return configure_campus()


def step_to(tn, clock, when):
    clock['t'] = when
    tn.step()


def received(market, neighbor, vertices):
    """A signal as received from this neighbor: the same (price, quantity) vertices in every interval of the market."""
    signal = TransactiveSignal(market=market, neighbor=neighbor, direction=SignalDirection.received)
    for time_interval in market.time_intervals:
        signal.update(time_interval, [Vertex(price=p, quantity=q) for p, q in vertices], archive=False)
    return signal


def deliver_neighbor_signals(tn, market, offices=True, supplier=True):
    """Hand the campus the neighbor signals its auction waits for: the two offices' bids and the supplier's offer."""
    cor, *small_offices = tn.neighbors
    if offices:
        for office in small_offices:
            office.last_received_signals[market] = received(market, office, OFFICE_BID)
    if supplier:
        cor.last_received_signals[market] = received(market, cor, SUPPLIER_OFFER)


def clear(tn, clock, market):
    """Drive the market to a cleared DeliveryLead: schedule and bid at the active time (the campus series run Active,
    Negotiation and MarketLead at the same instant), then balance at the delivery lead time."""
    deliver_neighbor_signals(tn, market)
    step_to(tn, clock, market.transition_times.active)
    assert market.market_state is MarketState.MarketLead and market._stateIsCompleted, 'the bid went to the supplier'
    step_to(tn, clock, market.transition_times.delivery_lead)
    assert market.market_state is MarketState.DeliveryLead and market._stateIsCompleted, 'the market balanced'


def prices(market) -> list[float]:
    return [market.marginal_prices.get_by_interval(ti).value for ti in market.time_intervals]


def powers(entity, market) -> list[float]:
    return [entity.scheduled_powers.get_by_interval(ti).value for ti in market.time_intervals]


def asset_named(tn, name):
    return tn.get_assets_by_attribute('name', name, first_only=True)
