"""Regime-conditional pooling across parallel-world fills.

Companion to confluence_slot_order.py / confluence_start_phase_null.py. Those
showed the 4-slot book's annual outcome is dominated by regime-timing variance:
different fill orders / start phases admit different candidate SUBSETS of the
fixed ~1,200/FY pool, so each parallel world trades a different regime mix.

Operator's insight: parallel worlds aren't "the same trades reordered" — they
enter on DIFFERENT days, hence under DIFFERENT regimes. For OVERALL significance
that's just a wide null (you can't out-sample the fixed historical regime
sequence). But for REGIME-CONDITIONAL behaviour it is the legitimate way to get
more samples: the UNION of trades admitted across many worlds covers far more
DISTINCT candidates per regime bucket than the single chronological path's
handful.

This pools admitted trades over (start offset x within-day shuffle) worlds, bins
each by the N225 trend regime at its entry (trailing-60-bar momentum terciles,
global cut), and reports per-regime DR / mean per-trade return / EV with the
DISTINCT-candidate count, vs the single chronological path's per-bucket n. It
answers "how does the book behave on bear-entry vs bull-entry trades" with real
n, NOT "is the overall Sharpe significant" (it isn't, per the nulls). A second
pass beta-strips each trade (alpha = return − β·N225_over_hold, trailing-60bar β)
to ask whether a per-regime EV gap is alpha or just missing beta.

OUTCOME (2026-05-23, 161 worlds/FY = 1 chronological + 4 offsets x 40 shuffles):
  POOLING WORKS: 4,656 distinct trades vs 284 on the single chronological path
  (~16x more per regime bucket, ~1,550 each vs ~100). Per-regime EV is now
  readable. Shape is NON-MONOTONIC in N225 trend — the weak spot is the MIDDLE,
  not the bear:
    bearish (<=-0.1%):  DR 57.0%  raw +1.31%  | alpha +0.57% (β 0.77, DR 53.2%)
    neutral (..+8.1%):  DR 53.8%  raw +0.52%  | alpha +0.33% (β 0.82, DR 52.1%)
    bullish (>+8.1%):   DR 63.7%  raw +3.31%  | alpha +1.20% (β 0.72, DR 57.3%)
  DR > 50% in all three (positive edge everywhere). Raw favors up-trends = the
  beta face (β share 64% in bullish). NEUTRAL IS WORST ON BOTH RAW AND ALPHA →
  the weakness SURVIVES beta-strip = genuine signal-quality regime dependence,
  the session's first real sizing-tilt candidate. BUT caveats: (1) the alpha gap
  is modest and neutral alpha is still POSITIVE (+0.33%, DR 52%) → "trim", not
  "skip"; (2) a per-regime sizing tilt is still a market-timing rule and must
  clear the SAME fill-order/phase null at the PORTFOLIO level that every
  selection rule failed — per-trade alpha signal != portfolio sizing edge;
  (3) this conditions on LOCAL entry momentum, a DIFFERENT axis than the FY-level
  regime-inverse alpha in project_confluence_market_neutral (do not conflate).

STOCK-CHOP CUT (same run, binned by the STOCK's own ADX14 at entry — operator's
"avoid sideways/choppy stocks" idea): REFUTED, and non-monotonic again.
  choppy (avg ADX 13): DR 58.8%  raw +1.71%  alpha +0.67%   <- 2nd best, NOT bad
  mid    (avg ADX 18): DR 56.3%  raw +1.42%  alpha +0.45%   <- the weak spot
  trending(avg ADX 28):DR 59.3%  raw +2.01%  alpha +0.98%   <- best
  Filtering out choppy stocks would DELETE a winning cohort. "Middle is mush"
  shape (same as the N225-regime cut): bullish set mixes reversal signs (shine in
  low-ADX) + breakout signs (shine in high-ADX); mid-ADX serves neither. Only
  hinted tilt is "trim mid-ADX" — opposite of the proposal, modest (all positive),
  and still a selection rule facing the portfolio null. Consistent with the
  2026-05-19 trend_score no-op (the >=3-bullish gate is already trend-aware).
  See project_confluence_phase_regime.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_regime_pooling
"""
from __future__ import annotations

import datetime
import random
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.exit_benchmark import FyConfig, _add_adx
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_N225_MOM = 60          # trailing bars for N225 trend regime
_BETA_WIN = 60          # trailing bars for per-trade beta (same as market_neutral)
_OFFSETS = [0, 10, 20, 30]
_K_INNER = 40           # within-day shuffles per offset  -> 160 worlds/FY
_FYS = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                 "classified2016"),
        FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)


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
    """Trailing-60-bar N225 return at date d (regime proxy); None if unavailable."""
    try:
        i = n_dts.index(d)
    except ValueError:
        return None
    if i < _N225_MOM:
        return None
    p0 = n_cmap[n_dts[i - _N225_MOM]]
    return n_cmap[d] / p0 - 1.0 if p0 else None


def _trade_ret(p):
    return p.return_pct   # confluence entries are all long


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

    # pooled admissions: (code, entry_date) -> {"mom":, "ret":, "n_worlds":}
    pooled: dict[tuple, dict] = {}
    base_trades: list[tuple] = []      # single chronological path (offset 0 deterministic)

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
                    _add_adx(c)   # populate ADX14 (ZsTpSl exit doesn't, so do it here)
                    caches[code] = c
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        n_dts, n_cmap = _closes(n225)

        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        # ── beta/alpha structures: daily returns by date for N225 and each stock
        n_ret = {n_dts[i]: (n_cmap[n_dts[i]] / n_cmap[n_dts[i - 1]] - 1.0
                            if n_cmap[n_dts[i - 1]] else 0.0)
                 for i in range(1, len(n_dts))}
        stock_ser = {}
        for code, c in caches.items():
            sdts, scmap = _closes(c)
            sret = {sdts[i]: (scmap[sdts[i]] / scmap[sdts[i - 1]] - 1.0
                              if scmap[sdts[i - 1]] else 0.0)
                    for i in range(1, len(sdts))}
            stock_ser[code] = (sdts, {d: i for i, d in enumerate(sdts)}, sret)

        # stock ADX14 at each date (last bar of day); 0.0/NaN warmup -> missing
        stock_adx = {}
        for code, c in caches.items():
            m = {}
            for b in c.bars:
                a = b.indicators.get("ADX14")
                if a and a == a:   # excludes 0.0 warmup and NaN (nan != nan)
                    m[b.dt.date()] = a
            stock_adx[code] = m

        def _alpha_of(p):
            """(alpha, beta) = (return − β·N225_hold, trailing-60bar β); None if thin."""
            info = stock_ser.get(p.stock_code)
            if info is None:
                return None, None
            sdts, sidx, sret = info
            ei = sidx.get(p.entry_date)
            if ei is None or ei < _BETA_WIN:
                return None, None
            rs, rn = [], []
            for d in sdts[ei - _BETA_WIN:ei]:
                if d in sret and d in n_ret:
                    rs.append(sret[d]); rn.append(n_ret[d])
            if len(rn) < 30:
                return None, None
            rn = np.asarray(rn); rs = np.asarray(rs)
            if rn.var() == 0 or p.entry_date not in n_cmap or p.exit_date not in n_cmap:
                return None, None
            beta = float(np.cov(rs, rn)[0, 1] / rn.var())
            m = n_cmap[p.exit_date] / n_cmap[p.entry_date] - 1.0
            return p.return_pct - beta * m, beta

        def _record(results, into_base=False):
            for p in results:
                key = (p.stock_code, p.entry_date)
                mom = _n225_mom(n_dts, n_cmap, p.entry_date)
                if mom is None:
                    continue
                if key not in pooled:
                    a, b = _alpha_of(p)
                    pooled[key] = {"mom": mom, "ret": _trade_ret(p),
                                   "alpha": a, "beta": b,
                                   "adx": stock_adx.get(p.stock_code, {}).get(p.entry_date),
                                   "n_worlds": 0}
                pooled[key]["n_worlds"] += 1
                if into_base:
                    base_trades.append(key)

        # single chronological path
        _record(run_simulation(sorted(cands, key=lambda c: c.entry_date),
                               cbt._EXIT_RULE, caches, cfg.end), into_base=True)
        # parallel worlds: offsets x within-day shuffles
        for o in _OFFSETS:
            eff_start = cal[o] if o < len(cal) else cal[-1]
            pool_o = [c for c in cands if c.entry_date >= eff_start]
            for k in range(_K_INNER):
                rng = random.Random(o * 10_000 + k)
                shuf = pool_o[:]
                rng.shuffle(shuf)
                _record(run_simulation(shuf, cbt._EXIT_RULE, caches, cfg.end))
        logger.info("  {} done ({} candidates, {} distinct pooled so far)",
                    cfg.label, len(cands), len(pooled))

    # global terciles of N225 trend momentum over DISTINCT admitted trades
    moms = np.array([v["mom"] for v in pooled.values()])
    t1, t2 = np.percentile(moms, [33.33, 66.67])

    def _regime(m):
        return "bearish" if m <= t1 else ("neutral" if m <= t2 else "bullish")

    buckets = defaultdict(list)              # regime -> list of distinct trade returns
    for v in pooled.values():
        buckets[_regime(v["mom"])].append(v["ret"])
    base_buckets = defaultdict(int)          # single-path distinct count per regime
    for key in set(base_trades):
        base_buckets[_regime(pooled[key]["mom"])] += 1

    print("\n" + "=" * 82)
    print("REGIME-CONDITIONAL POOLING — distinct trades across {} worlds/FY, "
          "binned by N225 trend".format(1 + len(_OFFSETS) * _K_INNER))
    print(f"N225 trailing-{_N225_MOM}bar momentum terciles: "
          f"bearish <= {t1*100:+.1f}% < neutral <= {t2*100:+.1f}% < bullish")
    print("=" * 82)
    print(f"\n{'regime':<10}{'distinct n':>12}{'DR':>8}{'mean ret':>10}{'EV*':>9}"
          f"{'single-path n':>15}")
    for reg in ("bearish", "neutral", "bullish"):
        rets = np.array(buckets[reg])
        dr = float((rets > 0).mean()) * 100
        mr = float(rets.mean()) * 100
        # EV in DR/mag form, consistent with benchmark.md convention
        win = rets[rets > 0]; loss = rets[rets <= 0]
        ev = ((rets > 0).mean() * (win.mean() if win.size else 0.0)
              + (rets <= 0).mean() * (loss.mean() if loss.size else 0.0)) * 100
        print(f"{reg:<10}{rets.size:>12}{dr:>7.1f}%{mr:>9.2f}%{ev:>8.2f}%"
              f"{base_buckets[reg]:>15}")
    print(f"\n  (*EV = mean per-trade return, same number as 'mean ret' — shown "
          "for parity with benchmark.md.)")
    print(f"  total distinct trades pooled: {len(pooled)}  | "
          f"single chronological path: {len(set(base_trades))}")
    print(f"  pooling multiplies per-regime n by ~{len(pooled)/max(1,len(set(base_trades))):.0f}x "
          "vs the single path — the legitimate 'more samples' win, for "
          "regime-CONDITIONAL EV only.")

    # ── (b) beta-stripped: is the NEUTRAL weak spot alpha or just missing beta? ──
    a_buckets = defaultdict(list)   # regime -> alphas
    b_buckets = defaultdict(list)   # regime -> betas
    for v in pooled.values():
        if v["alpha"] is not None:
            a_buckets[_regime(v["mom"])].append(v["alpha"])
            b_buckets[_regime(v["mom"])].append(v["beta"])

    print("\n" + "=" * 82)
    print("BETA-STRIPPED PER-REGIME ALPHA  (alpha = return − β·N225_over_hold, "
          f"trailing-{_BETA_WIN}bar β)")
    print("=" * 82)
    print(f"\n{'regime':<10}{'n(α)':>8}{'avg β':>8}{'raw ret':>10}{'mean α':>10}"
          f"{'α DR':>8}{'β share':>10}")
    a_means = {}
    for reg in ("bearish", "neutral", "bullish"):
        al = np.array(a_buckets[reg]); bt = np.array(b_buckets[reg])
        raw = float(np.array(buckets[reg]).mean()) * 100
        ma = float(al.mean()) * 100
        a_means[reg] = ma
        adr = float((al > 0).mean()) * 100
        bshare = (raw - ma) / raw * 100 if raw else float("nan")
        print(f"{reg:<10}{al.size:>8}{bt.mean():>8.2f}{raw:>9.2f}%{ma:>9.2f}%"
              f"{adr:>7.1f}%{bshare:>9.0f}%")

    worst_raw = min(("bearish", "neutral", "bullish"),
                    key=lambda r: np.array(buckets[r]).mean())
    worst_alpha = min(a_means, key=a_means.get)
    print(f"\n  worst RAW regime = {worst_raw}; worst ALPHA regime = {worst_alpha}.")
    if worst_alpha == "neutral":
        print("  VERDICT: neutral weakness SURVIVES beta-strip → genuine signal-quality "
              "regime dependence (sizing-tilt candidate, not just missing beta).")
    else:
        print("  VERDICT: neutral weakness DISSOLVES on beta-strip → it was missing "
              "beta tailwind, not bad alpha; NOT a sizing-tilt candidate.")

    # ── STOCK-chop cut: are choppy (low-ADX) stock entries worse? ────────────
    # The operator's "avoid sideways/choppy stocks" idea — binned on the STOCK's
    # own ADX14 at entry (not the N225 regime above). trend_score floor on
    # confluence was a no-op (gate already trend-filters); ADX is the canonical
    # chop measure, so re-checked here on the large pooled sample.
    with_adx = [v for v in pooled.values() if v.get("adx") is not None]
    adxv = np.array([v["adx"] for v in with_adx])
    q1, q2 = np.percentile(adxv, [33.33, 66.67])

    def _chop(a):
        return "choppy(lowADX)" if a <= q1 else ("mid" if a <= q2 else "trending(hiADX)")

    print("\n" + "=" * 82)
    print("STOCK-CHOP CUT — pooled trades binned by the STOCK's own ADX14 at entry")
    print(f"ADX terciles: choppy <= {q1:.1f} < mid <= {q2:.1f} < trending   "
          f"({len(with_adx)}/{len(pooled)} trades have ADX)")
    print("=" * 82)
    print(f"\n{'stock state':<16}{'n':>8}{'avg ADX':>9}{'DR':>8}{'raw ret':>10}"
          f"{'mean α':>10}{'α DR':>8}")
    for lab in ("choppy(lowADX)", "mid", "trending(hiADX)"):
        grp = [v for v in with_adx if _chop(v["adx"]) == lab]
        rets = np.array([v["ret"] for v in grp])
        al = np.array([v["alpha"] for v in grp if v["alpha"] is not None])
        adxs = np.array([v["adx"] for v in grp])
        print(f"{lab:<16}{rets.size:>8}{adxs.mean():>9.1f}"
              f"{float((rets>0).mean())*100:>7.1f}%{float(rets.mean())*100:>9.2f}%"
              f"{float(al.mean())*100:>9.2f}%{float((al>0).mean())*100:>7.1f}%")
    print("\n  (If choppy ≈ trending on alpha → the ≥3-bullish gate already "
          "trend-filters, and a stock-chop screen is redundant — consistent with "
          "the 2026-05-19 trend_score no-op. A clear choppy<trending alpha gap "
          "would reopen it, subject to the portfolio-level fill-order null.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
