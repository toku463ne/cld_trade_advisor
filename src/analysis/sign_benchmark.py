"""Sign benchmark — evaluate all sign types over a historical period.

For each sign type × stock set × date range:
  1. Detect all fire events using the sign detector.
  2. Per event: find the first confirmed zigzag peak (HIGH or LOW) within
     ``trend_cap_days`` daily bars after the fire bar.
  3. Store individual events (trend_direction, trend_bars, trend_magnitude)
     and aggregate metrics (direction_rate, mag_follow, mag_reverse,
     benchmark_flw, benchmark_rev) to DB.

benchmark_flw = direction_rate × mag_follow
benchmark_rev = (1 − direction_rate) × mag_reverse

Signs with extra data requirements:
  div_peer   — loads all cluster members' 1h caches for peer comparison
  corr_shift — loads ^GSPC 1h cache; computes daily rolling corr on-the-fly
  corr_peak  — looks up mean_corr_b from peak_corr_results (most recent 1d run)

CLI usage
---------
    uv run --env-file devenv python -m src.analysis.sign_benchmark \\
        --sign div_bar --cluster-set classified2023 \\
        --start 2024-05-01 --end 2025-03-31

    for sign in div_bar div_vol div_gap div_peer corr_flip corr_shift corr_peak \\
                str_hold str_lead brk_sma brk_bol; do
        uv run --env-file devenv python -m src.analysis.sign_benchmark \\
            --sign $sign --cluster-set classified2023 \\
            --start 2024-05-01 --end 2025-03-31
    done
"""

from __future__ import annotations

import argparse
import datetime
import sys
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.models import (
    PeakCorrResult,
    PeakCorrRun,
    SignBenchmarkEvent,
    SignBenchmarkRun,
    StockClusterMember,
    StockClusterRun,
)
from src.data.db import get_session
from src.indicators.zigzag import detect_peaks
from src.signs import (
    BrkBolDetector,
    BrkSmaDetector,
    CorrFlipDetector,
    CorrPeakDetector,
    CorrShiftDetector,
    DivBarDetector,
    DivGapDetector,
    DivPeerDetector,
    DivVolDetector,
    RevNDayDetector,
    RevPeakDetector,
    StrHoldDetector,
    StrLeadDetector,
)
from src.simulator.cache import DataCache

# ── Constants ─────────────────────────────────────────────────────────────────

_N225        = "^N225"
_GSPC        = "^GSPC"
_TREND_CAP   = 30
_ZZ_SIZE     = 5
_ZZ_MID_SIZE = 2


# ── Per-event result ──────────────────────────────────────────────────────────

@dataclass
class _EventResult:
    stock_code:      str
    fired_at:        datetime.datetime
    sign_score:      float
    trend_direction: int | None
    trend_bars:      int | None
    trend_magnitude: float | None


# ── Extra-data bundle (sign-specific) ────────────────────────────────────────

@dataclass
class _ExtraData:
    peer_caches:      list[DataCache]  = field(default_factory=list)
    n225_down_corr_b: float | None     = None
    gspc_cache:       DataCache | None = None   # used to compute corr_shift series
    proximity_pct:    float            = 0.015  # rev_lo / rev_hi


# ── Daily corr from hourly caches ─────────────────────────────────────────────

def _daily_corr_series(
    stock_cache: DataCache,
    ind_cache: DataCache,
    window: int = 10,
) -> pd.Series:
    """Rolling window-day Pearson corr of daily returns (derived from hourly closes)."""
    stock_close = pd.Series(
        {b.dt.date(): b.close for b in stock_cache.bars}, dtype=float
    )
    ind_close = pd.Series(
        {b.dt.date(): b.close for b in ind_cache.bars}, dtype=float
    )
    aligned = pd.concat(
        [stock_close.rename("s"), ind_close.rename("i")], axis=1
    ).dropna()
    s_ret = aligned["s"].pct_change()
    i_ret = aligned["i"].pct_change()
    return s_ret.rolling(window, min_periods=max(5, window // 2)).corr(i_ret)


# ── Zigzag trend measurement ──────────────────────────────────────────────────

def _first_zigzag_peak(
    fired_at: datetime.datetime,
    bars_1d: list,
    cap: int,
    zz_size: int,
    zz_mid_size: int,
) -> tuple[int | None, int | None, float | None]:
    entry_idx = next((i for i, b in enumerate(bars_1d) if b.dt > fired_at), None)
    if entry_idx is None:
        return None, None, None

    entry_price = bars_1d[entry_idx].open
    if not entry_price:
        return None, None, None

    window = bars_1d[entry_idx : entry_idx + cap + zz_size]
    if len(window) < zz_size * 2 + 1:
        return None, None, None

    highs = [b.high for b in window]
    lows  = [b.low  for b in window]
    peaks = detect_peaks(highs, lows, size=zz_size, middle_size=zz_mid_size)

    for p in peaks:
        if abs(p.direction) != 2:
            continue
        if p.bar_index > cap:
            break
        trend_dir  = +1 if p.direction == 2 else -1
        trend_bars = p.bar_index + 1
        magnitude  = abs(p.price - entry_price) / entry_price
        return trend_dir, trend_bars, magnitude

    return None, None, None


# ── Per-stock evaluation ──────────────────────────────────────────────────────

def _eval_stock(
    stock_code: str,
    sign_type: str,
    cache_1h: DataCache,
    cache_1d: DataCache,
    n225_cache_1h: DataCache,
    window: int,
    valid_bars: int,
    trend_cap_days: int,
    zz_size: int,
    zz_mid_size: int,
    extra: _ExtraData,
) -> list[_EventResult]:
    if not cache_1h.bars:
        return []

    # Build detector
    if sign_type == "div_bar":
        detector = DivBarDetector(cache_1h, n225_cache_1h, window=window)
    elif sign_type == "div_vol":
        detector = DivVolDetector(cache_1h, n225_cache_1h, window=window)
    elif sign_type == "div_gap":
        detector = DivGapDetector(cache_1h, n225_cache_1h)
    elif sign_type == "div_peer":
        detector = DivPeerDetector(cache_1h, extra.peer_caches)
    elif sign_type == "corr_flip":
        detector = CorrFlipDetector(cache_1h, n225_cache_1h, window=window)
    elif sign_type == "corr_shift":
        if extra.gspc_cache is None:
            return []
        n225_corr = _daily_corr_series(cache_1h, n225_cache_1h, window=window)
        gspc_corr = _daily_corr_series(cache_1h, extra.gspc_cache, window=window)
        detector = CorrShiftDetector(cache_1h, n225_corr, gspc_corr)
    elif sign_type == "corr_peak":
        if extra.n225_down_corr_b is None:
            return []
        detector = CorrPeakDetector(cache_1h, n225_cache_1h, extra.n225_down_corr_b)
    elif sign_type == "str_hold":
        detector = StrHoldDetector(cache_1h, n225_cache_1h)
    elif sign_type == "str_lead":
        detector = StrLeadDetector(cache_1h, n225_cache_1h)
    elif sign_type == "brk_sma":
        detector = BrkSmaDetector(cache_1h, window=window)
    elif sign_type == "brk_bol":
        detector = BrkBolDetector(cache_1h, window=window)
    elif sign_type == "rev_lo":
        detector = RevPeakDetector(cache_1h, proximity_pct=extra.proximity_pct, side="lo")
    elif sign_type == "rev_hi":
        detector = RevPeakDetector(cache_1h, proximity_pct=extra.proximity_pct, side="hi")
    elif sign_type == "rev_nhi":
        detector = RevNDayDetector(cache_1h, n_days=window, side="hi")
    elif sign_type == "rev_nlo":
        detector = RevNDayDetector(cache_1h, n_days=window, side="lo")
    else:
        raise ValueError(f"Unknown sign_type: {sign_type!r}")

    bars_1d  = cache_1d.bars
    results: list[_EventResult] = []

    for bar in cache_1h.bars:
        sign = detector.detect(bar.dt, valid_bars=valid_bars)
        if sign is None or sign.fired_at != bar.dt:
            continue

        trend_dir, trend_bars, magnitude = _first_zigzag_peak(
            bar.dt, bars_1d, trend_cap_days, zz_size, zz_mid_size,
        )
        results.append(_EventResult(
            stock_code=stock_code,
            fired_at=bar.dt,
            sign_score=sign.score,
            trend_direction=trend_dir,
            trend_bars=trend_bars,
            trend_magnitude=magnitude,
        ))

    return results


# ── Aggregate metrics ─────────────────────────────────────────────────────────

def _aggregate(events: list[_EventResult]) -> dict:
    if not events:
        return {}

    with_trend = [e for e in events if e.trend_direction is not None]
    if not with_trend:
        return dict(
            direction_rate=None, mean_trend_bars=None,
            mag_follow=None, mag_reverse=None,
            benchmark_flw=None, benchmark_rev=None,
        )

    direction_rate  = float(sum(1 for e in with_trend if e.trend_direction == +1) / len(with_trend))
    mean_trend_bars = float(np.mean([e.trend_bars for e in with_trend if e.trend_bars is not None]))

    flw_mags = [e.trend_magnitude for e in with_trend
                if e.trend_direction == +1 and e.trend_magnitude is not None]
    rev_mags = [e.trend_magnitude for e in with_trend
                if e.trend_direction == -1 and e.trend_magnitude is not None]

    mag_follow  = float(np.mean(flw_mags)) if flw_mags else None
    mag_reverse = float(np.mean(rev_mags)) if rev_mags else None

    benchmark_flw = direction_rate * mag_follow         if mag_follow  is not None else None
    benchmark_rev = (1 - direction_rate) * mag_reverse  if mag_reverse is not None else None

    return dict(
        direction_rate=direction_rate,
        mean_trend_bars=mean_trend_bars,
        mag_follow=mag_follow,
        mag_reverse=mag_reverse,
        benchmark_flw=benchmark_flw,
        benchmark_rev=benchmark_rev,
    )


# ── Main run function ─────────────────────────────────────────────────────────

def run_benchmark(
    session: Session,
    sign_type: str,
    stock_codes: list[str],
    stock_set: str,
    start: datetime.datetime,
    end: datetime.datetime,
    window: int         = 20,
    valid_bars: int     = 5,
    trend_cap_days: int = _TREND_CAP,
    zz_size: int        = _ZZ_SIZE,
    zz_mid_size: int    = _ZZ_MID_SIZE,
    proximity_pct: float = 0.015,
) -> int:
    logger.info("Loading ^N225 1h cache …")
    n225_1h = DataCache(_N225, "1h"); n225_1h.load(session, start, end)

    # ── Sign-specific pre-loading ──────────────────────────────────────────────
    extra_template = _ExtraData(proximity_pct=proximity_pct)

    if sign_type == "corr_shift":
        logger.info("Loading ^GSPC 1h cache for corr_shift …")
        gspc = DataCache(_GSPC, "1h"); gspc.load(session, start, end)
        extra_template.gspc_cache = gspc

    elif sign_type == "corr_peak":
        peak_run = session.execute(
            select(PeakCorrRun)
            .where(PeakCorrRun.granularity == "1d")
            .order_by(PeakCorrRun.id.desc())
        ).scalar_one_or_none()
        if peak_run is None:
            raise SystemExit("No 1d peak_corr_run found — run peak-corr analysis first.")
        corr_b_rows = session.execute(
            select(PeakCorrResult)
            .where(PeakCorrResult.run_id == peak_run.id,
                   PeakCorrResult.indicator == _N225)
        ).scalars().all()
        peak_corr_b_map: dict[str, float] = {r.stock: (r.mean_corr_b or 0.0) for r in corr_b_rows}
        logger.info("Loaded peak_corr_b for {} stocks (run_id={})", len(peak_corr_b_map), peak_run.id)

    elif sign_type == "div_peer":
        cluster_run = session.execute(
            select(StockClusterRun).where(StockClusterRun.fiscal_year == stock_set)
        ).scalar_one_or_none()
        if cluster_run is None:
            raise SystemExit(f"No StockClusterRun for {stock_set!r}")
        all_members = session.execute(
            select(StockClusterMember)
            .where(StockClusterMember.run_id == cluster_run.id)
        ).scalars().all()
        cluster_groups: dict[int, list[str]] = defaultdict(list)
        stock_to_cluster: dict[str, int] = {}
        for m in all_members:
            cluster_groups[m.cluster_id].append(m.stock_code)
            stock_to_cluster[m.stock_code] = m.cluster_id

        # Pre-load 1h caches for all cluster members (incl. non-representatives)
        all_member_codes = sorted({m.stock_code for m in all_members})
        logger.info("Pre-loading 1h caches for {} cluster members …", len(all_member_codes))
        member_caches: dict[str, DataCache] = {}
        for code in all_member_codes:
            c = DataCache(code, "1h"); c.load(session, start, end)
            member_caches[code] = c

    # ── Per-stock loop ─────────────────────────────────────────────────────────
    all_events: list[_EventResult] = []

    for i, code in enumerate(stock_codes, 1):
        logger.debug("  [{}/{}] {}", i, len(stock_codes), code)
        cache_1h = DataCache(code, "1h"); cache_1h.load(session, start, end)
        cache_1d = DataCache(code, "1d"); cache_1d.load(session, start, end)

        if not cache_1h.bars:
            logger.warning("  No 1h data for {} — skipped", code)
            continue

        # Build per-stock extra data
        extra = _ExtraData(
            gspc_cache=extra_template.gspc_cache,
            proximity_pct=extra_template.proximity_pct,
        )

        if sign_type == "div_peer":
            cid = stock_to_cluster.get(code)
            if cid is not None:
                extra.peer_caches = [
                    member_caches[c]
                    for c in cluster_groups[cid]
                    if c != code and c in member_caches
                ]

        elif sign_type == "corr_peak":
            b = peak_corr_b_map.get(code)
            extra.n225_down_corr_b = b  # None if stock not in peak_corr_results

        events = _eval_stock(
            code, sign_type,
            cache_1h, cache_1d, n225_1h,
            window, valid_bars, trend_cap_days, zz_size, zz_mid_size,
            extra,
        )
        all_events.extend(events)
        if events:
            logger.debug("    {} events", len(events))

    logger.info("Total fire events: {}", len(all_events))
    agg = _aggregate(all_events)
    logger.info(
        "direction_rate={:.1%}  benchmark_flw={:.4f}  benchmark_rev={:.4f}  "
        "mean_trend_bars={:.1f}",
        agg.get("direction_rate") or 0,
        agg.get("benchmark_flw") or 0,
        agg.get("benchmark_rev") or 0,
        agg.get("mean_trend_bars") or 0,
    )

    run = SignBenchmarkRun(
        sign_type=sign_type, stock_set=stock_set, gran="1h",
        start_dt=start, end_dt=end,
        window=window, valid_bars=valid_bars,
        zz_size=zz_size, zz_mid_size=zz_mid_size, trend_cap_days=trend_cap_days,
        n_stocks=len(stock_codes), n_events=len(all_events),
        created_at=datetime.datetime.now(datetime.timezone.utc),
        **agg,
    )
    session.add(run)
    session.flush()

    session.bulk_insert_mappings(SignBenchmarkEvent, [  # type: ignore[arg-type]
        {
            "run_id":          run.id,
            "stock_code":      e.stock_code,
            "fired_at":        e.fired_at,
            "sign_score":      e.sign_score,
            "trend_direction": e.trend_direction,
            "trend_bars":      e.trend_bars,
            "trend_magnitude": e.trend_magnitude,
        }
        for e in all_events
    ])
    session.commit()
    return run.id


# ── CLI ───────────────────────────────────────────────────────────────────────

_SIGN_CHOICES = [
    "div_bar", "div_vol", "div_gap", "div_peer",
    "corr_flip", "corr_shift", "corr_peak",
    "str_hold", "str_lead",
    "brk_sma", "brk_bol",
    "rev_lo", "rev_hi",
    "rev_nhi", "rev_nlo",
]


def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(prog="python -m src.analysis.sign_benchmark")
    p.add_argument("--sign",        required=True, choices=_SIGN_CHOICES)
    p.add_argument("--cluster-set", required=True, metavar="LABEL")
    p.add_argument("--start",       required=True)
    p.add_argument("--end",         required=True)
    p.add_argument("--window",      type=int, default=20)
    p.add_argument("--valid-bars",  type=int, default=5)
    p.add_argument("--trend-cap",   type=int, default=_TREND_CAP)
    p.add_argument("--zz-size",     type=int,   default=_ZZ_SIZE)
    p.add_argument("--zz-mid-size", type=int,   default=_ZZ_MID_SIZE)
    p.add_argument("--proximity",   type=float, default=0.005,
                   help="Price proximity threshold for rev_lo/rev_hi (default 1.5%%)")
    args = p.parse_args(argv)

    with get_session() as session:
        cluster_run = session.execute(
            select(StockClusterRun)
            .where(StockClusterRun.fiscal_year == args.cluster_set)
        ).scalar_one_or_none()
        if cluster_run is None:
            raise SystemExit(f"No StockClusterRun for {args.cluster_set!r}")
        codes = list(session.execute(
            select(StockClusterMember.stock_code)
            .where(StockClusterMember.run_id == cluster_run.id,
                   StockClusterMember.is_representative.is_(True))
        ).scalars().all())
    logger.info("Loaded {} stocks from [{}]", len(codes), args.cluster_set)

    start = _parse_dt(args.start)
    end   = _parse_dt(args.end)

    with get_session() as session:
        run_id = run_benchmark(
            session=session,
            sign_type=args.sign,
            stock_codes=codes,
            stock_set=args.cluster_set,
            start=start,
            end=end,
            window=args.window,
            valid_bars=args.valid_bars,
            trend_cap_days=args.trend_cap,
            zz_size=args.zz_size,
            zz_mid_size=args.zz_mid_size,
            proximity_pct=args.proximity,
        )
    logger.info("Done — sign_benchmark_run.id={}", run_id)


if __name__ == "__main__":
    main()
