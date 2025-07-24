from enum import Enum


class OrderStatus(Enum):
    # Enumeration of valid Order object statuses.
    NEW = 0
    NEW_REVISED = 1
    STANDING_ORDER = 2          # One of two valid Order objects to be matched from a MatchingEngine object order book.
    REVISED_STANDING_ORDER = 3  # One of two valid Order objects to be matched from a MatchingEngine object order book.
    MATCHED = 4
    REVISED_MATCHED = 5
    CANCELLED = 6
    EXPIRED = 7
    SUPERSEDED = 8


class SupplyOrDemand(Enum):
    # Enumeration of Order object attribution as either supply or demand.
    SUPPLY = 0
    DEMAND = 1
    BOTH = 2


class VTypes(Enum):
    NONE = 0
    LIMIT_ORDER = 1
    MARKET_ORDER = 1  # A market order while behave much like a special limit order for matching. Same enum.
    LINEAR = 2
    COMPLEX = 3
