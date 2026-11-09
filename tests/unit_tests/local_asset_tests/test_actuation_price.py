"""
An asset actuates on its own curve's price at the power the market cleared it to (ModelFrameAsset.actuation_price),
not on the market's marginal price as such. For a market that clears at one price the two coincide: the asset was
scheduled from its curve at that price. For an order book whose fills are at the resting orders' prices (continuous
clearing) the asset's cleared power is the sum of its legs and its curve's price for it is what the asset itself asked,
where the market's marginal price may be another participant's. Where the curve has no unique price at the cleared
power the market's price is used.
"""
import pytest

from tent.containers.vertex import Vertex
from tests.unit_tests.local_asset_tests.test_model_frame_consistency import RecordingManager, build, light, thermostat


def delivery_interval(market):
    return next(ti for ti in market.time_intervals if ti.start_time == market.transition_times.delivery)


def written_for(asset, price, ti, market) -> dict:
    """What the manager writes for a price: the mapping's set points under the models' actuation keys."""
    mapping = asset.set_points_at_price(price, ti, market)
    return {'hp1/csp': mapping['HP1'], 'l1/level': mapping['L1']}


def build_actuating(points=5):
    tn, market, asset = build(points, [thermostat('HP1', 'hp1/csp'), light('L1', 'l1/level')],
                              actuation={'class_name': 'RecordingManager', 'module_name':
                                         'tests.unit_tests.local_asset_tests.test_model_frame_consistency'})
    asset.schedule(market)
    return tn, market, asset, delivery_interval(market)


def test_for_a_market_clearing_at_one_price_the_actuation_price_is_the_market_price():
    """An auction's clearing assigns the asset its curve's power at the market price (Market.assign_cleared_powers);
    the asset's price for that power is the market price again, and the set points written are the same."""
    tn, market, asset, ti = build_actuating()
    for price in (0.0425, 0.045, 0.05, 0.0575):  # within the curve's span, 0.04 to 0.06
        market.marginal_prices.update(ti, price)
        market.assign_cleared_powers(ti, price)
        assert market.cleared_power(asset, ti) == pytest.approx(
            asset.active_vertices.get_by_interval(ti).power_from_vertices(price))
        assert asset.actuation_price(ti, market) == pytest.approx(price, abs=1e-9)
        asset.actuation_manager.actuate(market)
        assert asset.actuation_manager.written == pytest.approx(written_for(asset, price, ti, market))
    # A price beyond the span clears the asset at the curve's end; the curve's price there is the end vertex's, and the
    # set points are the clamped ones the market price gives: the auction's actuation is unchanged.
    for price, end in ((0.10, 0.06), (0.01, 0.04)):
        market.marginal_prices.update(ti, price)
        market.assign_cleared_powers(ti, price)
        assert asset.actuation_price(ti, market) == pytest.approx(end)
        asset.actuation_manager.actuate(market)
        assert asset.actuation_manager.written == pytest.approx(written_for(asset, price, ti, market))


def test_for_a_book_the_asset_actuates_on_its_own_price_for_what_it_traded():
    """A book settles the asset's legs into its cleared power (OrderBook.settle stores the power with the curve, as
    balance does) and reports another participant's price as the interval's marginal price: the asset actuates on its
    curve's price for the power it traded, 0.045 here, not on the market's 0.055."""
    tn, market, asset, ti = build_actuating()
    curve = asset.active_vertices.get_by_interval(ti)
    traded = curve.power_from_vertices(0.045)
    market._cleared_powers[(asset.id, ti.name)] = (curve, traded)
    market.marginal_prices.update(ti, 0.055)
    assert asset.actuation_price(ti, market) == pytest.approx(0.045, abs=1e-9)
    asset.actuation_manager.actuate(market)
    assert asset.actuation_manager.written == pytest.approx(written_for(asset, 0.045, ti, market))
    assert asset.actuation_manager.written != written_for(asset, 0.055, ti, market)
    # And what the devices are set to produce is the power the asset traded.
    params = asset._model_params(ti, None)
    assert asset.model_power_at(params, asset.set_points_at_price(0.045, ti, market)) == pytest.approx(traded, abs=1e-6)


def test_the_market_price_applies_where_the_curve_has_no_unique_price():
    tn, market, asset, ti = build_actuating()
    market.marginal_prices.update(ti, 0.055)
    assert market.cleared_power(asset, ti) is None
    assert asset.actuation_price(ti, market) == 0.055, 'nothing cleared yet'
    curve = asset.active_vertices.get_by_interval(ti)
    market._cleared_powers[(asset.id, ti.name)] = (curve, curve.vertices[0].quantity - 5.0)
    assert asset.actuation_price(ti, market) == 0.055, 'a power off the curve'
    # A vertical segment: two vertices at one quantity.
    quantity = curve.vertices[2].quantity
    asset.active_vertices.update(ti, [Vertex(price=0.04, quantity=quantity - 1.0), Vertex(price=0.045, quantity=quantity),
                                      Vertex(price=0.055, quantity=quantity), Vertex(price=0.06, quantity=quantity + 1.0)],
                                 replace=True)
    vertical = asset.active_vertices.get_by_interval(ti)
    market._cleared_powers[(asset.id, ti.name)] = (vertical, quantity)
    assert asset.actuation_price(ti, market) == 0.055
    market._cleared_powers[(asset.id, ti.name)] = (vertical, quantity - 0.5)
    assert asset.actuation_price(ti, market) == pytest.approx(0.0425), 'on a sloped segment the curve prices it'
    # An inelastic asset (one vertex) has no price of its own.
    tn1, market1, asset1, ti1 = build_actuating(points=1)
    market1.marginal_prices.update(ti1, 0.07)
    curve1 = asset1.active_vertices.get_by_interval(ti1)
    market1._cleared_powers[(asset1.id, ti1.name)] = (curve1, curve1.vertices[0].quantity)
    assert asset1.actuation_price(ti1, market1) == 0.07
    market1.marginal_prices._data.clear()
    assert asset1.actuation_price(ti1, market1) == market1.series.default_price, 'and the series default without one'
