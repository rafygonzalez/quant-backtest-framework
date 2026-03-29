from __future__ import annotations
import logging
from typing import Any, Callable
from collections import defaultdict

logger = logging.getLogger(__name__)

# Valid lifecycle hook names
LIFECYCLE_HOOKS = frozenset({
    "before_bar", "after_bar",
    "before_signal", "after_signal",
    "before_order", "after_order",
    "before_fill", "after_fill",
    "on_position_open", "on_position_close",
    "on_drawdown_exceeded", "on_margin_call",
    "on_backtest_start", "on_backtest_end",
    "on_strategy_error", "on_fill_error",
    "on_market_closed",
})


class HookManager:
    """Manages lifecycle hooks for the backtest engine."""

    def __init__(self, strict: bool = False):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._strict = strict  # If True, only allow LIFECYCLE_HOOKS

    def on(self, hook_name: str, handler: Callable) -> None:
        """Register a handler for a lifecycle event."""
        if self._strict and hook_name not in LIFECYCLE_HOOKS:
            raise ValueError(
                f"Unknown hook '{hook_name}'. Valid hooks: {sorted(LIFECYCLE_HOOKS)}"
            )
        self._handlers[hook_name].append(handler)

    def off(self, hook_name: str, handler: Callable) -> None:
        """Remove a handler for a lifecycle event."""
        try:
            self._handlers[hook_name].remove(handler)
        except ValueError:
            pass

    def emit(self, hook_name: str, **kwargs: Any) -> None:
        """Emit a lifecycle event, calling all registered handlers."""
        for handler in self._handlers.get(hook_name, []):
            try:
                handler(**kwargs)
            except Exception as e:
                logger.error(f"Hook handler error on '{hook_name}': {e}", exc_info=True)
                if self._strict:
                    raise

    def clear(self, hook_name: str | None = None) -> None:
        """Clear handlers for a specific event, or all events if None."""
        if hook_name is None:
            self._handlers.clear()
        else:
            self._handlers.pop(hook_name, None)

    def list_hooks(self) -> dict[str, int]:
        """Return registered hooks and their handler counts."""
        return {k: len(v) for k, v in self._handlers.items() if v}
