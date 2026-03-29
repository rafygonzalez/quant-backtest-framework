"""Tests for the component registry."""
import pytest
from btframework.core.registry import ComponentRegistry
from btframework.exceptions import RegistryError


class TestRegistry:
    def test_register_and_get(self, registry):
        @registry.indicator("test_ind")
        class TestInd:
            pass

        assert registry.get("indicator", "test_ind") is TestInd

    def test_duplicate_registration_raises(self, registry):
        @registry.indicator("dup")
        class A:
            pass

        with pytest.raises(RegistryError):
            @registry.indicator("dup")
            class B:
                pass

    def test_get_missing_raises(self, registry):
        with pytest.raises(RegistryError):
            registry.get("indicator", "nonexistent")

    def test_list_components(self, registry):
        @registry.strategy("strat_a")
        class A:
            pass
        @registry.strategy("strat_b")
        class B:
            pass
        assert registry.list("strategy") == ["strat_a", "strat_b"]

    def test_all_categories(self, registry):
        for cat in ("indicator", "strategy", "provider", "middleware", "broker"):
            decorator = getattr(registry, cat)
            @decorator(f"test_{cat}")
            class C:
                pass
            assert registry.get(cat, f"test_{cat}") is C
