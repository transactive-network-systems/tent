"""
GridLAB-D feasibility timing script.

Measures how long each phase of a TENT + GridLAB-D market cycle takes,
so you can judge whether GLD is fast enough to use as a real-time control backend.

Phases timed:
  1. GLD startup   — load + setup_after_load
  2. TENT setup    — configure_node (market, asset, neighbor)
  3. GLD step      — one simulation timestep
  4. TENT market   — full market cycle (Active → Expired), including asset scheduling
  5. Full loop     — N_STEPS combined iterations of GLD step + TENT market cycle

Run from the TENT root:
  .venv/bin/python scripts/time_gridlabd.py [--steps N]

Requires the gridlabd package to be installed:
  cd gridlab-d/python_bindings && pip install -e .
"""

import os
import sys
import time
import argparse
import statistics
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

# ── Make sure tent is importable when run from TENT root ────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tent.enumerations.market_state import MarketState
from tent.utils.timer import Timer
from tests.mock_tn import MockTN

# ---------------------------------------------------------------------------
# RC thermostat coefficients (from tent/local_asset/model_frame/config.json)
# ---------------------------------------------------------------------------
_C1 = [
    0.0033120383229135715, 0.0, 0.0, 0.0,
    0.0085083927804455329, -0.0034916222710922652, -0.061795391927456936,
    -0.11549651734517435, -0.15529363415504729, -0.1808815744686369,
    -0.22721699980115925, -0.21450065961452566, -0.25000062667914968,
    -0.25837283176225989, -0.26079571385406741, -0.23642950842938459,
    -0.28015976345870219, -0.14624174659267433, -0.14843818333354364,
    -0.1584339849631726, -0.094063041692555666, 0.00061798719043989223,
    -0.0054037322655405484, 0.0036413017240370417,
]
_C2 = [
    0.0014056546275679498, 0.0, 0.0, 0.0,
    0.02788403141867463, 0.14513147547176014, 0.11118445969429161,
    0.18804955414014699, 0.23961615104358308, 0.22063011894390921,
    0.25891026251095917, 0.18290029269525862, 0.20389087595453123,
    0.19026429938860173, 0.17032280463427033, 0.12795337017241595,
    0.19795990044282899, 0.015545247214509814, 0.040125477723492307,
    0.070317282356116895, 0.0013916069435426469, -0.0010610095643050501,
    0.0011759834535182432, 0.0011778477344168998,
]
_C3 = [
    0.0001404314886164651, 0.0, 0.0, 0.0,
    0.0016405925453123402, 0.0024864791905304662, 0.02617854404770277,
    0.020623611306076012, 0.019164829502084868, 0.018162293300869593,
    0.015753475303225115, 0.013800477167573398, 0.012669029751344006,
    0.018852330078445334, 0.019871955909389345, 0.021104137282738696,
    0.020353583079027751, 0.024898053423863028, 0.017827962204744335,
    0.01672111812984273, 0.015700729602455603, 0.00089221621574899347,
    0.00019196002068200382, 0.0002078633455269473,
]
_C4 = [
    -0.12271722331728817, 0.0, 0.0, 0.0,
    -0.88611700516013303, -3.2878699157468843, -1.4068057997856389,
    -1.8055464268715324, -2.0896212750798795, -1.0673043684992383,
    -0.84773688255885005, 0.65613889763045841, 1.0327098244832666,
    1.3428993229271895, 1.8194670270004978, 2.2223727779241078,
    1.5511900429244405, 2.6285742227628783, 2.3163296564583842,
    1.8704803961931331, 2.0218927534747868, -0.0051486840773421697,
    0.10930094056501329, -0.12739756957779722,
]

_MIN_CSP_F     = 68.0
_MAX_CSP_F     = 80.0
_SUPPLY_PRICE_LOW  = 0.02
_SUPPLY_PRICE_HIGH = 0.10


# ---------------------------------------------------------------------------
# Timing helper
# ---------------------------------------------------------------------------

class Stopwatch:
    def __init__(self, label: str):
        self.label = label
        self.elapsed: float | None = None
        self._start: float | None = None

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.elapsed = time.perf_counter() - self._start

    def __str__(self):
        if self.elapsed is None:
            return f"{self.label}: not run"
        ms = self.elapsed * 1000
        if ms >= 1000:
            return f"{self.label}: {self.elapsed:.3f} s"
        return f"{self.label}: {ms:.1f} ms"


# ---------------------------------------------------------------------------
# Helpers (mirrors test file)
# ---------------------------------------------------------------------------

def _parse_gld_float(value_str: str) -> float:
    return float(str(value_str).split()[0].lstrip('+'))


def _cleared_price_to_setpoint(price: float) -> float:
    ratio = max(0.0, min(1.0, (price - _SUPPLY_PRICE_LOW) /
                              max(_SUPPLY_PRICE_HIGH - _SUPPLY_PRICE_LOW, 1e-9)))
    return _MIN_CSP_F + ratio * (_MAX_CSP_F - _MIN_CSP_F)


def _build_node_config() -> dict:
    return {
        "tz": "UTC",
        "data_manager": {
            "class_name": "NullDataManager",
            "module_name": "tent.data_manager.null_data_manager"
        },
        "information_service_models": [
            {
                "class_name": "TemperatureForecastModel",
                "module_name": "tent.information_service_model.temperature_forecast_model",
                "name": "TemperatureForecast",
                "weather_file_path": None,
                "temperature_point_name": "OutdoorAirTemperature",
            }
        ],
        "local_assets": [
            {
                "class_name": "ModelFrameAsset",
                "module_name": "tent.local_asset.model_frame.model_frame_asset",
                "name": "HvacHouse",
                "minimum_power": -14.0,
                "maximum_power": 0.0,
                "default_power": -7.0,
                "information_services": ["TemperatureForecast"],
                "temperature_forecast_name": "TemperatureForecast",
                "model_configs": {
                    "demand_curve_points": 2,
                    "models": [
                        {
                            "topic": "hvac",
                            "model_type": "thermostat.Thermostat",
                            "model_config": {
                                "rated_power": 13.68,
                                "c1": _C1, "c2": _C2, "c3": _C3, "c4": _C4,
                                "nominal_setpoint": 22.8,
                                "max_set_point_offset": 2.0,
                                "actuation_topic": "hvac/cooling_sp",
                            },
                        }
                    ],
                },
                "occupancy_manager": {
                    "class_name": "OccupancyManager",
                    "module_name": "tent.local_asset.occupancy_manager.simple",
                    "schedule": {},
                },
                "actuation_manager": {
                    "class_name": "DirectRatioActuationManager",
                    "module_name": "tent.local_asset.actuation_manager.direct_ratio",
                    "active_onstart": True,
                    "control_interval": 60,
                },
            }
        ],
        "neighbors": [
            {
                "class_name": "Neighbor",
                "module_name": "tent.neighbor",
                "name": "GridSupply",
                "up_or_down": "upstream",
                "transactive": False,
                "maximum_power": 1000.0,
                "minimum_power": 0.0,
                "cost_parameters": [0.0, 0.0, 0.0],
                "default_vertices": [
                    [_SUPPLY_PRICE_LOW,  0.0, 0.0,    True],
                    [_SUPPLY_PRICE_HIGH, 0.0, 1000.0, True],
                ],
            }
        ],
        "market_series": [
            {
                "class_name": "MarketSeries",
                "module_name": "tent.market.market_series",
                "market_class_name": "Market",
                "market_module_name": "tent.market",
                "name": "RT",
                "default_price": 0.05,
                "interval_duration": 3600,
                "intervals_to_clear": 1,
                "market_clearing_interval": 3600,
                "duration_market_lead": 60,
                "method": "Interpolation",
                "price_model": None,
            }
        ],
    }


def _populate_forecast(tn, market, oat_f: float):
    ism = next(i for i in tn.information_service_models if i.name == "TemperatureForecast")
    for ti in market.time_intervals:
        ism.predicted_values.update(ti, oat_f)


def _drive_market_cycle(tn, market, oat_f: float = 65.0) -> dict:
    tt = market.transition_times
    _prepared_ids: set = {id(market)}

    def _prepare(mkt):
        _populate_forecast(tn, mkt, oat_f)
        mkt.check_marginal_prices()
        for a in tn.local_assets:
            a.schedule(mkt)
        for n in tn.neighbors:
            n.schedule(mkt)

    _prepare(market)

    def step_at(t):
        with patch.object(Timer, 'now', return_value=t):
            tn.step()
        for series in tn.market_series:
            for mkt in series:
                if id(mkt) not in _prepared_ids:
                    _prepared_ids.add(id(mkt))
                    _prepare(mkt)

    step_at(tt.active)
    step_at(tt.active + timedelta(seconds=1))
    step_at(tt.negotiation)
    step_at(tt.negotiation + timedelta(seconds=1))
    step_at(tt.market_lead)
    step_at(tt.market_lead + timedelta(seconds=1))
    step_at(tt.delivery_lead)
    step_at(tt.delivery_lead + timedelta(seconds=1))

    cleared: dict = {}
    for ti in market.time_intervals:
        mp = market.marginal_prices.get_by_interval(ti)
        if mp is not None:
            cleared[ti] = mp.value

    step_at(tt.delivery)
    step_at(tt.delivery + timedelta(seconds=1))
    step_at(tt.reconciliation)
    step_at(tt.reconciliation + timedelta(seconds=1))

    return cleared


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Time GridLAB-D + TENT market cycle phases")
    parser.add_argument("--steps", type=int, default=10,
                        help="Number of GLD step + market cycle iterations to time (default: 10)")
    args = parser.parse_args()

    try:
        import gridlabd
    except ImportError:
        print("ERROR: gridlabd package not installed.")
        print("  cd gridlab-d/python_bindings && pip install -e .")
        sys.exit(1)

    tests_dir = os.path.realpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "gridlab-d", "python_bindings", "tests")
    )
    glm_path = os.path.join(tests_dir, "test_HVAC_balance.glm")
    if not os.path.exists(glm_path):
        print(f"ERROR: GLM file not found: {glm_path}")
        sys.exit(1)

    print("=" * 60)
    print("GridLAB-D + TENT feasibility timing")
    print(f"Steps: {args.steps}")
    print("=" * 60)

    # ── Phase 1: GLD startup ─────────────────────────────────────────────────
    with Stopwatch("GLD startup (load + setup)") as sw_gld_start:
        gld = gridlabd.GridLabD()
        gld.set_working_directory(tests_dir)
        gld.load(glm_path)
        gld.setup_after_load()
        houses = gld.get_objects_by_class("house")
        house = houses[0]
    print(sw_gld_start)

    # ── Phase 2: TENT setup ──────────────────────────────────────────────────
    with Stopwatch("TENT setup (configure_node)") as sw_tent_start:
        Timer.simulation = False
        tn = MockTN()
        config = _build_node_config()
        with patch("tent.data_manager.local_data_manager.LocalDataManager.init_archive",
                   Mock(return_value=None)):
            tn.configure_node(config)
        if hasattr(tn, "data_manager") and tn.data_manager:
            tn.data_manager.archive_data = Mock(return_value=None)
        markets = []
        for series in tn.market_series:
            markets.extend(list(series))
        market = markets[0]
        asset = tn.local_assets[0]
    print(sw_tent_start)

    # ── Phase 3: single GLD step ─────────────────────────────────────────────
    with Stopwatch("Single GLD step") as sw_gld_step:
        gld.step()
    print(sw_gld_step)

    # ── Phase 4: single TENT market cycle (no GLD) ───────────────────────────
    oat_f = _parse_gld_float(gld.get_property(house, "outdoor_temperature")[1])
    tin_f = _parse_gld_float(gld.get_property(house, "air_temperature")[1])
    csp_f = _parse_gld_float(gld.get_property(house, "cooling_setpoint")[1])
    asset.new_model_data({
        "datetime": datetime(2001, 5, 5, 10, 0, tzinfo=timezone.utc),
        "data": {"hvac": {
            "OutdoorAirTemperature": oat_f,
            "ZoneTemperature": tin_f,
            "ZoneCoolingTemperatureSetPoint": csp_f,
        }},
    })
    with Stopwatch("Single TENT market cycle") as sw_tent_cycle:
        _drive_market_cycle(tn, market, oat_f=oat_f)
    print(sw_tent_cycle)

    # ── Phase 5: N combined iterations ──────────────────────────────────────
    print()
    print(f"Running {args.steps} combined GLD step + TENT market cycles...")
    iteration_times = []
    gld_step_times  = []
    tent_cycle_times = []

    for i in range(args.steps):
        t_iter_start = time.perf_counter()

        with Stopwatch("") as sw_s:
            code, sim_time = gld.step()
            oat_f = _parse_gld_float(gld.get_property(house, "outdoor_temperature")[1])
            tin_f = _parse_gld_float(gld.get_property(house, "air_temperature")[1])
            csp_f = _parse_gld_float(gld.get_property(house, "cooling_setpoint")[1])
        gld_step_times.append(sw_s.elapsed)

        asset.new_model_data({
            "datetime": datetime(2001, 5, 5, (10 + i) % 24, 0, tzinfo=timezone.utc),
            "data": {"hvac": {
                "OutdoorAirTemperature": oat_f,
                "ZoneTemperature": tin_f,
                "ZoneCoolingTemperatureSetPoint": csp_f,
            }},
        })

        current_markets = []
        for series in tn.market_series:
            current_markets.extend(list(series))
        active_market = next(
            (m for m in current_markets if m.market_state != MarketState.Expired),
            market,
        )

        with Stopwatch("") as sw_t:
            cleared = _drive_market_cycle(tn, active_market, oat_f=oat_f)
        tent_cycle_times.append(sw_t.elapsed)

        cleared_price = next(iter(cleared.values()), None)
        new_csp = _cleared_price_to_setpoint(cleared_price) if cleared_price else _MIN_CSP_F
        gld.set_property(house, "cooling_setpoint", str(new_csp))

        iteration_times.append(time.perf_counter() - t_iter_start)
        print(f"  step {i+1:>3}/{args.steps}  "
              f"gld={gld_step_times[-1]*1000:6.1f}ms  "
              f"tent={tent_cycle_times[-1]*1000:6.1f}ms  "
              f"total={iteration_times[-1]*1000:6.1f}ms  "
              f"price=${cleared_price:.5f}  csp={new_csp:.1f}°F")

    # ── Summary ──────────────────────────────────────────────────────────────
    def stats(label, times_s):
        ms = [t * 1000 for t in times_s]
        print(f"  {label:<28}  "
              f"min={min(ms):6.1f}ms  "
              f"avg={statistics.mean(ms):6.1f}ms  "
              f"max={max(ms):6.1f}ms  "
              f"p95={sorted(ms)[int(len(ms)*0.95)]:6.1f}ms")

    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(sw_gld_start)
    print(sw_tent_start)
    print(sw_gld_step)
    print(sw_tent_cycle)
    print()
    stats("GLD step + property reads", gld_step_times)
    stats("TENT market cycle", tent_cycle_times)
    stats("Full iteration (GLD + TENT)", iteration_times)
    print()

    avg_iter_ms = statistics.mean(iteration_times) * 1000
    print("Feasibility verdict:")
    if avg_iter_ms < 100:
        print(f"  FAST   ({avg_iter_ms:.1f}ms avg) — well within a 1-minute market interval")
    elif avg_iter_ms < 1000:
        print(f"  OK     ({avg_iter_ms:.1f}ms avg) — fine for minute-level market intervals")
    elif avg_iter_ms < 10000:
        print(f"  SLOW   ({avg_iter_ms:.1f}ms avg) — marginal for real-time use; consider async")
    else:
        print(f"  VERY SLOW ({avg_iter_ms:.1f}ms avg) — likely not feasible for real-time control")


if __name__ == "__main__":
    main()
