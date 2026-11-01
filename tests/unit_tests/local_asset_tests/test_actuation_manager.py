"""ActuationManager without an occupancy manager (audit T-INC-06): the asset counts as always occupied."""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from tent.local_asset.actuation_manager.base import ActuationManager
from tent.local_asset.actuation_manager.external_target import ExternalTargetActuationManager


class Recording(ActuationManager):
    def actuate(self, mkt):
        super().actuate(mkt)

    def release(self, mkt):
        super().release(mkt)


def _market():
    mkt = Mock()
    mkt.name = 'M'
    mkt.transition_times.delivery = datetime(2026, 1, 1, tzinfo=timezone.utc)
    mkt.series.duration.market_interval = timedelta(hours=1)
    return mkt


def test_no_occupancy_manager_means_always_occupied():
    parent = Mock()
    parent.name = 'Asset'
    manager = Recording(parent=parent, occupancy_manager=None)
    assert manager.occupancy_manager is None
    manager.actuate(_market())
    assert manager.actuation_active is True


def test_unoccupied_schedule_blocks_actuation():
    parent = Mock()
    parent.name = 'Asset'
    occupancy = Mock()
    occupancy.check_schedule = Mock(return_value=False)
    manager = Recording(parent=parent, occupancy_manager=occupancy)
    manager.actuate(_market())
    assert manager.actuation_active is False


def test_external_target_manager_constructs_through_the_base_init():
    parent = Mock()
    parent.name = 'Asset'
    manager = ExternalTargetActuationManager(parent=parent, control_interval=5)
    assert manager.control_interval == timedelta(seconds=5) and manager.occupancy_manager is None
