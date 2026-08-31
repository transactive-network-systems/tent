#!/usr/bin/env python3
"""Run and step a pvlib Solar PV model across a 24-hour simulation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from der_interface import (
    Interval,
    capabilities_of,
)
from der_interface.adapters.pvlib import PvlibPvAdapter

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"
UTC = timezone.utc


def main():
    parser = argparse.ArgumentParser(description="Simulate a pvlib Solar PV System.")
    default_cfg = CONFIG_DIR / "pvlib" / "solar_pv.json"
    if not default_cfg.exists():
        default_cfg = CONFIG_DIR / "solar_pv.json"
    parser.add_argument("--config", "-c", default=str(default_cfg), help="Config JSON path")
    parser.add_argument("--hours", type=float, default=24.0, help="Simulation hours (default 24)")
    parser.add_argument("--step-min", type=int, default=15, help="Interval duration in minutes (default 15)")
    parser.add_argument("--start-time", default="2026-08-05T00:00:00+00:00", help="Simulation ISO start timestamp")
    parser.add_argument("--no-plot", action="store_true", help="Disable terminal graph rendering")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    adapter = PvlibPvAdapter.from_config(**cfg)
    caps = capabilities_of(adapter)

    start_dt = datetime.fromisoformat(args.start_time)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)

    duration_s = args.step_min * 60
    n_steps = int((args.hours * 60) // args.step_min)
    intervals = Interval.sequence(start_dt, duration_s, n_steps)

    limits = adapter.get_limits(intervals[0].start)
    print("\n" + "=" * 92)
    print(f"  MODEL: {adapter.identity.resource_id} (SOLAR PV) | Source: {adapter.identity.source}")
    print("=" * 92)
    print(f"  Capabilities: flexibility={caps.flexibility}, state={caps.state}, dispatch={caps.dispatch}")
    print(f"  Physical Limits: [{limits.min_power_kw:.2f} kW, {limits.max_power_kw:.2f} kW] (load-positive)")
    print(f"\n  [{args.hours:.1f}-Hour Dynamic pvlib Solar Simulation ({n_steps} steps)]")
    print("  " + "-" * 88)
    print(f"  {'Interval Start':<18} | {'q_min (kW)':<12} | {'q_max (kW)':<12} | {'q_base (kW)':<12} | {'Power (kW)':<12} | {'Live Physics':<18}")
    print("  " + "-" * 88)

    envelope_points = []

    for iv in intervals:
        env = adapter.get_envelope([iv])
        pt = env[0]
        envelope_points.append(pt)

        time_str = iv.start.strftime("%m-%d %H:%M")
        gen_str = f"Generation={-pt.q_min_kw:.2f}kW"
        print(f"  {time_str:<18} | {pt.q_min_kw:+10.2f}kW | {pt.q_max_kw:+10.2f}kW | {pt.q_baseline_kw:+10.2f}kW | {pt.q_baseline_kw:+10.2f}kW | {gen_str}")

    print("  " + "-" * 88)

    if not args.no_plot:
        from run_der import plot_simulation_results
        plot_simulation_results({
            "adapter": adapter,
            "envelope": envelope_points,
            "dispatch_results": [],
        })

    print("\nSolar PV simulation complete.\n")


if __name__ == "__main__":
    main()
