"""sign_features — per-fire feature/outcome table for sign characterization.

For every sign fire already recorded in `SignBenchmarkEvent` (FY2010-FY2025
after the 2008 backfill + extended benchmark), records the cross-sectional
context at the fire bar plus forward outcomes, and writes a parquet artifact
for the discover/validate/holdout/strategy analysis (see docs/analysis).

Per fire row:
  Identity / own signal
    stock_code, fired_on (date), fy, sign_type, sign_score

  Co-fire context (from the events table — signs whose VALID_BARS window
  covers this fire's bar, for the same stock):
    valid_<sign>      — that sign's score if valid on this bar, else NaN
    bullish_valid_n / bearish_valid_n / valid_n  — directional co-fire counts

  Own indicator distances (1d, look-ahead-safe, from _trend_score math):
    sma_dist, kumo_dist, chiko_dist, tenkan_dist, zz_momentum

  Daily correlations (rolling Pearson of daily returns):
    corr_n225, corr_gspc, corr_hsi

  N225 self-contained sign scores (the index's own signal that day):
    n225_<sign>       — score if that self-contained sign is valid on ^N225

  Outcomes (LABELS — forward-looking, never feed back as features):
    out_direction, out_bars, out_magnitude   — next confirmed zigzag peak
    fwd_ret_h         — fixed-horizon return: (close[T+1+H] - open[T+1]) / open[T+1]

Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.sign_features \\
        --out /tmp/sign_features.parquet
"""
from __future__ import annotations

import argparse
import datetime
import math
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.indicators.ichimoku import calc_ichimoku
from src.indicators.zigzag import detect_peaks
from src.signs import (
    BrkBolDetector,
    BrkFloorDetector,
    BrkKumoDetector,
    BrkSmaDetector,
    BrkTenkanDetector,
    BrkWallDetector,
    ChikoDetector,
    RevNDayDetector,
    RevPeakDetector,
)
from src.simulator.cache import DataCache

# ── Constants ───────────────────────────────────────────────────────────────
_N225, _GSPC, _HSI = "^N225", "^GSPC", "^HSI"
_INDICES = {_N225, _GSPC, _HSI}
_VALID_BARS = 5            # a fire is "valid" on its bar + 4 following bars
_FWD_H = 20               # fixed-horizon forward return (bars after entry)
_CORR_WINDOW = 20         # rolling daily-return correlation window
_LOAD_START = datetime.datetime(2008, 1, 1, tzinfo=datetime.timezone.utc)
_LOAD_END = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)

# Distance constants mirror _trend_score (DO NOT change without re-deriving).
_SMA_N, _PEAK_SIZE, _PEAK_MID = 50, 5, 2
_CHIKO_LAG, _KUMO_DISP = 26, 26

# NOTE: this collector deliberately stores NO bullish/bearish classification.
# Whether a sign is bullish or bearish (and in which situation) is a CONCLUSION
# to be derived from this table's measured outcomes — not an assumption baked in.
# Directional grouping lives in the analysis layer (sign_characteristics.py).


def _fy_label(d: datetime.date) -> str:
    """Japanese fiscal year: FY_N runs Apr-N .. Mar-(N+1)."""
    y = d.year if d.month >= 4 else d.year - 1
    return f"FY{y}"


# ── Indicator distances (per-stock, date-indexed) ────────────────────────────

def _distance_frame(cache: DataCache) -> pd.DataFrame:
    """sma/kumo/chiko/tenkan distance + signed zigzag-leg momentum per date."""
    bars = cache.bars
    n = len(bars)
    dates = [b.dt.date() for b in bars]
    closes = np.array([b.close for b in bars], dtype=float)
    highs = np.array([b.high for b in bars], dtype=float)
    lows = np.array([b.low for b in bars], dtype=float)

    sma = pd.Series(closes).rolling(_SMA_N).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        sma_dist = (closes - sma) / sma

    ichi = calc_ichimoku(highs.tolist(), lows.tolist(), closes.tolist())
    senkou_a = np.array(ichi["senkou_a"], dtype=float)
    senkou_b = np.array(ichi["senkou_b"], dtype=float)
    tenkan = np.array(ichi["tenkan"], dtype=float)
    kumo_mid = np.full(n, np.nan)
    for i in range(_KUMO_DISP, n):
        a, b = senkou_a[i - _KUMO_DISP], senkou_b[i - _KUMO_DISP]
        if not (math.isnan(a) or math.isnan(b)):
            kumo_mid[i] = (a + b) / 2
    with np.errstate(divide="ignore", invalid="ignore"):
        kumo_dist = (closes - kumo_mid) / kumo_mid
        tenkan_dist = (closes - tenkan) / tenkan

    chiko_dist = np.full(n, np.nan)
    for i in range(_CHIKO_LAG, n):
        ref = closes[i - _CHIKO_LAG]
        if ref > 0:
            chiko_dist[i] = (closes[i] - ref) / ref

    # Signed zigzag-leg momentum: last confirmed leg's signed pct change.
    peaks = detect_peaks(highs.tolist(), lows.tolist(),
                         size=_PEAK_SIZE, middle_size=_PEAK_MID)
    confirmed = sorted(
        [(p.bar_index + _PEAK_SIZE, p.price) for p in peaks if abs(p.direction) == 2],
        key=lambda t: t[0],
    )
    zz = np.full(n, np.nan)
    j, seen = 0, []
    for i in range(n):
        while j < len(confirmed) and confirmed[j][0] <= i:
            seen.append(confirmed[j])
            j += 1
        if len(seen) >= 2 and seen[-2][1] > 0:
            zz[i] = (seen[-1][1] - seen[-2][1]) / seen[-2][1]

    out = pd.DataFrame(
        {"sma_dist": sma_dist, "kumo_dist": kumo_dist, "chiko_dist": chiko_dist,
         "tenkan_dist": tenkan_dist, "zz_momentum": zz},
        index=pd.Index(dates, name="date"),
    )
    out[~np.isfinite(out)] = np.nan
    return out


def _corr_series(stock: DataCache, index: DataCache) -> pd.Series:
    sc = pd.Series({b.dt.date(): b.close for b in stock.bars}, dtype=float)
    ic = pd.Series({b.dt.date(): b.close for b in index.bars}, dtype=float)
    a = pd.concat([sc.rename("s"), ic.rename("i")], axis=1).dropna()
    return (a["s"].pct_change()
            .rolling(_CORR_WINDOW, min_periods=_CORR_WINDOW // 2)
            .corr(a["i"].pct_change()))


def _corr_at(s: pd.Series, d: datetime.date) -> float | None:
    if d in s.index:
        v = s.loc[d]
        return float(v) if pd.notna(v) else None
    return None


# ── N225 self-contained sign scores ──────────────────────────────────────────

def _n225_sign_frame(n225: DataCache) -> pd.DataFrame:
    """Validity-scored self-contained signs on ^N225 itself, date-indexed.

    Only signs that need the price series alone are meaningful on the index;
    relative signs (str_*, div_*, corr_*, rev_nlo/nhold) are excluded.
    """
    dets: dict[str, object] = {
        "brk_sma": BrkSmaDetector(n225, window=20),
        "brk_bol": BrkBolDetector(n225, window=20),
        "brk_wall": BrkWallDetector(n225),
        "brk_floor": BrkFloorDetector(n225),
        "brk_kumo_hi": BrkKumoDetector(n225, side="hi"),
        "brk_kumo_lo": BrkKumoDetector(n225, side="lo"),
        "brk_tenkan_hi": BrkTenkanDetector(n225, side="hi"),
        "brk_tenkan_lo": BrkTenkanDetector(n225, side="lo"),
        "chiko_hi": ChikoDetector(n225, side="hi"),
        "chiko_lo": ChikoDetector(n225, side="lo"),
        "rev_lo": RevPeakDetector(n225, side="lo"),
        "rev_hi": RevPeakDetector(n225, side="hi"),
        "rev_nhi": RevNDayDetector(n225, n_days=20, side="hi"),
    }
    dates = [b.dt.date() for b in n225.bars]
    cols: dict[str, list[float | None]] = {f"n225_{k}": [] for k in dets}
    for bar in n225.bars:
        for k, det in dets.items():
            r = det.detect(bar.dt, valid_bars=_VALID_BARS)
            cols[f"n225_{k}"].append(r.score if r is not None else None)
    return pd.DataFrame(cols, index=pd.Index(dates, name="date"))


# ── Main collection ───────────────────────────────────────────────────────────

def _load_events() -> pd.DataFrame:
    """All sign fires joined to their sign_type, one row per (stock, date, sign)."""
    with get_session() as s:
        rows = s.execute(
            select(
                SignBenchmarkEvent.stock_code,
                SignBenchmarkEvent.fired_at,
                SignBenchmarkRun.sign_type,
                SignBenchmarkEvent.sign_score,
                SignBenchmarkEvent.trend_direction,
                SignBenchmarkEvent.trend_bars,
                SignBenchmarkEvent.trend_magnitude,
            ).join(SignBenchmarkRun, SignBenchmarkEvent.run_id == SignBenchmarkRun.id)
        ).all()
    df = pd.DataFrame(rows, columns=[
        "stock_code", "fired_at", "sign_type", "sign_score",
        "out_direction", "out_bars", "out_magnitude",
    ])
    df["fired_on"] = df["fired_at"].map(lambda t: t.date())
    # A (stock, date, sign) may recur across overlapping runs — keep the first.
    df = df.drop_duplicates(["stock_code", "fired_on", "sign_type"])
    return df


def _na(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def _fl(v):
    x = _na(v)
    return float(x) if x is not None else None


def _it(v):
    x = _na(v)
    return int(x) if x is not None else None


def _write_db(records: list[dict], label: str) -> int:
    """Persist enrichment records to sign_feature_runs / sign_feature_records."""
    from src.analysis.models import SignFeatureRecord, SignFeatureRun

    mappings: list[dict] = []
    for r in records:
        cofire = {k[len("valid_"):]: _fl(v) for k, v in r.items()
                  if k.startswith("valid_") and k != "valid_n" and _na(v) is not None}
        n225 = {k[len("n225_"):]: _fl(v) for k, v in r.items()
                if k.startswith("n225_") and _na(v) is not None}
        mappings.append({
            "stock_code": r["stock_code"], "fired_on": r["fired_on"], "fy": r["fy"],
            "sign_type": r["sign_type"], "sign_score": _fl(r.get("sign_score")),
            "sma_dist": _fl(r.get("sma_dist")), "kumo_dist": _fl(r.get("kumo_dist")),
            "chiko_dist": _fl(r.get("chiko_dist")), "tenkan_dist": _fl(r.get("tenkan_dist")),
            "zz_momentum": _fl(r.get("zz_momentum")),
            "corr_n225": _fl(r.get("corr_n225")), "corr_gspc": _fl(r.get("corr_gspc")),
            "corr_hsi": _fl(r.get("corr_hsi")),
            "valid_n": _it(r.get("valid_n")),
            "n225_valid_n": sum(1 for k in r
                                if k.startswith("n225_") and _na(r.get(k)) is not None),
            "out_direction": _it(r.get("out_direction")), "out_bars": _it(r.get("out_bars")),
            "out_magnitude": _fl(r.get("out_magnitude")), "fwd_ret_h": _fl(r.get("fwd_ret_h")),
            "cofire_scores": cofire or None, "n225_scores": n225 or None,
        })

    with get_session() as s:
        run_row = SignFeatureRun(
            label=label, fwd_h=_FWD_H, valid_bars=_VALID_BARS, corr_window=_CORR_WINDOW,
            n_records=len(mappings),
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
        s.add(run_row)
        s.flush()
        rid = run_row.id
        s.bulk_insert_mappings(SignFeatureRecord, [{"run_id": rid, **m} for m in mappings])
        s.commit()
    logger.info("Wrote {} records to sign_feature_records (run_id={})", len(mappings), rid)
    return rid


def run(pkl_out: str | None, to_db: bool, label: str) -> int:
    logger.info("Loading events …")
    ev = _load_events()
    all_signs = sorted(ev["sign_type"].unique())
    logger.info("{} fires, {} sign types, {} stocks",
                len(ev), len(all_signs), ev["stock_code"].nunique())

    logger.info("Loading index caches …")
    with get_session() as s:
        n225 = DataCache(_N225, "1d"); n225.load(s, _LOAD_START, _LOAD_END)
        gspc = DataCache(_GSPC, "1d"); gspc.load(s, _LOAD_START, _LOAD_END)
        hsi = DataCache(_HSI, "1d"); hsi.load(s, _LOAD_START, _LOAD_END)
    n225_signs = _n225_sign_frame(n225)
    logger.info("N225 self-contained sign frame: {} dates", len(n225_signs))

    records: list[dict] = []
    codes = sorted(c for c in ev["stock_code"].unique() if c not in _INDICES)
    for ci, code in enumerate(codes, 1):
        sub = ev[ev["stock_code"] == code]
        if sub.empty:
            continue
        with get_session() as s:
            cache = DataCache(code, "1d"); cache.load(s, _LOAD_START, _LOAD_END)
        if len(cache.bars) < _SMA_N:
            continue

        date_to_idx = {b.dt.date(): i for i, b in enumerate(cache.bars)}
        opens = [b.open for b in cache.bars]
        closes = [b.close for b in cache.bars]
        dist = _distance_frame(cache)
        cn = _corr_series(cache, n225)
        cg = _corr_series(cache, gspc)
        ch = _corr_series(cache, hsi)

        # Per-stock fire bar indices by sign, for the co-fire validity join.
        fires_by_sign: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for _, r in sub.iterrows():
            idx = date_to_idx.get(r["fired_on"])
            if idx is not None:
                fires_by_sign[r["sign_type"]].append((idx, r["sign_score"]))
        for k in fires_by_sign:
            fires_by_sign[k].sort()

        def _valid_score(sign: str, bar_idx: int) -> float | None:
            """Most-recent score of `sign` if it fired within VALID_BARS of bar_idx."""
            best: float | None = None
            for fidx, score in fires_by_sign.get(sign, []):
                if fidx > bar_idx:
                    break
                if 0 <= bar_idx - fidx < _VALID_BARS:
                    best = score
            return best

        for _, r in sub.iterrows():
            d = r["fired_on"]
            idx = date_to_idx.get(d)
            if idx is None:
                continue

            rec: dict = {
                "stock_code": code, "fired_on": d, "fy": _fy_label(d),
                "sign_type": r["sign_type"], "sign_score": r["sign_score"],
                "out_direction": r["out_direction"], "out_bars": r["out_bars"],
                "out_magnitude": r["out_magnitude"],
            }

            # Co-fire context — raw per-sign scores + direction-agnostic count only
            tot = 0
            for sg in all_signs:
                sc = _valid_score(sg, idx)
                rec[f"valid_{sg}"] = sc
                if sc is not None:
                    tot += 1
            rec["valid_n"] = tot

            # Indicator distances
            if d in dist.index:
                for col in dist.columns:
                    v = dist.loc[d, col]
                    rec[col] = float(v) if pd.notna(v) else None
            # Correlations
            rec["corr_n225"] = _corr_at(cn, d)
            rec["corr_gspc"] = _corr_at(cg, d)
            rec["corr_hsi"] = _corr_at(ch, d)
            # N225 self-contained sign scores
            if d in n225_signs.index:
                for col in n225_signs.columns:
                    v = n225_signs.loc[d, col]
                    rec[col] = float(v) if pd.notna(v) else None

            # Fixed-H forward return (two-bar fill: entry next-bar open)
            ei, xi = idx + 1, idx + 1 + _FWD_H
            if xi < len(closes) and opens[ei]:
                rec["fwd_ret_h"] = (closes[xi] - opens[ei]) / opens[ei]
            else:
                rec["fwd_ret_h"] = None

            records.append(rec)

        if ci % 25 == 0:
            logger.info("  [{}/{}] {} — {} rows so far", ci, len(codes), code, len(records))

    if pkl_out:
        df = pd.DataFrame(records)
        df.to_pickle(pkl_out)
        logger.info("Wrote {} rows × {} cols → {}", len(df), df.shape[1], pkl_out)
    if to_db:
        _write_db(records, label)
    return len(records)


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    p = argparse.ArgumentParser(prog="python -m src.analysis.sign_features")
    p.add_argument("--out", default=None, help="Optional pickle output path")
    p.add_argument("--to-db", action="store_true", help="Persist to sign_feature_records")
    p.add_argument("--label", default="fy2010_2025_h20", help="SignFeatureRun label")
    args = p.parse_args(argv)
    if not args.out and not args.to_db:
        p.error("Provide --out and/or --to-db.")
    run(args.out, args.to_db, args.label)


if __name__ == "__main__":
    main()
