"""NullDataManager: accepts and discards records; the default when a node has no data_manager block (audit T-INC-01)."""
import pytest

from archivable import DataManager, NullDataManager

import tent.data_manager
from tent.enumerations import MarketState
from tent.market import Market
from tent.market.market_series import MarketSeries
from tent.utils.timer import Timer
from tests.mock_tn import MockTN


def test_definitions_with_tent_enumerations_are_parsed_once_prepared():
    dm = tent.data_manager.prepare(NullDataManager(parent=None))
    assert isinstance(dm, DataManager) and dm.archive_ready and dm.archive_schema.application == 'TENT'
    dm.add_tables(MarketSeries.get_archive_definitions())
    dm.add_tables(Market.get_archive_definitions())  # market_state_change names MarketState
    assert {'market_series', 'component', 'market_state_change'} <= set(dm.tables)
    assert dm.tables['market_state_change'].c.state.type.enums == [m.name for m in MarketState]
    with pytest.raises(ValueError, match="Unknown enumeration 'NoSuchEnum'"):
        dm.add_tables([{'name': 'broken', 'columns': [{'name': 'x', 'enumeration': 'NoSuchEnum'}]}])


def test_an_unprepared_manager_refuses_tables_that_name_an_enumeration():
    with pytest.raises(ValueError, match="Unknown enumeration 'MarketTypes'.*register_enumerations"):
        NullDataManager(parent=None).add_tables(Market.get_archive_definitions())


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
