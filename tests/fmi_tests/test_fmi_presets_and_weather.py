"""Tests for FlexibilityModelInterface aliases, preset configurations, and spiky weather modeling."""

import math
from datetime import datetime, timezone
import pytest

from tent import flexibility as fmi
from tent.flexibility import (
    FlexibilityModelAdapter,
    FlexibilityModelInterfaceError,
    Interval,
    PRESET_CONFIGS,
    from_config,
    load_adapter,
)
from tent.flexibility.adapters.pvlib import CloudCoverWeather, PvlibPvAdapter


def test_fmi_module_exports_and_aliases():
    """Verify the tent.flexibility package exposes all core contracts."""
    assert fmi.FlexibilityModelAdapter is not None
    assert fmi.FlexibilityEnvelope is not None
    assert fmi.EnvelopePoint is not None
    assert fmi.from_config is not None
    assert fmi.load_adapter is not None
    assert fmi.PRESET_CONFIGS is not None


def test_preset_configs_available():
    """Verify standard presets are registered."""
    expected_presets = [
        "residential_pv",
        "campus_pv",
        "cloudy_pv",
        "residential_hvac",
        "dwelling",
        "water_heater_50gal",
        "residential_battery",
        "ev_level2",
        "gridlabd_house",
        "gridlabd_water_heater",
        "gridlabd_battery",
        "gridlabd_ev",
        "distribution_feeder",
        "replay_csv",
    ]
    for p in expected_presets:
        assert p in PRESET_CONFIGS, f"Preset {p} missing from PRESET_CONFIGS"


def test_load_adapter_from_preset_name():
    """Test instantiating an adapter directly by preset name."""
    pv = from_config("residential_pv")
    assert isinstance(pv, PvlibPvAdapter)
    assert pv.identity.resource_id == "residential_pv_5kw"
    assert pv._rated_kw == 5.0

    # Test with parameter override
    pv_override = from_config("residential_pv", rated_kw=10.0)
    assert pv_override._rated_kw == 10.0


def test_cloudy_weather_spiky_profile():
    """Verify CloudCoverWeather produces non-smooth intermittent irradiance."""
    pv_clear = from_config("residential_pv")
    pv_cloudy = from_config("cloudy_pv")

    start = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
    intervals = Interval.sequence(start, duration_s=900, count=40)

    env_clear = pv_clear.get_envelope(intervals)
    env_cloudy = pv_cloudy.get_envelope(intervals)

    powers_clear = [pt.q_baseline_kw for pt in env_clear]
    powers_cloudy = [pt.q_baseline_kw for pt in env_cloudy]

    # Both produce generation (negative kW in load-positive convention) during mid-day
    midday_clear = powers_clear[10:30]
    midday_cloudy = powers_cloudy[10:30]

    assert any(p < -0.5 for p in midday_clear)
    assert any(p < -0.5 for p in midday_cloudy)

    # Cloudy profile has higher variance / spikiness (relative standard deviation of power differences)
    diffs_clear = [abs(midday_clear[i+1] - midday_clear[i]) for i in range(len(midday_clear)-1)]
    diffs_cloudy = [abs(midday_cloudy[i+1] - midday_cloudy[i]) for i in range(len(midday_cloudy)-1)]

    # Cloudy solar exhibits sharp dips and spikes
    assert max(diffs_cloudy) > 0.1
