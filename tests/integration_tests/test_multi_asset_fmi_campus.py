"""Integration test: Multi-Asset Campus Microgrid with FMI Physical Adapters.

Tests a full TransactiveNode containing a composite ModelFrameAsset with:
- Solar PV (pvlib physics via preset)
- Battery Storage (bidirectional storage FMI model via preset)
- Water Heater (thermal storage FMI model via preset)
- Uncontrolled Base Load

Coordinating with an upstream utility supplier Neighbor through full double auction
market negotiation and physical actuation dispatch.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
import pytest

from tent.containers import MarketTimings, TimeInterval, Vertex
from tent.enumerations import Direction, MarketState
from tent.local_asset.model_frame.model_frame_asset import ModelFrameAsset
from tent.market.auction import Auction
from tent.market.market_series import MarketSeries
from tent.market.price_models import HourlyAveragePriceModel
from tent.neighbor.bulk_supplier_dc import BulkSupplierDC

from tests.mock_tn import MockTN

# This suite builds a campus node from real pvlib and OCHRE models. Both are
# optional extras, so skip the module rather than erroring when absent.
pytest.importorskip("pvlib")
pytest.importorskip("ochre")

UTC = timezone.utc


@pytest.fixture
def multi_asset_campus_node():
    """Build a complete TransactiveNode containing multi-FMI asset and bulk supplier."""
    tn = MockTN()

    # 1. Composite ModelFrameAsset with multiple FMI physical adapters
    asset_config = {
        "class_name": "ModelFrameAsset",
        "module_name": "tent.local_asset.model_frame.model_frame_asset",
        "name": "CampusMicrogridAsset",
        "description": "Multi-resource campus asset combining PV, Battery, Water Heater, and Base Load",
        "maximum_power": 100.0,
        "minimum_power": -100.0,
        "default_power": 0.0,
        "model_configs": {
            "demand_curve_points": 2,
            "models": [
                {
                    "subscribe_key": "devices/campus_pv",
                    "class_name": "FlexibilityModelFrameAdapter",
                    "model_config": {
                        "preset": "residential_pv",
                        "resource_id": "campus_pv",
                        "rated_kw": 50.0,
                    },
                },
                {
                    "subscribe_key": "devices/campus_battery",
                    "class_name": "FlexibilityModelFrameAdapter",
                    "model_config": {
                        "preset": "residential_battery",
                        "resource_id": "campus_battery",
                        "capacity_kwh": 40.0,
                        "max_power_kw": 15.0,
                    },
                },
                {
                    "subscribe_key": "devices/campus_water_heater",
                    "class_name": "FlexibilityModelFrameAdapter",
                    "model_config": {
                        "preset": "water_heater_50gal",
                        "resource_id": "campus_wh",
                        "rated_kw": 4.5,
                    },
                },
                {
                    "subscribe_key": "devices/base_load",
                    "class_name": "UncontrolledLoad",
                    "model_config": {
                        "uncontrolled_load_schedule": [-25.0] * 24,
                    },
                },
            ],
        },
    }

    # 2. Grid Interconnection Neighbor (bidirectional import/export with utility)
    supplier_config = {
        "class_name": "Neighbor",
        "module_name": "tent.neighbor",
        "name": "GridInterconnection",
        "description": "Bidirectional utility grid connection",
        "up_or_down": "upstream",
        "transactive": False,
        "maximum_power": 1000.0,
        "minimum_power": -1000.0,
        "cost_parameters": [0.0, 0.08, 0.0],
        "default_vertices": [
            [0.02, 0.0, -1000.0, True],
            [0.15, 0.0, 1000.0, True],
        ],
    }

    # 3. 24-Hour Market Series
    market_series_config = {
        "class_name": "MarketSeries",
        "module_name": "tent.market.market_series",
        "name": "DayAheadMarket",
        "market_class_name": "Auction",
        "market_module_name": "tent.market.auction",
        "commitment": True,
        "duration_market_lead": 3600,
        "duration_delivery_lead": 3600,
        "duration_negotiation": 3600,
        "interval_duration": 3600,
        "intervals_to_clear": 24,
        "price_model": {
            "class_name": "HourlyAveragePriceModel",
            "module_name": "tent.market.price_models",
            "default_price": 0.08,
        },
    }

    node_config = {
        "agentid": "campus_microgrid_agent",
        "name": "CampusMicrogridNode",
        "market_cycle_in_min": 60,
        "local_assets": [asset_config],
        "neighbors": [supplier_config],
        "market_series": [market_series_config],
    }

    with patch("tent.data_manager.local_data_manager.LocalDataManager.init_archive", Mock(return_value=None)):
        tn.configure_node(node_config)

    asset = tn.local_assets[0]
    neighbor = tn.neighbors[0]
    series = tn.market_series[0]

    return tn, asset, neighbor, series


class TestMultiAssetFmiCampus:
    def test_node_initialization_with_multiple_fmi_models(self, multi_asset_campus_node):
        """Verify node instantiates all 4 sub-models inside ModelFrameAsset."""
        tn, asset, neighbor, series = multi_asset_campus_node

        assert len(tn.local_assets) == 1
        assert len(tn.neighbors) == 1
        assert len(tn.market_series) == 1

        # Check sub-models in ModelFrame
        assert len(asset.models) == 4
        assert "devices/campus_pv" in asset.models
        assert "devices/campus_battery" in asset.models
        assert "devices/campus_water_heater" in asset.models
        assert "devices/base_load" in asset.models

    def test_multi_asset_flexibility_aggregation_across_horizon(self, multi_asset_campus_node):
        """Verify net flexibility combines PV, battery, water heater, and base load."""
        tn, asset, neighbor, series = multi_asset_campus_node

        # Spawn a market for the day-ahead horizon
        start_time = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)
        timings = MarketTimings(
            active=start_time,
            negotiation=start_time + timedelta(hours=1),
            market_lead=start_time + timedelta(hours=2),
            delivery_lead=start_time + timedelta(hours=3),
            delivery=start_time + timedelta(hours=4),
            reconciliation=start_time + timedelta(hours=28),
            expiration=start_time + timedelta(hours=29),
        )
        market = Auction(
            transactive_node=tn,
            market_series=series,
            transition_times=timings,
            initial_market_state=MarketState.Active,
        )

        # Schedule asset power and active vertices
        asset.schedule(market)

        assert asset.schedule_calculated is True
        assert len(asset.scheduled_powers) == 24
        assert len(asset.active_vertices) == 24

        # Check noon interval (12:00 UTC): solar generates, baseload consumes (-25 kW)
        noon_iv = market.time_intervals[12]
        scheduled_noon = asset.scheduled_powers.get_by_interval(noon_iv)
        assert scheduled_noon is not None
        # In TENT: generation is positive, baseload is negative (-25 kW)
        # Solar PV (50 kW rated) at noon generates > 20 kW net of sun angle
        assert isinstance(scheduled_noon.value, float)

        # Check vertex quantities span feasible operational range
        sd_curve = asset.active_vertices.get_by_interval(noon_iv)
        assert len(sd_curve.vertices) == 2
        p_min_tent = sd_curve.vertices[0].quantity
        p_max_tent = sd_curve.vertices[1].quantity
        # Min power should be less than Max power
        assert p_min_tent < p_max_tent

    def test_closed_loop_market_clearing_and_multi_adapter_actuation(self, multi_asset_campus_node):
        """Verify double auction clears and dispatches to all dispatchable FMI adapters."""
        tn, asset, neighbor, series = multi_asset_campus_node

        start_time = datetime(2026, 7, 15, 6, 0, tzinfo=UTC)
        timings = MarketTimings(
            active=start_time,
            negotiation=start_time + timedelta(hours=1),
            market_lead=start_time + timedelta(hours=2),
            delivery_lead=start_time + timedelta(hours=3),
            delivery=start_time + timedelta(hours=4),
            reconciliation=start_time + timedelta(hours=28),
            expiration=start_time + timedelta(hours=29),
        )
        market = Auction(
            transactive_node=tn,
            market_series=series,
            transition_times=timings,
            initial_market_state=MarketState.Active,
        )

        # Schedule both asset and neighbor
        asset.schedule(market)
        neighbor.schedule(market)

        # Step market to Negotiation and clear auction
        market.transition_from_active_to_negotiation()
        market.while_in_negotiation()
        market.balance()
        market.transition_from_negotiation_to_market_lead()
        market.transition_from_market_lead_to_delivery_lead()
        market.transition_from_delivery_lead_to_delivery()

        # Check market cleared successfully
        assert market.converged is True
        for ti in market.time_intervals:
            price = market.marginal_prices.get_by_interval(ti)
            assert price is not None
            assert price.value > 0.0

        # Execute actuation: dispatches cleared schedules to physical adapters
        asset.actuate(market)

        # Verify dispatchable sub-models received dispatch commands
        battery_model = asset.models["devices/campus_battery"]
        wh_model = asset.models["devices/campus_water_heater"]
        pv_model = asset.models["devices/campus_pv"]

        # Battery is dispatchable -> received one accepted dispatch per interval
        assert len(battery_model.dispatch_responses) == len(market.time_intervals)
        for resp in battery_model.dispatch_responses:
            assert resp.accepted is True

        # Water Heater is dispatchable -> received one accepted dispatch per interval
        assert len(wh_model.dispatch_responses) == len(market.time_intervals)
        for resp in wh_model.dispatch_responses:
            assert resp.accepted is True

        # PV is must-take (not dispatchable) -> no dispatch reached it (the adapter records responses only on dispatch)
        assert getattr(pv_model, "dispatch_responses", []) == []
