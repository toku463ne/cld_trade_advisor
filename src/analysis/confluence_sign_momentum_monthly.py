"""Monthly trailing sign winner/loser list — walk-forward predictive test (read-only).

Operator follow-up (2026-06-01): the trailing winner/loser sign-list was rejected on YEARLY
buckets (labels flip year-to-year, project_monthly_sign_winner_list_reject). Operator clarifies
the list would be refreshed MONTHLY. This tests whether monthly refresh changes the verdict.

Two ways monthly could differ: (a) faster adaptation could catch regime turns; (b) far less
data per update could make it noisier. This script measures which dominates, using the EXACT
rule walk-forward across the full FY2019-FY2025 filled book.

Pools all canonical 6-slot fills (beta-stripped alpha from confluence_2019_beta_strip._build_fy),
sorts by entry_date, then for each fill computes a look-ahead-safe TRAILING SIGN SCORE:
    score(fill) = mean over signs s valid at entry of [ mean alpha of all PRIOR fills (strictly
                  earlier entry_date, within trailing window W) that had s valid ]
This is precisely "prioritize signs that have been winning recently / avoid recent losers."

Tests:
  0. Data density: median trades per sign per calendar month (can a monthly list even estimate?).
  1. Lag-1 autocorrelation of per-sign monthly alpha (does last month predict this month?).
  2. Walk-forward predictive power of the trailing score at W in {21,63,126,252} trading days
     (~1,3,6,12 months): Spearman(score, realized alpha) + top-half vs bottom-half cohort alpha.
     A working monthly list needs POSITIVE Spearman / top>bottom at the short windows.

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_sign_momentum_monthly
"""
from __future__ import annotations

import statistics
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

from src.analysis.confluence_2019_beta_strip import _BULLISH, _build_fy
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS
from src.data.db import get_session
import src.analysis.confluence_strategy_backtest as cbt

_WINDOWS = [21, 63, 126, 252]   # trailing trading-day windows ~ 1/3/6/12 months


def _spearman(xs, ys):
    if len(xs) < 8:
        return float("nan")
    rx = np.argsort(np.argsort(xs)).astype(float)
    ry = np.argsort(np.argsort(ys)).astype(float)
    rx -= rx.mean(); ry -= ry.mean()
    d = np.sqrt((rx**2).sum() * (ry**2).sum())
    return float((rx * ry).sum() / d) if d else float("nan")


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    recs = []
    for cfg in RS_FY_CONFIGS:
        recs.extend(_build_fy(cfg, fires))
    recs.sort(key=lambda x: x["entry_date"])
    n = len(recs)

    print("\n" + "=" * 82)
    print(f"MONTHLY TRAILING SIGN-LIST — WALK-FORWARD TEST  (FY2019-FY2025, n={n} fills)")
    print("=" * 82)

    # 0. density
    permonth = defaultdict(lambda: defaultdict(int))
    for x in recs:
        ym = (x["entry_date"].year, x["entry_date"].month)
        for sg in x["signs"]:
            permonth[ym][sg] += 1
    months = sorted(permonth)
    total_per_month = [sum(permonth[m].values()) for m in months]
    persign_counts = [permonth[m][sg] for m in months for sg in _BULLISH if permonth[m][sg] > 0]
    print(f"\n0. DENSITY:  {len(months)} active months, median {statistics.median(total_per_month):.0f} "
          f"sign-tagged fills/month total; median {statistics.median(persign_counts):.0f} fills "
          f"per (sign,month) when present.")
    print("   => a monthly per-sign win-rate is estimated from ~1-2 trades. That is noise.")

    # 1. lag-1 autocorr of per-sign monthly alpha
    sign_month_alpha = defaultdict(dict)
    for sg in _BULLISH:
        for m in months:
            xs = [x["alpha"] for x in recs
                  if (x["entry_date"].year, x["entry_date"].month) == m and sg in x["signs"]]
            if xs:
                sign_month_alpha[sg][m] = statistics.mean(xs)
    ac = []
    for sg in _BULLISH:
        series = [sign_month_alpha[sg][m] for m in months if m in sign_month_alpha[sg]]
        if len(series) >= 10:
            a = np.array(series)
            r = np.corrcoef(a[:-1], a[1:])[0, 1]
            if r == r:
                ac.append(r)
    print(f"\n1. LAG-1 AUTOCORRELATION of per-sign monthly alpha (n_signs={len(ac)}):")
    print(f"   mean {statistics.mean(ac):+.3f}   median {statistics.median(ac):+.3f}")
    print("   => ~0 or negative means last month does NOT predict this month (no momentum to ride).")

    # 2. walk-forward predictive power of trailing sign score
    print(f"\n2. WALK-FORWARD: does the trailing sign score predict realized alpha?")
    print(f"   {'window':<12}{'n_scored':>9}{'Spearman':>10}{'top-half α':>12}{'bot-half α':>12}")
    for W in _WINDOWS:
        scores, reals = [], []
        for i, f in enumerate(recs):
            ed = f["entry_date"]
            persign_prior = {}
            for sg in f["signs"]:
                prior = [recs[j]["alpha"] for j in range(i)
                         if sg in recs[j]["signs"]
                         and 0 < (ed - recs[j]["entry_date"]).days <= int(W * 1.5)]
                if prior:
                    persign_prior[sg] = statistics.mean(prior)
            if persign_prior:
                scores.append(statistics.mean(persign_prior.values()))
                reals.append(f["alpha"])
        if len(scores) < 10:
            print(f"   {f'~{W}d':<12}{len(scores):>9}{'n<10':>10}")
            continue
        sp = _spearman(scores, reals)
        order = np.argsort(scores)
        half = len(scores) // 2
        bot = [reals[k] for k in order[:half]]
        top = [reals[k] for k in order[-half:]]
        print(f"   {f'~{W}d':<12}{len(scores):>9}{sp:>+10.3f}"
              f"{statistics.mean(top)*100:>+11.2f}%{statistics.mean(bot)*100:>+11.2f}%")
    print("\n   VERDICT: positive Spearman AND top-half > bot-half at the SHORT windows would")
    print("   support a monthly list. ~0/negative Spearman or top<=bot = the list is noise:")
    print("   refreshing monthly just fits the last 1-2 trades/sign and whipsaws faster.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
