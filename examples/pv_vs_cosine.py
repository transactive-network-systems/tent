"""Side-by-side: TENT's cosine PV model vs. pvlib, through the same interface.

Run:
    python examples/pv_vs_cosine.py

Reproduces the 120 kW campus PV site from TENT's ``sample_configs/tn.campus.json``
two ways, and prints the difference.
"""

from __future__ import annotations

import math

import pandas as pd
from pvlib.location import Location
from pvlib.pvsystem import PVSystem
from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS

from tent.flexibility import Interval, SignConvention
from tent.flexibility.adapters.pvlib import PvlibPvAdapter

# The campus site as TENT configures it today.
RATED_KW = 120.0
LAT, LON, TZ = 46.28, -119.28, "US/Pacific"
CLOUD_FACTOR = 1.0


def cosine_kw(hour_of_day: float) -> float:
    """TENT's current model, verbatim from solar_pv_resource_model.py:85."""
    if hour_of_day < 5.5 or hour_of_day > 17.5:
        return 0.0
    clear_sky = 0.5 * (1 + math.cos((hour_of_day - 12) * 2.0 * math.pi / 12))
    return RATED_KW * clear_sky * CLOUD_FACTOR


def build_system() -> PVSystem:
    return PVSystem(
        surface_tilt=30,
        surface_azimuth=180,
        module_parameters={"pdc0": RATED_KW * 1000, "gamma_pdc": -0.004},
        inverter_parameters={"pdc0": RATED_KW * 1000},
        temperature_model_parameters=(
            TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]
        ),
    )


def build_adapter(curtailable: bool = False) -> PvlibPvAdapter:
    return PvlibPvAdapter.from_location(
        resource_id="campus_pv",
        location=Location(LAT, LON, tz=TZ),
        system=build_system(),
        rated_kw=RATED_KW,
        curtailable=curtailable,
    )


def compare(adapter: PvlibPvAdapter, date: str) -> None:
    start = pd.Timestamp(f"{date} 00:00", tz=TZ).to_pydatetime()
    intervals = Interval.sequence(start, 3600, 24)
    envelope = adapter.get_envelope(intervals)

    print(f"\n{date}  ({RATED_KW:.0f} kW site at {LAT}, {LON})")
    print(f"{'hour':>5} {'cosine':>9} {'pvlib':>9} {'diff':>9}")
    print("-" * 35)

    cos_total = 0.0
    pv_total = 0.0
    for hour, point in enumerate(envelope):
        cos_kw = cosine_kw(hour + 0.5)
        # Envelope is load-positive, so generation is negative. Flip for display.
        pv_kw = -point.q_baseline_kw
        cos_total += cos_kw
        pv_total += pv_kw
        if cos_kw > 0.1 or pv_kw > 0.1:
            print(f"{hour:>5} {cos_kw:>9.1f} {pv_kw:>9.1f} {pv_kw - cos_kw:>+9.1f}")

    print("-" * 35)
    print(f"{'kWh':>5} {cos_total:>9.1f} {pv_total:>9.1f} {pv_total - cos_total:>+9.1f}")
    if pv_total > 1e-9:
        ratio = cos_total / pv_total
        verb = "overstates" if ratio > 1.0 else "understates"
        print(f"cosine model {verb} daily energy by {max(ratio, 1 / ratio):.2f}x")


def main() -> None:
    adapter = build_adapter()

    print("=" * 35)
    print("TENT cosine model vs. pvlib")
    print("=" * 35)

    compare(adapter, "2026-07-15")  # summer
    compare(adapter, "2026-12-15")  # winter

    print("\nWhat the cosine model cannot represent:")
    for note in (
        "seasonal day length (sunrise fixed at 05:30 year-round)",
        "latitude and solar declination",
        "panel tilt and azimuth",
        "module temperature derating",
        "inverter clipping",
        "actual weather",
    ):
        print(f"  - {note}")

    # The same adapter also serves forecasts to any other consumer:
    # OCHRE PV equipment, GridLAB-D solar objects, a TESP BTM-solar stream.
    intervals = Interval.sequence(
        pd.Timestamp("2026-07-15 12:00", tz=TZ).to_pydatetime(), 900, 4
    )
    forecast = adapter.get_forecast("ac_output_kw", intervals)
    print(f"\nSame adapter as a forecast provider: {adapter.available_forecasts()}")
    print(f"  midday 15-min AC output (kW): {[round(v, 1) for v in forecast.values()]}")

    # And curtailment is one flag, not a new class.
    curtailable = build_adapter(curtailable=True)
    point = curtailable.get_envelope(intervals)[0]
    print("\nWith curtailable=True the same array becomes flexible:")
    print(f"  q_min={point.q_min_kw:.1f} kW (full output)  "
          f"q_max={point.q_max_kw:.1f} kW (curtailed)")

    tent_view = curtailable.get_envelope(intervals).in_convention(
        SignConvention.GENERATION_POSITIVE
    )
    print(f"\nHanded to TENT (generation-positive): "
          f"q_min={tent_view[0].q_min_kw:.1f} q_max={tent_view[0].q_max_kw:.1f}")


if __name__ == "__main__":
    main()
