"""
Class resolution for configured components (audit T-SEC-02, T-INC-02, T-21, T-29): the registry of shipped classes,
explicit module_name for classes from elsewhere, the tent.components entry points, the base-class check, and callers
that read their configuration dictionaries without consuming them.
"""
import importlib.metadata
from datetime import timedelta
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest

from tent import registry
from tent.data_manager import DataManager
from tent.local_asset import LocalAsset
from tent.local_asset.actuation_manager import ActuationManager
from tent.local_asset.actuation_manager.null_actuation_manager import NullActuationManager
from tent.local_asset.model_frame.model_frame_asset import ModelFrameAsset
from tent.local_asset.model_frame.models import Model, ModelFrame, UncontrolledLoad
from tent.local_asset.occupancy_manager import OccupancyManager, SimpleOccupancyManager
from tent.market import Market
from tent.market.auction import Auction
from tent.market.price_models import PriceModel
from tent.neighbor import Neighbor
from tent.utils.helpers import get_class_from_spec, spec_kwargs
from tent.utils.timer import Timer
from tests.mock_tn import MockTN


class ProjectAsset(LocalAsset):
    """A class TENT does not ship, reachable only by module_name or an entry point."""


def entry_point(name, value):
    return importlib.metadata.EntryPoint(name=name, value=value, group=registry.ENTRY_POINT_GROUP)


@pytest.fixture
def entry_points():
    """Replace the installed tent.components entry points with the list the test fills in."""
    installed = []
    with patch.object(registry.importlib.metadata, 'entry_points', side_effect=lambda group: list(installed)):
        yield installed


# --- the registry -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('name', sorted(registry.SHIPPED))
def test_every_shipped_name_resolves_to_a_class_of_that_name(name):
    cls = registry.resolve(name, object)
    assert isinstance(cls, type) and cls.__name__ == name


def test_shipped_classes_are_subclasses_of_the_slot_they_are_for():
    assert issubclass(registry.resolve('Auction', Market), Market)
    assert issubclass(registry.resolve('HourlyAveragePriceModel', PriceModel), PriceModel)
    assert issubclass(registry.resolve('SimpleOccupancyManager', OccupancyManager), OccupancyManager)
    assert issubclass(registry.resolve('NullActuationManager', ActuationManager), ActuationManager)
    for model in ('Thermostat', 'Lighting', 'UncontrolledLoad', 'FlexibilityModelFrameAdapter'):
        assert issubclass(registry.resolve(model, Model), Model)


def test_a_class_of_the_wrong_base_is_a_type_error():
    with pytest.raises(TypeError, match='not a subclass of tent.local_asset.LocalAsset'):
        registry.resolve('Neighbor', LocalAsset)
    with pytest.raises(TypeError, match='not a subclass'):
        registry.resolve('ProjectAsset', Neighbor, module_name=__name__)


def test_an_unknown_name_says_where_it_looked():
    with pytest.raises(ValueError) as e:
        registry.resolve('NoSuchAsset', LocalAsset)
    text = str(e.value)
    assert 'NoSuchAsset' in text and 'module_name' in text and registry.ENTRY_POINT_GROUP in text
    assert 'OpenLoopLoadPredictor' in text, 'the shipped names are listed'


def test_no_module_path_is_guessed_from_the_class_name():
    # The former behaviour derived tent.local_asset.project_asset from ProjectAsset; a name that is neither shipped
    # nor registered is an error whatever module its snake-case form would point at.
    with pytest.raises(ValueError, match='not among the classes TENT ships'):
        registry.resolve('ProjectAsset', LocalAsset)


@pytest.mark.parametrize('bad', [None, '', 7])
def test_class_name_must_be_a_non_empty_string(bad):
    with pytest.raises(ValueError, match='class_name must be a non-empty string'):
        registry.resolve(bad, LocalAsset)


def test_module_name_loads_a_class_from_anywhere():
    assert registry.resolve('ProjectAsset', LocalAsset, module_name=__name__) is ProjectAsset
    assert registry.resolve('Auction', Market, module_name='tent.market.auction') is Auction, 'shipped classes too'


def test_module_name_errors_are_explicit():
    with pytest.raises(ImportError, match="importing module 'no.such.module' failed"):
        registry.resolve('ProjectAsset', LocalAsset, module_name='no.such.module')
    with pytest.raises(ValueError, match="has no attribute 'Missing'"):
        registry.resolve('Missing', LocalAsset, module_name=__name__)
    with pytest.raises(ValueError, match='module_name must be a non-empty string'):
        registry.resolve('ProjectAsset', LocalAsset, module_name='')


def test_entry_points_register_names_for_installed_packages(entry_points):
    entry_points.append(entry_point('ProjectAsset', f'{__name__}:ProjectAsset'))
    assert registry.resolve('ProjectAsset', LocalAsset) is ProjectAsset
    with pytest.raises(TypeError, match='not a subclass'):
        registry.resolve('ProjectAsset', Neighbor)


def test_an_entry_point_may_not_shadow_a_shipped_name(entry_points):
    entry_points.append(entry_point('Neighbor', f'{__name__}:ProjectAsset'))
    with pytest.raises(ValueError, match='Give module_name'):
        registry.resolve('Neighbor', Neighbor)
    assert registry.resolve('Neighbor', Neighbor, module_name='tent.neighbor') is Neighbor


def test_two_distributions_registering_one_name_is_an_error(entry_points):
    entry_points.append(entry_point('ProjectAsset', f'{__name__}:ProjectAsset'))
    entry_points.append(entry_point('ProjectAsset', 'tent.neighbor:Neighbor'))
    with pytest.raises(ValueError, match='more than one installed distribution'):
        registry.resolve('ProjectAsset', LocalAsset)


def test_a_broken_entry_point_is_an_import_error(entry_points):
    entry_points.append(entry_point('ProjectAsset', 'no.such.module:ProjectAsset'))
    with pytest.raises(ImportError, match='failed to load'):
        registry.resolve('ProjectAsset', LocalAsset)


# --- the helpers ------------------------------------------------------------------------------------------------------

def test_get_class_from_spec_reads_the_spec_and_leaves_it_alone():
    spec = {'class_name': 'ProjectAsset', 'module_name': __name__, 'name': 'a', 'maximum_power': 1.0}
    before = deepcopy(spec)
    assert get_class_from_spec(spec, LocalAsset) is ProjectAsset
    assert spec == before
    assert spec_kwargs(spec) == {'name': 'a', 'maximum_power': 1.0}
    assert spec_kwargs(spec, 'name') == {'maximum_power': 1.0}
    assert spec == before


def test_get_class_from_spec_errors():
    with pytest.raises(ValueError, match='no class_name'):
        get_class_from_spec({'module_name': __name__}, LocalAsset)
    with pytest.raises(TypeError, match='must be a dictionary'):
        get_class_from_spec(['ProjectAsset'], LocalAsset)
    with pytest.raises(ValueError, match='class_name must be a non-empty string'):
        get_class_from_spec({'class_name': None}, LocalAsset)


# --- the node ---------------------------------------------------------------------------------------------------------

NODE_CONFIG = {
    'data_manager': {'class_name': 'LocalDataManager', 'db_params': {'url': 'sqlite://'}},
    'neighbors': [{'class_name': 'Neighbor', 'name': 'grid', 'transactive': False, 'cost_parameters': [0.0, 0.1, 0.0],
                   'default_vertices': [[0.1, 0.0, -100.0, True], [0.1, 0.0, 100.0, True]],
                   'maximum_power': 100.0, 'minimum_power': -100.0}],
    'local_assets': [{'class_name': 'ProjectAsset', 'module_name': __name__, 'name': 'plant',
                      'cost_parameters': [0.0, 0.0, 0.0], 'maximum_power': 10.0, 'minimum_power': 0.0}],
    'market_series': [{'class_name': 'MarketSeries', 'name': 'Day Ahead', 'market_class_name': 'Auction',
                       'duration_market_lead': 60.0, 'duration_delivery_lead': 60.0, 'interval_duration': 3600.0,
                       'intervals_to_clear': 1, 'market_clearing_interval': 3600.0}],
}


def configure(config):
    Timer.simulation = False
    tn = MockTN()
    with patch('tent.data_manager.local_data_manager.LocalDataManager.init_archive', Mock(return_value=None)):
        tn.configure_node(config)
    return tn


def test_a_node_is_configured_from_names_alone_and_the_configuration_survives():
    config = deepcopy(NODE_CONFIG)
    tn = configure(config)
    assert type(tn.data_manager).__name__ == 'LocalDataManager'
    assert [type(n).__name__ for n in tn.neighbors] == ['Neighbor']
    assert [type(a) for a in tn.local_assets] == [ProjectAsset]
    assert tn.market_series[0].market_class is Auction, 'T-21: the market class resolves without a module name'
    assert config == NODE_CONFIG, 'T-29: nothing was popped from or injected into the configuration'
    again = configure(config)  # the same dictionaries configure a second node
    assert [a.name for a in again.local_assets] == ['plant'] and config == NODE_CONFIG


def test_a_series_without_a_market_class_name_spawns_plain_markets():
    config = deepcopy(NODE_CONFIG)
    del config['market_series'][0]['market_class_name']
    assert configure(config).market_series[0].market_class is Market


def test_a_market_module_without_a_market_class_name_is_refused():
    config = deepcopy(NODE_CONFIG)
    del config['market_series'][0]['market_class_name']
    config['market_series'][0]['market_module_name'] = 'tent.market.auction'
    with pytest.raises(ValueError, match='market_module_name without market_class_name'):
        configure(config)


def test_a_component_of_the_wrong_kind_is_refused_by_slot():
    config = deepcopy(NODE_CONFIG)
    config['local_assets'][0] = dict(config['neighbors'][0], name='not an asset')
    with pytest.raises(TypeError, match='LocalAsset slot'):
        configure(config)


def test_the_data_manager_slot_takes_data_managers_only():
    config = deepcopy(NODE_CONFIG)
    config['data_manager'] = {'class_name': 'Neighbor'}
    with pytest.raises(TypeError, match='DataManager slot'):
        configure(config)
    assert issubclass(registry.resolve('LocalDataManager', DataManager), DataManager)


# --- model frames and their managers ----------------------------------------------------------------------------------

def test_model_frame_models_are_named_by_class_and_their_configuration_is_not_touched():
    model_config = {'uncontrolled_load_schedule': [5.0] * 24}
    config = {'demand_curve_points': 3,
              'models': [{'subscribe_key': 'devices/base', 'class_name': 'UncontrolledLoad', 'model_config': model_config}]}
    frame = ModelFrame(config)
    model = frame.models['devices/base']
    assert isinstance(model, UncontrolledLoad) and model.n_points == 3
    assert model_config == {'uncontrolled_load_schedule': [5.0] * 24}, 'the frame adds its keys to a copy'


def test_model_type_is_no_longer_a_model_key():
    config = {'models': [{'subscribe_key': 'devices/base', 'model_type': 'uncontrolled.UncontrolledLoad',
                          'model_config': {'uncontrolled_load_schedule': [5.0] * 24}}]}
    with pytest.raises(ValueError, match='model_type is not a configuration key'):
        ModelFrame(config)


def test_a_model_must_be_a_model():
    config = {'models': [{'subscribe_key': 'devices/base', 'class_name': 'Neighbor', 'model_config': {}}]}
    with pytest.raises(TypeError, match='Model slot'):
        ModelFrame(config)


def test_model_frame_asset_managers_default_to_the_simple_and_null_classes():
    Timer.simulation = False
    tn = MockTN()
    asset = ModelFrameAsset(transactive_node=tn, name='building', cost_parameters=[0.0, 0.0, 0.0],
                            occupancy_manager={'schedule': {}}, actuation_manager={'control_interval': 30})
    assert isinstance(asset.occupancy_manager, SimpleOccupancyManager)
    assert isinstance(asset.actuation_manager, NullActuationManager)
    assert asset.actuation_manager.control_interval == timedelta(seconds=30) and asset.actuation_manager.tn is tn
    assert asset.actuation_manager.parent is asset


def test_model_frame_asset_managers_are_checked_against_their_bases():
    Timer.simulation = False
    tn = MockTN()
    with pytest.raises(TypeError, match='OccupancyManager slot'):
        ModelFrameAsset(transactive_node=tn, name='b', cost_parameters=[0.0, 0.0, 0.0],
                        occupancy_manager={'class_name': 'NullActuationManager'})
    with pytest.raises(TypeError, match='ActuationManager slot'):
        ModelFrameAsset(transactive_node=tn, name='b', cost_parameters=[0.0, 0.0, 0.0],
                        actuation_manager={'class_name': 'SimpleOccupancyManager'})
