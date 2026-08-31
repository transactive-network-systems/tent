"""Integration tests for FlexibilityModelFrameAdapter inside TENT's ModelFrame and ModelFrameAsset."""

from datetime import datetime, timezone
import pytest

from tent.local_asset.model_frame.models.base import ModelFrame
from tent.local_asset.model_frame.models.fmi_adapter import FlexibilityModelFrameAdapter


def test_fmi_modelframe_adapter_initialization_from_preset():
    """Verify FlexibilityModelFrameAdapter builds from a preset."""
    config = {
        "name": "SolarModel",
        "topic": "devices/rooftop_pv",
        "preset": "residential_pv",
    }
    model = FlexibilityModelFrameAdapter(config)
    assert model.topic == "devices/rooftop_pv"
    assert model.adapter is not None

    # Predict power at night (0.0 kW, 08:00 UTC = 01:00 AM Pacific)
    night_params = {"interval_time": datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)}
    p_night = model.predict_power(night_params)
    assert abs(p_night) < 1e-3

    # Predict power at noon (positive generation in TENT sign convention)
    noon_params = {"interval_time": datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)}  # 12:00 Pacific = 20:00 UTC
    p_noon = model.predict_power(noon_params)
    assert p_noon > 0.5  # Positive generation in TENT


def test_modelframe_aggregation_with_fmi_models():
    """Verify ModelFrame correctly aggregates multiple FMI models."""
    frame_config = {
        "demand_curve_points": 2,
        "models": [
            {
                "topic": "devices/pv",
                "model_type": "fmi",
                "model_config": {
                    "preset": "residential_pv",
                    "rated_kw": 5.0,
                },
            },
            {
                "topic": "devices/water_heater",
                "model_type": "fmi_adapter.FlexibilityModel",
                "model_config": {
                    "preset": "water_heater_50gal",
                    "rated_kw": 4.5,
                },
            },
        ],
    }

    mf = ModelFrame(frame_config)
    assert len(mf.models) == 2
    assert "devices/pv" in mf.models
    assert "devices/water_heater" in mf.models

    # Aggregate flexibility calculation
    noon_params = {"interval_time": datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)}
    flex = mf.model_flexibility(noon_params)
    assert len(flex) == 2
    assert isinstance(flex[0], float)
    assert isinstance(flex[1], float)
