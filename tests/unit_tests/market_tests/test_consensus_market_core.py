"""ConsensusMarket must be constructible through MarketSeries like every other Market subclass."""
from tent.enumerations import MarketState, MarketTypes, Method
from tent.market.consensus_market import ConsensusMarket
from tent.market.market_series import MarketSeries
from tent.utils.timer import Timer
from tests.mock_tn import MockTN


def test_consensus_market_spawns_from_series():
    Timer.simulation = False
    tn = MockTN()
    series = MarketSeries(transactive_node=tn, name='Consensus Series', market_class=ConsensusMarket,
                          initial_market_state=MarketState.Negotiation, method='Interpolation')
    market = series.newest_market
    assert isinstance(market, ConsensusMarket)
    assert market.MARKET_TYPE is MarketTypes.consensus
    assert series.method is Method.Interpolation
    assert series.market_kwargs == {}
