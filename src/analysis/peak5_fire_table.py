"""peak5_fire_table — Phase 1 of /sign-debate peak5_shape_cluster cycle.

Builds the (P0..P4, P5_label) fire table from causally-derived peaks and
computes 3 pre-probe measurement gates:

  M1 (displacement)   : median |entry_open − P4.price| / ATR(14) ≤ 1.0
  M2 (demotion rate)  : frac of P4-early peaks that never confirm as dir=±2 ≤ 30%
  M2b (demote-in-wait): frac of P4 demoted between early_bar and fire_bar ≤ 10%

If all three PASS → Phase 2 (peak5_shape_cluster_probe.py) is authorized.

Anchoring details (causal):
- P0..P3 are confirmed peaks (dir=±2) at positions q ≤ T - 6 (confirmed by bar T-1
  given size=5 confirmation lag), alternating in direction.
- P4 is an early-detected peak at position p = T - 3 (detected at bar T-1 given
  middle_size=2 lag), opposite direction to P3.
- Fire bar = T (two-bar fill from detection at T-1).
- 'Early peak at p' means bar p is the local extremum over bars[p-size : p+middle_size+1]
  (8 bars). NOTE: src/indicators/zigzag.py's main loop starts at i=n-2*size, which
  prevents it from emitting peaks at p > n - size - 1. So we use our own causal
  scan here — this is the cleanest path without touching production code.

P5 label: scanned forward in a 40-bar window from fire bar. Fires without a
confirmed P5 (size=5 confirmation) within 40 bars are DROPPED, never imputed.

CLI: uv run --env-file devenv python -m src.analysis.peak5_fire_table
"""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.data.db import get_session
from src.data.models import Ohlcv1d, Stock
from src.indicators.zigzag import detect_peaks

_OUT_DIR = Path(__file__).parent.parent.parent / "data" / "analysis" / "peak5_shape"
_ZZ_SIZE = 5
_ZZ_MID = 2
_ATR_WIN = 14
_FORWARD_BARS = 40
_FIRE_MIN_DATE = datetime.date(2019, 4, 1)
_FIRE_MAX_DATE = datetime.date(2026, 3, 31)


def _load_ohlcv(code: str, session) -> pd.DataFrame:
    rows = session.execute(
        select(Ohlcv1d.ts, Ohlcv1d.open_price, Ohlcv1d.high_price,
               Ohlcv1d.low_price, Ohlcv1d.close_price)
        .where(Ohlcv1d.stock_code == code)
        .order_by(Ohlcv1d.ts)
    ).all()
    if not rows:
        return pd.DataFrame()
    idx = pd.Index([r.ts.date() for r in rows], name="date")
    return pd.DataFrame({
        "open":  [float(r.open_price) for r in rows],
        "high":  [float(r.high_price) for r in rows],
        "low":   [float(r.low_price)  for r in rows],
        "close": [float(r.close_price) for r in rows],
    }, index=idx).sort_index()


def _atr(df: pd.DataFrame, win: int) -> pd.Series:
    h_l = df["high"] - df["low"]
    h_pc = (df["high"] - df["close"].shift()).abs()
    l_pc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    return tr.rolling(win, min_periods=win).mean()


def _fy_label(d: datetime.date) -> str:
    fy_start = d.year if d.month >= 4 else d.year - 1
    return f"FY{fy_start}"


def _check_early_peak(highs: list[float], lows: list[float], p: int,
                       size: int, middle_size: int) -> int:
    """Return +1 if bar p is a (causal) early HIGH, -1 if early LOW, 0 otherwise.

    Requires bars[p-size : p+middle_size+1] to exist; checks that bar p is the
    max (for HIGH) or min (for LOW) of that window.
    """
    if p < size or p + middle_size >= len(highs):
        return 0
    h_win = highs[p - size : p + middle_size + 1]
    if highs[p] == max(h_win):
        return 1
    l_win = lows[p - size : p + middle_size + 1]
    if lows[p] == min(l_win):
        return -1
    return 0


def _process_stock(code: str, df: pd.DataFrame, atr_series: pd.Series) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    stats = {"early_attempts": 0, "demoted_ever": 0, "demoted_in_wait": 0}

    highs = df["high"].tolist()
    lows  = df["low"].tolist()
    opens = df["open"].tolist()
    n_bars = len(df)

    # Compute confirmed peaks ONCE on full history. A confirmed peak at position
    # q is causally knowable from bar q + size onwards.
    all_peaks = detect_peaks(highs, lows, size=_ZZ_SIZE, middle_size=_ZZ_MID)
    confirmed = [p for p in all_peaks if abs(p.direction) == 2]
    confirmed_bars = [p.bar_index for p in confirmed]

    min_T = 200
    for T in range(min_T, n_bars - _FORWARD_BARS - _ZZ_SIZE):
        p4_bar = T - 3   # detected at bar T-1, fired at T
        edir = _check_early_peak(highs, lows, p4_bar, _ZZ_SIZE, _ZZ_MID)
        if edir == 0:
            continue
        stats["early_attempts"] += 1

        # Need 4 prior confirmed peaks at positions q ≤ T - 1 - _ZZ_SIZE = T - 6,
        # all before p4_bar.
        max_q = T - 1 - _ZZ_SIZE
        # Find indices in confirmed_bars with bar_index <= max_q AND < p4_bar
        # (use bisect over the sorted bars)
        # Walk from end to find the 4 most recent
        prior_4 = []
        for p in reversed(confirmed):
            if p.bar_index >= p4_bar:
                continue
            if p.bar_index > max_q:
                continue
            prior_4.append(p)
            if len(prior_4) == 4:
                break
        if len(prior_4) < 4:
            continue
        prior_4.reverse()
        P0, P1, P2, P3 = prior_4

        # Direction sanity: P4 should alternate with P3
        if P3.direction * edir > 0:
            continue
        # Check alternation among P0..P3
        if P0.direction * P1.direction >= 0 or P1.direction * P2.direction >= 0 \
           or P2.direction * P3.direction >= 0:
            continue

        fire_date = df.index[T]
        if fire_date < _FIRE_MIN_DATE or fire_date > _FIRE_MAX_DATE:
            continue

        # M2: P4 demotion — does this early peak ever appear as a confirmed (dir=±2)
        # peak in the full-history detect_peaks output?
        p4_confirmed = p4_bar in confirmed_bars
        if not p4_confirmed:
            stats["demoted_ever"] += 1

        # M2b: P4 demoted between early_bar (T-1) and fire_bar (T)?
        # At bar T-1, we detected p4_bar as early. By bar T, has anything changed?
        # Using our causal check at "now bar T" — would we still detect it?
        # Since we used the same window (p4_bar +/- size/middle_size), and the
        # window is fully past at bar T, the answer is yes — early detection is
        # stable once made. So M2b is effectively 0 with this definition. We use
        # an alternative interpretation: did a NEW peak emerge in the 1-bar wait
        # at position T - 2 = p4_bar + 1 that would supersede p4?
        new_peak_in_wait = _check_early_peak(highs, lows, T - 2, _ZZ_SIZE, _ZZ_MID)
        if new_peak_in_wait != 0:
            stats["demoted_in_wait"] += 1
            continue

        # P4 price
        p4_price = highs[p4_bar] if edir > 0 else lows[p4_bar]

        # P5 label: find next confirmed peak after p4_bar within forward window
        forward_end = min(T + _FORWARD_BARS, n_bars - 1)
        p5 = None
        for p in confirmed:
            if p.bar_index > p4_bar and p.bar_index <= forward_end:
                p5 = p
                break
        if p5 is None:
            continue

        # Y_T = "trend continuation"
        # If P4 is HIGH (edir>0): trend is up; continuation means P6 > P4. We have P5
        #   (likely a LOW). We use: continuation = 1 if P5 < P3 (lower low than previous LOW),
        #   meaning the down-leg is deep enough to suggest trend break (NOT continuation).
        # Actually let's use the simpler binary: did the next confirmed peak make
        # a new extremum past P4's level?
        # P4=HIGH expects P5=LOW (alternation). If P5.price < P3.price, the LOW is lower
        # than previous LOW — that's a BEARISH signal, NOT continuation of the up-trend.
        # If P5.price > P3.price, the LOW is higher → uptrend continues (higher low).
        if edir > 0:   # P4 HIGH
            y_t = 1 if p5.price > P3.price else 0
        else:          # P4 LOW
            y_t = 1 if p5.price < P3.price else 0

        # Displacement at fire bar
        atr_at_p4 = float(atr_series.iloc[p4_bar])
        if pd.isna(atr_at_p4) or atr_at_p4 <= 0:
            continue
        entry_open = opens[T]
        displacement_atr = abs(entry_open - p4_price) / atr_at_p4

        total_span = p4_bar - P0.bar_index

        rows.append({
            "stock": code,
            "fire_date": fire_date,
            "fire_bar": T,
            "side": "long" if edir > 0 else "short",
            "P0_price": P0.price, "P0_bar": P0.bar_index,
            "P1_price": P1.price, "P1_bar": P1.bar_index,
            "P2_price": P2.price, "P2_bar": P2.bar_index,
            "P3_price": P3.price, "P3_bar": P3.bar_index,
            "P4_price": p4_price, "P4_bar": p4_bar,
            "P5_price": p5.price, "P5_bar": p5.bar_index,
            "P5_dir": p5.direction,
            "y_t": int(y_t),
            "displacement_atr": float(displacement_atr),
            "total_span_bars": int(total_span),
            "p5_lag": int(p5.bar_index - p4_bar),
            "fy": _fy_label(fire_date),
        })

    return rows, stats


def main() -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()

    all_rows: list[dict] = []
    total_stats = {"early_attempts": 0, "demoted_ever": 0, "demoted_in_wait": 0}

    with get_session() as session:
        codes = session.execute(
            select(Stock.code).where(Stock.is_active.is_(True)).order_by(Stock.code)
        ).scalars().all()
        codes = [c for c in codes if not c.startswith("^") and "=" not in c]
        logger.info("Universe: {} active stocks", len(codes))

        n_eval = 0
        for i, code in enumerate(codes, 1):
            df = _load_ohlcv(code, session)
            if len(df) < 250:
                continue
            n_eval += 1
            atr_series = _atr(df, _ATR_WIN)
            rows, stats = _process_stock(code, df, atr_series)
            all_rows.extend(rows)
            for k in total_stats:
                total_stats[k] += stats[k]
            if i % 100 == 0:
                logger.info("  {}/{} stocks  fires_so_far={}",
                            i, len(codes), len(all_rows))

    if not all_rows:
        raise SystemExit("no fires collected — check causal-detection logic")

    fires_df = pd.DataFrame(all_rows)
    logger.info("Total fires: {}", len(fires_df))

    fires_path = _OUT_DIR / f"fire_table_{today}.csv"
    fires_df.to_csv(fires_path, index=False)
    logger.info("Wrote fire table to {}", fires_path)

    m1_median = float(fires_df["displacement_atr"].median())
    m1_pass = m1_median <= 1.0
    early_total = total_stats["early_attempts"]
    m2_rate = total_stats["demoted_ever"] / max(1, early_total)
    m2_pass = m2_rate <= 0.30
    m2b_rate = total_stats["demoted_in_wait"] / max(1, early_total)
    m2b_pass = m2b_rate <= 0.10
    all_pass = m1_pass and m2_pass and m2b_pass

    verdict = "PHASE-2 AUTHORIZED" if all_pass else "PHASE-2 BLOCKED"

    md = [
        "# peak5_fire_table — Phase 1 measurements",
        "",
        f"Generated: {today}  ",
        f"Universe: {n_eval} stocks  ·  Total fires after drops: {len(fires_df):,}  ",
        f"Early-detection attempts (pre-filter): {early_total:,}  ",
        "",
        f"## Verdict: **{verdict}**",
        "",
        "| Gate | Observed | Threshold | Pass? |",
        "|------|----------|-----------|-------|",
        f"| M1 displacement (median ATR-units) | {m1_median:.3f} | ≤ 1.0 | {'✓' if m1_pass else '✗'} |",
        f"| M2 P4 demotion rate (overall) | {m2_rate*100:.1f}% | ≤ 30% | {'✓' if m2_pass else '✗'} |",
        f"| M2b P4 demoted in 1-bar wait | {m2b_rate*100:.1f}% | ≤ 10% | {'✓' if m2b_pass else '✗'} |",
        "",
        f"## Fire distribution",
        "",
        f"- Long (P4=HIGH): {(fires_df['side']=='long').sum():,}",
        f"- Short (P4=LOW): {(fires_df['side']=='short').sum():,}",
        f"- Median P5 lag (bars): {fires_df['p5_lag'].median():.1f}",
        f"- Median total_span (bars): {fires_df['total_span_bars'].median():.1f}",
        f"- Unconditional Y_T=1 rate: {fires_df['y_t'].mean():.4f}",
        f"  - Long: {fires_df[fires_df['side']=='long']['y_t'].mean():.4f}",
        f"  - Short: {fires_df[fires_df['side']=='short']['y_t'].mean():.4f}",
        "- Displacement percentiles: "
        f"p25={fires_df['displacement_atr'].quantile(0.25):.3f}, "
        f"p50={fires_df['displacement_atr'].quantile(0.50):.3f}, "
        f"p75={fires_df['displacement_atr'].quantile(0.75):.3f}, "
        f"p90={fires_df['displacement_atr'].quantile(0.90):.3f}",
        "",
        "## Per-FY",
        "",
        "| FY | n | Y_T=1 rate |",
        "|----|---|------------|",
    ]
    for fy, sub in fires_df.groupby("fy"):
        md.append(f"| {fy} | {len(sub)} | {sub['y_t'].mean():.4f} |")
    md.append("")

    out = _OUT_DIR / f"phase1_{today}.md"
    out.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote report to {}", out)
    print("\n".join(md))


if __name__ == "__main__":
    main()
