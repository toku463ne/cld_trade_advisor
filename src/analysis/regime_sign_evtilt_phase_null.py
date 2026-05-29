"""RegimeSign EV-tilt vs the COMBINED phase+order null — the binding test for a regime-keyed rule.

ESCALATION of `regime_sign_evtilt_null`. That probe found TILT-DL (trim neutral-N225-momentum entries to
τ=0.5) beats equal-weight on the FILL-ORDER null (Δ Sharpe +0.074, P=0.985, CI [+0.009,+0.126], maxDD
+4.4pp) — but the fill-order null pairs only on within-day order, so EVERY shuffle sees the SAME historical
N225 momentum path → it CANNOT detect regime-timing luck (helping merely by deleveraging before the
specific drawdowns in this one path). RegimeSign's OOS FY2025 Δ was −0.067, the canary for in-sample
regime fitting; and its edge is thinner than confluence's → the phase null is genuinely decisive here.

The BINDING null sweeps the START OFFSET (→ different neutral periods get trimmed) AND the within-day fill
order, pairs TILT-DL vs EW within each world, and asks whether trimming neutral reliably helps ACROSS
regime-timing realizations. Worlds = 8 offsets (0..35 trading days) × 25 shuffles = 200/FY, FY2019–2025.
Mirrors `confluence_evtilt_phase_null.py`; only the shipped RegimeSign pool (`build_fy_candidates`) and the
frozen Stage-0 cutoffs differ. Re-windowing the SAME pool (drop candidates before cal[offset]) — no
re-proposing.

GATE (same, in the WIDER band): P(Δ Sharpe>0) ≥ 0.95 AND 95% CI-lo > 0. Survives → robust to regime
timing; collapses → it was canonical-phase luck (consistent with the OOS flip). Read-only.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.regime_sign_evtilt_phase_null
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

_SLOTS = 6
_N225_MOM = 60
_T1, _T2 = -0.0101, 0.0654   # FROZEN Stage-0 cutoffs (same as regime_sign_evtilt_null)
_TAU = 0.5
_OFFSETS = list(range(0, 40, 5))   # 0,5,..,35 trading days of start phase
_K_INNER = 25                      # within-day shuffles per offset → 200 worlds/FY


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


def _two_series(results, stock_dts, regime_of, cal):
    cal_set = set(cal)
    day_items: defaultdict = defaultdict(list)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        reg = regime_of.get((p.stock_code, p.entry_date), "na")
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_items[d].append((r, reg))
    ew = [sum(r for r, _ in day_items.get(d, [])) / _SLOTS for d in cal]
    dl = [sum(r * (1.0 / _SLOTS) * (_TAU if reg == "neutral" else 1.0)
              for r, reg in day_items.get(d, [])) for d in cal]
    return ew, dl


def run() -> None:
    worlds = [(o, k) for o in _OFFSETS for k in range(_K_INNER)]
    ew_st = {w: [] for w in worlds}
    dl_st = {w: [] for w in worlds}
    ew_phase = {o: [] for o in _OFFSETS}
    dl_phase = {o: [] for o in _OFFSETS}

    exsim._MAX_LOW_CORR = 5
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

        for o in _OFFSETS:
            eff_start = cal[o] if o < len(cal) else cal[-1]
            pool_o = [c for c in cands if c.entry_date >= eff_start]
            base = run_simulation(sorted(pool_o, key=lambda c: c.entry_date),
                                  EXIT_RULE, caches, cfg.end)
            bew, bdl = _two_series(base, stock_dts, regime_of, cal)
            ew_phase[o] += bew[1:]; dl_phase[o] += bdl[1:]
            for k in range(_K_INNER):
                rng = random.Random(o * 10_000 + k)
                shuf = pool_o[:]
                rng.shuffle(shuf)
                res = run_simulation(shuf, EXIT_RULE, caches, cfg.end)
                ew, dl = _two_series(res, stock_dts, regime_of, cal)
                ew_st[(o, k)] += ew[1:]
                dl_st[(o, k)] += dl[1:]
        logger.info("  {} done ({} candidates, {} worlds)", cfg.label, len(cands), len(worlds))

    ew_sh = np.array([_sharpe(ew_st[w]) for w in worlds])
    dl_sh = np.array([_sharpe(dl_st[w]) for w in worlds])
    ew_dd = np.array([_ret_dd(ew_st[w])[1] for w in worlds])
    dl_dd = np.array([_ret_dd(dl_st[w])[1] for w in worlds])
    d = dl_sh - ew_sh

    print("\n" + "=" * 88)
    print(f"REGIMESIGN EV-TILT vs COMBINED PHASE+ORDER NULL — {len(_OFFSETS)} offsets × {_K_INNER} shuffles "
          f"= {len(worlds)} worlds/FY, 6-slot, τ={_TAU}")
    print("=" * 88)
    print(f"\n{'arm':<16}{'Sharpe mean':>12}{'sd':>7}{'p5':>7}{'p50':>7}{'p95':>7}{'DD mean':>10}")
    print(f"{'EW':<16}{ew_sh.mean():>12.3f}{ew_sh.std():>7.3f}"
          f"{np.percentile(ew_sh,5):>7.2f}{np.percentile(ew_sh,50):>7.2f}{np.percentile(ew_sh,95):>7.2f}"
          f"{ew_dd.mean()*100:>9.1f}%")
    print(f"{'TILT-DL':<16}{dl_sh.mean():>12.3f}{dl_sh.std():>7.3f}"
          f"{np.percentile(dl_sh,5):>7.2f}{np.percentile(dl_sh,50):>7.2f}{np.percentile(dl_sh,95):>7.2f}"
          f"{dl_dd.mean()*100:>9.1f}%")

    print(f"\n[paired Δ Sharpe = TILT-DL − EW, same fills+phase each world]")
    print(f"  mean {d.mean():+.3f} | sd {d.std():.3f} | "
          f"95% CI [{np.percentile(d,2.5):+.3f}, {np.percentile(d,97.5):+.3f}] | "
          f"P(Δ>0) {(d>0).mean():.3f} ({int((d>0).sum())}/{len(worlds)})")
    ddd = dl_dd - ew_dd
    print(f"  paired Δ maxDD mean {ddd.mean()*100:+.2f}pp (positive = shallower) | "
          f"P(shallower) {(ddd>0).mean():.3f}")

    print(f"\n[per-offset deterministic Δ Sharpe (TILT-DL − EW) — phase robustness]")
    print(f"  {'offset':>7}{'EW Sh':>8}{'DL Sh':>8}{'Δ':>8}")
    pos_off = 0
    for o in _OFFSETS:
        e, l = _sharpe(ew_phase[o]), _sharpe(dl_phase[o])
        if l - e > 0:
            pos_off += 1
        print(f"  {o:>7}{e:>8.2f}{l:>8.2f}{l-e:>+8.3f}")
    print(f"  → TILT-DL > EW at {pos_off}/{len(_OFFSETS)} start phases (deterministic).")

    print("\n" + "-" * 88)
    survives = (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
    if survives:
        verdict = ("SURVIVES — TILT-DL beats EW across regime-timing realizations → the neutral-trim edge "
                   "is robust to regime timing. Drawdown lever; escalate to operator / lot-granularity.")
    else:
        verdict = ("COLLAPSES — the fill-order-null edge does NOT survive start-phase variation → it was "
                   "canonical-phase regime-timing luck. REJECT (Sharpe), consistent with the OOS FY2025 flip. "
                   "(Check whether the Δ maxDD survives — the drawdown cut may still hold even if Sharpe doesn't.)")
    print(f"  VERDICT: {verdict}")
    print(f"  (Gate: P(Δ>0)≥0.95 AND 95% CI-lo>0 in the wider phase+order band.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
