"""GridLAB-D engine session and connection manager.

Mediates communication between der-interface adapters and a live GridLAB-D
simulation instance via Python bindings (`gridlabd`).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ...errors import AdapterError

_log = logging.getLogger(__name__)


def _parse_gld_number(val: Any) -> float:
    """Parse numeric values from GridLAB-D property strings (e.g., '72.5 degF', '4.5 kW', '120+0j V')."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, complex):
        return float(val.real)

    val_str = str(val).strip()
    if not val_str:
        return 0.0

    # Complex power or units like '1200+300j' or '72.5 degF'
    match = re.search(r"^([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)", val_str)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return 0.0


@runtime_checkable
class GridlabdSession(Protocol):
    """Protocol satisfied by real GridLAB-D instances."""

    def get_property(self, object_name: str, property_name: str) -> tuple[int, Any]: ...
    def set_property(self, object_name: str, property_name: str, value: Any) -> tuple[int, Any]: ...
    def step(self) -> tuple[int, Any]: ...


class GridlabdEngine:
    """Session manager that coordinates property queries and setpoints for GridLAB-D DERs."""

    _shared_instances: dict[str, GridlabdEngine] = {}

    def __init__(self, session: Any = None, model_path: str | Path | None = None) -> None:
        self.model_path = str(model_path) if model_path else None
        self.session: Any

        if session is not None:
            self.session = session
        else:
            self.session = self._init_session(self.model_path)

    @classmethod
    def get_or_create(cls, model_path: str | Path | None = None) -> "GridlabdEngine":
        """Reuse a shared engine instance for the same model file."""
        key = str(model_path or "default")
        if key not in cls._shared_instances:
            cls._shared_instances[key] = cls(model_path=model_path)
        return cls._shared_instances[key]

    def _init_session(self, model_path: str | None) -> Any:
        try:
            import gridlabd  # type: ignore
        except ImportError as err:
            raise AdapterError(
                "GridLAB-D Python bindings ('gridlabd') are not installed. "
                "Install them via 'pip install -e gridlab-d/python_bindings' to run GridLAB-D models."
            ) from err

        gld = getattr(gridlabd, "IsolatedGridLabD", gridlabd.GridLabD)()
        if model_path:
            p = Path(model_path)
            if not p.exists():
                pkg_root = Path(__file__).resolve().parent.parent.parent.parent.parent
                candidates = [
                    pkg_root / model_path,
                    pkg_root / "models" / "gridlabd" / p.name,
                    pkg_root / "models" / p.name,
                    pkg_root.parent / model_path,
                    pkg_root.parent / "der-interface" / model_path,
                    pkg_root.parent / "der-interface" / "models" / "gridlabd" / p.name,
                    Path.cwd() / model_path,
                    Path.cwd().parent / model_path,
                    Path.cwd() / "der-interface" / model_path,
                    Path.cwd() / "models" / "gridlabd" / p.name,
                    Path.cwd().parent / "models" / "gridlabd" / p.name,
                ]
                for cand in candidates:
                    if cand.exists():
                        p = cand
                        break
            if p.exists():
                p_abs = p.resolve()
                if hasattr(gld, "set_working_directory"):
                    gld.set_working_directory(str(p_abs.parent))
                if hasattr(gld, "load_glm"):
                    ret = gld.load_glm(p_abs.name, workdir=str(p_abs.parent))
                else:
                    ret = gld.load(str(p_abs))

                if ret != 0:
                    err_msgs = []
                    if hasattr(gld, "get_messages"):
                        err_msgs = [m["message"] for m in gld.get_messages() if m.get("type") in ("ERROR", "FATAL")]
                    detail = (": " + "; ".join(err_msgs)) if err_msgs else f" (error code {ret})"
                    raise AdapterError(f"GridLAB-D failed to load model '{p_abs}'{detail}")

                if hasattr(gld, "setup_after_load"):
                    try:
                        gld.setup_after_load()
                    except Exception as e:
                        _log.warning(f"setup_after_load warning: {e}")

                # Check for critical compilation errors in messages
                if hasattr(gld, "get_messages"):
                    fatal_msgs = [m["message"] for m in gld.get_messages() if m.get("type") in ("ERROR", "FATAL")]
                    if fatal_msgs:
                        raise AdapterError(f"GridLAB-D model error in '{p_abs}': " + "; ".join(fatal_msgs))
            else:
                raise AdapterError(f"GridLAB-D model file not found: '{model_path}'")
        return gld

    def get_property(self, object_name: str, property_name: str, default: Any = None) -> Any:
        try:
            res = self.session.get_property(object_name, property_name)
            if isinstance(res, tuple):
                status, val = res
                if status == 0 and val is not None:
                    return val
            elif res is not None:
                return res
        except Exception as e:
            _log.debug(f"GridLAB-D get_property({object_name}, {property_name}) failed: {e}")
        return default

    def get_float_property(self, object_name: str, property_name: str, default: Any = 0.0) -> Any:
        raw = self.get_property(object_name, property_name)
        if raw is None:
            return None if default is None else float(default)
        return _parse_gld_number(raw)

    def set_property(self, object_name: str, property_name: str, value: Any) -> bool:
        try:
            res = self.session.set_property(object_name, property_name, str(value))
            if isinstance(res, tuple):
                return res[0] == 0
            return True
        except Exception as e:
            _log.error(f"GridLAB-D set_property({object_name}, {property_name}, {value}) failed: {e}")
            return False

    def step(self, delta_seconds: int = 900) -> bool:
        """Advance GridLAB-D simulation step safely."""
        try:
            if hasattr(self.session, "set_time_step"):
                try:
                    self.session.set_time_step(delta_seconds)
                except Exception as e:
                    _log.debug(f"set_time_step({delta_seconds}) notice: {e}")
            if hasattr(self.session, "step"):
                res = self.session.step()
                if isinstance(res, tuple):
                    status, _ = res
                    if status != 0:
                        _log.warning(f"GridLAB-D step returned non-zero status code: {status}")
            return True
        except Exception as e:
            _log.error(f"GridLAB-D step({delta_seconds}) failed: {e}")
            return False
