"""Two-arm peak-anchored exit probe — /sign-debate iter 2 evidence.

Pre-registered (locked before running):

- Arm A  PeakPriceExit(buffer_pct=0.005, lookback_peaks=2):
    Long TP = max(last 2 confirmed HIGH peak prices) * (1 - buffer)
    Long SL = min(last 2 confirmed LOW  peak prices) * (1 - buffer)
    Fallback to ZsTpSl(2.0, 2.0, 0.3) when geometry inverted (TP <= entry
    or SL >= entry) or fewer than 2 highs/lows known.

- Arm B  ZsTpSlClipped(2.0, 2.0, 0.3, peak_clip_buffer=0.005, lookback_peaks=2):
    EWA-projected TP clipped DOWN to max(recent HIGHs) * (1 - buffer)
    when projection overshoots structural resistance. EWA-projected SL
    clipped UP to min(recent LOWs) * (1 + buffer) when projection
    undershoots structural support. No fallback — when peaks unavailable
    the raw EWA bands are used (degrades gracefully to baseline).

Comparators:
- Baseline:    ZsTpSl(2.0, 2.0, 0.3)            (current production preview)
- Ceiling ref: TimeStop(max_bars=40)            (best simple rule, Sharpe 2.23
                                                  on FY2018–FY2024 aggregate)

Reports intersect-cohort metrics (trades present in all four result lists,
keyed by (stock, entry_date)), per-FY Sharpe per rule, and Arm-specific
diagnostics (Arm A fallback rate; Arm B clip rate per side).

Falsifier per /sign-debate judge ruling:
- Pre-registered single cell for each arm — no grid search.
- Aggregate ΔSharpe vs baseline reported; ≥ +0.05 needed to ACCEPT.
- Arm A fallback rate ≤ 40% needed (else result is mostly fallback in disguise).
- time_40b column shown alongside so reader sees absolute ceiling.

Run:
    uv run --env-file devenv python -m src.analysis.peak_price_exit_probe
"""

from __future__ import annotations

import copy
import datetime
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, NamedTuple

from loguru import logger

from src.analysis.exit_benchmark import (
    FY_CONFIGS,
    _add_adx,
    _load_cache,
    _load_rep_codes,
    _metrics,
)
from src.exit.base import EntryCandidate, ExitContext, ExitResult, ExitRule
from src.exit.entry_scanner import scan_entries
from src.exit.time_stop import TimeStop
from src.exit.zs_tp_sl import ZsTpSl
from src.indicators.zigzag import detect_peaks
from src.simulator.cache import DataCache

_N225 = "^N225"
_ZZ_SIZE = 5
_ZZ_MIDDLE = 2
_PEAK_LOOKBACK = 2          # last 2 confirmed peaks per side
_PEAK_BUFFER   = 0.005      # pre-registered (0.5%)


# ─────────────────────────────────────────────────────────────────────────────
# Peak-history extraction (per candidate, at entry_date)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PeakHist:
    recent_highs: tuple[float, ...]   # last K confirmed HIGH prices, chronological
    recent_lows:  tuple[float, ...]   # last K confirmed LOW  prices, chronological


def _peak_hist_for(
    cache: DataCache,
    entry_date: datetime.date,
    lookback: int = _PEAK_LOOKBACK,
) -> PeakHist:
    """Return last *lookback* confirmed HIGH and LOW peak prices known on entry_date."""
    bars_by_date: dict[datetime.date, list] = defaultdict(list)
    for b in cache.bars:
        bars_by_date[b.dt.date()].append(b)
    dates = sorted(d for d in bars_by_date if d <= entry_date)
    if not dates:
        return PeakHist((), ())
    highs = [max(b.high for b in bars_by_date[d]) for d in dates]
    lows  = [min(b.low  for b in bars_by_date[d]) for d in dates]
    peaks = detect_peaks(highs, lows, size=_ZZ_SIZE, middle_size=_ZZ_MIDDLE)
    conf_highs = [p.price for p in peaks if p.direction == 2]
    conf_lows  = [p.price for p in peaks if p.direction == -2]
    return PeakHist(
        recent_highs=tuple(conf_highs[-lookback:]),
        recent_lows =tuple(conf_lows [-lookback:]),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Rule definitions (inline; do not modify src/exit/ per protocol)
# ─────────────────────────────────────────────────────────────────────────────

class PeakPriceExit(ExitRule):
    """Arm A — anchors TP/SL on prior confirmed peak prices.

    Falls back to ZsTpSl(2.0, 2.0, 0.3) when geometry is inverted or peaks
    are unavailable. Per-trade diagnostic `_fallback_fired` is consumed by
    the probe to compute fallback rate.
    """

    def __init__(
        self,
        peaks: PeakHist,
        buffer_pct: float = _PEAK_BUFFER,
    ) -> None:
        self._peaks      = peaks
        self._buffer     = buffer_pct
        self._tp:  float = 0.0
        self._sl:  float = 0.0
        self._fb:  ZsTpSl = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
        self._initted = False
        self._fallback_fired = False     # diagnostic

    @property
    def name(self) -> str:
        return "peak_price_b0.005_l2"

    def reset(self) -> None:
        self._tp = 0.0
        self._sl = 0.0
        self._initted = False
        self._fallback_fired = False
        self._fb = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)

    def _init_levels(self, ctx: ExitContext) -> None:
        self._initted = True
        highs, lows = self._peaks.recent_highs, self._peaks.recent_lows
        entry = ctx.entry_price
        if len(highs) < 1 or len(lows) < 1:
            self._fallback_fired = True
            return
        tp = max(highs) * (1.0 - self._buffer)
        sl = min(lows)  * (1.0 - self._buffer)
        if not (tp > entry > sl):
            self._fallback_fired = True
            return
        self._tp, self._sl = tp, sl

    def should_exit(self, ctx: ExitContext) -> tuple[bool, str]:
        if not self._initted:
            self._init_levels(ctx)
        if self._fallback_fired:
            return self._fb.should_exit(ctx)
        if ctx.bar_index >= 40:
            return True, "time"
        if ctx.high >= self._tp:
            return True, "tp"
        if ctx.low <= self._sl:
            return True, "sl"
        return False, ""


def _ewa(legs: tuple[float, ...], alpha: float) -> float:
    ewa = legs[0]
    for leg in legs[1:]:
        ewa = alpha * leg + (1.0 - alpha) * ewa
    return ewa


class ZsTpSlClipped(ExitRule):
    """Arm B — ZsTpSl whose TP/SL are clipped by structural peak prices.

    Per-trade diagnostics: `_clipped_tp`, `_clipped_sl` flag whether the
    peak anchor was binding on either side.
    """

    def __init__(
        self,
        peaks: PeakHist,
        tp_mult: float = 2.0,
        sl_mult: float = 2.0,
        alpha:   float = 0.3,
        buffer:  float = _PEAK_BUFFER,
        min_legs: int = 3,
        fallback_pct: float = 0.05,
        max_bars: int = 40,
    ) -> None:
        self._peaks    = peaks
        self._tpm      = tp_mult
        self._slm      = sl_mult
        self._alpha    = alpha
        self._buf      = buffer
        self._minlegs  = min_legs
        self._fbpct    = fallback_pct
        self._maxbars  = max_bars
        self._tp: float = 0.0
        self._sl: float = 0.0
        self._initted  = False
        self._clipped_tp = False
        self._clipped_sl = False

    @property
    def name(self) -> str:
        return "zs_tp2.0_sl2.0_a0.3_clip0.005_l2"

    def reset(self) -> None:
        self._tp = 0.0
        self._sl = 0.0
        self._initted = False
        self._clipped_tp = False
        self._clipped_sl = False

    def _init_levels(self, ctx: ExitContext) -> None:
        self._initted = True
        legs = ctx.zs_history
        if len(legs) >= self._minlegs:
            band = _ewa(legs, self._alpha)
        else:
            band = ctx.entry_price * self._fbpct
        tp_raw = ctx.entry_price + self._tpm * band
        sl_raw = ctx.entry_price - self._slm * band

        highs, lows = self._peaks.recent_highs, self._peaks.recent_lows
        if highs:
            cap = max(highs) * (1.0 - self._buf)
            if cap < tp_raw and cap > ctx.entry_price:
                tp_raw = cap
                self._clipped_tp = True
        if lows:
            floor = min(lows) * (1.0 + self._buf)
            if floor > sl_raw and floor < ctx.entry_price:
                sl_raw = floor
                self._clipped_sl = True
        self._tp, self._sl = tp_raw, sl_raw

    def should_exit(self, ctx: ExitContext) -> tuple[bool, str]:
        if not self._initted:
            self._init_levels(ctx)
        if ctx.bar_index >= self._maxbars:
            return True, "time"
        if ctx.high >= self._tp:
            return True, "tp"
        if ctx.low <= self._sl:
            return True, "sl"
        return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# Custom simulator — accepts a rule factory so per-candidate state lives
# in the rule instance.  Mirrors run_simulation otherwise (portfolio
# constraints, two-bar fill, ADX advance).
# ─────────────────────────────────────────────────────────────────────────────

class _DayBar(NamedTuple):
    date:  datetime.date
    open:  float
    high:  float
    low:   float
    close: float
    adx:   float
    adx_p: float
    adx_n: float


def _daily_bars_with_adx(cache: DataCache) -> list[_DayBar]:
    groups: dict[datetime.date, list] = {}
    for b in cache.bars:
        groups.setdefault(b.dt.date(), []).append(b)
    out: list[_DayBar] = []
    for d in sorted(groups):
        day = groups[d]
        last = day[-1]
        out.append(_DayBar(
            date=d, open=day[0].open,
            high=max(b.high for b in day), low=min(b.low for b in day),
            close=last.close,
            adx=last.indicators.get("ADX14", float("nan")),
            adx_p=last.indicators.get("ADX14_POS", float("nan")),
            adx_n=last.indicators.get("ADX14_NEG", float("nan")),
        ))
    return out


def _isnan(x: float) -> bool:
    return x != x


@dataclass
class _OpenPos:
    cand:       EntryCandidate
    fill_price: float
    fill_date:  datetime.date
    bars:       list[_DayBar]
    peak_adx:   float
    rule:       ExitRule


def run_probe_sim(
    candidates: list[EntryCandidate],
    rule_factory: Callable[[EntryCandidate], ExitRule],
    stock_caches: dict[str, DataCache],
    end_date: datetime.date,
) -> tuple[list[ExitResult], list[ExitRule]]:
    """Like run_simulation but rule_factory(cand) builds a fresh rule per position.

    Returns (results, used_rules) — used_rules is parallel to results, so the
    caller can read per-trade diagnostics (e.g. PeakPriceExit._fallback_fired).
    """
    sorted_cands = sorted(candidates, key=lambda c: c.entry_date)
    bar_index = {code: _daily_bars_with_adx(c) for code, c in stock_caches.items()}
    date_to_idx = {code: {b.date: i for i, b in enumerate(bars)}
                   for code, bars in bar_index.items()}
    all_dates = sorted({b.date for bars in bar_index.values() for b in bars})

    results: list[ExitResult] = []
    used_rules: list[ExitRule] = []
    open_pos: list[_OpenPos] = []
    cand_idx = 0
    n_cands  = len(sorted_cands)

    for today in all_dates:
        if today > end_date:
            break

        # advance bars
        for pos in open_pos:
            bar_i = date_to_idx.get(pos.cand.stock_code, {}).get(today)
            if bar_i is None:
                continue
            bar = bar_index[pos.cand.stock_code][bar_i]
            pos.bars.append(bar)
            if not _isnan(bar.adx):
                pos.peak_adx = max(pos.peak_adx, bar.adx)

        # check exits
        remaining: list[_OpenPos] = []
        for pos in open_pos:
            if not pos.bars:
                remaining.append(pos); continue
            bar = pos.bars[-1]
            n   = len(pos.bars) - 1
            ctx = ExitContext(
                bar_index=n, entry_price=pos.fill_price,
                high=bar.high, low=bar.low, close=bar.close,
                adx=0.0 if _isnan(bar.adx) else bar.adx,
                adx_pos=0.0 if _isnan(bar.adx_p) else bar.adx_p,
                adx_neg=0.0 if _isnan(bar.adx_n) else bar.adx_n,
                peak_adx=pos.peak_adx, zs_history=pos.cand.zs_history,
            )
            exit_now, reason = pos.rule.should_exit(ctx)
            force = today >= end_date
            if exit_now or force:
                reason = reason if (exit_now and not force) else "end_of_data"
                results.append(ExitResult(
                    stock_code=pos.cand.stock_code, entry_date=pos.cand.entry_date,
                    exit_date=today, entry_price=pos.fill_price,
                    exit_price=bar.close, hold_bars=n,
                    exit_reason=reason, corr_mode=pos.cand.corr_mode,
                ))
                used_rules.append(pos.rule)
            else:
                remaining.append(pos)
        open_pos = remaining

        # accept new candidates (portfolio constraints same as run_simulation)
        while cand_idx < n_cands and sorted_cands[cand_idx].entry_date <= today:
            cand = sorted_cands[cand_idx]; cand_idx += 1
            if cand.entry_date != today:
                continue
            high_open = sum(1 for p in open_pos if p.cand.corr_mode == "high")
            low_open  = sum(1 for p in open_pos if p.cand.corr_mode != "high")
            if cand.corr_mode == "high" and high_open >= 1: continue
            if cand.corr_mode != "high" and low_open  >= 3: continue
            code = cand.stock_code
            idx_map = date_to_idx.get(code, {})
            bar_i = idx_map.get(today)
            if bar_i is None or bar_i + 1 >= len(bar_index.get(code, [])):
                continue
            fill_bar = bar_index[code][bar_i + 1]
            r = rule_factory(cand); r.reset()
            open_pos.append(_OpenPos(
                cand=cand, fill_price=fill_bar.open, fill_date=fill_bar.date,
                bars=[], peak_adx=0.0, rule=r,
            ))

    # force close
    for pos in open_pos:
        if not pos.bars: continue
        bar = pos.bars[-1]
        results.append(ExitResult(
            stock_code=pos.cand.stock_code, entry_date=pos.cand.entry_date,
            exit_date=bar.date, entry_price=pos.fill_price,
            exit_price=bar.close, hold_bars=len(pos.bars)-1,
            exit_reason="end_of_data", corr_mode=pos.cand.corr_mode,
        ))
        used_rules.append(pos.rule)
    return results, used_rules


# ─────────────────────────────────────────────────────────────────────────────
# Main probe runner
# ─────────────────────────────────────────────────────────────────────────────

def _run_one_fy(cfg) -> dict[str, tuple[list[ExitResult], list[ExitRule]]] | None:
    """Return {rule_label: (results, used_rules)} for one FY, or None if data missing."""
    logger.info("FY {} ({} – {})", cfg.label, cfg.start, cfg.end)
    rep_codes = _load_rep_codes(cfg.stock_set)
    n225 = _load_cache(_N225, cfg.start, cfg.end)
    if n225 is None:
        logger.warning("  ^N225 cache missing for {} — skipping", cfg.label)
        return None
    stock_caches: dict[str, DataCache] = {}
    for code in rep_codes:
        c = _load_cache(code, cfg.start, cfg.end)
        if c: stock_caches[code] = c
    logger.info("  {}/{} caches", len(stock_caches), len(rep_codes))

    cands: list[EntryCandidate] = []
    for code, cache in stock_caches.items():
        cands.extend(scan_entries(cache, n225, cfg.start, cfg.end))
    cands.sort(key=lambda c: c.entry_date)
    logger.info("  {} early-LOW candidates", len(cands))

    # Pre-compute peak history per candidate
    peaks_by_key: dict[tuple[str, datetime.date], PeakHist] = {}
    for c in cands:
        peaks_by_key[(c.stock_code, c.entry_date)] = _peak_hist_for(
            stock_caches[c.stock_code], c.entry_date, _PEAK_LOOKBACK,
        )

    def factory_arm_a(c: EntryCandidate) -> ExitRule:
        return PeakPriceExit(peaks=peaks_by_key[(c.stock_code, c.entry_date)])

    def factory_arm_b(c: EntryCandidate) -> ExitRule:
        return ZsTpSlClipped(peaks=peaks_by_key[(c.stock_code, c.entry_date)])

    baseline = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
    ceiling  = TimeStop(max_bars=40)

    res = {
        "baseline_zs":  run_probe_sim(cands, lambda c: copy.deepcopy(baseline), stock_caches, cfg.end),
        "ceiling_t40":  run_probe_sim(cands, lambda c: copy.deepcopy(ceiling),  stock_caches, cfg.end),
        "arm_a_peak":   run_probe_sim(cands, factory_arm_a,                     stock_caches, cfg.end),
        "arm_b_clip":   run_probe_sim(cands, factory_arm_b,                     stock_caches, cfg.end),
    }
    return res


def _intersect(per_rule: dict[str, list[ExitResult]]) -> dict[str, list[ExitResult]]:
    """Keep only trades whose (stock, entry_date) key appears in every rule."""
    keys = None
    for results in per_rule.values():
        ks = {(r.stock_code, r.entry_date) for r in results}
        keys = ks if keys is None else keys & ks
    keys = keys or set()
    return {
        name: [r for r in results if (r.stock_code, r.entry_date) in keys]
        for name, results in per_rule.items()
    }


def _row(label: str, results: list[ExitResult]) -> str:
    m = _metrics(results)
    if m.n == 0:
        return f"  {label:<24}  n=  0  |  —"
    return (
        f"  {label:<24}  n={m.n:>3}  |  "
        f"mean_r={m.fmt_mean_r():>7}  "
        f"sharpe={m.fmt_sharpe():>6}  "
        f"win={m.fmt_win():>6}  "
        f"hold={m.fmt_hold():>5}"
    )


def _available_fys() -> list:
    from src.data.db import get_session
    from src.analysis.models import StockClusterRun
    from sqlalchemy import select
    with get_session() as s:
        avail = set(s.execute(select(StockClusterRun.fiscal_year)).scalars().all())
    keep = [c for c in FY_CONFIGS if c.stock_set in avail]
    skipped = [c.label for c in FY_CONFIGS if c.stock_set not in avail]
    if skipped:
        logger.warning("Skipping FYs without stock_set: {}", skipped)
    return keep


def main() -> None:
    fy_to_results: dict[str, dict[str, tuple[list[ExitResult], list[ExitRule]]]] = {}
    for cfg in _available_fys():
        res = _run_one_fy(cfg)
        if res is not None:
            fy_to_results[cfg.label] = res
    if not fy_to_results:
        raise RuntimeError("No usable FYs — check DB.")
    logger.info("Probe ran on {} FYs: {}", len(fy_to_results), list(fy_to_results))

    # Aggregate per rule
    agg: dict[str, list[ExitResult]] = {k: [] for k in
        ("baseline_zs", "ceiling_t40", "arm_a_peak", "arm_b_clip")}
    agg_rules: dict[str, list[ExitRule]] = {k: [] for k in agg}
    for fy, by_rule in fy_to_results.items():
        for k, (res, rl) in by_rule.items():
            agg[k].extend(res)
            agg_rules[k].extend(rl)

    inter = _intersect(agg)

    print("\n" + "="*78)
    print("PEAK-ANCHORED EXIT PROBE — FY2018–FY2024 aggregate")
    print("="*78)

    print("\n— FULL (each rule on its own simulated cohort) —")
    for name, results in agg.items():
        print(_row(name, results))

    print("\n— INTERSECT COHORT (same trades across all 4 rules) —")
    for name, results in inter.items():
        print(_row(name, results))

    base_inter = _metrics(inter["baseline_zs"])
    a_inter    = _metrics(inter["arm_a_peak"])
    b_inter    = _metrics(inter["arm_b_clip"])
    ceil_inter = _metrics(inter["ceiling_t40"])
    print(f"\n  ΔSharpe Arm A vs baseline: {a_inter.sharpe - base_inter.sharpe:+.3f}")
    print(f"  ΔSharpe Arm B vs baseline: {b_inter.sharpe - base_inter.sharpe:+.3f}")
    print(f"  ΔSharpe ceiling vs baseline: {ceil_inter.sharpe - base_inter.sharpe:+.3f}  (reference)")

    # Per-FY Sharpe table
    print("\n— PER-FY SHARPE (intersect cohort within each FY) —")
    fy_inter: dict[str, dict[str, list[ExitResult]]] = {}
    for fy, by_rule in fy_to_results.items():
        per_rule = {k: r for k, (r, _) in by_rule.items()}
        fy_inter[fy] = _intersect(per_rule)
    hdr = f"  {'FY':<7}  {'n':>4}  {'base':>7}  {'arm_A':>7}  {'arm_B':>7}  {'ceil':>7}  {'ΔA':>6}  {'ΔB':>6}"
    print(hdr); print("  " + "-"*72)
    n_pass_a = 0; n_pass_b = 0; n_total = 0
    for fy, inter_d in fy_inter.items():
        mb = _metrics(inter_d["baseline_zs"])
        ma = _metrics(inter_d["arm_a_peak"])
        mB = _metrics(inter_d["arm_b_clip"])
        mc = _metrics(inter_d["ceiling_t40"])
        n  = mb.n
        if n == 0: continue
        da = ma.sharpe - mb.sharpe
        db = mB.sharpe - mb.sharpe
        if da >= 0: n_pass_a += 1
        if db >= 0: n_pass_b += 1
        n_total += 1
        print(f"  {fy:<7}  {n:>4}  {mb.sharpe:>7.2f}  {ma.sharpe:>7.2f}  "
              f"{mB.sharpe:>7.2f}  {mc.sharpe:>7.2f}  {da:>+6.2f}  {db:>+6.2f}")
    print(f"\n  Arm A: {n_pass_a}/{n_total} FY cells with ΔSharpe ≥ 0 (gate: 4/7)")
    print(f"  Arm B: {n_pass_b}/{n_total} FY cells with ΔSharpe ≥ 0 (gate: 4/7)")

    # Arm-specific diagnostics
    fb_a = sum(1 for r in agg_rules["arm_a_peak"]
               if isinstance(r, PeakPriceExit) and r._fallback_fired)
    n_a  = len(agg_rules["arm_a_peak"])
    clipped_tp = sum(1 for r in agg_rules["arm_b_clip"]
                     if isinstance(r, ZsTpSlClipped) and r._clipped_tp)
    clipped_sl = sum(1 for r in agg_rules["arm_b_clip"]
                     if isinstance(r, ZsTpSlClipped) and r._clipped_sl)
    n_b  = len(agg_rules["arm_b_clip"])
    print("\n— DIAGNOSTICS —")
    print(f"  Arm A fallback rate: {fb_a}/{n_a} = {100.0*fb_a/max(n_a,1):.1f}%  (gate: ≤ 40%)")
    print(f"  Arm B TP clip rate:  {clipped_tp}/{n_b} = {100.0*clipped_tp/max(n_b,1):.1f}%")
    print(f"  Arm B SL clip rate:  {clipped_sl}/{n_b} = {100.0*clipped_sl/max(n_b,1):.1f}%")

    # Verdict summary
    print("\n— PRE-REGISTERED GATES —")
    agg_da = a_inter.sharpe - base_inter.sharpe
    agg_db = b_inter.sharpe - base_inter.sharpe
    gate_agg_a = agg_da >= 0.05
    gate_agg_b = agg_db >= 0.05
    gate_fy_a = n_pass_a >= 4
    gate_fy_b = n_pass_b >= 4
    gate_fb   = (fb_a / max(n_a, 1)) <= 0.40
    print(f"  Arm A aggregate ΔSharpe ≥ +0.05:  {'PASS' if gate_agg_a else 'FAIL'} ({agg_da:+.3f})")
    print(f"  Arm A 4/7 FY ΔSharpe ≥ 0:         {'PASS' if gate_fy_a else 'FAIL'} ({n_pass_a}/{n_total})")
    print(f"  Arm A fallback ≤ 40%:             {'PASS' if gate_fb else 'FAIL'} ({100.0*fb_a/max(n_a,1):.1f}%)")
    print(f"  Arm B aggregate ΔSharpe ≥ +0.05:  {'PASS' if gate_agg_b else 'FAIL'} ({agg_db:+.3f})")
    print(f"  Arm B 4/7 FY ΔSharpe ≥ 0:         {'PASS' if gate_fy_b else 'FAIL'} ({n_pass_b}/{n_total})")
    print("="*78 + "\n")


if __name__ == "__main__":
    main()
