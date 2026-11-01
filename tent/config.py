"""
The configuration contract of TENT's configurable classes.

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
"""
import logging
from datetime import time, timedelta
from enum import Enum
from math import isinf
from typing import Annotated, Any, ClassVar, Mapping

from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer, PlainValidator, ValidationError,
                      WithJsonSchema)

from tent.utils.helpers import parse_time

_log = logging.getLogger(__name__)

__all__ = ['ConfigModel', 'NamedConfig', 'ConfigurationError', 'ReconfigurationError', 'Configurable', 'Setting', 'ClassSpec',
           'class_spec', 'Seconds', 'NonNegativeSeconds', 'PositiveSeconds', 'TimeOfDay', 'time_of_day', 'enum_by_name',
           'CostParameters', 'DefaultVertices', 'NameReferences', 'Coefficients24', 'RECONFIGURABLE', 'SPEC_OF', 'SPEC_DEFAULT',
           'REFERENCE']

# Keys of Field.json_schema_extra that the models use as markers.
RECONFIGURABLE = 'reconfigurable'   # True: a running component may change this field in place.
SPEC_OF = 'x-tent-spec'             # Name of the base class a nested class specification must resolve to.
SPEC_DEFAULT = 'x-tent-spec-default'  # Class a nested specification without class_name names (see class_spec).
REFERENCE = 'x-tent-reference'      # Name of the node list whose component names this field refers to.


# --------------------------------------------------------------------------------------------------------- errors

class ConfigurationError(ValueError):
    """A configuration the models reject. The message names the component (class and configured name) and every
    offending key, so the file can be corrected without reading a traceback."""

    def __init__(self, owner: str, problems: list[str]):
        self.owner = owner
        self.problems = list(problems)
        super().__init__(f'{owner}: ' + '; '.join(self.problems))

    @classmethod
    def from_validation(cls, owner: str, error: ValidationError, model: type[BaseModel]) -> 'ConfigurationError':
        problems = []
        unknown = []
        for detail in error.errors():
            location = '.'.join(str(part) for part in detail['loc'])
            if detail['type'] == 'extra_forbidden':
                unknown.append(location)
            elif detail['type'] == 'missing':
                problems.append(f'{location} is required')
            else:
                message = detail['msg']
                if message.startswith('Value error, '):
                    message = message[len('Value error, '):]
                problems.append(f'{location}: {message}' if location else message)
        if unknown:
            known = sorted(info.alias or name for name, info in model.model_fields.items())
            problems.insert(0, f'unknown configuration key{"s" if len(unknown) > 1 else ""} '
                               f'{", ".join(repr(key) for key in unknown)} (the keys of {model.__qualname__} are '
                               f'{known})')
        return cls(owner, problems)


class ReconfigurationError(ValueError):
    """A change a running component cannot take in place: it names a field that is not reconfigurable (the node must
    be rebuilt for it), or the changed values do not validate."""


# ---------------------------------------------------------------------------------------------------------- fields

def Setting(default: Any = ..., *, reconfigurable: bool = False, spec_of: str | None = None,
            spec_default: str | None = None, reference: str | None = None, **field_kwargs: Any) -> Any:
    """
    Field() with TENT's markers in json_schema_extra: ``reconfigurable`` for a setting a running component may change
    in place, ``spec_of`` for a nested class specification (the name of the base class it must resolve to, see
    tent.registry.BASES; ``spec_default`` the class a specification without class_name names), ``reference`` for a field
    naming components of another node list. Everything else (default,
    description, ge, gt, ...) is passed to Field. Units belong in the description, in brackets.
    """
    extra: dict[str, Any] = {}
    if reconfigurable:
        extra[RECONFIGURABLE] = True
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
    return Field(default, **field_kwargs)


def _field_extra(info) -> dict:
    extra = info.json_schema_extra
    return extra if isinstance(extra, dict) else {}


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

class ConfigModel(BaseModel):
    """Base of every configuration model: unknown keys are errors, Python objects (components, adapters) may be given
    where code wires things directly, defaults are written in configuration form (seconds, member names) and validated
    like given values, and the markers set through Setting() are readable."""
    model_config = ConfigDict(extra='forbid', arbitrary_types_allowed=True, validate_default=True)

    @classmethod
    def reconfigurable_fields(cls) -> frozenset[str]:
        """The fields a running component may change in place."""
        return frozenset(name for name, info in cls.model_fields.items() if _field_extra(info).get(RECONFIGURABLE))

    @classmethod
    def spec_fields(cls) -> dict[str, str]:
        """Field name -> base class name (see tent.registry.BASES) of every nested class specification field."""
        return {name: _field_extra(info)[SPEC_OF] for name, info in cls.model_fields.items()
                if SPEC_OF in _field_extra(info)}

    @classmethod
    def validate_for(cls, owner: str, data: Mapping) -> 'ConfigModel':
        """model_validate(data), reporting failures as a ConfigurationError that names `owner`."""
        if not isinstance(data, Mapping):
            raise ConfigurationError(owner, [f'the configuration must be a dictionary of settings, not '
                                             f'{type(data).__name__}: {data!r}'])
        try:
            return cls.model_validate(dict(data))
        except ValidationError as error:
            raise ConfigurationError.from_validation(owner, error, cls) from None

    def changed_fields(self, other: 'ConfigModel') -> set[str]:
        """The names of the fields whose values differ between this configuration and `other` (same model)."""
        return {name for name in type(self).model_fields if getattr(self, name) != getattr(other, name)}


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

class Configurable:
    """
    What a class with a nested Config model gets: validation of its keyword arguments in the constructor (see
    _configure), the validated configuration as ``config``, and in-place reconfiguration of the fields its Config marks
    reconfigurable (reconfigure). Subclass models extend the parent's model; a subclass constructor validates once and
    hands the validated instance down as ``config=``, so the parent reads the same object instead of validating again.
    """
    Config: ClassVar[type[ConfigModel]] = ConfigModel
    # Keys of a specification dict that are not settings of the Config (consumed by whoever builds the object).
    RUNTIME_SPEC_KEYS: ClassVar[frozenset[str]] = frozenset({'class_name', 'module_name'})

    config: ConfigModel
    config_version: int

    @classmethod
    def declares_config(cls) -> bool:
        """True when this class declares its own Config (as every class TENT ships does, ``Config = Parent.Config``
        when nothing is added). A class from elsewhere that only extends a shipped constructor is validated by the
        shipped base's Config for the keywords that reach it, and is not pre-validated by the node."""
        return 'Config' in vars(cls)

    @classmethod
    def describe(cls, data: Mapping | None = None, name: str | None = None) -> str:
        """'ClassName' or "ClassName 'configured name'" for messages."""
        if name is None and isinstance(data, Mapping):
            name = data.get('name')
        return f'{cls.__name__} {name!r}' if name else cls.__name__

    @classmethod
    def validate_config(cls, data: Mapping, name: str | None = None) -> ConfigModel:
        """The validated Config for the settings `data`; a ConfigurationError names this class and `name`."""
        return cls.Config.validate_for(cls.describe(data, name), data)

    @classmethod
    def validate_spec(cls, spec: Mapping) -> ConfigModel:
        """
        Validate a configuration dictionary that names this class (class_name, module_name and the settings), and
        recursively the nested class specifications its fields hold, without building anything. Used by
        TransactiveNode.validate_configuration to reject a configuration before any component exists.
        """
        settings = {key: value for key, value in spec.items() if key not in cls.RUNTIME_SPEC_KEYS}
        cfg = cls.validate_config(settings)
        cls.validate_nested_specs(cfg, cls.describe(settings))
        return cfg

    @classmethod
    def validate_nested_specs(cls, cfg: ConfigModel, owner: str) -> None:
        """Validate every nested ClassSpec held by `cfg` (directly, in lists, or inside nested models) against the
        Config of the class it names, resolved through tent.registry against the base its field declares."""
        from tent import registry  # local: registry is imported by tent.utils.helpers, which this module imports
        for field_name, base_name in type(cfg).spec_fields().items():
            value = getattr(cfg, field_name)
            specs = value if isinstance(value, list) else [value] if value is not None else []
            for index, spec in enumerate(specs):
                if not isinstance(spec, ClassSpec):
                    continue
                where = f'{owner}.{field_name}' + (f'[{index}]' if isinstance(value, list) else '')
                nested_class = registry.resolve(spec.class_name, registry.base_class(base_name), spec.module_name)
                if issubclass(nested_class, Configurable) and nested_class.declares_config():
                    try:
                        nested_class.validate_spec(spec.spec())
                    except ConfigurationError as error:
                        raise ConfigurationError(f'{where}: {error.owner}', error.problems) from None
        for field_name in type(cfg).model_fields:
            value = getattr(cfg, field_name)
            nested = value if isinstance(value, list) else [value]
            for item in nested:
                if isinstance(item, ConfigModel) and not isinstance(item, ClassSpec):
                    cls.validate_nested_specs(item, f'{owner}.{field_name}')

    def _configure(self, config: ConfigModel | None, kwargs: Mapping) -> ConfigModel:
        """
        The validated configuration of this object: `config` when a subclass constructor already validated it (it must
        then be an instance of this class's Config and `kwargs` must be empty), otherwise `kwargs` validated through
        this class's Config. Stored as ``self.config`` (version 1).
        """
        if config is not None:
            if not isinstance(config, self.Config):
                raise TypeError(f'{type(self).__name__} was given a {type(config).__qualname__} as its configuration; '
                                f'it needs a {self.Config.__qualname__}.')
            if kwargs:
                raise TypeError(f'{type(self).__name__} was given both a validated configuration and keywords '
                                f'{sorted(kwargs)}; give one or the other.')
            cfg = config
        else:
            cfg = self.validate_config(kwargs)
        if getattr(self, 'config', None) is None:
            self.config = cfg
            self.config_version = 1
        return cfg

    # ------------------------------------------------------------------------------------------- reconfiguration

    def check_reconfigure(self, changes: Mapping) -> ConfigModel:
        """
        The configuration this object would have after `changes`, validated, without applying it. Raises
        ReconfigurationError when a changed field is not reconfigurable (listing them) or the new values do not
        validate.
        """
        changes = dict(changes)
        refused = sorted(set(changes) - self.Config.reconfigurable_fields())
        if refused:
            unknown = sorted(set(refused) - set(self.Config.model_fields))
            detail = (f' ({", ".join(repr(k) for k in unknown)} {"are" if len(unknown) > 1 else "is"} not a '
                      f'configuration key at all)' if unknown else '')
            raise ReconfigurationError(
                f'{self.describe(name=getattr(self, "name", None))}: {refused} cannot be changed on a running '
                f'component{detail}; reconfigurable fields are {sorted(self.Config.reconfigurable_fields())}. '
                f'Rebuild the node for other changes.')
        current = self.config.model_dump()
        try:
            return self.validate_config({**current, **changes}, name=getattr(self, 'name', None))
        except ConfigurationError as error:
            raise ReconfigurationError(str(error)) from None

    def reconfigure(self, changes: Mapping) -> None:
        """Change the reconfigurable fields in `changes` in place: validate (check_reconfigure), apply, and record
        the new configuration version."""
        new_config = self.check_reconfigure(changes)
        changed = set(changes)
        self._apply_reconfiguration(new_config, changed)
        self.config = new_config
        self.config_version += 1
        _log.info(f'{self.describe(name=getattr(self, "name", None))} reconfigured {sorted(changed)} '
                  f'(configuration version {self.config_version}).')
        self._configuration_changed(changed)

    def diff_configuration(self, settings: Mapping) -> dict:
        """The changes (field -> new value) that would take this object from its current configuration to `settings`
        (a full set of settings, as configured). Settings `settings` leaves out revert to their defaults."""
        new_config = self.validate_config(settings, name=getattr(self, 'name', None))
        return {name: getattr(new_config, name) for name in self.config.changed_fields(new_config)}

    def _apply_reconfiguration(self, cfg: ConfigModel, changed: set[str]) -> None:
        """Apply the changed fields of `cfg` to this object. The base behaviour sets the attribute of the same name;
        classes that derive other state from a field extend this."""
        for field_name in changed:
            setattr(self, field_name, getattr(cfg, field_name))

    def _configuration_changed(self, changed: set[str]) -> None:
        """Hook after a reconfiguration is applied (components archive the new version here)."""
        return None

    def configuration_record(self) -> dict:
        """The current configuration as JSON-ready data (for the archive and for comparison)."""
        return self.config.model_dump(mode='json')
