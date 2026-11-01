"""BulkSupplierDC.update_vertices: the BPA tariff's energy rate by hour type, with demand charges in heavy-load hours."""
from datetime import datetime, timezone

import pytest

from tent.neighbor.bulk_supplier_dc import BulkSupplierDC
from tests.mock_tn import MockTN
from tests.stubs import ConstantPriceModel, make_market


def supplier_in_market(hour: datetime, demand_threshold: float):
    tn = MockTN()
    market = make_market(tn, hour, price_model=ConstantPriceModel(0.04))
    supplier = BulkSupplierDC(transactive_node=tn, name='BulkSupplier', cost_parameters=[0.0, 0.0, 0.0],
                              default_vertices=[{'price': float('inf'), 'quantity': 0.0, 'cost': 0.0}],
                              maximum_power=100.0, minimum_power=0.0, loss_factor=0.0, demand_rate=0.0,
                              demand_threshold=demand_threshold)
    tn.neighbors = [supplier]
    interval = market.time_intervals[0]
    assert interval.start_time == hour
    supplier.scheduled_powers.update(interval, 0.0)
    return supplier, market, interval


def points(sd_curve):
    return sorted((round(v.quantity, 6), round(v.price, 5)) for v in sd_curve.vertices)


def test_light_load_hour_without_demand_charges_gives_a_flat_two_vertex_curve():
    supplier, market, interval = supplier_in_market(datetime(2021, 1, 1, 1, tzinfo=timezone.utc),
                                                    demand_threshold=101.0)  # above the power range
    supplier.update_vertices(market)
    assert points(supplier.active_vertices.get_by_interval(interval)) == [(0.0, 0.0366), (100.0, 0.0366)]


def test_heavy_load_hour_adds_the_demand_charge_above_the_threshold():
    supplier, market, interval = supplier_in_market(datetime(2021, 3, 3, 13, tzinfo=timezone.utc),
                                                    demand_threshold=50.0)  # inside the power range
    supplier.update_vertices(market)
    curve = supplier.active_vertices.get_by_interval(interval)
    assert [q for q, _ in points(curve)] == pytest.approx([0.0, 50.0, 50.0, 100.0])
    assert [p for _, p in points(curve)] == pytest.approx([0.03641, 0.03641, 9.31641, 9.31641], rel=1e-4)
