"""ResponseNeighbor: configuration, schedule messages, the curves each form builds, and the archive row."""
import logging
from datetime import datetime, timedelta, timezone

import pytest

from tent.neighbor import Neighbor
from tent.neighbor.response_neighbor import ResponseNeighbor
from tent.registry import resolve
from tests.mock_tn import MockTN
from tests.stubs import ConstantPriceModel, make_market

T0 = datetime(2017, 7, 1, 0, 0, tzinfo=timezone.utc)
INF = float('inf')


class RecordingDataManager:
    def __init__(self):
        self.records = []

    def archive_data(self, records):
        self.records.extend(records if isinstance(records, (list, tuple)) else [records])

    def by_table(self, name):
        return [r['data'] for r in self.records if r.get('table_name') == name]


def node_and_market(intervals=2):
    tn = MockTN(name='campus')
    tn._data_manager = RecordingDataManager()
    market = make_market(tn, T0, price_model=ConstantPriceModel(0.05), intervals_to_clear=intervals)
    return tn, market


def price_neighbor(tn, **overrides):
    kwargs = dict(transactive_node=tn, name='utility', response_type='price', subscribe_key='utility/price',
                  minimum_power=0.0, maximum_power=1000.0, loss_factor=0.0, demand_rate=0.0,
                  cost_parameters=[0.0, 0.0, 0.0])
    kwargs.update(overrides)
    neighbor = ResponseNeighbor(**kwargs)
    tn.neighbors = [neighbor]
    return neighbor


def quantity_neighbor(tn, **overrides):
    kwargs = dict(transactive_node=tn, name='dr_program', response_type='quantity', subscribe_key='dr/instruction',
                  minimum_power=0.0, maximum_power=1000.0, loss_factor=0.0, demand_rate=0.0,
                  cost_parameters=[0.0, 0.0, 0.0])
    kwargs.update(overrides)
    neighbor = ResponseNeighbor(**kwargs)
    tn.neighbors = [neighbor]
    return neighbor


def points(sd_curve):
    return [(v.price, v.quantity) for v in sd_curve.vertices]


class TestConfiguration:
    def test_is_registered_as_a_shipped_neighbor_class(self):
        assert resolve('ResponseNeighbor', Neighbor) is ResponseNeighbor

    def test_is_never_transactive(self):
        tn, _ = node_and_market()
        neighbor = price_neighbor(tn)
        assert neighbor.transactive is False
        with pytest.raises(ValueError, match='transactive must be false'):
            price_neighbor(tn, transactive=True)

    def test_response_type_must_be_price_or_quantity(self):
        tn, _ = node_and_market()
        with pytest.raises(ValueError, match="response_type must be one of"):
            price_neighbor(tn, response_type='curve')

    def test_subscribe_key_is_required(self):
        tn, _ = node_and_market()
        with pytest.raises(ValueError, match='subscribe_key'):
            price_neighbor(tn, subscribe_key='')

    def test_price_form_needs_a_power_range(self):
        tn, _ = node_and_market()
        with pytest.raises(ValueError, match='needs a power range'):
            price_neighbor(tn, minimum_power=0.0, maximum_power=0.0)

    def test_curtailment_reward_belongs_to_the_quantity_form_and_needs_a_range(self):
        tn, _ = node_and_market()
        with pytest.raises(ValueError, match='quantity form only'):
            price_neighbor(tn, curtailment_reward=0.1)
        with pytest.raises(ValueError, match='needs a power range'):
            quantity_neighbor(tn, curtailment_reward=0.1, minimum_power=0.0, maximum_power=0.0)
        with pytest.raises(ValueError, match='finite non-negative'):
            quantity_neighbor(tn, curtailment_reward=-0.1)
        assert quantity_neighbor(tn, curtailment_reward=0.1).curtailment_reward == 0.1

    def test_subscribes_to_the_configured_key(self):
        tn, _ = node_and_market()
        neighbor = price_neighbor(tn, subscribe_key='tariff/today')
        assert tn.subscriptions['tariff/today'] == neighbor.receive_schedule


class TestScheduleMessages:
    def test_a_single_number_applies_from_the_beginning_of_time(self):
        tn, _ = node_and_market()
        neighbor = price_neighbor(tn)
        neighbor.receive_schedule(T0, 'utility/price', 0.07)
        assert neighbor.value_for(datetime(1990, 1, 1, tzinfo=timezone.utc)) == 0.07
        assert neighbor.value_for(T0 + timedelta(days=400)) == 0.07

    def test_entries_merge_by_start_time_and_a_number_replaces_them(self):
        tn, _ = node_and_market()
        neighbor = price_neighbor(tn)
        neighbor.receive_schedule(T0, 'utility/price', [{'start_time': T0.isoformat(), 'value': 0.06},
                                                        {'start_time': (T0 + timedelta(hours=2)).isoformat(),
                                                         'value': 0.08}])
        neighbor.receive_schedule(T0, 'utility/price', [{'start_time': (T0 + timedelta(hours=2)).isoformat(),
                                                         'value': 0.09}])
        assert neighbor.value_for(T0 + timedelta(hours=1)) == 0.06, 'an earlier entry survives a later message'
        assert neighbor.value_for(T0 + timedelta(hours=2)) == 0.09, 'the same start time is overridden'
        neighbor.receive_schedule(T0, 'utility/price', 0.05)
        assert neighbor.value_for(T0 + timedelta(hours=2)) == 0.05, 'a bare number replaces the schedule'
        assert len(neighbor.stated_values) == 1

    def test_a_value_holds_until_changed_and_is_absent_before_the_first_entry(self):
        tn, _ = node_and_market()
        neighbor = price_neighbor(tn)
        neighbor.receive_schedule(T0, 'utility/price', [{'start_time': T0.isoformat(), 'value': 0.06},
                                                        {'start_time': (T0 + timedelta(hours=3)).isoformat(),
                                                         'value': 0.08}])
        assert neighbor.value_for(T0 - timedelta(seconds=1)) is None
        assert neighbor.value_for(T0) == 0.06
        assert neighbor.value_for(T0 + timedelta(hours=2, minutes=59)) == 0.06
        assert neighbor.value_for(T0 + timedelta(hours=3)) == 0.08
        assert neighbor.value_for(T0 + timedelta(days=30)) == 0.08

    def test_datetime_start_times_are_accepted_as_well_as_strings(self):
        tn, _ = node_and_market()
        neighbor = price_neighbor(tn)
        neighbor.receive_schedule(T0, 'utility/price', [{'start_time': T0, 'value': 0.06}])
        assert neighbor.value_for(T0) == 0.06

    @pytest.mark.parametrize('message, fault', [
        ([], 'non-empty list'),
        ({'start_time': '2017-07-01T00:00:00+00:00', 'value': 1}, 'non-empty list'),
        ('0.07', 'non-empty list'),
        (True, 'non-empty list'),
        ([{'start_time': '2017-07-01T00:00:00+00:00'}], 'exactly the keys'),
        ([{'start_time': '2017-07-01T00:00:00+00:00', 'value': 1, 'unit': 'kW'}], 'exactly the keys'),
        ([{'start_time': '2017-07-01T00:00:00', 'value': 1}], 'timezone-aware'),
        ([{'start_time': 'tomorrow', 'value': 1}], 'not ISO 8601'),
        ([{'start_time': '2017-07-01T00:00:00+00:00', 'value': '0.07'}], 'must be a number'),
        ([{'start_time': '2017-07-01T00:00:00+00:00', 'value': True}], 'must be a number'),
        ([{'start_time': '2017-07-01T00:00:00+00:00', 'value': float('nan')}], 'must be finite'),
        (float('inf'), 'must be finite'),
        ([{'start_time': '2017-07-01T00:00:00+00:00', 'value': 1},
          {'start_time': '2017-07-01T00:00:00Z', 'value': 2}], 'appears twice'),
    ])
    def test_a_malformed_message_is_an_error_and_changes_nothing(self, message, fault):
        tn, _ = node_and_market()
        neighbor = price_neighbor(tn)
        neighbor.receive_schedule(T0, 'utility/price', 0.05)
        with pytest.raises(ValueError, match=fault):
            neighbor.receive_schedule(T0, 'utility/price', message)
        assert neighbor.value_for(T0) == 0.05

    def test_a_stated_quantity_must_lie_within_a_configured_power_range(self):
        tn, _ = node_and_market()
        neighbor = quantity_neighbor(tn, minimum_power=0.0, maximum_power=100.0)
        with pytest.raises(ValueError, match='within the power limits'):
            neighbor.receive_schedule(T0, 'dr/instruction', 150.0)
        with pytest.raises(ValueError, match='within the power limits'):
            neighbor.receive_schedule(T0, 'dr/instruction', -1.0)
        neighbor.receive_schedule(T0, 'dr/instruction', 100.0)
        # Without a range (the 0.0 defaults) any finite quantity is accepted, an export instruction included.
        unbounded = quantity_neighbor(tn, minimum_power=0.0, maximum_power=0.0)
        unbounded.receive_schedule(T0, 'dr/instruction', -250.0)
        assert unbounded.value_for(T0) == -250.0


class TestCurves:
    def test_price_form_is_a_flat_two_vertex_curve_across_the_power_limits(self):
        tn, market = node_and_market()
        neighbor = price_neighbor(tn, minimum_power=-200.0, maximum_power=1000.0)
        neighbor.receive_schedule(T0, 'utility/price', 0.07)
        neighbor.schedule(market)
        for interval in market.time_intervals:
            curve = neighbor.active_vertices.get_by_interval(interval)
            assert points(curve) == [(0.07, -200.0), (0.07, 1000.0)]
            assert [v.cost for v in curve.vertices] == pytest.approx([0.07 * -200.0, 0.07 * 1000.0])
        # Scheduling against a price below the stated one takes the minimum, at or above it the maximum.
        assert neighbor.production(0.05, market.time_intervals[0]) == -200.0
        assert neighbor.production(0.07, market.time_intervals[0]) == 1000.0

    def test_quantity_form_is_one_inelastic_vertex(self):
        tn, market = node_and_market()
        neighbor = quantity_neighbor(tn)
        neighbor.receive_schedule(T0, 'dr/instruction', 50.0)
        neighbor.schedule(market)
        for interval in market.time_intervals:
            assert points(neighbor.active_vertices.get_by_interval(interval)) == [(INF, 50.0)]
            assert neighbor.scheduled_powers.get_by_interval(interval).value == 50.0

    def test_curtailment_reward_is_a_step_from_the_minimum_power_to_the_stated_quantity(self):
        tn, market = node_and_market()
        neighbor = quantity_neighbor(tn, curtailment_reward=0.10)
        neighbor.receive_schedule(T0, 'dr/instruction', 50.0)
        neighbor.schedule(market)
        interval = market.time_intervals[0]
        curve = neighbor.active_vertices.get_by_interval(interval)
        assert points(curve) == [(0.10, 0.0), (0.10, 50.0)]
        assert [v.cost for v in curve.vertices] == pytest.approx([0.0, 5.0])
        assert neighbor.production(0.09, interval) == 0.0, 'below the reward the whole instruction is curtailed'
        assert neighbor.production(0.11, interval) == 50.0, 'above it the stated quantity is imported, never more'
        # An instruction at the minimum power leaves nothing to curtail: an inelastic vertex.
        neighbor.receive_schedule(T0, 'dr/instruction', 0.0)
        neighbor.schedule(market)
        assert points(neighbor.active_vertices.get_by_interval(interval)) == [(INF, 0.0)]

    def test_each_interval_takes_the_value_in_force_at_its_start(self):
        tn, market = node_and_market(intervals=3)
        neighbor = price_neighbor(tn)
        neighbor.receive_schedule(T0, 'utility/price', [{'start_time': T0.isoformat(), 'value': 0.06},
                                                        {'start_time': (T0 + timedelta(hours=2)).isoformat(),
                                                         'value': 0.09}])
        neighbor.schedule(market)
        prices = [points(neighbor.active_vertices.get_by_interval(ti))[0][0] for ti in market.time_intervals]
        assert prices == [0.06, 0.06, 0.09]

    def test_an_interval_before_the_first_value_has_no_curve_and_is_reported_once_per_market(self, caplog):
        tn, market = node_and_market(intervals=2)
        neighbor = price_neighbor(tn)
        neighbor.receive_schedule(T0, 'utility/price', [{'start_time': (T0 + timedelta(hours=1)).isoformat(),
                                                         'value': 0.06}])
        first, second = market.time_intervals
        with caplog.at_level(logging.ERROR, logger='tent.neighbor.response_neighbor'):
            neighbor.schedule(market)
            neighbor.schedule(market)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1, 'one report per market and interval, however often it schedules'
        assert 'utility' in errors[0].getMessage() and first.name in errors[0].getMessage()
        assert neighbor.active_vertices.get_by_interval(first) is None
        assert points(neighbor.active_vertices.get_by_interval(second)) == [(0.06, 0.0), (0.06, 1000.0)]
        assert neighbor.scheduled_powers.get_by_interval(first).value == 0.0
        # The market sums its curve without this neighbor in the first interval.
        assert points(market.sum_vertices(first)) == []
        assert market.sum_vertices(second).max_quantity() == 1000.0
        # Another market with the same interval start is reported separately.
        other = make_market(tn, T0, price_model=ConstantPriceModel(0.05), intervals_to_clear=2, name='Other')
        with caplog.at_level(logging.ERROR, logger='tent.neighbor.response_neighbor'):
            neighbor.schedule(other)
        assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 2

    def test_losses_and_demand_charges_apply_when_configured(self):
        tn, market = node_and_market()
        neighbor = price_neighbor(tn, loss_factor=0.02, demand_rate=1.0, demand_threshold=500.0)
        neighbor.receive_schedule(T0, 'utility/price', 0.05)
        neighbor.schedule(market)
        curve = neighbor.active_vertices.get_by_interval(market.time_intervals[0])
        assert curve.max_quantity() == pytest.approx(1000.0 * (1 - 0.02))
        assert min(v.price for v in curve.vertices) == pytest.approx(0.05)
        assert max(v.price for v in curve.vertices) > 1.0, 'the demand rate is added above the threshold'


class TestNoSignals:
    def test_transactive_signal_methods_log_and_return_none(self, caplog):
        tn, market = node_and_market()
        neighbor = price_neighbor(tn)
        with caplog.at_level(logging.INFO, logger='tent.neighbor.response_neighbor'):
            assert neighbor.prep_transactive_signal(market) is None
            assert neighbor.send_transactive_signal(market) is None
            assert neighbor.receive_transactive_signal(None, None, {'anything': 1}) is None
        assert sum('ResponseNeighbor' in r.getMessage() for r in caplog.records) == 3
        assert market not in neighbor.prepared_signals and market not in neighbor.last_sent_signals
        assert market not in neighbor.last_received_signals


class TestArchive:
    def test_the_archive_row_carries_the_response_type(self):
        tn, _ = node_and_market()
        neighbor = quantity_neighbor(tn, curtailment_reward=0.25)
        neighbor.archive()
        rows = tn.data_manager.by_table('response_neighbor')
        assert len(rows) == 1
        row = rows[0]
        assert row['id'] == neighbor.id
        assert row['response_type'] == 'quantity'
        assert row['subscribe_key'] == 'dr/instruction'
        assert row['curtailment_reward'] == 0.25
        assert row['transactive'] is False and row['name'] == 'dr_program'
        assert not tn.data_manager.by_table('neighbor'), 'the subclass table is the row, inheriting the neighbor columns'

    def test_the_table_definition_inherits_neighbor(self):
        definitions = {d['name']: d for d in ResponseNeighbor.get_archive_definitions()}
        assert definitions['response_neighbor']['inherits'] == 'neighbor'
        columns = {c['name'] for c in definitions['response_neighbor']['columns']}
        assert columns == {'id', 'response_type', 'subscribe_key', 'curtailment_reward'}
        assert 'neighbor' in definitions and 'entity' in definitions
