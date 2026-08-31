"""OCHRE Physical Simulation Adapters.

Connects to NREL's OCHRE (Object-oriented Controllable High-resolution Residential Energy Model)
to provide high-fidelity physical simulation of water heaters, HVAC dwellings, batteries,
and electric vehicles for transactive market coordination.
"""

# Ensure Pint UnitRegistry compatibility for OCHRE under Pint >= 0.24
try:
    from ochre.utils.units import ureg
    try:
        ureg.define("inch_H2O_39F = 249.08891 * Pa")
    except Exception:
        pass
except ImportError:
    pass

# Patch pandas join for OCHRE import_occupancy_schedule compatibility under pandas >= 2.0
try:
    import ochre.utils.schedule as _ochre_sched
    _orig_import_sched = _ochre_sched.import_occupancy_schedule

    def _patched_import_occupancy_schedule(*args, **kwargs):
        import pandas as pd
        _orig_join = pd.DataFrame.join
        def _patched_join(self, other, *jargs, **jkwargs):
            res = _orig_join(self, other, *jargs, **jkwargs)
            cols_to_drop = [c for c in res.columns if str(c).startswith("key_")]
            if cols_to_drop:
                res = res.drop(columns=cols_to_drop)
            return res
        pd.DataFrame.join = _patched_join
        try:
            return _orig_import_sched(*args, **kwargs)
        finally:
            pd.DataFrame.join = _orig_join

    _ochre_sched.import_occupancy_schedule = _patched_import_occupancy_schedule
except (ImportError, AttributeError):
    pass

from .battery import OchreBatteryAdapter
from .dwelling import OchreDwellingAdapter
from .ev import OchreEvAdapter
from .hvac import OchreHvacAdapter
from .water_heater import OchreWaterHeaterAdapter

__all__ = [
    "OchreBatteryAdapter",
    "OchreDwellingAdapter",
    "OchreEvAdapter",
    "OchreHvacAdapter",
    "OchreWaterHeaterAdapter",
]
