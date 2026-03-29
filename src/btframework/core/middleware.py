from __future__ import annotations
import logging
from typing import Protocol, Any, Callable, runtime_checkable
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class ExecutionContext:
    """Context passed through the middleware pipeline."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str = ""
    current_price: Any = None
    volume: float = 0.0
    account: Any = None  # Account reference
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class Middleware(Protocol):
    """Protocol for execution middlewares."""

    def process_order(self, order: Any, ctx: ExecutionContext, next_fn: Callable) -> Any | None:
        """Process an order. Return modified order, None to reject, or call next_fn to pass through."""
        ...

    def process_fill(self, fill: Any, ctx: ExecutionContext, next_fn: Callable) -> Any | None:
        """Process a fill. Return modified fill, None to reject, or call next_fn to pass through."""
        ...


class MiddlewarePipeline:
    """Chain-of-responsibility pipeline for order/fill processing.

    Wraps each middleware call in try-catch so a failing middleware
    logs the error and rejects the item instead of silently losing it.
    """

    def __init__(self) -> None:
        self._middlewares: list[Middleware] = []

    def use(self, middleware: Middleware) -> "MiddlewarePipeline":
        """Add a middleware to the pipeline. Returns self for chaining."""
        self._middlewares.append(middleware)
        return self

    def execute_order(self, order: Any, ctx: ExecutionContext) -> Any | None:
        """Run an order through the middleware chain."""
        return self._execute(order, ctx, "process_order")

    def execute_fill(self, fill: Any, ctx: ExecutionContext) -> Any | None:
        """Run a fill through the middleware chain."""
        return self._execute(fill, ctx, "process_fill")

    def _execute(self, item: Any, ctx: ExecutionContext, method: str) -> Any | None:
        """Shared chain execution for both orders and fills."""
        if not self._middlewares:
            return item

        def build_chain(index: int) -> Callable:
            def next_fn(obj: Any) -> Any | None:
                if index >= len(self._middlewares):
                    return obj
                mw = self._middlewares[index]
                try:
                    handler = getattr(mw, method)
                    return handler(obj, ctx, build_chain(index + 1))
                except Exception as e:
                    mw_name = type(mw).__name__
                    logger.error(
                        f"Middleware '{mw_name}' raised on {method}: {e}",
                        exc_info=True,
                    )
                    return None
            return next_fn

        mw = self._middlewares[0]
        try:
            handler = getattr(mw, method)
            return handler(item, ctx, build_chain(1))
        except Exception as e:
            mw_name = type(mw).__name__
            logger.error(
                f"Middleware '{mw_name}' raised on {method}: {e}",
                exc_info=True,
            )
            return None

    @property
    def middlewares(self) -> list[Middleware]:
        return list(self._middlewares)

    def clear(self) -> None:
        self._middlewares.clear()
