"""Factory for building physical flexibility model adapters from configurations or presets."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from ..errors import AdapterError

PRESET_CONFIGS: dict[str, dict[str, Any]] = {
    "residential_pv": {
        "model_type": "pvlib.pv",
        "resource_id": "residential_pv_5kw",
        "rated_kw": 5.0,
        "location": {"latitude": 46.28, "longitude": -119.28, "tz": "US/Pacific"},
        "system": {"surface_tilt": 25.0, "surface_azimuth": 180.0},
        "weather": {"kind": "clearsky"},
    },
    "rooftop_pv_5kw": {
        "model_type": "pvlib.pv",
        "resource_id": "rooftop_pv_5kw",
        "rated_kw": 5.0,
        "location": {"latitude": 46.28, "longitude": -119.28, "tz": "US/Pacific"},
        "system": {"surface_tilt": 25.0, "surface_azimuth": 180.0},
        "weather": {"kind": "clearsky"},
    },
    "campus_pv": {
        "model_type": "pvlib.pv",
        "resource_id": "campus_pv_120kw",
        "rated_kw": 120.0,
        "location": {"latitude": 46.28, "longitude": -119.28, "tz": "US/Pacific"},
        "system": {"surface_tilt": 30.0, "surface_azimuth": 180.0},
        "weather": {"kind": "tmy", "path": "WA-Pasco.tmy3"},
    },
    "cloudy_pv": {
        "model_type": "pvlib.pv",
        "resource_id": "cloudy_pv_5kw",
        "rated_kw": 5.0,
        "location": {"latitude": 46.28, "longitude": -119.28, "tz": "US/Pacific"},
        "system": {"surface_tilt": 25.0, "surface_azimuth": 180.0},
        "weather": {"kind": "cloudy", "cloud_factor": 0.75, "volatility": 0.6},
    },
    "residential_hvac": {
        "model_type": "ochre.hvac",
        "resource_id": "residential_hvac",
        "rated_kw": 6.0,
        "cooling_capacity_w": 10550.0,
        "cooling_cop": 3.8,
        "deadband_c": 1.0,
    },
    "small_office": {
        "model_type": "ochre.dwelling",
        "resource_id": "small_office_dwelling",
        "rated_kw": 15.0,
    },
    "dwelling": {
        "model_type": "ochre.dwelling",
        "resource_id": "dwelling_up11",
        "rated_kw": 15.0,
    },
    "water_heater_50gal": {
        "model_type": "ochre.water_heater",
        "resource_id": "ochre_water_heater_50gal",
        "rated_kw": 4.5,
        "volume_gal": 50.0,
        "setpoint_c": 51.67,
    },
    "residential_battery": {
        "model_type": "ochre.battery",
        "resource_id": "ochre_battery_10kwh",
        "rated_kw": 5.0,
        "capacity_kwh": 13.5,
        "initial_soc": 0.5,
    },
    "ev_level2": {
        "model_type": "ochre.ev",
        "resource_id": "ochre_ev_level2",
        "rated_kw": 7.7,
        "battery_capacity_kwh": 75.0,
        "initial_soc": 0.4,
    },
    "gridlabd_house": {
        "model_type": "gridlabd.hvac",
        "resource_id": "gld_house_1",
        "rated_kw": 5.5,
    },
    "gridlabd_water_heater": {
        "model_type": "gridlabd.water_heater",
        "resource_id": "gld_water_heater_1",
        "rated_kw": 4.5,
    },
    "gridlabd_battery": {
        "model_type": "gridlabd.battery",
        "resource_id": "gld_battery_1",
        "rated_kw": 5.0,
        "capacity_kwh": 13.5,
    },
    "gridlabd_ev": {
        "model_type": "gridlabd.ev",
        "resource_id": "gld_ev_1",
        "rated_kw": 7.7,
    },
    "distribution_feeder": {
        "model_type": "gridlabd.feeder",
        "resource_id": "gld_feeder_13node",
        "rated_kw": 5000.0,
    },
    "replay_csv": {
        "model_type": "replay",
        "resource_id": "replay_csv_asset",
        "csv_path": "replay_data.csv",
    },
}

# Aliases
PRESET_CONFIGS["pv"] = PRESET_CONFIGS["residential_pv"]
PRESET_CONFIGS["solar"] = PRESET_CONFIGS["residential_pv"]
PRESET_CONFIGS["hvac"] = PRESET_CONFIGS["residential_hvac"]
PRESET_CONFIGS["water_heater"] = PRESET_CONFIGS["water_heater_50gal"]
PRESET_CONFIGS["battery"] = PRESET_CONFIGS["residential_battery"]
PRESET_CONFIGS["ev"] = PRESET_CONFIGS["ev_level2"]
PRESET_CONFIGS["replay"] = PRESET_CONFIGS["replay_csv"]


def from_config(config_or_preset: str | Mapping[str, Any], **overrides: Any) -> Any:
    """Instantiate any flexibility adapter from a preset name, config path, or dict config.

    Accepts:
        - Preset name string: e.g. ``"small_office"``, ``"residential_pv"``, ``"battery"``
        - JSON configuration file path: e.g. ``"configs/pvlib/solar_pv.json"``
        - Configuration dictionary with ``model_type`` or ``preset``
        - Keyword arguments to override or supply extra simulation parameters (**kwargs)
    """
    cfg: dict[str, Any] = {}

    if isinstance(config_or_preset, str):
        preset_key = config_or_preset.lower().strip()
        if preset_key in PRESET_CONFIGS:
            cfg = dict(PRESET_CONFIGS[preset_key])
        elif os.path.exists(config_or_preset):
            with open(config_or_preset, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        else:
            # Check package configs directory
            pkg_root = Path(__file__).resolve().parents[3]
            candidates = [
                pkg_root / "configs" / config_or_preset,
                pkg_root / "configs" / f"{config_or_preset}.json",
                pkg_root / "configs" / "ochre" / f"{config_or_preset}.json",
                pkg_root / "configs" / "gridlabd" / f"{config_or_preset}.json",
                pkg_root / "configs" / "pvlib" / f"{config_or_preset}.json",
                pkg_root / "configs" / "replay" / f"{config_or_preset}.json",
            ]
            found = False
            for c in candidates:
                if c.exists():
                    with open(c, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    found = True
                    break
            if not found:
                raise AdapterError(
                    f"unknown preset or configuration file: {config_or_preset!r}. "
                    f"Valid presets: {sorted(PRESET_CONFIGS.keys())}"
                )
    elif isinstance(config_or_preset, Mapping):
        cfg = dict(config_or_preset)
        if "preset" in cfg:
            preset_name = str(cfg.pop("preset")).lower().strip()
            if preset_name in PRESET_CONFIGS:
                base = dict(PRESET_CONFIGS[preset_name])
                base.update(cfg)
                cfg = base
    else:
        raise AdapterError(f"expected str or dict config, got {type(config_or_preset).__name__}")

    # Apply overrides and extra_params
    cfg.update(overrides)
    extra_params = cfg.pop("extra_params", {})
    if isinstance(extra_params, dict):
        for k, v in extra_params.items():
            if k not in cfg:
                cfg[k] = v

    model_type = str(cfg.pop("model_type", "")).lower().strip()
    if not model_type:
        # Infer model_type if not specified
        if "location" in cfg or "pvlib" in cfg:
            model_type = "pvlib.pv"
        elif "csv_path" in cfg:
            model_type = "replay"
        elif "gridlabd" in cfg:
            model_type = "gridlabd.hvac"
        elif "ochre" in cfg:
            model_type = "ochre.dwelling"
        else:
            raise AdapterError("configuration must specify 'model_type' or a valid 'preset'")

    # Instantiate the corresponding adapter
    if model_type in ("pvlib.pv", "pvlib", "pv", "solar"):
        from .pvlib.pv import PvlibPvAdapter
        return PvlibPvAdapter.from_config(**cfg)

    elif model_type in ("ochre.dwelling", "dwelling", "small_office"):
        from .ochre.dwelling import OchreDwellingAdapter
        return OchreDwellingAdapter.from_config(**cfg)

    elif model_type in ("ochre.hvac", "ochre_hvac"):
        from .ochre.hvac import OchreHvacAdapter
        return OchreHvacAdapter.from_config(**cfg)

    elif model_type in ("ochre.water_heater", "ochre_water_heater", "ochre_wh"):
        from .ochre.water_heater import OchreWaterHeaterAdapter
        return OchreWaterHeaterAdapter.from_config(**cfg)

    elif model_type in ("ochre.battery", "ochre_battery", "ochre_bess"):
        from .ochre.battery import OchreBatteryAdapter
        return OchreBatteryAdapter.from_config(**cfg)

    elif model_type in ("ochre.ev", "ochre_ev", "ochre_evcharger"):
        from .ochre.ev import OchreEvAdapter
        return OchreEvAdapter.from_config(**cfg)

    elif model_type in ("gridlabd.hvac", "gridlabd_hvac", "gridlabd_house"):
        from .gridlabd.hvac import GridlabdHvacAdapter
        return GridlabdHvacAdapter.from_config(**cfg)

    elif model_type in ("gridlabd.water_heater", "gridlabd_water_heater", "gridlabd_wh"):
        from .gridlabd.water_heater import GridlabdWaterHeaterAdapter
        return GridlabdWaterHeaterAdapter.from_config(**cfg)

    elif model_type in ("gridlabd.battery", "gridlabd_battery"):
        from .gridlabd.battery import GridlabdBatteryInverterAdapter
        return GridlabdBatteryInverterAdapter.from_config(**cfg)

    elif model_type in ("gridlabd.ev", "gridlabd_ev"):
        from .gridlabd.ev import GridlabdEvAdapter
        return GridlabdEvAdapter.from_config(**cfg)

    elif model_type in ("gridlabd.feeder", "gridlabd_feeder"):
        from .gridlabd.feeder import GridlabdFeederAdapter
        return GridlabdFeederAdapter.from_config(**cfg)

    elif model_type in ("replay", "replay_csv", "replay.replay"):
        from .replay.replay import ReplayAdapter
        # Auto-resolve relative replay CSV path if needed
        if "csv_path" in cfg and not os.path.exists(cfg["csv_path"]):
            pkg_root = Path(__file__).resolve().parents[3]
            bundled_csv = pkg_root / "models" / "replay" / cfg["csv_path"]
            if bundled_csv.exists():
                cfg["csv_path"] = str(bundled_csv)
            else:
                bundled_csv2 = pkg_root / "models" / "replay" / Path(cfg["csv_path"]).name
                if bundled_csv2.exists():
                    cfg["csv_path"] = str(bundled_csv2)
        return ReplayAdapter.from_config(**cfg)

    raise AdapterError(f"unsupported model_type: {model_type!r}")


# Alias
load_adapter = from_config
