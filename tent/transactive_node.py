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

from datetime import datetime, timezone
from itertools import chain
from sortedcontainers import SortedList
from typing import Any, Callable
from zoneinfo import ZoneInfo

from tent.base import Archivable
import tent.containers as containers
from tent.market.market_series import MarketSeries
from tent.utils.helpers import get_class_from_spec, validate_bool, parse_datetime
from tent.utils.timer import Timer

_log = logging.getLogger(__name__)


class TransactiveNode(Archivable):
    """
    TransactiveNode is the local perspective of the computational agent among a network of such nodes.
    """

    def __init__(self, description='', loop_sleep_duration=0.01, mechanism='consensus', name='', status='unknown'):
        super(TransactiveNode, self).__init__()
        self.description = description                          # [text]
        self.loop_sleep_duration = loop_sleep_duration
        self.mechanism = mechanism                              # future, unused in Version 2
        self.name = name                                        # [text]
        self.actor_id: str | None = None         # Permanent, configured identity of the actor this node represents.
        self.organization_id: str | None = None  # Optional identity of the business entity the actor belongs to.
        self.simulation = False
        self.simulation_start_time = Timer.now()
        self.simulation_one_hour_in_seconds = 3600
        self.status = status                                    # future: will be enumeration
        now = datetime.now(timezone.utc).astimezone()
        self.tz = now.tzinfo.tzname(now)

        self._data_manager = None

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
        _log.debug('IN CONFIGURE NODE')
        updated_configs = self._update_node_configurations(configs)
        self._setup_timer(updated_configs)
        self._setup_data_manager(updated_configs)
        self._setup_component_objects(updated_configs)
        # The run's row: identity, simulation anchor and ratio (see Timer.set), timezone. Queued before init_archive()
        # so it is flushed in the same unit of work as the components that reference it (foreign-key order).
        self.archive()
        self.data_manager.init_archive()

    def _update_node_configurations(self, configs):
        _log.debug('IN _UPDATE_NODE_CONFIGURATIONS')
        updated_configs = self.default_config.copy()
        updated_configs.update(configs)

        # TransactiveNode Configurations:
        self.description = updated_configs.get('description', self.description)
        self.loop_sleep_duration = updated_configs.get('loop_sleep_duration', self.loop_sleep_duration)
        self.mechanism = updated_configs.get('mechanism', self.mechanism)
        self.name = updated_configs.get('name', self.name)
        self.actor_id = updated_configs.get('actor_id', self.actor_id)
        self.organization_id = updated_configs.get('organization_id', self.organization_id)
        self.status = updated_configs.get('status', self.status)
        self.tz = updated_configs.get('tz', self.tz)
        return updated_configs

    def _setup_timer(self, config):
        # Simulation Configurations:
        self.simulation = validate_bool(config.get('simulation', self.simulation), 'simulation')
        if self.simulation:
            self.simulation_start_time = parse_datetime(config.get('simulation_start_time', self.simulation_start_time)
                                                        ).replace(tzinfo=ZoneInfo(self.tz))
            self.simulation_one_hour_in_seconds = int(config.get('simulation_one_hour_in_seconds',
                                                                 self.simulation_one_hour_in_seconds))
        Timer.set(sim_one_hr_in_sec=self.simulation_one_hour_in_seconds,
                  sim_start_time=self.simulation_start_time,
                  simulation=self.simulation,
                  tz=ZoneInfo(self.tz),
                  anchor=self.real_time_created
        )

    def _setup_component_objects(self, config):
        # Configure TransactiveNode Component Classes
        _log.debug('IN _SETUP_COMPONENT_OBJECTS')
        try:
            self.meter_points = self._configure_components(config.get('meter_points'), 'Meter Points')
            _log.debug('DONE WITH METER_POINTS')
            self.information_service_models = self._configure_components(config.get('information_service_models'),
                                                                        'Information Service Models')
            _log.debug('DONE WITH ISM')
            self.local_assets = self._configure_components(config.get('local_assets'), 'Local Assets')
            _log.debug('DONE WITH ASSETS')
            self.neighbors = self._configure_components(config.get('neighbors'), 'Neighbors')
            _log.debug('DONE WITH NEIGHBORS')
            self.market_series = self._configure_components(config.get('market_series'), 'Market Series')
            # TODO: Organize them by market order?
            _log.debug('DONE WITH MARKETS')
        except ValueError as e:
            _log.error("ERROR PROCESSING CONFIGURATION {}".format(e))
            raise

    def _configure_components(self, configs, component_type: str):
        """Instantiates and configures each component object defined in a passed list of configurations.
        It is up to the individual component to manage its own configuration from its __init__ method.
        Returns list of instantiated and configured components
        """
        components = []
        _log.info(f'Configuring {component_type}')
        if not configs:
            _log.info(f'No {component_type} configurations were provided.')
            return components
        for config in configs:
            _log.info(f'Configuring {config.get("class_name")}: {config.get("name")}')
            component_class = get_class_from_spec(config, default_module_prefix='tent', transactive_node=self)
            self.data_manager.add_tables(component_class.get_archive_definitions())
            if issubclass(component_class, MarketSeries):
                market_class = get_class_from_spec({'class_name': (config.pop('market_class_name', None)),
                                                    'module_name': (config.pop('market_module_name', None))},
                                                   default_module_prefix='tent', transactive_node=self)
                config['market_class'] = market_class
            component = component_class(**config)
            if issubclass(component_class, MarketSeries):
                self.data_manager.add_tables(component.market_class.get_archive_definitions())
            if not component.archived:  # MarketSeries archives itself before spawning its first market
                component.archive()
            components.append(component)
        dep_names = [d.name for d in components]
        if len(dep_names) != len(set(dep_names)):
            raise ValueError(f'Configured {component_type} have duplicate names: {[d.name for d in components]}')
        return components

    def _setup_data_manager(self, config):
        dm_configs = config.get('data_manager', {})
        # TODO: Make sure that we can run without a DataManager. This chokes if there is not one in the config.
        #  Alternately, should we have a NullDataManager that just passes when called?
        dm_class =  get_class_from_spec(dm_configs, default_module_prefix='tent.data_manager', transactive_node=self)
        self._data_manager = dm_class(self, **dm_configs)
        self.data_manager.add_tables(self.get_archive_definitions())

    def get_meters_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.meter_points, attribute, match, first_only)

    def get_isms_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.information_service_models, attribute, match, first_only)

    def get_market_series_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.market_series, attribute, match, first_only)

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
        """This method should sleep for self.loop_sleep_duration as is appropriate
            to the multitasking system employed by the agent.
        """
        pass

    @abc.abstractmethod
    def state_machine_loop(self):
        """This method should call _state_machine_iteration in a loop until stopped."""
        pass

    def _state_machine_iteration(self):
        """One complete iteration through the market state machine. """
        for series in self.market_series:
            for market in series:
                market.events()
                self._sleep()

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
