"""Half-SL-from-fire invalidation — Stage-0 (operator idea, 2026-06-21).

Idea: after a confluence fires, if price has already travelled "too far" from the
fire-day price BEFORE we get in — e.g. more than HALF the stop-loss distance against
us — the setup's real validity is gone; skip it. This is volatility-normalized
(scaled by each fire's own SL/zigzag band) and CUMULATIVE-from-fire, unlike the
single-bar adverse-move veto already rejected ([[project_confluence_adverse_move_stage0_reject]]).

It also operationalizes the fixed-N-bar validity-window complaint: replace "valid for
5 bars no matter what" with "valid until price moves >x·SL from the fire".

Construction (per stock, FY2018-FY2025):
  - windowed confluence as in the live strategy; a BURST = maximal run of consecutive
    >=3-valid-sign days. fire = first day of the burst; ref = close[fire].
  - SL_dist = ref - sl, where (tp, sl) = ZsTpSl(2.0/2.0/0.3).preview_levels(ref,
    zs_history[fire]). This is the SL that setup would have used.
  - for EVERY valid day d in the burst (offset 0 = fire day, offset>0 = late/lingering):
        sl_frac = (close[d] - ref) / SL_dist           (negative = adverse/down)
    enter at d (fill open[d+1]) with the real ZsTpSl exit; record forward return.
  - slot caps lifted (isolated single positions) so this is a per-entry EV view, not
    the 6-slot book — Stage-0 premise check. If it separates, the binding test is the
    paired fill-order null on the canonical book.

Pre-stated Stage-0 gates (decided BEFORE running):
  ESCALATE only if ALL hold:
    (1) BELOW-HALF (sl_frac <= -0.5) − ABOVE-FIRE (sl_frac >= 0) mean_r spread <= -0.5pp,
    (2) monotone: bucket mean_r non-decreasing from most-adverse SL to least,
    (3) BELOW-HALF win% < ABOVE-FIRE win%,
    (4) sign-consistent: spread negative in >=6/8 FYs.
  If the below-half cohort is NOT worse (or BETTER — slides bounce, as the single-bar
  dip did), REJECT: a >x·SL invalidation would skip winners. Descriptive only.

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_half_sl_invalidation_stage0
"""
from __future__ import annotations

import datetime
import random
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.confluence_benchmark import _FYS
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import EntryCandidate, run_simulation
from src.simulator.cache import DataCache
from src.strategy.confluence_sign import _BULLISH_SIGNS, _VALID_BARS

_N_GATE = 3

# sl_frac buckets, most-adverse (down) first
_SL_BUCKETS = [
    (-1e9, -1.0, "< -1.0 SL"),
    (-1.0, -0.5, "-1..-0.5 SL"),
    (-0.5,  0.0, "-0.5..0 SL"),
    (0.0,  1e9, ">=0 (>=fire)"),
]


def _bucket(v: float) -> str:
    for lo, hi, lab in _SL_BUCKETS:
        if lo < v <= hi:
            return lab
    return _SL_BUCKETS[0][2]


def _coh(name: str, rets: list[float]) -> str:
    if not rets:
        return f"  {name:>14}: n=0"
    a = np.asarray(rets)
    return (f"  {name:>14}: n={len(a):>6}  mean_r={a.mean()*100:+.2f}%  "
            f"win%={float((a > 0).mean()*100):.1f}%  med={np.median(a)*100:+.2f}%")


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    exsim._MAX_LOW_CORR = 10_000     # lift slot caps -> isolated single positions
    exsim._MAX_HIGH_CORR = 10_000
    rule = cbt._EXIT_RULE

    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH_SIGNS)))).all()
    fires_by_code: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for sg, st, fa in rows:
        fires_by_code[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    sl_buckets: defaultdict[str, list[float]] = defaultdict(list)
    by_offset: dict[str, list[float]] = {"offset0": [], "offset>=1": []}
    per_fy: dict[str, dict] = {}
    tot = 0
    below_half = 0

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 90)
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
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}

        cands: list[EntryCandidate] = []
        feat: dict[tuple[str, datetime.date], tuple[float, int]] = {}   # (sl_frac, offset)
        for code, c in caches.items():
            closes: dict[datetime.date, float] = {}
            cal: list[datetime.date] = []
            seen: set[datetime.date] = set()
            for b in c.bars:
                d = b.dt.date()
                if d in seen:
                    continue
                seen.add(d); closes[d] = b.close; cal.append(d)
            cal.sort()
            idx = {d: i for i, d in enumerate(cal)}
            zsm = zs_maps.get(code, {})

            fire_idx: dict[str, list[int]] = defaultdict(list)
            for sg, fd in fires_by_code.get(code, []):
                if fd in idx:
                    fire_idx[sg].append(idx[fd])

            # count of valid signs per calendar index
            def count_at(i: int) -> int:
                n = 0
                for sg in _BULLISH_SIGNS:
                    vb = _VALID_BARS.get(sg, 5)
                    if any(fj <= i <= fj + vb for fj in fire_idx.get(sg, [])):
                        n += 1
                return n

            counts = [count_at(i) for i in range(len(cal))]
            i = 0
            while i < len(cal):
                if counts[i] < _N_GATE:
                    i += 1
                    continue
                # burst = maximal consecutive >=N run starting at i
                j = i
                ref_d = cal[i]
                ref = closes[ref_d]
                _tp, sl = rule.preview_levels(ref, zsm.get(ref_d, ()))
                sl_dist = ref - sl
                while j < len(cal) and counts[j] >= _N_GATE:
                    d = cal[j]
                    if cfg.start <= d <= cfg.end and j + 1 < len(cal) and sl_dist > 0:
                        sl_frac = (closes[d] - ref) / sl_dist
                        cands.append(EntryCandidate(
                            stock_code=code, entry_date=d, entry_price=closes[d],
                            corr_mode="low", corr_n225=0.0, zs_history=zsm.get(d, ()),
                        ))
                        feat[(code, d)] = (sl_frac, j - i)
                    j += 1
                i = j

        results = run_simulation(cands, rule, caches, cfg.end)
        fy_below: list[float] = []
        fy_above: list[float] = []
        for p in results:
            if not p.entry_price:
                continue
            fv = feat.get((p.stock_code, p.entry_date))
            if fv is None:
                continue
            sl_frac, offset = fv
            ret = p.exit_price / p.entry_price - 1.0
            sl_buckets[_bucket(sl_frac)].append(ret)
            by_offset["offset0" if offset == 0 else "offset>=1"].append(ret)
            tot += 1
            if sl_frac <= -0.5:
                fy_below.append(ret); below_half += 1
            elif sl_frac >= 0.0:
                fy_above.append(ret)
        per_fy[cfg.label] = {
            "below_mean": float(np.mean(fy_below)) if fy_below else float("nan"),
            "above_mean": float(np.mean(fy_above)) if fy_above else float("nan"),
            "below_n": len(fy_below),
        }
        logger.info("  {} done ({} entries, below-half {})",
                    cfg.label, len(results), len(fy_below))

    _report(sl_buckets, by_offset, per_fy, tot, below_half)


def _report(sl_buckets, by_offset, per_fy, tot, below_half) -> None:
    print("\n=== Half-SL-from-fire invalidation — Stage-0 (per-entry, isolated positions) ===")
    print(f"params: N_GATE={_N_GATE}  exit=ZsTpSl(2/2/0.3)  FY2018-FY2025 (FY2025=OOS)\n")

    print("(A) forward return by SL-scaled excursion from fire ((close[d]-close[fire])/SL_dist):")
    for _, _, lab in _SL_BUCKETS:
        print(_coh(lab, sl_buckets.get(lab, [])))

    print("\n(B) by entry timing within the burst:")
    print(_coh("fire day", by_offset["offset0"]))
    print(_coh("late (>=1)", by_offset["offset>=1"]))

    below = sl_buckets.get("< -1.0 SL", []) + sl_buckets.get("-1..-0.5 SL", [])
    above = sl_buckets.get(">=0 (>=fire)", [])
    print("\n(C) binding contrast — BELOW-HALF(sl_frac<=-0.5) vs ABOVE-FIRE(sl_frac>=0):")
    print(_coh("BELOW-HALF", below))
    print(_coh("ABOVE-FIRE", above))
    spread = (np.mean(below) - np.mean(above)) * 100 if (below and above) else float("nan")
    bwin = float((np.asarray(below) > 0).mean() * 100) if below else float("nan")
    awin = float((np.asarray(above) > 0).mean() * 100) if above else float("nan")

    means = [np.mean(sl_buckets[lab]) if sl_buckets.get(lab) else np.nan
             for _, _, lab in _SL_BUCKETS]
    valid = [m for m in means if not np.isnan(m)]
    monotone = all(valid[i] <= valid[i + 1] + 1e-9 for i in range(len(valid) - 1))

    print("\n   per-FY BELOW−ABOVE mean_r spread:")
    neg = pos = 0
    for lab, d in per_fy.items():
        if np.isnan(d["below_mean"]) or np.isnan(d["above_mean"]):
            print(f"     {lab:<8} (insufficient below-half; n={d['below_n']})")
            continue
        sp = (d["below_mean"] - d["above_mean"]) * 100
        neg += sp < 0; pos += sp > 0
        print(f"     {lab:<8} {sp:+6.2f}pp  (below-half n={d['below_n']})")
    print(f"   sign consistency: {neg} FYs negative / {pos} positive")

    cov = below_half / tot * 100 if tot else float("nan")
    print(f"\n(D) coarseness: a 'skip if moved <= -0.5 SL from fire' rule thins {cov:.1f}% of entries")

    print("\n(E) VERDICT (gates pre-stated in docstring):")
    g1 = (not np.isnan(spread)) and spread <= -0.5
    g3 = (not np.isnan(bwin)) and bwin < awin
    g4 = neg >= 6
    print(f"  (1) BELOW−ABOVE spread {spread:+.2f}pp   {'PASS' if g1 else 'FAIL'} (<= -0.5pp)")
    print(f"  (2) monotone most-adverse→least          {'PASS' if monotone else 'FAIL'}")
    print(f"  (3) BELOW win% {bwin:.1f} < ABOVE {awin:.1f}   {'PASS' if g3 else 'FAIL'}")
    print(f"  (4) FY sign-consistency {neg}/8 negative   {'PASS' if g4 else 'FAIL'} (>=6)")
    print(f"  → {'ESCALATE (paired fill-order null)' if (g1 and monotone and g3 and g4) else 'REJECT — descriptive only, write memory'}")
    print()


if __name__ == "__main__":
    run()
