"""Stage 0 — 指値/逆指値 entry vs market-at-open, on IDENTICAL confluence signals.

Operator hypothesis (2026-05-30): instead of filling at the next-day OPEN (market),
place a 指値 (buy-limit below) or 逆指値 (buy-stop above) good for the sign's validity
window. Limit → cheaper entry + skip names that gap away; validity → a few days to fill
if price comes back.

Risk being tested: ADVERSE SELECTION. A buy-limit below fills the names that dip and
misses the ones that run up (the best bullish-confluence outcomes). Does the better
fill price beat the missed winners?

Design — isolate ENTRY EXECUTION only:
  - One pool: production WINDOWED candidates (signal day T = entry_date).
  - Common exit for ALL modes: close at index(T)+1+H (H=20 ≈ median hold), so only the
    entry fill price/date differ. Non-fills = cash (no trade).
  - Daily-bar fill convention (no intraday look-ahead):
      MKT  : fill at open[T+1].                                    (production)
      LIM  : limit = close[T]; scan j in T+1..T+W: open[j]<=L -> fill open[j];
             elif low[j]<=L -> fill L. First touch. Else NO FILL.
      STOP : stop = close[T];  scan j in T+1..T+W: open[j]>=S -> fill open[j];
             elif high[j]>=S -> fill S. First touch. Else NO FILL.
  - W = 5 trading-day fill window (the dominant sign validity).

Reports per mode: fill rate, conditional-on-fill mean ret / DR / win%, all-candidate
mean (non-fill counts as 0 = cash), and the ADVERSE-SELECTION diagnostic — the baseline
(MKT) return of the candidates each conditional mode FAILED to fill.

Stage 0 only (per-trade). If a mode clearly beats MKT here, Stage 1 = portfolio paired
fill-order null. Read-only.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_limit_entry_stage0
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_benchmark import _FYS
from src.analysis.confluence_capacity_null import _closes
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.simulator.cache import DataCache

_WINDOWED = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
             "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_W = 5          # fill window (trading days), = dominant sign validity
_H = 20         # common forward-exit horizon (bars after T+1) ~ median hold


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


def _ohlc(cache):
    """Return (dates list, idx map, open/high/low/close arrays) deduped by date."""
    seen, dts, o, h, l, c = set(), [], [], [], [], []
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d)
        o.append(b.open); h.append(b.high); l.append(b.low); c.append(b.close)
    idx = {d: i for i, d in enumerate(dts)}
    return dts, idx, np.array(o), np.array(h), np.array(l), np.array(c)


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    cbt._VALID_BARS = dict(_WINDOWED)
    fires = _fires(_WINDOWED)

    # per-mode accumulators: list of (ret, filled_bool, baseline_ret)
    rows: dict[str, list] = {"MKT": [], "LIM": [], "STOP": []}

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
        se = cfg.end + datetime.timedelta(days=90)   # +90 for H-bar forward exit
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
        ohlc = {code: _ohlc(c) for code, c in caches.items()}

        pool = []
        for code in caches:
            pool += cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}),
                cfg.start, cfg.end, _N_GATE)

        for cand in pool:
            dts, idx, o, h, l, c = ohlc[cand.stock_code]
            t = idx.get(cand.entry_date)
            if t is None or t + 1 >= len(dts):
                continue
            exit_i = min(t + 1 + _H, len(dts) - 1)
            exit_px = c[exit_i]
            base_fill = o[t + 1]                         # MKT fill = T+1 open
            base_ret = exit_px / base_fill - 1.0
            rows["MKT"].append((base_ret, True, base_ret))

            limit = c[t]                                  # 指値 at signal close
            stop  = c[t]                                  # 逆指値 at signal close
            lim_fill = stop_fill = None
            for j in range(t + 1, min(t + 1 + _W, len(dts))):
                if lim_fill is None:
                    if o[j] <= limit:
                        lim_fill = o[j]
                    elif l[j] <= limit:
                        lim_fill = limit
                if stop_fill is None:
                    if o[j] >= stop:
                        stop_fill = o[j]
                    elif h[j] >= stop:
                        stop_fill = stop
                if lim_fill is not None and stop_fill is not None:
                    break
            rows["LIM"].append(
                (exit_px / lim_fill - 1.0 if lim_fill else 0.0, lim_fill is not None, base_ret))
            rows["STOP"].append(
                (exit_px / stop_fill - 1.0 if stop_fill else 0.0, stop_fill is not None, base_ret))
        logger.info("  {} done ({} candidates)", cfg.label, len(pool))

    print("\n" + "=" * 92)
    print(f"指値/逆指値 ENTRY vs MARKET-AT-OPEN — Stage 0, identical signals, "
          f"common +{_H}-bar exit, {_W}d fill window")
    print("=" * 92)
    print(f"\n{'mode':<26}{'fill%':>7}{'cond mean':>11}{'cond DR':>9}{'cond win%':>10}"
          f"{'all mean(cash=0)':>18}")
    n_tot = len(rows["MKT"])
    for mode, lbl in [("MKT", "MKT  open[T+1] (prod)"),
                      ("LIM", "LIM  指値 @ close[T]"),
                      ("STOP", "STOP 逆指値 @ close[T]")]:
        rs = rows[mode]
        filled = [r for r, f, _ in rs if f]
        fill_rate = len(filled) / len(rs) if rs else 0.0
        cond_mean = float(np.mean(filled)) if filled else float("nan")
        cond_dr = float(np.mean([1 for x in filled if x > 0]) ) if filled else float("nan")
        cond_dr = (sum(1 for x in filled if x > 0) / len(filled)) if filled else float("nan")
        all_mean = float(np.mean([r for r, _, _ in rs])) if rs else float("nan")
        print(f"{lbl:<26}{fill_rate*100:>6.0f}%{cond_mean*100:>+10.2f}%"
              f"{cond_dr*100:>8.0f}%{cond_dr*100:>9.0f}%{all_mean*100:>+17.2f}%")

    print("\n[ADVERSE-SELECTION diagnostic] baseline (MKT) return of candidates each "
          "conditional mode FAILED to fill:")
    for mode in ("LIM", "STOP"):
        miss = [b for r, f, b in rows[mode] if not f]
        fill = [b for r, f, b in rows[mode] if f]
        if miss:
            print(f"  {mode:<5} non-fills n={len(miss):<5} mean baseline ret "
                  f"{np.mean(miss)*100:>+6.2f}%   ||  filled n={len(fill):<5} "
                  f"mean baseline ret {np.mean(fill)*100:>+6.2f}%   "
                  f"(miss > fill ⇒ the order skipped the WINNERS)")
    print(f"\n  total candidates: {n_tot}.  Stage 0 only (per-trade, common exit, no slot "
          f"cap). A mode must clearly beat MKT's all-candidate mean AND not be a pure "
          f"adverse-selection artifact before a portfolio paired null is warranted.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
