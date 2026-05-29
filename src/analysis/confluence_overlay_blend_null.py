"""Confluence + uncorrelated TSMOM overlay blend — paired fill-order null (backlog item 6).

Backlog item 6 (docs/analysis/confluence_improvement_backlog.md), pre-reg
docs/analysis/confluence_overlay_blend_preregistration.md. The 6-slot confluence book only TIES the
equal-weight universe (project_confluence_buyhold_win) — its edge over the index is a drawdown cut, ~62%
beta, not alpha. The standing thesis: the biggest risk-adjusted gain is pairing the book with an
UNCORRELATED stream, not optimizing the book. Candidate: the single-index TSMOM long/flat L=12 monthly
defensive overlay (docs/analysis/20260528_tsmom_overlay.md) — breadth-immune, ~1 switch/yr.

NOT the rejected TSMOM ENTRY-GATE (confluence_tsmom_gate_probe): that skipped confluence entries when the
index trend was down (fought the regime-inverse alpha). This runs the overlay as a PARALLEL capital sleeve
blended at the portfolio level — it never changes which names confluence buys, only splits the ¥2M between
the stock book and a timed index sleeve.

METHOD: reuse confluence_voltarget_null's reconstruction of the production 6-slot book (_MAX_LOW_CORR=5),
FY2018-2025, capital-aware equal-weight daily-return series r_c. For each of K=200 fill-order shuffles
r_c[k] varies; the OVERLAY r_o is DETERMINISTIC (computed once). Blend, no leverage:
  r_blend[d] = (1-f)*r_c[d] + f*r_o[d].
f of the ¥2M permanently allocated to the TSMOM sleeve, (1-f) to the stock book; gross <= 1 (overlay
holds cash when flat). PRIMARY f=0.30 carries the verdict; f in {0.20,0.30,0.50} reported as dose-response.

OVERLAY (r_o): ^N225 daily via yfinance (clean multi-decade 12-mo lookback; fallback to in-DB ^N225
daily). Monthly long/flat L=12 signal exactly as tsmom_index_probe._tsmom_book: end-of-month sign of
trailing-12mo return -> long(1)/flat(0) next month; 30bps/switch; flat = cash (0).

GATE (frozen, pre-reg): judged as a diversification/drawdown lever (overlay is tail-insurance not alpha).
  PRIMARY (Sharpe): f=0.30 vs confluence-only, P(Δ Sharpe>0)>=0.95 AND 95% CI-lo>0.
  SECONDARY drawdown escape (if PRIMARY fails): mean Δ maxDD>=+2.0pp AND P(shallower)>=0.95 AND
    Δ Sharpe CI-lo>=-0.10 AND OOS FY2025 Δ Sharpe>=-0.30.
  else REJECT.

OUTCOME (2026-05-29, K=200, FY2018-2025): REJECT — both gates fail. CRUX: pooled ρ(confluence_daily,
overlay_daily) = +0.605 — the overlay is long-equity beta 70% of the time, co-moving with the ~0.7-beta
book, so it is NOT a diversifier in THIS window (the 41-yr TSMOM edge lives in sustained bears outside
FY2018-25). Standalone overlay Sharpe +0.37 / maxDD -39% < confluence 0.91. Blend just dilutes toward
the lower-Sharpe asset: f=0.30 Δ Sharpe -0.081 (P(Δ>0)=0.015, CI [-0.156,-0.012]), Δ maxDD -0.61pp
(WORSE, P(shallower)=0.235), Δ return -77pp; monotone-worse in f (0.20->-0.046, 0.50->-0.179). Per-FY
maxDD: small cuts in calm/up FYs but WORSE in the years that matter (FY2024 -1.2pp, the documented
good-in-bearish year; FY2019 +0.2pp ~no-op on the -29% loss year — TSMOM was long going in). The
predicted failure mode (pre-reg) confirmed. OOS FY2025 leans + (+0.074) but pooled binds + it loses
badly overall. CLOSES the diversification-overlay lever for a single-index TSMOM sleeve. See backlog
item 6 + confluence_overlay_blend_preregistration.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_overlay_blend_null
"""
from __future__ import annotations

import datetime
import math
import random
import statistics
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200
_SLOTS = 6
_LOOKBACK_MO = 12            # TSMOM canonical lookback (Moskowitz-Ooi-Pedersen), frozen
_COST_BPS = 30.0            # per switch, one-way notional (matches tsmom_index_probe)
_FRACS = (0.20, 0.30, 0.50)
_PRIMARY_F = 0.30
# FY2018-2025 (matches the backlog baseline + item 2), = FY2018 + RS_FY_CONFIGS
_FYS = [FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)


# --------------------------------------------------------------------------- confluence book series
def _closes(cache):
    dts, cmap, seen = [], {}, set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cmap[d] = b.close
    dts.sort()
    return dts, cmap


def _pos_daily(p, dts, cmap):
    try:
        ie, ix = dts.index(p.entry_date), dts.index(p.exit_date)
    except ValueError:
        return {}
    out = {}
    if ie == ix:
        out[p.entry_date] = p.exit_price / p.entry_price - 1.0
        return out
    span = dts[ie:ix + 1]
    for k, d in enumerate(span):
        if k == 0:
            out[d] = cmap[d] / p.entry_price - 1.0
        elif d == p.exit_date:
            out[d] = p.exit_price / cmap[span[k - 1]] - 1.0
        else:
            out[d] = cmap[d] / cmap[span[k - 1]] - 1.0
    return out


def _conf_daily(results, stock_dts, cal_set):
    """Equal-weight 6-slot daily-return series for the FY (production baseline)."""
    day_sum: defaultdict[datetime.date, float] = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_sum[d] += r / _SLOTS
    return day_sum


# --------------------------------------------------------------------------- TSMOM overlay (deterministic)
def _n225_daily():
    """(dates, closes) ^N225 daily, yfinance preferred (clean lookback), DB fallback."""
    try:
        import yfinance as yf
        df = yf.download("^N225", start="2016-01-01", end="2026-05-29",
                         interval="1d", progress=False, auto_adjust=False)
        if df is not None and not df.empty:
            close = df["Close"]
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
            close = close.dropna()
            return [d.date() for d in close.index], close.to_numpy(dtype=np.float64), "yfinance"
    except Exception as e:
        logger.warning("yfinance ^N225 unavailable ({}) — falling back to in-DB daily", e)
    with get_session() as s:
        c = DataCache("^N225", "1d")
        c.load(s, datetime.datetime(2016, 1, 1, tzinfo=datetime.timezone.utc),
               datetime.datetime(2026, 5, 29, tzinfo=datetime.timezone.utc))
    dts, cmap = _closes(c)
    return dts, np.array([cmap[d] for d in dts], dtype=np.float64), "in-DB"


def _overlay_daily_map():
    """Deterministic {date: overlay daily return} for the TSMOM long/flat L=12 sleeve, plus a
    {date: long?} indicator. Monthly signal (sign of trailing-12mo return) sets the position held
    through the next month; 30bps per switch; flat = 0 (cash)."""
    dates, closes, src = _n225_daily()
    # month-end index of each (year,month)
    by_month: dict[tuple, int] = {}
    for i, d in enumerate(dates):
        by_month[(d.year, d.month)] = i            # last trading day of the month (asc)
    months = sorted(by_month)
    mend = {m: by_month[m] for m in months}        # (y,m) -> daily index of month-end close
    # monthly signal: position for month m_i = sign of trailing-12mo return at end of m_{i-1}
    pos_for_month: dict[tuple, int] = {}
    for j, m in enumerate(months):
        if j == 0:
            pos_for_month[m] = 1                   # bootstrap: long (unconditional prior)
            continue
        prev_end = mend[months[j - 1]]
        back = prev_end - _LOOKBACK_MO * 21        # ~21 trading days/month
        if back < 0:
            pos_for_month[m] = 1                   # insufficient lookback -> long (pre-FY2018 only)
            continue
        sig = closes[prev_end] / closes[back] - 1.0
        pos_for_month[m] = 1 if sig > 0 else 0
    # expand to daily overlay returns
    r_o: dict[datetime.date, float] = {}
    is_long: dict[datetime.date, bool] = {}
    pos_prev = 0
    cur_month = None
    for i in range(1, len(dates)):
        d = dates[i]
        m = (d.year, d.month)
        pos = pos_for_month.get(m, 1)
        ret = closes[i] / closes[i - 1] - 1.0
        cost = 0.0
        if m != cur_month:                          # first trading day of a (new) month -> settle switch
            cost = (_COST_BPS / 10_000.0) * abs(pos - pos_prev)
            cur_month = m
            pos_prev = pos
        r_o[d] = pos * ret - cost
        is_long[d] = pos > 0
    return r_o, is_long, src


# --------------------------------------------------------------------------- stats
def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _ret_dd(rets):
    if len(rets) < 2:
        return float("nan"), float("nan")
    eq = np.cumprod(1.0 + np.asarray(rets))
    runmax = np.maximum.accumulate(eq)
    return float(eq[-1] - 1.0), float((eq / runmax - 1.0).min())


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    r_o_map, is_long_map, ov_src = _overlay_daily_map()
    logger.info("overlay built from {} ^N225 daily ({} dated returns)", ov_src, len(r_o_map))

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

    # stitched daily series per shuffle: confluence-only (conf) and each blend frac
    conf = [[] for _ in range(_K)]
    blend = {f: [[] for _ in range(_K)] for f in _FRACS}
    oos_conf = [[] for _ in range(_K)]
    oos_blend = {f: [[] for _ in range(_K)] for f in _FRACS}
    # full deterministic overlay stitched series (shuffle-invariant) on the union trading calendar
    overlay_stitch: list[float] = []
    overlay_long_days = overlay_total_days = 0
    # pooled (conf_day, overlay_day) pairs for correlation — use shuffle 0 confluence as representative
    corr_c: list[float] = []
    corr_o: list[float] = []
    perfy_dd = {}   # label -> (conf_dd, blend_primary_dd) at shuffle 0

    exsim._MAX_LOW_CORR = 5
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
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        n_dts, _ = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        cal_set = set(cal)
        cal_seq = cal[1:]   # drop first day (matches voltarget stitching)
        # overlay stitched series + %long over this FY's calendar
        for d in cal_seq:
            overlay_stitch.append(r_o_map.get(d, 0.0))
            overlay_total_days += 1
            overlay_long_days += int(is_long_map.get(d, False))

        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)
            results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
            day_sum = _conf_daily(results, stock_dts, cal_set)
            c_seq = [day_sum.get(d, 0.0) for d in cal_seq]
            o_seq = [r_o_map.get(d, 0.0) for d in cal_seq]
            conf[k] += c_seq
            for f in _FRACS:
                b_seq = [(1.0 - f) * c + f * o for c, o in zip(c_seq, o_seq)]
                blend[f][k] += b_seq
                if cfg.label == "FY2025":
                    oos_blend[f][k] += b_seq
            if cfg.label == "FY2025":
                oos_conf[k] += c_seq
            if k == 0:
                corr_c += c_seq
                corr_o += o_seq
                perfy_dd[cfg.label] = (_ret_dd(c_seq)[1],
                                       _ret_dd([(1 - _PRIMARY_F) * c + _PRIMARY_F * o
                                                for c, o in zip(c_seq, o_seq)])[1])
        logger.info("  {} done ({} candidates, {} shuffles)", cfg.label, len(cands), _K)

    # ---- aggregate
    conf_sh = np.array([_sharpe(conf[k]) for k in range(_K)])
    conf_rt = np.array([_ret_dd(conf[k])[0] for k in range(_K)])
    conf_dd = np.array([_ret_dd(conf[k])[1] for k in range(_K)])
    conf_oos = np.array([_sharpe(oos_conf[k]) for k in range(_K)])

    ov_sh, ov_dd = _sharpe(overlay_stitch), _ret_dd(overlay_stitch)[1]
    ov_rt = _ret_dd(overlay_stitch)[0]
    ov_cagr = (1.0 + ov_rt) ** (252.0 / len(overlay_stitch)) - 1.0 if overlay_stitch else float("nan")
    rho = float(np.corrcoef(corr_c, corr_o)[0, 1]) if len(corr_c) > 2 else float("nan")

    print("\n" + "=" * 92)
    print(f"CONFLUENCE + TSMOM OVERLAY BLEND — {_K} paired fill-order shuffles, 6-slot, FY2018-2025")
    print("=" * 92)
    print(f"\nOVERLAY (standalone, deterministic, FY2018-2025, src={ov_src}): "
          f"Sharpe {ov_sh:+.2f}  maxDD {ov_dd * 100:.1f}%  CAGR {ov_cagr * 100:.1f}%  "
          f"ret {ov_rt * 100:.0f}%  %long {overlay_long_days / max(1, overlay_total_days) * 100:.0f}%")
    print(f"CRUX — pooled ρ(confluence_daily, overlay_daily) = {rho:+.3f}  "
          f"({'positive → NOT a diversifier in this window' if rho > 0.1 else 'low → genuine diversifier'})")

    print(f"\n{'arm':<22}{'Sharpe mean':>12}{'sd':>7}{'p5':>7}{'p50':>7}{'p95':>7}"
          f"{'ret mean':>10}{'DD mean':>9}")
    print(f"{'confluence-only (f=0)':<22}{conf_sh.mean():>12.3f}{conf_sh.std():>7.3f}"
          f"{np.percentile(conf_sh,5):>7.2f}{np.percentile(conf_sh,50):>7.2f}"
          f"{np.percentile(conf_sh,95):>7.2f}{conf_rt.mean()*100:>9.0f}%{conf_dd.mean()*100:>8.1f}%")
    blend_stats = {}
    for f in _FRACS:
        bsh = np.array([_sharpe(blend[f][k]) for k in range(_K)])
        brt = np.array([_ret_dd(blend[f][k])[0] for k in range(_K)])
        bdd = np.array([_ret_dd(blend[f][k])[1] for k in range(_K)])
        boos = np.array([_sharpe(oos_blend[f][k]) for k in range(_K)])
        blend_stats[f] = (bsh, brt, bdd, boos)
        print(f"{f'blend f={f:.2f}':<22}{bsh.mean():>12.3f}{bsh.std():>7.3f}"
              f"{np.percentile(bsh,5):>7.2f}{np.percentile(bsh,50):>7.2f}"
              f"{np.percentile(bsh,95):>7.2f}{brt.mean()*100:>9.0f}%{bdd.mean()*100:>8.1f}%")

    def report(f):
        bsh, brt, bdd, boos = blend_stats[f]
        d = bsh - conf_sh
        ddd = bdd - conf_dd
        dr = brt - conf_rt
        do = boos - conf_oos
        ci_lo, ci_hi = np.percentile(d, 2.5), np.percentile(d, 97.5)
        print(f"\n[blend f={f:.2f} − confluence-only, paired, same fills each draw]")
        print(f"  Δ Sharpe  mean {d.mean():+.3f} | sd {d.std():.3f} | "
              f"95% CI [{ci_lo:+.3f}, {ci_hi:+.3f}] | P(Δ>0) {(d>0).mean():.3f} ({int((d>0).sum())}/{_K})")
        print(f"  Δ maxDD   mean {ddd.mean()*100:+.2f}pp (positive = shallower) | "
              f"P(shallower) {(ddd>0).mean():.3f}")
        print(f"  Δ return  mean {dr.mean()*100:+.1f}pp | P(Δ>0) {(dr>0).mean():.3f}")
        print(f"  OOS FY2025 Δ Sharpe mean {do.mean():+.3f} | P(Δ>0) {(do>0).mean():.3f}")
        return d, ddd, do

    print("\n" + "-" * 92)
    print("DOSE-RESPONSE (report only):")
    for f in _FRACS:
        if f != _PRIMARY_F:
            report(f)
    print("\n" + "=" * 92)
    print(f"PRIMARY (f={_PRIMARY_F:.2f}) — carries the verdict")
    d, ddd, do = report(_PRIMARY_F)

    print("\nPER-FY maxDD (shuffle 0): confluence vs blend f=0.30")
    for lab, (cdd, bdd0) in perfy_dd.items():
        print(f"    {lab:<8} conf {cdd*100:>7.1f}%   blend {bdd0*100:>7.1f}%   "
              f"Δ {(bdd0-cdd)*100:>+6.1f}pp")

    primary_pass = (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
    dd_escape = (ddd.mean() >= 0.02 and (ddd > 0).mean() >= 0.95
                 and np.percentile(d, 2.5) >= -0.10 and do.mean() >= -0.30)
    print("\n" + "-" * 92)
    if primary_pass:
        verdict = "ACCEPT — blend Sharpe band sits above confluence-only net of fill-order luck"
    elif dd_escape:
        verdict = ("ACCEPT (drawdown lever) — Sharpe gate fails but maxDD cut >=2pp @P>=0.95 with no "
                   "Sharpe collapse (operator call, like item 2)")
    else:
        verdict = ("REJECT — blend does not beat confluence-only on the fill-order null; the overlay "
                   "dilutes toward a lower-Sharpe, in-window-correlated asset (see ρ + per-FY maxDD)")
    print(f"  VERDICT: {verdict}")
    print("  (Gate: PRIMARY P(ΔSharpe>0)>=0.95 AND CI-lo>0; SECONDARY drawdown escape "
          "ΔmaxDD>=+2pp @P>=0.95 AND ΔSharpe CI-lo>=-0.10 AND OOS>=-0.30.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
