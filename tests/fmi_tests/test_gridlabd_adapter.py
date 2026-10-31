"""Unit tests for GridLAB-D physical simulation adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from tent.flexibility import (
    DispatchCommand,
    Interval,
    ResourceKind,
    SignConvention,
    capabilities_of,
)
from tent.flexibility.adapters.gridlabd import (
    GridlabdBatteryInverterAdapter,
    GridlabdEngine,
    GridlabdEvAdapter,
    GridlabdFeederAdapter,
    GridlabdHvacAdapter,
    GridlabdWaterHeaterAdapter,
)

UTC = timezone.utc


class DictStubSession:
    """Minimal dictionary-backed test session stub for unit testing adapters."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}

    def register_object(self, name: str, props: dict[str, Any]) -> None:
        self.objects[name] = dict(props)

    def get_property(self, object_name: str, property_name: str) -> tuple[int, Any]:
        if object_name not in self.objects:
            return -1, None
        return 0, self.objects[object_name].get(property_name)

    def set_property(self, object_name: str, property_name: str, value: Any) -> tuple[int, Any]:
        if object_name not in self.objects:
            self.objects[object_name] = {}
        self.objects[object_name][property_name] = value
        return 0, value

    def step(self, *args: Any, **kwargs: Any) -> tuple[int, Any]:
        return 0, None


@pytest.fixture
def mock_session():
    mock = DictStubSession()
    mock.register_object(
        "wh_test",
        {
            "temperature": 125.0,
            "tank_setpoint": 125.0,
            "thermostat_deadband": 4.0,
            "heating_element_capacity": 4.5,
            "tank_volume": 50.0,
            "actual_load": 0.0,
        },
    )
    mock.register_object(
        "house_test",
        {
            "air_temperature": 73.0,
            "cooling_setpoint": 74.0,
            "heating_setpoint": 68.0,
            "thermostat_deadband": 2.0,
            "hvac_power": 0.0,
            "design_cooling_capacity": 3.5,
        },
    )
    mock.register_object(
        "inv_test",
        {
            "P_Out": 0.0,
            "rated_power": 5.0,
            "SOC": 0.50,
            "battery_capacity": 13.5,
        },
    )
    mock.register_object(
        "substation_test",
        {
            "distribution_load": 1500.0,
            "measured_real_power": 1500000.0,
            "measured_reactive_power": 300000.0,
            "voltage_A": 7200.0,
        },
    )
    mock.register_object(
        "ev_test",
        {
            "charge_rate": 0.0,
            "rated_power": 7.2,
            "SOC": 0.40,
            "battery_capacity": 60.0,
            "is_connected": True,
        },
    )
    return mock


@pytest.fixture
def engine(mock_session):
    return GridlabdEngine(session=mock_session)


@pytest.fixture
def intervals():
    return Interval.sequence(datetime(2026, 8, 5, 14, tzinfo=UTC), 900, 4)


class TestGridlabdWaterHeaterAdapter:
    def test_capabilities_and_construction(self, engine):
        adapter = GridlabdWaterHeaterAdapter.from_config(
            resource_id="wh_test",
            rated_kw=4.5,
            gridlabd={"object_name": "wh_test", "engine": engine},
        )
        caps = capabilities_of(adapter)
        assert caps.flexibility is True
        assert caps.state is True
        assert caps.dispatch is True
        assert adapter.identity.kind == ResourceKind.WATER_HEATING

    def test_get_envelope(self, engine, intervals):
        adapter = GridlabdWaterHeaterAdapter(
            identity=adapter_identity("wh_test", ResourceKind.WATER_HEATING),
            object_name="wh_test",
            engine=engine,
            rated_kw=4.5,
        )
        env = adapter.get_envelope(intervals)
        assert len(env) == 4
        assert env[0].q_min_kw == 0.0
        assert env[0].q_max_kw == 4.5
        assert env[0].q_baseline_kw == 0.0  # Temp (125°F) is at setpoint

    def test_dispatch_actuates_gridlabd_setpoint(self, engine, intervals):
        adapter = GridlabdWaterHeaterAdapter(
            identity=adapter_identity("wh_test", ResourceKind.WATER_HEATING),
            object_name="wh_test",
            engine=engine,
            rated_kw=4.5,
        )
        cmd = DispatchCommand.for_power(adapter.identity, intervals[0], 4.5)
        resp = adapter.dispatch(cmd)
        assert resp.accepted is True
        assert resp.actual_power_kw == 4.5
        # Verify setpoint updated in GridLAB-D object
        setpoint = engine.get_float_property("wh_test", "tank_setpoint")
        assert setpoint == 140.0


class TestGridlabdHvacAdapter:
    def test_capabilities_and_envelope(self, engine, intervals):
        adapter = GridlabdHvacAdapter(
            identity=adapter_identity("house_test", ResourceKind.THERMAL_LOAD),
            object_name="house_test",
            engine=engine,
            rated_kw=3.5,
        )
        caps = capabilities_of(adapter)
        assert caps.flexibility is True
        assert caps.dispatch is True
        env = adapter.get_envelope(intervals)
        assert len(env) == 4
        assert env[0].q_min_kw == 0.0
        assert env[0].q_max_kw == 3.5

    def test_dispatch_actuates_house_cooling_setpoint(self, engine, intervals):
        adapter = GridlabdHvacAdapter(
            identity=adapter_identity("house_test", ResourceKind.THERMAL_LOAD),
            object_name="house_test",
            engine=engine,
            rated_kw=3.5,
        )
        # Dispatch 3.5 kW pre-cooling
        cmd = DispatchCommand.for_power(adapter.identity, intervals[0], 3.5)
        resp = adapter.dispatch(cmd)
        assert resp.accepted is True
        assert engine.get_float_property("house_test", "cooling_setpoint") == 68.0


class TestGridlabdBatteryInverterAdapter:
    def test_capabilities_and_envelope(self, engine, intervals):
        adapter = GridlabdBatteryInverterAdapter(
            identity=adapter_identity("inv_test", ResourceKind.STORAGE),
            object_name="inv_test",
            engine=engine,
            rated_kw=5.0,
            capacity_kwh=13.5,
        )
        caps = capabilities_of(adapter)
        assert caps.flexibility is True
        assert caps.dispatch is True

        env = adapter.get_envelope(intervals)
        assert len(env) == 4
        assert env[0].q_min_kw == -5.0  # Discharging in load-positive
        assert env[0].q_max_kw == 5.0   # Charging

    def test_dispatch_actuates_inverter_p_out(self, engine, intervals):
        adapter = GridlabdBatteryInverterAdapter(
            identity=adapter_identity("inv_test", ResourceKind.STORAGE),
            object_name="inv_test",
            engine=engine,
            rated_kw=5.0,
            capacity_kwh=13.5,
        )
        cmd = DispatchCommand.for_power(adapter.identity, intervals[0], -4.0)
        resp = adapter.dispatch(cmd)
        assert resp.accepted is True
        # P_Out is generation-positive and in VA, so a canonical load-positive
        # discharge of -4.0 kW must be written as +4000 VA.
        assert engine.get_float_property("inv_test", "P_Out") == 4000.0
        # And reading it back must round-trip to canonical load-positive kW.
        assert adapter.current_power_kw == -4.0


class TestGridlabdFeederAdapter:
    def test_capabilities_and_envelope(self, engine, intervals):
        adapter = GridlabdFeederAdapter(
            identity=adapter_identity("substation_test", ResourceKind.AGGREGATE),
            object_name="substation_test",
            engine=engine,
            transformer_capacity_kw=5000.0,
        )
        caps = capabilities_of(adapter)
        assert caps.flexibility is True
        assert caps.state is True
        assert caps.dispatch is True

        env = adapter.get_envelope(intervals)
        assert len(env) == 4
        assert env[0].q_min_kw == -5000.0
        assert env[0].q_max_kw == 5000.0
        assert env[0].q_baseline_kw == 1500.0

    def test_dispatch_actuates_distribution_load(self, engine, intervals):
        adapter = GridlabdFeederAdapter(
            identity=adapter_identity("substation_test", ResourceKind.AGGREGATE),
            object_name="substation_test",
            engine=engine,
            transformer_capacity_kw=5000.0,
        )
        cmd = DispatchCommand.for_power(adapter.identity, intervals[0], 2000.0)
        resp = adapter.dispatch(cmd)
        assert resp.accepted is True
        assert engine.get_float_property("substation_test", "distribution_load") == 2000.0


class TestGridlabdEvAdapter:
    def test_capabilities_and_envelope(self, engine, intervals):
        adapter = GridlabdEvAdapter(
            identity=adapter_identity("ev_test", ResourceKind.ELECTRIC_VEHICLE),
            object_name="ev_test",
            engine=engine,
            rated_charging_kw=7.2,
            battery_capacity_kwh=60.0,
            target_soc=0.90,
        )
        caps = capabilities_of(adapter)
        assert caps.flexibility is True
        assert caps.state is True
        assert caps.dispatch is True

        env = adapter.get_envelope(intervals)
        assert len(env) == 4
        assert env[0].q_min_kw == 0.0
        assert env[0].q_max_kw == 7.2
        assert env[0].q_baseline_kw == 7.2  # Under target SOC, demands charging

    def test_dispatch_actuates_ev_charge_rate(self, engine, intervals):
        adapter = GridlabdEvAdapter(
            identity=adapter_identity("ev_test", ResourceKind.ELECTRIC_VEHICLE),
            object_name="ev_test",
            engine=engine,
            rated_charging_kw=7.2,
            battery_capacity_kwh=60.0,
            target_soc=0.90,
        )
        cmd = DispatchCommand.for_power(adapter.identity, intervals[0], 7.2)
        resp = adapter.dispatch(cmd)
        assert resp.accepted is True
        assert engine.get_float_property("ev_test", "charge_rate") == 7.2


def adapter_identity(res_id: str, kind: ResourceKind):
    from tent.flexibility import ResourceIdentity
    return ResourceIdentity(resource_id=res_id, kind=kind, source="test")
