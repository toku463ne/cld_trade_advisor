"""Registry scanner: discover valid signs and exits from module-level flags."""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass

import src.exit
import src.signs


@dataclass
class SignEntry:
    module_name: str
    sign_names: list[str]
    valid: bool


@dataclass
class ExitEntry:
    module_name: str
    rule: object | None
    valid: bool


def scan_signs() -> list[SignEntry]:
    """Return all sign modules that expose SIGN_VALID and SIGN_NAMES."""
    entries: list[SignEntry] = []
    for info in pkgutil.iter_modules(src.signs.__path__):
        mod = importlib.import_module(f"src.signs.{info.name}")
        valid = getattr(mod, "SIGN_VALID", None)
        names = getattr(mod, "SIGN_NAMES", None)
        if valid is None or names is None:
            continue
        entries.append(SignEntry(module_name=info.name, sign_names=names, valid=bool(valid)))
    return sorted(entries, key=lambda e: e.module_name)


def scan_exits() -> list[ExitEntry]:
    """Return all exit modules that expose EXIT_VALID and EXIT_RULE."""
    entries: list[ExitEntry] = []
    for info in pkgutil.iter_modules(src.exit.__path__):
        mod = importlib.import_module(f"src.exit.{info.name}")
        valid = getattr(mod, "EXIT_VALID", None)
        if valid is None:
            continue
        rule = getattr(mod, "EXIT_RULE", None)
        entries.append(ExitEntry(module_name=info.name, rule=rule, valid=bool(valid)))
    return sorted(entries, key=lambda e: e.module_name)


def valid_signs() -> list[SignEntry]:
    return [s for s in scan_signs() if s.valid]


def valid_exits() -> list[ExitEntry]:
    return [e for e in scan_exits() if e.valid]


def all_valid_sign_names() -> list[str]:
    names: list[str] = []
    for entry in valid_signs():
        names.extend(entry.sign_names)
    return sorted(set(names))
