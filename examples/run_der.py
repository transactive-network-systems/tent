#!/usr/bin/env python3
"""Interactive Launcher and Tester for DER Interface Models.

Individual Standalone Scripts:
- GridLAB-D Water Heater : python examples/gridlabd_water_heater.py
- GridLAB-D HVAC House   : python examples/gridlabd_hvac.py
- GridLAB-D Battery      : python examples/gridlabd_battery.py
- GridLAB-D Feeder       : python examples/gridlabd_feeder.py
- pvlib Solar PV         : python examples/pvlib_solar.py
- Historical Replay CSV  : python examples/replay_csv.py

Master Runner Usage:
  python examples/run_der.py                          # Interactive selection menu
  python examples/run_der.py --model water_heater     # Run GridLAB-D water heater
  python examples/run_der.py --model hvac             # Run GridLAB-D HVAC house
  python examples/run_der.py --model battery          # Run GridLAB-D battery
  python examples/run_der.py --model feeder           # Run GridLAB-D feeder
  python examples/run_der.py --model pv               # Run pvlib solar
  python examples/run_der.py --model replay           # Run replay CSV
  python examples/run_der.py --model all              # Run all physical adapters
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from der_interface import (
    DispatchCommand,
    Interval,
    ResourceIdentity,
    SignConvention,
    capabilities_of,
)
from der_interface.adapters.gridlabd import (
    GridlabdBatteryInverterAdapter,
    GridlabdEvAdapter,
    GridlabdFeederAdapter,
    GridlabdHvacAdapter,
    GridlabdWaterHeaterAdapter,
)
from der_interface.adapters.ochre import (
    OchreBatteryAdapter,
    OchreDwellingAdapter,
    OchreEvAdapter,
    OchreHvacAdapter,
    OchreWaterHeaterAdapter,
)
from der_interface.adapters.pvlib import PvlibPvAdapter
from der_interface.adapters.replay import ReplayAdapter

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"
UTC = timezone.utc


def load_config(path_or_name: str | Path) -> dict[str, Any]:
    path = Path(path_or_name)
    if not path.exists():
        candidates = [
            CONFIG_DIR / path.name,
            CONFIG_DIR / "gridlabd" / path.name,
            CONFIG_DIR / "pvlib" / path.name,
            CONFIG_DIR / "replay" / path.name,
            CONFIG_DIR / "ochre" / path.name,
        ]
        for cand in candidates:
            if cand.exists():
                path = cand
                break
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path_or_name}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_adapter(model_type: str, config_override: dict[str, Any] | None = None) -> Any:
    model_type = model_type.lower().strip()

    if model_type in ("water_heater", "wh", "gridlabd_wh"):
        cfg = config_override or load_config(CONFIG_DIR / "gridlabd" / "water_heater.json")
        return GridlabdWaterHeaterAdapter.from_config(**cfg)

    elif model_type in ("hvac", "house", "gridlabd_hvac"):
        cfg = config_override or load_config(CONFIG_DIR / "gridlabd" / "hvac.json")
        return GridlabdHvacAdapter.from_config(**cfg)

    elif model_type in ("battery", "bess", "storage", "gridlabd_battery"):
        cfg = config_override or load_config(CONFIG_DIR / "gridlabd" / "battery.json")
        return GridlabdBatteryInverterAdapter.from_config(**cfg)

    elif model_type in ("ev", "evcharger", "gridlabd_ev"):
        cfg = config_override or load_config(CONFIG_DIR / "gridlabd" / "ev_charger.json")
        return GridlabdEvAdapter.from_config(**cfg)

    elif model_type in ("feeder", "substation", "gridlabd_feeder"):
        cfg = config_override or load_config(CONFIG_DIR / "gridlabd" / "feeder.json")
        return GridlabdFeederAdapter.from_config(**cfg)

    elif model_type in ("ochre_battery", "ochre_bess", "ochre_storage"):
        cfg = config_override or load_config(CONFIG_DIR / "ochre" / "battery.json")
        return OchreBatteryAdapter.from_config(**cfg)

    elif model_type in ("ochre_water_heater", "ochre_wh"):
        cfg = config_override or load_config(CONFIG_DIR / "ochre" / "water_heater.json")
        return OchreWaterHeaterAdapter.from_config(**cfg)

    elif model_type in ("ochre_hvac", "ochre_house"):
        cfg = config_override or load_config(CONFIG_DIR / "ochre" / "hvac.json")
        return OchreHvacAdapter.from_config(**cfg)

    elif model_type in ("ochre_ev", "ochre_evcharger"):
        cfg = config_override or load_config(CONFIG_DIR / "ochre" / "ev_charger.json")
        return OchreEvAdapter.from_config(**cfg)

    elif model_type in ("ochre_dwelling", "ochre_home", "dwelling"):
        cfg = config_override or load_config(CONFIG_DIR / "ochre" / "dwelling.json")
        return OchreDwellingAdapter.from_config(**cfg)

    elif model_type in ("pv", "solar", "pvlib"):
        cfg = config_override or load_config(CONFIG_DIR / "pvlib" / "solar_pv.json")
        return PvlibPvAdapter.from_config(**cfg)

    elif model_type in ("replay", "csv"):
        cfg = config_override or load_config(CONFIG_DIR / "replay" / "replay.json")
        csv_path = Path(cfg.get("path", "configs/replay/replay_data.csv"))
        if not csv_path.exists() and (CONFIG_DIR / "replay" / csv_path.name).exists():
            csv_path = CONFIG_DIR / "replay" / csv_path.name
        elif not csv_path.exists() and (CONFIG_DIR / csv_path.name).exists():
            csv_path = CONFIG_DIR / csv_path.name
        return ReplayAdapter.from_csv(csv_path, strict=False)

    else:
        raise ValueError(
            f"Unknown model type '{model_type}'. Choose from: water_heater, hvac, battery, ev, feeder, ochre_battery, ochre_wh, ochre_hvac, ochre_ev, ochre_dwelling, pv, replay, all"
        )


def run_model_simulation(
    adapter: Any,
    intervals: list[Interval],
    dispatch_power_kw: float | None = None,
    verbose: bool = True,
    show_plot: bool = True,
) -> dict[str, Any]:
    caps = capabilities_of(adapter)
    res_id = adapter.identity.resource_id
    res_kind = adapter.identity.kind.value
    res_src = adapter.identity.source

    if verbose:
        print("\n" + "=" * 88)
        print(f"  MODEL: {res_id} ({res_kind.upper()}) | Source: {res_src}")
        print("=" * 88)
        print(f"  Capabilities: flexibility={caps.flexibility}, state={caps.state}, dispatch={caps.dispatch}")

    # 1. Query Initial State & Limits
    state = adapter.get_state(intervals[0].start) if caps.state else None
    limits = adapter.get_limits(intervals[0].start) if caps.state else None

    if verbose and state and limits:
        print(f"  Physical Limits : [{limits.min_power_kw:+.2f} kW, {limits.max_power_kw:+.2f} kW] (load-positive)")
        state_vals = ", ".join(f"{k}={v:.2f}" if isinstance(v, (int, float)) else f"{k}={v}" for k, v in state.values.items())
        print(f"  Initial State   : {state_vals}")

    # 2. Step-by-Step Dynamic Physical Simulation
    envelope_points = []
    dispatch_results = []

    total_hours = (len(intervals) * intervals[0].duration_s) / 3600.0
    if verbose:
        print(f"\n  [{total_hours:.1f}-Hour Dynamic Physical Simulation ({len(intervals)} steps)]")
        print("  " + "-" * 84)
        print(f"  {'Interval Start':<18} | {'q_min':<8} | {'q_max':<8} | {'q_base':<8} | {'Power':<8} | {'Live State & Physics':<22}")
        print("  " + "-" * 84)

    for iv in intervals:
        # Step A: Query envelope for this exact instant
        env = adapter.get_envelope([iv])
        pt = env[0]
        envelope_points.append(pt)

        # Step B: Choose dispatch power (custom command or autonomous baseline)
        if dispatch_power_kw is not None:
            cmd_kw = dispatch_power_kw
        else:
            cmd_kw = pt.q_baseline_kw

        # Step C: Dispatch and advance physics
        if caps.dispatch:
            cmd = DispatchCommand.for_power(
                adapter.identity,
                iv,
                cmd_kw,
                sign=SignConvention.LOAD_POSITIVE,
                source="run_der",
            )
            resp = adapter.dispatch(cmd)
            dispatch_results.append(resp)
            actual_kw = resp.actual_power_kw
            state_dict = resp.actual
        else:
            actual_kw = pt.q_baseline_kw
            state_obj = adapter.get_state(iv.end) if caps.state else None
            state_dict = dict(state_obj.values) if state_obj else {}

        if verbose:
            time_str = iv.start.strftime("%m-%d %H:%M")
            summary_parts = []
            for k, v in state_dict.items():
                if k in ("power_kw", "object_name"):
                    continue
                if isinstance(v, bool):
                    summary_parts.append("connected" if v else "away")
                elif "soc" in k and isinstance(v, (int, float)):
                    summary_parts.append(f"SOC={v:.1%}" if v <= 1.0 else f"SOC={v:.1f}%")
                elif "temp" in k and isinstance(v, (int, float)):
                    summary_parts.append(f"{k}={v:.1f}°F")
                elif isinstance(v, float):
                    summary_parts.append(f"{k}={v:.2f}")
                else:
                    summary_parts.append(f"{k}={v}")
            state_summary = ", ".join(summary_parts) if summary_parts else "OK"

            print(f"  {time_str:<18} | {pt.q_min_kw:+7.2f}k | {pt.q_max_kw:+7.2f}k | {pt.q_baseline_kw:+7.2f}k | {actual_kw:+7.2f}k | {state_summary:<28}")

    if verbose:
        print("  " + "-" * 84)

    sim_data = {
        "adapter": adapter,
        "envelope": envelope_points,
        "dispatch_results": dispatch_results,
    }

    if verbose and show_plot:
        plot_simulation_results(sim_data)

    return sim_data


def plot_simulation_results(sim_data: dict[str, Any], width: int = 100, height: int = 13) -> None:
    """Render high-fidelity, comprehensive terminal plots of simulation results using plotext."""
    try:
        import plotext as plt
    except ImportError:
        return

    envelope = sim_data.get("envelope", [])
    dispatch_results = sim_data.get("dispatch_results", [])
    adapter = sim_data.get("adapter")
    if not envelope:
        return

    n = len(envelope)
    times = [pt.interval.start.strftime("%H:%M") for pt in envelope]
    p_max = [pt.q_max_kw for pt in envelope]
    p_min = [pt.q_min_kw for pt in envelope]
    p_base = [pt.q_baseline_kw for pt in envelope]

    if dispatch_results:
        p_act = [r.actual_power_kw for r in dispatch_results]
    else:
        p_act = p_base

    dur_h = envelope[0].interval.hours if n > 0 else 0.25
    total_kwh = sum(abs(p) * dur_h for p in p_act)
    peak_p = max(abs(p) for p in p_act) if p_act else 0.0

    x = list(range(n))
    tick_step = max(1, n // 8)
    tick_indices = list(range(0, n, tick_step))
    if (n - 1) not in tick_indices:
        tick_indices.append(n - 1)
    tick_labels = [times[i] for i in tick_indices]

    res_id = adapter.identity.resource_id if adapter else "DER"
    is_inflexible = all(abs(mn - mx) < 1e-4 for mn, mx in zip(p_min, p_max))

    # Header Legend & Metrics Banner
    if is_inflexible and all(p <= 0 for p in p_act):
        legend_str = "Yellow = Solar Generation Output (-kW load-positive) | Inflexible Must-Take"
    else:
        legend_str = "Cyan = P_max Headroom  |  Yellow = P_min Floor  |  Blue = P_expected Baseline  |  Green = Cleared Dispatch / Actual"

    print("\n  " + "=" * width)
    print(f"  HIGH-FIDELITY TELEMETRY: {res_id} (POWER & FLEXIBILITY)")
    print(f"  [ {legend_str} ]")
    print(f"  [ Peak Power: {peak_p:.2f} kW  |  Total Energy Throughput: {total_kwh:.2f} kWh  |  Samples: {n} ]")
    print("  " + "=" * width)

    # Plot 1: Power & Flexibility Envelope
    plt.clf()
    plt.theme("clear")
    plt.plotsize(width, height)
    plt.grid(horizontal=True, vertical=True)

    if is_inflexible:
        curve_color = "yellow" if any(p < 0 for p in p_act) else "green"
        plt.plot(x, p_act, color=curve_color, marker="braille")
    else:
        plt.plot(x, p_max, color="cyan", marker="braille", label="P_max")
        if any(abs(b - m) > 1e-3 for b, m in zip(p_base, p_max)):
            plt.plot(x, p_base, color="blue", marker="braille", label="P_expected")
        if any(abs(m) > 1e-3 or abs(m - mx) > 1e-3 for m, mx in zip(p_min, p_max)):
            plt.plot(x, p_min, color="yellow", marker="braille", label="P_min")
        plt.plot(x, p_act, color="green", marker="braille", label="Actual Dispatch")

    plt.xticks(tick_indices, tick_labels)
    plt.ylabel("kW")
    plt.show()

    # Plot 2: Secondary Physical State (Temperature, SOC, or Solar AC Generation)
    state_values: dict[str, list[float]] = {}
    if dispatch_results:
        for r in dispatch_results:
            for k, v in r.actual.items():
                if isinstance(v, (int, float)) and k not in ("power_kw", "object_name"):
                    state_values.setdefault(k, []).append(float(v))

    # If solar with negative power and no state dictionary, generate positive solar generation curve
    if not state_values and any(p < 0 for p in p_act):
        state_values["solar_ac_generation_kw"] = [max(0.0, -p) for p in p_act]

    for key, vals in state_values.items():
        if len(vals) == n and len(set(vals)) > 1:
            unit = "°F" if "temp" in key else ("%" if "soc" in key else ("kW" if "kw" in key else ""))
            display_vals = [v * 100 if ("soc" in key and max(vals) <= 1.0) else v for v in vals]
            color = "red" if "temp" in key else ("magenta" if "soc" in key else "yellow")
            label_name = key.replace("_f", "").replace("_", " ").title()

            min_val = min(display_vals)
            max_val = max(display_vals)
            avg_val = sum(display_vals) / len(display_vals)

            print("\n  " + "=" * width)
            print(f"  HIGH-FIDELITY PHYSICAL DYNAMICS: {res_id} - {label_name}")
            print(f"  [ Color: {color.capitalize()} ({unit or key})  |  Min: {min_val:.2f}{unit}  |  Max: {max_val:.2f}{unit}  |  Avg: {avg_val:.2f}{unit} ]")
            print("  " + "=" * width)

            plt.clf()
            plt.theme("clear")
            plt.plotsize(width, height - 2)
            plt.grid(horizontal=True, vertical=True)
            plt.plot(x, display_vals, color=color, marker="braille")
            plt.xticks(tick_indices, tick_labels)
            plt.ylabel(unit or key)
            plt.xlabel("Simulation Time")
            plt.show()
            break


def interactive_menu():
    print("\n" + "=" * 65)
    print("        DER-INTERFACE PHYSICAL ADAPTER TEST HARNESS")
    print("=" * 65)
    print("  [GridLAB-D Physical Circuit Models]:")
    print("    1. GridLAB-D Water Heater  (physical waterheater object)")
    print("    2. GridLAB-D HVAC House    (physical house thermal zone)")
    print("    3. GridLAB-D Battery       (inverter 4-quadrant storage)")
    print("    4. GridLAB-D EV Charger    (vehicle charging session & SOC)")
    print("    5. GridLAB-D Feeder        (substation / feeder aggregate head)")
    print("\n  [NREL OCHRE Residential Physical Models]:")
    print("    6. OCHRE Water Heater      (stratified multi-node tank)")
    print("    7. OCHRE HVAC House        (building thermal envelope & RC network)")
    print("    8. OCHRE Battery Storage   (cell degradation & 4-quadrant inverter)")
    print("    9. OCHRE EV Charger        (commute departure/arrival & Level 2)")
    print("   10. OCHRE Whole Dwelling    (aggregated residential net meter)")
    print("\n  [Renewable & Historical Replay]:")
    print("   11. Solar PV                (pvlib - solar position & irradiance)")
    print("   12. Replay / CSV            (deterministic historical playback)")
    print("   13. Run ALL Models")
    print("    0. Exit")
    print("-" * 65)

    choice = input("Select option [0-13]: ").strip()
    if choice == "1":
        model_name = "water_heater"
        p_cmd = float(input("Enter heating dispatch in kW [default 4.5]: ") or "4.5")
    elif choice == "2":
        model_name = "hvac"
        p_cmd = float(input("Enter HVAC cooling dispatch in kW [default 3.5]: ") or "3.5")
    elif choice == "3":
        model_name = "battery"
        p_cmd = float(input("Enter battery dispatch in kW (+5.0=Charge, -5.0=Discharge) [default +3.0]: ") or "3.0")
    elif choice == "4":
        model_name = "ev"
        p_cmd = float(input("Enter EV charge dispatch in kW [default 7.2]: ") or "7.2")
    elif choice == "5":
        model_name = "feeder"
        p_cmd = float(input("Enter feeder dispatch target in kW [default 1200.0]: ") or "1200.0")
    elif choice == "6":
        model_name = "ochre_wh"
        p_cmd = float(input("Enter OCHRE heating dispatch in kW [default 4.5]: ") or "4.5")
    elif choice == "7":
        model_name = "ochre_hvac"
        p_cmd = float(input("Enter OCHRE cooling dispatch in kW [default 3.5]: ") or "3.5")
    elif choice == "8":
        model_name = "ochre_battery"
        p_cmd = float(input("Enter OCHRE battery dispatch in kW (+5.0=Charge, -5.0=Discharge) [default +3.0]: ") or "3.0")
    elif choice == "9":
        model_name = "ochre_ev"
        p_cmd = float(input("Enter OCHRE EV dispatch in kW [default 7.2]: ") or "7.2")
    elif choice == "10":
        model_name = "ochre_dwelling"
        p_cmd = float(input("Enter dispatch power in kW [default 5.0]: ") or "5.0")
    elif choice == "11":
        model_name = "pv"
        p_cmd = None
    elif choice == "12":
        model_name = "replay"
        p_cmd = float(input("Enter dispatch power in kW [default 3.0]: ") or "3.0")
    elif choice == "13":
        model_name = "all"
        p_cmd = 3.0
    else:
        print("Exiting.")
        sys.exit(0)

    hours_str = input("Enter simulation hours (e.g. 2, 24) [default 2]: ").strip()
    hours = float(hours_str) if hours_str else 2.0

    return model_name, p_cmd, hours


def main():
    parser = argparse.ArgumentParser(description="Test and run any der-interface physical adapter.")
    parser.add_argument(
        "--model",
        "-m",
        choices=[
            "water_heater",
            "hvac",
            "battery",
            "ev",
            "feeder",
            "ochre_wh",
            "ochre_hvac",
            "ochre_battery",
            "ochre_ev",
            "ochre_dwelling",
            "pv",
            "replay",
            "all",
        ],
        help="Model to run",
    )
    parser.add_argument("--config", "-c", help="Path to JSON config file")
    parser.add_argument("--hours", type=float, default=2.0, help="Simulation duration in hours (default 2)")
    parser.add_argument("--intervals", "-n", type=int, help="Number of intervals (overrides --hours)")
    parser.add_argument("--step-min", type=int, default=15, help="Interval duration in minutes (default 15)")
    parser.add_argument("--dispatch", "-d", type=float, help="Simulate a dispatch command (kW)")
    parser.add_argument("--no-plot", action="store_true", help="Disable terminal plotext graph rendering")
    parser.add_argument(
        "--start-time",
        default="2026-08-05T14:00:00+00:00",
        help="Simulation ISO start timestamp (default 2026-08-05T14:00:00+00:00)",
    )

    args = parser.parse_args()

    model = args.model
    dispatch_kw = args.dispatch

    sim_hours = float(args.hours)
    if not model:
        model, interactive_dispatch, interactive_hours = interactive_menu()
        if dispatch_kw is None:
            dispatch_kw = interactive_dispatch
        sim_hours = interactive_hours

    start_dt = datetime.fromisoformat(args.start_time)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)

    models_to_run = [
        "water_heater",
        "hvac",
        "battery",
        "ev",
        "feeder",
        "ochre_wh",
        "ochre_hvac",
        "ochre_battery",
        "ochre_ev",
        "pv",
        "replay",
    ] if model == "all" else [model]

    for m in models_to_run:
        config_dict = load_config(args.config) if (args.config and model != "all") else None
        
        # Allow config files to define their own interval step duration if not explicitly passed
        step_min = args.step_min
        if config_dict and "step_min" in config_dict:
            step_min = int(config_dict["step_min"])
        elif config_dict and "interval_duration_s" in config_dict:
            step_min = int(config_dict["interval_duration_s"]) // 60

        dur_s = step_min * 60
        n_intervals = args.intervals if args.intervals is not None else int((sim_hours * 60) // step_min)
        run_intervals = Interval.sequence(start_dt, dur_s, n_intervals)

        adapter = build_adapter(m, config_override=config_dict)
        run_model_simulation(adapter, run_intervals, dispatch_power_kw=dispatch_kw, show_plot=not args.no_plot)

    print("\nSimulation complete. All physical models functional.\n")


if __name__ == "__main__":
    main()
