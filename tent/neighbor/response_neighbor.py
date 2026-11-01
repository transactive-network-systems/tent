"""
A neighbor that states only a price or only a quantity and exchanges no transactive signals.

Counterparties in an existing market system often do not negotiate: a utility publishes a dynamic price or a tariff, a
demand-response program issues an instruction ("hold imports at 50 kW"), a distribution operator sets an import cap.
`ResponseNeighbor` turns such a stated value into the degenerate supply or demand curve that TENT's interpolation
clears against exactly, so no iteration with the counterparty is needed:

- the **price form** is a horizontal two-vertex curve at the stated price spanning the neighbor's power limits (any
  quantity between `minimum_power` and `maximum_power` is available at that price), the shape `BulkSupplierDC` uses
  for the utility tariff;
- the **quantity form** is a single inelastic vertex at the stated quantity. With a `curtailment_reward` ($/kWh) the
  curve is instead horizontal at the reward from `minimum_power` to the stated quantity: every kilowatt-hour imported
  below the instruction forgoes the reward, so the local system curtails when its own flexibility values power below
  the reward and otherwise imports exactly the stated quantity. Imports above the stated quantity are not available.

Quantities are stated from this node's side, as every neighbor's are: positive means power imported from the
counterparty. Losses (`loss_factor`) and demand charges (`demand_rate`) are applied to the built curve exactly as
`Neighbor.update_vertices` applies them to any non-transactive neighbor; set both to zero when the stated price is the
whole price.

The values arrive through the node's ordinary subscription mechanism on `subscribe_key`, as either a list of entries
`{"start_time": <aware ISO 8601>, "value": <number>}` or a single number that applies to every interval. Entries are
kept by start time, so later messages add to or override earlier ones, and a value holds until an entry with a later
start time changes it. A single number replaces the whole schedule. An interval that starts before the earliest entry
has no value: that is logged as an error once per market and interval, and the interval is left without a curve
(`Market.sum_vertices` leaves such an entity out of the system curve with a warning) and with a scheduled power of
zero. A malformed message is a ValueError.

On every message the neighbor rebuilds its curves for every market of the node that has not yet reached DeliveryLead
and calls `market.on_local_change(self)`, so a `ConsensusMarket` rebalances and renegotiates on its next tick. An
`Auction` takes the new value up when it next schedules this neighbor (the base `on_local_change` does nothing).
"""
import logging
import math
from typing import Literal
from datetime import datetime, timezone

from pydantic import model_validator
from sortedcontainers import SortedDict

from tent.containers import SDCurve, Vertex
from tent.enumerations import MarketState
from tent.neighbor import Neighbor
from tent.utils.helpers import require_aware
from tent.config import ConfigModel, Setting

_log = logging.getLogger(__name__)

RESPONSE_TYPES = ('price', 'quantity')

# The start time under which a single-number message is stored: it precedes every interval.
_FROM_THE_BEGINNING = datetime.min.replace(tzinfo=timezone.utc)

# States in which a market no longer schedules its entities; a new value changes nothing for it.
_SETTLED_STATES = frozenset({MarketState.DeliveryLead, MarketState.Delivery, MarketState.Reconcile,
                             MarketState.Expired})


class ResponseNeighbor(Neighbor):
    """A non-transactive neighbor that states only a price or only a quantity (see the module docstring)."""

    class Config(Neighbor.Config):
        response_type: Literal['price', 'quantity'] = Setting(
            ..., description="What the counterparty states: 'price' ($/kWh, a flat curve across the power range) or "
                             "'quantity' (kW, an inelastic vertex).")
        subscribe_key: str = Setting(..., min_length=1,
                                     description='Subscription key on which the stated schedule arrives: a list of '
                                                 '{"start_time", "value"} entries or a single number for every interval.')
        curtailment_reward: float | None = Setting(None, ge=0, reconfigurable=True,
                                                   description='$/kWh paid for curtailing to the stated quantity '
                                                               '(quantity form only): a price step at the quantity.')

        @model_validator(mode='after')
        def _not_transactive(self):
            if self.transactive:
                raise ValueError('a ResponseNeighbor neither sends nor accepts transactive signals; transactive must '
                                 'be false')
            if self.curtailment_reward is not None and self.response_type != 'quantity':
                raise ValueError('curtailment_reward applies to the quantity form only')
            return self

    def __init__(self, transactive_node, config: ConfigModel | None = None, **kwargs):
        cfg = self._configure(config, kwargs)
        super(ResponseNeighbor, self).__init__(transactive_node, config=cfg)
        self.response_type: str = cfg.response_type
        self.subscribe_key: str = cfg.subscribe_key
        self.curtailment_reward: float | None = cfg.curtailment_reward
        if (self.response_type == 'price' or self.curtailment_reward is not None) and not self.has_power_range:
            what = 'the price form' if self.response_type == 'price' else 'a curtailment reward'
            raise ValueError(f'ResponseNeighbor {self.name}: {what} needs a power range, minimum_power below '
                             f'maximum_power; got {self.minimum_power} and {self.maximum_power}.')
        # The stated schedule: value by the aware start time from which it holds (see value_for).
        self.stated_values: SortedDict = SortedDict()
        # (market name, interval name) pairs whose missing value has been reported.
        self._missing_value_reported: set[tuple[str, str]] = set()

        transactive_node.register_subscription(subscribe_key=self.subscribe_key, callback=self.receive_schedule)

    @property
    def has_power_range(self) -> bool:
        return self.minimum_power < self.maximum_power

    # ------------------------------------------------------------------------------------------------ the schedule
    def receive_schedule(self, time_received: datetime, subscribe_key: str, message) -> None:
        """
        Subscription callback (time received, key, message). The message is a list of {"start_time", "value"} entries,
        merged into the schedule by start time, or a single number that replaces it. Then every market that is still
        scheduling has its curves rebuilt and is told of the change.
        """
        entries = self.parse_schedule(message)
        if _FROM_THE_BEGINNING in entries:
            self.stated_values.clear()
        self.stated_values.update(entries)
        _log.info(f'{self.tn.name}: {self.name} states {self.response_type} '
                  f'{ {k.isoformat(): v for k, v in entries.items()} } (received {time_received}).')
        for series in self.tn.market_series:
            for market in series:
                if market.market_state in _SETTLED_STATES:
                    continue
                self.update_vertices(market)
                market.on_local_change(self)

    def parse_schedule(self, message) -> dict[datetime, float]:
        """The entries of a schedule message by aware start time; a bare number is one entry from the beginning of
        time. Anything else is a ValueError naming the neighbor and the fault."""
        who = f'ResponseNeighbor {self.name} ({self.response_type} on {self.subscribe_key!r})'
        if self._is_number(message):
            return {_FROM_THE_BEGINNING: self._checked_value(float(message), who)}
        if not isinstance(message, list) or not message:
            raise ValueError(f'{who}: a schedule is a non-empty list of {{"start_time", "value"}} entries or a single '
                             f'number, not {message!r}.')
        entries: dict[datetime, float] = {}
        for entry in message:
            if not isinstance(entry, dict) or set(entry) != {'start_time', 'value'}:
                raise ValueError(f'{who}: each schedule entry must have exactly the keys "start_time" and "value", '
                                 f'not {entry!r}.')
            start_time = entry['start_time']
            if isinstance(start_time, str):
                try:
                    start_time = datetime.fromisoformat(start_time)
                except ValueError as error:
                    raise ValueError(f'{who}: start_time {entry["start_time"]!r} is not ISO 8601: {error}') from error
            start_time = require_aware(start_time, f'{who}: start_time')
            if not self._is_number(entry['value']):
                raise ValueError(f'{who}: value must be a number, not {entry["value"]!r}.')
            if start_time in entries:
                raise ValueError(f'{who}: start_time {start_time.isoformat()} appears twice in one message.')
            entries[start_time] = self._checked_value(float(entry['value']), who)
        return entries

    @staticmethod
    def _is_number(value) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def _checked_value(self, value: float, who: str) -> float:
        if not math.isfinite(value):
            raise ValueError(f'{who}: value must be finite, not {value!r}.')
        if self.response_type == 'quantity' and self.has_power_range and \
                not self.minimum_power <= value <= self.maximum_power:
            raise ValueError(f'{who}: a stated quantity must lie within the power limits '
                             f'[{self.minimum_power}, {self.maximum_power}] kW; got {value}.')
        return value

    def value_for(self, start_time: datetime) -> float | None:
        """The value in force at start_time: the entry with the latest start time not after it, or None before the
        first entry."""
        index = self.stated_values.bisect_right(start_time)
        return None if index == 0 else self.stated_values.peekitem(index - 1)[1]

    # ------------------------------------------------------------------------------------------------ scheduling
    def update_vertices(self, market):
        """Build each interval's curve from the value in force at its start; an interval before the first value is
        reported once per market and left without a curve."""
        active_threshold = self.demand_threshold
        for time_interval in market.time_intervals:
            value = self.value_for(time_interval.start_time)
            if value is None:
                key = (market.name, time_interval.name)
                if key not in self._missing_value_reported:
                    self._missing_value_reported.add(key)
                    _log.error(f'{self.tn.name}: {self.name} has no stated {self.response_type} for interval '
                               f'{time_interval.name} of {market.name}; the schedule on {self.subscribe_key!r} starts '
                               f'later. The interval has no curve for this neighbor.')
                continue
            sd_curve = self._build_curve(time_interval, value)
            if self.loss_factor != 0:
                self.include_marginal_losses(sd_curve)
            if self.demand_rate != 0:
                self.include_demand_charges(sd_curve=sd_curve, threshold=active_threshold)
                active_threshold = self._get_new_threshold(sd_curve, active_threshold, market, time_interval)
            self.active_vertices.update(time_interval, sd_curve, archive=False, replace=True)

    def _build_curve(self, time_interval, value: float) -> SDCurve:
        a0 = self.cost_parameters[0]
        if self.response_type == 'price':
            vertices = [Vertex(price=value, quantity=self.minimum_power, cost=a0 + value * self.minimum_power),
                        Vertex(price=value, quantity=self.maximum_power, cost=a0 + value * self.maximum_power)]
        elif self.curtailment_reward is not None and value > self.minimum_power:
            reward = self.curtailment_reward
            vertices = [Vertex(price=reward, quantity=self.minimum_power, cost=a0),
                        Vertex(price=reward, quantity=value, cost=a0 + reward * (value - self.minimum_power))]
        else:
            vertices = [Vertex(price=float('inf'), quantity=value, cost=a0)]
        return SDCurve(measurement=None, interval=time_interval, vertices=vertices)

    def schedule_power(self, market):
        """As Neighbor.schedule_power (the market's assigned power where it has balanced against this curve, which
        settles the quantity on the flat segments these curves have, otherwise the curve at the price), except that an
        interval without a curve (no stated value yet) schedules zero so the market's sums still add up; it contributes
        nothing to the system curve."""
        for time_interval in market.time_intervals:
            if self.active_vertices.get_by_interval(time_interval) is None:
                self.scheduled_powers.update(time_interval, 0.0)
                continue
            self.scheduled_powers.update(time_interval, self.market_power(market, time_interval))

    # ------------------------------------------------------------------------------------------------ no signals
    def prep_transactive_signal(self, market):
        _log.info(f'{self.name} is a ResponseNeighbor; it prepares no transactive signal for {market.name}.')
        return None

    def send_transactive_signal(self, market, include_schedule=False):
        _log.info(f'{self.name} is a ResponseNeighbor; no transactive signal is sent for {market.name}.')
        return None

    def receive_transactive_signal(self, _, __, signal_dict: dict, **___):
        _log.info(f'{self.name} is a ResponseNeighbor; a transactive signal addressed to it is ignored.')
        return None

    # ------------------------------------------------------------------------------------------------ archive
    @property
    def table_name(self):
        return 'response_neighbor'

    def get_record(self) -> dict:
        record = super(ResponseNeighbor, self).get_record()
        record.update({
            'response_type': self.response_type,
            'subscribe_key': self.subscribe_key,
            'curtailment_reward': self.curtailment_reward,
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(ResponseNeighbor, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "response_neighbor",
                "inherits": "neighbor",
                "columns": [
                    {"name": "id", "foreign_key": "neighbor.id", "kwargs": {"primary_key": True}},
                    {"name": "response_type", "type": "String", "type_args": [16]},
                    {"name": "subscribe_key", "type": "String", "type_args": [255]},
                    {"name": "curtailment_reward", "type": "Float"},
                ]
            }
        ])
        return definitions
