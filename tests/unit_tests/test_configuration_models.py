"""
The configuration models (tent.config and the Config of every configurable class): validation of the shipped and
deployed configurations, the value conventions, unknown keys, reconfigurable fields and in-place reconfiguration of
components and of the node, and the JSON Schema export.
"""
import glob
import json
import os
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tent import registry
from tent.config import ConfigurationError, ReconfigurationError
from tent.enumerations import Direction, MarketState, MeasurementType, Method
from tent.local_asset import LocalAsset
from tent.local_asset.model_frame.model_frame_asset import ModelFrameAsset
from tent.local_asset.model_frame.models import Lighting, UncontrolledLoad
from tent.local_asset.occupancy_manager import SimpleOccupancyManager
from tent.market import Market
from tent.market.consensus_market import ConsensusMarket
from tent.market.market_series import MarketSeries
from tent.market.price_models import HourlyAveragePriceModel
from tent.neighbor import Neighbor
from tent.neighbor.bulk_supplier_dc import BulkSupplierDC
from tent.transactive_node import TransactiveNode
from tests.mock_tn import MockTN

REPO = Path(__file__).resolve().parents[2]
SAMPLE_CONFIG = REPO / 'sample_configs' / 'tn.campus.json'


def _container_configs() -> list[str]:
    """The node configurations deployed in the simulation containers: containers/tent/*/agent_configs/tn.*.json in the
    repository that holds this one as a sub-repository (code/TENT). Looked for beside this checkout and, from a git
    worktree, beside the main checkout; TENT_CONTAINERS_DIR names the containers directory directly."""
    candidates = [REPO.parents[1] / 'containers' / 'tent']
    git_file = REPO / '.git'
    if git_file.is_file():  # a worktree: "gitdir: <main checkout>/.git/worktrees/<name>"
        git_dir = Path(git_file.read_text().split(':', 1)[1].strip())
        if '.git' in git_dir.parts:
            main_checkout = Path(*git_dir.parts[:git_dir.parts.index('.git')])
            candidates.append(main_checkout.parents[1] / 'containers' / 'tent')
    if os.environ.get('TENT_CONTAINERS_DIR'):
        candidates.insert(0, Path(os.environ['TENT_CONTAINERS_DIR']))
    for directory in candidates:
        found = sorted(glob.glob(str(directory / '*' / 'agent_configs' / 'tn.*.json')))
        if found:
            return found
    return []


CONTAINER_CONFIGS = _container_configs()


def node_config(**overrides) -> dict:
    """A node configuration exercising every kind of component, archived nowhere."""
    config = {
        'name': 'twin', 'tz': 'UTC', 'actor_id': 'twin-01',
        'data_manager': {'class_name': 'NullDataManager'},
        'meter_points': [{'class_name': 'MeterPoint', 'name': 'Main', 'measurement_type': 'PowerReal',
                          'measurement_unit': 'kW', 'measurement_interval': 900}],
        'information_service_models': [{'class_name': 'TemperatureForecastModel', 'name': 'Weather'}],
        'local_assets': [
            {'class_name': 'LocalAsset', 'name': 'Load', 'default_power': -5.0, 'meter_points': ['Main'],
             'cost_parameters': [0.0, 0.0, 0.0], 'default_vertices': [['inf', 0.0, -5.0, True]]},
            {'class_name': 'ModelFrameAsset', 'name': 'Office', 'minimum_power': -20.0,
             'information_services': ['Weather'], 'temperature_forecast_name': 'Weather',
             'model_configs': {'demand_curve_points': 2, 'models': [
                 {'class_name': 'Lighting', 'subscribe_key': 'lights',
                  'model_config': {'rated_power': 2.0, 'default_lighting_schedule': [1.0] * 24}},
                 {'class_name': 'UncontrolledLoad', 'subscribe_key': 'Uncontrolled',
                  'model_config': {'uncontrolled_load_schedule': [3.0] * 24}}]},
             'occupancy_manager': {'schedule': {}},
             'actuation_manager': {'class_name': 'NullActuationManager', 'control_interval': 30}},
        ],
        'neighbors': [{'class_name': 'Neighbor', 'name': 'CoR', 'transactive': True, 'up_or_down': 'upstream',
                       'maximum_power': 100.0, 'loss_factor': 0.03, 'demand_rate': 4.5,
                       'default_vertices': [[0.04, 0.0, 0.0, True], [0.06, 0.0, 100.0, True]]}],
        'market_series': [{'class_name': 'MarketSeries', 'name': 'Core', 'market_class_name': 'Auction',
                           'duration_market_lead': 600, 'market_clearing_interval': 3600, 'interval_duration': 3600,
                           'intervals_to_clear': 2, 'default_price': 0.05, 'actuate_assets': True}],
    }
    config.update(overrides)
    return config


@pytest.fixture
def node():
    tn = MockTN()
    tn.configure_node(node_config())
    return tn


# --- the shipped and deployed configurations ---------------------------------------------------------------------------

def test_the_sample_configuration_validates_and_configures_a_node(tmp_path):
    with open(SAMPLE_CONFIG) as f:
        configs = json.load(f)
    cfg = TransactiveNode.validate_configuration(configs)
    assert cfg.name == 'PNNL_Campus' and len(cfg.market_series) == 2
    configs['data_manager']['db_params']['url'] = f'sqlite:///{tmp_path / "campus.sqlite"}'
    tn = MockTN()
    tn.configure_node(configs)
    assert [a.name for a in tn.local_assets] == ['InelasticBuildings', 'SolarPv']
    assert tn.market_series[1].refined_series is not None, 'the real-time series refines the day-ahead series'
    rows = tn.data_manager.query_data('component_configuration')
    assert len(rows) >= len(tn.neighbors) + len(tn.local_assets) + len(tn.market_series)
    assert all(row['version'] == 1 for row in rows)


def _component_problems(configs: dict) -> tuple[list[str], list[str]]:
    """(problems the models report, components whose class could not be imported here) for a deployed configuration,
    component by component, so that one host-package class does not hide the rest."""
    problems, unavailable = [], []
    try:
        cfg = TransactiveNode.Config.validate_for('TransactiveNode', configs)
    except ConfigurationError as error:
        return [str(error)], []
    specs = [('data_manager', cfg.data_manager, 'DataManager')] if cfg.data_manager else []
    for list_name, base_name in registry.NODE_LISTS.items():
        specs.extend((f'{list_name}[{i}]', spec, base_name) for i, spec in enumerate(getattr(cfg, list_name)))
    for where, spec, base_name in specs:
        try:
            component_class = registry.resolve(spec.class_name, registry.base_class(base_name), spec.module_name)
        except ImportError:
            unavailable.append(f'{where}: {spec.class_name} from {spec.module_name}')
            continue
        if not component_class.declares_config():
            continue
        try:
            TransactiveNode._validate_component_spec(component_class, spec.spec(), where)
        except ConfigurationError as error:
            problems.append(str(error))
        except ImportError as error:  # a nested specification (actuation manager) names a host-package class
            unavailable.append(f'{where}: {error}')
    return problems, unavailable


KNOWN_DEPLOYED_DEVIATIONS = (
    'every container config gives the TemperatureForecastModel location "" where the model takes a dict or null, and '
    'the LocalDataManager db_params carry db_type, a key TENT never read'
)


@pytest.mark.parametrize('path', CONTAINER_CONFIGS or [None], ids=lambda p: Path(p).name if p else 'none')
def test_deployed_container_configurations_validate(path):
    if path is None:
        pytest.skip('the containers/tent directory is not beside this repository')
    with open(path) as f:
        configs = json.load(f)
    problems, unavailable = _component_problems(configs)
    if problems:
        pytest.fail(f'{Path(path).name}: ' + '; '.join(problems) +
                    (f' (not checked here, host package absent: {unavailable})' if unavailable else ''))


# --- unknown keys and legacy keys -------------------------------------------------------------------------------------

def test_an_unknown_key_fails_at_configuration_naming_the_component_and_the_key():
    tn = MockTN()
    with pytest.raises(ConfigurationError, match="Neighbor 'CoR': unknown configuration key 'loss_factr'"):
        Neighbor(tn, name='CoR', loss_factr=0.1)
    configs = node_config()
    configs['neighbors'][0]['loss_factr'] = 0.1
    with pytest.raises(ConfigurationError, match=r"neighbors\[0\]: Neighbor 'CoR': unknown configuration key 'loss_factr'"):
        TransactiveNode.validate_configuration(configs)
    configs = node_config()
    configs['local_assets'][1]['model_configs']['models'][0]['model_config']['rated_powr'] = 1
    with pytest.raises(ConfigurationError, match=r"local_assets\[1\]: ModelFrameAsset 'Office'.model_configs.models\[0\]: "
                                                 r"Lighting 'lights': unknown configuration key 'rated_powr'"):
        TransactiveNode.validate_configuration(configs)
    configs = node_config()
    configs['market_series'][0]['actuate_asets'] = False
    with pytest.raises(ConfigurationError, match=r"MarketSeries 'Core' \(market settings of Auction\): unknown configuration "
                                                 r"key 'actuate_asets'"):
        TransactiveNode.validate_configuration(configs)


def test_a_rejected_configuration_builds_nothing():
    tn = MockTN()
    configs = node_config()
    configs['market_series'][0]['duration_market_led'] = 1
    with pytest.raises(ConfigurationError, match='duration_market_led'):
        tn.configure_node(configs)
    assert tn.neighbors == [] and tn.local_assets == [] and tn.meter_points == [] and tn.config is None
    assert tn.data_manager.discarded == 0, 'nothing was archived'


def test_legacy_camel_case_keys_are_errors_naming_the_current_key():
    with pytest.raises(ConfigurationError, match="'meterPoints' is not a configuration key; use 'meter_points'"):
        TransactiveNode.validate_configuration({'meterPoints': [], 'markets': []})
    with pytest.raises(ConfigurationError, match="TransactiveNode: unknown configuration key 'agentid'"):
        TransactiveNode.validate_configuration({'agentid': 'x'})


def test_a_name_reference_that_matches_nothing_is_an_error(node):
    with pytest.raises(ValueError, match="LocalAsset 'Late': no meter point named \\['Nope'\\] is configured on the node"):
        LocalAsset(node, name='Late', meter_points=['Nope'])


def test_a_refined_series_is_linked_when_configured_through_the_node():
    configs = node_config()
    configs['market_series'].append({'class_name': 'MarketSeries', 'name': 'Day Ahead', 'market_class_name': 'Auction',
                                     'refined_series': 'Core', 'intervals_to_clear': 24, 'market_clearing_interval': 86400,
                                     'duration_market_lead': 600, 'actuate_assets': False})
    tn = MockTN()
    tn.configure_node(configs)
    day_ahead = next(s for s in tn.market_series if s.name == 'Day Ahead')
    assert day_ahead.refined_series is not None and day_ahead.refined_series.name == 'Core'
    configs['market_series'][1]['refined_series'] = 'Nope'
    with pytest.raises(ValueError, match="refined_series 'Nope' names no market series"):
        MockTN().configure_node(configs)


# --- value conventions ------------------------------------------------------------------------------------------------

def test_durations_in_seconds_become_timedeltas_and_bounds_are_checked(node):
    series = MarketSeries(node, name='S', market_class=Market, duration_market_lead=600, interval_duration=900,
                          intervals_to_clear=4, market_clearing_interval=3600, future_horizon=7200)
    assert series.duration.market_lead == timedelta(minutes=10) and series.duration.market_interval == timedelta(minutes=15)
    assert series.future_horizon == timedelta(hours=2) and series.config.duration_market_lead == timedelta(minutes=10)
    assert MarketSeries(node, name='T', market_class=Market, duration_market_lead=timedelta(minutes=5)).duration.market_lead \
        == timedelta(minutes=5), 'a timedelta is taken as given'
    with pytest.raises(ConfigurationError, match="MarketSeries 'U': duration_market_lead: a duration is a number of seconds; got '600'"):
        MarketSeries(node, name='U', market_class=Market, duration_market_lead='600')
    with pytest.raises(ConfigurationError, match='interval_duration: must be positive; got 0.0'):
        MarketSeries(node, name='U', market_class=Market, interval_duration=0)
    assert node.information_service_models[0].update_period == timedelta(hours=1)
    assert node.meter_points[0].measurement_interval == timedelta(minutes=15)


def test_anchor_times_are_times_of_day(node):
    series = MarketSeries(node, name='S', market_class=Market, delivery_anchor_time='02:00')
    assert series.duration.delivery_anchor_time == time(2, 0) and series.config.delivery_anchor_time == time(2, 0)
    with pytest.raises(ConfigurationError, match='delivery_anchor_time: Unrecognised time of day'):
        MarketSeries(node, name='T', market_class=Market, delivery_anchor_time='two')
    manager = SimpleOccupancyManager({'monday': {'start': '6:00', 'end': '18:00'}, **{d: 'always_off' for d in
                                     ('tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')}})
    assert manager.schedule[0] == {'start': time(6, 0), 'end': time(18, 0)} and manager.schedule[6] == 'always_off'


def test_enumerations_are_given_by_name(node):
    neighbor = Neighbor(node, name='N', up_or_down='downstream')
    assert neighbor.up_or_down is Direction.downstream
    assert Neighbor(node, name='M', up_or_down=Direction.upstream).up_or_down is Direction.upstream
    series = MarketSeries(node, name='S', market_class=Market, initial_market_state='Active', method='Interpolation')
    assert series.initial_market_state is MarketState.Active and series.method is Method.Interpolation
    with pytest.raises(ConfigurationError, match="initial_market_state: must be the name of a MarketState member"):
        MarketSeries(node, name='T', market_class=Market, initial_market_state=1)
    with pytest.raises(ConfigurationError, match="method: must be the name of a Method member.*got 'interpolation'"):
        MarketSeries(node, name='T', market_class=Market, method='interpolation')
    assert node.meter_points[0].measurement_type is MeasurementType.PowerReal


def test_cost_parameters_are_exactly_three_floats_or_null(node):
    assert Neighbor(node, name='A', cost_parameters=[1, 2, 3]).cost_parameters == [1.0, 2.0, 3.0]
    assert Neighbor(node, name='B', cost_parameters=None).cost_parameters == [0.0, 0.0, 0.0]
    assert Neighbor(node, name='C').cost_parameters == [0.0, 0.0, 0.0]
    for bad in ([], [1.0, 2.0], [1.0, 2.0, 3.0, 4.0], 'abc', [1, 'x', 3]):
        with pytest.raises(ConfigurationError, match="Neighbor 'D': cost_parameters"):
            Neighbor(node, name='D', cost_parameters=bad)


def test_default_vertices_are_vertex_lists_or_dicts(node):
    by_list = Neighbor(node, name='A', default_vertices=[['inf', 0, -60.0, True], [0.05, 1, 10]])
    assert by_list.config.default_vertices == [[float('inf'), 0.0, -60.0, 1.0], [0.05, 1.0, 10.0]]
    assert sorted(v.price for v in by_list.default_vertices.vertices) == [0.05, float('inf')]
    by_dict = Neighbor(node, name='B', default_vertices=[{'price': 0.1, 'quantity': 5.0}])
    assert [v.quantity for v in by_dict.default_vertices.vertices] == [5.0]
    assert by_dict.configuration_record()['default_vertices'] == [{'price': 0.1, 'quantity': 5.0}]
    assert by_list.configuration_record()['default_vertices'][0][0] == 'inf', 'infinity is written as "inf"'
    for bad in ([[1, 2, 3, 4, 5, 6]], [{'price': 1, 'colour': 'red'}], [{'quantity': 1}], ['1,2'], 'x'):
        with pytest.raises(ConfigurationError, match="Neighbor 'C': default_vertices"):
            Neighbor(node, name='C', default_vertices=bad)
    none = Neighbor(node, name='D', default_vertices=None)
    assert len(none.default_vertices) == 1 and none.default_vertices.vertices[0].price == float('inf')


def test_datetimes_are_aware_with_the_node_zone_assumed_for_the_simulation_start():
    cfg = TransactiveNode.validate_configuration({'tz': 'America/Los_Angeles', 'simulation': True,
                                                  'simulation_start_time': '2017-07-01T00:00:00'})
    assert cfg.simulation_start_time == datetime(2017, 7, 1, tzinfo=ZoneInfo('America/Los_Angeles'))
    cfg = TransactiveNode.validate_configuration({'tz': 'UTC', 'simulation_start_time': '2017-07-01T00:00:00-07:00',
                                                  'simulation_anchor_time': '2026-10-31 12:00:00'})
    assert cfg.simulation_start_time == datetime(2017, 7, 1, 7, tzinfo=timezone.utc)
    assert cfg.simulation_anchor_time == datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    with pytest.raises(ConfigurationError, match='simulation_start_time: Unrecognised datetime'):
        TransactiveNode.validate_configuration({'simulation_start_time': 'yesterday'})
    with pytest.raises(ConfigurationError, match='tz must be an IANA zone name'):
        TransactiveNode.validate_configuration({'tz': 'PDT'})
    with pytest.raises(ConfigurationError, match="BulkSupplierDC 'B': holidays.0: Input should be a valid date"):
        BulkSupplierDC(MockTN(), name='B', holidays=['2018-13-01'])
    assert BulkSupplierDC(MockTN(), name='B', holidays=['2018-01-01']).holidays == frozenset({date(2018, 1, 1)})


def test_a_price_models_initial_model_must_be_complete():
    model = HourlyAveragePriceModel(forgetting_factor=2, initial_model={h: [0.1, 0.2] for h in range(24)})
    assert list(model.current_model[3]) == [0.1, 0.2] and model.current_model[3].maxlen == 2
    with pytest.raises(ConfigurationError, match='initial_model must have the hours 0 to 23'):
        HourlyAveragePriceModel(forgetting_factor=2, initial_model={0: [0.1, 0.2]})
    with pytest.raises(ConfigurationError, match=r'initial_model must hold forgetting_factor \(2\) prices per hour'):
        HourlyAveragePriceModel(forgetting_factor=2, initial_model={h: [0.1] for h in range(24)})


# --- reconfigurable fields and reconfigure ----------------------------------------------------------------------------

def test_reconfigurable_fields_are_the_costs_limits_thresholds_and_schedules():
    neighbor_fields = Neighbor.Config.reconfigurable_fields()
    assert {'loss_factor', 'demand_rate', 'demand_threshold', 'cost_parameters', 'maximum_power', 'minimum_power',
            'default_vertices', 'convergence_threshold'} <= neighbor_fields
    assert not {'name', 'transactive', 'up_or_down', 'friend', 'mechanism'} & neighbor_fields
    assert MarketSeries.Config.reconfigurable_fields() == {'default_price', 'duality_gap_threshold'}
    assert ConsensusMarket.Config.reconfigurable_fields() == {'max_balance_iterations'}
    assert 'subscribe_key' not in Lighting.Config.reconfigurable_fields()
    assert {'default_lighting_schedule', 'rated_power'} <= Lighting.Config.reconfigurable_fields()
    assert 'schedule' in SimpleOccupancyManager.Config.reconfigurable_fields()
    assert TransactiveNode.Config.reconfigurable_fields() == frozenset(), 'node settings rebuild the node'
    schema = Neighbor.Config.model_json_schema()
    assert schema['properties']['loss_factor']['reconfigurable'] is True
    assert 'reconfigurable' not in schema['properties']['transactive']


def test_a_neighbor_takes_a_new_loss_factor_in_place_and_archives_the_version(tmp_path):
    configs = node_config(data_manager={'class_name': 'LocalDataManager',
                                        'db_params': {'url': f'sqlite:///{tmp_path / "node.sqlite"}'}})
    tn = MockTN()
    tn.configure_node(configs)
    neighbor = tn.neighbors[0]
    assert neighbor.loss_factor == 0.03 and neighbor.config_version == 1
    neighbor.reconfigure({'loss_factor': 0.05, 'demand_rate': 6.0})
    assert neighbor.loss_factor == 0.05 and neighbor.demand_rate == 6.0
    assert neighbor.config.loss_factor == 0.05 and neighbor.config_version == 2
    rows = tn.data_manager.query_data('component_configuration', {'component_id': neighbor.id}, order_by=['version'])
    assert [row['version'] for row in rows] == [1, 2]
    assert json.loads(rows[0]['configuration'])['loss_factor'] == 0.03
    assert json.loads(rows[1]['configuration'])['loss_factor'] == 0.05
    assert rows[1]['class_name'] == 'Neighbor'


def test_a_series_takes_a_new_default_price_and_a_markets_reconfigurable_setting(node):
    series = node.market_series[0]
    series.reconfigure({'default_price': 0.07})
    assert series.default_price == 0.07 and series.price_model_spec['default_price'] == 0.07
    assert series.config.default_price == 0.07 and series.config_version == 2
    consensus = MarketSeries(node, name='Consensus', market_class=ConsensusMarket, max_balance_iterations=5)
    market = consensus.newest_market
    assert market.max_balance_iterations == 5
    consensus.reconfigure({'max_balance_iterations': 9, 'duality_gap_threshold': 0.02})
    assert market.max_balance_iterations == 9 and consensus.market_kwargs['max_balance_iterations'] == 9
    assert consensus.duality_gap_threshold == 0.02
    consensus.spawn()
    assert consensus.newest_market.max_balance_iterations == 9, 'later markets get the new setting too'


def test_a_change_to_a_non_reconfigurable_field_is_refused_listing_it(node):
    neighbor = node.neighbors[0]
    with pytest.raises(ReconfigurationError, match=r"Neighbor 'CoR': \['transactive', 'up_or_down'\] cannot be changed on a running component"):
        neighbor.reconfigure({'transactive': False, 'up_or_down': 'downstream', 'loss_factor': 0.09})
    assert neighbor.loss_factor == 0.03 and neighbor.config_version == 1, 'nothing was applied'
    with pytest.raises(ReconfigurationError, match=r"\['loss_factr'\].*'loss_factr' is not a configuration key at all"):
        neighbor.reconfigure({'loss_factr': 0.09})
    with pytest.raises(ReconfigurationError, match='loss_factor: Input should be less than 1'):
        neighbor.reconfigure({'loss_factor': 1.5})
    series = node.market_series[0]
    with pytest.raises(ReconfigurationError, match=r"\['duration_market_lead'\] cannot be changed"):
        series.reconfigure({'duration_market_lead': 1200})
    with pytest.raises(ReconfigurationError, match=r"market settings \['actuate_assets'\] cannot be changed"):
        series.reconfigure({'actuate_assets': False})


def test_a_model_frame_asset_reconfigures_its_models_schedules_and_occupancy(node):
    asset = next(a for a in node.local_assets if a.name == 'Office')
    lights = asset.models['lights']
    new_models = deepcopy(asset.configuration['model_configs'] if hasattr(asset, 'configuration')
                          else node.configuration['local_assets'][1]['model_configs'])
    new_models['models'][0]['model_config']['default_lighting_schedule'] = [0.5] * 24
    asset.reconfigure({'model_configs': new_models,
                       'occupancy_manager': {'schedule': {d: 'always_on' for d in
                                                          ('monday', 'tuesday', 'wednesday', 'thursday', 'friday',
                                                           'saturday', 'sunday')}}})
    assert lights.lighting_schedule == [0.5] * 24 and lights.config_version == 2
    assert asset.models['lights'] is lights, 'the model object is kept'
    assert asset.occupancy_manager.schedule[0] == 'always_on'
    assert asset.actuation_manager.occupancy_manager is not None
    fewer = deepcopy(new_models)
    fewer['models'].pop()
    with pytest.raises(ReconfigurationError, match='the set of models changed'):
        asset.reconfigure({'model_configs': fewer})
    other_class = deepcopy(new_models)
    other_class['models'][1]['class_name'] = 'Lighting'
    other_class['models'][1]['model_config'] = {'rated_power': 1.0}
    with pytest.raises(ReconfigurationError, match="model 'Uncontrolled' changed class"):
        asset.reconfigure({'model_configs': other_class})
    other_points = deepcopy(new_models)
    other_points['demand_curve_points'] = 3
    with pytest.raises(ReconfigurationError, match='demand_curve_points cannot be changed'):
        asset.reconfigure({'model_configs': other_points})


def test_the_node_takes_a_configuration_that_changes_only_reconfigurable_fields(node):
    new_config = deepcopy(node.configuration)
    new_config['neighbors'][0]['loss_factor'] = 0.08
    del new_config['neighbors'][0]['demand_rate']  # back to the default
    new_config['market_series'][0]['default_price'] = 0.09
    new_config['local_assets'][1]['model_configs']['models'][1]['model_config']['uncontrolled_load_schedule'] = [4.0] * 24
    assert node.reconfigure(new_config) is True
    assert node.neighbors[0].loss_factor == 0.08 and node.neighbors[0].demand_rate == 4.5
    assert node.market_series[0].default_price == 0.09
    office = next(a for a in node.local_assets if a.name == 'Office')
    assert office.models['Uncontrolled'].load_schedule == [4.0] * 24
    assert node.configuration == new_config and node.config.neighbors[0].settings()['loss_factor'] == 0.08
    assert node.reconfigure(new_config) is True, 'an unchanged configuration is taken, with nothing to do'


@pytest.mark.parametrize('change', [
    lambda c: c.update(tz='America/Los_Angeles'),
    lambda c: c.update(name='other'),
    lambda c: c.update(data_manager={'class_name': 'LocalDataManager', 'db_params': {'url': 'sqlite://'}}),
    lambda c: c['neighbors'].append({'class_name': 'Neighbor', 'name': 'New'}),
    lambda c: c['neighbors'].clear(),
    lambda c: c['neighbors'][0].update(name='Renamed'),
    lambda c: c['neighbors'][0].update(class_name='BulkSupplierDC', transactive=False),
    lambda c: c['neighbors'][0].update(transactive=False),
    lambda c: c['market_series'][0].update(duration_market_lead=1200),
    lambda c: (c['market_series'][0].pop('actuate_assets'), c['market_series'][0].update(market_class_name='ConsensusMarket')),
    lambda c: c['market_series'][0].update(actuate_assets=False),
    lambda c: c['local_assets'][1]['model_configs']['models'].pop(),
    lambda c: c['local_assets'][1].update(actuation_manager={'class_name': 'DirectRatioActuationManager'}),
], ids=['tz', 'name', 'data_manager', 'neighbor added', 'neighbor removed', 'neighbor renamed', 'neighbor class',
        'neighbor transactive', 'series timing', 'market class', 'market setting', 'model removed', 'actuation manager'])
def test_the_node_declines_a_configuration_that_needs_a_rebuild(node, change):
    new_config = deepcopy(node.configuration)
    new_config['neighbors'][0]['loss_factor'] = 0.08  # a reconfigurable change alongside the one that needs a rebuild
    change(new_config)
    assert node.reconfigure(new_config) is False
    assert node.neighbors[0].loss_factor == 0.03, 'nothing was applied'
    assert node.configuration != new_config and node.neighbors[0].config_version == 1


def test_the_node_rejects_an_invalid_new_configuration(node):
    new_config = deepcopy(node.configuration)
    new_config['neighbors'][0]['loss_factr'] = 0.08
    with pytest.raises(ConfigurationError, match="loss_factr"):
        node.reconfigure(new_config)
    with pytest.raises(RuntimeError, match='no configuration yet'):
        MockTN().reconfigure(new_config)


# --- the JSON Schema --------------------------------------------------------------------------------------------------

def test_the_configuration_schema_describes_every_registered_class():
    schema = registry.configuration_schema()
    assert schema['$schema'] == registry.JSON_SCHEMA_DIALECT and schema['additionalProperties'] is False
    variants = {}
    for definition in schema['$defs'].values():
        for variant in definition.get('oneOf', []):
            variants[variant['title']] = variant
    for name, target in registry.SHIPPED.items():
        cls = registry.resolve(name, object)
        if issubclass(cls, Market):
            assert any(v['properties'].get('market_class_name', {}).get('const') == name for v in variants.values()), name
        elif issubclass(cls, MarketSeries):
            assert {v['properties']['class_name']['const'] for v in variants.values() if v['title'].startswith(name)} == {name}
        else:
            assert name in variants and variants[name]['properties']['class_name']['const'] == name, name
            assert variants[name]['additionalProperties'] is False
    neighbor = variants['Neighbor']['properties']
    assert neighbor['loss_factor']['reconfigurable'] is True and neighbor['loss_factor']['type'] == 'number'
    assert variants['MarketSeries of Auction']['properties']['actuate_assets']['type'] == 'boolean'
    assert variants['MarketSeries of ConsensusMarket']['properties']['actuate_assets']['default'] is True
    assert 'actuate_assets' in variants['MarketSeries of MatchingEngine']['properties']
    assert variants['MarketSeries of Auction']['properties']['duration_market_lead']['x-tent-unit'] == 'seconds'
    assert variants['Neighbor']['properties']['up_or_down']['enum'] == [d.name for d in Direction]
    assert schema['properties']['neighbors']['items'] == {'$ref': '#/$defs/AnyNeighbor'}
    assert variants['ModelFrameAsset']['properties']['occupancy_manager']['anyOf'][0] == {'$ref': '#/$defs/AnyOccupancyManager'}
    assert schema['$defs']['ModelFrameConfigs']['properties']['models']['items'] == {'$ref': '#/$defs/AnyModel'}
    assert 'default_lighting_schedule' in variants['Lighting']['properties']['model_config']['properties']
    assert variants['LocalAsset']['properties']['meter_points']['x-tent-reference'] == 'meter_points'
    jsonschema = pytest.importorskip('jsonschema')
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    with open(SAMPLE_CONFIG) as f:
        assert list(validator.iter_errors(json.load(f))) == []
    assert list(validator.iter_errors(node_config())) == []
    bad = node_config()
    bad['neighbors'][0]['loss_factr'] = 0.1
    assert any('loss_factr' in error.message for error in validator.iter_errors(bad))
