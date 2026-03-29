"""Shared test fixtures."""
import pytest
from datetime import datetime, timedelta
from decimal import Decimal
import pandas as pd
from btframework.data.models import Bar, Instrument
from btframework.events.market import MarketDataEvent
from btframework.events.base import EventBus
from btframework.core.registry import ComponentRegistry
from btframework.core.middleware import MiddlewarePipeline
from btframework.core.hooks import HookManager
from btframework.account.account import Account
from btframework.execution.exchange import SimulatedExchange
from btframework.execution.orders import Order
from btframework.account.broker import ForexBrokerProfile
from btframework.types import Side, OrderType


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def registry():
    return ComponentRegistry()


@pytest.fixture
def pipeline():
    return MiddlewarePipeline()


@pytest.fixture
def hooks():
    return HookManager()


@pytest.fixture
def account():
    return Account(initial_balance=100_000, currency="USD")


@pytest.fixture
def exchange():
    return SimulatedExchange()


@pytest.fixture
def sample_bars():
    """Generate sample OHLCV bars."""
    base_date = datetime(2024, 1, 1)
    bars = []
    price = 100.0
    for i in range(100):
        import random
        random.seed(42 + i)
        change = random.uniform(-2, 2)
        o = Decimal(str(round(price, 2)))
        h = Decimal(str(round(price + abs(change) + 0.5, 2)))
        l = Decimal(str(round(price - abs(change) - 0.5, 2)))
        c = Decimal(str(round(price + change, 2)))
        bars.append(Bar(
            timestamp=base_date + timedelta(days=i),
            open=o, high=h, low=l, close=c,
            volume=1_000_000 + random.randint(-500_000, 500_000),
            symbol="TEST",
        ))
        price = float(c)
    return bars


@pytest.fixture
def sample_market_events(sample_bars):
    """Convert bars to MarketDataEvents."""
    return [
        MarketDataEvent(
            symbol=bar.symbol,
            open=bar.open, high=bar.high, low=bar.low, close=bar.close,
            volume=bar.volume, bar_timestamp=bar.timestamp,
        )
        for bar in sample_bars
    ]


@pytest.fixture
def sample_dataframe():
    """Sample OHLCV DataFrame."""
    base_date = datetime(2024, 1, 1)
    data = {
        "timestamp": [base_date + timedelta(days=i) for i in range(50)],
        "open": [100 + i * 0.5 for i in range(50)],
        "high": [101 + i * 0.5 for i in range(50)],
        "low": [99 + i * 0.5 for i in range(50)],
        "close": [100.5 + i * 0.5 for i in range(50)],
        "volume": [1_000_000] * 50,
    }
    return pd.DataFrame(data)


@pytest.fixture
def sample_order():
    return Order(
        symbol="TEST",
        side=Side.BUY,
        quantity=Decimal("100"),
        order_type=OrderType.MARKET,
        strategy_id="test_strategy",
    )


@pytest.fixture
def instrument():
    """A sample forex instrument."""
    return Instrument(
        symbol="EURUSD",
        name="Euro vs US Dollar",
        asset_class="forex",
        currency="USD",
    )


@pytest.fixture
def margin_account(instrument):
    """Account with forex broker and registered instrument."""
    broker = ForexBrokerProfile()
    acct = Account(initial_balance=10_000, broker=broker)
    acct.register_instrument(instrument)
    return acct


@pytest.fixture
def feed_synthetic():
    """Synthetic data feed for deterministic testing."""
    from btframework.events.market import MarketDataEvent

    class _SyntheticFeed:
        def __init__(self, bars=10):
            self._bars = bars

        @property
        def symbols(self):
            return ["TEST"]

        def __iter__(self):
            from decimal import Decimal as D
            for i in range(self._bars):
                price = D("100") + D(str(i))
                event = MarketDataEvent(
                    symbol="TEST",
                    open=price,
                    high=price + D("2"),
                    low=price - D("2"),
                    close=price + D("1"),
                    volume=1000.0,
                    bar_timestamp=datetime(2024, 1, 1 + i),
                )
                yield {"TEST": event}

    return _SyntheticFeed


@pytest.fixture
def forex_calendar():
    from btframework.data.calendar import ForexCalendar
    return ForexCalendar()


@pytest.fixture
def spread_profile():
    from btframework.execution.middlewares.spread import SpreadProfile
    return SpreadProfile.default_forex()


@pytest.fixture
def price_path_generator():
    from btframework.execution.price_path import OHLCPricePathGenerator
    return OHLCPricePathGenerator(seed=42)
