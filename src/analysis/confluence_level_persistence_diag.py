"""Diagnostic: how often would a "level still holds today" persistence gate bite?

/sign-debate 2026-05-23 (operator: brk_tenkan_hi fired 2026-01-13 still "valid"
2026-01-16 when price fell back below tenkan).  READ-ONLY — measures, does not gate.

Confluence counts a sign valid if it fired within valid_bars.  brk_sma/brk_bol
ALREADY re-check their level at detect-time (so they self-trim and never appear
lapsed in a proposal's constituent list); brk_kumo_hi/brk_tenkan_hi do NOT.
chiko_hi is out of scope (its satisfied[] is unused in detect(); it's an interval
relation, not price-vs-level — judge's instruction).

Uses the LIVE ConfluenceSignStrategy (computes signs fresh from OHLCV) — the
sign-benchmark backtest path can't be used (sign_benchmark_events were wiped in
the 2026-05-23 DB incident and not rebuilt; only classified2024 exists).  So this
runs on the available window (classified2024, ~2025-01 → 2026-05-22) — a single
stock_set / ~1.4yr, smaller than the full FY2017-2025 the backtest path would give.

For every N>=3 stock-day proposal, recompute the as-of-day level for each
brk_tenkan_hi / brk_kumo_hi constituent:
  brk_tenkan_hi still-holds: low[D] > tenkan[D]
  brk_kumo_hi    still-holds: low[D] > max(senkouA[D-26], senkouB[D-26])
Drop lapsed level constituents and ask: does the stock-day still clear N>=3?

PROCEED/STOP (judge): >=5% of N>=3 stock-days drop below quorum -> escalate to
operator for an A/B.  <5% -> NO-OP (à la trend_score floor 0.7% / dSharpe 0.00);
resolve the brk_sma-vs-kumo/tenkan code inconsistency by fiat, no A/B.

OUTCOME (2026-05-23, classified2024 / 2025-01→2026-05, 8,479 N>=3 stock-days):
MATERIAL, NOT a no-op (overturns the trend_score-floor prior). 33.9% of N>=3
stock-days have >=1 lapsed level constituent; **27.8% would drop below N>=3** if
lapsed brk_tenkan_hi/brk_kumo_hi were excluded (lapses: tenkan 2327, kumo 1255).
Tenkan is a fast line → frequent intra-window lapse. >>5% threshold → escalate to
operator for a strategy A/B + paired fill-order null. CAVEATS: (1) per stock-DAY,
not unique fire event (over-weights persistently-lapsed names); (2) shows the gate
BITES, NOT that it HELPS — whether dropping these improves the book is unproven and
needs the A/B; (3) single stock_set / ~1.4yr (full FY2017-2025 needs the wiped
sign_benchmark rebuilt). See project_confluence_level_persistence.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_level_persistence_diag
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

import numpy as np
from loguru import logger

from src.indicators.ichimoku import calc_ichimoku
from src.strategy.confluence_sign import ConfluenceSignStrategy

_STOCK_SET = "classified2024"
_N_GATE = 3
_LEVEL_SIGNS = ("brk_tenkan_hi", "brk_kumo_hi")   # the un-gated level breakouts
_START = datetime.datetime(2024, 6, 1, tzinfo=datetime.timezone.utc)
_END = datetime.datetime(2026, 5, 22, 23, 59, tzinfo=datetime.timezone.utc)
_EVAL_FROM = datetime.date(2025, 1, 1)   # after ichimoku warmup
_EVAL_TO = datetime.date(2026, 5, 22)


def _daily(cache):
    seen, dts, H, L, C = set(), [], [], [], []
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); H.append(b.high); L.append(b.low); C.append(b.close)
    order = np.argsort(dts)
    dts = [dts[i] for i in order]; H = [H[i] for i in order]
    L = [L[i] for i in order]; C = [C[i] for i in order]
    ichi = calc_ichimoku(H, L, C, tenkan_period=9)
    tenkan = np.asarray(ichi["tenkan"], dtype=float)
    sa = np.asarray(ichi["senkou_a"], dtype=float)
    sb = np.asarray(ichi["senkou_b"], dtype=float)
    d = ichi["displacement"]
    n = len(dts)
    kumo_top = np.full(n, np.nan)
    for i in range(d, n):
        kumo_top[i] = max(sa[i - d], sb[i - d])
    return {dd: i for i, dd in enumerate(dts)}, np.asarray(L, dtype=float), tenkan, kumo_top


def _lapsed(sign, lo, tk, kt, i):
    if i is None:
        return False
    if sign == "brk_tenkan_hi":
        return not (tk[i] == tk[i] and lo[i] > tk[i])      # tk==tk excludes NaN
    if sign == "brk_kumo_hi":
        return not (kt[i] == kt[i] and lo[i] > kt[i])
    return False


def run() -> None:
    strat = ConfluenceSignStrategy.from_config(
        stock_set=_STOCK_SET, start=_START, end=_END, n_gate=_N_GATE)
    levels = {code: _daily(c) for code, c in strat._stock_caches.items()}
    by_day = strat.propose_range(
        datetime.datetime.combine(_EVAL_FROM, datetime.time.min, tzinfo=datetime.timezone.utc),
        datetime.datetime.combine(_EVAL_TO, datetime.time.max, tzinfo=datetime.timezone.utc),
    )

    n_fires = n_lapsed = n_drop = 0
    lapse_by_sign = defaultdict(int)
    by_month = defaultdict(lambda: [0, 0, 0])

    for day, props in by_day.items():
        mk = day.strftime("%Y-%m")
        for p in props:
            signs = p.sign_type.split(":", 1)[-1].split(",")
            N = len(signs)
            info = levels.get(p.stock_code)
            if info is None:
                continue
            idx, lo, tk, kt = info
            i = idx.get(day)
            lapsed = [s for s in signs if s in _LEVEL_SIGNS and _lapsed(s, lo, tk, kt, i)]
            n_fires += 1; by_month[mk][0] += 1
            if lapsed:
                n_lapsed += 1; by_month[mk][1] += 1
                for s in lapsed:
                    lapse_by_sign[s] += 1
                if N - len(lapsed) < _N_GATE:
                    n_drop += 1; by_month[mk][2] += 1

    print("\n" + "=" * 74)
    print("LEVEL-PERSISTENCE DIAGNOSTIC (live path) — would 'still holds today' bite?")
    print(f"{_STOCK_SET}, eval {_EVAL_FROM}..{_EVAL_TO}; tenkan/kumo only "
          "(brk_sma/brk_bol self-trim; chiko out of scope)")
    print("=" * 74)
    print(f"\n{'month':<9}{'N>=3 days':>11}{'w/ lapsed':>12}{'drop<N':>9}")
    for mk in sorted(by_month):
        f, lp, dr = by_month[mk]
        print(f"{mk:<9}{f:>11}{lp:>12}{dr:>9}")
    print(f"\n{'TOTAL':<9}{n_fires:>11}{n_lapsed:>12}{n_drop:>9}")
    pl = 100 * n_lapsed / n_fires if n_fires else 0.0
    pd = 100 * n_drop / n_fires if n_fires else 0.0
    print(f"\n{n_fires} N>=3 stock-days | {n_lapsed} ({pl:.1f}%) had >=1 lapsed level "
          f"constituent | {n_drop} ({pd:.1f}%) would DROP below N>=3.")
    print("lapses by sign: " + ", ".join(f"{k}={v}" for k, v in lapse_by_sign.items()))
    print(f"\nVERDICT: {pd:.1f}% of N>=3 stock-days drop below quorum under a persistence gate.")
    if pd >= 5.0:
        print("  >= 5% -> MATERIAL: escalate to operator for a strategy A/B + paired null "
              "(detector edits out of autonomous scope).")
    else:
        print("  < 5% -> NO-OP (cf. trend_score floor 0.7% / dSharpe 0.00). Resolve the "
              "brk_sma-vs-kumo/tenkan code inconsistency by fiat; do not run an A/B.")
    print("CAVEAT: single stock_set / ~1.4yr (sign_benchmark wiped → full FY2017-2025 "
          "diagnostic needs a rebench).")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
