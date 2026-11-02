"""
The entity whose curve is flat at the cleared price is the marginal one, and its own curve does not say how much it
exchanges: a tariff supplier read at the interpolated price (a hair under the tariff) scheduled its minimum, 0 kW, while
the market called itself balanced with the whole load unmet. Market.assign_cleared_powers gives such entities the
residual of the others, and ConsensusMarket refuses convergence while the scheduled powers do not add up.
"""
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tent.market.consensus_market import ConsensusMarket
from tent.market.market_series import MarketSeries
from tent.neighbor.bulk_supplier_dc import BulkSupplierDC
from tent.neighbor.response_neighbor import ResponseNeighbor
from tent.utils.const import bpa_energy_rate
from tent.utils.timer import Timer
from tests.integration_tests.test_response_neighbor_markets import FlexibleAsset, INELASTIC_LOAD, SERIES
from tests.mock_tn import MockTN

T0 = datetime(2017, 7, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def clock():
    state = {'t': T0}
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: state['t']):
        yield state


def node_with(neighbors_of, load_curve=INELASTIC_LOAD, load_limits=(-100.0, 0.0), intervals=2):
    tn = MockTN(name='campus')
    tn.local_assets = [FlexibleAsset(transactive_node=tn, name='buildings', cost_parameters=[0.0, 0.0, 0.0],
                                     default_vertices=load_curve, minimum_power=load_limits[0],
                                     maximum_power=load_limits[1])]
    tn.neighbors = neighbors_of(tn)
    tn.market_series = [MarketSeries(transactive_node=tn, market_class=ConsensusMarket,
                                     **dict(SERIES, intervals_to_clear=intervals))]
    return tn


def negotiate(tn, clock, ticks=2):
    market = tn.market_series[0].newest_market
    times = market.transition_times
    for when in (times.active, times.negotiation, *(times.negotiation + timedelta(seconds=s) for s in range(1, ticks + 1))):
        clock['t'] = when
        tn.step()
    return market


def tariff_price(supplier, time_interval):
    month = time_interval.start_time.month
    return bpa_energy_rate[month - 1][0 if supplier.is_heavy_load_hour(time_interval.start_time) else 1]


def test_a_flat_tariff_supplier_is_scheduled_for_the_whole_load(clock):
    tn = node_with(lambda tn: [BulkSupplierDC(transactive_node=tn, name='bpa', cost_parameters=[0.0, 0.0, 0.0],
                                              minimum_power=0.0, maximum_power=1000.0, loss_factor=0.0,
                                              demand_rate=0.0, demand_threshold=1e9, up_or_down='upstream',
                                              default_vertices=[[float('inf'), 0.0, 0.0, True]])])
    market = negotiate(tn, clock)
    supplier = tn.neighbors[0]
    assert market.converged and market.in_consensus
    for time_interval in market.time_intervals:
        assert market.marginal_prices.get_by_interval(time_interval).value == pytest.approx(
            tariff_price(supplier, time_interval), abs=1e-9)
        assert supplier.scheduled_powers.get_by_interval(time_interval).value == pytest.approx(100.0)
        assert market.net_powers.get_by_interval(time_interval).value == pytest.approx(0.0, abs=1e-9)


def test_two_suppliers_flat_at_the_same_price_share_the_load_by_segment_width(clock):
    def suppliers(tn):
        return [ResponseNeighbor(transactive_node=tn, name=name, response_type='price', subscribe_key=f'{name}/price',
                                 minimum_power=0.0, maximum_power=maximum, loss_factor=0.0, demand_rate=0.0,
                                 cost_parameters=[0.0, 0.0, 0.0], up_or_down='upstream')
                for name, maximum in (('wide', 1000.0), ('narrow', 500.0))]
    tn = node_with(suppliers, load_curve=[[float('inf'), 0.0, -300.0, True]], load_limits=(-300.0, 0.0))
    tn.provide_subscription('wide/price', 0.05)
    tn.provide_subscription('narrow/price', 0.05)
    market = negotiate(tn, clock)
    assert market.converged
    by_name = {n.name: n for n in tn.neighbors}
    for time_interval in market.time_intervals:
        assert market.marginal_prices.get_by_interval(time_interval).value == pytest.approx(0.05, abs=1e-9)
        assert by_name['wide'].scheduled_powers.get_by_interval(time_interval).value == pytest.approx(200.0)
        assert by_name['narrow'].scheduled_powers.get_by_interval(time_interval).value == pytest.approx(100.0)
        assert market.net_powers.get_by_interval(time_interval).value == pytest.approx(0.0, abs=1e-9)


def test_a_deficit_no_counterparty_supplies_is_out_of_consensus_but_does_not_spin(clock, caplog):
    """The supplier can give at most 60 kW to a 100 kW inelastic load: no price balances the interval. The market
    schedules the supplier at its 60 kW, records the 40 kW deficit, keeps the interval out of consensus, says so once
    per interval, and does not rebalance on every tick."""
    tn = node_with(lambda tn: [BulkSupplierDC(transactive_node=tn, name='bpa', cost_parameters=[0.0, 0.0, 0.0],
                                              minimum_power=0.0, maximum_power=60.0, loss_factor=0.0,
                                              demand_rate=0.0, demand_threshold=1e9, up_or_down='upstream',
                                              default_vertices=[[float('inf'), 0.0, 0.0, True]])])
    with caplog.at_level(logging.WARNING, logger='tent.market'):
        market = negotiate(tn, clock, ticks=4)
    assert market.converged, 'the local problem is solved as far as its counterparties allow'
    assert not market.in_consensus
    assert all(bool(v) is False for v in market.full_consensus.values)
    assert market.unbalanceable == {ti.name: pytest.approx(-40.0) for ti in market.time_intervals}
    cannot = [r.getMessage() for r in caplog.records if 'cannot balance' in r.getMessage()]
    assert len(cannot) == 2 and all('deficit of 40 kW' in m for m in cannot), 'once per interval over five passes'
    assert not any('is not balanced' in r.getMessage() for r in caplog.records), 'a known deficit is not an imbalance'
    assert market.negotiation_passes == 1, 'nothing rebalances on later ticks: the negotiation does not spin'
    for time_interval in market.time_intervals:
        assert market.net_powers.get_by_interval(time_interval).value == pytest.approx(-40.0)
        assert tn.neighbors[0].scheduled_powers.get_by_interval(time_interval).value == pytest.approx(60.0)
