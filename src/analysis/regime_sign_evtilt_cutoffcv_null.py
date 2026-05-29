"""RegimeSign EV-tilt cutoff cross-validation — the held-out falsifier for backlog item 2.

The neutral-trim tilt cleared three paired nulls (fill-order, phase+order, integer-lot), but all of them
resample fill-order/phase — NOT the price history — and the NEUTRAL tercile cutoffs (−1.01% / +6.54%) were
derived IN-PERIOD from the same FY2019–2025 tape. This is the binding forward-stability test.

FALSIFIER (pre-registered, frozen): re-derive the bear/neutral/bull terciles on TRAIN FY2019–FY2022 only
(N225 60-bar momentum at the affordable candidate entry dates in that window), FREEZE them, and re-run the
integer-lot paired null scored ONLY on held-out FY2023–FY2025 with those train-derived cutoffs.
  ACCEPT (→ adopt) : held-out Δ maxDD ≥ +2.0pp shallower with ≥ 90% draw-consistency AND Δ Sharpe ≥ 0.
  REJECT          : held-out Δ maxDD ≤ 0 — the drawdown cut was cutoff-axis in-period contamination.
  (in between → WEAK / still DEFER.)

If the maxDD cut survives with cutoffs that never saw FY2023–25, it is forward-stable. Same realistic
integer-lot book as `regime_sign_evtilt_lots_null` (¥2M/6-slot/100-sh, affordability skip, deployed-capital
weighting, τ=0.5). Mirrors `confluence_evtilt_cutoffcv_null.py`; shipped RegimeSign pool via
`build_fy_candidates`. Read-only.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.regime_sign_evtilt_cutoffcv_null
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
from src.portfolio.sizing import position_weight, recommended_lots

_K = 200
_SLOTS = 6
_BUDGET = 2_000_000
_N225_MOM = 60
_TAU = 0.5
_TRAIN = {"FY2019", "FY2020", "FY2021", "FY2022"}   # derive cutoffs here
_TEST = {"FY2023", "FY2024", "FY2025"}              # score the null here


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


def _mom(n_dts, n_idx, n_cmap, d):
    i = n_idx.get(d)
    if i is None or i < _N225_MOM:
        return None
    p0 = n_cmap[n_dts[i - _N225_MOM]]
    return (n_cmap[d] / p0 - 1.0) if p0 else None


def _regime(mom, t1, t2):
    if mom is None:
        return "na"
    return "bear" if mom <= t1 else ("neutral" if mom <= t2 else "bull")


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


def _load_fy(cfg):
    cs = build_fy_candidates(cfg)
    if not cs.candidates or cs.n225_cache is None:
        return None
    caches = cs.stock_caches
    n_dts, n_cmap = _closes(cs.n225_cache)
    n_idx = {d: i for i, d in enumerate(n_dts)}
    stock_dts = {code: _closes(c) for code, c in caches.items()}

    def _affordable(c) -> bool:
        _, cmap = stock_dts.get(c.stock_code, ([], {}))
        px = cmap.get(c.entry_date)
        return px is not None and recommended_lots(_BUDGET, float(px), _SLOTS) > 0
    cands = [c for c in cs.candidates if _affordable(c)]
    cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
    return dict(caches=caches, n_dts=n_dts, n_cmap=n_cmap, n_idx=n_idx,
                stock_dts=stock_dts, cands=cands, cal=cal)


def run() -> None:
    exsim._MAX_LOW_CORR = 5

    # ── PASS 1: derive terciles on TRAIN candidate-entry N225-60bar momenta ──
    train_moms: list[float] = []
    for cfg in RS_FY_CONFIGS:
        if cfg.label not in _TRAIN:
            continue
        fy = _load_fy(cfg)
        if fy is None:
            continue
        for c in fy["cands"]:
            m = _mom(fy["n_dts"], fy["n_idx"], fy["n_cmap"], c.entry_date)
            if m is not None:
                train_moms.append(m)
        logger.info("  TRAIN {} momenta collected ({} total)", cfg.label, len(train_moms))
    t1, t2 = (float(np.percentile(train_moms, 33.33)),
              float(np.percentile(train_moms, 66.67)))
    logger.info("  train-derived cutoffs: bear<= {:.4f} < neutral<= {:.4f} < bull "
                "(orig in-period: -0.0101 / 0.0654)", t1, t2)

    # ── PASS 2: integer-lot paired null scored on HELD-OUT TEST FYs only ──
    ew_st = [[] for _ in range(_K)]
    tl_st = [[] for _ in range(_K)]
    diag = {"neutral": 0, "trim_to_zero": 0, "base_lots_sum": 0, "tilt_lots_sum": 0}
    for cfg in RS_FY_CONFIGS:
        if cfg.label not in _TEST:
            continue
        fy = _load_fy(cfg)
        if fy is None:
            continue
        caches, stock_dts, cands, cal = fy["caches"], fy["stock_dts"], fy["cands"], fy["cal"]
        regime_of = {(c.stock_code, c.entry_date):
                     _regime(_mom(fy["n_dts"], fy["n_idx"], fy["n_cmap"], c.entry_date), t1, t2)
                     for c in cands}
        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)
            results = exsim.run_simulation(pool, EXIT_RULE, caches, cfg.end)
            cal_set = set(cal)
            ew_day, tl_day = defaultdict(float), defaultdict(float)
            for p in results:
                sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
                base_lots = recommended_lots(_BUDGET, float(p.entry_price), _SLOTS)
                reg = regime_of.get((p.stock_code, p.entry_date), "na")
                tilt_lots = int(_TAU * base_lots) if reg == "neutral" else base_lots
                w_ew = position_weight(base_lots, float(p.entry_price), _BUDGET)
                w_tl = position_weight(tilt_lots, float(p.entry_price), _BUDGET)
                if k == 0 and reg == "neutral":
                    diag["neutral"] += 1
                    diag["base_lots_sum"] += base_lots
                    diag["tilt_lots_sum"] += tilt_lots
                    if tilt_lots == 0:
                        diag["trim_to_zero"] += 1
                for d, r in _pos_daily(p, sdts, scmap).items():
                    if d in cal_set:
                        ew_day[d] += r * w_ew
                        tl_day[d] += r * w_tl
            ew_seq = [ew_day.get(d, 0.0) for d in cal]
            tl_seq = [tl_day.get(d, 0.0) for d in cal]
            ew_st[k] += ew_seq[1:]; tl_st[k] += tl_seq[1:]
        logger.info("  TEST {} done ({} affordable candidates, {} shuffles)",
                    cfg.label, len(cands), _K)

    ew_sh = np.array([_sharpe(ew_st[k]) for k in range(_K)])
    tl_sh = np.array([_sharpe(tl_st[k]) for k in range(_K)])
    ew_rd = np.array([_ret_dd(ew_st[k]) for k in range(_K)])
    tl_rd = np.array([_ret_dd(tl_st[k]) for k in range(_K)])
    d = tl_sh - ew_sh
    ddd = tl_rd[:, 1] - ew_rd[:, 1]
    dr = tl_rd[:, 0] - ew_rd[:, 0]

    print("\n" + "=" * 90)
    print("REGIMESIGN EV-TILT CUTOFF CV — train cutoffs FY2019–2022, scored on HELD-OUT FY2023–2025")
    print("=" * 90)
    n = diag["neutral"] or 1
    eff = diag["tilt_lots_sum"] / diag["base_lots_sum"] if diag["base_lots_sum"] else float("nan")
    print(f"\ntrain-derived cutoffs: bear ≤ {t1*100:+.2f}% < neutral ≤ {t2*100:+.2f}% < bull   "
          f"(original in-period: −1.01% / +6.54%)")
    print(f"held-out neutral fills (1 shuffle/FY): {diag['neutral']}  | "
          f"trim→0 lots {diag['trim_to_zero']}/{diag['neutral']} | τ_eff {eff:.3f}")
    print(f"\n{'arm':<14}{'Sharpe mean':>12}{'sd':>7}{'ret mean':>10}{'DD mean':>9}")
    print(f"{'EW-LOT':<14}{ew_sh.mean():>12.3f}{ew_sh.std():>7.3f}"
          f"{ew_rd[:,0].mean()*100:>9.0f}%{ew_rd[:,1].mean()*100:>8.1f}%")
    print(f"{'TILT-LOT':<14}{tl_sh.mean():>12.3f}{tl_sh.std():>7.3f}"
          f"{tl_rd[:,0].mean()*100:>9.0f}%{tl_rd[:,1].mean()*100:>8.1f}%")
    print(f"\n[held-out paired Δ = TILT-LOT − EW-LOT, same fills each shuffle]")
    print(f"  Δ Sharpe  mean {d.mean():+.3f} | 95% CI [{np.percentile(d,2.5):+.3f}, "
          f"{np.percentile(d,97.5):+.3f}] | P(Δ>0) {(d>0).mean():.3f}")
    print(f"  Δ maxDD   mean {ddd.mean()*100:+.2f}pp (positive = shallower) | "
          f"P(shallower) {(ddd>0).mean():.3f}")
    print(f"  Δ return  mean {dr.mean()*100:+.1f}pp")

    print("\n" + "-" * 90)
    dd_pass = ddd.mean() * 100 >= 2.0 and (ddd > 0).mean() >= 0.90
    sh_pass = d.mean() >= 0
    if dd_pass and sh_pass:
        verdict = ("ACCEPT — held-out maxDD cut ≥+2pp (≥90% consistent) AND Δ Sharpe ≥ 0 with "
                   "train-derived cutoffs → the drawdown edge is FORWARD-STABLE, not cutoff overfit.")
    elif ddd.mean() <= 0:
        verdict = ("REJECT — held-out maxDD edge ≤ 0 with train-derived cutoffs → the cut was "
                   "cutoff-axis in-period contamination.")
    else:
        verdict = ("WEAK / STILL DEFER — held-out maxDD positive but below the +2pp/90% gate; "
                   "survives directionally OOS but underpowered on the test window.")
    print(f"  FALSIFIER VERDICT: {verdict}")
    print(f"  (gate: held-out Δ maxDD ≥ +2.0pp AND ≥90% draw-consistency AND Δ Sharpe ≥ 0)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
