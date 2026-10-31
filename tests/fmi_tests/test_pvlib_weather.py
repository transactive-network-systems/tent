"""Regression tests for pvlib weather handling.

Two silent-wrong-number defects are guarded here:

1. TMY3 files splice months from different years, producing a non-monotonic
   index that pandas cannot reindex -- so the campus_pv preset raised on every
   envelope request.
2. A weather frame that does not cover the requested horizon yields all-NaN,
   which pvlib turns into an AC output of 0.0. A 120 kW array then reported a
   confident 0 kW at solar noon, flagged Quality.ESTIMATED.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("pvlib")
pd = pytest.importorskip("pandas")

from tent.flexibility.conventions import Quality  # noqa: E402
from tent.flexibility.errors import AdapterError  # noqa: E402
from tent.flexibility.adapters.pvlib.pv import (  # noqa: E402
    CloudCoverWeather,
    DataFrameWeather,
    PvlibPvAdapter,
)
from tent.flexibility.schema import Interval  # noqa: E402

UTC = timezone.utc

BASE = dict(
    resource_id="pv_test",
    rated_kw=120.0,
    location={"latitude": 46.28, "longitude": -119.28, "tz": "US/Pacific"},
    system={"surface_tilt": 30, "surface_azimuth": 180},
)


def typical_year_frame(year: int = 2000, periods: int = 8760):
    idx = pd.date_range(f"{year}-01-01", periods=periods, freq="h", tz="US/Pacific")
    return pd.DataFrame(
        {"ghi": 900.0, "dni": 800.0, "dhi": 100.0, "temp_air": 25.0, "wind_speed": 1.0},
        index=idx,
    )


class TestNonMonotonicIndex:
    def test_reindex_of_spliced_index_raises_adapter_error(self):
        """A non-monotonic frame must fail loudly, not with a bare pandas error."""
        # Mimic a real TMY3: a later calendar month drawn from an earlier year,
        # which is what makes the index jump backwards.
        jan_2002 = typical_year_frame(2002, 24)
        feb_2000 = typical_year_frame(2000, 24)
        feb_2000.index = feb_2000.index + pd.DateOffset(months=1)
        spliced = pd.concat([jan_2002, feb_2000])
        assert not spliced.index.is_monotonic_increasing

        weather = DataFrameWeather(spliced)
        times = pd.DatetimeIndex([datetime(2002, 1, 1, 3, tzinfo=UTC)])
        with pytest.raises(AdapterError):
            weather.get(times)

    def test_sorted_frame_reindexes_cleanly(self):
        jan_2002 = typical_year_frame(2002, 24)
        feb_2000 = typical_year_frame(2000, 24)
        feb_2000.index = feb_2000.index + pd.DateOffset(months=1)
        spliced = pd.concat([jan_2002, feb_2000]).sort_index()
        assert spliced.index.is_monotonic_increasing

        weather = DataFrameWeather(spliced, wrap_year=True)
        times = pd.DatetimeIndex([datetime(2026, 1, 1, 12, tzinfo=UTC)])
        assert len(weather.get(times)) == 1


class TestWeatherGapsAreFlaggedMissing:
    def test_horizon_outside_frame_is_missing_not_zero(self):
        """The regression: typical-year data vs a 2026 horizon."""
        frame = typical_year_frame(2000, 48)
        adapter = PvlibPvAdapter.from_config(
            **BASE, weather={"kind": "dataframe", "frame": frame}
        )
        intervals = Interval.sequence(datetime(2026, 8, 5, 20, tzinfo=UTC), 3600, 3)

        env = adapter.get_envelope(intervals)
        for point in env:
            assert point.quality is Quality.MISSING
            assert point.q_baseline_kw == 0.0

    def test_genuine_night_stays_estimated(self):
        """A real zero must remain distinguishable from a data gap."""
        adapter = PvlibPvAdapter.from_config(**BASE, weather={"kind": "clearsky"})
        # 09:00 UTC is ~02:00 local, solidly night.
        intervals = Interval.sequence(datetime(2026, 8, 5, 9, tzinfo=UTC), 3600, 1)

        point = adapter.get_envelope(intervals)[0]
        assert point.q_baseline_kw == 0.0
        assert point.quality is Quality.ESTIMATED

    def test_forecast_marks_gaps_missing(self):
        frame = typical_year_frame(2000, 48)
        adapter = PvlibPvAdapter.from_config(
            **BASE, weather={"kind": "dataframe", "frame": frame}
        )
        intervals = Interval.sequence(datetime(2026, 8, 5, 20, tzinfo=UTC), 3600, 2)

        forecast = adapter.get_forecast("solar_power_kw", intervals)
        assert all(p.quality is Quality.MISSING for p in forecast)


class TestWrapYear:
    def test_wrap_year_maps_horizon_onto_typical_year(self):
        frame = typical_year_frame(2000, 8760)
        adapter = PvlibPvAdapter.from_config(
            **BASE, weather={"kind": "dataframe", "frame": frame, "wrap_year": True}
        )
        intervals = Interval.sequence(datetime(2026, 8, 5, 20, tzinfo=UTC), 3600, 2)

        env = adapter.get_envelope(intervals)
        for point in env:
            assert point.quality is Quality.ESTIMATED
            # Generation is negative under load-positive.
            assert point.q_baseline_kw < 0.0

    def test_wrap_year_preserves_caller_timestamps(self):
        frame = typical_year_frame(2000, 8760)
        weather = DataFrameWeather(frame, wrap_year=True)
        times = pd.DatetimeIndex([datetime(2026, 8, 5, 20, tzinfo=UTC)])

        result = weather.get(times)
        assert list(result.index) == list(times)

    def test_leap_day_does_not_raise(self):
        frame = typical_year_frame(2001, 8760)  # non-leap typical year
        weather = DataFrameWeather(frame, wrap_year=True)
        times = pd.DatetimeIndex([datetime(2024, 2, 29, 12, tzinfo=UTC)])
        assert len(weather.get(times)) == 1


class TestPhysicalPlausibility:
    def test_output_peaks_near_local_solar_noon(self):
        adapter = PvlibPvAdapter.from_config(**BASE, weather={"kind": "clearsky"})
        # Full local day, 07:00 UTC = midnight PDT.
        intervals = Interval.sequence(datetime(2026, 8, 5, 7, tzinfo=UTC), 3600, 24)

        env = adapter.get_envelope(intervals)
        magnitudes = [abs(p.q_baseline_kw) for p in env]
        peak_hour_local = magnitudes.index(max(magnitudes))

        # Peak must land in the local midday band, not at night.
        assert 11 <= peak_hour_local <= 14

    def test_output_never_exceeds_rating(self):
        adapter = PvlibPvAdapter.from_config(**BASE, weather={"kind": "clearsky"})
        intervals = Interval.sequence(datetime(2026, 8, 5, 7, tzinfo=UTC), 3600, 24)

        for point in adapter.get_envelope(intervals):
            assert abs(point.q_baseline_kw) <= 120.0 + 1e-6

    def test_generation_is_negative_under_load_positive(self):
        adapter = PvlibPvAdapter.from_config(**BASE, weather={"kind": "clearsky"})
        intervals = Interval.sequence(datetime(2026, 8, 5, 19, tzinfo=UTC), 3600, 1)

        assert adapter.get_envelope(intervals)[0].q_baseline_kw < 0.0


class TestCloudCoverValidation:
    def test_negative_volatility_rejected(self):
        """rng.normal would otherwise raise far from the misconfiguration."""
        with pytest.raises(AdapterError, match="volatility"):
            CloudCoverWeather(location=None, volatility=-0.5)

    def test_cloud_factor_out_of_range_rejected(self):
        with pytest.raises(AdapterError, match="cloud_factor"):
            CloudCoverWeather(location=None, cloud_factor=1.5)


class TestCampusPvPreset:
    def test_campus_pv_produces_a_usable_envelope(self):
        """The flagship preset raised on every request before the TMY fix."""
        from tent.flexibility import from_config

        adapter = from_config("campus_pv")
        intervals = Interval.sequence(datetime(2026, 8, 5, 7, tzinfo=UTC), 3600, 24)

        env = adapter.get_envelope(intervals)
        assert len(env) == 24
        # Some interval of a summer day must generate.
        assert any(p.q_baseline_kw < 0.0 for p in env)
