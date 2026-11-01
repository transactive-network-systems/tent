"""Hourly temperature files in the format TemperatureForecastModel reads: a CSV with the columns Timestamp and Value."""
from datetime import datetime, timedelta
from math import pi, sin
from pathlib import Path


def temperature_at(when: datetime) -> float:
    """A deterministic diurnal temperature in degF: 60 at 03:00 and 15:00, 80 at 09:00, 40 at 21:00."""
    return round(60.0 + 20.0 * sin(2 * pi * (when.hour - 3) / 24.0), 2)


def write_weather_csv(path: Path, start: datetime, hours: int, temperature=temperature_at) -> list[tuple[datetime, float]]:
    """Write `hours` hourly rows from `start` (naive timestamps, read in the node's zone) and return (time, value) pairs."""
    rows = [(start + timedelta(hours=h), temperature(start + timedelta(hours=h))) for h in range(hours)]
    lines = ['Timestamp,Value'] + [f'{when.strftime("%Y-%m-%d %H:%M:%S")},{value}' for when, value in rows]
    Path(path).write_text('\n'.join(lines) + '\n')
    return rows
