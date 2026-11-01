"""Market: interval creation, system-curve aggregation, balancing and marginal-price initialisation."""
from datetime import datetime, timedelta, timezone

import pytest

from tent.containers.sd_curve import SDCurve
from tent.containers.vertex import Vertex
from tent.enumerations import MarketState
from tent.local_asset import LocalAsset
from tent.market import Market
from tent.neighbor import Neighbor
from tests.mock_tn import MockTN
from tests.stubs import ConstantPriceModel, make_market

INF = float('inf')


def curve(interval, vertices):
    return SDCurve(measurement=None, interval=interval, vertices=vertices, archive=False)


@pytest.fixture
def market_with_entities():
    """One asset consuming 4 kW inelastically and one neighbor supplying 0 kW at 0.02 up to 10 kW at 0.05."""
    tn = MockTN()
    market = make_market(tn)
    ti = market.time_intervals[0]
    asset = LocalAsset(transactive_node=tn)
    neighbor = Neighbor(transactive_node=tn)
    tn.local_assets, tn.neighbors = [asset], [neighbor]
    neighbor.active_vertices.update(ti, curve(ti, [Vertex(0.02, 0, 0), Vertex(0.05, 0, 10)]))
    asset.active_vertices.update(ti, curve(ti, [Vertex(INF, 0, -4)]))
    return market, ti, asset, neighbor


def test_assign_system_vertices_sums_the_entity_curves(market_with_entities):
    market, ti, _, _ = market_with_entities
    market.assign_system_vertices()
    vertices = list(market.active_vertices.get_by_interval(ti).vertices)
    assert [(v.price, v.quantity) for v in vertices] == [(0.02, -4), (0.05, 6)]


def test_balance_finds_the_price_where_the_system_curve_crosses_zero(market_with_entities):
    market, ti, _, _ = market_with_entities
    market.marginal_prices.update(ti, 0.06)  # a prior guess that balance must replace
    market.balance()
    vertices = list(market.active_vertices.get_by_interval(ti).vertices)
    assert [(v.price, v.quantity) for v in vertices] == [(0.02, -4), (0.05, 6)]
    assert market.marginal_prices.get_by_interval(ti).value == pytest.approx(0.032)


def test_balance_brackets_a_price_step_with_the_vertices_nearest_the_crossing():
    """Two demand vertices share the largest demand quantity at different prices (a load's floor reached at 0.08 and
    still there just under 0.10, where a flat supplier at 0.10 steps in); the crossing is at the step, 0.10."""
    tn = MockTN()
    market = make_market(tn)
    ti = market.time_intervals[0]
    load = LocalAsset(transactive_node=tn, cost_parameters=[0.0, 0.0, 0.0])
    supplier = Neighbor(transactive_node=tn, cost_parameters=[0.0, 0.0, 0.0])
    tn.local_assets, tn.neighbors = [load], [supplier]
    load.active_vertices.update(ti, curve(ti, [Vertex(0.04, 0, -100), Vertex(0.08, 0, -20)]))
    supplier.active_vertices.update(ti, curve(ti, [Vertex(0.10, 0, 0), Vertex(0.10, 0, 50)]))
    assert market.balance() is True
    vertices = [(round(v.price, 6), v.quantity) for v in market.active_vertices.get_by_interval(ti).vertices]
    assert vertices == [(0.04, -100), (0.08, -20), (0.1, -20), (0.1, 30)]
    assert market.marginal_prices.get_by_interval(ti).value == pytest.approx(0.10, abs=1e-9)
    # The load reads its curve at the price; the flat supplier takes the residual, 20 kW, not its 0 kW minimum.
    assert market.cleared_power(load, ti) == pytest.approx(-20.0)
    assert market.cleared_power(supplier, ti) == pytest.approx(20.0)


def test_balance_returns_false_and_keeps_the_old_price_when_an_interval_cannot_be_bracketed(market_with_entities):
    market, ti, asset, neighbor = market_with_entities
    neighbor.active_vertices.update(ti, curve(ti, [Vertex(INF, 0, 0)]), replace=True)  # no supply at all
    market.marginal_prices.update(ti, 0.06)
    assert market.balance() is False
    assert market.converged is False
    assert market.marginal_prices.get_by_interval(ti).value == 0.06
    assert market.cleared_power(asset, ti) is None


class TestClearedPowers:
    """Market.assign_cleared_powers: entities whose curves are flat at the cleared price take the residual of the rest."""

    def setup_entities(self, supplier_curves, load_quantity=-300.0):
        tn = MockTN()
        market = make_market(tn)
        ti = market.time_intervals[0]
        load = LocalAsset(transactive_node=tn, cost_parameters=[0.0, 0.0, 0.0])
        load.active_vertices.update(ti, curve(ti, [Vertex(INF, 0, load_quantity)]))
        suppliers = []
        for vertices in supplier_curves:
            supplier = Neighbor(transactive_node=tn, cost_parameters=[0.0, 0.0, 0.0])
            supplier.active_vertices.update(ti, curve(ti, vertices))
            suppliers.append(supplier)
        tn.local_assets, tn.neighbors = [load], suppliers
        return market, ti, load, suppliers

    def test_one_flat_supplier_takes_the_whole_residual(self):
        market, ti, load, (supplier,) = self.setup_entities([[Vertex(0.05, 0, 0), Vertex(0.05, 0, 1000)]])
        assert market.balance()
        assert market.marginal_prices.get_by_interval(ti).value == pytest.approx(0.05, abs=1e-9)
        assert market.cleared_power(supplier, ti) == pytest.approx(300.0)
        assert market.cleared_power(load, ti) == pytest.approx(-300.0)
        # Reading the flat curve at the interpolated price (a hair under 0.05) would have given its minimum.
        assert supplier.production(market.marginal_prices.get_by_interval(ti).value, ti) == 0.0

    def test_several_flat_suppliers_share_the_residual_by_segment_width(self):
        market, ti, _, (wide, narrow) = self.setup_entities([[Vertex(0.05, 0, 0), Vertex(0.05, 0, 1000)],
                                                             [Vertex(0.05, 0, 0), Vertex(0.05, 0, 500)]])
        assert market.balance()
        assert market.cleared_power(wide, ti) == pytest.approx(200.0)
        assert market.cleared_power(narrow, ti) == pytest.approx(100.0)

    def test_a_flat_segment_that_does_not_start_at_zero_is_shared_within_its_bounds(self):
        market, ti, _, (wide, offset) = self.setup_entities([[Vertex(0.05, 0, 0), Vertex(0.05, 0, 1000)],
                                                             [Vertex(0.05, 0, 100), Vertex(0.05, 0, 300)]])
        assert market.balance()
        # Residual 300 = 100 + 100 (the offset segment's floor) + 200 shared 1000:200 -> 166.67 and 33.33.
        assert market.cleared_power(wide, ti) == pytest.approx(200.0 * 1000 / 1200)
        assert market.cleared_power(offset, ti) == pytest.approx(100.0 + 200.0 * 200 / 1200)

    def test_a_sloped_supplier_reads_its_curve_and_the_flat_one_takes_what_is_left(self):
        market, ti, _, (sloped, flat) = self.setup_entities([[Vertex(0.03, 0, 0), Vertex(0.07, 0, 400)],
                                                             [Vertex(0.05, 0, 0), Vertex(0.05, 0, 1000)]])
        assert market.balance()
        assert market.marginal_prices.get_by_interval(ti).value == pytest.approx(0.05, abs=1e-9)
        assert market.cleared_power(sloped, ti) == pytest.approx(200.0, abs=1e-6)
        assert market.cleared_power(flat, ti) == pytest.approx(100.0, abs=1e-6)

    def test_an_assignment_lapses_when_the_entity_rebuilds_its_curve(self):
        market, ti, _, (supplier,) = self.setup_entities([[Vertex(0.05, 0, 0), Vertex(0.05, 0, 1000)]])
        assert market.balance()
        assert market.cleared_power(supplier, ti) == pytest.approx(300.0)
        assert supplier.market_power(market, ti) == pytest.approx(300.0)
        supplier.active_vertices.update(ti, curve(ti, [Vertex(0.05, 0, 0), Vertex(0.05, 0, 800)]), replace=True)
        assert market.cleared_power(supplier, ti) is None
        assert supplier.market_power(market, ti) == 0.0, 'read off the new curve at the price until the next balance'

    def test_a_residual_outside_the_flat_segments_is_clipped_with_a_warning(self, caplog):
        market, ti, _, (supplier,) = self.setup_entities([[Vertex(0.05, 0, 0), Vertex(0.05, 0, 1000)]])
        market.marginal_prices.update(ti, 0.05)
        import logging
        with caplog.at_level(logging.WARNING, logger='tent.market'):
            market.assign_cleared_powers(ti, 0.05)  # consistent: 300 within [0, 1000]
            assert not any('outside what the entities flat' in r.getMessage() for r in caplog.records)
            supplier.active_vertices.update(ti, curve(ti, [Vertex(0.05, 0, 0), Vertex(0.05, 0, 100)]), replace=True)
            market.assign_cleared_powers(ti, 0.05)  # 300 kW needed, 100 kW available
        assert market.cleared_power(supplier, ti) == pytest.approx(100.0)
        assert any('outside what the entities flat' in r.getMessage() for r in caplog.records)


def test_market_creates_one_interval_per_intervals_to_clear_from_delivery_start():
    clearing = datetime(2020, 1, 1, tzinfo=timezone.utc)
    market = make_market(MockTN(), clearing, intervals_to_clear=6, delivery_lead=timedelta(minutes=10))
    intervals = market.time_intervals
    assert intervals.number_of_intervals == 6
    assert intervals[0].start_time == market.transition_times.delivery == clearing + timedelta(minutes=10)
    assert intervals[1].start_time - intervals[0].start_time == market.series.interval_duration == timedelta(hours=1)


class TestCheckMarginalPrices:
    """check_marginal_prices fills a missing price from, in order: the existing price, the prior market, the refined
    market, the price model, the series default; and records None when nothing applies."""

    def test_an_existing_price_is_kept(self):
        market = make_market(MockTN(), default_price=0.05, price_model=ConstantPriceModel(0.07))
        ti = market.time_intervals[0]
        market.marginal_prices.update(ti, 3.14159)
        market.check_marginal_prices()
        assert market.marginal_prices.get_by_interval(ti).value == 3.14159

    def test_the_price_model_is_used_before_the_default(self):
        market = make_market(MockTN(), default_price=0.05, price_model=ConstantPriceModel(0.07))
        ti = market.time_intervals[0]
        market.marginal_prices._data.clear()
        market.check_marginal_prices()
        assert market.marginal_prices.get_by_interval(ti).value == 0.07

    def test_the_series_default_is_used_without_a_model(self):
        market = make_market(MockTN(), default_price=3.14159)
        market.price_model = None
        ti = market.time_intervals[0]
        market.marginal_prices._data.clear()
        market.check_marginal_prices()
        assert market.marginal_prices.get_by_interval(ti).value == 3.14159

    def test_none_is_recorded_when_nothing_applies(self):
        market = make_market(MockTN(), default_price=None)
        market.price_model = None
        ti = market.time_intervals[0]
        market.marginal_prices._data.clear()
        market.check_marginal_prices()
        price = market.marginal_prices.get_by_interval(ti)
        assert price is not None and price.value is None


class TestSumVertices:
    @pytest.fixture
    def setup(self):
        tn = MockTN()
        market = make_market(tn)
        ti = market.time_intervals[0]
        asset = LocalAsset(transactive_node=tn)
        neighbor = Neighbor(transactive_node=tn)
        tn.local_assets, tn.neighbors = [asset], [neighbor]
        asset.active_vertices.update(ti, curve(ti, [Vertex(0.2, 0, -110), Vertex(0.2, 0, -90)]))
        neighbor.active_vertices.update(ti, curve(ti, [Vertex(0.1, 0, 0), Vertex(0.3, 0, 200)]))
        return market, ti, asset, neighbor

    @staticmethod
    def points(sd_curve):
        return sorted((round(v.price, 4), round(v.quantity, 4)) for v in sd_curve.vertices)

    def test_interleaved_vertices_sum_at_every_breakpoint(self, setup):
        market, ti, _, _ = setup
        assert self.points(market.sum_vertices(ti)) == [(0.1, -110), (0.2, -10), (0.2, 10), (0.3, 110)]

    def test_an_excluded_entity_is_left_out(self, setup):
        market, ti, _, neighbor = setup
        assert self.points(market.sum_vertices(ti, neighbor)) == [(0.2, -110), (0.2, -90)]

    def test_an_inelastic_entity_shifts_the_curve_without_adding_a_breakpoint(self, setup):
        market, ti, asset, _ = setup
        asset.active_vertices.update(ti, curve(ti, [Vertex(0.2, 0, -110)]), replace=True)
        assert self.points(market.sum_vertices(ti)) == [(0.1, -110), (0.3, 90)]

    def test_a_vertical_segment_at_one_price_keeps_both_ends(self, setup):
        market, ti, asset, _ = setup
        asset.active_vertices.update(ti, curve(ti, [Vertex(0.1, 0, -110), Vertex(0.1, 0, -90)]), replace=True)
        assert self.points(market.sum_vertices(ti)) == [(0.1, -110), (0.1, -90), (0.3, 110)]


class TestRefreshPricesFromPriorMarket:
    T = datetime(2017, 7, 1, tzinfo=timezone.utc)

    def successor_of(self, tn, prior):
        """A market clearing one hour after `prior` in the same series: its first interval is the prior's second."""
        series = prior._stub_series
        later = Market(tn, series, MarketState.Inactive, series.duration.get_timings(self.T + timedelta(hours=1)),
                       prior_market_in_series=prior)
        tn.markets.append(later)
        return later

    def test_the_prior_markets_cleared_prices_replace_the_opening_prices_of_the_shared_intervals(self):
        tn = MockTN()
        prior = make_market(tn, clearing_time=self.T, intervals_to_clear=2)
        later = self.successor_of(tn, prior)
        shared, new = later.time_intervals
        assert shared.start_time == prior.time_intervals[1].start_time
        assert [later.marginal_prices.get_by_interval(ti).value for ti in (shared, new)] == [0.05, 0.05], \
            'spawned before the prior cleared: the opening prices are the prior\'s opening prices'
        prior.marginal_prices.update(prior.time_intervals[1], 0.07)  # the prior clears
        in_force = later.refresh_prices_from_prior_market()
        assert later.marginal_prices.get_by_interval(shared).value == 0.07
        assert later.marginal_prices.get_by_interval(new).value == 0.05, 'an interval the prior does not cover is kept'
        assert in_force == {shared.start_time: 0.07, new.start_time: 0.05}
        prior.marginal_prices.update(prior.time_intervals[1], 0.08)  # and clears again
        later.refresh_prices_from_prior_market()
        assert later.marginal_prices.get_by_interval(shared).value == 0.08, 'a later refresh follows the prior again'

    def test_a_price_this_market_balanced_itself_is_never_overwritten(self):
        tn = MockTN()
        prior = make_market(tn, clearing_time=self.T, intervals_to_clear=2)
        later = self.successor_of(tn, prior)
        asset = LocalAsset(transactive_node=tn, cost_parameters=[0.0, 0.0, 0.0])
        neighbor = Neighbor(transactive_node=tn, cost_parameters=[0.0, 0.0, 0.0])
        tn.local_assets, tn.neighbors = [asset], [neighbor]
        for ti in later.time_intervals:
            neighbor.active_vertices.update(ti, curve(ti, [Vertex(0.02, 0, 0), Vertex(0.05, 0, 10)]))
            asset.active_vertices.update(ti, curve(ti, [Vertex(INF, 0, -4)]))
        later.balance()
        shared, new = later.time_intervals
        assert later.marginal_prices.get_by_interval(shared).value == pytest.approx(0.032)
        prior.marginal_prices.update(prior.time_intervals[1], 0.07)
        later.refresh_prices_from_prior_market()
        assert later.marginal_prices.get_by_interval(shared).value == pytest.approx(0.032), 'balanced here: kept'
        assert later.marginal_prices.get_by_interval(new).value == pytest.approx(0.032)
