"""Tests for the hook manager."""
import pytest
from btframework.core.hooks import HookManager


class TestHookManager:
    def test_emit_calls_handler(self, hooks):
        called = []
        hooks.on("before_bar", lambda **kw: called.append(kw))
        hooks.emit("before_bar", price=100)
        assert len(called) == 1
        assert called[0]["price"] == 100

    def test_multiple_handlers(self, hooks):
        count = [0]
        hooks.on("test", lambda **kw: count.__setitem__(0, count[0] + 1))
        hooks.on("test", lambda **kw: count.__setitem__(0, count[0] + 1))
        hooks.emit("test")
        assert count[0] == 2

    def test_off_removes_handler(self, hooks):
        called = []
        handler = lambda **kw: called.append(1)
        hooks.on("test", handler)
        hooks.off("test", handler)
        hooks.emit("test")
        assert len(called) == 0

    def test_clear_specific(self, hooks):
        hooks.on("a", lambda **kw: None)
        hooks.on("b", lambda **kw: None)
        hooks.clear("a")
        assert "a" not in hooks.list_hooks()
        assert "b" in hooks.list_hooks()

    def test_clear_all(self, hooks):
        hooks.on("a", lambda **kw: None)
        hooks.on("b", lambda **kw: None)
        hooks.clear()
        assert hooks.list_hooks() == {}

    def test_strict_mode_rejects_unknown(self):
        hooks = HookManager(strict=True)
        with pytest.raises(ValueError):
            hooks.on("invalid_hook", lambda **kw: None)

    def test_strict_mode_allows_valid(self):
        hooks = HookManager(strict=True)
        hooks.on("before_bar", lambda **kw: None)  # Should not raise
