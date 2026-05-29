"""RegimeSign conditional-EV sizing tilt (trim neutral-momentum entries) — paired fill-order null.

Backlog item 2 Stage 1 (`docs/analysis/regime_sign_improvement_backlog.md`). Stage 0
(`regime_sign_evtilt_stage0.py`) found the SAME non-monotone NEUTRAL-momentum EV trough confluence has,
DEEPER: bearish raw +2.71%/α +1.14%, NEUTRAL +0.26%/α −0.03%/DR 49.8%, bullish +2.89%/α +1.52%. This probe
trims weight on neutral-regime fills (keeping the slot filled) and asks whether that beats equal-weight on
the production 6-slot book NET of fill-order luck.

WEIGHTS axis — changes how much capital a filled name gets, NOT which names fill the slots → NOT pre-killed
by the fill-order null (unlike skip/veto/reorder selection rules). Mirrors `confluence_evtilt_null.py`
exactly; only the candidate pool (shipped RegimeSign via `build_fy_candidates`) and the FROZEN tercile
cutoffs (from the RegimeSign Stage-0 run) differ.

Arms:
  - EW      : w_p = 1/6 each held day (shipped baseline).
  - TILT-DL : PRIMARY. neutral-entry names w_p = (1/6)*τ; bull/bear/na w_p = 1/6 (gross floats down).
  - TILT-RD : SECONDARY (same-gross redistribution toward bull/bear).

Binding gate (frozen): TILT-DL vs EW, K=200 paired shuffles — P(Δ Sharpe>0) ≥ 0.95 AND 95% CI-lo > 0,
OOS-stable. A return-only drop with flat Sharpe = the vol-target failure mode = REJECT. The fill-order null
cannot see regime-timing luck → if it passes, ESCALATE to a start-phase null (as confluence did).

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.regime_sign_evtilt_null
"""
from __future__ import annotations

import math
import random
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
from src.exit.exit_simulator import run_simulation

_K = 200
_SLOTS = 6
_N225_MOM = 60
# FROZEN tercile cutoffs from regime_sign_evtilt_stage0 (bear ≤ −1.01% < neutral ≤ +6.54% < bull)
_T1, _T2 = -0.0101, 0.0654
_TAU = 0.5
_TAU_SENS = [0.0, 0.25, 0.75]   # 0.0 = full skip-weight (Stage 0 hinted neutral α≈0)


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
    cal_set = set(cal)
    day_items: defaultdict = defaultdict(list)
    reg_count: defaultdict = defaultdict(int)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        reg = regime_of.get((p.stock_code, p.entry_date), "na")
        reg_count[reg] += 1
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_items[d].append((r, reg))
    return day_items, reg_count


def _ew(day_items, d):
    return sum(r for r, _ in day_items.get(d, [])) / _SLOTS


def _dl(day_items, d, tau):
    return sum(r * (1.0 / _SLOTS) * (tau if reg == "neutral" else 1.0)
               for r, reg in day_items.get(d, []))


def _rd(day_items, d, tau):
    items = day_items.get(d, [])
    if not items:
        return 0.0
    wi = [tau if reg == "neutral" else 1.0 for _, reg in items]
    sw = sum(wi)
    gross = len(items) / _SLOTS
    return gross * sum(r * (w / sw) for (r, _), w in zip(items, wi)) if sw > 0 else 0.0


def run() -> None:
    arms = ("ew", "dl", "rd")
    st = {a: [[] for _ in range(_K)] for a in arms}
    oos = {a: [[] for _ in range(_K)] for a in arms}
    per_fy: dict = {}
    sens = {t: [[] for _ in range(_K)] for t in _TAU_SENS}
    total_reg: defaultdict = defaultdict(int)

    exsim._MAX_LOW_CORR = 5   # production 6-slot book

    for cfg in RS_FY_CONFIGS:
        cs = build_fy_candidates(cfg)
        if not cs.candidates or cs.n225_cache is None:
            continue
        caches = cs.stock_caches
        cands = cs.candidates
        n_dts, n_cmap = _closes(cs.n225_cache)
        n_idx = {d: i for i, d in enumerate(n_dts)}
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        regime_of = {(c.stock_code, c.entry_date):
                     _regime_at(n_dts, n_idx, n_cmap, c.entry_date) for c in cands}

        per_fy[cfg.label] = {"ew": [None] * _K, "dl": [None] * _K}
        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)
            results = run_simulation(pool, EXIT_RULE, caches, cfg.end)
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
    print(f"REGIMESIGN CONDITIONAL-EV SIZING TILT vs FILL-ORDER NULL — {_K} paired shuffles, 6-slot, τ={_TAU}")
    print(f"  trim NEUTRAL N225-60bar entries (bear≤{_T1*100:+.2f}% < neutral≤{_T2*100:+.2f}% < bull)")
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

    print("\n[PER-FY paired Δ Sharpe (TILT-DL − EW), mean over shuffles — robustness]")
    print(f"  {'FY':<8}{'EW Sh':>8}{'DL Sh':>8}{'Δ Sharpe':>10}{'P(Δ>0)':>9}")
    npos = 0
    for cfg in RS_FY_CONFIGS:
        if cfg.label not in per_fy:
            continue
        ews = np.array([_sharpe(per_fy[cfg.label]["ew"][k]) for k in range(_K)])
        dls = np.array([_sharpe(per_fy[cfg.label]["dl"][k]) for k in range(_K)])
        d = dls - ews
        dm = d[~np.isnan(d)]
        tag = " OOS" if cfg.label == "FY2025" else ""
        if dm.size and dm.mean() > 0:
            npos += 1
        print(f"  {cfg.label:<8}{np.nanmean(ews):>8.2f}{np.nanmean(dls):>8.2f}"
              f"{(dm.mean() if dm.size else float('nan')):>+10.3f}"
              f"{((dm>0).mean() if dm.size else float('nan')):>9.2f}{tag}")
    nfy = sum(1 for cfg in RS_FY_CONFIGS if cfg.label in per_fy)
    print(f"  → TILT-DL improves {npos}/{nfy} FYs (mean Δ Sharpe > 0).")

    oos_d = oos_sh["dl"] - oos_sh["ew"]
    oos_ok = oos_d.mean() > 0
    print("\n" + "-" * 88)
    print(f"  GATE 1 (fill-order Sharpe): {'PASS' if p_dl else 'FAIL'} "
          f"(P(Δ>0) {(sh['dl']-sh['ew']>0).mean():.3f}, "
          f"CI-lo {np.percentile(sh['dl']-sh['ew'],2.5):+.3f})")
    print(f"  GATE 3 (OOS FY2025 no hard sign-flip): {'PASS' if oos_ok else 'FAIL'} "
          f"(Δ Sharpe {oos_d.mean():+.3f})")
    print("  CAVEAT: the fill-order null pairs on within-day order only — it CANNOT detect regime-timing")
    print("  luck. For a regime-keyed rule the binding follow-up is a START-PHASE / regime-resample null.")
    if p_dl and oos_ok and npos >= nfy - 1:
        verdict = "PASS (provisional) — broad per-FY + OOS hold; ESCALATE to start-phase null to confirm"
    elif p_dl and not oos_ok:
        verdict = ("HOLD / ESCALATE — passes fill-order Sharpe gate but OOS FY2025 sign-flips; "
                   "needs start-phase null + per-FY breadth before ACCEPT")
    elif p_dl:
        verdict = "PASS-but-narrow — clears the Sharpe gate; check per-FY breadth + escalate"
    else:
        verdict = "REJECT — does not clear the fill-order Sharpe gate"
    print(f"\n  PRIMARY VERDICT: {verdict}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
