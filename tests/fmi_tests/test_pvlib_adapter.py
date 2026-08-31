"""pvlib adapter tests.

Includes a direct comparison against the cosine approximation these adapters are
meant to replace.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from der_interface import (
    CapabilityError,
    Interval,
    Quality,
    SignConvention,
    capabilities_of,
)

pvlib = pytest.importorskip("pvlib")
pd = pytest.importorskip("pandas")

from der_interface.adapters.pvlib import (  # noqa: E402
    ClearSkyWeather,
    DataFrameWeather,
    PvlibPvAdapter,
)

UTC = timezone.utc
LOAD_POS = SignConvention.LOAD_POSITIVE
GEN_POS = SignConvention.GENERATION_POSITIVE

# Richland, WA -- PNNL. Same site TENT's sample campus config describes.
LAT, LON, TZ = 46.28, -119.28, "US/Pacific"
RATED_KW = 120.0


def build_system():
    from pvlib.pvsystem import PVSystem
    from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS

    return PVSystem(
        surface_tilt=30,
        surface_azimuth=180,
        module_parameters={"pdc0": RATED_KW * 1000, "gamma_pdc": -0.004},
        inverter_parameters={"pdc0": RATED_KW * 1000},
        temperature_model_parameters=(
            TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]
        ),
    )


@pytest.fixture
def location():
    from pvlib.location import Location

    return Location(LAT, LON, tz=TZ)


@pytest.fixture
def adapter(location):
    return PvlibPvAdapter.from_location(
        "campus_pv", location, build_system(), rated_kw=RATED_KW
    )


@pytest.fixture
def summer_day():
    """Hourly intervals across a full local summer day."""
    start = pd.Timestamp("2026-07-15 00:00", tz=TZ).to_pydatetime()
    return Interval.sequence(start, 3600, 24)


class TestConstruction:
    def test_identity_populated(self, adapter):
        assert adapter.identity.resource_id == "campus_pv"
        assert "pvlib" in adapter.identity.source
        assert adapter.identity.metadata["latitude"] == LAT
        assert adapter.identity.metadata["rated_kw"] == RATED_KW

    def test_rejects_non_positive_rating(self, location):
        from der_interface import AdapterError

        with pytest.raises(AdapterError, match="rated_kw must be > 0"):
            PvlibPvAdapter.from_location("x", location, build_system(), rated_kw=0.0)

    def test_capabilities_exclude_dispatch(self, adapter):
        """You cannot command the sun."""
        caps = capabilities_of(adapter)
        assert caps.flexibility
        assert caps.forecast
        assert caps.state
        assert not caps.dispatch


class TestSignConvention:
    def test_generation_is_negative_under_load_positive(self, adapter, summer_day):
        """The single most likely bug in a PV integration."""
        envelope = adapter.get_envelope(summer_day)
        midday = [p for p in envelope if p.q_baseline_kw != 0.0]
        assert midday, "expected some generation during a July day"
        assert all(p.q_baseline_kw < 0.0 for p in midday)

    def test_generation_is_positive_under_generation_positive(self, location, summer_day):
        adapter = PvlibPvAdapter.from_location(
            "campus_pv", location, build_system(), rated_kw=RATED_KW, sign=GEN_POS
        )
        envelope = adapter.get_envelope(summer_day)
        midday = [p for p in envelope if p.q_baseline_kw != 0.0]
        assert all(p.q_baseline_kw > 0.0 for p in midday)

    def test_conventions_are_mirror_images(self, location, summer_day):
        load_pos = PvlibPvAdapter.from_location(
            "pv", location, build_system(), rated_kw=RATED_KW, sign=LOAD_POS
        ).get_envelope(summer_day)
        gen_pos = PvlibPvAdapter.from_location(
            "pv", location, build_system(), rated_kw=RATED_KW, sign=GEN_POS
        ).get_envelope(summer_day)
        for a, b in zip(load_pos, gen_pos):
            assert a.q_baseline_kw == pytest.approx(-b.q_baseline_kw)


class TestMustTake:
    def test_zero_width_envelope(self, adapter, summer_day):
        """Default PV is must-take: no flexibility to offer."""
        envelope = adapter.get_envelope(summer_day)
        assert not envelope.flexible
        for point in envelope:
            assert point.q_min_kw == point.q_max_kw == point.q_baseline_kw
            assert point.range_kw == 0.0

    def test_marked_estimated(self, adapter, summer_day):
        """A model is not a meter."""
        for point in adapter.get_envelope(summer_day):
            assert point.quality is Quality.ESTIMATED

    def test_within_nameplate_limits(self, adapter, summer_day):
        adapter.get_envelope(summer_day).assert_within(adapter.get_limits())


class TestCurtailable:
    def test_curtailable_has_flexibility(self, location, summer_day):
        """One flag turns must-take PV into a flexible resource."""
        adapter = PvlibPvAdapter.from_location(
            "pv", location, build_system(), rated_kw=RATED_KW, curtailable=True
        )
        envelope = adapter.get_envelope(summer_day)
        assert envelope.flexible

    def test_curtailment_is_the_maximum_under_load_positive(self, location, summer_day):
        """Generation is negative, so full output is the MINIMUM and
        curtailment (0 kW) is the MAXIMUM. This inversion trips people up."""
        adapter = PvlibPvAdapter.from_location(
            "pv", location, build_system(), rated_kw=RATED_KW, curtailable=True
        )
        for point in adapter.get_envelope(summer_day):
            assert point.q_max_kw == 0.0
            assert point.q_min_kw <= 0.0
            assert point.q_baseline_kw == point.q_min_kw

    def test_baseline_is_generate_not_curtail(self, location, summer_day):
        """Default behaviour: generate unless paid not to."""
        adapter = PvlibPvAdapter.from_location(
            "pv", location, build_system(), rated_kw=RATED_KW, curtailable=True
        )
        envelope = adapter.get_envelope(summer_day)
        assert envelope.baseline_energy_kwh < 0.0


class TestPhysics:
    def test_night_is_zero(self, adapter):
        night = Interval.sequence(
            pd.Timestamp("2026-07-15 01:00", tz=TZ).to_pydatetime(), 3600, 3
        )
        for point in adapter.get_envelope(night):
            assert point.q_baseline_kw == 0.0

    def test_peak_near_solar_noon(self, adapter, summer_day):
        envelope = adapter.get_envelope(summer_day)
        powers = [p.q_baseline_kw for p in envelope]
        peak_hour = powers.index(min(powers))  # most negative == most generation
        assert 11 <= peak_hour <= 14, f"peak at hour {peak_hour}, expected near noon"

    def test_output_below_nameplate(self, adapter, summer_day):
        for point in adapter.get_envelope(summer_day):
            assert abs(point.q_baseline_kw) <= RATED_KW * 1.05

    def test_winter_generates_less_than_summer(self, adapter):
        """Seasonal variation the cosine model cannot represent at all."""
        summer = adapter.get_envelope(
            Interval.sequence(pd.Timestamp("2026-07-15 00:00", tz=TZ).to_pydatetime(), 3600, 24)
        )
        winter = adapter.get_envelope(
            Interval.sequence(pd.Timestamp("2026-12-15 00:00", tz=TZ).to_pydatetime(), 3600, 24)
        )
        assert abs(winter.baseline_energy_kwh) < abs(summer.baseline_energy_kwh)

    def test_interval_midpoint_used(self, adapter):
        """Evaluating at the interval start biases mornings low.

        A 06:00-07:00 interval should reflect ~06:30 conditions, so it must
        generate more than an instantaneous 06:00 evaluation would suggest.
        """
        hour = Interval(pd.Timestamp("2026-07-15 06:00", tz=TZ).to_pydatetime(), 3600)
        minute_at_start = Interval(
            pd.Timestamp("2026-07-15 06:00", tz=TZ).to_pydatetime(), 60
        )
        hourly = adapter.get_envelope([hour])[0].q_baseline_kw
        at_start = adapter.get_envelope([minute_at_start])[0].q_baseline_kw
        assert abs(hourly) > abs(at_start)


class TestAgainstCosineApproximation:
    """Direct comparison with the model this replaces.

    TENT's ``solar_pv_resource_model.py:85``::

        if hour < 5.5 or hour > 17.5: power = 0
        else: power = max_power * 0.5 * (1 + cos((hour - 12) * 2*pi / 12)) * cloud_factor
    """

    @staticmethod
    def cosine_kw(hour_of_day: float, max_power: float = RATED_KW,
                  cloud_factor: float = 1.0) -> float:
        if hour_of_day < 5.5 or hour_of_day > 17.5:
            return 0.0
        return max_power * 0.5 * (1 + math.cos((hour_of_day - 12) * 2.0 * math.pi / 12)) * cloud_factor

    def test_cosine_claims_generation_when_pvlib_says_dark(self, adapter):
        """In December the cosine model still reports output at 17:00.

        Sunset in Richland in mid-December is around 16:15.
        """
        interval = Interval(pd.Timestamp("2026-12-15 17:00", tz=TZ).to_pydatetime(), 3600)
        actual = abs(adapter.get_envelope([interval])[0].q_baseline_kw)
        naive = self.cosine_kw(17.0)
        assert naive > 1.0, "cosine model should claim output at 17:00"
        assert actual < 1.0, "pvlib should report darkness"

    def test_cosine_overstates_winter_daily_energy(self, adapter):
        """The cosine model has no seasonal awareness whatsoever."""
        day = Interval.sequence(
            pd.Timestamp("2026-12-15 00:00", tz=TZ).to_pydatetime(), 3600, 24
        )
        actual = abs(adapter.get_envelope(day).baseline_energy_kwh)
        naive = sum(self.cosine_kw(h) for h in range(24))
        assert naive > actual * 1.5

    def test_cosine_is_symmetric_but_reality_is_not(self, adapter):
        """The cosine curve is exactly symmetric about noon. Real output is not,
        because module temperature rises through the afternoon and cuts
        efficiency."""
        morning = Interval(pd.Timestamp("2026-07-15 09:00", tz=TZ).to_pydatetime(), 3600)
        afternoon = Interval(pd.Timestamp("2026-07-15 15:00", tz=TZ).to_pydatetime(), 3600)
        m = abs(adapter.get_envelope([morning])[0].q_baseline_kw)
        a = abs(adapter.get_envelope([afternoon])[0].q_baseline_kw)
        assert self.cosine_kw(9.5) == pytest.approx(self.cosine_kw(14.5))
        assert m != pytest.approx(a, rel=0.01)


class TestForecast:
    def test_available_variables(self, adapter):
        assert "solar_power_kw" in adapter.available_forecasts()
        assert "ac_output_kw" in adapter.available_forecasts()

    def test_solar_power_is_sign_normalized(self, adapter, summer_day):
        forecast = adapter.get_forecast("solar_power_kw", summer_day)
        assert any(v < 0.0 for v in forecast.values())

    def test_ac_output_is_raw_magnitude(self, adapter, summer_day):
        """What OCHRE PV, GridLAB-D solar, and a TESP BTM-solar stream expect."""
        forecast = adapter.get_forecast("ac_output_kw", summer_day)
        assert all(v >= 0.0 for v in forecast.values())

    def test_the_two_agree_in_magnitude(self, adapter, summer_day):
        signed = adapter.get_forecast("solar_power_kw", summer_day).values()
        raw = adapter.get_forecast("ac_output_kw", summer_day).values()
        for s, r in zip(signed, raw):
            assert abs(s) == pytest.approx(r)

    def test_unknown_variable_raises(self, adapter, summer_day):
        with pytest.raises(CapabilityError, match="cannot forecast"):
            adapter.get_forecast("wind_speed", summer_day[:1])


class TestWeatherSources:
    def test_cloud_factor_reduces_output(self, location, summer_day):
        clear = PvlibPvAdapter.from_location(
            "pv", location, build_system(), rated_kw=RATED_KW,
            weather_source=ClearSkyWeather(location, cloud_factor=1.0),
        )
        cloudy = PvlibPvAdapter.from_location(
            "pv", location, build_system(), rated_kw=RATED_KW,
            weather_source=ClearSkyWeather(location, cloud_factor=0.3),
        )
        assert abs(cloudy.get_envelope(summer_day).baseline_energy_kwh) < abs(
            clear.get_envelope(summer_day).baseline_energy_kwh
        )

    def test_cloud_factor_range_validated(self, location):
        from der_interface import AdapterError

        with pytest.raises(AdapterError, match=r"must be in \[0, 1\]"):
            ClearSkyWeather(location, cloud_factor=1.5)

    def test_dataframe_weather(self, location, summer_day):
        """A TMY file, PVGIS pull, or live API all arrive as a DataFrame."""
        times = pd.DatetimeIndex(
            [i.start for i in summer_day]
        ).tz_convert(TZ)
        frame = location.get_clearsky(times)
        frame["temp_air"] = 35.0
        frame["wind_speed"] = 1.0

        adapter = PvlibPvAdapter.from_location(
            "pv", location, build_system(), rated_kw=RATED_KW,
            weather_source=DataFrameWeather(frame),
        )
        envelope = adapter.get_envelope(summer_day)
        assert len(envelope) == 24

    def test_hot_weather_reduces_efficiency(self, location):
        """Module temperature matters. The cosine model cannot express this."""
        noon = [Interval(pd.Timestamp("2026-07-15 12:00", tz=TZ).to_pydatetime(), 3600)]
        cool = PvlibPvAdapter.from_location(
            "pv", location, build_system(), rated_kw=RATED_KW,
            weather_source=ClearSkyWeather(location, temp_air_c=10.0),
        )
        hot = PvlibPvAdapter.from_location(
            "pv", location, build_system(), rated_kw=RATED_KW,
            weather_source=ClearSkyWeather(location, temp_air_c=45.0),
        )
        assert abs(hot.get_envelope(noon)[0].q_baseline_kw) < abs(
            cool.get_envelope(noon)[0].q_baseline_kw
        )


class TestFromConfig:
    """Config-driven construction, as a JSON/YAML file would use."""

    def base_config(self, **overrides):
        config = {
            "resource_id": "campus_pv",
            "rated_kw": RATED_KW,
            # altitude omitted so this matches the `location` fixture, which
            # uses pvlib's default of 0 m.
            "location": {"latitude": LAT, "longitude": LON, "tz": TZ},
            "system": {"surface_tilt": 30, "surface_azimuth": 180},
        }
        config.update(overrides)
        return config

    def test_builds_from_plain_dicts(self, summer_day):
        adapter = PvlibPvAdapter.from_config(**self.base_config())
        envelope = adapter.get_envelope(summer_day)
        assert len(envelope) == 24
        assert any(p.q_baseline_kw < 0.0 for p in envelope)

    def test_altitude_affects_output(self, summer_day):
        """Altitude is a real physical input, not decoration."""
        sea_level = PvlibPvAdapter.from_config(**self.base_config())
        elevated = PvlibPvAdapter.from_config(
            **self.base_config(
                location={"latitude": LAT, "longitude": LON, "tz": TZ, "altitude": 1500}
            )
        )
        assert abs(elevated.get_envelope(summer_day).baseline_energy_kwh) != pytest.approx(
            abs(sea_level.get_envelope(summer_day).baseline_energy_kwh), rel=1e-4
        )

    def test_matches_from_location(self, location, summer_day):
        """The two factories must agree."""
        explicit = PvlibPvAdapter.from_location(
            "campus_pv", location, build_system(), rated_kw=RATED_KW
        ).get_envelope(summer_day)
        configured = PvlibPvAdapter.from_config(**self.base_config()).get_envelope(summer_day)
        for a, b in zip(explicit, configured):
            assert a.q_baseline_kw == pytest.approx(b.q_baseline_kw, rel=1e-6)

    def test_dc_rating_defaults_from_rated_kw(self, summer_day):
        """So the rating need not be stated twice."""
        adapter = PvlibPvAdapter.from_config(**self.base_config())
        for point in adapter.get_envelope(summer_day):
            assert abs(point.q_baseline_kw) <= RATED_KW * 1.05

    def test_missing_latitude_names_the_problem(self):
        config = self.base_config()
        del config["location"]["latitude"]
        from der_interface import AdapterError

        with pytest.raises(AdapterError, match="latitude and longitude are required"):
            PvlibPvAdapter.from_config(**config)

    def test_unknown_temperature_model_lists_valid_options(self):
        config = self.base_config()
        config["system"]["temperature_model"] = "nonsense"
        from der_interface import AdapterError

        with pytest.raises(AdapterError, match="unknown temperature_model"):
            PvlibPvAdapter.from_config(**config)

    def test_unknown_weather_kind_rejected(self):
        from der_interface import AdapterError

        with pytest.raises(AdapterError, match="unknown weather kind"):
            PvlibPvAdapter.from_config(**self.base_config(weather={"kind": "magic"}))

    def test_cloud_factor_via_config(self, summer_day):
        clear = PvlibPvAdapter.from_config(**self.base_config())
        cloudy = PvlibPvAdapter.from_config(
            **self.base_config(weather={"kind": "clearsky", "cloud_factor": 0.5})
        )
        assert abs(cloudy.get_envelope(summer_day).baseline_energy_kwh) < abs(
            clear.get_envelope(summer_day).baseline_energy_kwh
        )

    def test_curtailable_via_config(self, summer_day):
        adapter = PvlibPvAdapter.from_config(**self.base_config(curtailable=True))
        assert adapter.get_envelope(summer_day).flexible

    def test_tmy_requires_path(self):
        from der_interface import AdapterError

        with pytest.raises(AdapterError, match='requires a "path"'):
            PvlibPvAdapter.from_config(**self.base_config(weather={"kind": "tmy"}))

    def test_defaults_are_reasonable(self, summer_day):
        """Only latitude, longitude, and rating are truly required."""
        adapter = PvlibPvAdapter.from_config(
            resource_id="minimal",
            rated_kw=10.0,
            location={"latitude": LAT, "longitude": LON, "tz": TZ},
            system={},
        )
        assert any(p.q_baseline_kw < 0.0 for p in adapter.get_envelope(summer_day))


class TestStateAndLimits:
    def test_state_is_estimated(self, adapter):
        state = adapter.get_state(pd.Timestamp("2026-07-15 12:00", tz=TZ).to_pydatetime())
        assert state.quality is Quality.ESTIMATED
        assert state.power_kw < 0.0

    def test_limits_span_zero_to_negative_rating(self, adapter):
        limits = adapter.get_limits()
        assert limits.min_power_kw == -RATED_KW
        assert limits.max_power_kw == 0.0

    def test_limits_independent_of_weather(self, adapter):
        """Nameplate is nameplate."""
        a = adapter.get_limits(pd.Timestamp("2026-07-15 12:00", tz=TZ).to_pydatetime())
        b = adapter.get_limits(pd.Timestamp("2026-12-15 23:00", tz=TZ).to_pydatetime())
        assert a == b


class TestEdgeCases:
    def test_empty_intervals_rejected(self, adapter):
        from der_interface import AdapterError

        with pytest.raises(AdapterError, match="at least one interval"):
            adapter.get_envelope([])

    def test_one_point_per_interval(self, adapter, summer_day):
        envelope = adapter.get_envelope(summer_day)
        assert envelope.intervals == tuple(summer_day)

    def test_sub_hourly_intervals(self, adapter):
        intervals = Interval.sequence(
            pd.Timestamp("2026-07-15 12:00", tz=TZ).to_pydatetime(), 300, 12
        )
        assert len(adapter.get_envelope(intervals)) == 12

    def test_utc_intervals_work(self, adapter):
        """Callers need not use the site's local timezone."""
        intervals = Interval.sequence(datetime(2026, 7, 15, 19, tzinfo=UTC), 3600, 3)
        envelope = adapter.get_envelope(intervals)
        assert any(p.q_baseline_kw < 0.0 for p in envelope)
