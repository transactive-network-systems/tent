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
from dateutil import parser
from itertools import chain
from sortedcontainers import SortedList
from zoneinfo import ZoneInfo

from tent.base import Archivable
import tent.containers as containers
from tent.market.market_series import MarketSeries
from tent.utils.helpers import get_class_from_spec
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
        self.registry_id = None                                 # string: An out-of-band identifier from a node registry.
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
            "registry_id": self.registry_id,
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
        self.registry_id = updated_configs.get('registry_id', self.registry_id)
        self.status = updated_configs.get('status', self.status)
        self.tz = updated_configs.get('tz', self.tz)
        return updated_configs

    def _setup_timer(self, config):
        # Simulation Configurations:
        self.simulation = bool(config.get('simulation', self.simulation))
        if self.simulation:
            self.simulation_start_time = parser.parse(config.get('simulation_start_time', self.simulation_start_time)
                                                      ).replace(tzinfo=ZoneInfo(self.tz))
            self.simulation_one_hour_in_seconds = int(config.get('simulation_one_hour_in_seconds',
                                                                 self.simulation_one_hour_in_seconds))
        # TODO: If the TN is started repeatedly many times, the created time will change unexplainably.
        #  It may help to have a class method that guards changes in items like "created_time".
        Timer.created_time = Timer.now(timezone.utc).astimezone(ZoneInfo(self.tz))
        Timer.simulation = self.simulation
        Timer.sim_start_time = self.simulation_start_time
        Timer.sim_one_hr_in_sec = self.simulation_one_hour_in_seconds
        Timer.tz = ZoneInfo(self.tz)

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
        if not configs:
            return components
        for config in configs:
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
            component.archive()
            components.append(component)
        dep_names = [d.name for d in components]
        if len(dep_names) != len(set(dep_names)):
            raise ValueError(f'Configured {component_type} have duplicate names: {[d.name for d in components]}')
        return components

    def _setup_data_manager(self, config):
        dm_configs = config.get('data_manager', {})
        dm_class =  get_class_from_spec(dm_configs, default_module_prefix='tent.data_manager', transactive_node=self)
        self._data_manager = dm_class(**dm_configs)
        self.data_manager.add_tables(self.get_archive_definitions())

    def get_meters_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.meter_points, attribute, match, first_only)

    def get_isms_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.information_service_models, attribute, match, first_only)

    def get_market_series_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.market_series, attribute, match, first_only)

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
    def register_subscription(self, subscribe_key, callback):
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
        return {
            "created": self.real_time_created,
            "description": self.description,
            "name": self.name,
            "registry_id": self.registry_id,
            "simulation": self.simulation,
            "simulation_start_time": self.simulation_start_time,
            "simulation_one_hour_in_seconds": self.simulation_one_hour_in_seconds,
            "tz": self.tz
        }

    @classmethod
    def get_archive_definitions(cls):
        # TODO: Finish code to ingest definitions & call init_archive.
        # TODO: Mechanism to create initial entries.  How to store these until db is ready? Queue?
        # TODO: Mechanism for queueing and storing entries. How does this relate to archive_data?
        definitions = list(chain.from_iterable(
            [getattr(containers, c).get_archive_definitions() for c in containers.__all__]))
        definitions.extend([
            {
                "name": "transactive_node",
                "columns": [
                    {"name": "created", "type": "DateTime"},
                    {"name": "description", "type": "String", "type_args": [255]},
                    {"name": "id", "type": "Uuid", "identity": True, "kwargs": {"primary_key": True}},
                    {"name": "name", "type": "String", "type_args": [255]},
                    {"name": "registry_id", "type": "String", "type_args": [255], "kwargs": {"nullable": True}},
                    {"name": "simulation", "type": "Boolean"},
                    {"name": "simulation_start_time", "type": "DateTime"},
                    {"name": "simulation_one_hour_in_seconds", "type": "Float"},
                    {"name": "tz", "type": "TimezoneType", "type_args": ["zoneinfo"]}
                ],
                "indices": [
                    {"name": "idx_tn_instance", "columns": ["registry_id", "name", "created"], "unique": True}
                ]
            }
        ])
        from tent.market.market_series import MarketSeries
        definitions.extend(MarketSeries.get_archive_definitions())  # TODO: Market Series needs real integration.
        return definitions
