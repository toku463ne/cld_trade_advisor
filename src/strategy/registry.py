"""Strategy plugin registry — auto-discovers all modules in src/strategy/.

Adding a new strategy requires no changes here:
1. Create src/strategy/my_strategy.py with a Plugin subclass.
2. Call ``register(MyPlugin())`` at the bottom of that module.
3. The registry auto-imports every module in this package on first use.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

import src.strategy as _strategy_pkg
from src.strategy.base import StrategyPlugin

_by_name: dict[str, StrategyPlugin[Any]] = {}
_by_cli: dict[str, StrategyPlugin[Any]] = {}
_loaded = False


def register(plugin: StrategyPlugin[Any]) -> None:
    """Register a plugin under both its DB name and CLI slug."""
    _by_name[plugin.name] = plugin
    _by_cli[plugin.cli_name] = plugin


def get_by_name(name: str) -> StrategyPlugin[Any]:
    """Look up by DB strategy_name (e.g. ``'SMABreakout'``)."""
    _ensure_loaded()
    try:
        return _by_name[name]
    except KeyError:
        raise KeyError(f"Unknown strategy '{name}'. Available: {sorted(_by_name)}")


def get_by_cli_name(cli_name: str) -> StrategyPlugin[Any]:
    """Look up by CLI slug (e.g. ``'sma_breakout'``)."""
    _ensure_loaded()
    try:
        return _by_cli[cli_name]
    except KeyError:
        raise KeyError(
            f"Unknown strategy CLI name '{cli_name}'. Available: {sorted(_by_cli)}"
        )


def all_cli_names() -> list[str]:
    """Return all registered CLI slugs, sorted."""
    _ensure_loaded()
    return sorted(_by_cli)


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    _skip = {"src.strategy.base", "src.strategy.registry"}
    for _, modname, _ in pkgutil.iter_modules(
        _strategy_pkg.__path__, "src.strategy."
    ):
        if modname not in _skip:
            importlib.import_module(modname)
