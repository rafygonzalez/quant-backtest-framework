"""Tests for the middleware pipeline."""
from decimal import Decimal
from btframework.core.middleware import MiddlewarePipeline, ExecutionContext
from btframework.execution.orders import Order
from btframework.execution.fill import Fill
from btframework.types import Side, OrderType


class PassthroughMiddleware:
    def process_order(self, order, ctx, next_fn):
        return next_fn(order)
    def process_fill(self, fill, ctx, next_fn):
        return next_fn(fill)


class RejectMiddleware:
    def process_order(self, order, ctx, next_fn):
        return None  # Reject
    def process_fill(self, fill, ctx, next_fn):
        return next_fn(fill)


class ModifyMiddleware:
    def process_order(self, order, ctx, next_fn):
        order.metadata["modified"] = True
        return next_fn(order)
    def process_fill(self, fill, ctx, next_fn):
        return next_fn(fill)


class TestMiddlewarePipeline:
    def test_empty_pipeline_passes_through(self, pipeline, sample_order):
        ctx = ExecutionContext()
        result = pipeline.execute_order(sample_order, ctx)
        assert result is sample_order

    def test_passthrough(self, pipeline, sample_order):
        pipeline.use(PassthroughMiddleware())
        ctx = ExecutionContext()
        result = pipeline.execute_order(sample_order, ctx)
        assert result is not None

    def test_reject(self, pipeline, sample_order):
        pipeline.use(RejectMiddleware())
        ctx = ExecutionContext()
        result = pipeline.execute_order(sample_order, ctx)
        assert result is None

    def test_modify(self, pipeline, sample_order):
        pipeline.use(ModifyMiddleware())
        ctx = ExecutionContext()
        result = pipeline.execute_order(sample_order, ctx)
        assert result.metadata.get("modified") is True

    def test_chain_order(self, pipeline, sample_order):
        pipeline.use(ModifyMiddleware())
        pipeline.use(PassthroughMiddleware())
        ctx = ExecutionContext()
        result = pipeline.execute_order(sample_order, ctx)
        assert result.metadata.get("modified") is True

    def test_builder_pattern(self):
        p = MiddlewarePipeline()
        result = p.use(PassthroughMiddleware()).use(PassthroughMiddleware())
        assert result is p
        assert len(p.middlewares) == 2
