import logging
import weakref

from abc import ABCMeta, abstractmethod
from weakref import proxy

from tent.config import ConfigModel, Configurable, NonNegativeSeconds, Setting
from tent.local_asset import LocalAsset
from tent.transactive_node import TransactiveNode
from tent.utils.helpers import safe_weak_proxy_callback

from ..occupancy_manager import OccupancyManager

_log = logging.getLogger(__name__)


class ActuationManager(Configurable, metaclass=ABCMeta):
    """
    Writes an asset's cleared schedule to its devices. Configured in a ModelFrameAsset's ``actuation_manager`` block
    (class_name and the settings of its Config); the asset supplies ``parent``, ``occupancy_manager`` and
    ``transactive_node`` when it builds the manager.
    """
    class Config(ConfigModel):
        active_onstart: bool = Setting(True, description='Whether actuation is allowed as soon as the node starts.')
        control_interval: NonNegativeSeconds = Setting(60, description='[s] interval between control actions.')

    def __init__(self, parent: LocalAsset = None, occupancy_manager: OccupancyManager = None,
                 transactive_node: TransactiveNode = None, config: ConfigModel | None = None, **kwargs):
        cfg = self._configure(config, kwargs)
        self.active_onstart = cfg.active_onstart
        self.control_interval = cfg.control_interval
        self.parent = parent if parent else LocalAsset(transactive_node)
        # Optional: without one, actuation is allowed whenever the manager is active (see actuate()).
        self.occupancy_manager = None
        self.set_occupancy_manager(occupancy_manager)
        self.tn = transactive_node

        self.actuation_active = False
        self.actuation_allowed = True if self.active_onstart else False
        self.actuation_periodic = None

    def set_occupancy_manager(self, occupancy_manager: OccupancyManager | None) -> None:
        """Use `occupancy_manager` (held weakly; the asset owns it) to decide when actuation is allowed."""
        self.occupancy_manager = (proxy(occupancy_manager, safe_weak_proxy_callback(self, 'occupancy_manager'))
                                  if occupancy_manager is not None else None)

    @abstractmethod
    def actuate(self, mkt):
        start_time = mkt.transition_times.delivery
        end_time = start_time + mkt.series.duration.market_interval
        _log.info(f'Actuating {self.parent.name} for market: {mkt.name} with actuator {self.__class__.__name__}'
                  f' from {start_time.strftime("%A, %B %d -- %H:%M:%S")}'
                  f' to {end_time.strftime("%A, %B %d -- %H:%M:%S")}')
        # No occupancy manager means the asset counts as always occupied (ModelFrameAsset applies the same rule).
        occupied = (self.occupancy_manager is None
                    or (self.occupancy_manager.check_schedule(start_time)
                        and self.occupancy_manager.check_schedule(end_time)))
        if occupied and self.actuation_allowed:
            self.actuation_active = True
        else:
            if self.actuation_active:
                self.release(mkt)
            self.actuation_active = False

    @abstractmethod
    def release(self, mkt):
        _log.info(f'Releasing actuation for {self.parent.name} with actuator {self.__class__.__name__}')
        # Override to implement release behavior
        pass

    # TODO: VOLTTRON Specific, and possibly unneeded?
    # def enable_callback(self, peer, sender, bus, topic, headers, message):
    #     state = message
    #     _log.debug(f"{self.parent.name} actuation disabled {not bool(state)}")
    #     if not self.actuation_allowed and not (bool(state)):
    #         return
    #     self.actuation_allowed = bool(state)
    #     self.update_actuation_state(state)
