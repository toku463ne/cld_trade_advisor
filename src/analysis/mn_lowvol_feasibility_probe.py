"""MN-low-vol / BAB breadth/cost/borrow feasibility probe (read-only, advisory).

Last live thread of the short-selling sleeve after MN-Value REJECT
(docs/analysis/20260530_short_sleeve_map.md, [[project_short_sleeve_map]]). MN-Value died on
BORROWABILITY: the value premium lives in mid-caps whose short leg is unborrowable; the borrowable
large-caps carry no premium. Low-vol/BAB is the one candidate that wall need not kill — its short
leg is HIGH-β / HIGH-VOL names, which is NOT a size bucket and could include borrowable large-caps.

Same engine as mn_value_feasibility_probe; only the signal changes (price-derived, no fundamentals):
  • BAB  : long LOW trailing-252d β vs TOPIX / short HIGH β.
  • LOWVOL: long LOW trailing-252d return vol / short HIGH vol.
Dollar-neutral, equal-weight, monthly. Reports the same breadth sweep, cost+borrow sweep,
BORROWABILITY% of the short leg, residual β, per-FY, and a block-bootstrap time-CI — on the 225
cohort (borrowable) and the wide mid-cap tier.

Two reads that matter here:
  (1) BORROWABILITY% of the short (high-β/high-vol) leg — the map's open question.
  (2) RESIDUAL β — a naive long-low/short-high book is net SHORT beta; over FY2018–26 (mostly rising)
      a net-short-beta book is penalized, so a positive net Sharpe DESPITE negative resid β is a
      stronger (not weaker) low-vol-alpha signal. Read Sharpe and resid β together.

Returns are price-only (adj_close); dividends omitted bias low-vol DOWN (long low-vol names yield more
than short high-vol names) → conservative. Read-only.
Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.mn_lowvol_feasibility_probe
"""
from __future__ import annotations

import datetime
import sys

import numpy as np
from loguru import logger

import src.analysis.value_tilt_discovery_probe as vd
from src.analysis.mn_value_feasibility_probe import _report
from src.analysis.value_tilt_discovery_probe import _load, _load_tier_local, _rebalance_indices

_L = 252          # trailing window for β / vol
_MINPTS = 200


def _fy_label(d: datetime.date) -> int:
    return d.year + 1 if d.month >= 4 else d.year


def _build_months_lowvol(cal, row_of, adj, topix_adj, cohort_rows, tier_rows, signal: str):
    """Month tuples in the mn_value layout (score = long-low-risk), score from price β/vol."""
    rebs = _rebalance_indices(cal)
    ret = np.full_like(adj, np.nan)
    ret[:, 1:] = adj[:, 1:] / adj[:, :-1] - 1.0
    txret = np.full(len(topix_adj), np.nan)
    txret[1:] = topix_adj[1:] / topix_adj[:-1] - 1.0
    uni_rows = cohort_rows | tier_rows
    months = []
    for k in range(len(rebs) - 1):
        ti, tn = rebs[k], rebs[k + 1]
        if ti < _L:
            continue
        frac = (cal[tn] - cal[ti]).days / 365.0
        txw = txret[ti - _L:ti]
        txm = np.isfinite(txw)
        txwm = txw[txm]
        txvar = txwm.var()
        rrows, scores, fwds, cohm, tierm, borrow = [], [], [], [], [], []
        for ri in uni_rows:
            a_t, a_n = adj[ri, ti], adj[ri, tn]
            if not (a_t > 0) or not (a_n > 0):
                continue
            w = ret[ri, ti - _L:ti]
            m = np.isfinite(w) & txm
            if m.sum() < _MINPTS:
                continue
            wm = w[m]
            if signal == "bab":
                if txvar <= 0:
                    continue
                b = float(np.cov(wm, txw[m])[0, 1] / txw[m].var()) if txw[m].var() > 0 else np.nan
                risk = b
            else:                                    # lowvol
                risk = float(wm.std())
            if not np.isfinite(risk):
                continue
            rrows.append(ri); scores.append(-risk)   # long = highest score = lowest risk
            fwds.append(a_n / a_t - 1.0)             # price-only total return
            cohm.append(ri in cohort_rows); tierm.append(ri in tier_rows)
            borrow.append(ri in cohort_rows)
        if len(rrows) < 50:
            continue
        months.append((ti, tn, _fy_label(cal[ti]), np.array(rrows), np.array(scores),
                       np.array(fwds), np.array(cohm), np.array(tierm),
                       np.array(borrow), frac))
    return months


def run() -> None:
    cal, col_of, codes, row_of, adj, rawc, topix_adj, funds, cohort_rows, _turn = _load()
    vd._CAL_REF = cal
    tier_rows = {row_of[c] for c in _load_tier_local() if c in row_of}

    for signal, name in (("bab", "BAB (β-ranked)"), ("lowvol", "LOW-VOL (σ-ranked)")):
        logger.info("building monthly {} snapshots…", name)
        months = _build_months_lowvol(cal, row_of, adj, topix_adj, cohort_rows, tier_rows, signal)
        if len(months) < 12:
            logger.warning("{}: too few rebalances", name); continue
        logger.info("{}: {} rebalances ({}–{})", name, len(months),
                    cal[months[0][0]], cal[months[-1][0]])
        print("\n\n############################  SIGNAL = " + name + "  ############################")
        _report(months, cal, topix_adj, 6, f"{name} — 225 LARGE-CAP COHORT (both legs borrowable)")
        _report(months, cal, topix_adj, 7, f"{name} — WIDE MID-CAP TIER (short partly unborrowable)")

    print("\n" + "=" * 96)
    print("HOW TO READ — short sleeve final gate")
    print("=" * 96)
    print("• If on the 225 cohort (borrowable) K=6 net Sharpe is clearly >0 with time-CI lower bound\n"
          "  >0 → low-vol is the first short-sleeve book that clears the borrowability wall → pre-reg.\n"
          "• If the short (high-β/high-vol) leg's Borrow% on the WIDE tier is high AND K=6 net >0 →\n"
          "  a borrowable wide book is realizable (unlike MN-Value's 0%). Else the size/borrow wall\n"
          "  is universal across factor short legs → the entire Role-A short sleeve is CLOSED at ¥2M.\n"
          "• Read Sharpe WITH resid β: positive net Sharpe + negative resid β over a rising sample =\n"
          "  genuine low-vol alpha (the embedded short-beta was a headwind, not the source).")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
