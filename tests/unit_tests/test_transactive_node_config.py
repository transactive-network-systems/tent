"""Node configuration: timezone handling (audit T-02)."""
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest

from tests.mock_tn import MockTN

LOCAL_DM = {'class_name': 'LocalDataManager', 'module_name': 'archivable.local_data_manager', 'db_params': {'url': 'sqlite://'}}


def _configure(configs):
    snapshot = deepcopy(LOCAL_DM)
    tn = MockTN()
    with patch('archivable.local_data_manager.LocalDataManager.init_archive', Mock(return_value=None)):
        tn.configure_node({'data_manager': LOCAL_DM, **configs})
    assert LOCAL_DM == snapshot, 'configuring reads the dictionaries and leaves them as they were'
    return tn


def test_timezone_defaults_to_utc_not_the_host_abbreviation():
    tn = _configure({})
    assert tn.tz == 'UTC'
    assert tn.get_record()['tz'] == 'UTC'


def test_iana_zone_names_are_accepted():
    assert _configure({'tz': 'America/Los_Angeles'}).tz == 'America/Los_Angeles'


@pytest.mark.parametrize('bad', ['PDT', 'EST5EDT ', 'Pacific', ''])
def test_timezone_abbreviations_and_junk_are_rejected(bad):
    with pytest.raises(ValueError, match='IANA zone name'):
        _configure({'tz': bad})


def test_a_node_can_adopt_an_existing_run_identity():
    from datetime import datetime, timezone
    from uuid6 import uuid7
    first = _configure({})
    rebuilt = MockTN(node_id=first.id, created=first.real_time_created)
    assert rebuilt.id == first.id and rebuilt.real_time_created == first.real_time_created
    assert MockTN().id != first.id, 'without an identity to adopt a node is a new run'


def test_simulation_start_time_keeps_an_explicit_offset():  # audit T-20
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    tn = _configure({'tz': 'UTC', 'simulation': True, 'simulation_one_hour_in_seconds': 60,
                     'simulation_start_time': '2017-07-01T00:00:00-07:00'})
    assert tn.simulation_start_time == datetime(2017, 7, 1, 7, 0, tzinfo=timezone.utc)
    assert tn.simulation_start_time.tzinfo == ZoneInfo('UTC'), 'stated in the node zone'
    tn = _configure({'tz': 'America/Los_Angeles', 'simulation': True, 'simulation_one_hour_in_seconds': 60,
                     'simulation_start_time': '2017-07-01T00:00:00'})
    assert tn.simulation_start_time == datetime(2017, 7, 1, 0, 0, tzinfo=ZoneInfo('America/Los_Angeles')), \
        'a start without an offset is in the node zone'
    tn = _configure({'tz': 'UTC', 'simulation': True, 'simulation_one_hour_in_seconds': 60,
                     'simulation_start_time': '2017-07-01T00:00:00', 'simulation_anchor_time': '2026-10-31T12:00:00-07:00'})
    assert tn.simulation_anchor_time == datetime(2026, 10, 31, 19, 0, tzinfo=timezone.utc)


def test_configuration_dicts_are_left_as_given():  # audit T-29 (node side)
    from copy import deepcopy
    configs = {
        'name': 'twin', 'data_manager': {'class_name': 'NullDataManager'},
        'local_assets': [{'class_name': 'LocalAsset', 'module_name': 'tent.local_asset', 'name': 'Load',
                          'default_power': -5.0}],
        'neighbors': [{'class_name': 'Neighbor', 'module_name': 'tent.neighbor', 'name': 'Peer', 'maximum_power': 10.0}],
        'market_series': [{'class_name': 'MarketSeries', 'module_name': 'tent.market.market_series', 'name': 'Core',
                           'market_class_name': 'Auction', 'market_module_name': 'tent.market.auction'}],
    }
    before = deepcopy(configs)
    first, second = MockTN(), MockTN()
    first.configure_node(configs)
    assert configs == before, 'configuring a node does not consume its configuration'
    second.configure_node(configs)  # the same dict configures a second node
    assert [a.name for a in second.local_assets] == ['Load'] and second.market_series[0].market_class.__name__ == 'Auction'
    assert configs == before
