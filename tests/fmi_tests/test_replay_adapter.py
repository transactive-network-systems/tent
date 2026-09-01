"""Replay adapter tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from der_interface import (
    AdapterError,
    CapabilityError,
    DispatchCommand,
    Interval,
    Quality,
    ResourceIdentity,
    ResourceKind,
    capabilities_of,
)
from der_interface.adapters import ReplayAdapter

UTC = timezone.utc

CSV = """start,duration_s,q_min_kw,q_max_kw,q_baseline_kw
2026-08-05T14:00:00+00:00,900,0.0,4.2,2.8
2026-08-05T14:15:00+00:00,900,0.0,4.2,2.9
2026-08-05T14:30:00+00:00,900,0.0,4.0,3.1
2026-08-05T14:45:00+00:00,900,0.0,3.8,3.0
"""


@pytest.fixture
def csv_path(tmp_path):
    path = tmp_path / "house_12_hvac.csv"
    path.write_text(CSV)
    return path


@pytest.fixture
def adapter(csv_path):
    return ReplayAdapter.from_csv(csv_path)


class TestLoading:
    def test_from_csv_infers_identity_from_filename(self, adapter):
        assert adapter.identity.resource_id == "house_12_hvac"
        assert "replay" in adapter.identity.source

    def test_from_rows(self):
        adapter = ReplayAdapter.from_rows(
            ResourceIdentity("x", ResourceKind.LOAD),
            [
                {
                    "start": datetime(2026, 8, 5, 14, tzinfo=UTC),
                    "duration_s": 900,
                    "q_min_kw": 0.0,
                    "q_max_kw": 4.0,
                    "q_baseline_kw": 2.0,
                }
            ],
        )
        assert len(adapter.get_envelope([Interval(datetime(2026, 8, 5, 14, tzinfo=UTC), 900)])) == 1

    def test_missing_file(self, tmp_path):
        with pytest.raises(AdapterError, match="not found"):
            ReplayAdapter.from_csv(tmp_path / "nope.csv")

    def test_missing_column_names_it(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text("start,duration_s\n2026-08-05T14:00:00+00:00,900\n")
        with pytest.raises(AdapterError, match="missing required column"):
            ReplayAdapter.from_csv(path)

    def test_empty_file_rejected(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("start,duration_s,q_min_kw,q_max_kw,q_baseline_kw\n")
        with pytest.raises(AdapterError, match="no data rows"):
            ReplayAdapter.from_csv(path)

    def test_naive_timestamp_rejected_with_line_number(self, tmp_path):
        path = tmp_path / "naive.csv"
        path.write_text(
            "start,duration_s,q_min_kw,q_max_kw,q_baseline_kw\n"
            "2026-08-05T14:00:00,900,0.0,4.2,2.8\n"
        )
        with pytest.raises(AdapterError, match="line 2"):
            ReplayAdapter.from_csv(path)

    def test_bad_baseline_reports_line_number(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text(
            "start,duration_s,q_min_kw,q_max_kw,q_baseline_kw\n"
            "2026-08-05T14:00:00+00:00,900,0.0,4.2,2.8\n"
            "2026-08-05T14:15:00+00:00,900,0.0,4.2,99.0\n"
        )
        with pytest.raises(AdapterError, match="line 3"):
            ReplayAdapter.from_csv(path)

    def test_rows_sorted_regardless_of_input_order(self):
        t0 = datetime(2026, 8, 5, 14, tzinfo=UTC)
        rows = [
            {"start": t0 + timedelta(minutes=15), "duration_s": 900,
             "q_min_kw": 0.0, "q_max_kw": 4.0, "q_baseline_kw": 2.0},
            {"start": t0, "duration_s": 900,
             "q_min_kw": 0.0, "q_max_kw": 4.0, "q_baseline_kw": 1.0},
        ]
        adapter = ReplayAdapter.from_rows(ResourceIdentity("x"), rows)
        envelope = adapter.get_envelope(Interval.sequence(t0, 900, 2))
        assert envelope[0].q_baseline_kw == 1.0


class TestCapabilities:
    def test_supports_everything(self, adapter):
        caps = capabilities_of(adapter)
        assert caps.state and caps.forecast and caps.flexibility and caps.dispatch
        assert caps.biddable


class TestEnvelope:
    def test_exact_match(self, adapter):
        intervals = Interval.sequence(datetime(2026, 8, 5, 14, tzinfo=UTC), 900, 4)
        envelope = adapter.get_envelope(intervals)
        assert len(envelope) == 4
        assert [p.q_baseline_kw for p in envelope] == pytest.approx([2.8, 2.9, 3.1, 3.0])

    def test_one_point_per_requested_interval(self, adapter):
        """Callers must be able to zip envelope points with their intervals."""
        intervals = Interval.sequence(datetime(2026, 8, 5, 14, tzinfo=UTC), 900, 3)
        envelope = adapter.get_envelope(intervals)
        assert envelope.intervals == tuple(intervals)

    def test_requires_at_least_one_interval(self, adapter):
        with pytest.raises(AdapterError, match="at least one interval"):
            adapter.get_envelope([])

    def test_strict_mode_raises_outside_range(self, adapter):
        far = Interval(datetime(2026, 9, 1, tzinfo=UTC), 900)
        with pytest.raises(AdapterError, match="no replay data covers"):
            adapter.get_envelope([far])

    def test_error_reports_recorded_range(self, adapter):
        far = Interval(datetime(2026, 9, 1, tzinfo=UTC), 900)
        with pytest.raises(AdapterError, match="Recorded range is"):
            adapter.get_envelope([far])

    def test_non_strict_falls_back(self, csv_path):
        adapter = ReplayAdapter.from_csv(csv_path, strict=False)
        far = Interval(datetime(2026, 9, 1, tzinfo=UTC), 900)
        envelope = adapter.get_envelope([far])
        # Past the end of the recorded data this is hold-last-value, not an
        # interpolation within it. STALE keeps the two distinguishable.
        assert envelope[0].quality is Quality.STALE

    def test_non_strict_gap_within_range_is_estimated(self, csv_path):
        """A gap inside the recorded range is still an interpolation."""
        adapter = ReplayAdapter.from_csv(csv_path, strict=False)
        inside = Interval(datetime(2026, 8, 5, 14, 5, tzinfo=UTC), 300)
        assert adapter.get_envelope([inside])[0].quality is Quality.ESTIMATED

    def test_sub_interval_marked_estimated(self, adapter):
        """A 5-minute request against 15-minute data is an estimate, not a fact."""
        interval = Interval(datetime(2026, 8, 5, 14, 5, tzinfo=UTC), 300)
        point = adapter.get_envelope([interval])[0]
        assert point.quality is Quality.ESTIMATED
        assert point.interval == interval

    def test_limits_derived_from_data(self, adapter):
        limits = adapter.get_limits()
        assert limits.min_power_kw == 0.0
        assert limits.max_power_kw == 4.2

    def test_envelope_within_derived_limits(self, adapter):
        intervals = Interval.sequence(datetime(2026, 8, 5, 14, tzinfo=UTC), 900, 4)
        adapter.get_envelope(intervals).assert_within(adapter.get_limits())


class TestState:
    def test_state_from_baseline(self, adapter):
        state = adapter.get_state(datetime(2026, 8, 5, 14, 20, tzinfo=UTC))
        assert state.power_kw == 2.9
        assert state.quality is Quality.ESTIMATED

    def test_state_before_data_is_missing(self, adapter):
        """Not zero. Missing."""
        state = adapter.get_state(datetime(2026, 8, 1, tzinfo=UTC))
        assert state.quality is Quality.MISSING
        assert state.power_kw is None

    def test_state_after_data_is_stale(self, adapter):
        state = adapter.get_state(datetime(2026, 8, 6, tzinfo=UTC))
        assert state.quality is Quality.STALE


class TestForecast:
    def test_available(self, adapter):
        assert "baseline_power_kw" in adapter.available_forecasts()

    def test_baseline_forecast(self, adapter):
        intervals = Interval.sequence(datetime(2026, 8, 5, 14, tzinfo=UTC), 900, 4)
        forecast = adapter.get_forecast("baseline_power_kw", intervals)
        assert forecast.values() == pytest.approx((2.8, 2.9, 3.1, 3.0))

    def test_unknown_variable_raises(self, adapter):
        intervals = Interval.sequence(datetime(2026, 8, 5, 14, tzinfo=UTC), 900, 1)
        with pytest.raises(CapabilityError, match="cannot forecast"):
            adapter.get_forecast("soc", intervals)


class TestDispatch:
    def test_in_range_command_passes_through(self, adapter):
        interval = Interval(datetime(2026, 8, 5, 14, tzinfo=UTC), 900)
        response = adapter.dispatch(
            DispatchCommand.for_power(adapter.identity, interval, 3.0)
        )
        assert response.accepted
        assert response.actual_power_kw == 3.0
        assert response.violations == ()

    def test_out_of_range_command_is_clamped_and_reported(self, adapter):
        """A market result can never widen physics."""
        interval = Interval(datetime(2026, 8, 5, 14, tzinfo=UTC), 900)
        response = adapter.dispatch(
            DispatchCommand.for_power(adapter.identity, interval, 99.0)
        )
        assert response.accepted
        assert response.actual_power_kw == 4.2
        assert len(response.violations) == 1
        assert "clamped" in response.violations[0]

    def test_deviation_computable(self, adapter):
        interval = Interval(datetime(2026, 8, 5, 14, tzinfo=UTC), 900)
        command = DispatchCommand.for_power(adapter.identity, interval, 99.0)
        response = adapter.dispatch(command)
        assert response.deviation_kw(command) == pytest.approx(4.2 - 99.0)

    def test_commands_logged(self, adapter):
        interval = Interval(datetime(2026, 8, 5, 14, tzinfo=UTC), 900)
        adapter.dispatch(DispatchCommand.for_power(adapter.identity, interval, 1.0))
        adapter.dispatch(DispatchCommand.for_power(adapter.identity, interval, 2.0))
        assert len(adapter.dispatch_log) == 2

    def test_release(self, adapter):
        interval = Interval(datetime(2026, 8, 5, 14, tzinfo=UTC), 900)
        assert adapter.release(interval).accepted
