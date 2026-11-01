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
    'ResponseNeighbor': 'tent.neighbor.response_neighbor:ResponseNeighbor',
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
