"""Configure a node from the campus sample config against a fresh, temporary SQLite database."""
import json
from pathlib import Path

from sqlalchemy import select

from tests.mock_tn import MockTN

SAMPLE_CONFIG = Path(__file__).resolve().parents[1] / 'sample_configs' / 'tn.campus.json'


def test_configure_node_from_sample_config(tmp_path):
    with open(SAMPLE_CONFIG) as f:
        configs = json.load(f)
    # Never write to a persistent file: a database left over from an earlier schema would fail on new columns, since
    # create_all() does not alter existing tables.
    configs['data_manager']['db_params']['url'] = f'sqlite:///{tmp_path / "tn_startup.sqlite"}'
    tn = MockTN()
    tn.configure_node(configs)
    assert tn.market_series, 'the sample config defines market series'
    assert all(series.newest_market is not None for series in tn.market_series)
    assert all(series.market_kwargs == {} for series in tn.market_series)
    # Read the table directly: query_data() cannot yet decode the legacy DateTimeRangeType duration columns.
    table = tn.data_manager.tables['market_series']
    with tn.data_manager.engine.connect() as conn:
        rows = conn.execute(select(table.c.id)).all()
    assert len(rows) == len(tn.market_series)
    # TODO: Step through the state machine.
