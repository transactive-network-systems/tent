#!/usr/bin/env python3
"""Run and step an NREL OCHRE Battery model across a 24-hour simulation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from tent.flexibility import (
    DispatchCommand,
    Interval,
    SignConvention,
    capabilities_of,
)
from tent.flexibility.adapters.ochre import OchreBatteryAdapter

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"
UTC = timezone.utc


def main():
    parser = argparse.ArgumentParser(description="Simulate an NREL OCHRE Battery.")
    default_cfg = CONFIG_DIR / "ochre" / "battery.json"
    if not default_cfg.exists():
        default_cfg = CONFIG_DIR / "battery.json"
    parser.add_argument("--config", "-c", default=str(default_cfg), help="Config JSON path")
    parser.add_argument("--hours", type=float, default=24.0, help="Simulation hours (default 24)")
    parser.add_argument("--step-min", type=int, default=15, help="Interval duration in minutes (default 15)")
    parser.add_argument("--dispatch", "-d", type=float, default=None, help="Fixed dispatch active power in kW (-5.0 to +5.0)")
    parser.add_argument("--start-time", default="2026-08-05T00:00:00+00:00", help="Simulation ISO start timestamp")
    parser.add_argument("--no-plot", action="store_true", help="Disable terminal graph rendering")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    adapter = OchreBatteryAdapter.from_config(**cfg)
    caps = capabilities_of(adapter)

    start_dt = datetime.fromisoformat(args.start_time)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)

    duration_s = args.step_min * 60
    n_steps = int((args.hours * 60) // args.step_min)
    intervals = Interval.sequence(start_dt, duration_s, n_steps)

    print("\n" + "=" * 92)
    print(f"  MODEL: {adapter.identity.resource_id} (OCHRE STORAGE) | Source: {adapter.identity.source}")
    print("=" * 92)
    print(f"  Capabilities: flexibility={caps.flexibility}, state={caps.state}, dispatch={caps.dispatch}")
    print(f"  Power Rating: [-{adapter._rated_kw:.1f} kW, +{adapter._rated_kw:.1f} kW] | Capacity: {adapter._capacity_kwh:.1f} kWh")
    print(f"\n  [{args.hours:.1f}-Hour Dynamic Physical OCHRE Battery Simulation ({n_steps} steps)]")
    print("  " + "-" * 88)
    print(f"  {'Interval Start':<18} | {'q_min (kW)':<12} | {'q_max (kW)':<12} | {'q_base (kW)':<12} | {'Power (kW)':<12} | {'Live Physics':<18}")
    print("  " + "-" * 88)

    envelope_points = []
    dispatch_results = []

    for iv in intervals:
        env = adapter.get_envelope([iv])
        pt = env[0]
        envelope_points.append(pt)

        cmd_kw = args.dispatch if args.dispatch is not None else pt.q_baseline_kw
        cmd = DispatchCommand.for_power(adapter.identity, iv, cmd_kw, sign=SignConvention.LOAD_POSITIVE)
        resp = adapter.dispatch(cmd)
        dispatch_results.append(resp)

        time_str = iv.start.strftime("%m-%d %H:%M")
        state_str = f"SOC={adapter.soc:.1%}"
        print(f"  {time_str:<18} | {pt.q_min_kw:+10.2f}kW | {pt.q_max_kw:+10.2f}kW | {pt.q_baseline_kw:+10.2f}kW | {resp.actual_power_kw:+10.2f}kW | {state_str}")

    print("  " + "-" * 88)

    if not args.no_plot:
        from run_der import plot_simulation_results
        plot_simulation_results({
            "adapter": adapter,
            "envelope": envelope_points,
            "dispatch_results": dispatch_results,
        })

    print("\nOCHRE Battery simulation complete.\n")


if __name__ == "__main__":
    main()
