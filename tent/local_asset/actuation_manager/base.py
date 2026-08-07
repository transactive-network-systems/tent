import logging
import weakref

from abc import ABCMeta, abstractmethod

from tent.local_asset import LocalAsset
from tent.transactive_node import TransactiveNode

from ..occupancy_manager import OccupancyManager

_log = logging.getLogger(__name__)


class ActuationManager(metaclass=ABCMeta):
    def __init__(self,
                 active_onstart: bool = True,
                 control_interval: float = 60,
                 # outputs: Dict[dict] = None,  # TODO: How is this handled in parent?
                 parent: LocalAsset = None,
                 occupancy_manager: OccupancyManager = None,
                 transactive_node: TransactiveNode = None):
        self.active_onstart = active_onstart
        self.control_interval = control_interval
        # self.outputs = outputs if outputs else []
        self.parent = parent if parent else LocalAsset(transactive_node)
        # TODO: Does this really need to be a weak reference? Should probably be a proxy if it does.
        self.occupancy_manager = None if occupancy_manager is None else weakref.ref(occupancy_manager)
        self.tn = None if transactive_node is None else weakref.ref(transactive_node)

        self.actuation_active = False
        self.actuation_allowed = True if self.active_onstart else False
        self.actuation_periodic = None

        # if self.outputs:
        #     if self.tn and self.tn():
        #         tn = self.tn()
        #         tn.vip.pubsub.subscribe(peer='pubsub', prefix=self.enable_topic, callback=self.update_actuation_state)
        #     if self.active_onstart:
        #         self.update_actuation_state(True)
        # else:
        #     _log.info(f"{self.parent.name} - cannot initialize actuation state, no configured outputs.")

    @abstractmethod
    def actuate(self, mkt):
        start_time = mkt.marketClearingTime + mkt.deliveryLeadTime
        end_time = start_time + mkt.intervalDuration
        ocm = self.occupancy_manager() if self.occupancy_manager else self.occupancy_manager
        if ocm and ocm.check_schedule(start_time) and ocm.check_schedule(end_time) and self.actuation_allowed:
            self.actuation_active = True
        else:
            if self.actuation_active:
                self.release(mkt)
            self.actuation_active = False

    @abstractmethod
    def release(self, mkt):
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
