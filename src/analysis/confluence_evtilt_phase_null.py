"""EV-tilt vs the COMBINED phase+order null — the binding test for a regime-keyed rule.

ESCALATION of confluence_evtilt_null. That probe found TILT-DL (trim neutral-N225-momentum entries to
τ=0.5) beats equal-weight on the FILL-ORDER null (Δ Sharpe +0.120, P(Δ>0)=1.000, CI [+0.037,+0.207]) —
but with a CAVEAT: the fill-order null pairs only on within-day order, so EVERY shuffle sees the SAME
historical N225 momentum path. It therefore CANNOT detect regime-timing luck — the risk that "trim
neutral" helps merely by deleveraging before the specific FY2018/FY2021 drawdowns in this one path. The
OOS FY2025 Δ was mildly negative (−0.024), the canary for in-sample regime fitting.

confluence_start_phase_null established that START PHASE alone swings the book ±0.5 Sharpe (regime-timing
variance — sticky ~22-bar holds land over different regimes). So the BINDING null for a regime-keyed
sizing rule is the COMBINED phase+order null: sweep the start offset (→ different neutral periods get
trimmed) AND the within-day fill order, pair TILT-DL vs EW within each world, and ask whether trimming
neutral reliably helps ACROSS regime-timing realizations — not just at the canonical 4/1 phase.

Worlds = _OFFSETS (8 start phases, 0..35 trading days) × _K_INNER (25 within-day shuffles) = 200/FY,
stitched across FY2018–2025. Each world: drop candidates entered before cal[offset], shuffle, run once,
mark EW and TILT-DL on the SAME fills. Paired Δ = Sharpe(DL) − Sharpe(EW) per world.

GATE (same as the fill-order null, now in the WIDER band): P(Δ Sharpe > 0) ≥ 0.95 AND 95% CI-lo > 0.
If the edge SURVIVES here it is robust to regime timing (→ real, escalate to operator/lot-granularity);
if it COLLAPSES it was canonical-phase luck the fill-order null could not see.

OUTCOME (2026-05-29, 8 offsets × 25 shuffles = 200 worlds/FY, FY2018–2025): SURVIVES. EW Sharpe 0.878
(sd 0.204, vs the fill-order null's 0.162 — phase genuinely widens the band) / maxDD −27.2%; TILT-DL
1.001 / −23.0%. Paired Δ Sharpe +0.123, P(Δ>0)=0.990 (198/200), 95% CI [+0.007, +0.262] (CI-lo grazes
0 in the wider band, but clears); Δ maxDD +4.17pp shallower in 100% of worlds. DECISIVE: per-offset
deterministic Δ is positive at 8/8 start phases (+0.087..+0.223) — regime-timing luck would have made
some phases negative; it did not. So the neutral-trim edge is robust to regime timing, NOT canonical-
phase deleveraging. This is the FIRST backlog lever to clear BOTH binding nulls (fill-order + phase),
and it clears the 95% CI where the shipped 6-slot capacity null did not (that was P=0.865, CI included
0, adopted on risk-asymmetry). Caveats: CI-lo thin (+0.007); Sharpe gain concentrated in the weak FYs
(FY2021 +0.38 / FY2022 +0.28) while strong FYs ~flat and OOS FY2025 −0.024; cutoffs (t1/t2) are from
the in-period momentum distribution. Primarily a DRAWDOWN lever (−4pp, rock-solid) with a modest Sharpe
tailwind. → operator / sign-debate call; do NOT auto-ship production. See backlog item 2.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_evtilt_phase_null
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
_SLOTS = 6
_N225_MOM = 60
_T1, _T2 = -0.001, 0.081      # FROZEN tercile cutoffs (same as confluence_evtilt_null)
_TAU = 0.5                    # FROZEN primary neutral trim factor
_OFFSETS = list(range(0, 40, 5))   # 0,5,..,35 trading days of start phase
_K_INNER = 25                      # within-day shuffles per offset → 200 worlds/FY
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
    """(ew, dl) daily-return lists over cal from the SAME fills (τ=_TAU, deleverage)."""
    cal_set = set(cal)
    day_items: defaultdict[datetime.date, list] = defaultdict(list)
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
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0
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

    worlds = [(o, k) for o in _OFFSETS for k in range(_K_INNER)]
    ew_st = {w: [] for w in worlds}      # stitched EW per world
    dl_st = {w: [] for w in worlds}      # stitched TILT-DL per world
    # per-offset deterministic (k=0 unshuffled) for the phase-sensitivity curve
    ew_phase = {o: [] for o in _OFFSETS}
    dl_phase = {o: [] for o in _OFFSETS}

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
        n_dts, n_cmap = _closes(n225)
        n_idx = {d: i for i, d in enumerate(n_dts)}
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        regime_of = {(c.stock_code, c.entry_date):
                     _regime_at(n_dts, n_idx, n_cmap, c.entry_date) for c in cands}

        for o in _OFFSETS:
            eff_start = cal[o] if o < len(cal) else cal[-1]
            pool_o = [c for c in cands if c.entry_date >= eff_start]
            # deterministic (sorted) baseline for the phase-sensitivity curve
            base = run_simulation(sorted(pool_o, key=lambda c: c.entry_date),
                                  cbt._EXIT_RULE, caches, cfg.end)
            bew, bdl = _two_series(base, stock_dts, regime_of, cal)
            ew_phase[o] += bew[1:]; dl_phase[o] += bdl[1:]
            for k in range(_K_INNER):
                rng = random.Random(o * 10_000 + k)
                shuf = pool_o[:]
                rng.shuffle(shuf)
                res = run_simulation(shuf, cbt._EXIT_RULE, caches, cfg.end)
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
    print(f"EV-TILT vs COMBINED PHASE+ORDER NULL — {len(_OFFSETS)} offsets × {_K_INNER} shuffles "
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

    # phase-sensitivity: how much does the deterministic Δ move with start phase alone?
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
        verdict = ("SURVIVES — TILT-DL beats EW across regime-timing realizations, not just the canonical "
                   "phase → the neutral-trim edge is robust to regime timing. Escalate to operator / "
                   "lot-granularity check.")
    else:
        verdict = ("COLLAPSES — the fill-order-null edge does NOT survive start-phase variation → it was "
                   "canonical-phase regime-timing luck the fill-order null could not see. REJECT, consistent "
                   "with the OOS FY2025 flip and the start-phase-null precedent.")
    print(f"  VERDICT: {verdict}")
    print(f"  (Gate: P(Δ>0)≥0.95 AND 95% CI-lo>0 in the wider phase+order band.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
