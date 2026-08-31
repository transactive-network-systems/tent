#!/usr/bin/env python3
"""Run and step a GridLAB-D Feeder / Substation model across a 24-hour simulation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from der_interface import (
    DispatchCommand,
    Interval,
    SignConvention,
    capabilities_of,
)
from der_interface.adapters.gridlabd import GridlabdFeederAdapter

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"
UTC = timezone.utc


def main():
    parser = argparse.ArgumentParser(description="Simulate a GridLAB-D Feeder/Substation.")
    default_cfg = CONFIG_DIR / "gridlabd" / "feeder.json"
    if not default_cfg.exists():
        default_cfg = CONFIG_DIR / "feeder.json"
    parser.add_argument("--config", "-c", default=str(default_cfg), help="Config JSON path")
    parser.add_argument("--hours", type=float, default=24.0, help="Simulation hours (default 24)")
    parser.add_argument("--step-min", type=int, default=15, help="Interval duration in minutes (default 15)")
    parser.add_argument("--dispatch", "-d", type=float, default=None, help="Curtailment / target dispatch (kW)")
    parser.add_argument("--start-time", default="2026-08-05T00:00:00+00:00", help="Simulation ISO start timestamp")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    adapter = GridlabdFeederAdapter.from_config(**cfg)
    caps = capabilities_of(adapter)

    start_dt = datetime.fromisoformat(args.start_time)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)

    duration_s = args.step_min * 60
    n_steps = int((args.hours * 60) // args.step_min)
    intervals = Interval.sequence(start_dt, duration_s, n_steps)

    print("\n" + "=" * 92)
    print(f"  MODEL: {adapter.identity.resource_id} (FEEDER) | Source: {adapter.identity.source}")
    print("=" * 92)
    print(f"  Capabilities: flexibility={caps.flexibility}, state={caps.state}, dispatch={caps.dispatch}")
    print(f"  Transformer Capacity: [-{adapter._capacity_kw:.0f} kW, +{adapter._capacity_kw:.0f} kW]")
    print(f"\n  [{args.hours:.1f}-Hour Dynamic Physical Feeder Simulation ({n_steps} steps)]")
    print("  " + "-" * 88)
    print(f"  {'Interval Start':<18} | {'q_min (kW)':<12} | {'q_max (kW)':<12} | {'q_base (kW)':<12} | {'Power (kW)':<12} | {'Telemetry':<18}")
    print("  " + "-" * 88)

    for iv in intervals:
        env = adapter.get_envelope([iv])
        pt = env[0]

        if args.dispatch is not None:
            cmd = DispatchCommand.for_power(adapter.identity, iv, args.dispatch, sign=SignConvention.LOAD_POSITIVE)
            resp = adapter.dispatch(cmd)
            p_kw = resp.actual_power_kw
        else:
            p_kw = pt.q_baseline_kw

        time_str = iv.start.strftime("%m-%d %H:%M")
        telemetry = f"V={adapter.voltage_v:.0f}V, Q={p_kw*0.25:.1f}kVAR"
        print(f"  {time_str:<18} | {pt.q_min_kw:+10.1f}kW | {pt.q_max_kw:+10.1f}kW | {pt.q_baseline_kw:+10.1f}kW | {p_kw:+10.1f}kW | {telemetry}")

    print("  " + "-" * 88)
    print("\nFeeder simulation complete.\n")


if __name__ == "__main__":
    main()
