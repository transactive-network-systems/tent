"""Node configuration: timezone handling (audit T-02)."""
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest

from tent.utils.timer import Timer
from tests.mock_tn import MockTN

LOCAL_DM = {'class_name': 'LocalDataManager', 'module_name': 'tent.data_manager.local_data_manager', 'db_params': {'url': 'sqlite://'}}


def _configure(configs):
    Timer.simulation = False
    tn = MockTN()
    with patch('tent.data_manager.local_data_manager.LocalDataManager.init_archive', Mock(return_value=None)):
        # get_class_from_spec consumes the spec it is given (class_name, module_name), so each node gets its own copy.
        tn.configure_node({'data_manager': deepcopy(LOCAL_DM), **configs})
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
