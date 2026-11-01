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
from datetime import datetime
from typing import List

from tent.utils.helpers import get_class_from_spec, spec_kwargs

_log = logging.getLogger(__name__)
__version__ = "0.1"

__all__ = ['Model', 'ModelFrame']


class Model(abc.ABC):
    """
    One device model inside a ModelFrame: it predicts the power and the flexibility of its device for an interval and
    takes in the measurements published for it. A model-frame asset names each model's class with `class_name` (and
    `module_name` for a class TENT does not ship; see tent.registry) and constructs it with that entry's
    `model_config`, extended with the entry's `subscribe_key` and the frame's `demand_curve_points`, as the first
    argument and the frame as `parent`.
    """

    @abc.abstractmethod
    def predict_power(self, params: dict = None, set_point=None) -> float:
        """The device's power [kW, generation positive] for the interval described by params."""

    @abc.abstractmethod
    def predict_flexibility(self, params: dict = None) -> List[float]:
        """demand_curve_points powers [kW, generation positive] spanning the device's flexibility for the interval."""

    @abc.abstractmethod
    def update_data(self, data: dict, now: datetime) -> None:
        """Take in a measurement message published for this model's subscribe_key at time now."""


class ModelFrame(object):
    def __init__(self, config, **__):
        self.models = {}
        self.cleared_quantity = None
        if not config:
            return
        self.demand_curve_points = config.get('demand_curve_points', 2)
        for model in config['models']:
            try:
                subscribe_key = model["subscribe_key"]
            except KeyError as e:
                raise KeyError(f"Model configuration is missing key: {e}") from e
            if 'model_type' in model:
                raise ValueError(f'Model {subscribe_key!r}: model_type is not a configuration key; name the model class '
                                 f'with class_name (and module_name for a class TENT does not ship).')
            model_class = get_class_from_spec(model, Model)
            model_config = {**model.get('model_config', {}), 'demand_curve_points': self.demand_curve_points,
                            'subscribe_key': subscribe_key}
            self.models[subscribe_key] = model_class(model_config, parent=self)

    def model_flexibility(self, params: dict = None) -> List[float]:
        params = params if params else {}
        flexibility = [0.0] * self.demand_curve_points
        for model in self.models.values():
            try:
                prediction = list(model.predict_flexibility(params=params))
            except KeyError:
                _log.debug(f"Error making prediction for {model.subscribe_key}.")
                continue
            if len(prediction) != self.demand_curve_points:
                raise ValueError(f"Model {model.subscribe_key} returned {len(prediction)} flexibility points,"
                                 f" expected {self.demand_curve_points}.")
            for i, value in enumerate(prediction):
                flexibility[i] += value
        return flexibility

    def model_power(self, params: dict = None) -> float:
        params = params if params else {}
        q = 0.0
        for k, model in self.models.items():
            try:
                q += model.predict_power(params=params, set_point=None)
                # _log.debug(f'Power after {k}: {q}')
            except KeyError:
                _log.debug(f"Error making prediction for {model.subscribe_key}.")
        return q

#
# class BaseModel(object):
#     def __init__(self, actuation_key: str = None):
#         self.actuation_key = actuation_key
#
#     def predict_flexibility(self, params: dict = None) -> Iterable[float]:
#         pass
#
#     def predict_power(self, params: dict = None, set_point: float = None) -> float:
#         pass
#
#     def set_point_range(self, interval_start_time: datetime = None) -> Iterable[float]:
#         pass
#
#     def update_data(self, data: dict, now: datetime) -> None:
#         pass