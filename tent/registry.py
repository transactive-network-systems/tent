"""
Where a configured class name becomes a class.

Every configurable slot in a TENT configuration names its class with `class_name`. The name resolves in this order:

1. `module_name` given in the configuration: that module is imported and the class taken from it. This is the path
   for classes TENT does not ship (user and project classes), and it works for shipped classes too.
2. Otherwise the name is looked up among the classes TENT ships (`SHIPPED` below, registered explicitly by name).
3. Otherwise among the `tent.components` entry points of installed distributions (importlib.metadata). An extension
   package registers `MyAsset = "my_package.assets:MyAsset"` under that group and configurations can then name
   `MyAsset` without a `module_name`.

There is no guessing of module paths from class names and no allow-list of module prefixes. Whatever path produced
the class, it must be a subclass of the base the slot expects (a local asset slot takes LocalAsset subclasses, a
market slot takes Market subclasses, and so on); anything else is a TypeError. The module and class are logged when
loaded.
"""
import importlib
import importlib.metadata
import logging

_log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = 'tent.components'

# Class name -> "module:attribute" of every configurable class TENT ships. Lazy: a module is imported only when its
# class is asked for, so the optional simulator back-ends behind the model-frame FMI adapter stay optional.
SHIPPED: dict[str, str] = {
    # Data managers
    'LocalDataManager': 'tent.data_manager.local_data_manager:LocalDataManager',
    'NullDataManager': 'tent.data_manager.null_data_manager:NullDataManager',
    # Market series, markets and price models
    'MarketSeries': 'tent.market.market_series:MarketSeries',
    'Market': 'tent.market:Market',
    'Auction': 'tent.market.auction:Auction',
    'ConsensusMarket': 'tent.market.consensus_market:ConsensusMarket',
    'MatchingEngine': 'tent.market.matching_engine:MatchingEngine',
    'HourlyAveragePriceModel': 'tent.market.price_models.hourly_average_price_model:HourlyAveragePriceModel',
    # Neighbors
    'Neighbor': 'tent.neighbor:Neighbor',
    'BulkSupplierDC': 'tent.neighbor.bulk_supplier_dc:BulkSupplierDC',
    # Local assets
    'LocalAsset': 'tent.local_asset:LocalAsset',
    'OpenLoopLoadPredictor': 'tent.local_asset.openloop_load_predictor:OpenLoopLoadPredictor',
    'SimpleBuilding': 'tent.local_asset.simple_building:SimpleBuilding',
    'SolarPvResource': 'tent.local_asset.solar_pv_resource_model:SolarPvResource',
    'ModelFrameAsset': 'tent.local_asset.model_frame.model_frame_asset:ModelFrameAsset',
    # Model-frame models
    'Thermostat': 'tent.local_asset.model_frame.models.thermostat:Thermostat',
    'Lighting': 'tent.local_asset.model_frame.models.light:Lighting',
    'UncontrolledLoad': 'tent.local_asset.model_frame.models.uncontrolled:UncontrolledLoad',
    'FlexibilityModelFrameAdapter': 'tent.local_asset.model_frame.models.fmi_adapter:FlexibilityModelFrameAdapter',
    # Actuation and occupancy managers
    'DirectRatioActuationManager': 'tent.local_asset.actuation_manager.direct_ratio:DirectRatioActuationManager',
    'ExternalTargetActuationManager':
        'tent.local_asset.actuation_manager.external_target:ExternalTargetActuationManager',
    'NullActuationManager': 'tent.local_asset.actuation_manager.null_actuation_manager:NullActuationManager',
    'SimpleOccupancyManager': 'tent.local_asset.occupancy_manager.simple:SimpleOccupancyManager',
    # Information service models
    'TemperatureForecastModel': 'tent.information_service_model.temperature_forecast_model:TemperatureForecastModel',
    # Meter points
    'MeterPoint': 'tent.meter_point:MeterPoint',
    'PushMeterPoint': 'tent.meter_point.push_meter_point:PushMeterPoint',
    'PullMeterPoint': 'tent.meter_point.pull_meter_point:PullMeterPoint',
}


def entry_point_classes() -> dict[str, importlib.metadata.EntryPoint]:
    """The `tent.components` entry points of the installed distributions, by name. Read on every call: cheap, and a
    cache would hide packages installed after import. Two distributions registering the same name is an error when
    that name is looked up (see resolve)."""
    return {ep.name: ep for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)}


def _duplicate_entry_points(class_name: str) -> list[str]:
    return [f'{ep.value} ({ep.dist.name if ep.dist else "unknown distribution"})'
            for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP) if ep.name == class_name]


def _import_attribute(module_name: str, attribute: str, what: str) -> object:
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(f'Cannot load {what}: importing module {module_name!r} failed: {e}') from e
    try:
        return getattr(module, attribute)
    except AttributeError:
        raise ValueError(f'Cannot load {what}: module {module_name!r} has no attribute {attribute!r}.') from None


def resolve(class_name: str, base: type, module_name: str | None = None) -> type:
    """
    The class named `class_name`, found as described in the module docstring, checked to be a subclass of `base`.

    :raises ValueError: no or empty class_name; a module_name that names no such attribute; a name not shipped by TENT
        and not registered by any installed distribution; a name registered by more than one source.
    :raises ImportError: an explicit module_name that cannot be imported.
    :raises TypeError: the name resolves to something that is not a subclass of `base`.
    """
    if not isinstance(class_name, str) or not class_name:
        raise ValueError(f'Cannot load a class for a {base.__name__} slot: class_name must be a non-empty string, '
                         f'not {class_name!r}.')
    if module_name is not None and (not isinstance(module_name, str) or not module_name):
        raise ValueError(f'Cannot load class {class_name!r}: module_name must be a non-empty string when given, '
                         f'not {module_name!r}.')
    what = f'class {class_name!r} for a {base.__name__} slot'
    if module_name is not None:
        cls = _import_attribute(module_name, class_name, what)
        source = f'module {module_name!r} (configured)'
    else:
        shipped = SHIPPED.get(class_name)
        entry_points = entry_point_classes()
        entry_point = entry_points.get(class_name)
        if shipped is not None and entry_point is not None:
            raise ValueError(f'Cannot load {what}: TENT ships a class of that name and the installed distribution '
                             f'{entry_point.dist.name if entry_point.dist else "?"} also registers one under the '
                             f'{ENTRY_POINT_GROUP!r} entry-point group ({entry_point.value}). Give module_name to say '
                             f'which one is meant.')
        if shipped is not None:
            shipped_module, shipped_attribute = shipped.split(':')
            cls = _import_attribute(shipped_module, shipped_attribute, what)
            source = f'module {shipped_module!r} (shipped with TENT)'
        elif entry_point is not None:
            duplicates = _duplicate_entry_points(class_name)
            if len(duplicates) > 1:
                raise ValueError(f'Cannot load {what}: more than one installed distribution registers that name under '
                                 f'the {ENTRY_POINT_GROUP!r} entry-point group: {duplicates}. Give module_name to say '
                                 f'which one is meant.')
            try:
                cls = entry_point.load()
            except (ImportError, AttributeError) as e:
                raise ImportError(f'Cannot load {what}: the {ENTRY_POINT_GROUP!r} entry point {entry_point.value!r} '
                                  f'failed to load: {e}') from e
            source = f'entry point {entry_point.value!r} of {entry_point.dist.name if entry_point.dist else "?"}'
        else:
            raise ValueError(f'Cannot load {what}: no module_name was given, {class_name!r} is not among the classes '
                             f'TENT ships ({sorted(SHIPPED)}) and no installed distribution registers it under the '
                             f'{ENTRY_POINT_GROUP!r} entry-point group (registered: {sorted(entry_points) or "none"}).'
                             f' Give module_name for a class from elsewhere.')
    if not isinstance(cls, type) or not issubclass(cls, base):
        raise TypeError(f'Cannot use {cls!r} from {source} for a {base.__name__} slot: it is not a subclass of '
                        f'{base.__module__}.{base.__qualname__}.')
    _log.info(f'Loaded {cls.__module__}.{cls.__qualname__} from {source} for a {base.__name__} slot.')
    return cls


# ------------------------------------------------------------------------------------------- bases and the schema

# The base class of every configurable slot, by the name the Config models use in Setting(spec_of=...).
BASES: dict[str, str] = {
    'DataManager': 'tent.data_manager:DataManager',
    'MarketSeries': 'tent.market.market_series:MarketSeries',
    'Market': 'tent.market:Market',
    'PriceModel': 'tent.market.price_models:PriceModel',
    'Neighbor': 'tent.neighbor:Neighbor',
    'LocalAsset': 'tent.local_asset:LocalAsset',
    'Model': 'tent.local_asset.model_frame.models:Model',
    'ActuationManager': 'tent.local_asset.actuation_manager:ActuationManager',
    'OccupancyManager': 'tent.local_asset.occupancy_manager:OccupancyManager',
    'InformationServiceModel': 'tent.information_service_model:InformationServiceModel',
    'MeterPoint': 'tent.meter_point:MeterPoint',
}

# The node's component lists and the base each one's entries must resolve to.
NODE_LISTS: dict[str, str] = {
    'meter_points': 'MeterPoint',
    'information_service_models': 'InformationServiceModel',
    'local_assets': 'LocalAsset',
    'neighbors': 'Neighbor',
    'market_series': 'MarketSeries',
}

JSON_SCHEMA_DIALECT = 'https://json-schema.org/draft/2020-12/schema'


def base_class(name: str) -> type:
    """The base class of the slot `name` (a key of BASES)."""
    try:
        module_name, attribute = BASES[name].split(':')
    except KeyError:
        raise ValueError(f'{name!r} is not a configurable slot; the slots are {sorted(BASES)}.') from None
    return _import_attribute(module_name, attribute, f'the base class of {name!r} slots')


def registered_classes(base: type) -> dict[str, type]:
    """Every class TENT ships or an installed distribution registers (tent.components entry points) that is a subclass
    of `base`, by name. A registered name that fails to load is an error here: the schema must describe what a
    configuration can actually name."""
    classes: dict[str, type] = {}
    for name in list(SHIPPED) + sorted(entry_point_classes()):
        try:
            cls = resolve(name, object)
        except ValueError as error:  # a name both shipped and registered by a distribution
            raise ValueError(f'The configuration schema cannot be built: {error}') from error
        if issubclass(cls, base):
            classes[name] = cls
    return classes


def configuration_schema() -> dict:
    """
    The JSON Schema (draft 2020-12) of a TransactiveNode configuration, assembled from the Config models: the node's
    own settings, and for the data manager, every component list and every nested class specification (price model,
    model-frame models, actuation and occupancy managers) a oneOf over the registered classes of that slot, each
    variant discriminated by its ``class_name`` constant and allowing no undeclared keys. A market series variant
    exists per (series class, market class) pair, discriminated by ``class_name`` and ``market_class_name``, since
    a series entry holds the settings of both.
    """
    from tent.config import SPEC_DEFAULT, SPEC_OF  # local: tent.config imports tent.utils.helpers, which imports this
    from tent.market.market_series import MarketSeries
    from tent.transactive_node import TransactiveNode

    defs: dict[str, dict] = {}

    def model_schema(model: type) -> dict:
        schema = model.model_json_schema(ref_template='#/$defs/{model}')
        for name, definition in schema.pop('$defs', {}).items():
            if name in defs and defs[name] != definition:
                raise ValueError(f'Two different configuration models are both named {name!r}; the schema needs '
                                 f'unique model names.')
            defs.setdefault(name, definition)
        return schema

    def replace_specs(node):
        """Point every nested class specification at the union of the classes its slot takes (the marker stays, for
        the tool)."""
        if isinstance(node, dict):
            if SPEC_OF in node:
                target = spec_union(node[SPEC_OF], node.get(SPEC_DEFAULT))
                if node.get('type') == 'array' or 'items' in node:
                    node['items'] = target
                else:
                    nullable = any(alternative.get('type') == 'null' for alternative in node.get('anyOf', []))
                    node.pop('$ref', None)
                    node.pop('anyOf', None)
                    if nullable:
                        node['anyOf'] = [target, {'type': 'null'}]
                    else:
                        node['$ref'] = target['$ref']
                return
            for value in node.values():
                replace_specs(value)
        elif isinstance(node, list):
            for value in node:
                replace_specs(value)

    def class_name_property(cls: type, const: str | None = None) -> dict:
        return {'const': const or cls.__name__, 'description': 'The class to build.'}

    def module_name_property(cls: type) -> dict:
        return {'type': 'string', 'default': cls.__module__,
                'description': 'Module of the class; needed only for a class TENT does not ship.'}

    def component_variant(cls: type, base_name: str, is_default: bool = False) -> dict:
        """The schema of one class's entry. The default class of a slot (price model, occupancy and actuation manager)
        is also what an entry without class_name names, so for it class_name is not required."""
        schema = model_schema(cls.Config)
        replace_specs(schema)
        properties = {'class_name': class_name_property(cls), 'module_name': module_name_property(cls),
                      **schema.get('properties', {})}
        if is_default:
            properties['class_name'] = {**properties['class_name'], 'default': cls.__name__}
        return {'title': cls.__name__, 'type': 'object', 'additionalProperties': False, 'properties': properties,
                'required': ([] if is_default else ['class_name']) + list(schema.get('required', [])),
                'description': (cls.__doc__ or '').strip()}

    def model_variant(cls: type) -> dict:
        schema = model_schema(cls.Config)
        settings = dict(schema.get('properties', {}))
        settings.pop('subscribe_key', None)
        settings.pop('demand_curve_points', None)
        required = [name for name in schema.get('required', []) if name in settings]
        model_config = {'type': 'object', 'additionalProperties': schema.get('additionalProperties', False),
                        'properties': settings, 'required': required}
        return {'title': cls.__name__, 'type': 'object', 'additionalProperties': False,
                'properties': {'class_name': class_name_property(cls), 'module_name': module_name_property(cls),
                               'subscribe_key': {'type': 'string', 'minLength': 1,
                                                 'description': "Key the node subscribes to for this model's "
                                                                'measurements; unique within the frame.'},
                               'model_config': model_config},
                'required': ['class_name', 'subscribe_key'] + (['model_config'] if required else []),
                'description': (cls.__doc__ or '').strip()}

    def series_variant(series_cls: type, market_cls: type) -> dict:
        variant = component_variant(series_cls, 'MarketSeries')
        market_schema = model_schema(market_cls.Config)
        replace_specs(market_schema)
        variant['title'] = f'{series_cls.__name__} of {market_cls.__name__}'
        variant['properties'].update({
            'market_class_name': class_name_property(market_cls),
            'market_module_name': {'type': 'string', 'default': market_cls.__module__,
                                   'description': 'Module of the market class; needed only for a class TENT does not '
                                                  'ship.'},
            **market_schema.get('properties', {})})
        variant['required'] = ['class_name', 'market_class_name', *variant['required'][1:],
                               *market_schema.get('required', [])]
        return variant

    defaults: dict[str, str | None] = {}

    def spec_union(base_name: str, default_class: str | None = None) -> dict:
        definition_name = f'Any{base_name}'
        if defaults.setdefault(base_name, default_class) != default_class:
            raise ValueError(f'{base_name} slots name different default classes ({defaults[base_name]!r} and '
                             f'{default_class!r}); the schema needs one per slot.')
        if definition_name not in defs:
            defs[definition_name] = {}  # placeholder against recursion while the variants are built
            base = base_class(base_name)
            configurable = {name: cls for name, cls in registered_classes(base).items()
                            if hasattr(cls, 'Config') and cls.declares_config()}
            if base_name == 'Model':
                variants = [model_variant(cls) for cls in configurable.values()]
            elif base_name == 'MarketSeries':
                markets = {name: cls for name, cls in registered_classes(base_class('Market')).items()
                           if cls.declares_config()}
                variants = [series_variant(series_cls, market_cls) for series_cls in configurable.values()
                            for market_cls in markets.values()]
            else:
                variants = [component_variant(cls, base_name, is_default=(name == default_class))
                            for name, cls in configurable.items()]
            defs[definition_name] = {'title': f'Any {base_name}', 'oneOf': variants,
                                     'description': f'A configured {base_name}: one of the registered classes, chosen by '
                                                    f'class_name.'}
        return {'$ref': f'#/$defs/{definition_name}'}

    node = model_schema(TransactiveNode.Config)
    replace_specs(node)
    done: set[str] = set()
    while set(defs) - done:  # nested models (ModelFrameConfigs) hold specifications too; unions add definitions
        for name in sorted(set(defs) - done):
            replace_specs(defs[name])
            done.add(name)
    defs.pop('ClassSpec', None)  # every specification now points at its slot's union
    defs.pop('ModelSpec', None)
    node.update({'$schema': JSON_SCHEMA_DIALECT, 'title': 'TENT node configuration', '$defs': defs})
    return node
