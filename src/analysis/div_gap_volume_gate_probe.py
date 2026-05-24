"""div_gap_volume_gate_probe — does a volume gate on the EXISTING div_gap sign lift
the ZsTpSl realized return? (read-only, price-only — NO earnings ground truth)

Context: the 2026-05-24 /sign-debate REJECTED a price-based PEAD sign as specified
(see memory project_pead_price_drift_reject.md). The judge's only live follow-on was
the *price-only half* of the critic's re-scoped probe: take the existing benchmarked
`div_gap` sign (gap-up while N225 gaps down) and ask whether adding a VOLUME-SPIKE
gate — vol[T] >= v * mean(vol[T-20:T-1]) — concentrates the per-trade edge under the
live band-based exit ZsTpSl(2/2/0.3).

This is the PEAD "earnings surprise = big move on big volume" intuition stripped of
the earnings claim: we cannot confirm any fire is post-earnings (no settlement table;
minkabu 2023+ only), so this answers "volume-gated div_gap", NOT "PEAD". Scoped to
FY2023+ where that earnings link *could* later be validated against minkabu.

Method (faithful composite walk, per evaluation_criteria.md §5.9):
  * div_gap fires pulled from sign_benchmark (FY2023+), deduped by (stock, fire_date).
  * Entry = open of fire+1 (two-bar fill). TP/SL from ZsTpSl preview using zigzag legs
    at fire bar; walk forward bar-by-bar to max_bars=40; first touch of TP/SL else
    time-exit at close. Realized return = (exit-entry)/entry.
  * vol_ratio = vol[fire] / mean(vol[fire-20 .. fire-1]).
  * Arms: ALL (ungated baseline) vs v>=2.0 vs v>=3.0, plus the v<2.0 complement.
  * Per-FY (FY2023/FY2024/FY2025) + pooled DR / mean_r.

Gate (judge falsifier to justify a Stage-1 volume-gated div_gap variant):
  gated pooled mean_r >= ungated +0.30pp at n>=1000 AND FY2025 (OOS) mean_r >= 0.
NO 60-bar arm (the band truncates; 60-bar fixed-horizon over-counts, §5.9).

CLI: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.div_gap_volume_gate_probe
"""

from __future__ import annotations

import bisect
import datetime
import statistics
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.data.models import Ohlcv1d
from src.exit.zs_tp_sl import ZsTpSl
from src.indicators.zigzag import detect_peaks
from src.portfolio.crud import _ZS_LOOKBACK, _ZZ_SIZE, _ZZ_MIDDLE

_SIGN          = "div_gap"
_N225_CODE     = "^N225"
_VOL_WIN       = 20            # trailing bars for the volume baseline
_LOOKBACK_DAYS = 400           # for zigzag leg history
_MAX_BARS      = 40            # ZsTpSl production time-stop
_TP, _SL, _AL  = 2.0, 2.0, 0.3
_FALLBACK_PCT  = 0.05          # ZsTpSl default

# FY2023+ only (earnings link could later be validated against minkabu 2023+)
_FYS = [
    ("FY2023", datetime.date(2023, 4, 1), datetime.date(2024, 3, 31)),
    ("FY2024", datetime.date(2024, 4, 1), datetime.date(2025, 3, 31)),
    ("FY2025", datetime.date(2025, 4, 1), datetime.date(2026, 3, 31)),
]
_FY_START = _FYS[0][1]
_FY_END   = _FYS[-1][2]


def _load_ohlcv(code: str, start: datetime.date, end: datetime.date):
    """Return parallel (dates, open, high, low, close, volume), one bar per session."""
    s0 = datetime.datetime.combine(start, datetime.time.min, tzinfo=datetime.timezone.utc)
    s1 = datetime.datetime.combine(end, datetime.time.max, tzinfo=datetime.timezone.utc)
    with get_session() as s:
        rows = s.execute(
            select(Ohlcv1d.ts, Ohlcv1d.open_price, Ohlcv1d.high_price,
                   Ohlcv1d.low_price, Ohlcv1d.close_price, Ohlcv1d.volume)
            .where(Ohlcv1d.stock_code == code)
            .where(Ohlcv1d.ts >= s0).where(Ohlcv1d.ts <= s1)
            .order_by(Ohlcv1d.ts)).all()
    d, o, h, lo, c, v, seen = [], [], [], [], [], [], set()
    for ts, op, hi, low, cl, vol in rows:
        dd = ts.date()
        if dd in seen:
            continue
        seen.add(dd)
        d.append(dd); o.append(float(op)); h.append(float(hi))
        lo.append(float(low)); c.append(float(cl)); v.append(float(vol))
    return d, o, h, lo, c, v


def _zs_legs_at(dates, highs, lows, n225_set, upto: int) -> tuple[float, ...]:
    pairs = [(dates[i], highs[i], lows[i]) for i in range(upto + 1) if dates[i] in n225_set]
    if len(pairs) < _ZZ_SIZE * 2 + 1:
        return ()
    hs = [p[1] for p in pairs]; ls = [p[2] for p in pairs]
    peaks = sorted(detect_peaks(hs, ls, size=_ZZ_SIZE, middle_size=_ZZ_MIDDLE),
                   key=lambda p: p.bar_index)
    legs, prev = [], None
    for p in peaks:
        if prev is not None:
            legs.append(abs(p.price - prev))
        prev = p.price
    return tuple(legs[-_ZS_LOOKBACK:])


def _walk(o, h, lo, c, entry_idx: int, tp: float, sl: float) -> float | None:
    """Faithful ZsTpSl walk from entry bar; first touch TP/SL else time-exit at close."""
    entry = o[entry_idx]
    if entry <= 0:
        return None
    last = len(c) - 1
    for off in range(0, _MAX_BARS + 1):
        pos = entry_idx + off
        if pos > last:
            return (c[last] - entry) / entry          # ran out of data: mark at last close
        if off >= _MAX_BARS:
            return (c[pos] - entry) / entry            # time exit
        if h[pos] >= tp:
            return (tp - entry) / entry                # TP first (matches existing probe)
        if lo[pos] <= sl:
            return (sl - entry) / entry
    return (c[last] - entry) / entry


def run() -> None:
    # 1. div_gap fires, deduped by (stock, date), restricted to FY2023+
    with get_session() as s:
        run_ids = s.execute(
            select(SignBenchmarkRun.id).where(SignBenchmarkRun.sign_type == _SIGN)
        ).scalars().all()
        evts = s.execute(
            select(SignBenchmarkEvent.stock_code, SignBenchmarkEvent.fired_at)
            .where(SignBenchmarkEvent.run_id.in_(run_ids))).all()
    fires_by_stock: dict[str, set[datetime.date]] = defaultdict(set)
    for code, fa in evts:
        d = fa.date() if hasattr(fa, "date") else fa
        if _FY_START <= d <= _FY_END:
            fires_by_stock[code].add(d)
    n_fires = sum(len(v) for v in fires_by_stock.values())
    logger.info("{} div_gap fires FY2023+ across {} stocks", n_fires, len(fires_by_stock))

    # 2. N225 trading-day set (for zigzag alignment)
    n_dates, *_ = _load_ohlcv(_N225_CODE,
                              _FY_START - datetime.timedelta(days=_LOOKBACK_DAYS + 30),
                              _FY_END + datetime.timedelta(days=90))
    n225_set = set(n_dates)

    # recs: list of dict(fy, vr, r)
    recs = []
    skipped = defaultdict(int)
    for ci, (code, dset) in enumerate(sorted(fires_by_stock.items())):
        d, o, h, lo, c, v = _load_ohlcv(
            code,
            min(dset) - datetime.timedelta(days=_LOOKBACK_DAYS + 30),
            max(dset) + datetime.timedelta(days=90))
        if len(d) < _VOL_WIN + 5:
            skipped["no_bars"] += len(dset)
            continue
        for fd in sorted(dset):
            fi = bisect.bisect_right(d, fd) - 1
            if fi < 0 or d[fi] != fd:
                skipped["no_fire_bar"] += 1
                continue
            if fi < _VOL_WIN or fi + 1 > len(d) - 1:
                skipped["edge"] += 1
                continue
            base = statistics.mean(v[fi - _VOL_WIN:fi])      # vol[fi-20 .. fi-1]
            if base <= 0:
                skipped["no_vol"] += 1
                continue
            vr = v[fi] / base
            legs = _zs_legs_at(d, h, lo, n225_set, fi)
            entry = o[fi + 1]
            tp, sl = ZsTpSl(tp_mult=_TP, sl_mult=_SL, alpha=_AL,
                            fallback_pct=_FALLBACK_PCT).preview_levels(entry, legs)
            r = _walk(o, h, lo, c, fi + 1, tp, sl)
            if r is None:
                skipped["bad_walk"] += 1
                continue
            fy = next((nm for nm, a, b in _FYS if a <= fd <= b), None)
            if fy is None:
                continue
            recs.append({"fy": fy, "vr": vr, "r": r})
        if (ci + 1) % 50 == 0:
            logger.info("  {}/{} stocks", ci + 1, len(fires_by_stock))
    if skipped:
        logger.info("skipped: {}", dict(skipped))

    # 3. report
    def stats(rows):
        rs = [x["r"] for x in rows]
        if not rs:
            return (0, float("nan"), float("nan"))
        dr = 100 * sum(1 for x in rs if x > 0) / len(rs)
        return (len(rs), dr, statistics.mean(rs) * 100)

    arms = [
        ("ALL (ungated)", lambda x: True),
        ("v>=2.0",        lambda x: x["vr"] >= 2.0),
        ("v>=3.0",        lambda x: x["vr"] >= 3.0),
        ("v<2.0 (compl.)", lambda x: x["vr"] < 2.0),
    ]
    print("\n" + "=" * 84)
    print(f"div_gap VOLUME-GATE composite-walk A/B — ZsTpSl({_TP}/{_SL}/{_AL}), "
          f"max_bars={_MAX_BARS}, FY2023+")
    print("  (price-only: NO earnings ground truth — answers 'volume-gated div_gap', not PEAD)")
    print("=" * 84)
    base_n, base_dr, base_mr = stats(recs)
    print(f"\n  {'arm':<16}{'n':>6}{'DR%':>8}{'mean_r%':>10}{'Δmean_r':>10}  | per-FY mean_r% (n)")
    print("  " + "-" * 78)
    for name, f in arms:
        sub = [x for x in recs if f(x)]
        n, dr, mr = stats(sub)
        dmr = mr - base_mr if n else float("nan")
        per = []
        for nm, a, b in _FYS:
            fn, _, fmr = stats([x for x in sub if x["fy"] == nm])
            per.append(f"{nm[2:]}:{fmr:+.2f}({fn})")
        print(f"  {name:<16}{n:>6}{dr:>7.1f}%{mr:>+9.2f}%{dmr:>+9.2f}%  | " + "  ".join(per))
    print("  " + "-" * 78)

    # 4. verdict vs judge falsifier
    print("\n  GATE (justify Stage-1 volume-gated div_gap variant):")
    print("    gated pooled mean_r >= ungated +0.30pp AT n>=1000  AND  FY2025 OOS mean_r >= 0")
    for name, f in arms[1:3]:
        sub = [x for x in recs if f(x)]
        n, dr, mr = stats(sub)
        d = mr - base_mr
        fy25 = [x["r"] for x in sub if x["fy"] == "FY2025"]
        fy25_mr = statistics.mean(fy25) * 100 if fy25 else float("nan")
        lift_ok = d >= 0.30 and n >= 1000
        oos_ok = fy25_mr >= 0
        verdict = "ADVANCE" if (lift_ok and oos_ok) else "FAIL"
        print(f"    {name:<8} Δ={d:+.2f}pp (n={n}, need>=1000)  "
              f"FY2025={fy25_mr:+.2f}%  -> {verdict}"
              f"{'' if lift_ok else '  [lift/ n short]'}"
              f"{'' if oos_ok else '  [OOS<0]'}")
    print()


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
