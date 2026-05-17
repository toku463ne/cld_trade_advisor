"""long_high_continuation_strict_probe — clean N-bar high breakout (no retracement).

Sibling of long_high_continuation_probe.py (Probe A1).  Corrects the
operator-intent spec from "close > rolling_max(close, N)" to the
strict variant "low > rolling_max(close, N)" — the entire bar holds
above the prior N-bar close max, no intraday violation back into the
prior range.

This is a strictly tighter condition: only fires when the bar opens
above prior resistance and never trades back into it.  Hypothesis:
clean breakouts (no pullback within the bar) carry a stronger
continuation edge than the loose close-only formulation, which the
earlier probe REJECTED at +0.003 EV.

Same universe, same metrics, same pre-registered gate.  Pure spec
correction — re-test on the corrected formulation.

Note on overlap with the close-based probe: every strict fire IS
also a close-based fire (low > x implies close ≥ low > x), so the
strict events are a SUBSET of the close events.  The interesting
question is whether the survivor subset has materially better
per-FY behaviour.

Output: src/analysis/benchmark.md § Long-Term High Continuation Probe (Strict).
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.long_high_continuation_probe import (
    _ev,
    _FY_CONFIG,
    _FY_OOS,
    _FY_TRAINING,
    _GATE_DR_MIN,
    _GATE_OVERLAP_MAX,
    _GATE_POOLED_EV,
    _H_FORWARD,
    _NS,
    _TREND_CAP,
    _ZZ_MID_SIZE,
    _ZZ_SIZE,
    _atr_14,
    _load_rev_nhi_fires,
    _stocks_for_fy,
)
from src.analysis.sign_benchmark import _first_zigzag_peak
from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SECTION_HEADER = "## Long-Term High Continuation Probe (Strict)"


@dataclass
class _Fire:
    stock:        str
    fire_date:    datetime.date
    n_window:     int
    fy:           str
    score:        float | None
    r_h10:        float | None
    trend_dir:    int | None
    trend_mag:    float | None


def _measure_stock_strict(stock: str, fy_label: str,
                          bench_start: datetime.date,
                          bench_end: datetime.date) -> list[_Fire]:
    """As _measure_stock, but fires when low[T] > prior_N_close_max."""
    Ohlcv = OHLCV_MODEL_MAP["1d"]
    lookback_start = bench_start - datetime.timedelta(days=400)
    lookahead_end  = bench_end   + datetime.timedelta(days=60)
    with get_session() as s:
        rows = s.execute(
            select(Ohlcv.ts, Ohlcv.open_price, Ohlcv.high_price,
                   Ohlcv.low_price, Ohlcv.close_price)
            .where(
                Ohlcv.stock_code == stock,
                Ohlcv.ts >= datetime.datetime.combine(lookback_start,
                    datetime.time.min, tzinfo=datetime.timezone.utc),
                Ohlcv.ts <= datetime.datetime.combine(lookahead_end,
                    datetime.time.max, tzinfo=datetime.timezone.utc),
            )
            .order_by(Ohlcv.ts)
        ).all()
    if len(rows) < max(_NS) + 30:
        return []

    df = pd.DataFrame({
        "ts":    [r.ts for r in rows],
        "open":  [float(r.open_price)  for r in rows],
        "high":  [float(r.high_price)  for r in rows],
        "low":   [float(r.low_price)   for r in rows],
        "close": [float(r.close_price) for r in rows],
    })
    df["date"] = df["ts"].apply(lambda t: t.date() if hasattr(t, "date") else t)
    df = df.drop_duplicates(subset=["date"]).reset_index(drop=True)

    atr = _atr_14(df["high"], df["low"], df["close"])

    @dataclass
    class _Bar:
        dt:    datetime.datetime
        open:  float
        high:  float
        low:   float
        close: float
    bars_1d = [
        _Bar(dt=row.ts if hasattr(row.ts, "tzinfo") else
                 datetime.datetime.combine(row.ts, datetime.time.min,
                                           tzinfo=datetime.timezone.utc),
             open=row["open"], high=row["high"],
             low=row["low"],   close=row["close"])
        for _, row in df.iterrows()
    ]

    fires: list[_Fire] = []
    closes = df["close"].to_numpy()
    opens  = df["open"].to_numpy()
    lows   = df["low"].to_numpy()
    dates  = df["date"].to_numpy()
    n_bars = len(df)

    for n in _NS:
        for i in range(n, n_bars):
            d = dates[i]
            if d < bench_start or d > bench_end:
                continue
            prior_max = closes[i - n : i].max()
            # STRICT: low[T] > prior_max (not just close[T])
            if lows[i] <= prior_max:
                continue
            sc_atr = atr.iloc[i]
            score = ((closes[i] - prior_max) / sc_atr) if sc_atr and sc_atr > 0 else None

            r10: float | None = None
            if i + _H_FORWARD < n_bars and i + 1 < n_bars:
                r10 = (closes[i + _H_FORWARD] - opens[i + 1]) / opens[i + 1]

            tdir, _tbars, tmag = _first_zigzag_peak(
                bars_1d[i].dt, bars_1d, _TREND_CAP, _ZZ_SIZE, _ZZ_MID_SIZE,
            )
            fires.append(_Fire(
                stock=stock, fire_date=d, n_window=n, fy=fy_label,
                score=score, r_h10=r10, trend_dir=tdir, trend_mag=tmag,
            ))
    return fires


def _format_report(df: pd.DataFrame, overlap_pct: dict[int, float]) -> str:
    lines = [
        f"\n{_SECTION_HEADER}",
        f"\nProbe run: {datetime.date.today()}.  Spec-corrected sibling of the "
        "close-based probe — does a STRICT N-bar high breakout "
        "(`low[T] > rolling_max(close, N)[T-1]`, i.e. entire bar above prior "
        f"resistance) carry continuation signal for N ∈ {_NS}?",
        "",
        "Every strict fire is also a close-based fire (low > x implies close ≥ "
        "low > x), so this is a SUBSET of the events tested in the prior probe. "
        "The hypothesis being re-tested: clean breakouts (no intraday retracement) "
        "carry the edge the loose close-based formulation does not.",
        "",
        "**Pre-registered gate** (unchanged from prior cycle):",
        f"  - pooled EV (training FYs {_FY_TRAINING[0]}..{_FY_TRAINING[-1]}) ≥ {_GATE_POOLED_EV:+.3f}",
        "  - FY2025 OOS EV > 0",
        "  - all training FYs EV ≥ 0",
        f"  - DR ≥ {_GATE_DR_MIN*100:.0f}% (secondary)",
        f"  - rev_nhi same-bar overlap ≤ {_GATE_OVERLAP_MAX*100:.0f}%",
        "",
        "### Per N — Pooled (training) + per-FY EV table",
        "",
        "| N | n_total | n_train | overlap rev_nhi | pooled EV | DR | mean r_h10 | "
        + " | ".join([f"EV {fy}" for fy in _FY_TRAINING + [_FY_OOS]])
        + " | Gate |",
        "|---|---:|---:|---:|---:|---:|---:|"
        + "---:|" * (len(_FY_TRAINING) + 1) + "---|",
    ]
    for n in _NS:
        sub = df[df["n_window"] == n]
        sub_train = sub[sub["fy"].isin(_FY_TRAINING)]
        sub_oos   = sub[sub["fy"] == _FY_OOS]
        ev_pool, dr_pool, n_pool = _ev(sub_train)
        per_fy: dict[str, tuple[float, float, int]] = {}
        for fy in _FY_TRAINING:
            per_fy[fy] = _ev(sub_train[sub_train["fy"] == fy])
        per_fy[_FY_OOS] = _ev(sub_oos)
        mean_r10 = sub["r_h10"].dropna().mean() if not sub.empty else float("nan")

        ok = True
        notes: list[str] = []
        if math.isnan(ev_pool) or ev_pool < _GATE_POOLED_EV:
            ok = False
            notes.append(f"pooled EV {ev_pool:+.4f} < {_GATE_POOLED_EV:+.4f}")
        if math.isnan(per_fy[_FY_OOS][0]) or per_fy[_FY_OOS][0] <= 0:
            ok = False
            notes.append(f"FY2025 EV {per_fy[_FY_OOS][0]:+.4f} ≤ 0")
        neg_fy = [fy for fy in _FY_TRAINING
                  if not math.isnan(per_fy[fy][0]) and per_fy[fy][0] < 0]
        if neg_fy:
            ok = False
            notes.append(f"negative-EV FYs: {','.join(neg_fy)}")
        if math.isnan(dr_pool) or dr_pool < _GATE_DR_MIN:
            ok = False
            notes.append(f"DR {dr_pool*100:.1f}% < {_GATE_DR_MIN*100:.0f}%")
        if overlap_pct.get(n, 1.0) > _GATE_OVERLAP_MAX:
            ok = False
            notes.append(f"overlap {overlap_pct[n]*100:.0f}% > {_GATE_OVERLAP_MAX*100:.0f}%")

        cells = [f"| {n} | {len(sub)} | {n_pool} | {overlap_pct.get(n, float('nan'))*100:.1f}% | "
                 f"{ev_pool:+.4f} | {dr_pool*100:.1f}% | {mean_r10*100:+.2f}pp"]
        for fy in _FY_TRAINING + [_FY_OOS]:
            e, _, nn = per_fy[fy]
            cells.append(f" | {e:+.4f} (n={nn})" if not math.isnan(e) else " | —")
        cells.append(f" | **{'PASS' if ok else 'FAIL'}** |")
        lines.append("".join(cells))
        if notes:
            lines.append(f"|  |  |  |  |  |  |  |"
                         + " |" * (len(_FY_TRAINING) + 1)
                         + f" notes: {'; '.join(notes)} |")
    return "\n".join(lines)


def _append_to_benchmark(md: str) -> None:
    existing = _BENCH_MD.read_text() if _BENCH_MD.exists() else ""
    if _SECTION_HEADER in existing:
        idx = existing.index(_SECTION_HEADER)
        rest = existing[idx + len(_SECTION_HEADER):]
        nxt = rest.find("\n## ")
        existing = (existing[:idx].rstrip() + "\n") if nxt < 0 \
                   else (existing[:idx].rstrip() + "\n" + rest[nxt:].lstrip("\n"))
    _BENCH_MD.write_text(existing.rstrip() + "\n" + md.lstrip("\n"))
    logger.info("Appended report to {}", _BENCH_MD)


def main() -> None:
    rev_nhi_pairs = _load_rev_nhi_fires()
    logger.info("Loaded {} rev_nhi (stock, date) pairs", len(rev_nhi_pairs))

    all_fires: list[_Fire] = []
    for fy_label, start_str, end_str, cluster_year in _FY_CONFIG:
        cluster_set = f"classified{cluster_year}"
        bench_start = datetime.date.fromisoformat(start_str)
        bench_end   = datetime.date.fromisoformat(end_str)
        codes = _stocks_for_fy(cluster_set)
        if not codes:
            logger.warning("No cluster members for {} ({}) — skipping",
                           fy_label, cluster_set)
            continue
        logger.info("{}: probing {} stocks", fy_label, len(codes))
        for i, code in enumerate(codes):
            if i and i % 50 == 0:
                logger.info("  {}: {}/{} stocks done, fires so far={}",
                            fy_label, i, len(codes), len(all_fires))
            all_fires.extend(_measure_stock_strict(code, fy_label, bench_start, bench_end))
        logger.info("{}: cumulative fires={}", fy_label, len(all_fires))

    df = pd.DataFrame([f.__dict__ for f in all_fires])
    if df.empty:
        logger.error("No fires found — aborting")
        return

    overlap_pct: dict[int, float] = {}
    for n in _NS:
        sub = df[df["n_window"] == n]
        if sub.empty:
            overlap_pct[n] = float("nan")
            continue
        hits = sub.apply(
            lambda r: (r["stock"], r["fire_date"]) in rev_nhi_pairs, axis=1
        ).sum()
        overlap_pct[n] = float(hits) / len(sub)

    report = _format_report(df, overlap_pct)

    pass_ns: list[int] = []
    for n in _NS:
        sub_train = df[(df["n_window"] == n) & (df["fy"].isin(_FY_TRAINING))]
        sub_oos   = df[(df["n_window"] == n) & (df["fy"] == _FY_OOS)]
        ev_pool, dr_pool, _ = _ev(sub_train)
        ev_oos, _, _        = _ev(sub_oos)
        neg_fy = any(
            not math.isnan(_ev(sub_train[sub_train["fy"] == fy])[0])
            and _ev(sub_train[sub_train["fy"] == fy])[0] < 0
            for fy in _FY_TRAINING
        )
        ok = (not math.isnan(ev_pool) and ev_pool >= _GATE_POOLED_EV
              and not math.isnan(ev_oos) and ev_oos > 0
              and not math.isnan(dr_pool) and dr_pool >= _GATE_DR_MIN
              and overlap_pct.get(n, 1.0) <= _GATE_OVERLAP_MAX
              and not neg_fy)
        if ok:
            pass_ns.append(n)

    report += "\n\n### Verdict\n\n"
    if pass_ns:
        report += (f"**Strict variant: at least one N cleared the gate** "
                   f"(N={pass_ns}).  Recommend follow-up debate to authorize "
                   "a real detector + rebench using the strict fire condition.")
    else:
        report += ("**Strict variant also REJECTS — no N cleared the gate.**  "
                   "The clean-breakout hypothesis does not invert the loose-"
                   "breakout null on this universe / framing.  Closing both "
                   "the close-based and strict variants as REJECT.")
    report += "\n"

    print(report)
    _append_to_benchmark(report)


if __name__ == "__main__":
    main()
