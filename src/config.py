"""Shared configuration loader.

Handles two config layers:
  1. ``configs/stock_codes.ini``  — named sets of stock codes
  2. ``configs/*.yaml``           — run parameters (trainer, collector, etc.)

CLI arguments always override YAML values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Stock code sets (.ini with [section] + inline # comments, no key=value)
# ---------------------------------------------------------------------------

def load_stock_codes(ini_path: str | Path, section: str) -> list[str]:
    """Return the stock codes in *section* of *ini_path*.

    File format::

        [test]
        1716.T   # inline comment — stripped
        ^N225    # caret-prefixed codes are preserved as-is

    Blank lines, lines starting with ``#`` or ``;``, and inline comments
    are all handled.  Case is preserved (critical for ``^N225``).
    """
    path = Path(ini_path)
    codes: list[str] = []
    in_target = False
    found = False

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                in_target = current == section
                if in_target:
                    found = True
                continue
            if in_target:
                code = line.split("#")[0].strip()
                if code:
                    codes.append(code)

    if not found:
        available = list_stock_sets(ini_path)
        raise KeyError(
            f"Section [{section}] not found in {ini_path}. "
            f"Available: {available}"
        )
    return codes


def list_stock_sets(ini_path: str | Path) -> list[str]:
    """Return all section names defined in *ini_path*."""
    sections: list[str] = []
    with open(ini_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                sections.append(line[1:-1].strip())
    return sections


# ---------------------------------------------------------------------------
# YAML config loader
# ---------------------------------------------------------------------------

def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file and return the contents as a dict."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# YAML → argparse defaults (trainer)
# ---------------------------------------------------------------------------

def trainer_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    """Flatten a trainer YAML config dict to argparse-compatible defaults.

    Keys returned match the ``dest`` names used in trainer._build_parser().
    Missing YAML keys are simply omitted so argparse keeps its own defaults.
    """
    d: dict[str, Any] = {}

    for key in ("stock_codes_file", "stock_set"):
        if key in cfg:
            d[key] = cfg[key]

    for key in ("granularity", "start", "end"):
        v = cfg.get("data", {}).get(key)
        if v is not None:
            d[key] = str(v)

    strat = cfg.get("strategy", {})
    if "name" in strat:
        d["strategy"] = strat["name"]
    if "units" in strat:
        d["units"] = int(strat["units"])

    tr = cfg.get("trainer", {})
    _copy(tr, d, "algorithm", "trainer")
    _copy(tr, d, "capital",   "capital",   float)
    _copy(tr, d, "top",       "top",       int)
    _copy(tr, d, "score_agg", "score_agg")

    ga = cfg.get("ga", {})
    _copy(ga, d, "population",          "ga_pop",                 int)
    _copy(ga, d, "generations",         "ga_gen",                 int)
    _copy(ga, d, "elite_fraction",      "ga_elite",               float)
    _copy(ga, d, "crossover_prob",      "ga_crossover_prob",      float)
    _copy(ga, d, "mutation_rate",       "ga_mutation_rate",       float)
    _copy(ga, d, "mutation_sigma",      "ga_mutation_sigma",      float)
    _copy(ga, d, "stagnation_patience", "ga_stagnation_patience", int)
    _copy(ga, d, "seed",                "ga_seed",                _int_or_none)

    return d


# ---------------------------------------------------------------------------
# YAML → argparse defaults (collector)
# ---------------------------------------------------------------------------

def collect_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    """Flatten a collect YAML config dict to argparse-compatible defaults."""
    d: dict[str, Any] = {}

    for key in ("stock_codes_file", "stock_set"):
        if key in cfg:
            d[key] = cfg[key]

    for key in ("granularity", "start", "end"):
        v = cfg.get("data", {}).get(key)
        if v is not None:
            d[key] = str(v)

    return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _copy(
    src: dict[str, Any],
    dst: dict[str, Any],
    src_key: str,
    dst_key: str,
    cast: Any = None,
) -> None:
    if src_key not in src or src[src_key] is None:
        return
    val = src[src_key]
    dst[dst_key] = cast(val) if cast is not None else val


def _int_or_none(v: Any) -> int | None:
    return None if v is None else int(v)
