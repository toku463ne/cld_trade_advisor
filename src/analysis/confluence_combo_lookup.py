"""Per-FY frequency of a specific confluence sign-combo (read-only).

Operator (2026-05-23): is "brk_tenkan_hi + rev_lo + str_hold" a FY2021-specific
combo, or common across years?  (FY2021 top-6 showed it 134x; FY2025 155x.)

Reuses confluence_sign_combo's burst-deduped fire-set reconstruction (production
10-sign set, N=3, 10-bar cooldown).  For the target combo reports per FY:
  - EXACT  : fires whose valid set == the target (a pure N=3 of exactly these)
  - SUPERSET: fires whose valid set ⊇ the target (these three valid, maybe + more)
both as count and as % of that FY's confluence fires.

Edit _TARGET to look up any combo.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_combo_lookup
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_sign_combo import _BULLISH, _FYS, _fire_sets_for_stock
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.simulator.cache import DataCache

_TARGET = frozenset({"brk_tenkan_hi", "rev_lo", "str_hold"})


def run() -> None:
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    print("\n" + "=" * 78)
    print(f"COMBO LOOKUP — target = {{{', '.join(sorted(_TARGET))}}}  (N>=3 confluence fires)")
    print("=" * 78)
    print(f"\n  {'FY':<9}{'fires':>8}{'exact':>8}{'exact%':>9}{'superset':>10}{'superset%':>11}")
    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
        se = cfg.end + datetime.timedelta(days=60)
        sets = []
        with get_session() as s:
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    sets += _fire_sets_for_stock(fires.get(code, []), c, cfg.start, cfg.end)
        n = len(sets)
        if not n:
            continue
        exact = sum(1 for x in sets if x == _TARGET)
        sup = sum(1 for x in sets if x >= _TARGET)
        note = "  <-- worst" if cfg.label == "FY2021" else ("  OOS" if cfg.label == "FY2025" else "")
        print(f"  {cfg.label:<9}{n:>8}{exact:>8}{100*exact/n:>8.1f}%{sup:>10}{100*sup/n:>10.1f}%{note}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
