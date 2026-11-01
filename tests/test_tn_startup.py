"""Configure a node from the campus sample config against a fresh, temporary SQLite database."""
import json
from pathlib import Path

from datetime import timedelta

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
    # The day-ahead series is configured not to actuate (its deliveries are refined by the real-time series).
    by_name = {series.name: series.market_kwargs for series in tn.market_series}
    assert by_name == {'Day-Ahead_Auction': {'actuate_assets': False}, 'Real-Time_Auction': {'actuate_assets': True}}
    rows = tn.data_manager.query_data('market_series')
    assert len(rows) == len(tn.market_series)
    # Duration columns are SQLAlchemy Interval and read back as timedelta.
    assert all(isinstance(r['market_interval_duration'], timedelta) for r in rows)
    # TODO: Step through the state machine.
