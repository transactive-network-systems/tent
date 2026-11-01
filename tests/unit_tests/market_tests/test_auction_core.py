import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from tent.market import Market
from tent.market.auction import Auction
from tent.market.consensus_market import ConsensusMarket
from tent.enumerations import MarketState, Direction, MarketTypes
from tent.containers import MarketTimings


def make_timings():
    base = datetime(2025,1,1,10,0,0, tzinfo=timezone.utc)
    return MarketTimings(
        active=base,
        negotiation=base+timedelta(hours=1),
        market_lead=base+timedelta(hours=1, minutes=30),
        delivery_lead=base+timedelta(hours=1, minutes=45),
        delivery=base+timedelta(hours=2),
        reconciliation=base+timedelta(hours=3),
        expiration=base+timedelta(hours=4)
    )


def build_auction(tn):
    series = Mock(); series.name = "TestSeries"; series.description = ""
    series.intervals_to_clear = 1
    series.duration = Mock(); series.duration.market_interval = timedelta(hours=1)
    timings = make_timings()
    return Auction(transactive_node=tn, market_series=series, initial_market_state=MarketState.Inactive,
                   transition_times=timings)


def test_transition_from_active_to_negotiation_resets_and_schedules():
    tn = Mock(); asset = Mock(); asset.schedule_calculated = True
    tn.local_assets = [asset]; tn.neighbors = []
    auc = build_auction(tn)
    auc.market_state = MarketState.Active
    auc.converged = True
    auc.transition_from_active_to_negotiation()
    assert auc.converged is False
    asset.schedule.assert_called_once_with(auc)
    assert asset.schedule_calculated is False


def test_while_in_negotiation_sets_converged_when_all_calculated():
    tn = Mock(); a1=Mock(); a2=Mock(); a1.schedule_calculated=True; a2.schedule_calculated=True
    tn.local_assets=[a1,a2]; tn.neighbors=[]
    auc = build_auction(tn)
    auc.converged = False
    auc.while_in_negotiation()
    assert auc.converged and auc._stateIsCompleted


def test_while_in_negotiation_not_converged():
    tn = Mock(); a1=Mock(); a2=Mock(); a1.schedule_calculated=True; a2.schedule_calculated=False
    tn.local_assets=[a1,a2]; tn.neighbors=[]
    auc = build_auction(tn)
    auc.converged = False
    auc.while_in_negotiation()
    assert auc.converged is False and not auc._stateIsCompleted


def test_while_in_market_lead_assigns_unassigned_and_requires_single_upstream():
    tn = Mock()
    up = Mock(); up.up_or_down = Direction.upstream; up.transactive=True; up.last_received_signals={}
    down = Mock(); down.up_or_down = Direction.downstream; down.last_received_signals={}
    unassigned = Mock(); unassigned.up_or_down = None; unassigned.last_received_signals={}
    tn.neighbors = [up, down, unassigned]
    tn.local_assets = []
    auc = build_auction(tn)
    auc.time_intervals = []
    auc.while_in_market_lead()
    # unassigned becomes downstream
    assert unassigned.up_or_down == Direction.downstream


def test_while_in_market_lead_raises_without_upstream():
    tn = Mock(); tn.neighbors=[]; tn.local_assets=[]
    auc = build_auction(tn)
    with pytest.raises(RuntimeWarning):
        auc.while_in_market_lead()


def test_actuate_assets_can_be_switched_off_per_series_for_any_market_class():
    """actuate_assets is a base Market setting: a day-ahead series of any market class that is refined by a real-time
    one is configured not to actuate; a series with the default does. It is archived on the market row."""
    tn = Mock(); asset = Mock(); tn.local_assets = [asset]; tn.neighbors = []
    series = Mock(); series.name = "TestSeries"; series.description = ""; series.intervals_to_clear = 1
    series.duration = Mock(); series.duration.market_interval = timedelta(hours=1)
    silent = ConsensusMarket(transactive_node=tn, market_series=series, initial_market_state=MarketState.Inactive,
                             transition_times=make_timings(), actuate_assets=False)
    silent._actuate_assets()
    asset.actuate.assert_not_called()
    assert silent.actuate_assets is False and silent.get_record()['actuate_assets'] is False
    assert silent.table_name == 'market'
    loud = build_auction(tn)
    loud._actuate_assets()
    asset.actuate.assert_called_once_with(loud)
    assert loud.actuate_assets is True and loud.get_record()['actuate_assets'] is True
    assert loud.table_name == 'auction'
    market_columns = {c['name'] for d in Market.get_archive_definitions() if d['name'] == 'market' for c in d['columns']}
    assert 'actuate_assets' in market_columns
    auction = next(d for d in Auction.get_archive_definitions() if d['name'] == 'auction')
    assert auction['inherits'] == 'market' and [c['name'] for c in auction['columns']] == ['id']
    assert 'actuate_assets' in Market.Config.model_fields and 'actuate_assets' in ConsensusMarket.Config.model_fields
    assert Market.Config.model_fields['actuate_assets'].default is True
