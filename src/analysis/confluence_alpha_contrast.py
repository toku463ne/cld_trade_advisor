"""FY2024/FY2022 vs FY2019/FY2021 — what made down-market alpha positive? (read-only)

The market-neutral decomposition (confluence_market_neutral.py) showed the book is
89% beta with ~zero pooled alpha (t=0.30), BUT the alpha is regime-inverse and the
years split sharply in down/flat markets:
  POS group: FY2022 (N225 +1.4%, alpha +2.24%), FY2024 (N225 -10.5%, alpha +1.97%)
  NEG group: FY2019 (N225 -12.1%, alpha -0.62%), FY2021 (N225 -5.3%, alpha -0.93%)
All four are weak-market years, so beta doesn't separate them — something in the
SELECTION delivered alpha in POS and anti-alpha in NEG. This probe asks: is that
difference visible AT ENTRY (could gate it) and CONSISTENT across both years in each
group (not a single-year fluke)?

Per trade: alpha = raw_r - beta*n225_r (trailing-60-bar beta, look-ahead-safe),
corr_mode, and N225 60-bar momentum at entry. Compare POS vs NEG on:
  - alpha by corr_mode (is POS alpha concentrated in low-corr "genuine alpha"?)
  - alpha by N225 60-bar momentum tercile at entry
  - per-FY breakdown for each cut (consistency gate: both POS years must agree, both
    NEG years must agree, else it's regime-timing noise not a feature).

n-thin caution: ~50 trades/FY, 2 FYs/group; any sub-split thins fast. Descriptive
only — a real gate would need the per-FY consistency to hold AND survive OOS, which
at this n is unlikely (cf. the regime_sign cohort rejects). If POS/NEG don't separate
on any AT-ENTRY feature consistently, the conclusion is: the down-market alpha is
regime-timing luck, not a harvestable signal -> universe expansion is the only path.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_alpha_contrast
"""
from __future__ import annotations

import datetime
import statistics
import sys
from bisect import bisect_right
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_market_neutral import _close_map, _ret_series
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_BETA_WIN = 60
_MOM_WIN = 60
_GROUP = {"FY2019": "NEG", "FY2021": "NEG", "FY2022": "POS", "FY2024": "POS"}
_FYS = [c for c in RS_FY_CONFIGS if c.label in _GROUP]


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

    recs = []   # dict per trade
    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 120)
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
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)

        n_dts, n_ret = _ret_series(n225)
        n_cdts, n_cls = _close_map(n225)
        n_didx = {d: i for i, d in enumerate(n_dts)}

        def _cls_at(d):
            i = bisect_right(n_cdts, d) - 1
            return n_cls[i] if i >= 0 else None

        for r in results:
            c = caches.get(r.stock_code)
            if c is None:
                continue
            s_dts, s_ret = _ret_series(c)
            s_didx = {d: i for i, d in enumerate(s_dts)}
            ei = s_didx.get(r.entry_date)
            if ei is None or ei < _BETA_WIN:
                continue
            common = [d for d in s_dts[ei - _BETA_WIN:ei] if d in n_didx]
            rs = np.array([s_ret[s_didx[d]] for d in common])
            rn = np.array([n_ret[n_didx[d]] for d in common])
            m = ~(np.isnan(rs) | np.isnan(rn))
            rs, rn = rs[m], rn[m]
            if len(rn) < 30 or rn.var() == 0:
                continue
            beta = float(np.cov(rs, rn)[0, 1] / rn.var())
            ne, nx = _cls_at(r.entry_date), _cls_at(r.exit_date)
            if not ne or not nx:
                continue
            alpha = r.return_pct - beta * (nx - ne) / ne
            # N225 60-bar momentum ending the bar before entry
            nei = bisect_right(n_cdts, r.entry_date) - 1
            mom = (n_cls[nei] / n_cls[nei - _MOM_WIN] - 1.0) if nei >= _MOM_WIN else np.nan
            recs.append(dict(fy=cfg.label, grp=_GROUP[cfg.label], alpha=alpha,
                             beta=beta, corr=r.corr_mode, mom=mom))
        logger.info("  {} done ({} trades)", cfg.label, sum(1 for x in recs if x["fy"] == cfg.label))

    print("\n" + "=" * 86)
    print("ALPHA CONTRAST — POS (FY2022/FY2024) vs NEG (FY2019/FY2021), down/flat-market years")
    print("=" * 86)

    def _amean(rs):
        a = [x["alpha"] for x in rs]
        return (statistics.mean(a) * 100, len(a)) if a else (float("nan"), 0)

    # A. per-FY sanity
    print(f"\nA. per-FY: {'FY':<8}{'grp':>5}{'n':>5}{'avg alpha':>11}{'avg beta':>10}"
          f"{'%high':>7}{'%mid':>7}{'%low':>7}")
    for cfg in _FYS:
        rs = [x for x in recs if x["fy"] == cfg.label]
        if not rs:
            continue
        am, n = _amean(rs)
        cm = [x["corr"] for x in rs]
        print(f"         {cfg.label:<8}{_GROUP[cfg.label]:>5}{n:>5}{am:>+10.2f}%"
              f"{statistics.mean([x['beta'] for x in rs]):>10.2f}"
              f"{100*cm.count('high')/n:>6.0f}%{100*cm.count('mid')/n:>6.0f}%{100*cm.count('low')/n:>6.0f}%")

    # B. alpha by corr_mode, per group + per FY (consistency)
    print(f"\nB. alpha by corr_mode (mean alpha% | n):  [consistency = both FYs in a group agree]")
    print(f"   {'corr':<8}{'POS pooled':>14}{'  FY2022':>12}{'  FY2024':>12}   "
          f"{'NEG pooled':>13}{'  FY2019':>12}{'  FY2021':>12}")
    for cm in ("high", "mid", "low"):
        cells = []
        for grp, fys in [("POS", ["FY2022", "FY2024"]), ("NEG", ["FY2019", "FY2021"])]:
            pooled = _amean([x for x in recs if x["grp"] == grp and x["corr"] == cm])
            cells.append(f"{pooled[0]:>+8.2f}% ({pooled[1]:>2})")
            for fy in fys:
                v = _amean([x for x in recs if x["fy"] == fy and x["corr"] == cm])
                cells.append(f"{v[0]:>+7.2f}%({v[1]:>2})")
        print(f"   {cm:<8}{cells[0]:>14}{cells[1]:>12}{cells[2]:>12}   "
              f"{cells[3]:>13}{cells[4]:>12}{cells[5]:>12}")

    # C. alpha by N225 60-bar momentum tercile at entry
    moms = np.array([x["mom"] for x in recs if x["mom"] == x["mom"]])
    q1, q2 = np.percentile(moms, [33.33, 66.67])
    print(f"\nC. alpha by N225 60-bar momentum at entry (terciles {q1*100:+.1f}%/{q2*100:+.1f}%):")
    print(f"   {'mom bucket':<14}{'POS pooled':>14}{'  FY2022':>12}{'  FY2024':>12}   "
          f"{'NEG pooled':>13}{'  FY2019':>12}{'  FY2021':>12}")
    for lab, lo, hi in [("bearish", -1e9, q1), ("neutral", q1, q2), ("bullish", q2, 1e9)]:
        cells = []
        for grp, fys in [("POS", ["FY2022", "FY2024"]), ("NEG", ["FY2019", "FY2021"])]:
            sel = lambda xs: [x for x in xs if x["mom"] == x["mom"] and lo < x["mom"] <= hi]
            pooled = _amean(sel([x for x in recs if x["grp"] == grp]))
            cells.append(f"{pooled[0]:>+8.2f}% ({pooled[1]:>2})")
            for fy in fys:
                v = _amean(sel([x for x in recs if x["fy"] == fy]))
                cells.append(f"{v[0]:>+7.2f}%({v[1]:>2})")
        print(f"   {lab:<14}{cells[0]:>14}{cells[1]:>12}{cells[2]:>12}   "
              f"{cells[3]:>13}{cells[4]:>12}{cells[5]:>12}")

    print("\n  VERDICT logic: a harvestable feature needs POS > NEG on the SAME cut in BOTH "
          "years of each group.\n  If the separating cut flips sign across the two FYs within a "
          "group, it's regime-timing, not a gate.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
