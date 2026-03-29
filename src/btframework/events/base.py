from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from collections import defaultdict


@dataclass(frozen=True)
class Event:
    """Base event class."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str = ""

    def __post_init__(self):
        if not self.event_type:
            object.__setattr__(self, 'event_type', self.__class__.__name__)


class EventBus:
    """Pub/sub event bus for decoupled communication."""

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._log: list[Event] = []

    def subscribe(self, event_type: str, handler: Callable) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        self._handlers[event_type].remove(handler)

    def publish(self, event: Event) -> None:
        self._log.append(event)
        for handler in self._handlers.get(event.event_type, []):
            handler(event)
        # Also notify wildcard subscribers
        for handler in self._handlers.get("*", []):
            handler(event)

    @property
    def event_log(self) -> list[Event]:
        return list(self._log)

    def clear_log(self) -> None:
        self._log.clear()
