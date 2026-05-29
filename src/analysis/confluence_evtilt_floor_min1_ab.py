"""Floor-vs-take-1-lot A/B for the neutral-regime sizing tilt (operator question 2026-05-29).

The certified rule trims NEUTRAL-N225-momentum confluence entries to floor(0.5·base_lots). Under 100-share
lots at ¥2M/6 slots, base_lots==1 (share price ~¥1,667–3,333) → floor(0.5)=0 = SKIP — and ~50% of neutral
fills are 1-lot names, so the rule skips the entire mid-priced half of the neutral book. The source finding
said "trim, NOT skip" (neutral EV is positive, α +0.33%), so the operator asked: in the neutral regime,
is it better to still TAKE 1 lot for those names rather than skip them?

Three arms, applied to the SAME fills per shuffle (perfect pairing; weights differ only):
  - EW       : base_lots in all regimes (the equal-weight baseline).
  - TILT-FLOOR: neutral → floor(0.5·base) (the CERTIFIED rule; 1-lot → 0 = skip).
  - TILT-MIN1 : neutral → max(1, floor(0.5·base)) (NEVER skip an affordable neutral name; 1-lot → 1 = take,
                ≥2-lot → half as before). Differs from FLOOR ONLY on base==1 neutral names.

So FLOOR vs MIN1 isolates exactly the operator's question: skip the expensive 1-lot neutral names, or take
1 lot? MIN1 deleverages LESS in neutral (captures the positive neutral EV) → the τ dose-response predicts a
SMALLER drawdown cut but a smaller return drag. The A/B turns that into numbers.

Two passes: (A) full FY2018–2025 integer-lot null with the deployed frozen cutoffs (−0.1%/+8.1%); (B) the
held-out cutoff-CV (train cutoffs on FY2018–22, score FY2023–25) — the window the /sign-debate verdict rests
on. K=200 paired shuffles, ¥2M/6-slot/100-sh book.

OUTCOME (2026-05-29): KEEP FLOOR (skip). The dose-response prior holds — taking 1 lot (MIN1) gives back
~half the drawdown benefit. Full FY2018–25: EW maxDD −21.1%; FLOOR −17.0% (Δ vs EW +4.13pp, Sharpe
+0.126); MIN1 −19.2% (Δ vs EW +1.85pp, Sharpe +0.075). Held-out FY2023–25: EW −17.3%; FLOOR −12.8%
(+4.51pp); MIN1 −15.7% (+1.61pp). MIN1 − FLOOR: ΔmaxDD −2.27pp/−2.90pp (P(MIN1 shallower)=0.04/0.03 →
FLOOR reliably the deeper DD cut), Δret +5.4pp/+1.0pp (the return MIN1 recovers). MIN1's edge over EW is
weak OOS (Sharpe +0.024, P=0.63, CI spans 0). Skipping the expensive 1-lot neutral names (τ_eff 0.39/0.34
vs MIN1 0.55/0.61) IS most of the drawdown lever — it is load-bearing, not an artifact. As a DRAWDOWN
lever the certified FLOOR rule is correct; MIN1 is only a milder, more return-friendly operating point.
The shipped guideline (src/portfolio/sizing.py neutral_trim_lots = floor) is unchanged.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_evtilt_floor_min1_ab
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
from src.portfolio.sizing import position_weight, recommended_lots
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200
_SLOTS = 6
_BUDGET = 2_000_000
_N225_MOM = 60
_TAU = 0.5
_T1_DEPLOY, _T2_DEPLOY = -0.001, 0.081     # deployed frozen cutoffs (full-period pass A)
_ARMS = ("ew", "floor", "min1")
_ALL_FYS = [FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                     "classified2017")] + list(RS_FY_CONFIGS)
_TRAIN = {"FY2018", "FY2019", "FY2020", "FY2021", "FY2022"}
_TEST = {"FY2023", "FY2024", "FY2025"}
FIRES: dict = {}


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


def _arm_lots(base, regime, arm):
    """Lots for each arm. FLOOR and MIN1 differ ONLY on base==1 neutral names."""
    if arm == "ew" or regime != "neutral":
        return base
    if arm == "floor":
        return int(_TAU * base)            # 1 -> 0 (skip)
    return max(1, int(_TAU * base))        # min1: 1 -> 1 (take), >=2 -> half


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
    codes = cbt._stocks_for_fy(cfg.stock_set)
    if not codes:
        return None
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
    if not caches:
        return None
    corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
    zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
    n_dts, n_cmap = _closes(n225)
    n_idx = {d: i for i, d in enumerate(n_dts)}
    stock_dts = {code: _closes(c) for code, c in caches.items()}
    cands = []
    for code in caches:
        cands.extend(cbt._candidates_for_stock(
            code, FIRES.get(code, []), caches[code],
            corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))

    def _affordable(c) -> bool:
        _, cmap = stock_dts.get(c.stock_code, ([], {}))
        px = cmap.get(c.entry_date)
        return px is not None and recommended_lots(_BUDGET, float(px), _SLOTS) > 0
    cands = [c for c in cands if _affordable(c)]
    cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
    return dict(caches=caches, n_dts=n_dts, n_cmap=n_cmap, n_idx=n_idx,
                stock_dts=stock_dts, cands=cands, cal=cal, end=cfg.end)


def _score_fy(fy, regime_of, st, diag):
    """Run K paired shuffles for one FY, accumulate per-arm stitched series into st."""
    caches, stock_dts, cands, cal = fy["caches"], fy["stock_dts"], fy["cands"], fy["cal"]
    cal_set = set(cal)
    for k in range(_K):
        rng = random.Random(k)
        pool = cands[:]
        rng.shuffle(pool)
        results = exsim.run_simulation(pool, cbt._EXIT_RULE, caches, fy["end"])
        day = {a: defaultdict(float) for a in _ARMS}
        for p in results:
            sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
            base = recommended_lots(_BUDGET, float(p.entry_price), _SLOTS)
            reg = regime_of.get((p.stock_code, p.entry_date), "na")
            w = {a: position_weight(_arm_lots(base, reg, a), float(p.entry_price), _BUDGET)
                 for a in _ARMS}
            if k == 0 and reg == "neutral":
                diag["neutral"] += 1
                diag["base1"] += 1 if base == 1 else 0
                diag["floor_lots"] += _arm_lots(base, reg, "floor")
                diag["min1_lots"] += _arm_lots(base, reg, "min1")
                diag["base_lots"] += base
            for d, r in _pos_daily(p, sdts, scmap).items():
                if d in cal_set:
                    for a in _ARMS:
                        day[a][d] += r * w[a]
        for a in _ARMS:
            st[a][k] += [day[a].get(d, 0.0) for d in cal][1:]


def _report(title, st, diag):
    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in _ARMS}
    rd = {a: np.array([_ret_dd(st[a][k]) for k in range(_K)]) for a in _ARMS}
    ret = {a: rd[a][:, 0] for a in _ARMS}
    dd = {a: rd[a][:, 1] for a in _ARMS}
    print("\n" + "=" * 92)
    print(title)
    print("=" * 92)
    n = diag["neutral"] or 1
    print(f"neutral fills {diag['neutral']} | base==1 (FLOOR skips, MIN1 takes 1) {diag['base1']} "
          f"({diag['base1']/n*100:.0f}%) | τ_eff FLOOR {diag['floor_lots']/max(1,diag['base_lots']):.3f} "
          f"vs MIN1 {diag['min1_lots']/max(1,diag['base_lots']):.3f}")
    print(f"\n{'arm':<12}{'Sharpe':>9}{'sd':>7}{'ret%':>8}{'maxDD%':>9}")
    lab = {"ew": "EW", "floor": "TILT-FLOOR", "min1": "TILT-MIN1"}
    for a in _ARMS:
        print(f"{lab[a]:<12}{sh[a].mean():>9.3f}{sh[a].std():>7.3f}"
              f"{ret[a].mean()*100:>7.0f}%{dd[a].mean()*100:>8.1f}%")

    def pair(name, a, b):
        dS = sh[a] - sh[b]; dD = dd[a] - dd[b]; dR = ret[a] - ret[b]
        print(f"  {name:<18} ΔSharpe {dS.mean():+.3f} P{(dS>0).mean():.2f} "
              f"CI[{np.percentile(dS,2.5):+.3f},{np.percentile(dS,97.5):+.3f}] | "
              f"ΔmaxDD {dD.mean()*100:+.2f}pp P{(dD>0).mean():.2f} | Δret {dR.mean()*100:+.1f}pp")
    print("\n[paired Δ, same fills each shuffle]")
    pair("FLOOR − EW", "floor", "ew")
    pair("MIN1  − EW", "min1", "ew")
    pair("MIN1  − FLOOR", "min1", "floor")
    return sh, dd, ret


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
    for sg, st_, fa in rows:
        FIRES.setdefault(st_, []).append((sg, fa.date() if hasattr(fa, "date") else fa))

    exsim._MAX_LOW_CORR = 5
    # ── PASS A: full FY2018–2025, deployed frozen cutoffs. Also collect TRAIN momenta. ──
    stA = {a: [[] for _ in range(_K)] for a in _ARMS}
    diagA = defaultdict(int)
    train_moms: list[float] = []
    test_cache: dict[str, dict] = {}
    for cfg in _ALL_FYS:
        fy = _load_fy(cfg)
        if fy is None:
            continue
        regime_of = {(c.stock_code, c.entry_date):
                     _regime(_mom(fy["n_dts"], fy["n_idx"], fy["n_cmap"], c.entry_date),
                             _T1_DEPLOY, _T2_DEPLOY) for c in fy["cands"]}
        _score_fy(fy, regime_of, stA, diagA)
        if cfg.label in _TRAIN:
            for c in fy["cands"]:
                m = _mom(fy["n_dts"], fy["n_idx"], fy["n_cmap"], c.entry_date)
                if m is not None:
                    train_moms.append(m)
        if cfg.label in _TEST:
            test_cache[cfg.label] = fy        # reuse for pass B (avoid reload)
        logger.info("  PASS A {} done ({} cands)", cfg.label, len(fy["cands"]))

    # ── PASS B: held-out FY2023–25 with train-derived cutoffs (the verdict window) ──
    t1, t2 = float(np.percentile(train_moms, 33.33)), float(np.percentile(train_moms, 66.67))
    logger.info("  train cutoffs: bear<= {:.4f} < neutral<= {:.4f} < bull", t1, t2)
    stB = {a: [[] for _ in range(_K)] for a in _ARMS}
    diagB = defaultdict(int)
    for label in ("FY2023", "FY2024", "FY2025"):
        fy = test_cache.get(label)
        if fy is None:
            continue
        regime_of = {(c.stock_code, c.entry_date):
                     _regime(_mom(fy["n_dts"], fy["n_idx"], fy["n_cmap"], c.entry_date), t1, t2)
                     for c in fy["cands"]}
        _score_fy(fy, regime_of, stB, diagB)
        logger.info("  PASS B {} done", label)

    _report("PASS A — full FY2018–2025, deployed cutoffs (−0.1%/+8.1%)", stA, diagA)
    _report(f"PASS B — HELD-OUT FY2023–25, train cutoffs ({t1*100:+.2f}%/{t2*100:+.2f}%)", stB, diagB)

    print("\n" + "-" * 92)
    print("READ: FLOOR is the CERTIFIED rule (skip 1-lot neutral names); MIN1 takes 1 lot instead.")
    print("  'MIN1 − FLOOR' is the operator's question: + ΔmaxDD means taking 1 lot is SHALLOWER DD;")
    print("  − ΔmaxDD means skipping cuts more drawdown (the dose-response prior). Δret shows the")
    print("  return cost of skipping that MIN1 recovers.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
