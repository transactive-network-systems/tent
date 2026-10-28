import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock

from tent.market.real_time_auction import RealTimeAuction
from tent.market.auction import Auction
from tent.enumerations import MarketState
from tent.containers import MarketTimings


def timings():
    base = datetime(2025,1,1,10,0,0)
    return MarketTimings(
        active=base,
        negotiation=base+timedelta(hours=1),
        market_lead=base+timedelta(hours=1, minutes=30),
        delivery_lead=base+timedelta(hours=1, minutes=45),
        delivery=base+timedelta(hours=2),
        reconciliation=base+timedelta(hours=3),
        expiration=base+timedelta(hours=4)
    )


def build_rta(tn):
    series = Mock(); series.name = "TestSeries"; series.description = ""; series.intervals_to_clear=1; series.duration=Mock(); series.duration.market_interval=timedelta(hours=1)
    return RealTimeAuction(transactive_node=tn, market_series=series, initial_market_state=MarketState.Inactive,
                           transition_times=timings())


def test_real_time_auction_inherits_auction():
    tn = Mock(); tn.local_assets=[]; tn.neighbors=[]
    rta = build_rta(tn)
    assert isinstance(rta, Auction)


def test_transition_from_delivery_lead_to_delivery_updates_assets_and_model():
    tn = Mock(); asset=Mock(); tn.local_assets=[asset]; tn.publish=Mock()
    rta = build_rta(tn)
    rta.price_model = Mock(); rta.marginal_prices = []
    rta.transition_from_delivery_lead_to_delivery()
    asset.actuate.assert_called()
