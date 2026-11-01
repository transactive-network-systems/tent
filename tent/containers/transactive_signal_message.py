"""
The transactive signal as it travels between nodes: protocol version 1.0.

A transactive signal is one node's statement to one neighbor about one market: for each of the market's time intervals
it covers, the supply/demand curve the sender offers the neighbor (its flexibility vertices) and, when the sender has
scheduled a power with the neighbor, which vertex is that balance point. TransactiveSignalMessage is the only form a
signal takes on the wire. The sender builds one from its in-memory TransactiveSignal and serialises it; the receiver
validates whatever arrives against it before looking anything up, and rejects anything that does not fit.

Wire form (JSON object; every key required, no others allowed)::

    {
      "protocol_version": 1.0,                       exactly 1.0, as a number
      "market": "Day_Ahead_2026-10-31T12:00:00+00:00",  the market's name, as the sender knows it
      "neighbor": "campus",                          the sending node's name
      "signal_time": "2026-10-31T11:45:00+00:00",    when the sender prepared it; ISO 8601 with a UTC offset
      "sequence": 3,                                 1, 2, 3 ... per sender, neighbor and market
      "in_reply_to": 2,                              the sequence of the last signal received from the neighbor, or null
      "revised": true,                               false when the content repeats the previous signal (an acknowledgement)
      "unit": "kW",                                  the quantity unit, by MeasurementUnit name
      "intervals": {
        "2026-10-31T12:00:00+00:00": {               the interval's start time; ISO 8601 with a UTC offset
          "balance_point": 1,                        index into flexibility, or null when nothing is scheduled
          "flexibility": [
            {"marginal_price": 0.03, "quantity": -50.0, "cost": 0.0, "continuity": true, "uncertainty": 0.0},
            {"marginal_price": 0.05, "quantity": 20.0, "cost": 0.0, "continuity": true, "uncertainty": 0.0}
          ]
        }
      }
    }

Numbers are JSON numbers: the receiver does not accept numbers written as strings, nor booleans for numbers, nor
times written as numbers.
Quantities, costs and uncertainties are finite. A marginal price is finite or null: JSON has no infinity, and the price
of an inelastic vertex (a lone vertex at a fixed quantity) is infinite in memory, so `null` on the wire means +inf and
nothing else does. A negative infinity or a NaN cannot be sent. Signals are capped at MAX_INTERVALS intervals and
MAX_VERTICES_PER_INTERVAL vertices per interval so that an oversized message is refused before it is unpacked.
"""
import math

from datetime import datetime
from typing import Any, ClassVar

from pydantic import (AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr,
                      field_serializer, field_validator, model_validator)

from tent.enumerations import MeasurementUnit

PROTOCOL_VERSION = 1.0


def _iso_8601_or_datetime(value, name: str):
    """Times are written out as ISO 8601 text (or are datetimes already, on the sending side); a number is not a time."""
    if not isinstance(value, (str, datetime)):
        raise ValueError(f'{name} must be an ISO 8601 string, not {value!r}')
    return value


def _finite(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f'{name} must be finite, not {value!r}')
    return value


class VertexMessage(BaseModel):
    """One vertex of an interval's curve. marginal_price None stands for +inf (an inelastic vertex)."""
    model_config = ConfigDict(extra='forbid')

    marginal_price: StrictFloat | StrictInt | None
    quantity: StrictFloat | StrictInt
    cost: StrictFloat | StrictInt
    continuity: StrictBool
    uncertainty: StrictFloat | StrictInt

    @field_validator('marginal_price')
    @classmethod
    def _price_finite_or_null(cls, value):
        return None if value is None else _finite(float(value), 'marginal_price')

    @field_validator('quantity', 'cost')
    @classmethod
    def _finite_number(cls, value, info):
        return _finite(float(value), info.field_name)

    @field_validator('uncertainty')
    @classmethod
    def _finite_non_negative(cls, value):
        value = _finite(float(value), 'uncertainty')
        if value < 0:
            raise ValueError(f'uncertainty must be at least 0, not {value!r}')
        return value

    @property
    def price(self) -> float:
        """The price as held in memory: +inf for a null marginal_price."""
        return math.inf if self.marginal_price is None else self.marginal_price

    @classmethod
    def from_price(cls, price: float, quantity: float, cost: float, continuity: bool, uncertainty: float) -> 'VertexMessage':
        """A vertex record from in-memory values; +inf becomes a null marginal_price, anything else non-finite is an error."""
        if price == math.inf:
            marginal_price = None
        elif math.isfinite(price):
            marginal_price = float(price)
        else:
            raise ValueError(f'A vertex price of {price!r} cannot be sent: only finite prices and +inf (inelastic) have '
                             f'a wire form.')
        return cls(marginal_price=marginal_price, quantity=float(quantity), cost=float(cost), continuity=bool(continuity),
                   uncertainty=float(uncertainty))


class IntervalMessage(BaseModel):
    """One interval's curve: its vertices and which of them, if any, is the balance point."""
    model_config = ConfigDict(extra='forbid')

    MAX_VERTICES: ClassVar[int] = 64

    balance_point: StrictInt | None
    flexibility: list[VertexMessage] = Field(min_length=1)

    @model_validator(mode='after')
    def _balance_point_in_range_and_size(self):
        if len(self.flexibility) > self.MAX_VERTICES:
            raise ValueError(f'an interval carries {len(self.flexibility)} vertices; at most {self.MAX_VERTICES} are '
                             f'accepted')
        if self.balance_point is not None and not 0 <= self.balance_point < len(self.flexibility):
            raise ValueError(f'balance_point {self.balance_point} is not an index into the {len(self.flexibility)} '
                             f'flexibility vertices')
        return self


class TransactiveSignalMessage(BaseModel):
    """The transactive signal wire format, protocol 1.0. See the module docstring for the field meanings."""
    model_config = ConfigDict(extra='forbid')

    MAX_INTERVALS: ClassVar[int] = 4096

    protocol_version: StrictFloat
    market: StrictStr = Field(min_length=1, max_length=255)
    neighbor: StrictStr = Field(min_length=1, max_length=255)
    signal_time: AwareDatetime
    sequence: StrictInt = Field(ge=1)
    in_reply_to: StrictInt | None = Field(ge=1)
    revised: StrictBool
    unit: MeasurementUnit
    intervals: dict[AwareDatetime, IntervalMessage]

    @field_validator('protocol_version')
    @classmethod
    def _protocol_version_is_exactly_1_0(cls, value):
        if value != PROTOCOL_VERSION:
            raise ValueError(f'unsupported protocol version {value!r}; this node speaks {PROTOCOL_VERSION}')
        return value

    @field_validator('unit', mode='before')
    @classmethod
    def _unit_by_name(cls, value):
        if isinstance(value, MeasurementUnit):
            return value
        if isinstance(value, str) and value in MeasurementUnit.__members__:
            return MeasurementUnit[value]
        raise ValueError(f'unit must be a MeasurementUnit name ({", ".join(MeasurementUnit.__members__)}), '
                         f'not {value!r}')

    @field_serializer('unit')
    def _unit_as_name(self, unit: MeasurementUnit) -> str:
        return unit.name

    @field_validator('signal_time', mode='before')
    @classmethod
    def _time_is_written_out(cls, value):
        return _iso_8601_or_datetime(value, 'signal_time')

    @field_validator('intervals', mode='before')
    @classmethod
    def _interval_count_and_keys(cls, value):
        if isinstance(value, dict):
            if len(value) > cls.MAX_INTERVALS:
                raise ValueError(f'a signal carries {len(value)} intervals; at most {cls.MAX_INTERVALS} are accepted')
            for key in value:
                _iso_8601_or_datetime(key, 'an interval start time')
        return value

    def to_wire(self) -> dict[str, Any]:
        """The JSON-ready dictionary form: ISO 8601 times, unit by name, null for an infinite price."""
        return self.model_dump(mode='json')
