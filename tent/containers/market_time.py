from dataclasses import dataclass
from datetime import datetime, time, timedelta

from tent.utils.timer import Timer


@dataclass(frozen=True)
class MarketTimings:
    active: datetime
    negotiation: datetime
    market_lead: datetime
    delivery_lead: datetime
    delivery: datetime
    reconciliation: datetime
    expiration: datetime


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

    @property
    def delivery(self) -> timedelta:
        return self.market_interval * self.intervals_to_clear

    @classmethod
    def new(cls, active: float, negotiation: float, market_lead: float, delivery_lead: float, reconciliation: float,
            market_interval: float, intervals_to_clear: int, market_clearing_interval: float,
            delivery_anchor_time: str | None):
        return MarketDurations(
            active=timedelta(seconds=active),
            negotiation=timedelta(seconds=negotiation),
            market_lead=timedelta(seconds=market_lead),
            delivery_lead=timedelta(seconds=delivery_lead),
            reconciliation=timedelta(seconds=reconciliation),
            market_interval=timedelta(seconds=market_interval),
            intervals_to_clear=int(intervals_to_clear),
            market_clearing_interval=timedelta(seconds=market_clearing_interval),
            delivery_anchor_time=time.fromisoformat(delivery_anchor_time) if delivery_anchor_time else None
        )

    def get_timings(self, clearing_time: datetime | str) -> MarketTimings:
        delivery_lead_start = (clearing_time if isinstance(clearing_time, datetime)
                               else datetime.fromisoformat(clearing_time))
        delivery_start = delivery_lead_start + self.delivery_lead
        reconciliation_start = delivery_start + self.delivery
        expiration_start = reconciliation_start + self.reconciliation
        market_lead_start = delivery_lead_start - self.market_lead
        negotiation_start = market_lead_start - self.negotiation
        active_start = negotiation_start - self.active
        return MarketTimings(active_start, negotiation_start, market_lead_start, delivery_lead_start, delivery_start,
                             reconciliation_start, expiration_start)

    def get_first_clearing_time(self):
        # 240221DJH: TODO: Ensure that this base method begins delivery on major temporal transitions.
        now = Timer.now()
        run_gap = self.delivery_lead + self.market_lead + self.negotiation + self.active
        if not self.delivery_anchor_time:
            delivery_time = now + run_gap
        else:
            delivery_anchor_today = datetime.combine(now.date(), self.delivery_anchor_time)
            delivery_time = now + ((delivery_anchor_today - now - run_gap) % self.market_clearing_interval)
        return delivery_time - self.delivery_lead
