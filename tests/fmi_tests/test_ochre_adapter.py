"""Unit tests for NREL OCHRE physical simulation adapters."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

# OCHRE is an optional extra (pip install 'tent[ochre]'). Skip rather than
# hard-fail the suite when it is absent, matching test_pvlib_adapter.py.
pytest.importorskip("ochre")

from der_interface import (  # noqa: E402
    DispatchCommand,
    Interval,
    ResourceKind,
    SignConvention,
    capabilities_of,
)
from der_interface.adapters.ochre import (  # noqa: E402
    OchreBatteryAdapter,
    OchreDwellingAdapter,
    OchreEvAdapter,
    OchreHvacAdapter,
    OchreWaterHeaterAdapter,
)

UTC = timezone.utc


class TestOchreBatteryAdapter:
    """Test OCHRE battery storage physical model."""

    def test_battery_construction_and_capabilities(self):
        battery = OchreBatteryAdapter.from_config(
            resource_id="test_battery",
            rated_kw=5.0,
            capacity_kwh=10.0,
            min_soc=0.10,
            max_soc=0.90,
        )
        assert battery.identity.resource_id == "test_battery"
        assert battery.identity.kind == ResourceKind.STORAGE
        assert battery.identity.source == "ochre"

        caps = capabilities_of(battery)
        assert caps.flexibility is True
        assert caps.dispatch is True
        assert caps.state is True

    def test_battery_bidirectional_envelope(self):
        battery = OchreBatteryAdapter.from_config(
            resource_id="test_battery",
            rated_kw=5.0,
            capacity_kwh=10.0,
            min_soc=0.10,
            max_soc=0.90,
        )
        iv = Interval(datetime(2026, 8, 5, 12, 0, tzinfo=UTC), 900)
        env = battery.get_envelope([iv])
        assert len(env) == 1
        pt = env[0]

        # 50% SOC -> allows full +/- 5.0 kW charge/discharge headroom
        assert pt.q_min_kw == pytest.approx(-5.0, 0.1)
        assert pt.q_max_kw == pytest.approx(5.0, 0.1)
        assert pt.q_baseline_kw == 0.0

    def test_battery_dispatch_and_state(self):
        battery = OchreBatteryAdapter.from_config(
            resource_id="test_battery",
            rated_kw=5.0,
            capacity_kwh=10.0,
        )
        iv = Interval(datetime(2026, 8, 5, 12, 0, tzinfo=UTC), 900)
        cmd = DispatchCommand.for_power(battery.identity, iv, 2.5, sign=SignConvention.LOAD_POSITIVE)
        resp = battery.dispatch(cmd)

        assert resp.accepted is True
        assert resp.actual_power_kw == pytest.approx(2.5, 0.1)
        assert battery.soc > 0.50  # charged

        state = battery.get_state()
        assert state.power_kw is not None
        assert state.values["capacity_kwh"] == 10.0


class TestOchreWaterHeaterAdapter:
    """Test OCHRE water heater physical model."""

    def test_wh_construction_and_capabilities(self):
        wh = OchreWaterHeaterAdapter.from_config(
            resource_id="test_wh",
            rated_kw=4.5,
            t_min_f=110.0,
            t_max_f=140.0,
            t_desired_f=125.0,
        )
        assert wh.identity.resource_id == "test_wh"
        assert wh.identity.kind == ResourceKind.WATER_HEATING

        caps = capabilities_of(wh)
        assert caps.flexibility is True
        assert caps.dispatch is True
        assert caps.state is True

    def test_wh_envelope_and_dispatch(self):
        wh = OchreWaterHeaterAdapter.from_config(
            resource_id="test_wh",
            rated_kw=4.5,
        )
        iv = Interval(datetime(2026, 8, 5, 12, 0, tzinfo=UTC), 900)
        env = wh.get_envelope([iv])
        assert len(env) == 1
        pt = env[0]

        assert pt.q_min_kw == 0.0
        assert pt.q_max_kw == pytest.approx(4.5, 0.1)

        # Dispatch maximum heating
        cmd = DispatchCommand.for_power(wh.identity, iv, 4.5, sign=SignConvention.LOAD_POSITIVE)
        resp = wh.dispatch(cmd)
        assert resp.accepted is True
        assert resp.actual_power_kw == pytest.approx(4.5, 0.1)


class TestOchreHvacAdapter:
    """Test OCHRE dwelling HVAC physical model."""

    def test_hvac_construction_and_envelope(self):
        hvac = OchreHvacAdapter.from_config(
            resource_id="test_hvac",
            rated_kw=3.5,
            min_cooling_setpoint_f=68.0,
            max_cooling_setpoint_f=78.0,
        )
        assert hvac.identity.resource_id == "test_hvac"
        assert hvac.identity.kind == ResourceKind.THERMAL_LOAD

        caps = capabilities_of(hvac)
        assert caps.flexibility is True
        assert caps.dispatch is True

        iv = Interval(datetime(2018, 1, 1, 12, 0, tzinfo=UTC), 900)
        env = hvac.get_envelope([iv])
        assert len(env) == 1
        assert env[0].q_min_kw >= 0.0
        assert env[0].q_max_kw == pytest.approx(3.5, 0.1)

    def test_hvac_dispatch_and_state(self):
        hvac = OchreHvacAdapter.from_config(
            resource_id="test_hvac",
            rated_kw=3.5,
        )
        iv = Interval(datetime(2018, 1, 1, 12, 0, tzinfo=UTC), 900)
        cmd = DispatchCommand.for_power(hvac.identity, iv, 3.5, sign=SignConvention.LOAD_POSITIVE)
        resp = hvac.dispatch(cmd)
        assert resp.accepted is True

        state = hvac.get_state()
        assert "indoor_temp_f" in state.values
        assert 60.0 <= state.values["indoor_temp_f"] <= 85.0


class TestOchreEvAdapter:
    """Test OCHRE electric vehicle charger physical model."""

    def test_ev_construction_and_charging(self):
        ev = OchreEvAdapter.from_config(
            resource_id="test_ev",
            rated_kw=7.2,
            capacity_kwh=65.0,
            initial_soc=0.40,
            target_soc=0.90,
            start_time=datetime(2026, 8, 5, 20, 0, tzinfo=UTC),
        )
        assert ev.identity.resource_id == "test_ev"
        assert ev.identity.kind == ResourceKind.ELECTRIC_VEHICLE

        iv = Interval(datetime(2026, 8, 5, 20, 0, tzinfo=UTC), 900)
        env = ev.get_envelope([iv])
        assert len(env) == 1
        pt = env[0]

        assert pt.q_min_kw == 0.0
        assert pt.q_max_kw == pytest.approx(7.2, 0.1)

        # Dispatch 7.2 kW charging
        cmd = DispatchCommand.for_power(ev.identity, iv, 7.2, sign=SignConvention.LOAD_POSITIVE)
        resp = ev.dispatch(cmd)
        assert resp.accepted is True
        assert resp.actual_power_kw > 0.0
        assert ev.soc > 0.40  # SOC increased


class TestOchreDwellingAdapter:
    """Test OCHRE whole-dwelling aggregator."""

    def test_dwelling_construction_and_step(self):
        dwelling = OchreDwellingAdapter.from_config(
            resource_id="test_dwelling",
            rated_kw=15.0,
        )
        assert dwelling.identity.resource_id == "test_dwelling"
        assert dwelling.identity.kind == ResourceKind.BUILDING

        iv = Interval(datetime(2018, 1, 1, 12, 0, tzinfo=UTC), 900)
        env = dwelling.get_envelope([iv])
        assert len(env) == 1

        cmd = DispatchCommand.for_power(dwelling.identity, iv, 5.0, sign=SignConvention.LOAD_POSITIVE)
        resp = dwelling.dispatch(cmd)
        assert resp.accepted is True

        state = dwelling.get_state()
        assert state.power_kw is not None
        assert state.values["rated_kw"] == 15.0
