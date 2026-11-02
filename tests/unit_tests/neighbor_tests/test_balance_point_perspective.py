"""
The balance point of a signal is stated as the signal's curve is: in the neighbor's perspective, and across a lossy
link as the quantity the neighbor sends rather than what arrives. The offices of the consensus run marked their received
power (-39.79 kW, after 4 percent losses over a 120 kW link) on a curve whose end was the sent power (-40.33 kW) and
logged "Indeterminate price" 61 848 times.
"""
from datetime import datetime, timezone

import pytest

from tent.containers.transactive_signal import TransactiveSignal
from tent.containers.vertex import Vertex
from tent.local_asset import LocalAsset
from tent.market.consensus_market import ConsensusMarket
from tent.neighbor import Neighbor
from tests.mock_tn import MockTN
from tests.stubs import make_market

T0 = datetime(2017, 7, 3, 0, 0, tzinfo=timezone.utc)


def node(loss_factor):
    tn = MockTN(name='office')
    market = make_market(tn, T0)
    load = LocalAsset(transactive_node=tn, name='load', cost_parameters=[0.0, 0.0, 0.0], default_power=-39.79058800000003)
    campus = Neighbor(transactive_node=tn, name='campus', transactive=True, up_or_down='upstream',
                      default_vertices=[[0.045, 0.0, 0.0, True], [0.048, 0.0, 120.0, True]], minimum_power=0.0,
                      maximum_power=120.0, loss_factor=loss_factor, demand_rate=0.0, cost_parameters=[0.0, 0.0, 0.0])
    tn.local_assets, tn.neighbors = [load], [campus]
    return tn, market, load, campus


def test_the_balance_point_is_the_quantity_the_neighbor_sends(caplog):
    tn, market, load, campus = node(loss_factor=0.04)
    ti = market.time_intervals[0]
    load.schedule(market)
    campus.scheduled_powers.update(ti, 39.79058800000003)  # what arrives here, as the office scheduled it
    quantities = campus.balance_point_quantities(market)
    assert quantities[ti] == pytest.approx(-40.332833828204514), 'the sent power the office logged as the curve end'
    with caplog.at_level('WARNING'):
        campus.prep_transactive_signal(market)
    signal = campus.prepared_signals[market]
    curve = signal.get_by_interval(ti)
    assert len(curve) == 1, 'the residual is the inelastic load, with losses removed: one vertex, and it is the point'
    assert curve.vertices[0].quantity == pytest.approx(-40.332833828204514)
    assert signal.balance_point(ti) is curve.vertices[0]
    assert not any('Indeterminate price' in r.message for r in caplog.records)


def test_without_losses_the_balance_point_is_the_scheduled_power_sign_flipped():
    tn, market, load, campus = node(loss_factor=0.0)
    ti = market.time_intervals[0]
    campus.scheduled_powers.update(ti, 39.79058800000003)
    assert campus.balance_point_quantities(market) == {ti: pytest.approx(-39.79058800000003)}


def test_exports_are_not_translated_and_the_link_capacity_bounds_the_translation():
    tn, market, load, campus = node(loss_factor=0.04)
    ti = market.time_intervals[0]
    campus.scheduled_powers.update(ti, -25.0)  # this node supplies the neighbor: no losses on this side
    assert campus.balance_point_quantities(market) == {ti: pytest.approx(25.0)}
    campus.scheduled_powers.update(ti, 119.0)  # near the link's capacity: the sent power cannot exceed it
    assert campus.balance_point_quantities(market)[ti] == pytest.approx(-120.0)


def test_a_signal_marks_given_quantities_in_its_own_perspective():
    tn, market, load, campus = node(loss_factor=0.0)
    ti = market.time_intervals[0]
    signal = TransactiveSignal(market=market, neighbor=campus)
    signal.update(ti, [Vertex(price=0.04, quantity=-100.0), Vertex(price=0.08, quantity=-20.0)], archive=False)
    signal.mark_balance_points_at_quantities({ti: -60.0})
    assert signal.balance_point(ti).quantity == pytest.approx(-60.0)
    signal.sequence = 1
    message = signal.generate_signal({ti: -40.0})  # a mapping: already in the neighbor's perspective
    curve = list(message['intervals'].values())[0]
    assert curve['flexibility'][curve['balance_point']]['quantity'] == pytest.approx(-40.0)
