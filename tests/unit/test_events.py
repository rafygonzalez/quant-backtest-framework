"""Tests for the event system."""
from btframework.events.base import Event, EventBus
from btframework.events.market import MarketDataEvent
from btframework.events.signal import SignalEvent
from btframework.events.order import OrderEvent
from btframework.events.fill import FillEvent
from btframework.types import SignalType, Side


class TestEvent:
    def test_event_has_id_and_timestamp(self):
        event = Event()
        assert event.event_id
        assert event.timestamp
        assert event.event_type == "Event"

    def test_market_data_event(self):
        from decimal import Decimal
        e = MarketDataEvent(symbol="AAPL", open=Decimal("150"), close=Decimal("152"))
        assert e.symbol == "AAPL"
        assert e.event_type == "MarketDataEvent"

    def test_signal_event(self):
        e = SignalEvent(symbol="AAPL", signal_type=SignalType.ENTRY_LONG, strategy_id="test")
        assert e.signal_type == SignalType.ENTRY_LONG


class TestEventBus:
    def test_publish_subscribe(self, event_bus):
        received = []
        event_bus.subscribe("MarketDataEvent", lambda e: received.append(e))
        event = MarketDataEvent(symbol="TEST")
        event_bus.publish(event)
        assert len(received) == 1
        assert received[0].symbol == "TEST"

    def test_wildcard_subscriber(self, event_bus):
        received = []
        event_bus.subscribe("*", lambda e: received.append(e))
        event_bus.publish(MarketDataEvent(symbol="A"))
        event_bus.publish(SignalEvent(symbol="B"))
        assert len(received) == 2

    def test_unsubscribe(self, event_bus):
        handler = lambda e: None
        event_bus.subscribe("test", handler)
        event_bus.unsubscribe("test", handler)
        # Should not raise

    def test_event_log(self, event_bus):
        event_bus.publish(Event())
        event_bus.publish(Event())
        assert len(event_bus.event_log) == 2
