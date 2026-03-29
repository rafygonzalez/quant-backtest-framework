from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from dataclasses import asdict
from btframework.events.base import Event, EventBus


class EventLogger:
    """Logs all events for replay and debugging."""

    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._events: list[dict] = []
        self._event_bus.subscribe("*", self._on_event)

    def _on_event(self, event: Event) -> None:
        record = asdict(event)
        # Convert non-serializable types
        for k, v in record.items():
            if isinstance(v, datetime):
                record[k] = v.isoformat()
            elif hasattr(v, 'value'):  # Enum
                record[k] = str(v)
            elif hasattr(v, '__str__') and not isinstance(v, (str, int, float, bool, list, dict, type(None))):
                record[k] = str(v)
        self._events.append(record)

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self._events, f, indent=2, default=str)

    @classmethod
    def load(cls, path: Path | str) -> list[dict]:
        with open(path) as f:
            return json.load(f)

    def clear(self) -> None:
        self._events.clear()
