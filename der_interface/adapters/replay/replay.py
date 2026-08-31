"""Replay adapter: envelopes from a CSV file or in-memory rows.

Small, but load-bearing. It provides:

- A dependency-free adapter so the test suite runs anywhere.
- Deterministic reproduction of a case without launching a simulator.
- A reference implementation for anyone writing a new adapter.
- A way to record a simulator run once and replay it many times, which makes
  market-side debugging far faster than re-running co-simulation.

CSV format
----------
::

    start,duration_s,q_min_kw,q_max_kw,q_baseline_kw
    2026-08-05T14:00:00-07:00,900,0.0,4.2,2.8
    2026-08-05T14:15:00-07:00,900,0.0,4.2,2.9

``sigma_kw`` and ``quality`` columns are optional. ``start`` must be an
ISO-8601 timestamp *with* an offset -- naive timestamps are rejected rather than
assumed.
"""

from __future__ import annotations

import csv
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ...conventions import Quality, SignConvention
from ...errors import AdapterError, CapabilityError, SchemaError
from ...schema import (
    DispatchCommand,
    DispatchResponse,
    EnvelopePoint,
    FlexibilityEnvelope,
    Forecast,
    ForecastPoint,
    Interval,
    Limits,
    ResourceIdentity,
    ResourceKind,
    ResourceState,
)

__all__ = ["ReplayAdapter", "ReplayRow"]

_REQUIRED = ("start", "duration_s", "q_min_kw", "q_max_kw", "q_baseline_kw")


class ReplayRow:
    """One parsed row. Kept separate from :class:`EnvelopePoint` so parsing
    errors can name the source line number."""

    __slots__ = ("point", "line")

    def __init__(self, point: EnvelopePoint, line: int) -> None:
        self.point = point
        self.line = line


class ReplayAdapter:
    """Serves envelopes, state, and forecasts from recorded data.

    Satisfies :class:`~der_interface.protocols.FlexibilityProvider`,
    :class:`~der_interface.protocols.StateProvider`, and
    :class:`~der_interface.protocols.ForecastProvider`. It also accepts dispatch,
    but only records commands -- recorded data cannot physically respond, so
    ``actual`` echoes the clamped target and the response is marked
    :attr:`~der_interface.conventions.Quality.ESTIMATED`.
    """

    def __init__(
        self,
        identity: ResourceIdentity,
        points: Sequence[EnvelopePoint],
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        limits: Limits | None = None,
        strict: bool = True,
    ) -> None:
        """
        Args:
            identity: Who this resource is.
            points: Recorded envelope points, sorted by start time.
            sign: Convention the recorded values are already in.
            limits: Hard bounds. Derived from the data when omitted.
            strict: If True, requesting an interval with no recorded data raises.
                If False, such intervals fall back to the nearest earlier point.
        """
        if not points:
            raise AdapterError(f"{identity.resource_id!r}: no replay points supplied")

        ordered = tuple(sorted(points, key=lambda p: p.interval.start))
        self._identity = identity
        self._points = ordered
        self._sign = sign
        self._strict = bool(strict)
        self._starts = [p.interval.start for p in ordered]
        self._dispatch_log: list[tuple[DispatchCommand, DispatchResponse]] = []

        self._limits = limits or Limits(
            min_power_kw=min(p.q_min_kw for p in ordered),
            max_power_kw=max(p.q_max_kw for p in ordered),
            sign=sign,
        )

    # -- construction ------------------------------------------------------

    @classmethod
    def from_csv(
        cls,
        path: str | Path,
        identity: ResourceIdentity | None = None,
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> "ReplayAdapter":
        """Load from a CSV file.

        When *identity* is omitted the file stem becomes the resource id.
        """
        path = Path(path)
        if not path.exists():
            raise AdapterError(f"replay file not found: {path}")

        identity = identity or ResourceIdentity(
            resource_id=path.stem, kind=ResourceKind.UNKNOWN, source=f"replay:{path.name}"
        )

        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            missing = [c for c in _REQUIRED if c not in (reader.fieldnames or ())]
            if missing:
                raise AdapterError(
                    f"{path}: missing required column(s) {missing}; "
                    f"found {list(reader.fieldnames or [])}"
                )
            rows = cls._parse_rows(reader, str(path))

        return cls(identity, [r.point for r in rows], sign=sign, **kwargs)

    @classmethod
    def from_rows(
        cls,
        identity: ResourceIdentity,
        rows: Iterable[Mapping[str, Any]],
        sign: SignConvention = SignConvention.LOAD_POSITIVE,
        **kwargs: Any,
    ) -> "ReplayAdapter":
        """Load from an iterable of mappings, e.g. parsed JSON."""
        parsed = cls._parse_rows(rows, f"<rows for {identity.resource_id}>")
        return cls(identity, [r.point for r in parsed], sign=sign, **kwargs)

    @staticmethod
    def _parse_rows(rows: Iterable[Mapping[str, Any]], origin: str) -> list[ReplayRow]:
        out: list[ReplayRow] = []
        for lineno, row in enumerate(rows, start=2):  # line 1 is the header
            try:
                start = row["start"]
                if isinstance(start, str):
                    start = datetime.fromisoformat(start)
                quality = row.get("quality") or "valid"
                point = EnvelopePoint(
                    interval=Interval(start, int(float(row["duration_s"]))),
                    q_min_kw=float(row["q_min_kw"]),
                    q_max_kw=float(row["q_max_kw"]),
                    q_baseline_kw=float(row["q_baseline_kw"]),
                    sigma_kw=float(row.get("sigma_kw") or 0.0),
                    quality=Quality(quality) if isinstance(quality, str) else quality,
                )
            except SchemaError as exc:
                raise AdapterError(f"{origin} line {lineno}: {exc}") from exc
            except (KeyError, TypeError, ValueError) as exc:
                raise AdapterError(f"{origin} line {lineno}: could not parse row: {exc}") from exc
            out.append(ReplayRow(point, lineno))

        if not out:
            raise AdapterError(f"{origin}: file contained no data rows")
        return out

    # -- identity ----------------------------------------------------------

    @property
    def identity(self) -> ResourceIdentity:
        return self._identity

    @property
    def dispatch_log(self) -> tuple[tuple[DispatchCommand, DispatchResponse], ...]:
        """Every command received, for test assertions."""
        return tuple(self._dispatch_log)

    # -- FlexibilityProvider ----------------------------------------------

    def get_envelope(
        self,
        intervals: Sequence[Interval],
        context: Mapping[str, Any] | None = None,
    ) -> FlexibilityEnvelope:
        if not intervals:
            raise AdapterError("get_envelope requires at least one interval")
        points = tuple(self._point_for(i) for i in intervals)
        return FlexibilityEnvelope(self._identity, points, sign=self._sign)

    def _point_for(self, interval: Interval) -> EnvelopePoint:
        """Find the recorded point covering *interval*.

        Exact start match wins. Otherwise the point whose interval contains the
        requested start is used, and the result is re-stamped with the requested
        interval so callers can zip safely.
        """
        index = bisect_right(self._starts, interval.start) - 1
        if index >= 0:
            candidate = self._points[index]
            if candidate.interval.start == interval.start:
                if candidate.interval.duration_s == interval.duration_s:
                    return candidate
                return self._restamp(candidate, interval, Quality.ESTIMATED)
            if candidate.interval.contains(interval.start):
                return self._restamp(candidate, interval, Quality.ESTIMATED)

        if self._strict:
            raise AdapterError(
                f"{self._identity.resource_id!r}: no replay data covers {interval}. "
                f"Recorded range is {self._points[0].interval.start.isoformat()} to "
                f"{self._points[-1].interval.end.isoformat()}. "
                f"Pass strict=False to fall back to the nearest earlier point."
            )

        fallback = self._points[max(index, 0)]
        return self._restamp(fallback, interval, Quality.ESTIMATED)

    @staticmethod
    def _restamp(
        point: EnvelopePoint, interval: Interval, quality: Quality
    ) -> EnvelopePoint:
        return EnvelopePoint(
            interval=interval,
            q_min_kw=point.q_min_kw,
            q_max_kw=point.q_max_kw,
            q_baseline_kw=point.q_baseline_kw,
            sigma_kw=point.sigma_kw,
            quality=quality,
        )

    # -- StateProvider -----------------------------------------------------

    def get_state(self, timestamp: datetime | None = None) -> ResourceState:
        """State inferred from the recorded baseline.

        Recorded data has no true instantaneous state, so ``power_kw`` is the
        baseline of the covering interval and quality is
        :attr:`~der_interface.conventions.Quality.ESTIMATED`.
        """
        moment = timestamp or self._points[0].interval.start
        index = bisect_right(self._starts, moment) - 1
        if index < 0:
            return ResourceState(
                self._identity, moment, {}, quality=Quality.MISSING
            )
        point = self._points[index]
        quality = Quality.ESTIMATED if point.interval.contains(moment) else Quality.STALE
        return ResourceState(
            identity=self._identity,
            timestamp=moment,
            values={
                "power_kw": point.q_baseline_kw,
                "q_min_kw": point.q_min_kw,
                "q_max_kw": point.q_max_kw,
            },
            quality=quality,
        )

    def get_limits(self, timestamp: datetime | None = None) -> Limits:
        return self._limits

    # -- ForecastProvider --------------------------------------------------

    def available_forecasts(self) -> tuple[str, ...]:
        return ("baseline_power_kw", "q_min_kw", "q_max_kw")

    def get_forecast(self, variable: str, intervals: Sequence[Interval]) -> Forecast:
        attr = {
            "baseline_power_kw": "q_baseline_kw",
            "q_min_kw": "q_min_kw",
            "q_max_kw": "q_max_kw",
        }.get(variable)
        if attr is None:
            raise CapabilityError(
                f"{self._identity.resource_id!r} cannot forecast {variable!r}; "
                f"available: {self.available_forecasts()}"
            )
        points = tuple(
            ForecastPoint(
                interval=i,
                value=getattr(p, attr),
                sigma=p.sigma_kw,
                quality=p.quality,
            )
            for i, p in ((i, self._point_for(i)) for i in intervals)
        )
        return Forecast(variable, points, source=self._identity.source or "replay")

    # -- DispatchTarget ----------------------------------------------------

    def dispatch(self, command: DispatchCommand) -> DispatchResponse:
        """Record a command. Recorded data cannot physically respond.

        The target is clamped to the recorded envelope, and any clamping is
        reported in ``violations`` -- the same behaviour a real adapter must have.
        """
        point = self._point_for(command.interval)
        requested = command.power_kw
        violations: list[str] = []

        if requested is None:
            actual: Mapping[str, Any] = dict(command.target)
            violations.append("no power_kw in target; replay adapter recorded it verbatim")
        else:
            clamped = point.clamp(requested)
            if abs(clamped - requested) > 1e-9:
                violations.append(
                    f"requested {requested:.4f} kW clamped to {clamped:.4f} kW "
                    f"by envelope [{point.q_min_kw}, {point.q_max_kw}]"
                )
            actual = {"power_kw": clamped}

        response = DispatchResponse(
            identity=self._identity,
            interval=command.interval,
            accepted=True,
            actual=actual,
            state=self.get_state(command.interval.start),
            violations=tuple(violations),
            message="replay adapter: command recorded, no physical response",
        )
        self._dispatch_log.append((command, response))
        return response

    def release(self, interval: Interval) -> DispatchResponse:
        return DispatchResponse(
            identity=self._identity,
            interval=interval,
            accepted=True,
            actual={},
            message="replay adapter: nothing to release",
        )

    def __repr__(self) -> str:
        return (
            f"ReplayAdapter({self._identity.resource_id!r}, "
            f"{len(self._points)} points, "
            f"{self._points[0].interval.start.isoformat()} .. "
            f"{self._points[-1].interval.end.isoformat()})"
        )
