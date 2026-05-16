"""Bootstrap CI: AdxAdaptive vs ZsTpSl(2,2,0.3) on the ADX<25 sub-cohort.

Decision-aid for the proposed "dual-preview" Daily-tab UX where AdxAdaptive
becomes a clickable alternative TP/SL candidate when the entry-bar ADX(14)
indicates a moderate (15≤ADX<25) or choppy (ADX<15) regime — i.e. the
buckets where AdxAdaptive picks something *different* from the production
default ZsTpSl(2.0, 2.0, 0.3).

AdxAdaptive's *global* benchmark is poor (Sharpe −1.58 in src/exit/benchmark.md)
because the strong-trend bucket uses AdxTrail which cuts trades too short
across the universe. The hypothesis here is narrower: on the sub-cohort
where entry ADX < 25, AdxAdaptive's tighter TP/SL bands and shorter time
caps may beat ZsTpSl(2,2,0.3,max=40) by avoiding the structurally-unreachable
TP problem (the 6753.T 2026-05-12 case that triggered this whole chain).

Approach:
- Run benchmark for [ZsTpSl(2,2,0.3), AdxAdaptiveRule] across loadable FYs.
- Build (stock, entry_date) → entry_ADX map from cached bars.
- Filter ExitResult lists to trades with entry ADX < 25.
- Optionally split into moderate (15≤ADX<25) and choppy (ADX<15) sub-buckets.
- Bootstrap 10,000 iters of Sharpe difference per slice, report 95% CI.

Run:
    uv run --env-file devenv python -m src.analysis.adx_adaptive_subcohort_bootstrap
"""

from __future__ import annotations

import datetime
import math
import random
import statistics
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select

from src.analysis.exit_benchmark import (
    FY_CONFIGS,
    _load_cache,
    _load_rep_codes,
)
from src.analysis.models import StockClusterRun
from src.data.db import get_session
from src.exit.adx_adaptive import AdxAdaptiveRule
from src.exit.base import EntryCandidate, ExitResult
from src.exit.entry_scanner import scan_entries
from src.exit.exit_simulator import run_simulation
from src.exit.zs_tp_sl import ZsTpSl
from src.simulator.cache import DataCache

_N225 = "^N225"
_N_BOOT = 10_000
_RANDOM_SEED = 20260516
_MID_ADX  = 15.0
_HIGH_ADX = 25.0


# ─────────────────────────────────────────────────────────────────────────────
# Entry-bar ADX lookup
# ─────────────────────────────────────────────────────────────────────────────

def _entry_adx_map(
    caches: dict[str, DataCache],
    candidates: list[EntryCandidate],
) -> dict[tuple[str, datetime.date], float]:
    """For each candidate, find the bar at entry_date in its cache and read ADX14.

    Returns NaN-marker (float('nan')) for entries where ADX is not computed
    (warmup period or missing).
    """
    out: dict[tuple[str, datetime.date], float] = {}
    bars_by_stock_date: dict[str, dict[datetime.date, list]] = {}
    for code, cache in caches.items():
        d: dict[datetime.date, list] = {}
        for b in cache.bars:
            d.setdefault(b.dt.date(), []).append(b)
        bars_by_stock_date[code] = d

    for c in candidates:
        by_date = bars_by_stock_date.get(c.stock_code, {})
        bars = by_date.get(c.entry_date, [])
        if not bars:
            out[(c.stock_code, c.entry_date)] = float("nan")
            continue
        adx = bars[-1].indicators.get("ADX14", float("nan"))
        out[(c.stock_code, c.entry_date)] = float(adx) if adx is not None else float("nan")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Per-FY runner
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Returns:
    """Per-rule per-ADX-bucket list of return_pct values."""
    moderate: list[float]   # 15 ≤ ADX < 25
    choppy:   list[float]   # ADX < 15

    def all_sub25(self) -> list[float]:
        return self.moderate + self.choppy


def _bucket(adx: float) -> str | None:
    if math.isnan(adx):
        return None        # exclude warmup entries
    if adx >= _HIGH_ADX:
        return None        # outside sub-cohort
    if adx >= _MID_ADX:
        return "moderate"
    return "choppy"


def _split_returns(
    results: list[ExitResult],
    adx_map: dict[tuple[str, datetime.date], float],
) -> _Returns:
    r = _Returns(moderate=[], choppy=[])
    for x in results:
        adx = adx_map.get((x.stock_code, x.entry_date), float("nan"))
        b = _bucket(adx)
        if b is None:
            continue
        getattr(r, b).append(x.return_pct)
    return r


def _available_fys() -> list:
    with get_session() as s:
        avail = set(s.execute(select(StockClusterRun.fiscal_year)).scalars().all())
    return [c for c in FY_CONFIGS if c.stock_set in avail]


def _run_one_fy(cfg) -> tuple[_Returns, _Returns, dict[str, int]] | None:
    logger.info("FY {} ({} – {})", cfg.label, cfg.start, cfg.end)
    rep_codes = _load_rep_codes(cfg.stock_set)
    n225 = _load_cache(_N225, cfg.start, cfg.end)
    if n225 is None:
        logger.warning("  ^N225 missing for {} — skipping", cfg.label)
        return None
    caches: dict[str, DataCache] = {}
    for code in rep_codes:
        c = _load_cache(code, cfg.start, cfg.end)
        if c: caches[code] = c
    logger.info("  {}/{} caches loaded", len(caches), len(rep_codes))

    cands: list[EntryCandidate] = []
    for code, cache in caches.items():
        cands.extend(scan_entries(cache, n225, cfg.start, cfg.end))
    cands.sort(key=lambda c: c.entry_date)

    adx_map = _entry_adx_map(caches, cands)
    n_sub25 = sum(1 for v in adx_map.values()
                  if not math.isnan(v) and v < _HIGH_ADX)
    n_mod   = sum(1 for v in adx_map.values()
                  if not math.isnan(v) and _MID_ADX <= v < _HIGH_ADX)
    n_chop  = sum(1 for v in adx_map.values()
                  if not math.isnan(v) and v < _MID_ADX)
    logger.info("  {} candidates ({} ADX<25: {} moderate + {} choppy)",
                len(cands), n_sub25, n_mod, n_chop)

    zs_rule  = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
    adx_rule = AdxAdaptiveRule()
    zs_res   = run_simulation(cands, zs_rule,  caches, cfg.end)
    adx_res  = run_simulation(cands, adx_rule, caches, cfg.end)
    logger.info("  zs n={}  adx_adaptive n={}", len(zs_res), len(adx_res))

    zs_split  = _split_returns(zs_res,  adx_map)
    adx_split = _split_returns(adx_res, adx_map)
    info = {"n_cands": len(cands), "n_sub25": n_sub25,
            "n_mod": n_mod, "n_chop": n_chop}
    return zs_split, adx_split, info


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap (independent two-sample Sharpe diff)
# ─────────────────────────────────────────────────────────────────────────────

def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return float("nan")
    m = statistics.mean(returns)
    try:
        s = statistics.stdev(returns)
    except statistics.StatisticsError:
        return float("nan")
    if s <= 0:
        return float("nan")
    return m / s * math.sqrt(252)


def _bootstrap(
    a: list[float], b: list[float],
    n_boot: int = _N_BOOT, seed: int = _RANDOM_SEED,
) -> tuple[float, float, float, float]:
    """Return (Δ point, ci_lo_95, ci_hi_95, p(Δ≤0))."""
    if len(a) < 2 or len(b) < 2:
        return float("nan"), float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    na, nb = len(a), len(b)
    deltas: list[float] = []
    for _ in range(n_boot):
        sa = [a[rng.randrange(na)] for _ in range(na)]
        sb = [b[rng.randrange(nb)] for _ in range(nb)]
        deltas.append(_sharpe(sa) - _sharpe(sb))
    deltas = [d for d in deltas if not math.isnan(d)]
    deltas.sort()
    point = _sharpe(a) - _sharpe(b)
    lo = deltas[int(0.025 * len(deltas))]
    hi = deltas[int(0.975 * len(deltas))]
    p_neg = sum(1 for d in deltas if d <= 0) / len(deltas)
    return point, lo, hi, p_neg


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(returns: list[float]) -> str:
    if len(returns) < 2:
        return f"  —    (n={len(returns)})"
    s = _sharpe(returns)
    return f"{s:+6.2f}  (n={len(returns):>3})"


def main() -> None:
    zs_agg  = _Returns(moderate=[], choppy=[])
    adx_agg = _Returns(moderate=[], choppy=[])
    fy_list: list[str] = []
    fy_infos: list[tuple[str, dict[str, int]]] = []

    for cfg in _available_fys():
        out = _run_one_fy(cfg)
        if out is None:
            continue
        zs_r, adx_r, info = out
        zs_agg.moderate  += zs_r.moderate;  zs_agg.choppy  += zs_r.choppy
        adx_agg.moderate += adx_r.moderate; adx_agg.choppy += adx_r.choppy
        fy_list.append(cfg.label)
        fy_infos.append((cfg.label, info))

    if not fy_list:
        raise RuntimeError("No usable FYs.")

    print("\n" + "="*80)
    print("BOOTSTRAP — Sharpe(AdxAdaptive) − Sharpe(ZsTpSl 2.0/2.0/0.3)")
    print("                   restricted to entry-bar ADX(14) < 25")
    print("="*80)
    print(f"\nFYs: {', '.join(fy_list)}  ({len(fy_list)} of 7)")
    print(f"Bootstrap iterations: {_N_BOOT:,}  (independent two-sample)")
    print(f"Random seed: {_RANDOM_SEED}\n")

    print("— PER-FY ENTRY COUNTS —")
    print(f"  {'FY':<8} {'cands':>6} {'ADX<25':>8} {'moderate':>10} {'choppy':>8}")
    for label, info in fy_infos:
        print(f"  {label:<8} {info['n_cands']:>6} {info['n_sub25']:>8} "
              f"{info['n_mod']:>10} {info['n_chop']:>8}")

    print("\n— SHARPE POINT ESTIMATES (aggregate within sub-cohort) —")
    print(f"  {'bucket':<10}  {'AdxAdaptive':<14}  {'ZsTpSl 2/2/0.3':<14}")
    print("  " + "-"*42)
    print(f"  {'ADX<25':<10}  {_fmt(adx_agg.all_sub25()):<14}  {_fmt(zs_agg.all_sub25()):<14}")
    print(f"  {'  moderate':<10}  {_fmt(adx_agg.moderate):<14}  {_fmt(zs_agg.moderate):<14}")
    print(f"  {'  choppy':<10}  {_fmt(adx_agg.choppy):<14}  {_fmt(zs_agg.choppy):<14}")

    print("\n— Δ SHARPE (AdxAdaptive − ZsTpSl) 95% BOOTSTRAP CI —")
    print(f"  {'bucket':<10}  {'Δ point':>8}  {'95% CI':>22}  {'p(Δ≤0)':>10}  verdict")
    print("  " + "-"*82)
    for label, a, b in [
        ("ADX<25", adx_agg.all_sub25(), zs_agg.all_sub25()),
        ("moderate", adx_agg.moderate, zs_agg.moderate),
        ("choppy",   adx_agg.choppy,   zs_agg.choppy),
    ]:
        point, lo, hi, p = _bootstrap(a, b)
        if math.isnan(point):
            print(f"  {label:<10}  {'—':>8}  {'—':>22}  {'—':>10}  insufficient n")
            continue
        ci = f"[{lo:+.2f}, {hi:+.2f}]"
        v = ("Δ>0 significant"  if lo > 0 else
             "Δ<0 significant"  if hi < 0 else
             "Δ inside 0")
        print(f"  {label:<10}  {point:>+8.3f}  {ci:>22}  {p:>10.3f}  {v}")

    print("\n— DECISION AID —")
    pt, lo, hi, p = _bootstrap(adx_agg.all_sub25(), zs_agg.all_sub25())
    if math.isnan(pt):
        print("  → INSUFFICIENT n — cannot decide.")
    elif lo > 0:
        print("  → SHIP dual-preview: AdxAdaptive's edge on ADX<25 sub-cohort is significant.")
        print("    Wire option-3-dual-preview into daily.py.")
    elif hi < 0:
        print("  → DO NOT SHIP: AdxAdaptive is significantly WORSE on this sub-cohort.")
        print("    Keep ZsTpSl, document per-trade override (option 1) as known operator move.")
    else:
        print("  → AMBIGUOUS: CI crosses zero. Recommendation:")
        print("    - If |Δ point| < 0.10: keep ZsTpSl; UX change not justified by data.")
        print("    - If Δ point > +0.10 and p(Δ≤0) < 0.30: ship dual-preview as a")
        print("      *display-only* option (operator click required); revisit if data accrues.")
        print("    - If Δ point < −0.10: keep ZsTpSl.")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
