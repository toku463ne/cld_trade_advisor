"""Leave-one-out A/B: confluence WITH vs WITHOUT brk_tenkan_hi (read-only).

Operator (2026-05-23): brk_tenkan_hi is in ~90% of confluence fires and the
kijun-level swap was refuted — so does just REMOVING brk_tenkan_hi from the
bullish set help, hurt, or wash?

This is a strategy-level LOO, not a per-fire probe: dropping a sign that sits in
90% of fires collapses the ≥3 count for most days, so N=3 confluences that relied
on tenkan fall to N=2 and vanish.  Must be judged on the capital-aware 6-slot book
(+ trade count), reusing confluence_benchmark.py machinery.

Arm A: full production set (10 signs).
Arm B: drop brk_tenkan_hi (9 signs).
Both: N=3 gate, 6-slot book, ZsTpSl(2/2/0.3), deterministic sorted-entry order.

Reports per FY: trades + capital-aware book Sharpe for each arm, and the stitched
all-FY book.  NOTE the deterministic order is ONE fill-order draw (band ~+0.6..1.2);
a real ship decision needs the paired fill-order null — this A/B is the directional
read the operator asked for first.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_drop_tenkan_ab
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_benchmark import _FYS, _book, _closes, _pos_daily
from src.analysis.exit_benchmark import _metrics
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_FULL = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
         "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_DROP = "brk_tenkan_hi"
_N_GATE = 3
_SLOTS = 6


def _run_arm(bullish: dict, fires_all: dict) -> dict:
    """Return {fy: (n_trades, book_sharpe, tot, dd, daily_rets)} for one bullish set."""
    cbt._VALID_BARS = dict(bullish)
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    out = {}
    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
        se = cfg.end + datetime.timedelta(days=60)
        with get_session() as s:
            n225 = DataCache("^N225", "1d")
            n225.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                      datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
            caches = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    caches[code] = c
        if not caches:
            continue
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        n_dts, _ = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        cands = []
        for code in caches:
            cands += cbt._candidates_for_stock(
                code, fires_all.get(code, []), caches[code], corr_maps[code], zs_maps[code],
                cfg.start, cfg.end, _N_GATE)
        cands.sort(key=lambda c: c.entry_date)
        results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)
        m = _metrics(results)
        cal_set = set(cal)
        day = defaultdict(float)
        for p in results:
            sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
            for d, r in _pos_daily(p, sdts, scmap).items():
                if d in cal_set:
                    day[d] += r / _SLOTS
        rets = [day.get(d, 0.0) for d in cal]
        bsh, btot, bdd = _book(rets)
        out[cfg.label] = (m.n, bsh, btot, bdd, rets[1:])
        logger.info("  {} {} trades book {:+.2f}", cfg.label, m.n, bsh)
    return out


def run() -> None:
    # fires for each arm (DB query filtered to that arm's sign set)
    def _fires(signs):
        with get_session() as s:
            rows = s.execute(
                select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                       SignBenchmarkEvent.fired_at)
                .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
                .where(SignBenchmarkRun.sign_type.in_(list(signs)))).all()
        f = defaultdict(list)
        for sg, st, fa in rows:
            f[st].append((sg, fa.date() if hasattr(fa, "date") else fa))
        return f

    logger.info("ARM A — full 10-sign set")
    a = _run_arm(_FULL, _fires(_FULL))
    drop_set = {k: v for k, v in _FULL.items() if k != _DROP}
    logger.info("ARM B — drop {} ({} signs)", _DROP, len(drop_set))
    b = _run_arm(drop_set, _fires(drop_set))

    print("\n" + "=" * 88)
    print(f"CONFLUENCE LOO A/B — full 10-sign  vs  drop {_DROP}  (N>=3, 6-slot book)")
    print("=" * 88)
    print(f"\n{'FY':<9}{'A trades':>9}{'A bookSh':>10}{'A totRet':>10}   {'B trades':>9}"
          f"{'B bookSh':>10}{'B totRet':>10}   {'Δ bookSh':>10}")
    sa, sb = [], []
    for cfg in _FYS:
        if cfg.label not in a or cfg.label not in b:
            continue
        an, ash, atot, _, ar = a[cfg.label]
        bn, bsh, btot, _, br = b[cfg.label]
        sa += ar; sb += br
        oos = "  OOS" if cfg.label == "FY2025" else ""
        print(f"{cfg.label:<9}{an:>9}{ash:>10.2f}{atot*100:>9.0f}%   {bn:>9}{bsh:>10.2f}"
              f"{btot*100:>9.0f}%   {bsh-ash:>+10.2f}{oos}")
    ash, atot, add = _book(sa)
    bsh, btot, bdd = _book(sb)
    print(f"\n  STITCHED  A: Sharpe {ash:+.2f} tot {atot*100:+.0f}% maxDD {add*100:.0f}%")
    print(f"            B: Sharpe {bsh:+.2f} tot {btot*100:+.0f}% maxDD {bdd*100:.0f}%")
    print(f"            Δ book Sharpe (B−A) = {bsh-ash:+.2f}")
    print("\n  (deterministic single fill-order draw; ship needs paired null. "
          "B trades << A confirms brk_tenkan_hi anchors most ≥3 confluences.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
