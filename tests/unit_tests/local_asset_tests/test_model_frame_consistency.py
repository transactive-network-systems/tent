"""
The model is authoritative: a ModelFrameAsset's curve, its scheduled power and the set points its actuation manager
writes all come from one price-to-set-point mapping, so the scheduled power is the curve read at the price and the
devices are set to produce it. Before, the curve was two vertices at the summed minimum and maximum flexibility, the
schedule was the models' power at their own current set points, and the manager did its own ratio arithmetic; the
three disagreed by 17 to 20 percent on the consensus office and its local balance never closed.
"""
from datetime import datetime, timezone

import pytest

from tent.local_asset.actuation_manager.direct_ratio import DirectRatioActuationManager
from tent.local_asset.model_frame.model_frame_asset import ModelFrameAsset
from tests.mock_tn import MockTN
from tests.stubs import make_market

T0 = datetime(2017, 7, 3, 14, 0, tzinfo=timezone.utc)


class SpreadPriceModel:
    """Average 0.05 $/kWh with a 0.01 standard deviation: the curve spans 0.04 to 0.06."""

    def __init__(self, average=0.05, spread=0.01):
        self.average, self.spread = average, spread

    def get(self, _when):
        return self.average, self.spread

    def update(self, *_):
        pass


def thermostat(key, actuation_key=None):
    # Duty cycle q = -0.05 csp + 0.02 room + 1.5 at OAT 0: 0.916 at 20.8 degC, 0.716 at 24.8 degC (inside [0, 1], so
    # the power is linear in the set point: -9.16 kW to -7.16 kW for a 10 kW unit).
    return {'class_name': 'Thermostat', 'subscribe_key': key,
            'model_config': {'rated_power': 10.0, 'nominal_setpoint': 22.8, 'max_set_point_offset': 2.0,
                             'c1': [-0.05] * 24, 'c2': [0.02] * 24, 'c3': [0.0] * 24, 'c4': [1.5] * 24,
                             'actuation_key': actuation_key}}


def light(key, actuation_key=None):
    # Level 0.5 with a 0.1 offset: 0.4 to 0.6 of 2 kW, -0.8 to -1.2 kW. Its consumption rises with its level, so the
    # mapping runs it the other way: a higher price, a lower level.
    return {'class_name': 'Lighting', 'subscribe_key': key,
            'model_config': {'rated_power': 2.0, 'default_lighting_schedule': [0.5] * 24, 'max_set_point_offset': 0.1,
                             'actuation_key': actuation_key}}


def build(points, models, intervals=2, actuation=None):
    tn = MockTN()
    market = make_market(tn, T0, price_model=SpreadPriceModel(), intervals_to_clear=intervals)
    asset = ModelFrameAsset(transactive_node=tn, name='office', cost_parameters=[0.0, 0.0, 0.0],
                            model_configs={'demand_curve_points': points, 'models': models},
                            actuation_manager=actuation)
    tn.local_assets = [asset]
    return tn, market, asset


def price_at(asset, ti, market, ratio):
    low, high = asset.price_span(ti, market)
    return low + ratio * (high - low)


def test_the_schedule_is_the_curve_read_at_the_price():
    tn, market, asset = build(5, [thermostat('HP1'), light('L1')])
    asset.schedule(market)
    for ti in market.time_intervals:
        curve = asset.active_vertices.get_by_interval(ti)
        assert len(curve) == 5
        assert [v.price for v in curve.vertices] == pytest.approx([0.04, 0.045, 0.05, 0.055, 0.06])
        # At every sweep price the schedule is exactly the vertex there.
        for ratio in asset.sweep_ratios():
            price = price_at(asset, ti, market, ratio)
            market.marginal_prices.update(ti, price)
            asset.schedule_power(market)
            assert asset.scheduled_powers.get_by_interval(ti).value == pytest.approx(curve.power_from_vertices(price),
                                                                                    abs=1e-9)
        # Between sweep prices the curve interpolates linearly; the models are linear in their set points here, so the
        # schedule agrees within numerical tolerance (a clipped duty cycle would bend the model between vertices).
        for price in (0.0425, 0.0475, 0.0525, 0.0575):
            market.marginal_prices.update(ti, price)
            asset.schedule_power(market)
            assert asset.scheduled_powers.get_by_interval(ti).value == pytest.approx(curve.power_from_vertices(price),
                                                                                    abs=1e-6)
        # A price outside the span is clamped to the nearest end, as the manager clamps it.
        market.marginal_prices.update(ti, 0.10)
        asset.schedule_power(market)
        assert asset.scheduled_powers.get_by_interval(ti).value == pytest.approx(curve.vertices[-1].quantity)
        market.marginal_prices.update(ti, 0.01)
        asset.schedule_power(market)
        assert asset.scheduled_powers.get_by_interval(ti).value == pytest.approx(curve.vertices[0].quantity)


def test_without_a_market_price_the_series_default_applies():
    tn, market, asset = build(5, [thermostat('HP1')])
    ti = market.time_intervals[0]
    market.marginal_prices._data.clear()
    asset.schedule(market)
    assert asset.set_points_at_price(market.series.default_price, ti, market) == asset.set_points_at_price(0.05, ti, market)
    assert asset.scheduled_powers.get_by_interval(ti).value == pytest.approx(
        asset.active_vertices.get_by_interval(ti).power_from_vertices(0.05))


class RecordingManager(DirectRatioActuationManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.written = {}

    def direct_actuate(self, target_id, new_set_point):
        self.written[target_id] = new_set_point


def test_the_actuation_manager_writes_the_mappings_set_points():
    tn, market, asset = build(5, [thermostat('HP1', 'hp1/csp'), light('L1', 'l1/level')],
                              actuation={'class_name': 'RecordingManager', 'module_name': __name__})
    asset.schedule(market)
    delivery = next(ti for ti in market.time_intervals if ti.start_time == market.transition_times.delivery)
    for price, expected_ratio in ((0.045, 0.25), (0.06, 1.0), (0.10, 1.0), (0.0, 0.0)):  # clamped beyond the span
        market.marginal_prices.update(delivery, price)
        asset.actuation_manager.actuate(market)
        written = asset.actuation_manager.written
        mapping = asset.set_points_at_price(price, delivery, market)
        assert written == {'hp1/csp': mapping['HP1'], 'l1/level': mapping['L1']}
        assert mapping['HP1'] == pytest.approx(20.8 + expected_ratio * 4.0)
        assert mapping['L1'] == pytest.approx(0.6 - expected_ratio * 0.2), 'reverse-acting: dimmer at a higher price'
        assert asset.price_ratio(price, delivery, market) == pytest.approx(expected_ratio)
    # And what the devices are set to produce is the scheduled power at that price.
    market.marginal_prices.update(delivery, 0.045)
    asset.schedule_power(market)
    params = asset._model_params(delivery, None)
    assert asset.model_power_at(params, asset.set_points_at_price(0.045, delivery, market)) == pytest.approx(
        asset.scheduled_powers.get_by_interval(delivery).value)


def test_two_points_are_the_former_two_vertices():
    tn, market, asset = build(2, [thermostat('HP1')])
    asset.schedule(market)
    ti = market.time_intervals[0]
    curve = asset.active_vertices.get_by_interval(ti)
    flexibility = asset.model_flexibility(asset._model_params(ti, None))
    assert [v.price for v in curve.vertices] == pytest.approx([0.04, 0.06])
    assert [v.quantity for v in curve.vertices] == pytest.approx([min(flexibility), max(flexibility)])
    assert [v.quantity for v in curve.vertices] == pytest.approx([-9.16, -7.16])


def test_one_point_is_inelastic_at_the_first_set_point_whatever_the_price():
    tn, market, asset = build(1, [thermostat('HP1'), light('L1')])
    asset.schedule(market)
    ti = market.time_intervals[0]
    curve = asset.active_vertices.get_by_interval(ti)
    assert len(curve) == 1 and curve.vertices[0].quantity == pytest.approx(-9.16 - 1.2), 'ratio 0: most consumption'
    for price in (0.0, 0.05, 0.10):
        market.marginal_prices.update(ti, price)
        asset.schedule_power(market)
        assert asset.scheduled_powers.get_by_interval(ti).value == pytest.approx(curve.vertices[0].quantity)


def test_every_model_sweeps_the_linspace_of_its_set_point_range():
    tn, market, asset = build(5, [thermostat('HP1'), light('L1')])
    ti = market.time_intervals[0]
    assert asset.sweep_ratios() == [0.0, 0.25, 0.5, 0.75, 1.0]
    for ratio in asset.sweep_ratios():
        points = asset.set_points_at_ratio(ratio, asset._model_params(ti, None))
        assert points == {'HP1': pytest.approx(20.8 + ratio * 4.0), 'L1': pytest.approx(0.6 - ratio * 0.2)}
    params = asset._model_params(ti, None)
    summed = asset.model_flexibility(params)
    for i, ratio in enumerate(asset.sweep_ratios()):
        assert summed[i] == pytest.approx(asset.model_power_at(params, asset.set_points_at_ratio(ratio, params)))
    # The thermostat's consumption falls with its set point and the light's rises with its level; each is mapped in the
    # direction in which it consumes less at a higher price, so the summed curve consumes less at every higher ratio.
    assert all(a < b for a, b in zip(summed, summed[1:]))
    assert summed[0] == pytest.approx(-9.16 - 1.2) and summed[-1] == pytest.approx(-7.16 - 0.8)


def test_a_thermostat_whose_regression_slope_is_positive_is_mapped_the_other_way():
    """A regression with c1 > 0 says the unit consumes more at a higher set point (the deployed office has such hours).
    The mapping then lowers the set point as the price rises, and two points still reproduce the former sorted pair."""
    positive = thermostat('HP1')
    positive['model_config']['c1'] = [0.05] * 24
    positive['model_config']['c4'] = [-1.5] * 24  # duty cycle 0.05 csp + 0.456 - 1.5: 0.0 at 20.8, 0.196 at 24.8
    tn, market, asset = build(2, [positive])
    asset.schedule(market)
    ti = market.time_intervals[0]
    params = asset._model_params(ti, None)
    assert asset.set_points_at_ratio(0.0, params) == {'HP1': pytest.approx(24.8)}
    assert asset.set_points_at_ratio(1.0, params) == {'HP1': pytest.approx(20.8)}
    quantities = [v.quantity for v in asset.active_vertices.get_by_interval(ti).vertices]
    assert quantities == pytest.approx([-1.96, 0.0]), 'most consumption at the low price, as the sorted pair had it'
