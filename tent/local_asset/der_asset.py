"""
Copyright (c) 2020, Battelle Memorial Institute
All rights reserved.

PACIFIC NORTHWEST NATIONAL LABORATORY
operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
under Contract DE-AC05-76RL01830
"""

"""A LocalAsset backed by any external physical model.

``DerAsset`` replaces the per-simulator subclass pattern. Instead of writing a
new ``LocalAsset`` subclass for every resource type -- each with physics welded
inside ``schedule_power()`` -- one class consumes the ``der-interface``
contract::

    DerAsset + pvlib adapter        = PV
    DerAsset + OCHRE adapter        = HVAC, water heater, battery
    DerAsset + GridLAB-D adapter    = house, feeder-connected DER
    DerAsset + BMS adapter          = deployed hardware
    DerAsset + replay adapter       = reproducible test case

Compare with ``SolarPvResource``, which hardcodes a cosine curve with sunrise
fixed at 05:30 year-round, or ``SimpleBuilding``, which hardcodes on-peak and
off-peak vertices by hour. Those are configuration differences, not different
kinds of asset.

This class contains **no physics and no market logic**. Its entire job is
marshalling between two vocabularies:

===============================  ==========================================
der-interface                    TENT
===============================  ==========================================
``get_envelope()``               ``scheduled_powers``, ``active_vertices``
``EnvelopePoint.q_baseline_kw``  ``scheduled_powers``
``q_min_kw`` / ``q_max_kw``      ``Vertex`` objects
``DispatchCommand``              cleared ``marginal_prices``
``DispatchResponse``             reconciliation
===============================  ==========================================

Sign convention
---------------
TENT is generation-positive (see ``LocalAsset.cost``). ``der-interface`` is
load-positive, matching GridLAB-D, OCHRE, pvlib, and most metering equipment.
Conversion happens exactly once, in :meth:`_tent_envelope`, and getting it wrong
would silently invert every schedule this asset produces.
"""

import logging

from der_interface import (
    DispatchCommand,
    Interval,
    SignConvention,
    capabilities_of,
)
from der_interface.schema import FlexibilityEnvelope

from tent.containers import Vertex
from tent.local_asset import LocalAsset
from tent.utils.helpers import get_class_from_spec

_log = logging.getLogger(__name__)

#: TENT reports generated power as positive. Every envelope is converted into
#: this convention before it touches a TENT container.
TENT_SIGN = SignConvention.GENERATION_POSITIVE


class DerAsset(LocalAsset):
    """A local asset whose physics live in an external model.

    Args:
        flexibility: A ``der-interface`` object implementing
            ``get_envelope()``. Either a ready instance or a
            ``{'class_name': ..., 'module_name': ..., ...}`` spec for
            configuration-driven setup.
        dispatch: Optional object implementing ``dispatch()`` and ``release()``.
            When omitted the asset is observe-only, which is correct for
            must-take resources -- you cannot command the sun.
        state: Optional object implementing ``get_state()`` and
            ``get_limits()``. Used to validate envelopes against hard physical
            limits and to record actuals for reconciliation.
        price_range: ``(low, high)`` prices in $/kWh used to place the two
            vertices of a flexible interval. ``low`` is the price at which the
            asset accepts ``q_min``; ``high`` is where it accepts ``q_max``.
            Ignored for must-take resources.
        enforce_limits: When True (default) and a state provider is available,
            verify each envelope against physical limits before bidding. A
            market must never be able to widen physics.
        actuate_from_schedule: When True, dispatch the scheduled power for the
            delivery interval during ``actuate()``.
    """

    def __init__(self,
                 flexibility,
                 dispatch=None,
                 state=None,
                 price_range=(0.0, 0.10),
                 enforce_limits=True,
                 actuate_from_schedule=True,
                 *args, **kwargs):
        super(DerAsset, self).__init__(*args, **kwargs)

        self.flexibility = self._resolve(flexibility, 'flexibility')
        self.dispatch_target = self._resolve(dispatch, 'dispatch')
        self.state_provider = self._resolve(state, 'state')

        low, high = (float(p) for p in price_range)
        if low >= high:
            raise ValueError(
                f'{self.name}: price_range must be (low, high) with low < high, '
                f'got ({low}, {high})'
            )
        self.low_price = low
        self.high_price = high
        self.enforce_limits = bool(enforce_limits)
        self.actuate_from_schedule = bool(actuate_from_schedule)

        # A single provider commonly implements several protocols; when the
        # caller supplies only `flexibility`, reuse it for the others.
        capabilities = capabilities_of(self.flexibility)
        if self.dispatch_target is None and capabilities.dispatch:
            self.dispatch_target = self.flexibility
        if self.state_provider is None and capabilities.state:
            self.state_provider = self.flexibility

        if not capabilities_of(self.flexibility).flexibility:
            raise ValueError(
                f'{self.name}: flexibility provider {type(self.flexibility).__name__} '
                f'has no get_envelope() method, so it cannot be bid into a market.'
            )

        # Dynamic state; not configured.
        self._envelope: FlexibilityEnvelope | None = None
        self._interval_map: dict = {}
        self.dispatch_responses: list = []

        _log.debug(
            f'{self.name} configured with flexibility='
            f'{type(self.flexibility).__name__}, dispatch='
            f'{type(self.dispatch_target).__name__ if self.dispatch_target else None}'
        )

    @staticmethod
    def _resolve(spec, label):
        """Accept either a live object or a class spec from JSON configuration."""
        if spec is None or not isinstance(spec, dict):
            return spec
        spec = dict(spec)
        adapter_class = get_class_from_spec(spec, default_module_prefix='der_interface.adapters')
        factory = spec.pop('factory', None)
        builder = getattr(adapter_class, factory) if factory else adapter_class
        return builder(**spec)

    # ------------------------------------------------------------------
    # Translation
    # ------------------------------------------------------------------

    @staticmethod
    def _to_interval(time_interval) -> Interval:
        """Convert a TENT ``TimeInterval`` into a der-interface ``Interval``."""
        return Interval(
            start=time_interval.start_time,
            duration_s=int(time_interval.duration.total_seconds()),
        )

    def _tent_envelope(self, market) -> FlexibilityEnvelope:
        """Fetch the envelope and convert it into TENT's sign convention.

        Also builds a map from der-interface intervals back to the TENT
        ``TimeInterval`` objects, since TENT containers are keyed by identity.
        """
        time_intervals = list(market.time_intervals)
        intervals = [self._to_interval(ti) for ti in time_intervals]

        envelope = self.flexibility.get_envelope(intervals)

        if len(envelope) != len(intervals):
            raise RuntimeError(
                f'{self.name}: adapter returned {len(envelope)} envelope points '
                f'for {len(intervals)} requested intervals. Adapters must return '
                f'one point per interval, in order.'
            )

        if self.enforce_limits and self.state_provider is not None:
            try:
                limits = self.state_provider.get_limits()
            except Exception as e:  # noqa: BLE001 - limits are advisory here
                _log.warning(f'{self.name}: could not read limits ({e}); skipping check.')
            else:
                if limits.sign is envelope.sign:
                    envelope.assert_within(limits)

        # The one conversion that matters. Note that negating an interval's
        # endpoints reverses their order, so q_min and q_max swap.
        envelope = envelope.in_convention(TENT_SIGN)

        self._envelope = envelope
        self._interval_map = {
            point.interval: ti for point, ti in zip(envelope, time_intervals)
        }
        return envelope

    # ------------------------------------------------------------------
    # LocalAsset hooks
    # ------------------------------------------------------------------

    def schedule_power(self, market):
        """Schedule the baseline power the external model reports.

        The baseline is the counterfactual -- what the resource would do absent
        market intervention. It is the natural starting schedule and the
        reference a settlement process measures against.
        """
        envelope = self._tent_envelope(market)

        for point in envelope:
            time_interval = self._interval_map[point.interval]
            self.scheduled_powers.update(time_interval, point.q_baseline_kw)
            self.engagement_schedule.update(time_interval, point.flexible)

        self.schedule_calculated = True
        _log.debug(f'{self.name} scheduled_powers: {self.scheduled_powers.log_report}')

    def update_vertices(self, market):
        """Turn the envelope into supply/demand vertices.

        A must-take interval (``q_min == q_max``) yields one vertex at infinite
        price -- take it at any price, there is nothing to negotiate. A flexible
        interval yields two vertices spanning ``price_range``, so the market can
        interpolate an operating point.
        """
        if self._envelope is None:
            self._tent_envelope(market)

        for point in self._envelope:
            time_interval = self._interval_map[point.interval]

            if not point.flexible:
                vertices = [Vertex(price=float('inf'), cost=0.0,
                                   quantity=point.q_baseline_kw)]
            else:
                vertices = [
                    Vertex(price=self.low_price, cost=self.cost(point.q_min_kw),
                           quantity=point.q_min_kw),
                    Vertex(price=self.high_price, cost=self.cost(point.q_max_kw),
                           quantity=point.q_max_kw),
                ]

            self.active_vertices.update(time_interval, vertices, replace=True)

        _log.debug(f'{self.name} active_vertices: {self.active_vertices.log_report}')

    # ------------------------------------------------------------------
    # Actuation
    # ------------------------------------------------------------------

    def actuate(self, mkt):
        """Command the external model to follow the cleared schedule.

        Fills in the hook that ``LocalAsset.actuate`` leaves as ``pass``. Called
        by ``Market._actuate_assets`` during ``DeliveryLead``.

        Every response is retained in :attr:`dispatch_responses` so a
        reconciliation market can compare committed against delivered power --
        the input ``ReconciliationMarket.get_actual_power`` currently lacks.
        """
        if self.dispatch_target is None:
            return
        if not self.actuate_from_schedule:
            return

        for time_interval in mkt.time_intervals:
            scheduled = self.scheduled_powers.get_by_interval(time_interval)
            if scheduled is None:
                continue

            interval = self._to_interval(time_interval)
            command = DispatchCommand.for_power(
                identity=self.flexibility.identity,
                interval=interval,
                power_kw=scheduled.value,
                sign=TENT_SIGN,
                source=f'tent:{mkt.name}',
            )

            try:
                response = self.dispatch_target.dispatch(command)
            except Exception as e:  # noqa: BLE001 - one bad asset must not halt the market
                _log.error(f'{self.name}: dispatch failed for {time_interval.name}: {e}')
                continue

            self.dispatch_responses.append(response)

            if response.violations:
                _log.warning(
                    f'{self.name} {time_interval.name}: dispatch violations: '
                    f'{"; ".join(response.violations)}'
                )

            deviation = response.deviation_kw(command)
            if deviation is not None and abs(deviation) > 1e-6:
                _log.debug(
                    f'{self.name} {time_interval.name}: commanded '
                    f'{scheduled.value:.4f} kW, delivered '
                    f'{response.actual_power_kw:.4f} kW '
                    f'(deviation {deviation:+.4f} kW)'
                )

    def release(self, mkt):
        """Return the resource to autonomous operation.

        Deployed systems need this: a building whose setpoints stay pinned by a
        crashed agent is a real failure mode.
        """
        if self.dispatch_target is None:
            return
        for time_interval in mkt.time_intervals:
            try:
                self.dispatch_target.release(self._to_interval(time_interval))
            except Exception as e:  # noqa: BLE001
                _log.error(f'{self.name}: release failed for {time_interval.name}: {e}')

    # ------------------------------------------------------------------
    # Reconciliation support
    # ------------------------------------------------------------------

    def get_actual_power(self, time_interval):
        """Measured power for an interval, or ``None`` if unavailable.

        Prefers a live state reading; falls back to the recorded dispatch
        response. This is what a reconciliation market needs in order to settle
        committed versus delivered energy.

        State providers report in der-interface's canonical load-positive
        convention, so the reading is negated on the way into TENT.
        """
        if self.state_provider is not None:
            try:
                state = self.state_provider.get_state(time_interval.start_time)
            except Exception as e:  # noqa: BLE001
                _log.debug(f'{self.name}: state read failed ({e}); trying dispatch log.')
            else:
                power = state.power_kw
                if power is not None and state.quality.usable:
                    return TENT_SIGN.from_canonical(power)

        interval = self._to_interval(time_interval)
        for response in reversed(self.dispatch_responses):
            if response.interval == interval:
                return response.actual_power_kw
        return None

    # ------------------------------------------------------------------
    # Archiving
    # ------------------------------------------------------------------

    @property
    def table_name(self):
        return 'der_asset'

    def get_record(self) -> dict:
        record = super(DerAsset, self).get_record()
        record.update({
            'asset_id': self.id,
            'adapter_class': type(self.flexibility).__name__,
            'resource_id': getattr(self.flexibility.identity, 'resource_id', ''),
            'low_price': self.low_price,
            'high_price': self.high_price,
            'dispatchable': self.dispatch_target is not None,
        })
        return record

    @classmethod
    def get_archive_definitions(cls) -> list[dict]:
        definitions = super(DerAsset, cls).get_archive_definitions()
        definitions.extend([
            {
                "name": "der_asset",
                "inherits": "local_asset",
                "columns": [
                    {"name": "asset_id", "foreign_key": "local_asset.id",
                     "kwargs": {"primary_key": True}},
                    {"name": "adapter_class", "type": "String"},
                    {"name": "resource_id", "type": "String"},
                    {"name": "low_price", "type": "Float"},
                    {"name": "high_price", "type": "Float"},
                    {"name": "dispatchable", "type": "Boolean"},
                ]
            }
        ])
        return definitions
