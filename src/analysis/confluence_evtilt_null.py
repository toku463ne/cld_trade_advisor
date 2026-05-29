"""Conditional-EV sizing tilt (trim neutral-momentum entries) — paired fill-order null.

Backlog item 2 (docs/analysis/confluence_improvement_backlog.md). project_confluence_phase_regime
(confluence_regime_pooling.py) found per-trade EV is non-monotone in N225 trailing-60-bar momentum, with
NEUTRAL the weak spot, surviving β-strip (bearish α +0.57% / neutral +0.33% / bullish +1.20%). Neutral EV
is positive-but-lowest → "trim, not skip". This probe trims weight on neutral-regime fills (keeping the
slot filled) and asks whether that beats equal-weight on the 6-slot book NET of fill-order luck.

This is the WEIGHTS axis — it changes how much capital a filled name gets, NOT which names fill the slots
— so it is NOT pre-killed by the fill-order null (unlike skip/veto/reorder selection rules). It conditions
on LOCAL entry momentum, a different axis than the FY-level regime-inverse alpha (market_neutral) and the
market-regime EXIT (item 3, rejected) — do not conflate.

METHOD (same pairing as confluence_voltarget_null): per shuffle seed, run_simulation runs ONCE at the
production 6-slot book (_MAX_LOW_CORR=5). Fills are IDENTICAL across arms; arms differ ONLY in the
per-position daily weight. τ=0.5 (frozen primary trim factor). Each fill is tagged by N225 60-bar momentum
regime at entry, frozen tercile cutoffs from the prior pooled run.

Arms (see docs/analysis/confluence_evtilt_sizing_preregistration.md):
  - EW      : w_p = 1/6 each held day (shipped baseline).
  - TILT-DL : PRIMARY (deleverage, literal item-2 spec). neutral-entry names w_p = (1/6)*τ; bull/bear/
              unclassified w_p = 1/6. Gross floats down on neutral days. Carries the verdict.
  - TILT-RD : SECONDARY diagnostic (same-gross redistribution). relative weight τ for neutral, 1 else,
              normalized so daily gross = |H|/6 == EW. Tilts toward bull/bear with no leverage change.

Binding gate (frozen): TILT-DL vs EW, K=200 paired shuffles —
  P(Δ Sharpe > 0) >= 0.95 AND 95% CI lower bound > 0, OOS stable. (A return-only drop with flat Sharpe =
  the vol-target failure mode = REJECT.)

OUTCOME (2026-05-29, K=200, FY2018–2025): TILT-DL PASSES the fill-order Sharpe gate STRONGLY. EW Sharpe
0.911 / maxDD −27.3%; TILT-DL 1.031 / −23.2%, Δ Sharpe +0.120, P(Δ>0)=1.000 (200/200), CI [+0.037,
+0.207]; return also +11.4pp (NOT a return sacrifice); τ monotone (0.25→+0.173, 0.5→+0.120, 0.75→
+0.061). Per-FY 6/8 positive but magnitude concentrated in FY2021 (+0.38) / FY2022 (+0.28); strong FYs
~flat; OOS FY2025 −0.024 (flat-negative). The fill-order null CANNOT see regime-timing luck → ESCALATED
to confluence_evtilt_phase_null (combined phase+order null), which it ALSO SURVIVES (Δ +0.123, P=0.990,
CI [+0.007,+0.262], positive at 8/8 start phases, maxDD +4.17pp in 100% of worlds). Net: first backlog
lever to clear both binding nulls; primarily a DRAWDOWN lever; operator/sign-debate call (CI-lo thin,
OOS flat). TILT-RD (same-gross redistribute) is weaker (Δ +0.053, CI grazes 0). See backlog item 2.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_evtilt_null
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
_N225_MOM = 60          # trailing bars for N225 trend regime (matches regime_pooling)
_T1, _T2 = -0.001, 0.081  # FROZEN tercile cutoffs from the prior pooled run (bear/neutral/bull)
_TAU = 0.5              # FROZEN primary neutral trim factor
_TAU_SENS = [0.25, 0.75]   # non-binding sensitivity curve
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


def _regime_at(n_dts, n_idx, n_cmap, d):
    """N225 60-bar momentum regime at date d: 'neutral'/'bear'/'bull'/'na'."""
    i = n_idx.get(d)
    if i is None or i < _N225_MOM:
        return "na"
    p0 = n_cmap[n_dts[i - _N225_MOM]]
    if not p0:
        return "na"
    m = n_cmap[d] / p0 - 1.0
    return "bear" if m <= _T1 else ("neutral" if m <= _T2 else "bull")


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


def _day_items(results, stock_dts, regime_of, cal):
    """{date: [(daily_return, regime), ...]} from the fills, plus per-regime fill count."""
    cal_set = set(cal)
    day_items: defaultdict[datetime.date, list] = defaultdict(list)
    reg_count: defaultdict[str, int] = defaultdict(int)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        reg = regime_of.get((p.stock_code, p.entry_date), "na")
        reg_count[reg] += 1
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_items[d].append((r, reg))
    return day_items, reg_count


def _ew(day_items, d):
    items = day_items.get(d, [])
    return sum(r for r, _ in items) / _SLOTS


def _dl(day_items, d, tau):
    """Deleverage: neutral -> tau weight, else full; gross floats down on neutral days."""
    items = day_items.get(d, [])
    return sum(r * (1.0 / _SLOTS) * (tau if reg == "neutral" else 1.0) for r, reg in items)


def _rd(day_items, d, tau):
    """Same-gross redistribution: relative tau for neutral, 1 else, gross == |H|/_SLOTS."""
    items = day_items.get(d, [])
    if not items:
        return 0.0
    wi = [tau if reg == "neutral" else 1.0 for _, reg in items]
    sw = sum(wi)
    gross = len(items) / _SLOTS
    return gross * sum(r * (w / sw) for (r, _), w in zip(items, wi)) if sw > 0 else 0.0


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0   # DB holds only fresh post-rebuild runs (default 47 drops early FYs)
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

    arms = ("ew", "dl", "rd")
    st = {a: [[] for _ in range(_K)] for a in arms}
    oos = {a: [[] for _ in range(_K)] for a in arms}
    # per-FY (ew, dl) stitched series per shuffle — for the per-FY Δ-Sharpe decomposition
    # (binding robustness check: is the pooled win broad or concentrated in drawdown FYs?)
    per_fy: dict[str, dict[str, list]] = {}
    # sensitivity: deleverage Sharpe at other taus (non-binding) — stitched per tau
    sens = {t: [[] for _ in range(_K)] for t in _TAU_SENS}
    total_reg: defaultdict[str, int] = defaultdict(int)

    exsim._MAX_LOW_CORR = 5   # production 6-slot book, fixed for all arms
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
        n_dts, n_cmap = _closes(n225)
        n_idx = {d: i for i, d in enumerate(n_dts)}
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        # regime tag for every candidate entry_date (deterministic, shuffle-independent)
        regime_of = {(c.stock_code, c.entry_date):
                     _regime_at(n_dts, n_idx, n_cmap, c.entry_date) for c in cands}

        per_fy[cfg.label] = {"ew": [None] * _K, "dl": [None] * _K}
        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)           # within-day fill order; run_simulation stable-sorts by date
            results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
            di, rc = _day_items(results, stock_dts, regime_of, cal)
            if k == 0:
                for r_, c_ in rc.items():
                    total_reg[r_] += c_
            series = {"ew": [_ew(di, d) for d in cal],
                      "dl": [_dl(di, d, _TAU) for d in cal],
                      "rd": [_rd(di, d, _TAU) for d in cal]}
            for a in arms:
                st[a][k] += series[a][1:]
                if cfg.label == "FY2025":
                    oos[a][k] += series[a][1:]
            per_fy[cfg.label]["ew"][k] = series["ew"][1:]
            per_fy[cfg.label]["dl"][k] = series["dl"][1:]
            for t in _TAU_SENS:
                sens[t][k] += [_dl(di, d, t) for d in cal][1:]
        logger.info("  {} done ({} candidates, {} shuffles)", cfg.label, len(cands), _K)

    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in arms}
    rt = {a: np.array([_ret_dd(st[a][k])[0] for k in range(_K)]) for a in arms}
    dd = {a: np.array([_ret_dd(st[a][k])[1] for k in range(_K)]) for a in arms}
    oos_sh = {a: np.array([_sharpe(oos[a][k]) for k in range(_K)]) for a in arms}

    label = {"ew": "EW (baseline)", "dl": "TILT-DL (primary)", "rd": "TILT-RD (diag)"}
    print("\n" + "=" * 88)
    print(f"CONDITIONAL-EV SIZING TILT vs FILL-ORDER NULL — {_K} paired shuffles, 6-slot, τ={_TAU}")
    print(f"  trim NEUTRAL N225-60bar-momentum entries (bear≤{_T1*100:+.1f}% < neutral≤{_T2*100:+.1f}% < bull)")
    nn = sum(total_reg.values()) or 1
    print(f"  filled-position regime mix (1 shuffle/FY): "
          f"bear {total_reg['bear']} ({total_reg['bear']/nn*100:.0f}%) | "
          f"neutral {total_reg['neutral']} ({total_reg['neutral']/nn*100:.0f}%) | "
          f"bull {total_reg['bull']} ({total_reg['bull']/nn*100:.0f}%) | na {total_reg['na']}")
    print("=" * 88)
    print(f"\n{'arm':<20}{'Sharpe mean':>12}{'sd':>7}{'p5':>7}{'p50':>7}{'p95':>7}"
          f"{'ret mean':>10}{'DD mean':>9}")
    for a in arms:
        s_ = sh[a]
        print(f"{label[a]:<20}{s_.mean():>12.3f}{s_.std():>7.3f}"
              f"{np.percentile(s_,5):>7.2f}{np.percentile(s_,50):>7.2f}{np.percentile(s_,95):>7.2f}"
              f"{rt[a].mean()*100:>9.0f}%{dd[a].mean()*100:>8.1f}%")

    def report(name, a):
        d = sh[a] - sh["ew"]
        ddd = dd[a] - dd["ew"]
        dr = rt[a] - rt["ew"]
        do = oos_sh[a] - oos_sh["ew"]
        print(f"\n[{name}: paired Δ vs EW, same fills each draw]")
        print(f"  Δ Sharpe  mean {d.mean():+.3f} | sd {d.std():.3f} | "
              f"95% CI [{np.percentile(d,2.5):+.3f}, {np.percentile(d,97.5):+.3f}] | "
              f"P(Δ>0) {(d>0).mean():.3f} ({int((d>0).sum())}/{_K})")
        print(f"  Δ maxDD   mean {ddd.mean()*100:+.2f}pp (positive = shallower) | "
              f"P(shallower) {(ddd>0).mean():.3f}")
        print(f"  Δ return  mean {dr.mean()*100:+.1f}pp | P(Δ>0) {(dr>0).mean():.3f}")
        print(f"  OOS FY2025 Δ Sharpe mean {do.mean():+.3f} | P(Δ>0) {(do>0).mean():.3f}")
        return (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0

    p_dl = report("TILT-DL (PRIMARY — deleverage, trim neutral, keep bull/bear full)", "dl")
    report("TILT-RD (SECONDARY — same-gross redistribute toward bull/bear)", "rd")

    print("\n[τ sensitivity for TILT-DL (NON-BINDING) — paired Δ Sharpe vs EW]")
    for t in _TAU_SENS:
        sht = np.array([_sharpe(sens[t][k]) for k in range(_K)])
        dt = sht - sh["ew"]
        print(f"  τ={t}: Δ Sharpe mean {dt.mean():+.3f} | P(Δ>0) {(dt>0).mean():.3f} "
              f"| CI [{np.percentile(dt,2.5):+.3f}, {np.percentile(dt,97.5):+.3f}]")

    # ── per-FY decomposition (BINDING robustness) — is the pooled win broad, or
    #    concentrated in 1-2 drawdown FYs? A regime-keyed sizing rule that "works"
    #    only by deleveraging before FY2018/FY2021 is regime-timing luck the
    #    fill-order null cannot see (see confluence_start_phase_null).
    print("\n[PER-FY paired Δ Sharpe (TILT-DL − EW), mean over shuffles — robustness]")
    print(f"  {'FY':<8}{'EW Sh':>8}{'DL Sh':>8}{'Δ Sharpe':>10}{'P(Δ>0)':>9}{'frac+':>0}")
    npos = 0
    for cfg in _FYS:
        if cfg.label not in per_fy:
            continue
        ews = np.array([_sharpe(per_fy[cfg.label]["ew"][k]) for k in range(_K)])
        dls = np.array([_sharpe(per_fy[cfg.label]["dl"][k]) for k in range(_K)])
        d = dls - ews
        mask = ~(np.isnan(d))
        dm = d[mask]
        tag = " OOS" if cfg.label == "FY2025" else ""
        if dm.size and dm.mean() > 0:
            npos += 1
        print(f"  {cfg.label:<8}{np.nanmean(ews):>8.2f}{np.nanmean(dls):>8.2f}"
              f"{(dm.mean() if dm.size else float('nan')):>+10.3f}"
              f"{((dm>0).mean() if dm.size else float('nan')):>9.2f}{tag}")
    nfy = sum(1 for cfg in _FYS if cfg.label in per_fy)
    print(f"  → TILT-DL improves {npos}/{nfy} FYs (mean Δ Sharpe > 0).")

    oos_d = oos_sh["dl"] - oos_sh["ew"]
    oos_ok = oos_d.mean() > 0
    print("\n" + "-" * 88)
    print(f"  GATE 1 (fill-order Sharpe): {'PASS' if p_dl else 'FAIL'} "
          f"(P(Δ>0) {(sh['dl']-sh['ew']>0).mean():.3f}, CI-lo {np.percentile(sh['dl']-sh['ew'],2.5):+.3f})")
    print(f"  GATE 3 (OOS FY2025 no hard sign-flip): {'PASS' if oos_ok else 'FAIL'} "
          f"(Δ Sharpe {oos_d.mean():+.3f})")
    print("  CAVEAT: the fill-order null pairs on within-day order only — every shuffle sees the SAME")
    print("  N225 momentum path, so it CANNOT detect regime-timing luck. For a regime-keyed sizing rule")
    print("  the binding null is the START-PHASE / regime-resample null (confluence_start_phase_null).")
    if p_dl and oos_ok and npos >= nfy - 1:
        verdict = "ACCEPT (provisional) — broad per-FY + OOS hold; ESCALATE to start-phase null to confirm"
    elif p_dl and not oos_ok:
        verdict = ("HOLD / ESCALATE — passes fill-order Sharpe gate strongly but OOS FY2025 sign-flips; "
                   "likely in-sample regime-timing. Needs the start-phase null + per-FY breadth before ACCEPT")
    else:
        verdict = "REJECT — does not clear the fill-order Sharpe gate"
    print(f"\n  PRIMARY VERDICT: {verdict}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
