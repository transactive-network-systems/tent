"""
Copyright (c) 2020, Battelle Memorial Institute
All rights reserved.
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
The views and conclusions contained in the software and documentation are those
of the authors and should not be interpreted as representing official policies,
either expressed or implied, of the FreeBSD Project.
This material was prepared as an account of work sponsored by an agency of the
United States Government. Neither the United States Government nor the United
States Department of Energy, nor Battelle, nor any of their employees, nor any
jurisdiction or organization that has cooperated in th.e development of these
materials, makes any warranty, express or implied, or assumes any legal
liability or responsibility for the accuracy, completeness, or usefulness or
any information, apparatus, product, software, or process disclosed, or
represents that its use would not infringe privately owned rights.
Reference herein to any specific commercial product, process, or service by
trade name, trademark, manufacturer, or otherwise does not necessarily
constitute or imply its endorsement, recommendation, or favoring by the
United States Government or any agency thereof, or Battelle Memorial Institute.
The views and opinions of authors expressed herein do not necessarily state or
reflect those of the United States Government or any agency thereof.

PACIFIC NORTHWEST NATIONAL LABORATORY
operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
under Contract DE-AC05-76RL01830
"""
import abc
import logging

from copy import deepcopy
from datetime import datetime, timezone
from itertools import chain
from sortedcontainers import SortedList
from typing import Annotated, Any, Callable, Mapping
from uuid import UUID
from zoneinfo import ZoneInfo, available_timezones

from pydantic import BeforeValidator, field_validator, model_validator

from tent import registry
from archivable import DataManager, NullDataManager

from tent import data_manager as tent_archive
from tent.base import Archivable  # before tent.containers: the base package completes before the containers need it
from tent.config import ClassSpec, ConfigModel, Configurable, ConfigurationError, ReconfigurationError, Setting
import tent.containers as containers
from tent.information_service_model import InformationServiceModel
from tent.local_asset import LocalAsset
from tent.market import Market
from tent.market.market_series import MarketSeries
from tent.meter_point import MeterPoint
from tent.neighbor import Neighbor
from tent.utils.helpers import get_class_from_spec, parse_datetime, require_aware, spec_kwargs
from tent.utils.timer import Timer

_log = logging.getLogger(__name__)


def _parse_configured_datetime(value):
    return parse_datetime(value) if value is not None else None


ConfiguredDatetime = Annotated[datetime | None, BeforeValidator(_parse_configured_datetime)]
"""An ISO 8601 datetime; without an offset it is in the node's zone (tz), with one it is converted to that zone."""

# Spellings an earlier agent configuration format used for the node's lists; they are errors naming the current key.
LEGACY_NODE_KEYS = {'meterPoints': 'meter_points', 'informationServiceModels': 'information_service_models',
                    'localAssets': 'local_assets', 'markets': 'market_series', 'marketSeries': 'market_series',
                    'neighbours': 'neighbors'}


class TransactiveNode(Configurable, Archivable):
    """
    TransactiveNode is the local perspective of the computational agent among a network of such nodes.
    """

    class Config(ConfigModel):
        """
        The node configuration: the node's own settings, the settings its host (the VOLTTRON agent) reads, the data
        manager, and the five component lists. Each component list entry names its class (class_name, module_name for
        a class TENT does not ship) and gives that class's settings, validated by the class's own Config
        (TransactiveNode.validate_configuration does this for the whole file before anything is built).
        """
        # The node
        name: str = Setting('', description='Name of the node; neighbors address it by this name.')
        description: str = Setting('', description='Free text.')
        actor_id: str | None = Setting(None, description='Permanent identity of the actor this node represents, '
                                                         'constant across restarts and configuration changes.')
        organization_id: str | None = Setting(None, description='Identity of the business entity the actor belongs to.')
        tz: str = Setting('UTC', description='IANA zone name (such as "America/Los_Angeles") the node keeps time in.')
        mechanism: str = Setting('consensus', description='Coordination mechanism (future use).')
        status: str = Setting('unknown', description='Node status (future use).')
        loop_sleep_duration: float = Setting(0.01, ge=0, description='[s] pause between state-machine iterations.')
        # Simulation
        simulation: bool = Setting(False, description='Whether the node runs on simulated time.')
        simulation_start_time: ConfiguredDatetime = Setting(
            None, description='ISO 8601 datetime at which simulated time starts; without an offset it is in tz. '
                              'Null means the time the node is configured.')
        simulation_one_hour_in_seconds: int = Setting(3600, gt=0,
                                                      description='[s] real seconds per simulated hour.')
        simulation_anchor_time: ConfiguredDatetime = Setting(
            None, description='ISO 8601 real time at which simulated time equals simulation_start_time; shared by '
                              'every agent of one simulation (the host sets it from its environment). Null means the '
                              'time this node was created.')
        # Host (agent) settings, read by the TransactiveNodeAgent, not by the node
        db_topic: str | None = Setting(None, description='Root of the message-bus topics the agent publishes under.')
        subscribe_all_platforms: bool | None = Setting(None, description='Whether the agent subscribes across '
                                                                        'platforms.')
        topics: dict[str, str] | None = Setting(None, description='Subscription key to message-bus topic, for the '
                                                                 'agent.')
        # Components
        data_manager: ClassSpec | None = Setting(None, spec_of='DataManager',
                                                 description='The archive (class_name and its settings); null means '
                                                             'nothing is archived.')
        meter_points: list[ClassSpec] = Setting([], spec_of='MeterPoint', description='The meter points.')
        information_service_models: list[ClassSpec] = Setting([], spec_of='InformationServiceModel',
                                                              description='The information services (forecasts).')
        local_assets: list[ClassSpec] = Setting([], spec_of='LocalAsset', description='The local assets.')
        neighbors: list[ClassSpec] = Setting([], spec_of='Neighbor', description='The neighbors.')
        market_series: list[ClassSpec] = Setting([], spec_of='MarketSeries',
                                                 description='The market series, each with market_class_name.')

        @model_validator(mode='before')
        @classmethod
        def _reject_legacy_keys(cls, data):
            if isinstance(data, Mapping):
                legacy = [key for key in data if key in LEGACY_NODE_KEYS]
                if legacy:
                    raise ValueError('; '.join(f'{key!r} is not a configuration key; use {LEGACY_NODE_KEYS[key]!r}'
                                               for key in legacy))
            return data

        @field_validator('tz')
        @classmethod
        def _iana_zone(cls, value):
            if value not in available_timezones():
                raise ValueError(f'tz must be an IANA zone name such as "America/Los_Angeles" or "UTC"; got {value!r}')
            return value

        @model_validator(mode='after')
        def _times_in_the_node_zone(self):
            zone = ZoneInfo(self.tz)
            if self.simulation_start_time is not None:
                self.simulation_start_time = require_aware(self.simulation_start_time, 'simulation_start_time',
                                                           assume=zone).astimezone(zone)
            if self.simulation_anchor_time is not None:
                self.simulation_anchor_time = require_aware(self.simulation_anchor_time, 'simulation_anchor_time',
                                                            assume=zone)
            for list_name in registry.NODE_LISTS:
                names = [spec.settings().get('name', '') for spec in getattr(self, list_name)]
                duplicates = sorted({name for name in names if names.count(name) > 1})
                if duplicates:
                    raise ValueError(f'{list_name} have duplicate names {duplicates}; each component needs its own.')
            return self

    @classmethod
    def validate_configuration(cls, configs: Mapping) -> 'TransactiveNode.Config':
        """
        The node configuration validated through the models, without building anything: the node's own settings, then
        the data manager and every component (class resolved through tent.registry, settings validated by the class's
        Config, nested specifications recursively). Raises ConfigurationError (a ValueError) naming the component and
        the key for anything the models reject; class resolution failures raise as tent.registry does.
        """
        cfg = cls.Config.validate_for(cls.__name__, configs)
        if cfg.data_manager is not None:
            dm_class = registry.resolve(cfg.data_manager.class_name, DataManager, cfg.data_manager.module_name)
            if dm_class.declares_config():
                cls._validate_component_spec(dm_class, cfg.data_manager.spec(), 'data_manager')
        for list_name, base_name in registry.NODE_LISTS.items():
            base = registry.base_class(base_name)
            for index, spec in enumerate(getattr(cfg, list_name)):
                component_class = registry.resolve(spec.class_name, base, spec.module_name)
                if component_class.declares_config():
                    cls._validate_component_spec(component_class, spec.spec(), f'{list_name}[{index}]')
        return cfg

    @staticmethod
    def _validate_component_spec(component_class: type, spec: dict, where: str) -> None:
        try:
            if issubclass(component_class, MarketSeries):
                component_class.validate_spec(spec, TransactiveNode._market_class_from_series_config(spec))
            else:
                component_class.validate_spec(spec)
        except ConfigurationError as error:
            raise ConfigurationError(f'{where}: {error.owner}', error.problems) from None

    def __init__(self, description='', loop_sleep_duration=0.01, mechanism='consensus', name='', status='unknown',
                 node_id: UUID | None = None, created: datetime | None = None):
        # node_id and created let a node rebuilt from a changed configuration continue the same run (same archive
        # identity and, when the simulation settings are unchanged, the same clock anchor).
        super(TransactiveNode, self).__init__(id=node_id, real_time_created=created)
        self.description = description                          # [text]
        self.loop_sleep_duration = loop_sleep_duration
        self.mechanism = mechanism                              # future, unused in Version 2
        self.name = name                                        # [text]
        self.actor_id: str | None = None         # Permanent, configured identity of the actor this node represents.
        self.organization_id: str | None = None  # Optional identity of the business entity the actor belongs to.
        self.simulation = False
        self.simulation_start_time = Timer.now()
        self.simulation_one_hour_in_seconds = 3600
        self.simulation_anchor_time: datetime | None = None  # real time at which simulated time equals its start
        self.status = status                                    # future: will be enumeration
        self.tz = 'UTC'  # IANA zone name; configured per node. (The host's abbreviation, e.g. PDT, is not a zone.)

        self._data_manager = None
        self.config: TransactiveNode.Config | None = None   # the validated configuration, from configure_node
        self.configuration: dict = {}                       # the configuration as given to configure_node

        # The agent must keep track of various devices and their models that are listed among these properties.
        self.information_service_models = []
        self.local_assets = []
        self.market_series = SortedList(key=lambda series: series.market_order)
        self.meter_points = []
        self.neighbors = []

        self.default_config = {
            # TransactiveNode Configurations (from TransactiveNode super-class):
            "description": self.description,
            "loop_sleep_duration": self.loop_sleep_duration,
            "mechanism": self.mechanism,
            "name": self.name,
            "actor_id": self.actor_id,
            "organization_id": self.organization_id,
            "simulation": self.simulation,
            "status": self.status,
            "tz": self.tz,

            # component Objects:  # TODO: Why is this in the default_config? Does this even work with mutable lists?
            #                        Attempting to comment, as a test of whether it can be removed.
            # "meter_points": self.meter_points,
            # "information_service_models": self.information_service_models,
            # "local_assets": self.local_assets,
            # "market_series": self.market_series,
            # "neighbors": self.neighbors
        }
        if self.simulation:
            self.default_config["simulation_start_time"] = self.simulation_start_time.isoformat()
            self.default_config["simulation_one_hour_in_seconds"] = self.simulation_one_hour_in_seconds

    def configure_node(self, configs):
        """Build the node from `configs` (see Config). The whole configuration is validated through the models first,
        so a configuration the models reject fails here before any component exists."""
        _log.debug('IN CONFIGURE NODE')
        self.config = self.validate_configuration(configs)
        self.configuration = deepcopy(dict(configs))
        updated_configs = self._update_node_configurations(configs)
        self._setup_timer(updated_configs)
        self._setup_data_manager(updated_configs)
        self._setup_component_objects(updated_configs)
        # The run's row: identity, simulation anchor and ratio (see Timer.set), timezone. Queued before init_archive()
        # so it is flushed in the same unit of work as the components that reference it (foreign-key order).
        self.archive()
        self.data_manager.init_archive()

    def _update_node_configurations(self, configs):
        """Read the node's own settings from `configs`. Returns the configuration with the node's defaults filled in,
        as a dict, for hosts that read their own settings from it (the agent's db_topic, topics, ...)."""
        _log.debug('IN _UPDATE_NODE_CONFIGURATIONS')
        updated_configs = self.default_config.copy()
        updated_configs.update(configs)
        cfg = self.config if self.config is not None else self.Config.validate_for(type(self).__name__, configs)

        # TransactiveNode Configurations:
        self.description = cfg.description
        self.loop_sleep_duration = cfg.loop_sleep_duration
        self.mechanism = cfg.mechanism
        self.name = cfg.name
        self.actor_id = cfg.actor_id
        self.organization_id = cfg.organization_id
        self.status = cfg.status
        self.tz = cfg.tz
        return updated_configs

    def _setup_timer(self, config):
        # Simulation Configurations:
        cfg = self.config
        self.simulation = cfg.simulation
        if self.simulation:
            # A start time without an offset is in the node's zone; one with an offset is converted to that zone.
            if cfg.simulation_start_time is not None:
                self.simulation_start_time = cfg.simulation_start_time
            self.simulation_one_hour_in_seconds = int(cfg.simulation_one_hour_in_seconds)
        # The anchor is the real time at which simulated time equals simulation_start_time. Every agent in one simulation
        # should share it (config key simulation_anchor_time), so that agents started at different wall-clock times still
        # agree on the simulated time; a late starter joins at the current simulated time. Without it, this node's own
        # creation time is used.
        self.simulation_anchor_time = cfg.simulation_anchor_time or self.real_time_created
        Timer.set(sim_one_hr_in_sec=self.simulation_one_hour_in_seconds,
                  sim_start_time=self.simulation_start_time,
                  simulation=self.simulation,
                  tz=ZoneInfo(self.tz),
                  anchor=self.simulation_anchor_time
        )

    def _setup_component_objects(self, config):
        # Configure TransactiveNode Component Classes, in dependency order: entities refer to meter points and
        # information services by name, market series to earlier series.
        _log.debug('IN _SETUP_COMPONENT_OBJECTS')
        try:
            for list_name, base_name in registry.NODE_LISTS.items():
                self._configure_components(config.get(list_name), registry.base_class(base_name), list_name)
                _log.debug(f'DONE WITH {list_name.upper()}')
            for series in self.market_series:  # a series may refine another configured after it
                series.resolve_references()
        except ValueError as e:
            _log.error("ERROR PROCESSING CONFIGURATION {}".format(e))
            raise

    def _configure_components(self, configs, base: type, attribute: str):
        """Instantiates and configures each component object defined in a passed list of configurations into the node
        list `attribute`. Each configuration names its class (see tent.registry) and must resolve to a subclass of
        `base`; its other keys are the component's constructor keywords, validated by the class's Config. The node list
        is filled as the components are built, so a component may refer by name to one configured before it. The
        configurations are only read, so the same ones can be given again to rebuild the node. Returns the list.
        """
        components = []
        setattr(self, attribute, components)
        component_type = base.__name__
        _log.info(f'Configuring {component_type}')
        if not configs:
            _log.info(f'No {component_type} configurations were provided.')
            return components
        for config in configs:
            # A copy: the caller's configuration must still be whole (to configure another node, or this one again).
            config = dict(config)
            _log.info(f'Configuring {config.get("class_name")}: {config.get("name")}')
            component_class = get_class_from_spec(config, base)
            self.data_manager.add_tables(component_class.get_archive_definitions())
            kwargs = spec_kwargs(config, 'market_class_name', 'market_module_name')
            kwargs['transactive_node'] = self
            if issubclass(component_class, MarketSeries):
                market_class = self._market_class_from_series_config(config)
                if market_class is not None:
                    kwargs['market_class'] = market_class
            component = component_class(**kwargs)
            if issubclass(component_class, MarketSeries):
                self.data_manager.add_tables(component.market_class.get_archive_definitions())
            if not component.archived:  # MarketSeries archives itself before spawning its first market
                component.archive()
            components.append(component)
        dep_names = [d.name for d in components]
        if len(dep_names) != len(set(dep_names)):
            raise ValueError(f'Configured {component_type} have duplicate names: {[d.name for d in components]}')
        return components

    @staticmethod
    def _market_class_from_series_config(config: dict) -> type[Market] | None:
        """The Market subclass a market series configuration names with market_class_name (and market_module_name for
        a class TENT does not ship), or None when it names none and the series' own default applies."""
        if 'market_class_name' not in config:
            if 'market_module_name' in config:
                raise ValueError(f'Market series {config.get("name")!r} gives market_module_name without '
                                 f'market_class_name; name the market class too.')
            return None
        return get_class_from_spec({'class_name': config['market_class_name'],
                                    'module_name': config.get('market_module_name')}, Market)

    def _setup_data_manager(self, config):
        dm_configs = config.get('data_manager')
        if dm_configs:
            dm_class = get_class_from_spec(dm_configs, DataManager)
            self._data_manager = dm_class(self, **spec_kwargs(dm_configs))
        else:
            # Without a data_manager block the node runs without an archive.
            _log.warning(f'Node {self.name!r} has no data_manager configuration; nothing will be archived.')
            self._data_manager = NullDataManager(self)
        # TENT's archive schema version and enumerations, before any table definition that names an enumeration.
        tent_archive.prepare(self.data_manager)
        self.data_manager.add_tables(self.get_archive_definitions())

    # ----------------------------------------------------------------------------------------------- reconfiguration

    def reconfigure(self, new_config: Mapping) -> bool:
        """
        Take a new full configuration while running when it differs from the current one only in reconfigurable fields
        of existing components (matched by list, name and class): each component's reconfigure applies its changes and
        True is returned. Anything else (node settings, the data manager, a component added, removed or of another
        class, a field that is not reconfigurable) returns False, with the reason logged, and nothing is changed; the
        host then rebuilds the node. A configuration the models reject raises (ConfigurationError) as configure_node
        would.
        """
        if self.config is None:
            raise RuntimeError('The node has no configuration yet; call configure_node first.')
        new_cfg = self.validate_configuration(new_config)
        plan = self._reconfiguration_plan(new_cfg)
        if plan is None:
            return False
        for component, changes in plan:
            component.reconfigure(changes)
        self.config = new_cfg
        self.configuration = deepcopy(dict(new_config))
        _log.info(f'Node {self.name!r} took the new configuration in place: '
                  f'{[(c.name, sorted(changes)) for c, changes in plan] or "no component changed"}.')
        return True

    def _reconfiguration_plan(self, new_cfg: 'TransactiveNode.Config') -> list[tuple] | None:
        """(component, changes) pairs that take the node from its configuration to `new_cfg` in place, or None (with
        the reason logged) when the node must be rebuilt."""
        def rebuild(reason: str):
            _log.info(f'Node {self.name!r} must be rebuilt for the new configuration: {reason}')
            return None

        component_lists = set(registry.NODE_LISTS)
        current_node = self.config.model_dump(exclude=component_lists)
        new_node = new_cfg.model_dump(exclude=component_lists)
        changed_node_fields = sorted(key for key in current_node if current_node[key] != new_node[key])
        if changed_node_fields:
            return rebuild(f'node settings {changed_node_fields} changed.')
        plan = []
        for list_name in registry.NODE_LISTS:
            current_specs = {spec.settings().get('name', ''): spec for spec in getattr(self.config, list_name)}
            new_specs = {spec.settings().get('name', ''): spec for spec in getattr(new_cfg, list_name)}
            if set(current_specs) != set(new_specs):
                return rebuild(f'{list_name} were added or removed: {sorted(set(current_specs) ^ set(new_specs))}.')
            components = {component.name: component for component in getattr(self, list_name)}
            for name, new_spec in new_specs.items():
                current_spec = current_specs[name]
                if (new_spec.class_name, new_spec.module_name) != (current_spec.class_name, current_spec.module_name):
                    return rebuild(f'{list_name} {name!r} changed class.')
                component = components.get(name)
                if component is None:
                    return rebuild(f'{list_name} {name!r} is configured but not built.')
                settings = {key: value for key, value in new_spec.spec().items()
                            if key not in component.RUNTIME_SPEC_KEYS}
                if isinstance(component, MarketSeries):
                    market_class = self._market_class_from_series_config(new_spec.spec()) or Market
                    if market_class is not component.market_class:
                        return rebuild(f'market series {name!r} changed its market class.')
                try:
                    changes = component.diff_configuration(settings)
                    if changes:
                        component.check_reconfigure(changes)
                except ReconfigurationError as error:
                    return rebuild(str(error))
                if changes:
                    plan.append((component, changes))
        return plan

    def get_meters_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.meter_points, attribute, match, first_only)

    def get_isms_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.information_service_models, attribute, match, first_only)

    def get_market_series_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.market_series, attribute, match, first_only)

    def update_information_services(self, market) -> None:
        """Have every information service update its predictions for this market (called when a market becomes active,
        before assets schedule). One failing service does not stop the market: it is logged with its traceback and its
        consumers use their defaults for this market."""
        for service in self.information_service_models:
            try:
                service.update_information(market)
            except Exception:
                _log.exception(f'{self.name}: information service {service.name!r} failed to update for {market.name}; '
                               f'its consumers use their defaults for this market.')

    def on_market_spawned(self, market) -> None:
        """A series created a market: neighbors deliver any signal they held for it (see Neighbor.held_signals)."""
        for neighbor in self.neighbors:
            neighbor.on_market_spawned(market)

    def get_market_by_name(self, name):
        """The market with exactly this name, from any series. (Market names join the series name's words with
        underscores, so the series name itself cannot be used to narrow the search.)"""
        for series in self.market_series:
            for market in series:
                if market.name == name:
                    return market
        return None

    def get_assets_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.local_assets, attribute, match, first_only)

    def get_neighbors_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.neighbors, attribute, match, first_only)

    @staticmethod
    def get_objects_by_attribute(object_list, attribute, match, first_only=False):
        if not isinstance(match, (list, tuple, str)):
            raise ValueError('Match must be a list, tuple or string.')
        elif isinstance(match, str):
            match = [match]
        matches = [o for o in object_list if getattr(o, attribute, False) in match]
        if first_only and len(matches) > 0:
            return matches[0]
        else:
            return matches

    @abc.abstractmethod
    def issue_signal(self, recipient: str, serialized_signal: dict):
        _log.debug("WRONG PLACE: THIS IS THE ABSTRACT METHOD IN TN")
        pass

    @abc.abstractmethod
    def register_subscription(self, subscribe_key: str, callback: Callable[[datetime, str, Any], None]):
        """ When data is available for the given subscribe key,
            a callback with the following signature will be called to process the data:
                callback(time_received: datetime, subscribe_key: str, message: Any)
        """
        pass

    @abc.abstractmethod
    def _sleep(self):
        """The host's cooperative yield. The state machine is synchronous Python: _state_machine_iteration calls this
        after each market's events so that a host which multiplexes the node with its message handling on one thread (a
        gevent agent, say) can hand control to the hub between markets and receive signals while a long iteration runs,
        and so that a loop with nothing to do pauses rather than spins. A host implements it as the yield of its
        multitasking system (gevent.sleep, asyncio's sleep via its loop, time.sleep for a plain thread), pausing for
        self.loop_sleep_duration where a pause is wanted; a host that runs the node on its own thread may make it a no-op.
        """
        pass

    @abc.abstractmethod
    def state_machine_loop(self):
        """This method should call _state_machine_iteration in a loop until stopped."""
        pass

    def _state_machine_iteration(self):
        """One complete iteration: give every meter point its poll (see tent.meter_point), then run every market's
        events."""
        for meter_point in self.meter_points:
            meter_point.poll()
        for series in self.market_series:
            for market in series:
                market.events()
                self._sleep()
            series.prune_expired()

    @property
    def data_manager(self):
        return self._data_manager

    @property
    def table_name(self):
        return 'transactive_node'

    def get_record(self) -> dict:
        record = super(TransactiveNode, self).get_record()
        record.update({
            "created": self.real_time_created,
            "description": self.description,
            "name": self.name,
            "actor_id": self.actor_id,
            "organization_id": self.organization_id,
            "simulation": self.simulation,
            "simulation_start_time": self.simulation_start_time,
            "simulation_one_hour_in_seconds": self.simulation_one_hour_in_seconds,
            "simulation_anchor_time": self.simulation_anchor_time,
            "tz": self.tz
        })
        return record

    @classmethod
    def get_archive_definitions(cls):
        # TODO: Finish code to ingest definitions & call init_archive.
        # TODO: Mechanism to create initial entries.  How to store these until db is ready? Queue?
        # TODO: Mechanism for queueing and storing entries. How does this relate to archive_data?
        definitions = list(chain.from_iterable(
            [c.get_archive_definitions() for c in [getattr(containers, name) for name in containers.__all__]
             if issubclass(c, Archivable)])
        )
        definitions.extend([
            {
                "name": "transactive_node",
                "columns": [
                    {"name": "created", "type": "DateTime"},
                    {"name": "description", "type": "String", "type_args": [255]},
                    {"name": "id", "type": "Uuid", "identity": True, "kwargs": {"primary_key": True}},
                    {"name": "name", "type": "String", "type_args": [255]},
                    {"name": "actor_id", "type": "String", "type_args": [255], "kwargs": {"nullable": True}},
                    {"name": "organization_id", "type": "String", "type_args": [255], "kwargs": {"nullable": True}},
                    {"name": "simulation", "type": "Boolean"},
                    {"name": "simulation_start_time", "type": "DateTime"},
                    {"name": "simulation_one_hour_in_seconds", "type": "Float"},
                    {"name": "simulation_anchor_time", "type": "DateTime", "kwargs": {"nullable": True}},
                    {"name": "tz", "type": "String", "type_args": [64]}  # IANA zone name
                ],
                "indices": [
                    {"name": "idx_tn_instance", "columns": ["actor_id", "name", "created"], "unique": True}
                ]
            }
        ])
        from tent.market.market_series import MarketSeries
        definitions.extend(MarketSeries.get_archive_definitions())  # TODO: Market Series needs real integration.
        return definitions
