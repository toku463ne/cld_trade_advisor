"""Reconcile: how many confluence-valid stocks PER DAY in the historical data?

The Daily UI shows ~50 proposals on some days, but the backtest yields ~36
trades/yr. This computes the UI-equivalent SNAPSHOT — for each trading day, the
number of stocks with >=N valid bullish signs (validity-windowed, NO cooldown,
NOT collapsed to bursts) — to see whether the historical FY2017-2025 pool is
really ~1/day (n-thin real) or ~50/day (my candidate dedup hid a big pool).

OUTCOME (2026-05-22): the pool is BIG — mean ~20 confluence-valid stocks/day
(median 14, max 141); 80.6% of days have >=5 simultaneously valid. So the Daily
UI showing ~50 proposals is normal and the "n-thin / median-choice-1" claim was
WRONG. Root cause: run_simulation already enforces ≤1-high+≤3-low slots and
SKIPS candidates when full (~1200 candidates/FY → ~36 filled, 97% skipped); the
earlier selection-pressure/ranking/capacity scripts double-counted that slot cap
by re-selecting on its output. Corrected ranking REJECTs but 6-slot capacity is
a near-miss. See project_confluence_xsec_ranking_reject.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_selection_pressure
"""
from __future__ import annotations

import datetime
import statistics
import sys
from collections import defaultdict

from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS
from src.data.db import get_session
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_FYS = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                 "classified2016"),
        FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))
    print(f"total fire rows: {len(rows)}; stocks with fires: {len(fires)}")

    grand_snap = []  # per-day count of stocks with >=N valid bullish signs
    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
        se = cfg.end + datetime.timedelta(days=10)
        with get_session() as s:
            caches = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    caches[code] = c

        # per-day count of stocks with >=N valid bullish signs (UI-equivalent)
        day_stock_validcount: dict[datetime.date, int] = defaultdict(int)
        for code, c in caches.items():
            dates = sorted({b.dt.date() for b in c.bars})
            d2i = {d: i for i, d in enumerate(dates)}
            valid_per_idx: dict[int, set] = defaultdict(set)
            for sign, fd in fires.get(code, []):
                if fd not in d2i:
                    continue
                fi = d2i[fd]
                vb = _BULLISH.get(sign, 5)
                for j in range(fi, min(fi + vb + 1, len(dates))):
                    valid_per_idx[j].add(sign)
            for i, d in enumerate(dates):
                if cfg.start <= d <= cfg.end and len(valid_per_idx.get(i, ())) >= _N_GATE:
                    day_stock_validcount[d] += 1

        snaps = list(day_stock_validcount.values())
        # include zero days within the FY for an honest mean
        fy_trading_days = sorted({b.dt.date() for c in caches.values() for b in c.bars
                                  if cfg.start <= b.dt.date() <= cfg.end})
        full = [day_stock_validcount.get(d, 0) for d in fy_trading_days]
        grand_snap += full
        if full:
            busy = sorted(full)[-3:]
            print(f"{cfg.label}: {len(fy_trading_days)} trading days | "
                  f"valid-stocks/day mean={statistics.mean(full):.1f} "
                  f"median={statistics.median(full):.0f} max={max(full)} | "
                  f"days with >=5 valid: {sum(1 for x in full if x>=5)} | top3={busy}")

    if grand_snap:
        print("\n" + "=" * 60)
        print(f"ALL FYs: {len(grand_snap)} trading days")
        print(f"  valid confluence stocks/day: mean={statistics.mean(grand_snap):.2f} "
              f"median={statistics.median(grand_snap):.0f} max={max(grand_snap)}")
        for thr in (1, 2, 3, 5, 10, 20):
            n = sum(1 for x in grand_snap if x >= thr)
            print(f"  days with >= {thr:>2} simultaneously-valid stocks: "
                  f"{n} ({100.0*n/len(grand_snap):.1f}%)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
