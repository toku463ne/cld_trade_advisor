"""Bootstrap CI on the Sharpe gap between time_40b and zs_tp2.0_sl2.0_a0.3.

Prerequisite calc per /sign-debate judge (iter 1, 2026-05-16) on the
TimeStop(40) adoption proposal. The benchmark in src/exit/benchmark.md
shows time_40b Sharpe 2.23 (n=168) vs zs_tp2.0_sl2.0_a0.3 Sharpe 1.58
(n=229) — a 0.65 gap. Is this statistically significant given the sample
sizes and per-corr-mode breakdown?

Approach:
- Re-run the exit benchmark for just [TimeStop(40), ZsTpSl(2.0,2.0,0.3)]
  across whatever FYs are loadable in the dev DB (^N225 starts 2020-05-11
  so FY2018/2019 will be skipped).
- Capture per-event return_pct from each rule's ExitResult list.
- Bootstrap 10,000 iterations: resample with replacement from each rule's
  return distribution independently (the cohorts differ — portfolio
  constraints route candidates differently per rule, so paired bootstrap
  is not applicable).
- Compute Sharpe per resample, then Δ Sharpe (time_40b − zs).
- Report 95% CI on the Sharpe difference: aggregate, and stratified by
  corr_mode (high / mid / low).

Output is plain stdout — no markdown file written.

Run:
    uv run --env-file devenv python -m src.analysis.exit_sharpe_bootstrap
"""

from __future__ import annotations

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
from src.exit.base import EntryCandidate, ExitResult
from src.exit.entry_scanner import scan_entries
from src.exit.exit_simulator import run_simulation
from src.exit.time_stop import TimeStop
from src.exit.zs_tp_sl import ZsTpSl
from src.simulator.cache import DataCache

_N225 = "^N225"
_N_BOOT = 10_000
_RANDOM_SEED = 20260516


# ─────────────────────────────────────────────────────────────────────────────
# Run benchmark for just the two rules of interest
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Returns:
    """Per-rule per-corr-mode list of return_pct values."""
    high: list[float]
    mid:  list[float]
    low:  list[float]

    def all(self) -> list[float]:
        return self.high + self.mid + self.low

    def of(self, mode: str) -> list[float]:
        return getattr(self, mode)


def _collect_returns(results: list[ExitResult]) -> _Returns:
    r = _Returns(high=[], mid=[], low=[])
    for x in results:
        r.of(x.corr_mode).append(x.return_pct)
    return r


def _available_fys() -> list:
    with get_session() as s:
        avail = set(s.execute(select(StockClusterRun.fiscal_year)).scalars().all())
    return [c for c in FY_CONFIGS if c.stock_set in avail]


def _run_one_fy(cfg) -> tuple[_Returns, _Returns] | None:
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
    logger.info("  {} early-LOW candidates", len(cands))

    time_rule = TimeStop(max_bars=40)
    zs_rule   = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
    time_res  = run_simulation(cands, time_rule, caches, cfg.end)
    zs_res    = run_simulation(cands, zs_rule,   caches, cfg.end)
    logger.info("  time_40b n={}  zs n={}", len(time_res), len(zs_res))

    return _collect_returns(time_res), _collect_returns(zs_res)


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap
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


def _bootstrap_delta_sharpe(
    a: list[float],          # time_40b returns
    b: list[float],          # zs returns
    n_boot: int = _N_BOOT,
    seed: int = _RANDOM_SEED,
) -> tuple[float, float, float, float]:
    """Return (point_delta, ci_lo_95, ci_hi_95, p_value_one_sided_pos).

    p_value = fraction of bootstrap deltas ≤ 0 (one-sided test: is time_40b
    Sharpe genuinely > zs Sharpe?).
    """
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

def _fmt_sharpe(returns: list[float]) -> str:
    if len(returns) < 2:
        return "  —    (n<2)"
    s = _sharpe(returns)
    return f"{s:+6.2f}  (n={len(returns):>3})"


def main() -> None:
    time_agg = _Returns(high=[], mid=[], low=[])
    zs_agg   = _Returns(high=[], mid=[], low=[])
    fy_list: list[str] = []

    for cfg in _available_fys():
        out = _run_one_fy(cfg)
        if out is None:
            continue
        time_r, zs_r = out
        time_agg.high += time_r.high; time_agg.mid += time_r.mid; time_agg.low += time_r.low
        zs_agg.high   += zs_r.high;   zs_agg.mid   += zs_r.mid;   zs_agg.low   += zs_r.low
        fy_list.append(cfg.label)

    if not fy_list:
        raise RuntimeError("No usable FYs — check DB.")
    logger.info("Bootstrap on {} FYs: {}", len(fy_list), fy_list)

    print("\n" + "="*80)
    print("BOOTSTRAP — Sharpe(time_40b) − Sharpe(zs_tp2.0_sl2.0_a0.3)")
    print("="*80)
    print(f"\nFYs in cohort: {', '.join(fy_list)}  ({len(fy_list)} of 7)")
    print(f"Bootstrap iterations: {_N_BOOT:,}  (independent two-sample)")
    print(f"Random seed: {_RANDOM_SEED}")

    print("\n— SHARPE POINT ESTIMATES (aggregate within cohort) —")
    print(f"{'segment':<10}  {'time_40b':<14}  {'zs2.0/2.0/0.3':<14}")
    print("  " + "-"*42)
    for seg in ("all", "high", "mid", "low"):
        t = time_agg.all() if seg == "all" else time_agg.of(seg)
        z = zs_agg.all()   if seg == "all" else zs_agg.of(seg)
        print(f"  {seg:<10}  {_fmt_sharpe(t):<14}  {_fmt_sharpe(z):<14}")

    print("\n— Δ SHARPE (time_40b − zs) 95% BOOTSTRAP CI —")
    print(f"{'segment':<10}  {'Δ point':>8}  {'95% CI':>22}  {'p(Δ≤0)':>10}  {'verdict':<28}")
    print("  " + "-"*82)
    for seg in ("all", "high", "mid", "low"):
        t = time_agg.all() if seg == "all" else time_agg.of(seg)
        z = zs_agg.all()   if seg == "all" else zs_agg.of(seg)
        point, lo, hi, p = _bootstrap_delta_sharpe(t, z)
        if math.isnan(point):
            print(f"  {seg:<10}  {'—':>8}  {'—':>22}  {'—':>10}  insufficient data")
            continue
        ci_str = f"[{lo:+.2f}, {hi:+.2f}]"
        verdict = (
            "Δ>0 significant (lo>0)"  if lo > 0 else
            "Δ<0 significant (hi<0)"  if hi < 0 else
            "Δ inside 0 (not signif)"
        )
        print(f"  {seg:<10}  {point:>+8.3f}  {ci_str:>22}  {p:>10.3f}  {verdict:<28}")

    # Decision aid per judge's flip-rule
    print("\n— DECISION AID (per /sign-debate judge ruling) —")
    res_per_seg: dict[str, tuple[float, float, float, float]] = {}
    for seg in ("high", "mid", "low"):
        t = time_agg.of(seg); z = zs_agg.of(seg)
        res_per_seg[seg] = _bootstrap_delta_sharpe(t, z)

    all_pos = all(
        not math.isnan(lo) and lo > 0
        for (_, lo, _, _) in res_per_seg.values()
    )
    any_zero_cross = any(
        not math.isnan(lo) and not math.isnan(hi) and lo <= 0 <= hi
        for (_, lo, hi, _) in res_per_seg.values()
    )
    any_neg_signif = any(
        not math.isnan(hi) and hi < 0
        for (_, _, hi, _) in res_per_seg.values()
    )

    print(f"  CI lower bound > 0 in ALL corr_modes simultaneously?  {'YES' if all_pos else 'NO'}")
    print(f"  Any corr_mode CI crosses 0?                            {'YES' if any_zero_cross else 'NO'}")
    print(f"  Any corr_mode CI entirely below 0?                     {'YES' if any_neg_signif else 'NO'}")
    print()
    if all_pos:
        print("  → ADVANCE: Critic's revised 3-arm probe (Time / Zs / Segmented).")
        print("    TimeStop's edge is real across all corr_modes; full investigation justified.")
    elif any_neg_signif:
        print("  → COLLAPSE: Segmented-only investigation (TimeStop loses some segment).")
        print("    Global swap is REJECTED on bootstrap; only segmented arm worth probing.")
    elif any_zero_cross:
        print("  → COLLAPSE: Segmented-only investigation (uncertainty in ≥1 corr_mode).")
        print("    Aggregate gap may exist but not robustly per-segment; probe segmented arm only.")
    else:
        print("  → AMBIGUOUS: review CIs manually.")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
