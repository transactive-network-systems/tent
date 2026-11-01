import logging

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from tent.utils.helpers import require_aware

from tent.utils.timer import Timer

_log = logging.getLogger(__name__)

@dataclass(frozen=True)
class MarketTimings:
    active: datetime
    negotiation: datetime
    market_lead: datetime
    delivery_lead: datetime
    delivery: datetime
    reconciliation: datetime
    expiration: datetime

    def __post_init__(self):
        for name in ('active', 'negotiation', 'market_lead', 'delivery_lead', 'delivery', 'reconciliation', 'expiration'):
            require_aware(getattr(self, name), f'MarketTimings.{name}')


@dataclass(frozen=True)
class MarketDurations:
    active: timedelta
    negotiation: timedelta
    market_lead: timedelta
    delivery_lead: timedelta
    reconciliation: timedelta
    market_interval: timedelta
    intervals_to_clear: int
    market_clearing_interval: timedelta
    delivery_anchor_time: time | None

    def __post_init__(self):
        # A zero clearing interval divides by zero in get_first_clearing_time and spawns markets endlessly; a zero
        # interval or no intervals makes an empty market. MarketSeries reports the same checks under its own field names.
        for name in ('active', 'negotiation', 'market_lead', 'delivery_lead', 'reconciliation'):
            if getattr(self, name) < timedelta(0):
                raise ValueError(f'MarketDurations.{name} must not be negative; got {getattr(self, name)}')
        if self.market_interval <= timedelta(0):
            raise ValueError(f'MarketDurations.market_interval must be positive; got {self.market_interval}')
        if self.market_clearing_interval <= timedelta(0):
            raise ValueError(f'MarketDurations.market_clearing_interval must be positive; got {self.market_clearing_interval}')
        if self.intervals_to_clear < 1:
            raise ValueError(f'MarketDurations.intervals_to_clear must be at least 1; got {self.intervals_to_clear}')

    @property
    def delivery(self) -> timedelta:
        return self.market_interval * self.intervals_to_clear

    @classmethod
    def new(cls, active, negotiation, market_lead, delivery_lead, reconciliation,
            market_interval, intervals_to_clear: int, market_clearing_interval,
            delivery_anchor_time: time | str | None):
        """Factory that takes either float seconds or timedelta inputs for duration fields, and the anchor time as a
        time or an HH:MM string."""
        def to_td(v):
            return v if isinstance(v, timedelta) else timedelta(seconds=float(v))

        return MarketDurations(
            active=to_td(active),
            negotiation=to_td(negotiation),
            market_lead=to_td(market_lead),
            delivery_lead=to_td(delivery_lead),
            reconciliation=to_td(reconciliation),
            market_interval=to_td(market_interval),
            intervals_to_clear=int(intervals_to_clear),
            market_clearing_interval=to_td(market_clearing_interval),
            delivery_anchor_time=(delivery_anchor_time if isinstance(delivery_anchor_time, time)
                                  else time.fromisoformat(delivery_anchor_time) if delivery_anchor_time else None)
        )

    def get_timings(self, clearing_time: datetime | str) -> MarketTimings:
        delivery_lead_start = require_aware(clearing_time if isinstance(clearing_time, datetime)
                                            else datetime.fromisoformat(clearing_time), 'clearing_time')
        delivery_start = delivery_lead_start + self.delivery_lead
        reconciliation_start = delivery_start + self.delivery
        expiration_start = reconciliation_start + self.reconciliation
        market_lead_start = delivery_lead_start - self.market_lead
        negotiation_start = market_lead_start - self.negotiation
        active_start = negotiation_start - self.active
        return MarketTimings(active_start, negotiation_start, market_lead_start, delivery_lead_start, delivery_start,
                             reconciliation_start, expiration_start)

    def get_first_clearing_time(self) -> datetime:
        """
        The clearing time of a series' first market. Delivery cannot begin before the market has run through its active,
        negotiation, market-lead and delivery-lead periods (the run gap), so the earliest delivery is now plus that gap.
        With a delivery anchor, delivery is the first time at or after that which is the anchor time of day plus a whole
        number of clearing intervals, so the series' markets stay aligned to the anchor (midnight, noon, ...).
        """
        now = Timer.now()
        run_gap = self.delivery_lead + self.market_lead + self.negotiation + self.active
        earliest_delivery = now + run_gap
        if not self.delivery_anchor_time:
            delivery_time = earliest_delivery
        else:
            anchor_today = datetime.combine(now.date(), self.delivery_anchor_time, now.tzinfo)
            delivery_time = earliest_delivery + ((anchor_today - earliest_delivery) % self.market_clearing_interval)
        return delivery_time - self.delivery_lead
