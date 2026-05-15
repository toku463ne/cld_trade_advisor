"""peak_shape_measurement — round-1 measurement-only step for /sign-debate.

Judge's Insufficient verdict on `peak_shape_HSF_cont` demands 3 baseline measurements
BEFORE the bucket-encoding probe can be designed:

  (1) Median bars from P2 confirmation to the NEXT SAME-DIRECTION pivot.
      Target window: ≤ 25 bars (matches adx_trail_d8's 26.7-bar mean hold).
  (2) Unconditional P(continuation) — P(next same-dir peak's price exceeds P2's level),
      reported separately for bull (P2=HIGH) and bear (P2=LOW). Target window:
      0.40 ≤ P ≤ 0.55 (genuine information room — neither trivial nor adversarial).
  (3) Median (entry_open − P2.price)/ATR14 at fire bar = P2.bar_index + 6.
      Target: median displacement < 1.0×ATR (rev_peak rejected the analogous metric
      at ~6 bars post-trough).

Per ZigZag alternation: confirmed peaks (dir=±2) alternate H/L. "Next same-dir
pivot" is therefore 2 pivots ahead in the confirmed list.

Run over FY2019-FY2024 confirmed ZigZag(size=5) peaks on the 223-stock universe.

CLI: uv run --env-file devenv python -m src.analysis.peak_shape_measurement
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

_OUT_DIR = Path(__file__).parent.parent.parent / "data" / "analysis" / "peak_shape"
_ZZ_SIZE = 5
_ZZ_MID  = 2
_ATR_WIN = 14
_FIRE_OFFSET = _ZZ_SIZE + 1   # P2 confirmed at P2.bar_index + ZZ_SIZE, entry at +1 more
_TRAIN_START = datetime.date(2019, 4, 1)
_TRAIN_END   = datetime.date(2025, 3, 31)   # inclusive — FY2019-FY2024


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


def main() -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()

    bars_to_next_same: list[int] = []
    cont_bull: list[int] = []        # P2=HIGH, did next-same-dir HIGH exceed P2.price?
    cont_bear: list[int] = []        # P2=LOW,  did next-same-dir LOW  break below P2.price?
    displacement_atr: list[float] = []

    with get_session() as session:
        codes = session.execute(
            select(Stock.code).where(Stock.is_active.is_(True)).order_by(Stock.code)
        ).scalars().all()
        codes = [c for c in codes if not c.startswith("^") and "=" not in c]
        logger.info("Universe: {} active stocks", len(codes))

        n_eval = 0
        for i, code in enumerate(codes, 1):
            df = _load_ohlcv(code, session)
            if len(df) < _ATR_WIN + _ZZ_SIZE * 5:
                continue
            n_eval += 1
            atr = _atr(df, _ATR_WIN)
            peaks = detect_peaks(df["high"].tolist(), df["low"].tolist(),
                                 size=_ZZ_SIZE, middle_size=_ZZ_MID)
            confirmed = [p for p in peaks if abs(p.direction) == 2]
            if len(confirmed) < 3:
                continue

            # Walk confirmed peaks chronologically. For each P2 at position k,
            # the next same-direction peak is at position k+2 (alternation).
            for k in range(len(confirmed) - 2):
                p2 = confirmed[k]
                p_next_same = confirmed[k + 2]
                p2_date = df.index[p2.bar_index]
                if p2_date < _TRAIN_START or p2_date > _TRAIN_END:
                    continue
                # Need entry bar (p2.bar_index + ZZ_SIZE + 1) to exist
                entry_idx = p2.bar_index + _FIRE_OFFSET
                if entry_idx >= len(df):
                    continue

                bars = p_next_same.bar_index - p2.bar_index
                bars_to_next_same.append(int(bars))

                if p2.direction == 2:        # HIGH
                    cont_bull.append(1 if p_next_same.price > p2.price else 0)
                else:                        # LOW (dir=-2)
                    cont_bear.append(1 if p_next_same.price < p2.price else 0)

                # Displacement at fire bar
                entry_open = float(df["open"].iloc[entry_idx])
                atr_at_p2 = float(atr.iloc[p2.bar_index]) if pd.notna(atr.iloc[p2.bar_index]) else float("nan")
                if not np.isnan(atr_at_p2) and atr_at_p2 > 0:
                    raw_disp = entry_open - p2.price
                    # For LOW pivots, "moving away" means price rising above the LOW;
                    # take signed magnitude consistent with "distance from pivot."
                    displacement_atr.append(abs(raw_disp) / atr_at_p2)

            if i % 50 == 0:
                logger.info("  processed {}/{} stocks  peaks_so_far={}",
                            i, len(codes), len(bars_to_next_same))

    # ── Aggregate ─────────────────────────────────────────────────────────────
    def _pct(arr, q):
        return float(np.percentile(arr, q)) if len(arr) else float("nan")

    if not bars_to_next_same:
        raise SystemExit("no P2 peaks collected — check date filter / universe")

    bars_arr = np.array(bars_to_next_same)
    cont_bull_p = (sum(cont_bull) / len(cont_bull)) if cont_bull else float("nan")
    cont_bear_p = (sum(cont_bear) / len(cont_bear)) if cont_bear else float("nan")
    disp_arr = np.array(displacement_atr)

    bars_pass    = float(np.median(bars_arr)) <= 25
    cont_pass    = (0.40 <= cont_bull_p <= 0.55) and (0.40 <= cont_bear_p <= 0.55)
    disp_pass    = float(np.median(disp_arr)) < 1.0 if len(disp_arr) else False

    all_pass = bars_pass and cont_pass and disp_pass
    verdict = "ROUND-2 ACCEPT-PATH OPEN" if all_pass else "ROUND-2 ACCEPT-PATH NARROWED"

    md: list[str] = [
        "# peak_shape — round-1 measurement step",
        "",
        f"Generated: {today}  ",
        f"Universe: {n_eval} stocks, FY2019-FY2024 confirmed ZigZag(size={_ZZ_SIZE}) peaks  ",
        f"Total (P2, next-same-dir) pairs: {len(bars_to_next_same):,}",
        "",
        f"## Verdict: **{verdict}**",
        "",
        "## (1) Bars from P2 → next same-direction pivot",
        f"Threshold: median ≤ 25 bars (within adx_trail_d8 hold window).",
        "",
        f"- n = {len(bars_arr):,}",
        f"- p25 = {_pct(bars_arr, 25):.1f}  median = **{_pct(bars_arr, 50):.1f}**  "
        f"p75 = {_pct(bars_arr, 75):.1f}  p90 = {_pct(bars_arr, 90):.1f}",
        f"- mean = {float(np.mean(bars_arr)):.1f}",
        f"- **{'PASS' if bars_pass else 'FAIL'}** — median {_pct(bars_arr, 50):.1f} "
        f"{'≤' if bars_pass else '>'} 25",
        "",
        "## (2) Unconditional P(continuation), bull / bear",
        f"Threshold: each ∈ [0.40, 0.55] (genuine information room).",
        "",
        f"- Bull (P2=HIGH, P3.price > P2.price): n={len(cont_bull):,}  "
        f"P = **{cont_bull_p:.4f}**",
        f"- Bear (P2=LOW,  P3.price < P2.price): n={len(cont_bear):,}  "
        f"P = **{cont_bear_p:.4f}**",
        f"- **{'PASS' if cont_pass else 'FAIL'}** — "
        f"{'both' if cont_pass else 'one or both'} {'in [0.40,0.55]' if cont_pass else 'outside [0.40,0.55]'}",
        "",
        "## (3) (entry_open − P2.price)/ATR14 at fire bar P2.bar_index + 6",
        f"Threshold: median < 1.0×ATR (rev_peak-style structural-lateness check).",
        "",
        f"- n = {len(disp_arr):,}",
        f"- p25 = {_pct(disp_arr, 25):.3f}  median = **{_pct(disp_arr, 50):.3f}**  "
        f"p75 = {_pct(disp_arr, 75):.3f}  p90 = {_pct(disp_arr, 90):.3f}",
        f"- mean = {float(np.mean(disp_arr)):.3f}",
        f"- **{'PASS' if disp_pass else 'FAIL'}** — median {_pct(disp_arr, 50):.3f} "
        f"{'<' if disp_pass else '≥'} 1.0",
        "",
        "## Notes",
        "- This is a measurement step, not a sign or a probe.",
        "- 'Next same-direction pivot' = 2 positions ahead in the confirmed-peaks "
        "list (alternation rule).",
        "- ATR window = 14 bars. Displacement is |entry_open − P2.price| / ATR14[P2.bar_index].",
        "- These measurements inform round-2 framing: whether the original HSF "
        "primary cell survives, whether the Critic's P1-anchor counter-proposal "
        "is structurally better, or whether the whole concept is dead.",
        "",
    ]

    out = _OUT_DIR / f"measurement_{today}.md"
    out.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote report to {}", out)
    print("\n".join(md))


if __name__ == "__main__":
    main()
