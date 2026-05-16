"""Breadth-gate A/B probe — does (rev_nhi ∧ SMA(50)) HIGH improve outcomes?

Tests three arms on regime_sign-driven entries with the production
ZsTpSl(2.0, 2.0, 0.3) exit rule and portfolio constraints
(≤1 high-corr, ≤3 low/mid-corr):

  - Baseline      : take every proposal that fits portfolio constraints
  - Skip-gate     : when (rev_nhi HIGH ∧ SMA(50) HIGH), skip new entries
  - Half-size     : when both HIGH, take entries but at 0.5 weight

The AND-gate identifies the concentrated reversal-risk regime — the
2×2 conditional analysis (2026-05-16) showed n=68 days with both
HIGH and forward 10-bar N225 ≈ 0%, vs +1.25% baseline.  This probe
asks whether *acting on* that signal improves a real regime_sign
backtest, not just whether the signal exists.

Pre-registered gates (locked before running):
  - PASS skip-gate  : ΔSharpe ≥ +0.10 vs baseline on ALL cohort
                      AND CI lower bound > 0
                      AND ΔSharpe ≥ 0 on ≥ 2/3 individual cohorts
                      AND n(AND-gate trades) ≥ 30 (else under-powered)
  - PASS half-size  : same gate but on weighted-Sharpe
  - REJECT          : neither arm passes → keep current (no gate)

Skip-gate is the cleaner test (per-trade Sharpe on a sub-cohort).
Half-size is approximated by weighting each AND-gate trade's return
by 0.5 and re-computing Sharpe over the full set.

Run:
    uv run --env-file devenv python -m src.analysis.breadth_gate_probe
"""

from __future__ import annotations

import datetime
import math
import random
import statistics
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select

from src.analysis.exit_benchmark import _add_adx, _load_rep_codes
from src.analysis.models import SignBenchmarkRun, StockClusterRun
from src.data.db import get_session
from src.exit.base import EntryCandidate, ExitResult
from src.exit.exit_simulator import run_simulation
from src.exit.zs_tp_sl import ZsTpSl
from src.indicators.rev_n_regime import RevNRegime
from src.indicators.sma_regime import SMARegime
from src.indicators.zigzag import detect_peaks
from src.simulator.cache import DataCache
from src.strategy.regime_sign import RegimeSignStrategy

_N225        = "^N225"
_GRAN        = "1d"
_ZZ_SIZE     = 5
_ZZ_MIDDLE   = 2
_ZS_LOOKBACK = 16
_N_BOOT      = 10_000
_SEED        = 20260516

tz = datetime.timezone.utc
END_DT      = datetime.datetime(2026, 5, 15, 23, 59, 59, tzinfo=tz)
BUILD_START = datetime.datetime(2023, 3, 1, tzinfo=tz)

COHORTS = [
    (datetime.date(2024, 4, 1),  datetime.date(2025, 3, 31), "FY2024"),
    (datetime.date(2025, 4, 1),  datetime.date(2026, 3, 31), "FY2025"),
    (datetime.date(2026, 1, 1),  datetime.date(2026, 5, 15), "2026YTD"),
    (datetime.date(2023, 4, 1),  datetime.date(2026, 5, 15), "ALL"),
]


def _build_zs_legs_from_cache(
    cache: DataCache,
    as_of: datetime.date,
    n225_dates: set[datetime.date],
) -> tuple[float, ...]:
    """Inline replica of portfolio.crud._build_zs_legs but using pre-loaded caches."""
    bbd: dict[datetime.date, list] = {}
    for b in cache.bars:
        bbd.setdefault(b.dt.date(), []).append(b)
    days = sorted((d, g) for d, g in bbd.items() if d in n225_dates and d <= as_of)
    if not days:
        return ()
    highs = [max(b.high for b in g) for _, g in days]
    lows  = [min(b.low  for b in g) for _, g in days]
    peaks = detect_peaks(highs, lows, size=_ZZ_SIZE, middle_size=_ZZ_MIDDLE)
    peaks_sorted = sorted(peaks, key=lambda p: p.bar_index)
    leg_sizes: list[float] = []
    prev: float | None = None
    for p in peaks_sorted:
        if prev is not None:
            leg_sizes.append(abs(p.price - prev))
        prev = p.price
    return tuple(leg_sizes[-_ZS_LOOKBACK:])


@dataclass
class TaggedResult:
    result:      ExitResult
    on_and_gate: bool


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


def _weighted_sharpe(rets: list[float], weights: list[float]) -> float:
    """Sharpe over weighted contributions (each trade contributes w_i * r_i)."""
    if len(rets) < 2:
        return float("nan")
    wr = [w * r for w, r in zip(weights, rets)]
    return _sharpe(wr)


def _bootstrap_diff(a: list[float], b: list[float],
                    n_boot: int = _N_BOOT, seed: int = _SEED) -> tuple[float, float, float, float]:
    """Returns (Δ point [Sharpe b - Sharpe a], ci_lo, ci_hi, p(Δ>0))."""
    if len(a) < 2 or len(b) < 2:
        return float("nan"), float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    na, nb = len(a), len(b)
    diffs: list[float] = []
    for _ in range(n_boot):
        sa = [a[rng.randrange(na)] for _ in range(na)]
        sb = [b[rng.randrange(nb)] for _ in range(nb)]
        d  = _sharpe(sb) - _sharpe(sa)
        if not math.isnan(d):
            diffs.append(d)
    if not diffs:
        return float("nan"), float("nan"), float("nan"), float("nan")
    diffs.sort()
    point = _sharpe(b) - _sharpe(a)
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[int(0.975 * len(diffs))]
    p_pos = sum(1 for d in diffs if d > 0) / len(diffs)
    return point, lo, hi, p_pos


def _build_caches(strategy: RegimeSignStrategy) -> tuple[dict[str, DataCache], DataCache]:
    """Return (stock_caches with ADX, n225_cache).  Strategy caches lack ADX."""
    stock_caches: dict[str, DataCache] = {}
    with get_session() as session:
        for code, src in strategy._stock_caches.items():
            c = DataCache(code, _GRAN)
            try:
                c.load(session, BUILD_START, END_DT)
            except Exception:
                continue
            if c.bars:
                _add_adx(c)
                stock_caches[code] = c
    return stock_caches, strategy._n225_cache


def _proposals_to_candidates(
    proposals_by_date: dict[datetime.date, list],
    stock_caches:      dict[str, DataCache],
    n225_dates:        set[datetime.date],
) -> list[EntryCandidate]:
    cands: list[EntryCandidate] = []
    for d, props in proposals_by_date.items():
        for p in props:
            cache = stock_caches.get(p.stock_code)
            if cache is None:
                continue
            zs_hist = _build_zs_legs_from_cache(cache, d, n225_dates)
            cands.append(EntryCandidate(
                stock_code  = p.stock_code,
                entry_date  = d,
                entry_price = 0.0,            # simulator uses fill bar's open
                corr_mode   = p.corr_mode,
                corr_n225   = p.corr_n225,
                zs_history  = zs_hist,
            ))
    return cands


def _run_cohort(
    label:     str,
    c_start:   datetime.date,
    c_end:     datetime.date,
    strategy:  RegimeSignStrategy,
    stock_caches: dict[str, DataCache],
    n225_dates: set[datetime.date],
    and_gate_dates: set[datetime.date],
) -> dict:
    logger.info("[{}] proposing across {} – {}", label, c_start, c_end)
    start_dt = datetime.datetime(c_start.year, c_start.month, c_start.day, tzinfo=tz)
    end_dt   = datetime.datetime(c_end.year,   c_end.month,   c_end.day, 23, 59, 59, tzinfo=tz)
    out = strategy.propose_range(start_dt, end_dt)
    cands = _proposals_to_candidates(out, stock_caches, n225_dates)
    logger.info("[{}] {} candidates from {} proposal days", label, len(cands), len(out))
    if not cands:
        return {}

    rule = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
    results = run_simulation(cands, rule, stock_caches, c_end)
    logger.info("[{}] {} trades simulated", label, len(results))

    tagged = [TaggedResult(r, r.entry_date in and_gate_dates) for r in results]
    gated  = [t for t in tagged if t.on_and_gate]
    ungated = [t for t in tagged if not t.on_and_gate]

    base_rets = [t.result.return_pct for t in tagged]
    skip_rets = [t.result.return_pct for t in ungated]
    # half-size: gated trades contribute 0.5 × r, others 1.0
    half_weights = [0.5 if t.on_and_gate else 1.0 for t in tagged]
    half_rets    = base_rets

    s_base = _sharpe(base_rets)
    s_skip = _sharpe(skip_rets)
    s_half = _weighted_sharpe(half_rets, half_weights)

    pt_skip, lo_skip, hi_skip, pp_skip = _bootstrap_diff(base_rets, skip_rets)
    half_weighted_contrib = [w * r for w, r in zip(half_weights, half_rets)]
    pt_half, lo_half, hi_half, pp_half = _bootstrap_diff(base_rets, half_weighted_contrib)

    def _mean_pct(xs: list[float]) -> float:
        return statistics.mean(xs) * 100 if xs else float("nan")

    return dict(
        label=label,
        n_base=len(tagged), n_gate=len(gated), n_skip=len(ungated),
        sharpe_base=s_base, sharpe_skip=s_skip, sharpe_half=s_half,
        mean_base=_mean_pct(base_rets), mean_skip=_mean_pct(skip_rets),
        mean_gate=_mean_pct([t.result.return_pct for t in gated]),
        ds_skip=pt_skip, ci_lo_skip=lo_skip, ci_hi_skip=hi_skip,
        ds_half=pt_half, ci_lo_half=lo_half, ci_hi_half=hi_half,
    )


def _fmt(v: float, w: int = 7, dec: int = 2) -> str:
    return f"{v:>{w}.{dec}f}" if not math.isnan(v) else " " * (w-1) + "—"


def main() -> None:
    with get_session() as s:
        avail = set(s.execute(select(StockClusterRun.fiscal_year)).scalars().all())
        if "classified2024" not in avail:
            raise RuntimeError("classified2024 cluster set not found")
        run_ids = list(s.execute(
            select(SignBenchmarkRun.id).where(SignBenchmarkRun.stock_set.in_(['classified2024']))
        ).scalars().all())

    logger.info("Building strategy …")
    strategy = RegimeSignStrategy.from_config(
        stock_set='classified2024', run_ids=run_ids,
        start=BUILD_START, end=END_DT, mode='trade', min_dr=0.55,
    )

    logger.info("Reloading stock caches with ADX …")
    stock_caches, n225 = _build_caches(strategy)
    n225_dates = {b.dt.date() for b in n225.bars}

    logger.info("Building RevNRegime + SMARegime …")
    n225_date_list = sorted(n225_dates)
    revn = RevNRegime.build(strategy._stock_caches, n225_date_list)
    sma  = SMARegime.build (strategy._stock_caches, n225_date_list)

    and_gate_dates = {d for d in n225_date_list if revn.is_high(d) and sma.is_high(d)}
    logger.info("AND-gate days: {} of {} ({}%)",
                len(and_gate_dates), len(n225_date_list),
                round(100*len(and_gate_dates)/len(n225_date_list), 1))

    rows = []
    for c_start, c_end, lbl in COHORTS:
        r = _run_cohort(lbl, c_start, c_end, strategy, stock_caches, n225_dates, and_gate_dates)
        if r: rows.append(r)

    print("\n" + "="*100)
    print("BREADTH-GATE A/B — regime_sign cohort, ZsTpSl(2.0, 2.0, 0.3) exit, portfolio-constrained")
    print("  AND-gate = (rev_nhi top quintile) AND (SMA50 top quintile) on entry_date")
    print("="*100)

    print(f"\n  AND-gate days in full window: {len(and_gate_dates)} of {len(n225_date_list)}")
    print("\n— per-cohort trade counts and mean returns —")
    print(f"  {'cohort':<10} {'n_base':>7} {'n_gate':>7} {'n_skip':>7}  "
          f"{'mean_base %':>11} {'mean_skip %':>11} {'mean_gate %':>11}")
    for r in rows:
        print(f"  {r['label']:<10} {r['n_base']:>7} {r['n_gate']:>7} {r['n_skip']:>7}  "
              f"{_fmt(r['mean_base'], 10, 3)}  {_fmt(r['mean_skip'], 10, 3)}  {_fmt(r['mean_gate'], 10, 3)}")

    print("\n— per-cohort Sharpe per arm —")
    print(f"  {'cohort':<10}  {'baseline':>9}  {'skip-gate':>9}  {'half-size':>9}")
    for r in rows:
        print(f"  {r['label']:<10}  {_fmt(r['sharpe_base'],9)}  {_fmt(r['sharpe_skip'],9)}  {_fmt(r['sharpe_half'],9)}")

    print("\n— Δ Sharpe vs baseline, 95% bootstrap CI —")
    print(f"  {'cohort':<10}  {'arm':<10}  {'Δ point':>9}  {'95% CI':>22}  {'p(Δ>0)':>7}  verdict")
    for r in rows:
        for arm, pt, lo, hi, ppos in [
            ("skip-gate", r['ds_skip'], r['ci_lo_skip'], r['ci_hi_skip'], 0.0),
            ("half-size", r['ds_half'], r['ci_lo_half'], r['ci_hi_half'], 0.0),
        ]:
            if math.isnan(pt):
                print(f"  {r['label']:<10}  {arm:<10}  (insufficient n)")
                continue
            ci = f"[{lo:+.3f}, {hi:+.3f}]"
            sig = "✓PASS" if (pt >= 0.10 and lo > 0) else "✗FAIL"
            print(f"  {r['label']:<10}  {arm:<10}  {pt:>+8.3f}  {ci:>22}  {sig}")

    print("\n— PRE-REGISTERED GATE CHECK (ALL cohort) —")
    all_row = next((r for r in rows if r['label'] == 'ALL'), None)
    if all_row is None:
        print("  ALL cohort missing.")
        return

    n_gate_total = all_row['n_gate']
    gate_n_ok = n_gate_total >= 30
    skip_cohort_pass = sum(1 for r in rows if r['label'] != 'ALL' and r['ds_skip'] >= 0)
    half_cohort_pass = sum(1 for r in rows if r['label'] != 'ALL' and r['ds_half'] >= 0)
    n_individual = sum(1 for r in rows if r['label'] != 'ALL')

    print(f"  n(AND-gate trades) ≥ 30 : {'✓' if gate_n_ok else '✗'}  ({n_gate_total})")
    print(f"  skip-gate aggregate ΔSharpe ≥ +0.10 + CI>0 : "
          f"{'✓' if (all_row['ds_skip'] >= 0.10 and all_row['ci_lo_skip'] > 0) else '✗'}  "
          f"({all_row['ds_skip']:+.3f}, lo={all_row['ci_lo_skip']:+.3f})")
    print(f"  half-size aggregate ΔSharpe ≥ +0.10 + CI>0 : "
          f"{'✓' if (all_row['ds_half'] >= 0.10 and all_row['ci_lo_half'] > 0) else '✗'}  "
          f"({all_row['ds_half']:+.3f}, lo={all_row['ci_lo_half']:+.3f})")
    print(f"  skip-gate cohort pass ≥ 2/3 : "
          f"{'✓' if skip_cohort_pass >= 2 else '✗'}  ({skip_cohort_pass}/{n_individual})")
    print(f"  half-size cohort pass ≥ 2/3 : "
          f"{'✓' if half_cohort_pass >= 2 else '✗'}  ({half_cohort_pass}/{n_individual})")

    skip_ok = (gate_n_ok and all_row['ds_skip'] >= 0.10 and all_row['ci_lo_skip'] > 0
               and skip_cohort_pass >= 2)
    half_ok = (gate_n_ok and all_row['ds_half'] >= 0.10 and all_row['ci_lo_half'] > 0
               and half_cohort_pass >= 2)
    print()
    if skip_ok and half_ok:
        print("  → BOTH PASS: ship skip-gate (cleaner per-trade interpretation).")
    elif skip_ok:
        print("  → SHIP skip-gate: when AND-gate fires, skip new entries.")
    elif half_ok:
        print("  → SHIP half-size: when AND-gate fires, take entries at half size.")
    else:
        print("  → REJECT: keep current (display-only). Gate doesn't pass on regime_sign cohort.")
    print("="*100 + "\n")


if __name__ == "__main__":
    main()
