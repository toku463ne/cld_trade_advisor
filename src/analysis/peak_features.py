"""Peak Feature Collection — context scores at every confirmed hourly zigzag peak.

For each confirmed hourly zigzag HIGH/LOW peak, records:
  Technical context (daily-derived at confirmation bar):
    sma20_dist   — (price − SMA-20) / SMA-20
    rsi14        — 14-day RSI of daily closes
    bb_pct_b     — Bollinger %B (SMA-20 ± 2σ)
    vol_ratio    — confirmation-bar hourly volume / 20-bar avg
    trend_age    — hourly bars since last opposite confirmed peak

  Market regime (N225-derived daily):
    n225_sma20_dist — N225 distance from 20-day SMA
    n225_20d_ret    — N225 20-day return  →  is_crash if < −5 %

  Daily correlations (10-day rolling Pearson of daily returns):
    corr_n225, corr_gspc, corr_hsi

  All 15 sign detector scores (NULL = not active at confirmation bar)

  Outcome (next confirmed daily zigzag peak within trend_cap_days):
    outcome_direction  — +1=HIGH first, −1=LOW first
    outcome_bars       — daily bars until first confirmed peak
    outcome_magnitude  — |peak_price − entry_open| / entry_open

Crash detection: n225_20d_ret < −5 % → is_crash=True.  The IV analysis
uses this flag to run with and without crash periods.

CLI:
    uv run --env-file devenv python -m src.analysis.peak_features \\
        --cluster-set classified2023 --start 2024-05-01 --end 2025-03-31
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
    PeakFeatureRecord,
    PeakFeatureRun,
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
_HSI         = "^HSI"
_ZZ_SIZE     = 5
_ZZ_MID_SIZE = 2
_TREND_CAP   = 30
_CORR_WINDOW = 10
_CRASH_THRESH = -0.05   # N225 20-day return below this → is_crash

_ALL_SIGN_KEYS = [
    "div_bar", "div_vol", "div_gap", "div_peer",
    "corr_flip", "corr_shift", "corr_peak",
    "str_hold", "str_lead",
    "brk_sma", "brk_bol",
    "rev_lo", "rev_hi", "rev_nhi", "rev_nlo",
]


# ── Extra-data bundle ─────────────────────────────────────────────────────────

@dataclass
class _Extra:
    peer_caches:      list[DataCache]  = field(default_factory=list)
    n225_down_corr_b: float | None     = None
    gspc_cache:       DataCache | None = None
    hsi_cache:        DataCache | None = None


# ── Indicator helpers ─────────────────────────────────────────────────────────

def _daily_indicators(cache: DataCache) -> pd.DataFrame:
    """Compute SMA-20, RSI-14, Bollinger %B, and 20-day return from hourly bars."""
    closes: dict[datetime.date, float] = {}
    for b in cache.bars:
        closes[b.dt.date()] = b.close   # last bar of day wins

    dates = sorted(closes)
    s = pd.Series([closes[d] for d in dates], index=dates, dtype=float)

    sma20 = s.rolling(20).mean()
    std20 = s.rolling(20).std(ddof=1)

    gains  = s.diff().clip(lower=0)
    losses = (-s.diff()).clip(lower=0)
    avg_g  = gains.ewm(com=13, min_periods=14).mean()
    avg_l  = losses.ewm(com=13, min_periods=14).mean()
    rs     = avg_g / avg_l.replace(0, np.nan)
    rsi14  = 100.0 - 100.0 / (1.0 + rs)

    upper   = sma20 + 2 * std20
    lower_b = sma20 - 2 * std20
    band    = (upper - lower_b).replace(0, np.nan)
    bb_pct_b = (s - lower_b) / band

    sma20_dist = (s - sma20) / sma20.replace(0, np.nan)
    ret20      = s.pct_change(20)

    return pd.DataFrame({
        "sma20_dist": sma20_dist,
        "rsi14":      rsi14,
        "bb_pct_b":   bb_pct_b,
        "ret20":      ret20,
    }, index=dates)


def _daily_corr_series(
    stock_cache: DataCache,
    ind_cache: DataCache,
    window: int = _CORR_WINDOW,
) -> pd.Series:
    """Rolling Pearson corr of daily returns derived from hourly closes."""
    sc = pd.Series({b.dt.date(): b.close for b in stock_cache.bars}, dtype=float)
    ic = pd.Series({b.dt.date(): b.close for b in ind_cache.bars},   dtype=float)
    aligned = pd.concat([sc.rename("s"), ic.rename("i")], axis=1).dropna()
    return (
        aligned["s"].pct_change()
        .rolling(window, min_periods=max(5, window // 2))
        .corr(aligned["i"].pct_change())
    )


# ── Derived daily bars ────────────────────────────────────────────────────────

class _DailyBar:
    __slots__ = ("dt", "date", "open", "high", "low", "close")

    def __init__(self, dt: datetime.datetime, date: datetime.date,
                 open_: float, high: float, low: float, close: float) -> None:
        self.dt    = dt
        self.date  = date
        self.open  = open_
        self.high  = high
        self.low   = low
        self.close = close


def _derive_daily_bars(cache: DataCache) -> list[_DailyBar]:
    """Aggregate hourly bars into daily OHLC, using first bar's dt as day dt."""
    groups: dict[datetime.date, list] = {}
    for b in cache.bars:
        groups.setdefault(b.dt.date(), []).append(b)

    result: list[_DailyBar] = []
    for d in sorted(groups):
        day = groups[d]
        result.append(_DailyBar(
            dt=day[0].dt,   # first hourly bar → entry price reference
            date=d,
            open_=day[0].open,
            high=max(b.high for b in day),
            low=min(b.low  for b in day),
            close=day[-1].close,
        ))
    return result


# ── Outcome measurement ───────────────────────────────────────────────────────

def _outcome(
    confirmed_at: datetime.datetime,
    daily_bars: list[_DailyBar],
    cap: int,
    zz_size: int,
    zz_mid_size: int,
) -> tuple[int | None, int | None, float | None]:
    """First confirmed zigzag peak on daily bars after confirmed_at's date."""
    conf_date = confirmed_at.date()
    entry_idx = next((i for i, b in enumerate(daily_bars) if b.date > conf_date), None)
    if entry_idx is None:
        return None, None, None
    entry_price = daily_bars[entry_idx].open
    if not entry_price:
        return None, None, None

    window = daily_bars[entry_idx : entry_idx + cap + zz_size]
    if len(window) < zz_size * 2 + 1:
        return None, None, None

    highs  = [b.high for b in window]
    lows   = [b.low  for b in window]
    peaks  = detect_peaks(highs, lows, size=zz_size, middle_size=zz_mid_size)

    for p in peaks:
        if abs(p.direction) != 2:
            continue
        if p.bar_index > cap:
            break
        return (
            +1 if p.direction == 2 else -1,
            p.bar_index + 1,
            abs(p.price - entry_price) / entry_price,
        )
    return None, None, None


# ── Sign detector builder ─────────────────────────────────────────────────────

def _build_detectors(
    cache_1h: DataCache,
    n225_1h: DataCache,
    extra: _Extra,
) -> dict[str, object]:
    dets: dict[str, object] = {}
    dets["div_bar"]  = DivBarDetector(cache_1h, n225_1h)
    dets["div_vol"]  = DivVolDetector(cache_1h, n225_1h)
    dets["div_gap"]  = DivGapDetector(cache_1h, n225_1h)
    if extra.peer_caches:
        dets["div_peer"] = DivPeerDetector(cache_1h, extra.peer_caches)
    dets["corr_flip"] = CorrFlipDetector(cache_1h, n225_1h)
    if extra.gspc_cache is not None:
        n225_corr = _daily_corr_series(cache_1h, n225_1h)
        gspc_corr = _daily_corr_series(cache_1h, extra.gspc_cache)
        dets["corr_shift"] = CorrShiftDetector(cache_1h, n225_corr, gspc_corr)
    if extra.n225_down_corr_b is not None:
        dets["corr_peak"] = CorrPeakDetector(cache_1h, n225_1h, extra.n225_down_corr_b)
    dets["str_hold"] = StrHoldDetector(cache_1h, n225_1h)
    dets["str_lead"] = StrLeadDetector(cache_1h, n225_1h)
    dets["brk_sma"]  = BrkSmaDetector(cache_1h)
    dets["brk_bol"]  = BrkBolDetector(cache_1h)
    dets["rev_lo"]   = RevPeakDetector(cache_1h, side="lo")
    dets["rev_hi"]   = RevPeakDetector(cache_1h, side="hi")
    dets["rev_nhi"]  = RevNDayDetector(cache_1h, side="hi")
    dets["rev_nlo"]  = RevNDayDetector(cache_1h, side="lo")
    return dets


# ── Per-stock processing ──────────────────────────────────────────────────────

def _process_stock(
    stock_code: str,
    cache_1h: DataCache,
    n225_1h: DataCache,
    n225_daily_ind: pd.DataFrame,
    extra: _Extra,
    zz_size: int,
    zz_mid_size: int,
    trend_cap_days: int,
    valid_bars: int = 5,
) -> list[dict]:
    bars = cache_1h.bars
    if not bars:
        return []

    # Derive daily bars from hourly (1d DB data may not cover the period)
    daily_bars = _derive_daily_bars(cache_1h)

    # ── Daily indicators for this stock ──────────────────────────────────────
    stock_ind   = _daily_indicators(cache_1h)
    corr_n225   = _daily_corr_series(cache_1h, n225_1h)
    corr_gspc   = _daily_corr_series(cache_1h, extra.gspc_cache) if extra.gspc_cache else None
    corr_hsi    = _daily_corr_series(cache_1h, extra.hsi_cache)  if extra.hsi_cache  else None

    # ── Detect all confirmed hourly zigzag peaks ──────────────────────────────
    highs = [b.high for b in bars]
    lows  = [b.low  for b in bars]
    all_peaks = detect_peaks(highs, lows, size=zz_size, middle_size=zz_mid_size)
    major = [(p.bar_index, p.direction, p.price) for p in all_peaks if abs(p.direction) == 2]

    # conf_peaks: (conf_idx, peak_idx, direction, price)
    conf_peaks: list[tuple[int, int, int, float]] = []
    for peak_idx, direction, price in major:
        conf_idx = peak_idx + zz_size
        if conf_idx >= len(bars):
            continue
        conf_peaks.append((conf_idx, peak_idx, direction, price))

    if not conf_peaks:
        return []

    # ── Build sign detectors (once per stock) ────────────────────────────────
    detectors = _build_detectors(cache_1h, n225_1h, extra)

    # Hourly volume list for vol_ratio computation
    vols = [b.volume or 0.0 for b in bars]

    records: list[dict] = []
    for i, (conf_idx, peak_idx, peak_dir, peak_price) in enumerate(conf_peaks):
        conf_bar  = bars[conf_idx]
        conf_dt   = conf_bar.dt
        conf_date = conf_dt.date()

        # ── Technical features ────────────────────────────────────────────
        si = stock_ind.loc[conf_date] if conf_date in stock_ind.index else None

        def _fval(row, col: str) -> float | None:
            if row is None:
                return None
            v = row[col]
            return float(v) if pd.notna(v) else None

        sma20_dist = _fval(si, "sma20_dist")
        rsi14      = _fval(si, "rsi14")
        bb_pct_b   = _fval(si, "bb_pct_b")

        # Volume ratio (hourly: 20-bar window before confirmation bar)
        vol_win   = [v for v in vols[max(0, conf_idx - 20):conf_idx] if v > 0]
        cur_vol   = bars[conf_idx].volume or 0.0
        vol_ratio = (cur_vol / (sum(vol_win) / len(vol_win))
                     if vol_win and cur_vol > 0 else None)

        # Trend age: hourly bars since last opposite-direction confirmed peak
        trend_age: int | None = None
        for j in range(i - 1, -1, -1):
            prev_conf, _, prev_dir, _ = conf_peaks[j]
            if prev_dir != peak_dir:
                trend_age = conf_idx - prev_conf
                break

        # ── Market regime (N225) ──────────────────────────────────────────
        ni = n225_daily_ind.loc[conf_date] if conf_date in n225_daily_ind.index else None
        n225_sma20_dist = _fval(ni, "sma20_dist")
        n225_20d_ret    = _fval(ni, "ret20")
        is_crash = (n225_20d_ret < _CRASH_THRESH) if n225_20d_ret is not None else None

        # ── Daily correlations ────────────────────────────────────────────
        def _corr_at(series: pd.Series | None, date: datetime.date) -> float | None:
            if series is None or date not in series.index:
                return None
            v = series.loc[date]
            return float(v) if pd.notna(v) else None

        cn225 = _corr_at(corr_n225, conf_date)
        cgspc = _corr_at(corr_gspc, conf_date)
        chsi  = _corr_at(corr_hsi,  conf_date)

        # ── Sign scores ───────────────────────────────────────────────────
        sign_scores: dict[str, float | None] = {}
        for key in _ALL_SIGN_KEYS:
            det = detectors.get(key)
            if det is None:
                sign_scores[key] = None
            else:
                result = det.detect(conf_dt, valid_bars=valid_bars)
                sign_scores[key] = result.score if result is not None else None

        sign_active_count = sum(1 for v in sign_scores.values() if v is not None)

        # ── Outcome ───────────────────────────────────────────────────────
        out_dir, out_bars, out_mag = _outcome(
            conf_dt, daily_bars, trend_cap_days, zz_size, zz_mid_size,
        )

        records.append({
            "stock_code":     stock_code,
            "confirmed_at":   conf_dt,
            "peak_at":        bars[peak_idx].dt,
            "peak_direction": peak_dir,
            "peak_price":     peak_price,
            "sma20_dist":     sma20_dist,
            "rsi14":          rsi14,
            "bb_pct_b":       bb_pct_b,
            "vol_ratio":      vol_ratio,
            "trend_age_bars": trend_age,
            "n225_sma20_dist": n225_sma20_dist,
            "n225_20d_ret":   n225_20d_ret,
            "is_crash":       is_crash,
            "corr_n225":      cn225,
            "corr_gspc":      cgspc,
            "corr_hsi":       chsi,
            **{f"sign_{k}": v for k, v in sign_scores.items()},
            "sign_active_count": sign_active_count,
            "outcome_direction": out_dir,
            "outcome_bars":     out_bars,
            "outcome_magnitude": out_mag,
        })

    return records


# ── Main run ──────────────────────────────────────────────────────────────────

def run_peak_features(
    session: Session,
    stock_codes: list[str],
    stock_set: str,
    start: datetime.datetime,
    end: datetime.datetime,
    zz_size: int        = _ZZ_SIZE,
    zz_mid_size: int    = _ZZ_MID_SIZE,
    trend_cap_days: int = _TREND_CAP,
    valid_bars: int     = 5,
) -> int:
    logger.info("Loading index caches …")
    n225_1h = DataCache(_N225, "1h"); n225_1h.load(session, start, end)
    gspc_1h = DataCache(_GSPC, "1h"); gspc_1h.load(session, start, end)
    hsi_1h  = DataCache(_HSI,  "1h"); hsi_1h.load(session, start, end)

    if not gspc_1h.bars:
        gspc_1h = None
        logger.warning("No ^GSPC data — corr_gspc will be NULL")
    if not hsi_1h.bars:
        hsi_1h = None
        logger.warning("No ^HSI data — corr_hsi will be NULL")

    n225_daily_ind = _daily_indicators(n225_1h)

    # ── Sign-specific pre-loading ──────────────────────────────────────────
    extra_template = _Extra(gspc_cache=gspc_1h, hsi_cache=hsi_1h)

    # Peak-corr B metric
    peak_run = session.execute(
        select(PeakCorrRun)
        .where(PeakCorrRun.granularity == "1d")
        .order_by(PeakCorrRun.id.desc())
    ).scalar_one_or_none()
    peak_corr_b_map: dict[str, float] = {}
    if peak_run:
        rows = session.execute(
            select(PeakCorrResult)
            .where(PeakCorrResult.run_id == peak_run.id,
                   PeakCorrResult.indicator == _N225)
        ).scalars().all()
        peak_corr_b_map = {r.stock: (r.mean_corr_b or 0.0) for r in rows}
        logger.info("Loaded peak_corr_b for {} stocks", len(peak_corr_b_map))

    # Cluster membership for div_peer
    cluster_run = session.execute(
        select(StockClusterRun).where(StockClusterRun.fiscal_year == stock_set)
    ).scalar_one_or_none()
    cluster_groups: dict[int, list[str]] = defaultdict(list)
    stock_to_cluster: dict[str, int] = {}
    member_caches: dict[str, DataCache] = {}
    if cluster_run:
        all_members = session.execute(
            select(StockClusterMember)
            .where(StockClusterMember.run_id == cluster_run.id)
        ).scalars().all()
        for m in all_members:
            cluster_groups[m.cluster_id].append(m.stock_code)
            stock_to_cluster[m.stock_code] = m.cluster_id
        all_codes = sorted({m.stock_code for m in all_members})
        logger.info("Pre-loading {} cluster member caches …", len(all_codes))
        for code in all_codes:
            c = DataCache(code, "1h"); c.load(session, start, end)
            member_caches[code] = c

    # ── Per-stock loop ─────────────────────────────────────────────────────
    all_records: list[dict] = []

    for i, code in enumerate(stock_codes, 1):
        logger.debug("  [{}/{}] {}", i, len(stock_codes), code)
        cache_1h = DataCache(code, "1h"); cache_1h.load(session, start, end)
        if not cache_1h.bars:
            logger.warning("  No 1h data for {} — skipped", code)
            continue

        extra = _Extra(
            gspc_cache=extra_template.gspc_cache,
            hsi_cache=extra_template.hsi_cache,
            n225_down_corr_b=peak_corr_b_map.get(code),
        )
        cid = stock_to_cluster.get(code)
        if cid is not None:
            extra.peer_caches = [
                member_caches[c]
                for c in cluster_groups[cid]
                if c != code and c in member_caches
            ]

        recs = _process_stock(
            code, cache_1h, n225_1h, n225_daily_ind,
            extra, zz_size, zz_mid_size, trend_cap_days, valid_bars,
        )
        all_records.extend(recs)
        if recs:
            logger.debug("    {} peak records", len(recs))

    logger.info("Total peak records: {}", len(all_records))

    run = PeakFeatureRun(
        stock_set=stock_set,
        start_dt=start, end_dt=end,
        zz_size=zz_size, zz_mid_size=zz_mid_size,
        trend_cap_days=trend_cap_days,
        n_stocks=len(stock_codes),
        n_records=len(all_records),
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    session.add(run)
    session.flush()

    session.bulk_insert_mappings(PeakFeatureRecord, [  # type: ignore[arg-type]
        {"run_id": run.id, **rec} for rec in all_records
    ])
    session.commit()
    logger.info("Done — peak_feature_run.id={}", run.id)
    return run.id


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(prog="python -m src.analysis.peak_features")
    p.add_argument("--cluster-set", required=True, metavar="LABEL")
    p.add_argument("--start",       required=True)
    p.add_argument("--end",         required=True)
    p.add_argument("--valid-bars",  type=int, default=5)
    p.add_argument("--trend-cap",   type=int, default=_TREND_CAP)
    p.add_argument("--zz-size",     type=int, default=_ZZ_SIZE)
    p.add_argument("--zz-mid-size", type=int, default=_ZZ_MID_SIZE)
    args = p.parse_args(argv)

    with get_session() as session:
        cluster_run = session.execute(
            select(StockClusterRun).where(StockClusterRun.fiscal_year == args.cluster_set)
        ).scalar_one_or_none()
        if cluster_run is None:
            raise SystemExit(f"No StockClusterRun for {args.cluster_set!r}")
        codes = list(session.execute(
            select(StockClusterMember.stock_code)
            .where(StockClusterMember.run_id == cluster_run.id,
                   StockClusterMember.is_representative.is_(True))
        ).scalars().all())
    logger.info("Loaded {} stocks from [{}]", len(codes), args.cluster_set)

    with get_session() as session:
        run_peak_features(
            session=session,
            stock_codes=codes,
            stock_set=args.cluster_set,
            start=_parse_dt(args.start),
            end=_parse_dt(args.end),
            zz_size=args.zz_size,
            zz_mid_size=args.zz_mid_size,
            trend_cap_days=args.trend_cap,
            valid_bars=args.valid_bars,
        )


if __name__ == "__main__":
    main()
