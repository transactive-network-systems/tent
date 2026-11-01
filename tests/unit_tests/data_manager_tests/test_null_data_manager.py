"""NullDataManager: accepts and discards records; the default when a node has no data_manager block (audit T-INC-01)."""
import pytest

from tent.data_manager import DataManager
from tent.data_manager.null_data_manager import NullDataManager
from tent.market import Market
from tent.market.market_series import MarketSeries
from tent.utils.timer import Timer
from tests.mock_tn import MockTN


def test_records_are_accepted_and_discarded():
    dm = NullDataManager(parent=None)
    assert isinstance(dm, DataManager) and dm.archive_ready
    dm.archive_data({'table_name': 'anything', 'data': {'id': 1}})
    dm.archive_data([{'table_name': 'anything', 'data': {'id': 2}}, {'table_name': 'other', 'data': {}}])
    dm.archive_data(None)
    assert dm.discarded == 3
    assert not dm.archival_queue, 'nothing is ever queued'


def test_definitions_are_still_parsed():
    dm = NullDataManager(parent=None)
    dm.add_tables(MarketSeries.get_archive_definitions())
    assert 'market_series' in dm.tables and 'component' in dm.tables
    with pytest.raises(ValueError, match='Unknown column type'):
        dm.add_tables([{'name': 'broken', 'columns': [{'name': 'x', 'type': 'NoSuchType'}]}])


def test_node_without_a_data_manager_block_gets_the_null_manager(caplog):
    Timer.simulation = False
    tn = MockTN()
    tn.configure_node({'name': 'bare', 'market_series': [{'class_name': 'MarketSeries', 'name': 'Core',
                                                          'module_name': 'tent.market.market_series',
                                                          'market_class_name': 'Market', 'market_module_name': 'tent.market'}]})
    assert isinstance(tn.data_manager, NullDataManager)
    assert any('nothing will be archived' in r.message for r in caplog.records)
    assert tn.market_series[0].newest_market is not None, 'the node ran its configuration and archived into the void'
    assert tn.data_manager.discarded > 0


def test_null_manager_is_configurable_by_class_name():
    Timer.simulation = False
    tn = MockTN()
    tn.configure_node({'name': 'bare', 'data_manager': {'class_name': 'NullDataManager'}})
    assert isinstance(tn.data_manager, NullDataManager)


def test_a_component_built_before_configuration_archives_into_the_mock_node():
    Timer.simulation = False
    tn = MockTN()
    series = MarketSeries(transactive_node=tn, market_class=Market, name='Core')
    assert series.newest_market is not None
    assert tn.data_manager.discarded > 0
