from market import Market
from market_state import MarketState
from market_types import MarketTypes


class ReconciliationMarket(Market):
    # 211208DJH: This class is created to explore the reconciliation and price-tracking functions. It is probably better
    #            to  address these capabilities within the base Market construct, however, for its reconciliation state.

    def __init__(self,
                 actual_power=None,
                 approved_price=0,
                 global_price_correction=0,
                 local_price_correction=0):

        # 211208DJH: Parent Market properties remain relevant, and some properties will be unique to reconciliation.
        #            As a separate child class, instantiation would be necessary within a Market.spawn() method, but it
        #            will probably occur that these capabilities will be added to the base Market class for its
        #            reconciliation state.
        super(ReconciliationMarket, self).__init__(market_type=MarketTypes.reconciliation,
                                                   initial_market_state=MarketState.Reconcile,
                                                   )
        # 211208DJH: These are new properties that will be needed for price tracking (and reconciliation and
        #            commitment).
        self.actualPower = actual_power                     # Actual average power in market interval (e.g., from meter)
        self.approvedPrice = approved_price
        self.globalPriceCorrection = global_price_correction
        self.localPriceCorrection = local_price_correction

    def get_actual_power(self):
        # 211208DJH: A reconciliation process must be associated with a MeterPoint object. This method should call the
        #            MeterPoint object to read the average metered power during the market time interval(s).
        pass

    def update_local_price_correction(self):
        pass

    def update_global_price_correction(self):
        pass
