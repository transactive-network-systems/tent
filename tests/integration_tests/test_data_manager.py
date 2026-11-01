"""What a configured node writes to its archive, read back through the data manager's queries."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tent.enumerations import MarketState, MeasurementType
from tests.integration_tests.fixtures.sample_weather_data import write_weather_csv
from tests.mock_tn import MockTN
from tests.stubs import clock_at

SAMPLE_CONFIG = Path(__file__).resolve().parents[2] / 'sample_configs' / 'tn.campus.json'


@pytest.fixture
def node(tmp_path):
    with open(SAMPLE_CONFIG) as f:
        configs = json.load(f)
    weather = tmp_path / 'weather.csv'
    write_weather_csv(weather, datetime(2017, 7, 1), hours=72)
    for ism in configs['information_service_models']:
        ism['weather_file_path'] = str(weather)
    configs['data_manager']['db_params']['url'] = f'sqlite:///{tmp_path / "archive.sqlite"}'
    tn = MockTN()
    tn.configure_node(configs)
    return tn


def clear(tn, market):
    """Step the node through scheduling and clearing: Active -> Negotiation -> MarketLead -> DeliveryLead."""
    times = market.transition_times
    for when in (times.active, times.active + timedelta(seconds=1), times.market_lead, times.delivery_lead):
        clock_at(when, tn.tz)
        tn.step()
    assert market.market_state == MarketState.DeliveryLead, market.market_state


def measurement_of(dm, source, measurement_type):
    headers = [h for h in dm.query_data('measurement', conditions={'source_id': source.id})
               if h['measurement_type'] is measurement_type]
    assert len(headers) == 1, f'{source.name}: one {measurement_type.name} measurement header'
    return headers[0]


def latest_values(dm, measurement_id) -> dict:
    """interval_id -> value of the newest row (values are append-only versions)."""
    rows = dm.query_data('measurement_value', conditions={'measurement_id': measurement_id}, order_by=['record_time'])
    return {row['interval_id']: row['value'] for row in rows}


def test_every_configured_component_has_its_rows(node):
    dm = node.data_manager
    components = [*node.meter_points, *node.information_service_models, *node.local_assets, *node.neighbors,
                  *node.market_series]
    component_ids = {row['id'] for row in dm.query_data('component')}
    assert {c.id for c in components} <= component_ids
    meter = dm.query_data('meter_point', conditions={'id': node.meter_points[0].id})
    assert len(meter) == 1 and meter[0]['class'] == 'MeterPoint' and meter[0]['measurement_type'] is MeasurementType.PowerReal
    forecast = dm.query_data('temperature_forecast_model')
    assert len(forecast) == 1 and forecast[0]['weather_file_path'].endswith('weather.csv')
    assert {row['id'] for row in dm.query_data('local_asset')} == {a.id for a in node.local_assets}
    assert {row['id'] for row in dm.query_data('neighbor')} == {n.id for n in node.neighbors}


def test_marginal_prices_are_archived_for_every_interval_as_versions(node):
    dm = node.data_manager
    market = node.market_series[0].newest_market
    clear(node, market)
    header = measurement_of(dm, market, MeasurementType.MarginalPrice)
    archived = latest_values(dm, header['id'])
    assert set(archived) == {ti.id for ti in market.time_intervals}, 'one price per interval'
    for ti in market.time_intervals:
        assert archived[ti.id] == market.marginal_prices.get_by_interval(ti).value
    # A later price for an interval is a new row, not an update: both versions can be read back.
    first = market.time_intervals[0]
    before = len(dm.query_data('measurement_value', conditions={'measurement_id': header['id'], 'interval_id': first.id}))
    market.marginal_prices.update(first, 0.0999)
    rows = dm.query_data('measurement_value', conditions={'measurement_id': header['id'], 'interval_id': first.id},
                         order_by=['record_time'])
    assert len(rows) == before + 1 and rows[-1]['value'] == 0.0999
    assert latest_values(dm, header['id'])[first.id] == 0.0999


def test_asset_schedules_are_archived_for_every_interval(node):
    dm = node.data_manager
    market = node.market_series[0].newest_market
    clear(node, market)
    for asset in node.local_assets:
        header = measurement_of(dm, asset, MeasurementType.ScheduledPower)
        archived = latest_values(dm, header['id'])
        assert {ti.id for ti in market.time_intervals} <= set(archived), asset.name
        for ti in market.time_intervals:
            assert archived[ti.id] == asset.scheduled_powers.get_by_interval(ti).value, (asset.name, ti.name)


def test_meter_readings_are_archived_as_they_arrive(node):
    dm = node.data_manager
    meter = node.meter_points[0]
    when = datetime(2017, 7, 1, 0, 30, tzinfo=timezone.utc)
    meter.set_meter_value(12.5, when)
    meter.set_meter_value(13.0, when + timedelta(seconds=10))
    rows = dm.query_data('meter_reading', conditions={'meter_id': meter.id}, order_by=['reading_time'])
    assert [(r['reading_time'], r['value']) for r in rows] == [(when, 12.5), (when + timedelta(seconds=10), 13.0)]
    assert meter.current_measurement == 13.0 and meter.last_updated == when + timedelta(seconds=10)
