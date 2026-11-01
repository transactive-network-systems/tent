"""The iteration's host hooks: _run_market wraps each market's step (events, then the yield) so a host can time or log
markets without copying the iteration body, and _sleep is called once per market."""
import time
from datetime import datetime, timezone
from unittest.mock import patch

from tent.utils.timer import Timer
from tests.integration_tests.conftest import campus_config
from tests.mock_tn import MockTN


class TimingTN(MockTN):
    """A host that times each market's step, as the agent does, by wrapping _run_market."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen: list[str] = []
        self.yields = 0
        self.timings: dict[str, float] = {}

    def _run_market(self, market):
        started = time.perf_counter()
        super()._run_market(market)
        self.seen.append(market.name)
        self.timings[market.name] = time.perf_counter() - started

    def _sleep(self):
        self.yields += 1


def test_a_host_wrapping_run_market_sees_every_market_once_per_iteration_in_series_order():
    t0 = datetime(2017, 7, 1, tzinfo=timezone.utc)
    with patch.object(Timer, 'now', return_value=t0):
        tn = TimingTN()
        tn.configure_node(campus_config())
        expected = [market.name for series in tn.market_series for market in series]
        assert len(expected) == 2 and len(tn.market_series) == 2, 'one market per series at configuration'
        tn.step()
    assert tn.seen == expected, 'every market of every series, once, in series order'
    assert tn.yields == len(expected), 'the yield is taken once per market, inside the wrap'
    assert set(tn.timings) == set(expected) and all(t >= 0 for t in tn.timings.values())


def test_a_second_iteration_sees_the_markets_again_including_any_spawned():
    t0 = datetime(2017, 7, 1, tzinfo=timezone.utc)
    with patch.object(Timer, 'now', return_value=t0):
        tn = TimingTN()
        tn.configure_node(campus_config())
        tn.step()
        first = list(tn.seen)
        tn.seen.clear()
        tn.step()
    assert tn.seen == [market.name for series in tn.market_series for market in series]
    assert len(tn.seen) >= len(first)
