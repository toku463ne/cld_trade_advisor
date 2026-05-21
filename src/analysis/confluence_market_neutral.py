"""Confluence market-neutral diagnostic — is the +3.3 Sharpe alpha or beta?

Production ConfluenceSignStrategy (10-sign bullish set, N>=3), FY2017 +
FY2019-2025. For each trade compute:
    raw_r = (exit-entry)/entry              (what the strategy books)
    beta  = cov(r_stock, r_N225)/var(r_N225) over the trailing _BETA_WIN bars
            ending at entry (look-ahead-safe)
    n225_r = N225 close-to-close return over the trade's exact hold window
    mn_r  = raw_r - beta * n225_r           (market-neutral / beta-hedged)

Report raw vs market-neutral Sharpe / mean_r per-FY and pooled. mn is a
DIAGNOSTIC of edge quality (long-only manual strategy can't cheaply short N225);
it answers how much of the headline Sharpe is genuine selection alpha vs being
long an up-market.

OUTCOME (2026-05-21): 62% of headline return is BETA, 38% alpha. Pooled Sharpe
+3.41 → market-neutral +1.31; mn mean_r +0.77%/trade t=1.39 (NOT significant);
avg β 0.73 (high-beta long book). Alpha is REGIME-INVERSE: biggest raw-Sharpe
years evaporate (FY2020 +4.33→−3.05, FY2023 +6.33→+0.54, FY2025 OOS +2.93→−0.95)
while bearish FY2024 IMPROVES (+6.49→+8.74, β 0.59 = real alpha masked by negative
beta). Strategy is a beta vehicle with conditional alpha only in non-bull
regimes, NOT a market-neutral alpha engine. See memory
project_confluence_market_neutral.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_market_neutral
"""
from __future__ import annotations

import datetime
import math
import statistics
import sys
from bisect import bisect_right

import numpy as np
from loguru import logger

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.exit_benchmark import FyConfig
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_BETA_WIN = 60          # trailing bars for beta
_FYS = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                 "classified2016")] + list(RS_FY_CONFIGS)


def _ret_series(cache: DataCache) -> tuple[list[datetime.date], np.ndarray]:
    dts, cls = [], []
    seen = set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d)
        dts.append(d); cls.append(b.close)
    cls = np.asarray(cls, dtype=float)
    rets = np.concatenate([[np.nan], np.diff(cls) / cls[:-1]])
    return dts, rets


def _close_map(cache: DataCache) -> tuple[list[datetime.date], np.ndarray]:
    dts, cls, seen = [], [], set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cls.append(b.close)
    return dts, np.asarray(cls, dtype=float)


def _sharpe(r) -> float:
    if len(r) < 2:
        return float("nan")
    sd = statistics.stdev(r)
    return statistics.mean(r) / sd * math.sqrt(252) if sd > 0 else float("nan")


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)

    # load fires once for the bullish set
    from collections import defaultdict

    from sqlalchemy import select

    from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
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

    per = {}   # fy -> (raw[], mn[], betas[])
    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            logger.warning("no cluster for {} ({}) — skip", cfg.label, cfg.stock_set)
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
                corr_maps.get(code, {}), zs_maps.get(code, {}),
                cfg.start, cfg.end, _N_GATE))
        results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)

        # N225 return series + close lookup
        n_dts, n_ret = _ret_series(n225)
        n_cdts, n_cls = _close_map(n225)
        n_didx = {d: i for i, d in enumerate(n_dts)}

        raw, mn, betas = [], [], []
        for r in results:
            c = caches.get(r.stock_code)
            if c is None:
                continue
            s_dts, s_ret = _ret_series(c)
            s_didx = {d: i for i, d in enumerate(s_dts)}
            # trailing beta ending the bar BEFORE entry
            ei = s_didx.get(r.entry_date)
            if ei is None or ei < _BETA_WIN:
                continue
            # align stock & n225 returns over the trailing window
            win_dts = s_dts[ei - _BETA_WIN:ei]
            rs = np.array([s_ret[s_didx[d]] for d in win_dts])
            rn = np.array([n_ret[n_didx[d]] for d in win_dts if d in n_didx])
            if len(rn) < 30 or len(rs) != len(win_dts):
                continue
            # re-align lengths (n225 may miss a date)
            common = [d for d in win_dts if d in n_didx]
            rs = np.array([s_ret[s_didx[d]] for d in common])
            rn = np.array([n_ret[n_didx[d]] for d in common])
            m = ~(np.isnan(rs) | np.isnan(rn))
            rs, rn = rs[m], rn[m]
            if len(rn) < 30 or rn.var() == 0:
                continue
            beta = float(np.cov(rs, rn)[0, 1] / rn.var())
            # N225 return over hold window [entry_date, exit_date], nearest prior close
            def _cls_at(d):
                i = bisect_right(n_cdts, d) - 1
                return n_cls[i] if i >= 0 else None
            ne, nx = _cls_at(r.entry_date), _cls_at(r.exit_date)
            if not ne or not nx:
                continue
            n225_r = (nx - ne) / ne
            raw.append(r.return_pct)
            mn.append(r.return_pct - beta * n225_r)
            betas.append(beta)
        per[cfg.label] = (raw, mn, betas)
        logger.info("  {}: {} trades, avg beta {:.2f}", cfg.label, len(raw),
                    statistics.mean(betas) if betas else float("nan"))

    # ── report ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 82)
    print("CONFLUENCE MARKET-NEUTRAL — raw vs (raw − β·N225) — bullish N>=3")
    print("=" * 82)
    print(f"{'FY':<9}{'n':>5}{'avgβ':>7}{'raw_Sh':>9}{'mn_Sh':>9}{'raw_mr%':>9}{'mn_mr%':>9}{'ΔSh':>8}")
    allraw, allmn, allbeta = [], [], []
    fy_dsh = []
    for cfg in _FYS:
        if cfg.label not in per:
            continue
        raw, mn, betas = per[cfg.label]
        if not raw:
            continue
        rs, ms = _sharpe(raw), _sharpe(mn)
        allraw += raw; allmn += mn; allbeta += betas
        d = ms - rs if not (math.isnan(rs) or math.isnan(ms)) else float("nan")
        if not math.isnan(d):
            fy_dsh.append(d)
        print(f"{cfg.label:<9}{len(raw):>5}{statistics.mean(betas):>7.2f}{rs:>9.2f}{ms:>9.2f}"
              f"{statistics.mean(raw)*100:>9.2f}{statistics.mean(mn)*100:>9.2f}{d:>8.2f}")
    print("-" * 82)
    print(f"{'POOL':<9}{len(allraw):>5}{statistics.mean(allbeta):>7.2f}"
          f"{_sharpe(allraw):>9.2f}{_sharpe(allmn):>9.2f}"
          f"{statistics.mean(allraw)*100:>9.2f}{statistics.mean(allmn)*100:>9.2f}"
          f"{_sharpe(allmn)-_sharpe(allraw):>8.2f}")

    # how much of mean return is beta?
    pooled_raw_mr = statistics.mean(allraw)
    pooled_mn_mr = statistics.mean(allmn)
    beta_share = (pooled_raw_mr - pooled_mn_mr) / pooled_raw_mr if pooled_raw_mr else float("nan")
    print(f"\nPooled mean_r: raw {pooled_raw_mr*100:+.2f}%  →  market-neutral {pooled_mn_mr*100:+.2f}%")
    print(f"Beta-attributable share of mean return: {beta_share*100:.0f}%   "
          f"(alpha share {100-beta_share*100:.0f}%)")
    # is market-neutral mean_r > 0 with a t-stat?
    a = np.asarray(allmn)
    t = a.mean() / (a.std(ddof=1) / math.sqrt(len(a))) if a.std(ddof=1) > 0 else float("nan")
    print(f"Market-neutral mean_r t-stat (per-trade, naive): {t:+.2f}  (n={len(a)})")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
