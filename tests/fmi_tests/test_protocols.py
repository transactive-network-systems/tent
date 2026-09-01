"""Protocol and capability-detection tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tent.flexibility import (
    Capabilities,
    DispatchCommand,
    DispatchResponse,
    EnvelopePoint,
    FlexibilityEnvelope,
    Interval,
    Limits,
    ResourceIdentity,
    ResourceState,
    capabilities_of,
)

UTC = timezone.utc


class OnlyFlexibility:
    """The minimum viable market participant."""

    identity = ResourceIdentity("minimal")

    def get_envelope(self, intervals, context=None):
        return FlexibilityEnvelope(
            self.identity,
            tuple(EnvelopePoint(i, 0.0, 1.0, 0.5) for i in intervals),
        )


class ForecastOnly:
    """Like a weather service: predicts, owns nothing."""

    def available_forecasts(self):
        return ("outdoor_temp_c",)

    def get_forecast(self, variable, intervals):
        raise NotImplementedError


class Everything(OnlyFlexibility):
    def get_state(self, timestamp=None):
        return ResourceState(self.identity, timestamp or datetime.now(UTC), {})

    def get_limits(self, timestamp=None):
        return Limits(0.0, 1.0)

    def available_forecasts(self):
        return ()

    def get_forecast(self, variable, intervals):
        raise NotImplementedError

    def dispatch(self, command):
        return DispatchResponse(self.identity, command.interval, accepted=True)

    def release(self, interval):
        return DispatchResponse(self.identity, interval, accepted=True)


class TestCapabilities:
    def test_flexibility_only(self):
        caps = capabilities_of(OnlyFlexibility())
        assert caps.flexibility
        assert caps.biddable
        assert not caps.dispatch
        assert not caps.controllable
        assert not caps.state
        assert not caps.forecast

    def test_forecast_only(self):
        """A weather service is not biddable."""
        caps = capabilities_of(ForecastOnly())
        assert caps.forecast
        assert not caps.flexibility
        assert not caps.biddable

    def test_full_adapter(self):
        caps = capabilities_of(Everything())
        assert all([caps.state, caps.forecast, caps.flexibility, caps.dispatch])

    def test_plain_object_has_nothing(self):
        caps = capabilities_of(object())
        assert caps == Capabilities()

    def test_state_requires_both_methods(self):
        """Partial state support is not state support."""

        class HalfState:
            def get_state(self, timestamp=None):
                ...

        assert not capabilities_of(HalfState()).state

    def test_dispatch_requires_release(self):
        """A deployed agent must be able to hand control back."""

        class NoRelease:
            def dispatch(self, command):
                ...

        assert not capabilities_of(NoRelease()).dispatch


class TestDuckTyping:
    def test_adapter_needs_no_inheritance(self):
        """The reason protocols are structural: an OCHRE or vendor wrapper can
        satisfy the contract without importing this package at all."""
        assert OnlyFlexibility.__bases__ == (object,)
        caps = capabilities_of(OnlyFlexibility())
        assert caps.flexibility

    def test_envelope_from_minimal_adapter_is_valid(self):
        adapter = OnlyFlexibility()
        intervals = Interval.sequence(datetime(2026, 8, 5, 14, tzinfo=UTC), 900, 3)
        envelope = adapter.get_envelope(intervals)
        assert len(envelope) == 3
        envelope.assert_within(Limits(0.0, 1.0))
