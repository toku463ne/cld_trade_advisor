"""Stage 0 — brk_previous_2peaks: break above the last two confirmed swing-high peaks.

Operator (2026-05-30, chart 4041): price tested ~4041 yen at two prior swing highs
(2/26 and 3/18) and broke both on 4/14 — reads as a Stage-1→Stage-2 transition.
Hypothesis: a clean break above a level that was TESTED-AND-REJECTED TWICE (a
double-top resistance) is a stronger bullish event than a single consolidation wall
(`brk_wall`), and distinct enough to earn its own confluence vote.

This probe is the cheap gate BEFORE any confluence build, per the brk_wall precedent
(brk_wall passes standalone but was confluence-DILUTIVE: +3.72→+2.32, Δ−1.40). Two
questions:

  (A) STANDALONE DR — does the pattern carry? Pooled FY2018–FY2024 + FY2025 OOS,
      vs the 50% coin-flip null. Reported across a double-top tolerance sweep.

  (B) ORTHOGONALITY (binding) — of the fires, what fraction land on a bar where NONE
      of the 5 existing confluence breakout signs (brk_sma, brk_bol, brk_kumo_hi,
      brk_tenkan_hi, chiko_hi) are already valid? If it mostly co-fires it is
      dead-on-arrival for confluence regardless of (A) — same trap as brk_wall.

Causality: a confirmed zigzag high at bar p (needs `size` lower bars on each side) is
only treated as VISIBLE at fire bar T if p + size <= T (its right-side confirmation
window has elapsed). This avoids the ~20pp DR over-estimate the first brk_wall probe
hit by using globally-confirmed peaks.

Forward outcome = production-consistent entry at T+1 open, held H=20 bars, exit close.

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.brk_prev2peaks_stage0
"""
from __future__ import annotations

import datetime
import math
from collections import defaultdict

import numpy as np
from loguru import logger

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_benchmark import _FYS
from src.data.db import get_session
from src.indicators.zigzag import detect_peaks
from src.simulator.cache import DataCache
from src.strategy.confluence_sign import _VALID_BARS, _build_detector

_H = 20                         # forward-exit horizon (bars after T+1 open)
_ZZ_SIZE = 5                    # zigzag confirmation half-window
_ZZ_MID = 2
_TOLS = [1.00, 0.05, 0.03, 0.015]   # double-top tolerance sweep (|p1-p2|/mean)
_ORTHO_SIGNS = ("brk_sma", "brk_bol", "brk_kumo_hi", "brk_tenkan_hi", "chiko_hi")


def _daily(cache):
    """Return (dts, open, high, low, close, first_bar_idx) deduped by trade date."""
    seen: set[datetime.date] = set()
    dts: list[datetime.date] = []
    o: list[float] = []
    hi: list[float] = []
    lo: list[float] = []
    cl: list[float] = []
    fbi: list[int] = []
    for i, b in enumerate(cache.bars):
        d = b.dt.date()
        if d in seen:
            if b.high > hi[-1]:
                hi[-1] = b.high
            if b.low < lo[-1]:
                lo[-1] = b.low
            cl[-1] = b.close
            continue
        seen.add(d); dts.append(d)
        o.append(b.open); hi.append(b.high); lo.append(b.low); cl.append(b.close)
        fbi.append(i)
    return (dts, np.array(o), np.array(hi), np.array(lo), np.array(cl), fbi)


def _fires_for_stock(dts, hi, lo, tol):
    """Yield (T, resistance) strict double-peak breakouts; causal confirmed peaks."""
    n = len(dts)
    if n < 3 * _ZZ_SIZE + 2:
        return
    peaks = detect_peaks(list(hi), list(lo), size=_ZZ_SIZE, middle_size=_ZZ_MID)
    # confirmed highs only, in chronological order
    highs = [(p.bar_index, p.price) for p in peaks if p.direction == 2]
    if len(highs) < 2:
        return
    hi_idx = [p[0] for p in highs]
    for T in range(1, n):
        # last two confirmed highs visible by T (right-window elapsed: idx + size <= T)
        avail = [(bi, pr) for bi, pr in highs if bi + _ZZ_SIZE <= T]
        if len(avail) < 2:
            continue
        (i1, p1), (i2, p2) = avail[-2], avail[-1]
        mean_p = (p1 + p2) / 2.0
        if mean_p <= 0 or abs(p1 - p2) / mean_p > tol:
            continue
        level = max(p1, p2)
        if lo[T] > level and lo[T - 1] <= level:
            yield T, level


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    # rows[tol] = list of (fy, fwd_ret, n_ortho_covalid)
    rows: dict[float, list[tuple[str, float, int]]] = {t: [] for t in _TOLS}

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=260)   # zigzag peak history + warmup
        se = cfg.end + datetime.timedelta(days=90)
        with get_session() as s:
            caches: dict[str, DataCache] = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(
                    s,
                    datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                    datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc),
                )
                if c.bars:
                    caches[code] = c
        logger.info("{}: {} caches", cfg.label, len(caches))

        for code, c in caches.items():
            dts, o, hi, lo, cl, fbi = _daily(c)
            n = len(dts)
            if n < 3 * _ZZ_SIZE + _H + 2:
                continue
            # orthogonality detectors (built once per stock; n225 not needed for these)
            ortho_dets = {}
            for sg in _ORTHO_SIGNS:
                try:
                    ortho_dets[sg] = _build_detector(sg, c, None, 20)
                except Exception as e:  # noqa: BLE001
                    logger.warning("ortho build {} {}: {}", sg, code, e)

            for tol in _TOLS:
                for T, _level in _fires_for_stock(dts, hi, lo, tol):
                    if dts[T] < cfg.start or dts[T] > cfg.end:
                        continue            # count fires inside the FY window only
                    if T + 1 >= n:
                        continue
                    entry = o[T + 1]
                    if entry <= 0:
                        continue
                    xi = min(T + _H, n - 1)
                    ret = cl[xi] / entry - 1.0
                    # orthogonality: how many existing breakout signs valid at fire bar T
                    as_of = c.bars[fbi[T]].dt
                    co = 0
                    for sg, det in ortho_dets.items():
                        if det is None:
                            continue
                        r = det.detect(as_of, valid_bars=_VALID_BARS.get(sg, 5))
                        if r is not None:
                            co += 1
                    rows[tol].append((cfg.label, float(ret), co))

    _report(rows)


def _stats(rets: list[float]) -> tuple[int, float, float]:
    if not rets:
        return 0, float("nan"), float("nan")
    a = np.array(rets)
    dr = float((a > 0).mean() * 100.0)
    return len(a), dr, float(a.mean() * 100.0)


def _binom_p(n: int, dr_pct: float) -> float:
    """One-sided normal-approx p that DR > 50%."""
    if n == 0:
        return float("nan")
    k = dr_pct / 100.0
    z = (k - 0.5) / math.sqrt(0.25 / n)
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _report(rows: dict[float, list[tuple[str, float, int]]]) -> None:
    print("\n=== brk_previous_2peaks — Stage 0 ===\n")
    print("(A) STANDALONE DR by double-top tolerance  (H=20, T+1 open entry)\n")
    print(f"{'tol':>6} {'cohort':>16} {'n':>6} {'DR%':>7} {'mean_r%':>9} {'p(DR>50)':>10}")
    for tol in _TOLS:
        data = rows[tol]
        pool = [r for fy, r, _ in data if fy != "FY2025"]
        oos = [r for fy, r, _ in data if fy == "FY2025"]
        for label, sub in (("FY18-24 pool", pool), ("FY2025 OOS", oos)):
            n, dr, mr = _stats(sub)
            p = _binom_p(n, dr) if n else float("nan")
            tlab = "none" if tol >= 1.0 else f"{tol:.3f}"
            print(f"{tlab:>6} {label:>16} {n:>6} {dr:>7.1f} {mr:>9.2f} {p:>10.4f}")
        print()

    print("(B) ORTHOGONALITY — co-firing with existing confluence breakout signs")
    print("    (brk_sma, brk_bol, brk_kumo_hi, brk_tenkan_hi, chiko_hi)\n")
    print(f"{'tol':>6} {'n':>6} {'fresh%':>8} {'mean_co':>8}  DR(fresh) vs DR(co-fired)")
    for tol in _TOLS:
        data = rows[tol]
        if not data:
            continue
        co_counts = np.array([c for _, _, c in data])
        rets = np.array([r for _, r, _ in data])
        fresh_mask = co_counts == 0
        fresh_pct = float(fresh_mask.mean() * 100.0)
        mean_co = float(co_counts.mean())
        dr_fresh = float((rets[fresh_mask] > 0).mean() * 100.0) if fresh_mask.any() else float("nan")
        dr_cofire = float((rets[~fresh_mask] > 0).mean() * 100.0) if (~fresh_mask).any() else float("nan")
        nf = int(fresh_mask.sum()); nc = int((~fresh_mask).sum())
        tlab = "none" if tol >= 1.0 else f"{tol:.3f}"
        print(f"{tlab:>6} {len(data):>6} {fresh_pct:>8.1f} {mean_co:>8.2f}  "
              f"fresh n={nf} DR={dr_fresh:.1f}  |  co n={nc} DR={dr_cofire:.1f}")
    print()


if __name__ == "__main__":
    run()
