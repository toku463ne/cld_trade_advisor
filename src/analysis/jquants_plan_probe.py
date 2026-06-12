"""Probe which J-Quants v2 endpoints the current subscription covers.

Hits one cheap request per endpoint of interest and prints the HTTP status plus the
server's message (which distinguishes bad-path / bad-key / plan-gated — see
docs/analysis/20260613_jquants_margin_plan_check.md). Never prints the key.

Rerun after any plan/key change:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.jquants_plan_probe
"""
from __future__ import annotations

import os

import requests
from loguru import logger

_BASE = os.environ.get("JQUANTS_BASE_URL", "https://api.jquants.com/v2").rstrip("/")
_PROBES: list[tuple[str, dict[str, str]]] = [
    ("/markets/calendar", {"from": "2026-06-01", "to": "2026-06-10"}),
    ("/fins/summary", {"date": "2026-06-10"}),
    ("/markets/margin-interest", {"code": "72030"}),
    ("/markets/margin-alert", {"date": "2026-06-05"}),
    ("/markets/short-ratio", {"date": "2026-06-05"}),
    ("/markets/short-sale-report", {"code": "72030"}),
    ("/markets/breakdown", {"code": "72030"}),
]


def run() -> None:
    key = os.environ.get("JQUANTS_API_KEY", "")
    if not key:
        raise RuntimeError("JQUANTS_API_KEY is not set (or empty) — load devenv.")
    logger.info("probing {} endpoints against {} (key length {})", len(_PROBES), _BASE, len(key))
    for path, params in _PROBES:
        resp = requests.get(_BASE + path, params=params,
                            headers={"x-api-key": key}, timeout=30)
        if resp.status_code == 200:
            n = sum(len(v) for v in resp.json().values() if isinstance(v, list))
            print(f"  200  {path:<28} OK ({n} rows)")
        else:
            msg = resp.json().get("message", resp.text[:120]) if resp.text else ""
            print(f"  {resp.status_code}  {path:<28} {msg}")


if __name__ == "__main__":
    run()
