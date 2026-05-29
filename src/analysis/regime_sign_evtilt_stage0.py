"""RegimeSign EV-tilt Stage 0 — does the NEUTRAL-momentum EV trough exist? (read-only, diagnostic)

Backlog item 2 (`docs/analysis/regime_sign_improvement_backlog.md`). On Confluence, β-stripped EV is
NON-MONOTONE in N225 60-bar momentum — NEUTRAL is the weak spot (raw +0.52% / α +0.33% vs bullish
+3.31%/+1.20%, bearish +1.31%/+0.57%), and that weakness SURVIVES the beta-strip → it became the sole
backlog survivor (trim NEUTRAL-momentum entries). This probe asks whether the SAME trough exists for the
RegimeSign cohort (sign-ranked Kumo/ADX entries, a DIFFERENT entry population) before spending a Stage-1
pre-registration.

Method (mirrors `confluence_regime_pooling.py`, candidate level for max n / fill-order independence):
  - shipped RegimeSign candidate pool (`build_fy_candidates`), FY2019–2025, caches +90d for hold lookahead;
  - CAP-FREE ZsTpSl simulation → one trade per candidate (unbiased by slot contention);
  - per trade: N225 trailing-60-bar momentum at entry; trailing-60-bar β vs N225; N225 return over the hold;
    alpha = trade_return − β·N225_hold;
  - pool across FYs, GLOBAL terciles on the momentum, report per-tercile n / DR / raw mean_r / β / alpha.

Gate to escalate to Stage 1: NEUTRAL must be the (or tied-) weakest tercile on BOTH raw mean_r AND alpha
(the confluence shape), and neutral alpha should stay ≳ 0 (so it's a TRIM, not a SKIP). Read-only, no
deployment decision.
  PYTHONPATH=. uv run --env-file devenv python -m src.analysis.regime_sign_evtilt_stage0
"""
from __future__ import annotations

import datetime
import statistics
import sys
from collections import defaultdict

import numpy as np
from loguru import logger

import src.exit.exit_simulator as exsim
from src.analysis.regime_sign_backtest import (
    EXIT_RULE,
    RS_FY_CONFIGS,
    build_fy_candidates,
)
from src.analysis.regime_sign_oracle_ceiling_probe import _reload_caches
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_N225_MOM = 60
_BETA_WIN = 60


def _closes(cache):
    dts, cmap, seen = [], {}, set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cmap[d] = b.close
    dts.sort()
    return dts, cmap


def _n225_mom(n_dts, n_cmap, d):
    try:
        i = n_dts.index(d)
    except ValueError:
        return None
    if i < _N225_MOM:
        return None
    p0 = n_cmap[n_dts[i - _N225_MOM]]
    return n_cmap[d] / p0 - 1.0 if p0 else None


def run() -> None:
    orig_hi, orig_lo = exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR
    trades: list[dict] = []   # {mom, ret, beta, alpha}

    for cfg in RS_FY_CONFIGS:
        cs = build_fy_candidates(cfg)
        if not cs.candidates:
            continue
        codes = {c.stock_code for c in cs.candidates}
        caches = _reload_caches(codes, cfg.start, cfg.end)
        with get_session() as s:
            n225 = DataCache("^N225", "1d")
            n225.load(s,
                      datetime.datetime.combine(cfg.start - datetime.timedelta(days=200),
                                                datetime.time.min, tzinfo=datetime.timezone.utc),
                      datetime.datetime.combine(cfg.end + datetime.timedelta(days=90),
                                                datetime.time.max, tzinfo=datetime.timezone.utc))
        n_dts, n_cmap = _closes(n225)
        n_ret = {n_dts[i]: (n_cmap[n_dts[i]] / n_cmap[n_dts[i - 1]] - 1.0
                            if n_cmap[n_dts[i - 1]] else 0.0)
                 for i in range(1, len(n_dts))}

        # per-stock daily-return series for beta
        stock_ser = {}
        for code, c in caches.items():
            sdts, scmap = _closes(c)
            sret = {sdts[i]: (scmap[sdts[i]] / scmap[sdts[i - 1]] - 1.0
                              if scmap[sdts[i - 1]] else 0.0)
                    for i in range(1, len(sdts))}
            stock_ser[code] = (sdts, {d: i for i, d in enumerate(sdts)}, sret)

        # cap-free → one ZsTpSl trade per candidate
        cands = [c for c in cs.candidates if c.stock_code in caches]
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = 10**9, 10**9
        res = run_simulation(cands, EXIT_RULE, caches, cfg.end)
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = orig_hi, orig_lo

        def _beta(code, entry_date):
            info = stock_ser.get(code)
            if info is None:
                return None
            sdts, sidx, sret = info
            ei = sidx.get(entry_date)
            if ei is None or ei < _BETA_WIN:
                return None
            rs, rn = [], []
            for d in sdts[ei - _BETA_WIN:ei]:
                if d in sret and d in n_ret:
                    rs.append(sret[d]); rn.append(n_ret[d])
            if len(rn) < 30:
                return None
            var = float(np.var(rn))
            return float(np.cov(rs, rn)[0, 1] / var) if var > 0 else None

        def _n225_hold(entry_date, exit_date):
            if entry_date not in n_cmap or exit_date not in n_cmap:
                return None
            p0 = n_cmap[entry_date]
            return n_cmap[exit_date] / p0 - 1.0 if p0 else None

        for p in res:
            mom = _n225_mom(n_dts, n_cmap, p.entry_date)
            if mom is None:
                continue
            beta = _beta(p.stock_code, p.entry_date)
            hold = _n225_hold(p.entry_date, p.exit_date)
            ret = p.return_pct
            alpha = (ret - beta * hold) if (beta is not None and hold is not None) else None
            trades.append({"mom": mom, "ret": ret, "beta": beta, "alpha": alpha})
        logger.info("  {} done — {} cap-free trades", cfg.label, len(res))

    moms = sorted(t["mom"] for t in trades)
    n = len(moms)
    c1, c2 = moms[n // 3], moms[2 * n // 3]
    logger.info("global momentum terciles: bear ≤ {:+.2%} < neutral ≤ {:+.2%} < bull", c1, c2)

    def _bucket(m):
        return "bearish" if m <= c1 else ("neutral" if m <= c2 else "bullish")

    by = defaultdict(list)
    for t in trades:
        by[_bucket(t["mom"])].append(t)

    print("\n" + "=" * 92)
    print("REGIMESIGN EV-TILT STAGE 0 — β-stripped EV by N225 60-bar momentum tercile (cap-free)")
    print(f"(FY2019–2025, {n} trades; cutoffs bear ≤ {c1:+.2%} < neutral ≤ {c2:+.2%} < bull)")
    print("=" * 92)
    print(f"\n  {'tercile':<10}{'n':>6}{'DR(win%)':>10}{'raw mean_r':>12}"
          f"{'avg β':>8}{'alpha':>10}{'α DR':>8}")
    rows = {}
    for b in ("bearish", "neutral", "bullish"):
        ts = by[b]
        raw = statistics.mean(t["ret"] for t in ts)
        dr = sum(1 for t in ts if t["ret"] > 0) / len(ts)
        al = [t["alpha"] for t in ts if t["alpha"] is not None]
        bt = [t["beta"] for t in ts if t["beta"] is not None]
        amean = statistics.mean(al) if al else float("nan")
        adr = (sum(1 for a in al if a > 0) / len(al)) if al else float("nan")
        bmean = statistics.mean(bt) if bt else float("nan")
        rows[b] = (raw, amean)
        print(f"  {b:<10}{len(ts):>6}{dr*100:>9.1f}%{raw*100:>11.2f}%"
              f"{bmean:>8.2f}{amean*100:>9.2f}%{adr*100:>7.1f}%")

    print("\n" + "-" * 92)
    raw_min = min(rows, key=lambda b: rows[b][0])
    al_min = min(rows, key=lambda b: rows[b][1])
    neutral_raw_weakest = rows["neutral"][0] <= min(rows["bearish"][0], rows["bullish"][0]) + 1e-12
    neutral_al_weakest = rows["neutral"][1] <= min(rows["bearish"][1], rows["bullish"][1]) + 1e-12
    if neutral_raw_weakest and neutral_al_weakest:
        if rows["neutral"][1] > 0:
            v = ("PASS — NEUTRAL is weakest on BOTH raw and alpha, alpha still > 0 → TRIM candidate. "
                 "Escalate to Stage 1 (paired fill-order + phase null).")
        else:
            v = ("PARTIAL — NEUTRAL weakest on both but alpha ≤ 0 (would be a SKIP, not a trim). "
                 "Escalate cautiously.")
    else:
        v = (f"FAIL — the confluence NEUTRAL-trough shape does NOT replicate "
             f"(raw weakest = {raw_min}, alpha weakest = {al_min}). Do NOT build the tilt.")
    print("VERDICT:", v)
    print("-" * 92)


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
