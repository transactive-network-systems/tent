"""Entity.schedule and Entity.update_costs on the two concrete entities, Neighbor and LocalAsset."""
from datetime import datetime, timezone

import pytest

from tent.containers import Vertex
from tent.local_asset import LocalAsset
from tent.neighbor import Neighbor
from tests.mock_tn import MockTN
from tests.stubs import ConstantPriceModel, make_market

PRICE = 0.05


@pytest.fixture
def market():
    tn = MockTN()
    market = make_market(tn, datetime(2026, 10, 31, 20, tzinfo=timezone.utc), default_price=PRICE,
                         price_model=ConstantPriceModel(PRICE))
    market.marginal_prices.update(market.time_intervals[0], PRICE)
    return market


def neighbor(tn, name='TestNeighbor'):
    model = Neighbor(tn, cost_parameters=[0.0, 0.0, 0.0], default_vertices=[Vertex(price=PRICE, cost=0.0, quantity=0.0)],
                     name=name, maximum_power=100.0, minimum_power=0.0)
    model.transactive = False  # a non-transactive neighbor schedules from its default vertices
    tn.neighbors.append(model)
    return model


def local_asset(tn, name='TestAsset'):
    model = LocalAsset(tn, name=name, maximum_power=100.0)
    tn.local_assets.append(model)
    return model


def seed(entity, time_interval, power=100.0):
    entity.scheduled_powers.update(time_interval, power)
    entity.active_vertices.update(time_interval, [Vertex(price=PRICE, cost=0.0, quantity=power)])


@pytest.mark.parametrize('factory', [neighbor, local_asset])
def test_schedule_stores_power_margin_and_curve_for_every_interval(market, factory):
    entity = factory(market.tn)
    ti = market.time_intervals[0]
    entity.schedule(market)
    assert len(entity.scheduled_powers) == 1 and entity.scheduled_powers.get_by_interval(ti) is not None
    assert len(entity.reserve_margins) == 1 and entity.reserve_margins.get_by_interval(ti) is not None
    curve = entity.active_vertices.get_by_interval(ti)
    assert len(entity.active_vertices) == 1 and curve is not None and not curve.empty


@pytest.mark.parametrize('factory', [neighbor, local_asset])
def test_update_costs_stores_interval_costs_and_their_totals(market, factory):
    entity = factory(market.tn)
    ti = market.time_intervals[0]
    seed(entity, ti)
    entity.update_costs(market)
    assert len(entity.production_costs) == 1 and len(entity.dual_costs) == 1
    assert entity.total_production_cost == sum(v.value for _, v in entity.production_costs.all)
    assert entity.total_dual_cost == sum(v.value for _, v in entity.dual_costs.all)
    assert entity.total_dual_cost == pytest.approx(-PRICE * 100.0), 'dual cost of 100 kW supplied at the marginal price'
