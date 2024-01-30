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

import importlib
import logging
import re

from dateutil import parser
from tzlocal import get_localzone

from tent.market import Market
from tent.utils.helpers import format_timestamp
from tent.utils.log import setup_logging
from tent.utils.timer import Timer

setup_logging()
_log = logging.getLogger(__name__)


class TransactiveNode(object):
    """
    TransactiveNode is the local perspective of the computational agent among a network of such nodes.
    """

    def __init__(self, description='', mechanism='consensus', name='', status='unknown'):

        super(TransactiveNode, self).__init__()

        self.description = description                          # [text]
        self.mechanism = mechanism                              # future, unused in Version 2
        self.name = name                                        # [text]
        self.simulation = False
        self.simulation_start_time = Timer.get_cur_time()
        self.simulation_one_hour_in_seconds = 3600
        self.status = status                                    # future: will be enumeration
        self.tz = get_localzone()  # TODO: The Timer does not use aware date-times. This should be fixed.


        # The agent must keep track of various devices and their models that are listed among these properties.
        self.informationServiceModels = []
        self.localAssets = []
        self.markets = []
        self.meterPoints = []
        self.neighbors = []

        self.default_config = {
            # TransactiveNode Configurations (from TransactiveNode super-class):
            "description": self.description,
            "mechanism": self.mechanism,
            "name": self.name,
            "simulation": self.simulation,
            "status": self.status,
            "tz": self.tz,

            # component Objects:
            "meterPoints": self.meterPoints,
            "informationServiceModels": self.informationServiceModels,
            "localAssets": self.localAssets,
            "markets": self.markets,
            "neighbors": self.neighbors
        }
        if self.simulation:
            self.default_config["simulation_start_time"] = format_timestamp(self.simulation_start_time)
            self.default_config["simulation_one_hour_in_seconds"] = self.simulation_one_hour_in_seconds

    def configure_node(self, configs):
        _log.debug('IN CONFIGURE NODE')
        updated_configs = self._update_node_configurations(configs)
        self._setup_timer(updated_configs)
        self._setup_component_objects(updated_configs)

    def _update_node_configurations(self, configs):
        _log.debug('IN _UPDATE_NODE_CONFIGURATIONS')
        updated_configs = self.default_config.copy()
        updated_configs.update(configs)

        # TransactiveNode Configurations:
        self.description = updated_configs.get('description', self.description)
        self.mechanism = updated_configs.get('mechanism', self.mechanism)
        self.name = updated_configs.get('name', self.name)
        self.status = updated_configs.get('status', self.status)
        return updated_configs

    def _setup_timer(self, config):
        # Simulation Configurations:
        self.simulation = bool(config.get('simulation', self.simulation))
        if self.simulation:
            self.simulation_start_time = parser.parse(config.get('simulation_start_time', self.simulation_start_time))
            self.simulation_one_hour_in_seconds = int(config.get('simulation_one_hour_in_seconds',
                                                                 self.simulation_one_hour_in_seconds))
        Timer.created_time = Timer.get_cur_time()
        Timer.simulation = self.simulation
        Timer.sim_start_time = self.simulation_start_time
        Timer.sim_one_hr_in_sec = self.simulation_one_hour_in_seconds

    def _setup_component_objects(self, config):
        # Configure TransactiveNode Component Classes
        _log.debug('IN _SETUP_COMPONENT_OBJECTS')
        try:
            self.meterPoints = self._configure_components(config.get('meterPoints'), 'MeterPoints')
            _log.debug('DONE WITH METER_POINTS')
            self.informationServiceModels = self._configure_components(config.get('informationServiceModels'),
                                                                        'InformationServiceModels')
            _log.debug('DONE WITH ISM')
            self.localAssets = self._configure_components(config.get('localAssets'), 'LocalAssets')
            _log.debug('DONE WITH ASSETS')
            self.neighbors = self._configure_components(config.get('neighbors'), 'Neighbors')
            _log.debug('DONE WITH NEIGHBORS')
            self.markets = self._configure_components(config.get('markets'), 'Markets')
            _log.debug('DONE WITH MARKETS')
        except ValueError as e:
            _log.error("ERROR PROCESSING CONFIGURATION {}".format(e))
            raise

    def _configure_components(self, configs, component_type):
        """Instantiates and configures each component object defined in a passed list of configurations.
        It is up to the individual component to manage its own configuration from its __init__ method.
        Returns list of instantiated and configured components
        """
        first_cap_re = re.compile('(.)([A-Z][a-z]+)')
        all_cap_re = re.compile('([a-z\d])([A-Z])')

        def camel_to_snake(name):
            s1 = first_cap_re.sub(r'\1_\2', name)
            return all_cap_re.sub(r'\1_\2', s1).lower()

        components = []
        if not configs:
            return components
        for config in configs:
            cls = config.pop('class_name')
            # TODO: What will be the default directory for classes? Does this even make sense within a library package?
            default_module_name = 'tent.' + camel_to_snake(cls)
            module = config.pop('module_name', default_module_name)
            _log.info(f'Configuring class: {cls} from module: {module}')
            module = importlib.import_module(module)
            config['transactive_node'] = self
            component_class = getattr(module, cls)
            if issubclass(component_class, Market):
                config['first_market_in_series'] = True
            component = component_class(**config)
            components.append(component)
        dep_names = [d.name for d in components]
        if len(dep_names) != len(set(dep_names)):
            raise ValueError(f'Configured {component_type} have duplicate names: {[d.name for d in components]}')
        return components

    def get_meters_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.meterPoints, attribute, match, first_only)

    def get_isms_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.informationServiceModels, attribute, match, first_only)

    def get_markets_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.markets, attribute, match, first_only)

    def get_assets_by_attribute(self, attribute, match, first_only=False):
        return self.get_objects_by_attribute(self.localAssets, attribute, match, first_only)

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
