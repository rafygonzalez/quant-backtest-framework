from __future__ import annotations
import importlib
import sys
from pathlib import Path
from typing import Any, Callable
from collections import defaultdict
from btframework.exceptions import RegistryError


class ComponentRegistry:
    """Dynamic component registry for indicators, strategies, providers, middlewares, and brokers."""

    _categories = ("indicator", "strategy", "provider", "middleware", "broker")

    def __init__(self):
        self._components: dict[str, dict[str, type]] = defaultdict(dict)

    def _register(self, category: str, name: str) -> Callable:
        """Generic decorator factory for registration."""
        if category not in self._categories:
            raise RegistryError(f"Unknown category: {category}. Must be one of {self._categories}")

        def decorator(cls: type) -> type:
            if name in self._components[category]:
                raise RegistryError(f"{category}/{name} is already registered")
            self._components[category][name] = cls
            return cls
        return decorator

    def indicator(self, name: str) -> Callable:
        return self._register("indicator", name)

    def strategy(self, name: str) -> Callable:
        return self._register("strategy", name)

    def provider(self, name: str) -> Callable:
        return self._register("provider", name)

    def middleware(self, name: str) -> Callable:
        return self._register("middleware", name)

    def broker(self, name: str) -> Callable:
        return self._register("broker", name)

    def get(self, category: str, name: str) -> type:
        if category not in self._components or name not in self._components[category]:
            available = self.list(category)
            raise RegistryError(
                f"{category}/{name} not found. Available: {available}"
            )
        return self._components[category][name]

    def list(self, category: str) -> list[str]:
        return sorted(self._components.get(category, {}).keys())

    def load_plugins(self, path: Path | str) -> None:
        """Auto-discover and load .py files from a directory."""
        path = Path(path)
        if not path.is_dir():
            raise RegistryError(f"Plugin directory not found: {path}")
        for py_file in sorted(path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"btframework.plugins.{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

    def register_instance(self, category: str, name: str, cls: type) -> None:
        """Programmatic registration (non-decorator)."""
        if name in self._components[category]:
            raise RegistryError(f"{category}/{name} is already registered")
        self._components[category][name] = cls

    def clear(self) -> None:
        """Clear all registrations."""
        self._components.clear()


# Global registry singleton
registry = ComponentRegistry()
