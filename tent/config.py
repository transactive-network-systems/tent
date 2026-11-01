"""
The configuration contract of TENT's configurable classes, built on archivable.config.

Every configurable class (data managers, market series and markets, price models, neighbors, local assets and their
model-frame models, actuation and occupancy managers, information services, meter points, and the TransactiveNode
itself) declares a nested ``Config`` model built on ConfigModel below. The constructor validates its keyword arguments
through that model and reads its settings from the validated instance, so the declaration is the code: a key the model
does not know is an error at configuration time, naming the component and the key, and the JSON Schema the models
produce (tent.registry.configuration_schema) cannot drift from what the constructors accept.

Conventions the models encode, as Pydantic types:

- durations are given in seconds and become ``timedelta`` (Seconds, NonNegativeSeconds, PositiveSeconds);
- a time of day is ``HH:MM`` (or ``HH:MM:SS``) and becomes ``time`` (TimeOfDay);
- enumerations are given by member name (enum_by_name);
- ``cost_parameters`` is exactly three floats, with ``null`` meaning no cost (CostParameters);
- ``default_vertices`` is a list of vertices, each a list ``[price, cost, quantity, continuity, uncertainty]`` (one to
  five values; an infinite price is written ``"inf"``) or a dict with those keys (DefaultVertices);
- datetimes are timezone-aware; a naive one is an error except where the owner documents the zone it assumes;
- other components are referred to by name (NameReferences);
- a nested configurable object is a dict naming its class with ``class_name`` (ClassSpec).

Fields a running component may change in place are marked reconfigurable (``Setting(..., reconfigurable=True)``,
which puts ``{"reconfigurable": true}`` in the field's JSON Schema extra); ``Config.reconfigurable_fields()`` lists
them and ``Configurable.reconfigure`` applies a change to them, refusing anything else.

The generic half of the contract (ConfigModel's validation and error reporting, Configurable's constructor validation
and reconfiguration, the reconfigurable marker and the two errors) is the library's, in archivable.config, so the data
managers it ships are configured the same way; TENT's ConfigModel and Configurable extend them with the nested class
specifications (``class_spec``) and their validation through tent.registry.
"""
from datetime import time, timedelta
from enum import Enum
from math import isinf
from typing import Annotated, Any, ClassVar, Mapping

from pydantic import BeforeValidator, ConfigDict, Field, PlainSerializer, PlainValidator, WithJsonSchema

from archivable.config import RECONFIGURABLE, ConfigurationError, ReconfigurationError, field_extra
from archivable.config import ConfigModel as _ConfigModel
from archivable.config import Configurable as _Configurable
from archivable.config import Setting as _setting

from tent.utils.helpers import parse_time


__all__ = ['ConfigModel', 'NamedConfig', 'ConfigurationError', 'ReconfigurationError', 'Configurable', 'Setting', 'ClassSpec',
           'class_spec', 'spec_fields', 'Seconds', 'NonNegativeSeconds', 'PositiveSeconds', 'TimeOfDay', 'time_of_day',
           'enum_by_name', 'CostParameters', 'DefaultVertices', 'NameReferences', 'Coefficients24', 'RECONFIGURABLE',
           'SPEC_OF', 'SPEC_DEFAULT', 'REFERENCE']

# Keys of Field.json_schema_extra that the models use as markers (RECONFIGURABLE is the library's).
SPEC_OF = 'x-tent-spec'             # Name of the base class a nested class specification must resolve to.
SPEC_DEFAULT = 'x-tent-spec-default'  # Class a nested specification without class_name names (see class_spec).
REFERENCE = 'x-tent-reference'      # Name of the node list whose component names this field refers to.


# ---------------------------------------------------------------------------------------------------------- fields

def Setting(default: Any = ..., *, reconfigurable: bool = False, spec_of: str | None = None,
            spec_default: str | None = None, reference: str | None = None, **field_kwargs: Any) -> Any:
    """
    archivable.config.Setting with TENT's markers in json_schema_extra: ``reconfigurable`` for a setting a running
    component may change in place, ``spec_of`` for a nested class specification (the name of the base class it must
    resolve to, see tent.registry.BASES; ``spec_default`` the class a specification without class_name names),
    ``reference`` for a field naming components of another node list. Everything else (default, description, ge, gt,
    ...) is passed to Field. Units belong in the description, in brackets.
    """
    extra: dict[str, Any] = {}
    if spec_of is not None:
        extra[SPEC_OF] = spec_of
    if spec_default is not None:
        extra[SPEC_DEFAULT] = spec_default
    if reference is not None:
        extra[REFERENCE] = reference
    if extra:
        existing = field_kwargs.pop('json_schema_extra', None) or {}
        extra = {**existing, **extra}
        field_kwargs['json_schema_extra'] = extra
    return _setting(default, reconfigurable=reconfigurable, **field_kwargs)


# ----------------------------------------------------------------------------------------------------------- types

def _timedelta_from_seconds(value: Any) -> timedelta:
    if isinstance(value, timedelta):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'a duration is a number of seconds; got {value!r}')
    if isinf(value) or value != value:
        raise ValueError(f'a duration must be a finite number of seconds; got {value!r}')
    return timedelta(seconds=float(value))


def _non_negative_seconds(value: Any) -> timedelta:
    duration = _timedelta_from_seconds(value)
    if duration < timedelta(0):
        raise ValueError(f'must not be negative; got {_seconds(duration)!r}')
    return duration


def _positive_seconds(value: Any) -> timedelta:
    duration = _timedelta_from_seconds(value)
    if duration <= timedelta(0):
        raise ValueError(f'must be positive; got {_seconds(duration)!r}')
    return duration


def _seconds(duration: timedelta) -> float:
    return duration.total_seconds()


_SECONDS_SCHEMA = {'type': 'number', 'x-tent-unit': 'seconds'}

Seconds = Annotated[timedelta, PlainValidator(_timedelta_from_seconds), PlainSerializer(_seconds, when_used='json'),
                    WithJsonSchema(_SECONDS_SCHEMA)]
"""A duration configured as a number of seconds (a timedelta is accepted as given)."""

NonNegativeSeconds = Annotated[timedelta, PlainValidator(_non_negative_seconds),
                               PlainSerializer(_seconds, when_used='json'),
                               WithJsonSchema({**_SECONDS_SCHEMA, 'minimum': 0})]

PositiveSeconds = Annotated[timedelta, PlainValidator(_positive_seconds), PlainSerializer(_seconds, when_used='json'),
                            WithJsonSchema({**_SECONDS_SCHEMA, 'exclusiveMinimum': 0})]


def time_of_day(value: Any) -> time:
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        raise ValueError(f'a time of day is a string HH:MM; got {value!r}')
    return parse_time(value)


TimeOfDay = Annotated[time, PlainValidator(time_of_day),
                      PlainSerializer(lambda t: t.isoformat(timespec='minutes'), when_used='json'),
                      WithJsonSchema({'type': 'string', 'pattern': r'^\d{1,2}:\d{2}(:\d{2})?$',
                                      'x-tent-format': 'HH:MM'})]
"""A time of day configured as ``HH:MM`` (``HH:MM:SS`` is also read)."""


def enum_by_name(enumeration: type[Enum]) -> Any:
    """A field type for `enumeration` whose configured value is the member's name (a member is accepted as given).
    Integer values are not accepted: the names are the configuration vocabulary and the numbers are storage."""
    names = [member.name for member in enumeration]

    def by_name(value: Any) -> Enum:
        if isinstance(value, enumeration):
            return value
        if isinstance(value, str) and value in enumeration.__members__:
            return enumeration[value]
        raise ValueError(f'must be the name of a {enumeration.__name__} member, one of {names}; got {value!r}')

    return Annotated[enumeration, PlainValidator(by_name), PlainSerializer(lambda m: m.name, when_used='json'),
                     WithJsonSchema({'type': 'string', 'enum': names})]


def _cost_parameters(value: Any) -> tuple[float, float, float]:
    if value is None:
        return (0.0, 0.0, 0.0)
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f'must be a list of exactly three numbers [a0, a1, a2]; got {value!r}')
    if len(value) != 3:
        raise ValueError(f'must have exactly three coefficients [a0, a1, a2]; got {len(value)}: {list(value)!r}')
    coefficients = []
    for coefficient in value:
        if isinstance(coefficient, bool) or not isinstance(coefficient, (int, float)):
            raise ValueError(f'coefficients must be numbers; got {coefficient!r}')
        coefficients.append(float(coefficient))
    return tuple(coefficients)


CostParameters = Annotated[tuple[float, float, float], PlainValidator(_cost_parameters),
                           PlainSerializer(list, when_used='json'),
                           WithJsonSchema({'anyOf': [{'type': 'array', 'items': {'type': 'number'}, 'minItems': 3,
                                                      'maxItems': 3}, {'type': 'null'}]})]
"""Coefficients [a0, a1, a2] of the quadratic production-cost formula a0 + a1*p + 0.5*a2*p^2 [$/h]; null means no
cost (three zeros)."""

_VERTEX_KEYS = ('price', 'cost', 'quantity', 'continuity', 'uncertainty')


def _vertex_number(value: Any, what: str) -> float:
    """A vertex coordinate: a number, or the strings "inf" / "-inf" (JSON has no infinity). Booleans are accepted for
    continuity, which the Vertex constructor reads as a flag."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ('inf', '+inf', 'infinity', '+infinity'):
            return float('inf')
        if text in ('-inf', '-infinity'):
            return float('-inf')
    raise ValueError(f'{what} must be a number (or "inf" / "-inf"); got {value!r}')


def _default_vertices(value: Any) -> list | None:
    """Normalise a configured vertex list: every list becomes a list of floats in Vertex order, every dict keeps its
    keys with numeric values, and a Vertex object (constructed in code) becomes the dict of its coordinates."""
    if value is None:
        return None
    if isinstance(value, (str, bytes, dict)) or not isinstance(value, (list, tuple)):
        raise ValueError(f'must be a list of vertices, each [price, cost, quantity, continuity, uncertainty] or a dict '
                         f'with those keys; got {value!r}')
    from tent.containers.vertex import Vertex  # local: containers import tent.utils.helpers, not this module
    vertices = []
    for index, vertex in enumerate(value):
        if isinstance(vertex, Vertex):
            vertices.append({'price': vertex.price, 'cost': vertex.cost, 'quantity': vertex.quantity,
                             'continuity': vertex.continuity, 'uncertainty': vertex.uncertainty})
        elif isinstance(vertex, (list, tuple)):
            if not 1 <= len(vertex) <= len(_VERTEX_KEYS):
                raise ValueError(f'vertex {index} must have one to five values [price, cost, quantity, continuity, '
                                 f'uncertainty]; got {len(vertex)}: {list(vertex)!r}')
            vertices.append([_vertex_number(v, f'vertex {index} {_VERTEX_KEYS[i]}') for i, v in enumerate(vertex)])
        elif isinstance(vertex, dict):
            unknown = sorted(set(vertex) - set(_VERTEX_KEYS))
            if unknown:
                raise ValueError(f'vertex {index} has unknown keys {unknown}; a vertex dict has {list(_VERTEX_KEYS)}')
            if 'price' not in vertex:
                raise ValueError(f'vertex {index} must have a price')
            vertices.append({key: _vertex_number(v, f'vertex {index} {key}') for key, v in vertex.items()})
        else:
            raise ValueError(f'vertex {index} must be a list [price, cost, quantity, continuity, uncertainty] or a '
                             f'dict with those keys; got {vertex!r}')
    return vertices


def _json_number(value: float) -> float | str:
    if isinstance(value, float) and isinf(value):
        return 'inf' if value > 0 else '-inf'
    return value


def _default_vertices_json(value: list | None):
    if value is None:
        return None
    return [[_json_number(v) for v in vertex] if isinstance(vertex, list)
            else {key: _json_number(v) for key, v in vertex.items()} for vertex in value]


_VERTEX_NUMBER_SCHEMA = {'anyOf': [{'type': 'number'}, {'type': 'string', 'enum': ['inf', '-inf']},
                                   {'type': 'boolean'}]}
DefaultVertices = Annotated[list | None, PlainValidator(_default_vertices),
                            PlainSerializer(_default_vertices_json, when_used='json'),
                            WithJsonSchema({'anyOf': [
                                {'type': 'array', 'items': {'anyOf': [
                                    {'type': 'array', 'items': _VERTEX_NUMBER_SCHEMA, 'minItems': 1, 'maxItems': 5},
                                    {'type': 'object', 'required': ['price'], 'additionalProperties': False,
                                     'properties': {key: _VERTEX_NUMBER_SCHEMA for key in _VERTEX_KEYS}}]}},
                                {'type': 'null'}]})]
"""The vertices of an entity's default supply/demand curve; null means a single inelastic vertex."""


def NameReferences(node_list: str) -> Any:
    """A list of component names from the node list `node_list` (resolved at construction); null means none. Component
    objects are accepted as given, for code that wires components directly."""
    def references(value: Any) -> list | None:
        if value is None:
            return None
        if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError(f'must be a list of {node_list} names; got {value!r}')
        from tent.base.component import Component  # local: component imports this module
        items = list(value)
        if all(isinstance(item, str) for item in items):
            return items
        if all(isinstance(item, Component) for item in items):
            return items
        raise ValueError(f'must be a list of {node_list} names (strings); got {items!r}')

    return Annotated[list | None, PlainValidator(references),
                     PlainSerializer(lambda v: None if v is None else [i if isinstance(i, str) else i.name for i in v],
                                     when_used='json'),
                     WithJsonSchema({'anyOf': [{'type': 'array', 'items': {'type': 'string'}}, {'type': 'null'}],
                                     REFERENCE: node_list})]


Coefficients24 = Annotated[list[float], Field(min_length=24, max_length=24)]
"""One value per hour of the day."""


# ------------------------------------------------------------------------------------------------------ the models

def spec_fields(model: type[_ConfigModel]) -> dict[str, str]:
    """Field name -> base class name (see tent.registry.BASES) of every nested class specification field of `model`
    (any configuration model, the library's included)."""
    return {name: field_extra(info)[SPEC_OF] for name, info in model.model_fields.items()
            if SPEC_OF in field_extra(info)}


class ConfigModel(_ConfigModel):
    """Base of every configuration model TENT declares: the library's ConfigModel (unknown keys are errors, Python
    objects such as components and adapters may be given where code wires things directly, defaults are written in
    configuration form and validated like given values) plus the nested class specification fields."""

    @classmethod
    def spec_fields(cls) -> dict[str, str]:
        """Field name -> base class name (see tent.registry.BASES) of every nested class specification field."""
        return spec_fields(cls)


class NamedConfig(ConfigModel):
    """The two settings every component configured by name has."""
    name: str = Setting('', description="Name of the component; unique among the node's components of its kind, and "
                                        'the name other components refer to it by.')
    description: str = Setting('', description='Free text.')


class ClassSpec(ConfigModel):
    """
    A nested configurable object: ``class_name`` (and ``module_name`` for a class TENT does not ship, see
    tent.registry) with the object's settings alongside. The settings are validated by the named class's own Config
    when the object is built (and beforehand by TransactiveNode.validate_configuration), so they are kept here as
    given.
    """
    model_config = ConfigDict(extra='allow', arbitrary_types_allowed=True)

    class_name: str = Setting(..., min_length=1, description='Name of the class to build.')
    module_name: str | None = Setting(None, min_length=1,
                                      description='Module of the class, for a class TENT does not ship.')

    def settings(self) -> dict:
        """The keys other than class_name and module_name: the constructor keywords."""
        return dict(self.__pydantic_extra__ or {})

    def spec(self) -> dict:
        """The specification as a plain dict (class_name, module_name when given, and the settings)."""
        result = {'class_name': self.class_name}
        if self.module_name is not None:
            result['module_name'] = self.module_name
        result.update(self.settings())
        return result


def class_spec(default_class_name: str | None = None) -> Any:
    """The ClassSpec type for a nested specification field; with `default_class_name`, a dict without class_name
    names that class (the documented default of the slot)."""
    def with_default(value: Any) -> Any:
        if default_class_name is not None and isinstance(value, Mapping) and 'class_name' not in value:
            return {'class_name': default_class_name, **value}
        return value

    return Annotated[ClassSpec, BeforeValidator(with_default)]


# ----------------------------------------------------------------------------------------------------- the mixin

class Configurable(_Configurable):
    """
    The library's Configurable (validation of the keyword arguments in the constructor, the validated configuration as
    ``config``, in-place reconfiguration of the fields the Config marks reconfigurable) for a TENT class: the nested
    class specifications its Config holds are validated against the Config of the class they name, resolved through
    tent.registry, before anything is built (TransactiveNode.validate_configuration).
    """
    Config: ClassVar[type[ConfigModel]] = ConfigModel

    @classmethod
    def validate_nested_specs(cls, cfg: ConfigModel, owner: str) -> None:
        """Validate every nested ClassSpec held by `cfg` (directly, in lists, or inside nested models) against the
        Config of the class it names, resolved through tent.registry against the base its field declares."""
        from tent import registry  # local: registry is imported by tent.utils.helpers, which this module imports
        for field_name, base_name in spec_fields(type(cfg)).items():
            value = getattr(cfg, field_name)
            specs = value if isinstance(value, list) else [value] if value is not None else []
            for index, spec in enumerate(specs):
                if not isinstance(spec, ClassSpec):
                    continue
                where = f'{owner}.{field_name}' + (f'[{index}]' if isinstance(value, list) else '')
                nested_class = registry.resolve(spec.class_name, registry.base_class(base_name), spec.module_name)
                if issubclass(nested_class, _Configurable) and nested_class.declares_config():
                    try:
                        nested_class.validate_spec(spec.spec())
                    except ConfigurationError as error:
                        raise ConfigurationError(f'{where}: {error.owner}', error.problems) from None
        for field_name in type(cfg).model_fields:
            value = getattr(cfg, field_name)
            nested = value if isinstance(value, list) else [value]
            for item in nested:
                if isinstance(item, _ConfigModel) and not isinstance(item, ClassSpec):
                    cls.validate_nested_specs(item, f'{owner}.{field_name}')
