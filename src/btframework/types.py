from decimal import Decimal
from enum import Enum, auto
from typing import Any, Callable
from datetime import datetime

# Type aliases
Price = Decimal
Quantity = Decimal
Money = Decimal

class Side(Enum):
    BUY = auto()
    SELL = auto()

class OrderType(Enum):
    MARKET = auto()
    LIMIT = auto()
    STOP = auto()
    STOP_LIMIT = auto()

class OrderStatus(Enum):
    PENDING = auto()
    SUBMITTED = auto()
    PARTIAL = auto()
    FILLED = auto()
    CANCELLED = auto()
    REJECTED = auto()
    EXPIRED = auto()

class TimeInForce(Enum):
    GTC = auto()  # Good Till Cancel
    DAY = auto()
    IOC = auto()  # Immediate or Cancel
    FOK = auto()  # Fill or Kill

class PositionSide(Enum):
    LONG = auto()
    SHORT = auto()
    FLAT = auto()

class SignalType(Enum):
    ENTRY_LONG = auto()
    ENTRY_SHORT = auto()
    EXIT_LONG = auto()
    EXIT_SHORT = auto()
    EXIT_ALL = auto()

class Timeframe(Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"
    MN1 = "1M"

# Callback types
NextFn = Callable[..., Any]
HookHandler = Callable[..., None]
