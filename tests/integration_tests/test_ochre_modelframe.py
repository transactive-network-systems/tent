"""
Closed-loop integration test: OCHRE building simulation ↔ TENT ModelFrameAsset market.

This test mirrors test_gridlabd_modelframe.py exactly, replacing GridLAB-D with the
OCHRE Python library (https://github.com/NatLabRockies/OCHRE).  The TENT side
(MockTN, ModelFrameAsset, Thermostat, market, helpers) is identical; only the
physics fixture and the read/write glue change.

OCHRE differences vs GridLAB-D:
  - Pure Python: no C++ bindings, no string parsing.
  - All temperatures are in Celsius.  The Thermostat model is unit-agnostic (the
    coefficients just map numbers to numbers), so we pass °C through unchanged.
  - The control signal persists: writing a setpoint is reflected on the very next
    dwelling.update() call (unlike GLD's house thermostat, which overrode the value).
    This lets us make a stronger closed-loop assertion.

Environment:
  This test requires a dedicated venv with OCHRE 0.9.2 + pint==0.24.4:
    /usr/local/bin/python3.13 -m venv /path/to/tent/OCHRE/.venv
    .venv/bin/pip install --ignore-requires-python -e /path/to/tent/OCHRE
    .venv/bin/pip install "pint==0.24.4"   # fixes 3.13 frozen-dataclass crash
    .venv/bin/pip install -e /path/to/tent/TENT pytest
  Run:
    cd /path/to/tent/TENT
    /path/to/tent/OCHRE/.venv/bin/python -m pytest \
        tests/integration_tests/test_ochre_modelframe.py -v -s
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from tent.containers import Vertex
from tent.enumerations.market_state import MarketState
from tent.utils.timer import Timer

from tests.mock_tn import MockTN


from tent.information_service_model.temperature_forecast_model import TemperatureForecastModel  # noqa: E402


class ReadingsFedTemperatureForecast(TemperatureForecastModel):
    """A forecast whose predicted_values the test fills from simulator readings: there is no file and nothing to update."""
    def update_information(self, market):
        return None


# ---------------------------------------------------------------------------
# Thermostat RC coefficients from tent/local_asset/model_frame/config.json
# (rated_power 13.68 kW, 24-element c1..c4 arrays)
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

# Cooling setpoint bounds the market is allowed to request [°C]
# (equivalent range to the GLD test's 68–80 °F)
_MIN_CSP_C = 20.0   # floor: cool aggressively when price is cheap
_MAX_CSP_C = 27.0   # ceiling: relax cooling when price is expensive

# A "cheap" supply price band to bracket the demand curve for clearing
_SUPPLY_PRICE_LOW  = 0.02   # $/kWh
_SUPPLY_PRICE_HIGH = 0.10   # $/kWh


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _feed(asset, msg):
    """Deliver a {"datetime": ..., "data": {subscribe_key: payload}} message through the subscription callback form."""
    for key, payload in msg["data"].items():
        asset.new_model_data(msg["datetime"], key, payload)


def _cleared_price_to_setpoint(price: float,
                                low_price: float = _SUPPLY_PRICE_LOW,
                                high_price: float = _SUPPLY_PRICE_HIGH) -> float:
    """
    Map cleared marginal price to a cooling setpoint (°C).

    Higher price  → relax cooling (higher setpoint, less HVAC load).
    Lower price   → cool aggressively (lower setpoint, more HVAC load).
    """
    ratio = max(0.0, min(1.0, (price - low_price) / max(high_price - low_price, 1e-9)))
    return _MIN_CSP_C + ratio * (_MAX_CSP_C - _MIN_CSP_C)


def _build_node_config() -> dict:
    """
    Return a TENT node config dict for:
      - one ModelFrameAsset with a Thermostat model
      - one non-transactive supply Neighbor (makes base Market clearing possible)
      - one Market (base Market class, single interval, Interpolation)
      - one TemperatureForecastModel ISM (no CSV file; we populate values directly)

    This config is identical to the GridLAB-D test.  The Thermostat's
    nominal_setpoint and max_set_point_offset are already in the Thermostat's own
    unit system (°C-scale numerically), so no unit conversion is needed here.
    """
    return {
        "tz": "UTC",
        # ----------------------------------------------------------------
        # Data manager (stub — no DB writes in tests)
        # ----------------------------------------------------------------
        "data_manager": {
            "class_name": "NullDataManager",
            "module_name": "tent.data_manager.null_data_manager"
        },
        # ----------------------------------------------------------------
        # Information service: temperature forecast (CSV path is None;
        # we populate predicted_values directly from OCHRE readings)
        # ----------------------------------------------------------------
        "information_service_models": [
            {
                "class_name": "ReadingsFedTemperatureForecast",
                "module_name": "tests.integration_tests.test_ochre_modelframe",
                "name": "TemperatureForecast",
                "weather_file_path": None,
                "temperature_point_name": "OutdoorAirTemperature",
            }
        ],
        # ----------------------------------------------------------------
        # Local asset: ModelFrameAsset wrapping a single Thermostat model
        # ----------------------------------------------------------------
        "local_assets": [
            {
                "class_name": "ModelFrameAsset",
                "module_name": "tent.local_asset.model_frame.model_frame_asset",
                "name": "HvacHouse",
                # Thermostat consumes ~0–14 kW (negative = demand)
                "minimum_power": -14.0,
                "maximum_power": 0.0,
                "default_power": -7.0,
                # ISM lookup by name
                "information_services": ["TemperatureForecast"],
                "temperature_forecast_name": "TemperatureForecast",
                # ModelFrame config ──────────────────────────────────────
                "model_configs": {
                    "demand_curve_points": 2,
                    "models": [
                        {
                            "subscribe_key": "hvac",
                            "class_name": "Thermostat",
                            "model_config": {
                                "rated_power": 13.68,
                                "c1": _C1,
                                "c2": _C2,
                                "c3": _C3,
                                "c4": _C4,
                                "nominal_setpoint": 22.8,
                                "max_set_point_offset": 2.0,
                                "actuation_key": "hvac/cooling_sp",
                            },
                        }
                    ],
                },
                # OccupancyManager — empty schedule → always occupied
                "occupancy_manager": {
                    "class_name": "SimpleOccupancyManager",
                    "module_name": "tent.local_asset.occupancy_manager.simple",
                    "schedule": {},
                },
                # ActuationManager — real DirectRatioActuationManager;
                # direct_actuate() is still a stub (pass) so no signal is emitted,
                # but actuate() runs without crashing through the fixed base class.
                "actuation_manager": {
                    "class_name": "DirectRatioActuationManager",
                    "module_name": "tent.local_asset.actuation_manager.direct_ratio",
                    "active_onstart": True,
                    "control_interval": 60,
                },
            }
        ],
        # ----------------------------------------------------------------
        # Supply neighbor: non-transactive, two supply vertices that
        # bracket the expected demand curve so base Market can clear.
        # Vertex format: [price, cost, quantity, continuity]
        # quantity ≥ 0 → supply
        # ----------------------------------------------------------------
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
        # ----------------------------------------------------------------
        # Market: base Market (no transactive neighbors needed), single
        # 1-hour interval, Interpolation clearing.
        # ----------------------------------------------------------------
        "market_series": [
            {
                "class_name": "MarketSeries",
                "module_name": "tent.market.market_series",
                "market_class_name": "Market",
                "market_module_name": "tent.market",
                "name": "RT",
                "default_price": 0.05,
                "interval_duration": 3600,    # 1 hour [s]
                "intervals_to_clear": 1,
                "market_clearing_interval": 3600,
                "duration_market_lead": 60,
                "method": "Interpolation",
                "price_model": None,
            }
        ],
    }


def _populate_forecast(tn: MockTN, market, oat_c: float) -> None:
    """
    Fill the TemperatureForecast ISM's predicted_values for every interval
    in the given market with the current outdoor temperature from OCHRE (°C).

    This bypasses CSV file IO and works around the OAT/'OAT' key mismatch:
    the Thermostat model uses self.oat set via update_data(), not the
    forecast value passed by the asset under the wrong key 'OAT'.
    """
    ism = next(i for i in tn.information_service_models if i.name == "TemperatureForecast")
    for time_interval in market.time_intervals:
        ism.predicted_values.update(time_interval, oat_c)


def _drive_market_cycle(tn: MockTN, market, oat_f: float = 20.0) -> dict:
    """
    Advance the market through its full lifecycle using mocked Timer.now().

    Returns a dict mapping TimeInterval → cleared marginal price (float), captured
    after the DeliveryLead transition and *before* the market expires (which destroys
    time_intervals, making post-expiry reads return nothing).

    Sequence mirrors test_market_state_transitions.py: each transition needs
    a 'landing' tick at the transition time and a '+1 s' tick to complete the state.

    oat_f: outdoor air temperature used to populate the temperature forecast ISM
    for any newly-spawned markets so schedule_power() doesn't blow up.
    (Named oat_f for parity with the GLD test; the value is in °C here.)
    """
    tt = market.transition_times
    # Track which markets have already been prepared (by id) so we don't double-prepare.
    _prepared_ids: set = {id(market)}

    def _prepare_market(mkt):
        """Populate forecast and pre-schedule a market."""
        _populate_forecast(tn, mkt, oat_f)
        mkt.check_marginal_prices()
        for local_asset in tn.local_assets:
            local_asset.schedule(mkt)
        for neighbor in tn.neighbors:
            neighbor.schedule(mkt)

    _prepare_market(market)

    def at_with_prepare(t: datetime):
        """
        Step the clock, then prepare any newly-spawned markets so the next tick
        won't crash on balance() → sum_vertices() with empty vertices.
        """
        with patch.object(Timer, 'now', return_value=t):
            tn.step()
        # Prepare any newly spawned markets that haven't been prepared yet
        for series in tn.market_series:
            for mkt in series:
                if id(mkt) not in _prepared_ids:
                    _prepared_ids.add(id(mkt))
                    _prepare_market(mkt)

    # Now drive the full lifecycle
    at_with_prepare(tt.active)
    at_with_prepare(tt.active + timedelta(seconds=1))
    at_with_prepare(tt.negotiation)
    at_with_prepare(tt.negotiation + timedelta(seconds=1))
    at_with_prepare(tt.market_lead)
    at_with_prepare(tt.market_lead + timedelta(seconds=1))
    at_with_prepare(tt.delivery_lead)          # clearing happens here (balance() in Negotiation)
    at_with_prepare(tt.delivery_lead + timedelta(seconds=1))

    # ── Capture cleared prices NOW, before expiry destroys time_intervals ──
    # balance() was called during Negotiation; prices are valid through Delivery.
    cleared_prices: dict = {}
    for ti in market.time_intervals:
        mp = market.marginal_prices.get_by_interval(ti)
        if mp is not None:
            cleared_prices[ti] = mp.value

    at_with_prepare(tt.delivery)               # actuation happens here
    at_with_prepare(tt.delivery + timedelta(seconds=1))
    at_with_prepare(tt.reconciliation)
    at_with_prepare(tt.reconciliation + timedelta(seconds=1))   # → Expired

    return cleared_prices


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ochre_dwelling():
    """
    Boot an OCHRE Dwelling using the bundled HPXML + weather files and yield it.
    The Dwelling is pre-stepped with the initialization period before tests begin.

    Requires the dedicated venv:
      /path/to/tent/OCHRE/.venv/bin/python -m pytest ...
    """
    pytest.importorskip(
        "ochre",
        reason=(
            "ochre not installed — create the dedicated venv:\n"
            "  /usr/local/bin/python3.13 -m venv /path/to/tent/OCHRE/.venv\n"
            "  .venv/bin/pip install --ignore-requires-python -e /path/to/tent/OCHRE\n"
            "  .venv/bin/pip install 'pint==0.24.4'\n"
            "  .venv/bin/pip install -e /path/to/tent/TENT pytest"
        ),
    )

    import datetime as dt
    from ochre import Dwelling
    from ochre.utils import default_input_path

    args = {
        "name": "TentHouse",
        "start_time": dt.datetime(2018, 7, 1, 0, 0),     # summer → cooling is active
        "time_res": dt.timedelta(minutes=15),
        "duration": dt.timedelta(days=3),   # ample horizon across module tests
        "initialization_time": dt.timedelta(days=1),     # warm up to steady state
        "verbosity": 6,   # exposes all 4 status-dict keys we need
        "hpxml_file": os.path.join(
            default_input_path, "Input Files", "bldg0112631-up11.xml"
        ),
        "hpxml_schedule_file": os.path.join(
            default_input_path, "Input Files", "bldg0112631_schedule.csv"
        ),
        "weather_file": os.path.join(
            default_input_path, "Weather", "USA_CO_Denver.Intl.AP.725650_TMY3.epw"
        ),
        "output_path": tempfile.mkdtemp(prefix="ochre_tent_"),
        "save_results": False,   # side-effect-free
    }
    dwelling = Dwelling(**args)
    yield dwelling
    # No explicit teardown required; garbage collection handles it.


@pytest.fixture(scope="module")
def tent_node():
    """
    Build a TENT MockTN with a ModelFrameAsset and supply Neighbor.
    Returns (tn, market, asset).

    Identical to the GridLAB-D test fixture.
    """
    tn = MockTN()
    config = _build_node_config()

    with patch(
        "tent.data_manager.local_data_manager.LocalDataManager.init_archive",
        Mock(return_value=None),
    ):
        tn.configure_node(config)

    if hasattr(tn, "data_manager") and tn.data_manager:
        tn.data_manager.archive_data = Mock(return_value=None)

    assert tn.local_assets, "No local assets configured"
    assert tn.market_series, "No market series configured"

    markets = []
    for series in tn.market_series:
        markets.extend(list(series))
    assert markets, "No markets spawned"

    market = markets[0]
    asset = tn.local_assets[0]

    return tn, market, asset


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOchreModelFrameAsset:
    """Closed-loop TENT ↔ OCHRE integration tests."""

    def test_ochre_steps_and_reads_house_state(self, ochre_dwelling):
        """Sanity: OCHRE Dwelling steps forward and returns plausible HVAC readings (°C)."""
        dwelling = ochre_dwelling

        status = dwelling.update(control_signal={"HVAC Cooling": {"Setpoint": 22.0}})

        assert "Temperature - Outdoor (C)" in status, "OAT key missing from status dict"
        assert "Temperature - Indoor (C)"  in status, "TIN key missing from status dict"
        assert "HVAC Cooling Setpoint (C)" in status, "CSP key missing from status dict"

        oat = status["Temperature - Outdoor (C)"]
        tin = status["Temperature - Indoor (C)"]
        csp = status["HVAC Cooling Setpoint (C)"]

        # Sanity bounds in °C: Denver summer, realistic building temperatures
        assert -20 < oat < 55, f"OAT out of range: {oat} °C"
        assert   5 < tin < 45, f"TIN out of range: {tin} °C"
        assert  15 < csp < 32, f"CSP out of range: {csp} °C"

        # Control signal was sent: setpoint should match what we wrote
        assert abs(csp - 22.0) < 0.1, f"OCHRE did not apply Setpoint=22.0; got {csp}"

        print(f"\n  OCHRE step → OAT={oat:.2f}°C  TIN={tin:.2f}°C  CSP={csp:.2f}°C")

    def test_tent_node_builds_correctly(self, tent_node):
        """TENT node configures without errors: asset, market, and neighbor present."""
        tn, market, asset = tent_node

        assert asset.name == "HvacHouse"
        assert len(tn.neighbors) == 1
        assert tn.neighbors[0].name == "GridSupply"
        assert market is not None
        print(f"\n  Market: {market.name}, intervals: {len(list(market.time_intervals))}")
        print(f"  Asset: {asset.name}")

    def test_model_data_updates_thermostat_state(self, tent_node):
        """ModelFrameAsset.new_model_data() updates the Thermostat model's internal state."""
        _, _, asset = tent_node

        msg = {
            "datetime": datetime(2001, 5, 5, 10, 0, tzinfo=timezone.utc),
            "data": {
                "hvac": {
                    "OutdoorAirTemperature": 20.0,   # °C
                    "ZoneTemperature": 22.0,
                    "ZoneCoolingTemperatureSetPoint": 23.0,
                }
            },
        }
        _feed(asset, msg)

        thermostat = asset.models["hvac"]
        assert abs(thermostat.oat - 20.0) < 1e-6, "OAT not updated"
        assert abs(thermostat.room_temp - 22.0) < 1e-6, "TIN not updated"
        assert not thermostat.error, "Thermostat update_data raised an error"
        print(f"\n  Thermostat state after update: OAT={thermostat.oat}  TIN={thermostat.room_temp}")

    def test_asset_schedule_produces_vertices(self, tent_node):
        """
        After feeding model data and populating the forecast ISM,
        asset.schedule(market) produces scheduled_powers and active_vertices (bid curve).
        """
        tn, market, asset = tent_node

        oat_c = 22.9   # °C — representative Denver summer morning
        tin_c = 21.7
        csp_c = 22.0
        now = datetime(2001, 5, 5, 10, 0, tzinfo=timezone.utc)

        # Feed real-time state into TENT
        _feed(asset, {
            "datetime": now,
            "data": {
                "hvac": {
                    "OutdoorAirTemperature": oat_c,
                    "ZoneTemperature": tin_c,
                    "ZoneCoolingTemperatureSetPoint": csp_c,
                }
            },
        })

        # Populate temperature forecast so schedule_power() doesn't blow up on .value
        _populate_forecast(tn, market, oat_c)

        # Initialise marginal prices and schedule all entities before stepping
        market.check_marginal_prices()
        for neighbor in tn.neighbors:
            neighbor.schedule(market)
        # This is the call under test — schedule the ModelFrameAsset
        asset.schedule(market)

        tt = market.transition_times
        with patch.object(Timer, "now", return_value=tt.active):
            tn.step()
        with patch.object(Timer, "now", return_value=tt.active + timedelta(seconds=1)):
            tn.step()

        intervals = list(market.time_intervals)
        assert intervals, "No time intervals in market"

        for ti in intervals:
            sp = asset.scheduled_powers.get_by_interval(ti)
            av = asset.active_vertices.get_by_interval(ti)

            assert sp is not None, f"No scheduled power for {ti.name}"
            assert av is not None and not av.empty, f"No active vertices for {ti.name}"
            # Two vertices expected (demand_curve_points = 2)
            assert len(av.vertices) == 2, f"Expected 2 vertices, got {len(av.vertices)}"
            # Both vertices should be negative (demand)
            for v in av.vertices:
                assert v.quantity <= 0, f"Supply vertex from demand asset? qty={v.quantity}"

            print(f"\n  {ti.name}: power={sp.value:.2f} kW  "
                  f"v0=(${av.vertices[0].price:.4f},{av.vertices[0].quantity:.2f}kW)  "
                  f"v1=(${av.vertices[1].price:.4f},{av.vertices[1].quantity:.2f}kW)")

    def test_full_closed_loop(self, ochre_dwelling, tent_node):
        """
        Full closed-loop test: N OCHRE timesteps × TENT market cycles.

        Each iteration:
          1. Step OCHRE → read OAT, TIN, CSP (°C).
          2. Push state into TENT asset via new_model_data().
          3. Populate temperature forecast ISM from OAT.
          4. Drive market through full lifecycle.
          5. Read cleared marginal price from market.
          6. Convert price → cooling setpoint (°C).
          7. Command new setpoint in the next dwelling.update() call.

        Assertions:
          - Market clears on each cycle (marginal price exists).
          - Cleared price is a positive finite float.
          - OCHRE setpoint readback matches commanded value (the stronger closed-loop
            proof OCHRE enables — unlike GLD, OCHRE persists the control signal).
          - Setpoint responds correctly to price: higher price → higher setpoint.
        """
        dwelling = ochre_dwelling
        tn, market, asset = tent_node

        N_STEPS = 24   # one market cycle per hour × 24 hours
        OCHRE_STEPS_PER_MARKET = 4   # 4 × 15 min = 1 hour per market cycle
        setpoints_written = []
        prices_cleared = []

        # Initial setpoint command (°C) — will be overwritten by market on cycle 0
        csp_command = 22.0

        for i in range(N_STEPS):
            # ── 1. Advance OCHRE one market interval (1 hour = 4 × 15-min steps) ──
            # The control signal must be re-sent every step or OCHRE reverts to schedule.
            for _ in range(OCHRE_STEPS_PER_MARKET):
                status = dwelling.update(
                    control_signal={"HVAC Cooling": {"Setpoint": csp_command}}
                )
            oat_c = status["Temperature - Outdoor (C)"]
            tin_c = status["Temperature - Indoor (C)"]
            csp_c = status["HVAC Cooling Setpoint (C)"]

            # ── 1a. OCHRE setpoint readback assertion ─────────────────
            # OCHRE persists the control signal — verify it honored the commanded value.
            # Skip i==0: the dwelling initialized with its own schedule setpoint on
            # the first step; the command only takes effect on step 1+.
            if i > 0:
                assert abs(csp_c - csp_command) < 0.1, (
                    f"Step {i}: OCHRE did not honor commanded setpoint "
                    f"{csp_command:.2f}°C; reported {csp_c:.2f}°C"
                )

            # ── 2. Push state into TENT ────────────────────────────────
            now = datetime(2018, 7, 1, i % 24, 0, tzinfo=timezone.utc)
            _feed(asset, {
                "datetime": now,
                "data": {
                    "hvac": {
                        "OutdoorAirTemperature": oat_c,
                        "ZoneTemperature": tin_c,
                        "ZoneCoolingTemperatureSetPoint": csp_c,
                    }
                },
            })

            # ── 3. Populate temperature forecast ───────────────────────
            _populate_forecast(tn, market, oat_c)

            # ── 4. Drive market lifecycle ──────────────────────────────
            # Collect the current non-expired market from the series
            current_markets = []
            for series in tn.market_series:
                current_markets.extend(list(series))
            active_market = next(
                (m for m in current_markets
                 if m.market_state != MarketState.Expired),
                market,
            )

            cleared_prices = _drive_market_cycle(tn, active_market, oat_f=oat_c)

            # ── 5. Read cleared price ──────────────────────────────────
            cleared_price = next(iter(cleared_prices.values()), None) if cleared_prices else None

            assert cleared_price is not None, f"Step {i}: no marginal price cleared"
            assert 0 < cleared_price < 10.0, f"Step {i}: price out of range: {cleared_price}"
            prices_cleared.append(cleared_price)

            # ── 6. Price → setpoint (°C) ───────────────────────────────
            csp_command = _cleared_price_to_setpoint(cleared_price)
            setpoints_written.append(csp_command)
            assert _MIN_CSP_C <= csp_command <= _MAX_CSP_C, (
                f"Step {i}: setpoint {csp_command:.2f}°C outside bounds "
                f"[{_MIN_CSP_C}, {_MAX_CSP_C}]"
            )

            print(
                f"\n  Step {i+1}/{N_STEPS}: "
                f"OAT={oat_c:.2f}°C  TIN={tin_c:.2f}°C  CSP_reported={csp_c:.2f}°C  "
                f"price=${cleared_price:.5f}/kWh  → new_CSP={csp_command:.2f}°C"
            )

        # ── Verify price→setpoint mapping is monotone ─────────────────
        test_prices = [
            _SUPPLY_PRICE_LOW,
            (_SUPPLY_PRICE_LOW + _SUPPLY_PRICE_HIGH) / 2,
            _SUPPLY_PRICE_HIGH,
        ]
        test_sps = [_cleared_price_to_setpoint(p) for p in test_prices]
        assert test_sps[0] < test_sps[1] < test_sps[2], (
            f"Price→setpoint mapping is not monotonically increasing: "
            f"{list(zip(test_prices, test_sps))}"
        )

        print(f"\n  ✅ Completed {N_STEPS} closed-loop iterations")
        print(f"  Prices cleared: {[f'${p:.5f}' for p in prices_cleared]}")
        print(f"  Setpoints commanded: {[f'{s:.2f}°C' for s in setpoints_written]}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
