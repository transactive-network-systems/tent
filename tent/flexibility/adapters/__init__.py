"""Adapters: the disposable translation layer.

Adapters connect external physics models and simulators (pvlib, GridLAB-D, OCHRE, replay files,
and future EnergyPlus models) to the tent.flexibility contract.

Subpackages by model origin:
- :mod:`der_interface.adapters.gridlabd`: GridLAB-D Water Heater, HVAC House, EV, and Battery Inverter adapters.
- :mod:`der_interface.adapters.ochre`: NREL OCHRE Water Heater, HVAC Dwelling, EV, Battery, and Dwelling adapters.
- :mod:`der_interface.adapters.pvlib`: Solar PV models backed by pvlib.
- :mod:`der_interface.adapters.replay`: Deterministic CSV / in-memory replay adapters.

All adapters are also lazily accessible directly from :mod:`der_interface.adapters`.
"""

from __future__ import annotations

import importlib

from .factory import PRESET_CONFIGS, from_config, load_adapter

# The adapter subpackages need their simulators' optional dependencies (pandas, pvlib, ochre), so they are loaded on
# first use, by name through __getattr__, instead of being imported here.
_SUBPACKAGES = ("gridlabd", "ochre", "pvlib", "replay")

__all__ = [
    "from_config",
    "load_adapter",
    "PRESET_CONFIGS",
    "ReplayAdapter",
    "PvlibPvAdapter",
    "GridlabdWaterHeaterAdapter",
    "GridlabdHvacAdapter",
    "GridlabdBatteryInverterAdapter",
    "GridlabdEvAdapter",
    "GridlabdFeederAdapter",
    "GridlabdEngine",
    "OchreBatteryAdapter",
    "OchreWaterHeaterAdapter",
    "OchreHvacAdapter",
    "OchreEvAdapter",
    "OchreDwellingAdapter",
    "gridlabd",
    "ochre",
    "pvlib",
    "replay",
]


def __getattr__(name: str):
    if name in _SUBPACKAGES:
        return importlib.import_module(f"{__name__}.{name}")
    if name == "ReplayAdapter":
        from .replay.replay import ReplayAdapter

        return ReplayAdapter
    if name == "PvlibPvAdapter":
        from .pvlib.pv import PvlibPvAdapter

        return PvlibPvAdapter
    if name == "GridlabdWaterHeaterAdapter":
        from .gridlabd.water_heater import GridlabdWaterHeaterAdapter

        return GridlabdWaterHeaterAdapter
    if name == "GridlabdHvacAdapter":
        from .gridlabd.hvac import GridlabdHvacAdapter

        return GridlabdHvacAdapter
    if name == "GridlabdBatteryInverterAdapter":
        from .gridlabd.battery import GridlabdBatteryInverterAdapter

        return GridlabdBatteryInverterAdapter
    if name == "GridlabdEvAdapter":
        from .gridlabd.ev import GridlabdEvAdapter

        return GridlabdEvAdapter
    if name == "GridlabdFeederAdapter":
        from .gridlabd.feeder import GridlabdFeederAdapter

        return GridlabdFeederAdapter
    if name == "GridlabdEngine":
        from .gridlabd.engine import GridlabdEngine

        return GridlabdEngine
    if name == "OchreBatteryAdapter":
        from .ochre.battery import OchreBatteryAdapter

        return OchreBatteryAdapter
    if name == "OchreWaterHeaterAdapter":
        from .ochre.water_heater import OchreWaterHeaterAdapter

        return OchreWaterHeaterAdapter
    if name == "OchreHvacAdapter":
        from .ochre.hvac import OchreHvacAdapter

        return OchreHvacAdapter
    if name == "OchreEvAdapter":
        from .ochre.ev import OchreEvAdapter

        return OchreEvAdapter
    if name == "OchreDwellingAdapter":
        from .ochre.dwelling import OchreDwellingAdapter

        return OchreDwellingAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
