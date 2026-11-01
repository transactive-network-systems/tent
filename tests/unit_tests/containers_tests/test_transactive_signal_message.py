"""
The transactive signal wire format (audit T-SEC-01 approach, A-11): TransactiveSignalMessage validates everything a
node receives before anything is looked up, the sending side serialises through it, an infinite price travels as null,
and the receiving neighbor drops what does not fit with a logged reason.
"""
import json
import logging
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from tent.containers import TimeInterval, Vertex
from tent.containers.transactive_signal import TransactiveSignal
from tent.containers.transactive_signal_message import (IntervalMessage, TransactiveSignalMessage, VertexMessage,
                                                        PROTOCOL_VERSION)
from tent.enumerations import MeasurementUnit, SignalDirection
from tent.market import Market
from tent.market.market_series import MarketSeries
from tent.neighbor import Neighbor
from tent.utils.helpers import json_dumps, json_encoder
from tent.utils.timer import Timer
from tests.mock_tn import MockTN

T0 = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
INF = math.inf
SERIES = dict(name='Day Ahead', duration_market_lead=300.0, duration_delivery_lead=300.0, interval_duration=3600.0,
              intervals_to_clear=2, market_clearing_interval=3600.0, default_price=0.05)


class RecordingDataManager:
    def __init__(self):
        self.records = []

    def archive_data(self, records):
        self.records.extend(records if isinstance(records, (list, tuple)) else [records])


def build_node(name, peer):
    tn = MockTN(name=name)
    tn._data_manager = RecordingDataManager()
    tn.neighbors = [Neighbor(transactive_node=tn, name=peer, transactive=True, up_or_down='upstream',
                             default_vertices=[[0.04, 0.0, 0.0, True], [0.08, 0.0, 1000.0, True]],
                             minimum_power=0.0, maximum_power=1000.0, cost_parameters=[0.0, 0.0, 0.0])]
    tn.market_series = [MarketSeries(transactive_node=tn, market_class=Market, **SERIES)]
    return tn


@pytest.fixture
def clock():
    Timer.simulation = False
    state = {'t': T0}
    with patch.object(Timer, 'now', side_effect=lambda *a, **k: state['t']):
        yield state


@pytest.fixture
def pair(clock):
    campus, city = build_node('campus', 'city'), build_node('city', 'campus')
    campus.connect_peer(city)
    city.connect_peer(campus)
    return campus, city


def market_of(tn):
    return tn.market_series[0].newest_market


def prepared_signal(sender, sequence=1, in_reply_to=None, revised=True):
    """A signal from `sender` to its neighbor: a two-vertex curve in the first interval, a lone inelastic vertex in the
    second, balance points marked on both."""
    market, neighbor = market_of(sender), sender.neighbors[0]
    signal = TransactiveSignal(market=market, neighbor=neighbor, sequence=sequence, in_reply_to=in_reply_to,
                               revised=revised)
    first, second = market.time_intervals
    signal.update(first, [Vertex(0.04, 0.0, 0.0), Vertex(0.08, 0.0, 1000.0)], archive=False, replace=True)
    signal.update(second, [Vertex(INF, 0.0, 250.0)], archive=False, replace=True)
    for ti, value in ((first, -500.0), (second, -250.0)):  # the neighbor's power as seen from here: it supplies
        neighbor.scheduled_powers.update(ti, value, archive=False)
    signal.mark_balance_points(neighbor.scheduled_powers)
    return signal


def valid_message(market_name, start_time, neighbor='campus', **overrides):
    message = {'protocol_version': PROTOCOL_VERSION, 'market': market_name, 'neighbor': neighbor,
               'signal_time': T0.isoformat(), 'sequence': 1, 'in_reply_to': None, 'revised': True, 'unit': 'kW',
               'intervals': {start_time.isoformat(): {
                   'balance_point': 0,
                   'flexibility': [{'marginal_price': 0.1, 'quantity': 0.0, 'cost': 0.0, 'continuity': True,
                                    'uncertainty': 0.0}]}}}
    message.update(overrides)
    return message


# --- sending ----------------------------------------------------------------------------------------------------------

def test_generate_signal_emits_the_wire_form_and_only_json(pair):
    campus, _ = pair
    wire = prepared_signal(campus).generate_signal()
    assert wire['protocol_version'] == 1.0 and wire['market'] == market_of(campus).name and wire['neighbor'] == 'campus'
    assert wire['signal_time'] == T0.isoformat().replace('+00:00', 'Z') or wire['signal_time'] == T0.isoformat()
    assert (wire['sequence'], wire['in_reply_to'], wire['revised'], wire['unit']) == (1, None, True, 'kW')
    first, second = market_of(campus).time_intervals
    intervals = {datetime.fromisoformat(k): v for k, v in wire['intervals'].items()}
    assert set(intervals) == {first.start_time, second.start_time}
    curve = intervals[first.start_time]
    assert [v['quantity'] for v in curve['flexibility']] == [0.0, 500.0, 1000.0], 'the balance point was added'
    assert curve['balance_point'] == 1
    lone = intervals[second.start_time]
    assert lone == {'balance_point': 0, 'flexibility': [{'marginal_price': None, 'quantity': 250.0, 'cost': 0.0,
                                                          'continuity': True, 'uncertainty': 0.0}]}, \
        'an inelastic vertex carries a null price: JSON has no infinity'
    text = json_dumps(wire)
    assert 'Infinity' not in text and 'NaN' not in text
    assert TransactiveSignalMessage.model_validate(json.loads(text)).to_wire() == wire


def test_generate_signal_refuses_prices_without_a_wire_form(pair):
    campus, _ = pair
    for bad in (-INF, math.nan):
        signal = prepared_signal(campus)
        first = market_of(campus).time_intervals[0]
        signal.update(first, [Vertex(bad, 0.0, 0.0), Vertex(0.08, 0.0, 1000.0)], archive=False, replace=True)
        with pytest.raises(ValueError, match='cannot be sent'):
            signal.generate_signal()


def test_generate_signal_requires_a_sequence_number(pair):
    campus, _ = pair
    with pytest.raises(ValueError, match='no sequence number'):
        prepared_signal(campus, sequence=None).generate_signal()


# --- receiving --------------------------------------------------------------------------------------------------------

def test_a_signal_round_trips_between_nodes(pair):
    campus, city = pair
    sent = prepared_signal(campus, sequence=3, in_reply_to=2, revised=False)
    campus.issue_signal('city', sent.generate_signal())
    city_neighbor = city.neighbors[0]
    received = city_neighbor.last_received_signals[market_of(city)]
    assert received.direction is SignalDirection.received and received.neighbor is city_neighbor
    assert (received.sequence, received.in_reply_to, received.revised) == (3, 2, False)
    assert received.signal_time == T0 and received.unit is MeasurementUnit.kW
    first, second = market_of(city).time_intervals
    assert [(v.price, v.quantity) for v in received.get_by_interval(first).vertices] == \
           [(0.04, 0.0), (0.06, 500.0), (0.08, 1000.0)]
    assert received.balance_point(first).quantity == 500.0
    lone = received.get_by_interval(second).vertices[0]
    assert lone.price == INF and lone.quantity == 250.0, 'null on the wire is +inf in memory'
    assert received.balance_point(second) is lone
    assert any(r.get('table_name') == 'transactive_signal' for r in city._data_manager.records), 'archived'


def test_the_wire_form_is_plain_json_as_a_bus_would_deliver_it(pair):
    campus, city = pair
    campus.issue_signal('city', json.loads(json.dumps(prepared_signal(campus).generate_signal())))
    assert market_of(city) in city.neighbors[0].last_received_signals


def test_a_signal_from_another_sender_is_dropped(pair, caplog):
    campus, city = pair
    message = prepared_signal(campus).generate_signal()
    message['neighbor'] = 'impostor'
    with caplog.at_level(logging.WARNING):
        city.neighbors[0].receive_transactive_signal(None, None, message)
    assert not city.neighbors[0].last_received_signals
    assert "naming 'impostor' as its sender" in caplog.text


def test_a_signal_for_an_unknown_market_is_dropped(pair, caplog):
    campus, city = pair
    message = prepared_signal(campus).generate_signal()
    message['market'] = 'Nowhere_2026-10-31T12:00:00+00:00'
    with caplog.at_level(logging.WARNING):
        city.neighbors[0].receive_transactive_signal(None, None, message)
    assert not city.neighbors[0].last_received_signals and 'unknown market' in caplog.text


def test_a_signal_with_an_interval_the_market_lacks_is_dropped_whole(pair, caplog):
    campus, city = pair
    message = prepared_signal(campus).generate_signal()
    stray = (T0 + timedelta(days=30)).isoformat()
    message['intervals'][stray] = deepcopy(next(iter(message['intervals'].values())))
    with caplog.at_level(logging.WARNING):
        city.neighbors[0].receive_transactive_signal(None, None, message)
    assert not city.neighbors[0].last_received_signals, 'nothing of it is kept'
    assert 'is not an interval of market' in caplog.text


def test_a_malformed_signal_is_dropped_with_the_validation_report(pair, caplog):
    campus, city = pair
    message = prepared_signal(campus).generate_signal()
    message['sequence'] = 'first'
    with caplog.at_level(logging.WARNING):
        city.neighbors[0].receive_transactive_signal(None, None, message)
    assert not city.neighbors[0].last_received_signals
    assert 'rejected a malformed transactive signal' in caplog.text and 'sequence' in caplog.text


@pytest.mark.parametrize('junk', [None, 'signal', 42, ['market'], {}])
def test_things_that_are_not_signals_are_dropped_not_raised(pair, junk):
    _, city = pair
    city.neighbors[0].receive_transactive_signal(None, None, junk)
    assert not city.neighbors[0].last_received_signals


def test_parse_signal_raises_with_the_reason(pair):
    campus, city = pair
    message = prepared_signal(campus).generate_signal()
    with pytest.raises(ValueError, match="arrived on the link to 'campus'"):
        TransactiveSignal.parse_signal(city.neighbors[0], dict(message, neighbor='someone'))
    with pytest.raises(ValueError, match='malformed transactive signal'):
        TransactiveSignal.parse_signal(city.neighbors[0], dict(message, protocol_version=2.0))


# --- the model --------------------------------------------------------------------------------------------------------

def test_a_valid_message_validates_and_keeps_its_types():
    start = T0 + timedelta(hours=1)
    message = TransactiveSignalMessage.model_validate(valid_message('M', start))
    assert message.unit is MeasurementUnit.kW and message.signal_time == T0
    assert list(message.intervals) == [start], 'interval keys are aware datetimes'
    vertex = message.intervals[start].flexibility[0]
    assert isinstance(vertex.quantity, float) and vertex.price == 0.1
    assert message.to_wire()['unit'] == 'kW'


def test_the_protocol_version_is_a_number_equal_to_one_point_zero():
    assert TransactiveSignalMessage.model_validate(valid_message('M', T0, protocol_version=1)).protocol_version == 1.0
    for bad in ('1.0', 1.1, 2, 0.9, None, True):
        with pytest.raises(ValidationError, match='protocol_version'):
            TransactiveSignalMessage.model_validate(valid_message('M', T0, protocol_version=bad))


def test_null_price_is_infinity_and_nothing_else_is():
    message = TransactiveSignalMessage.model_validate(valid_message('M', T0))
    vertex = message.intervals[T0].flexibility[0]
    assert VertexMessage.model_validate(dict(vertex.model_dump(), marginal_price=None)).price == INF
    assert VertexMessage.from_price(INF, 1.0, 0.0, True, 0.0).marginal_price is None
    assert VertexMessage.from_price(0.5, 1.0, 0.0, True, 0.0).marginal_price == 0.5
    with pytest.raises(ValueError, match='cannot be sent'):
        VertexMessage.from_price(-INF, 1.0, 0.0, True, 0.0)
    for bad in (INF, -INF, math.nan, 'inf', 'Infinity', '0.1', True):
        with pytest.raises(ValidationError, match='marginal_price'):
            VertexMessage.model_validate(dict(vertex.model_dump(), marginal_price=bad))


@pytest.mark.parametrize('field, bad', [
    ('quantity', INF), ('quantity', math.nan), ('quantity', '5'), ('quantity', None), ('quantity', True),
    ('cost', -INF), ('cost', 'free'),
    ('uncertainty', -0.1), ('uncertainty', math.nan),
    ('continuity', 1), ('continuity', 'true'), ('continuity', None),
])
def test_vertex_fields_are_finite_numbers_of_the_right_kind(field, bad):
    good = {'marginal_price': 0.1, 'quantity': 1.0, 'cost': 0.0, 'continuity': True, 'uncertainty': 0.0}
    with pytest.raises(ValidationError, match=field):
        VertexMessage.model_validate(dict(good, **{field: bad}))
    with pytest.raises(ValidationError, match='extra'):
        VertexMessage.model_validate(dict(good, price=0.1))


@pytest.mark.parametrize('field, bad', [
    ('market', ''), ('market', 7), ('market', None), ('market', 'x' * 256),
    ('neighbor', ''), ('neighbor', ['campus']),
    ('signal_time', '2026-10-31T12:00:00'), ('signal_time', 'noon'), ('signal_time', 1761912000), ('signal_time', None),
    ('sequence', 0), ('sequence', -1), ('sequence', '1'), ('sequence', 1.0), ('sequence', None), ('sequence', True),
    ('in_reply_to', 0), ('in_reply_to', '2'), ('in_reply_to', 2.5),
    ('revised', 'yes'), ('revised', 1), ('revised', None),
    ('unit', 4), ('unit', 'MW'), ('unit', 'kw'), ('unit', None),
    ('intervals', []), ('intervals', None), ('intervals', {'2026-10-31T12:00:00': {'balance_point': None, 'flexibility': []}}),
])
def test_header_fields_are_checked(field, bad):
    with pytest.raises(ValidationError, match=field):
        TransactiveSignalMessage.model_validate(valid_message('M', T0, **{field: bad}))


def test_no_field_may_be_missing_and_none_may_be_added():
    for field in valid_message('M', T0):
        message = valid_message('M', T0)
        del message[field]
        with pytest.raises(ValidationError, match=field):
            TransactiveSignalMessage.model_validate(message)
    with pytest.raises(ValidationError, match='extra'):
        TransactiveSignalMessage.model_validate(valid_message('M', T0, sender='campus'))


def test_interval_curves_are_lists_with_a_balance_point_index_in_range():
    vertex = {'marginal_price': 0.1, 'quantity': 0.0, 'cost': 0.0, 'continuity': True, 'uncertainty': 0.0}
    assert IntervalMessage.model_validate({'balance_point': None, 'flexibility': [vertex]}).balance_point is None
    assert IntervalMessage.model_validate({'balance_point': 1, 'flexibility': [vertex, vertex]}).balance_point == 1
    for bad in ({'balance_point': None, 'flexibility': {0: vertex}},  # the former dictionary form
                {'balance_point': None, 'flexibility': []},
                {'balance_point': 1, 'flexibility': [vertex]},
                {'balance_point': -1, 'flexibility': [vertex]},
                {'balance_point': '0', 'flexibility': [vertex]},
                {'balance_point': 0.0, 'flexibility': [vertex]},
                {'flexibility': [vertex]},
                {'balance_point': 0, 'flexibility': [vertex], 'cleared': True}):
        with pytest.raises(ValidationError):
            IntervalMessage.model_validate(bad)


def test_oversized_signals_are_refused_before_they_are_unpacked():
    vertex = {'marginal_price': 0.1, 'quantity': 0.0, 'cost': 0.0, 'continuity': True, 'uncertainty': 0.0}
    IntervalMessage.model_validate({'balance_point': None, 'flexibility': [vertex] * IntervalMessage.MAX_VERTICES})
    with pytest.raises(ValidationError, match=f'at most {IntervalMessage.MAX_VERTICES}'):
        IntervalMessage.model_validate({'balance_point': None, 'flexibility': [vertex] * (IntervalMessage.MAX_VERTICES + 1)})
    interval = {'balance_point': None, 'flexibility': [vertex]}
    too_many = {(T0 + timedelta(minutes=i)).isoformat(): interval for i in range(TransactiveSignalMessage.MAX_INTERVALS + 1)}
    with pytest.raises(ValidationError, match=f'at most {TransactiveSignalMessage.MAX_INTERVALS}'):
        TransactiveSignalMessage.model_validate(valid_message('M', T0, intervals=too_many))


def test_interval_keys_in_another_zone_name_the_same_instant():
    pacific = T0.astimezone(timezone(timedelta(hours=-7))).isoformat()
    message = valid_message('M', T0)
    message['intervals'] = {pacific: next(iter(message['intervals'].values()))}
    assert list(TransactiveSignalMessage.model_validate(message).intervals) == [T0]


# --- JSON helpers (A-11) ---------------------------------------------------------------------------------------------

def test_json_dumps_refuses_non_finite_numbers_and_keeps_the_encoder():
    assert json.loads(json_dumps({'t': T0, 'unit': MeasurementUnit.kW, 'd': timedelta(minutes=5)})) == \
           {'t': T0.isoformat(), 'unit': 'kW', 'd': 300.0}
    for bad in (INF, -INF, math.nan):
        with pytest.raises(ValueError):
            json_dumps({'price': bad})
    assert json.dumps({'price': INF}) == '{"price": Infinity}', 'which is what the plain library would have written'
    with pytest.raises(TypeError):
        json_encoder(object())


# --- comparing balance points (T-13) ---------------------------------------------------------------------------------

def test_balance_points_differ_measures_vertices_not_intervals(pair):
    campus, _ = pair
    market, neighbor = market_of(campus), campus.neighbors[0]
    first, second = market.time_intervals
    sent = prepared_signal(campus)  # 3 vertices with the balance point at (0.06, 500) in first; a lone (inf, 250) in second

    def received_with(lone_quantity):
        # What the neighbor states from its side: quantities carry the opposite sign.
        signal = TransactiveSignal(market=market, neighbor=neighbor, direction=SignalDirection.received, sequence=1)
        signal.update(first, [Vertex(0.04, 0.0, 0.0), Vertex(0.06, 0.0, -500.0), Vertex(0.08, 0.0, -1000.0)],
                      archive=False, replace=True)
        signal.update(second, [Vertex(INF, 0.0, lone_quantity)], archive=False, replace=True)
        signal.mark_balance_points(SimpleNamespace(all=[(first, SimpleNamespace(value=500.0)),
                                                        (second, SimpleNamespace(value=-lone_quantity))]))
        return signal

    agreeing = received_with(-250.0)
    assert agreeing.balance_point(first).quantity == -500.0
    assert not TransactiveSignal.balance_points_differ(sent, agreeing, first, 0.01)
    assert not TransactiveSignal.balance_points_differ(sent, agreeing, second, 0.01), 'a lone vertex is its balance point'
    assert TransactiveSignal.balance_points_differ(sent, received_with(-400.0), second, 0.01)
    # A multi-vertex curve without a marked balance point cannot be compared: it differs. An interval one side lacks differs.
    unmarked = TransactiveSignal(market=market, neighbor=neighbor, direction=SignalDirection.received, sequence=1)
    unmarked.update(first, [Vertex(0.04, 0.0, 0.0), Vertex(0.08, 0.0, -1000.0)], archive=False, replace=True)
    assert TransactiveSignal.balance_points_differ(sent, unmarked, first, 0.01)
    assert TransactiveSignal.balance_points_differ(sent, unmarked, second, 0.01)
