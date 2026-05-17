"""bullish_confluence_v2_probe — confluence via per-sign validity windows.

v1 collapsed signs to the calendar day they fired — only 13 stock-dates
in 7 FYs ever had ≥3 bullish signs simultaneously, because signs from
different detectors detect different timescales and rarely fire on the
same date.

v2 uses the catalogue's own `valid_bars` — each fire remains in effect
for K trading days (str_hold=3, brk_bol=3, others=5).  For each
(stock, trade_date) in the FY, count signs whose [fired_at, fired_at+K]
window contains trade_date.  Outcome at trade_date = next confirmed
zigzag peak FROM that date (same convention as benchmark.md).

Same pre-registered gate as v1:
    EV[≥3 signs] − EV[1 sign] ≥ +1.0pp
    EV[≥2 signs] − EV[1 sign] ≥ +0.5pp
    uplift sign consistent in ≥4 of 6 training FYs
    FY2025 uplift sign matches pooled training
    n[≥3 signs] in FY2025 ≥ 50

Read-only.  Output: src/analysis/benchmark.md
§ Bullish Confluence Probe (validity-windowed).
"""
from __future__ import annotations

import datetime
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import (
    SignBenchmarkEvent,
    SignBenchmarkRun,
    StockClusterMember,
    StockClusterRun,
)
from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP
from src.indicators.zigzag import detect_peaks

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SECTION_HEADER = "## Bullish Confluence Probe (validity-windowed)"
_MULTIYEAR_MIN_RUN_ID = 47

# Bullish-set valid_bars defaults (from detector module sources).
_VALID_BARS: dict[str, int] = {
    "str_hold":  3,
    "str_lead":  5,
    "str_lag":   5,
    "brk_sma":   5,
    "brk_bol":   3,
    "rev_lo":    5,   # rev_peak side="lo"
    "rev_nlo":   5,
}
_BULLISH_SIGNS = tuple(_VALID_BARS)

_FY_CONFIG: list[tuple[str, str, str, str]] = [
    ("FY2018", "2018-04-01", "2019-03-31", "2017"),
    ("FY2019", "2019-04-01", "2020-03-31", "2018"),
    ("FY2020", "2020-04-01", "2021-03-31", "2019"),
    ("FY2021", "2021-04-01", "2022-03-31", "2020"),
    ("FY2022", "2022-04-01", "2023-03-31", "2021"),
    ("FY2023", "2023-04-01", "2024-03-31", "2022"),
    ("FY2024", "2024-04-01", "2025-03-31", "2023"),
    ("FY2025", "2025-04-01", "2026-03-31", "2024"),
]
_FY_TRAINING = [c[0] for c in _FY_CONFIG if c[0] != "FY2025"]
_FY_OOS = "FY2025"

_TREND_CAP = 30
_ZZ_SIZE = 5
_ZZ_MID_SIZE = 2

_GATE_UPLIFT_3 = 0.010
_GATE_UPLIFT_2 = 0.005
_GATE_FY_CONSIST = 4
_GATE_OOS_N_MIN = 50


# ── 1. Load universe + fires ──────────────────────────────────────────


def _stocks_for_fy(cluster_set: str) -> list[str]:
    with get_session() as s:
        run = s.execute(
            select(StockClusterRun).where(StockClusterRun.fiscal_year == cluster_set)
        ).scalar_one_or_none()
        if run is None:
            return []
        return list(s.execute(
            select(StockClusterMember.stock_code)
            .where(StockClusterMember.run_id == run.id,
                   StockClusterMember.is_representative.is_(True))
        ).scalars().all())


def _load_fires() -> dict[str, list[tuple[str, datetime.date]]]:
    """Return {stock: [(sign_type, fire_date), ...]} for all bullish signs."""
    with get_session() as s:
        rows = s.execute(
            select(
                SignBenchmarkRun.sign_type,
                SignBenchmarkEvent.stock_code,
                SignBenchmarkEvent.fired_at,
            )
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(
                SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID,
                SignBenchmarkRun.sign_type.in_(_BULLISH_SIGNS),
            )
        ).all()
    by_stock: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for sign, stock, fired_at in rows:
        d = fired_at.date() if hasattr(fired_at, "date") else fired_at
        by_stock[stock].append((sign, d))
    logger.info("Loaded {} fires across {} stocks", sum(len(v) for v in by_stock.values()),
                len(by_stock))
    return by_stock


# ── 2. Per-stock probe ────────────────────────────────────────────────


@dataclass
class _Bar:
    dt:    datetime.datetime
    open:  float
    high:  float
    low:   float
    close: float


def _next_peak_from(idx: int, bars: list[_Bar], peaks) -> tuple[int | None, float | None]:
    """Return (trend_dir, trend_mag) for first confirmed peak with bar_index > idx,
    within _TREND_CAP bars.  Uses pre-computed peaks list (sorted by bar_index)."""
    if not bars or idx >= len(bars):
        return (None, None)
    entry_price = bars[idx].open
    if not entry_price:
        return (None, None)
    for p in peaks:
        if abs(p.direction) != 2:
            continue
        if p.bar_index <= idx:
            continue
        bars_ahead = p.bar_index - idx
        if bars_ahead > _TREND_CAP:
            return (None, None)
        trend_dir = +1 if p.direction == 2 else -1
        mag = abs(p.price - entry_price) / entry_price
        return (trend_dir, mag)
    return (None, None)


def _probe_stock(stock: str, fires: list[tuple[str, datetime.date]],
                 fy_label: str, bench_start: datetime.date,
                 bench_end: datetime.date) -> list[dict]:
    """For each trade_date in FY where ≥1 sign is valid, record (count, outcome)."""
    Ohlcv = OHLCV_MODEL_MAP["1d"]
    # Need lookback for prior fires; lookahead for trend cap
    span_start = bench_start - datetime.timedelta(days=15)
    span_end   = bench_end   + datetime.timedelta(days=60)
    with get_session() as s:
        rows = s.execute(
            select(Ohlcv.ts, Ohlcv.open_price, Ohlcv.high_price,
                   Ohlcv.low_price, Ohlcv.close_price)
            .where(
                Ohlcv.stock_code == stock,
                Ohlcv.ts >= datetime.datetime.combine(span_start,
                    datetime.time.min, tzinfo=datetime.timezone.utc),
                Ohlcv.ts <= datetime.datetime.combine(span_end,
                    datetime.time.max, tzinfo=datetime.timezone.utc),
            )
            .order_by(Ohlcv.ts)
        ).all()
    if len(rows) < 40:
        return []

    bars: list[_Bar] = []
    seen_dates: set[datetime.date] = set()
    for r in rows:
        d = r.ts.date() if hasattr(r.ts, "date") else r.ts
        if d in seen_dates:
            continue
        seen_dates.add(d)
        ts = r.ts if hasattr(r.ts, "tzinfo") else \
             datetime.datetime.combine(r.ts, datetime.time.min,
                                       tzinfo=datetime.timezone.utc)
        bars.append(_Bar(dt=ts, open=float(r.open_price), high=float(r.high_price),
                         low=float(r.low_price), close=float(r.close_price)))

    if not bars:
        return []

    trading_dates = [b.dt.date() for b in bars]
    date_to_idx = {d: i for i, d in enumerate(trading_dates)}

    # Pre-compute peaks once
    highs = [b.high for b in bars]
    lows  = [b.low  for b in bars]
    peaks = sorted(detect_peaks(highs, lows, size=_ZZ_SIZE, middle_size=_ZZ_MID_SIZE),
                   key=lambda p: p.bar_index)

    # Build per-date valid-sign set
    valid_per_date: dict[int, set[str]] = defaultdict(set)
    for sign, fire_date in fires:
        if fire_date not in date_to_idx:
            continue
        fi = date_to_idx[fire_date]
        vb = _VALID_BARS[sign]
        for j in range(fi, min(fi + vb + 1, len(bars))):
            valid_per_date[j].add(sign)

    out: list[dict] = []
    for i, d in enumerate(trading_dates):
        if d < bench_start or d > bench_end:
            continue
        valid = valid_per_date.get(i, set())
        if not valid:
            continue
        tdir, tmag = _next_peak_from(i, bars, peaks)
        out.append({
            "stock":     stock,
            "trade_date": d,
            "fy":        fy_label,
            "n_signs":   len(valid),
            "signs":     ",".join(sorted(valid)),
            "trend_dir": tdir,
            "trend_mag": tmag,
        })
    return out


# ── 3. EV + report ────────────────────────────────────────────────────


def _ev(sub: pd.DataFrame) -> tuple[float, float, int]:
    n = len(sub)
    if n == 0:
        return (float("nan"), float("nan"), 0)
    wd = sub.dropna(subset=["trend_dir"])
    if wd.empty:
        return (float("nan"), float("nan"), n)
    dr = (wd["trend_dir"] == 1).sum() / len(wd)
    flw = wd[wd["trend_dir"] == 1]["trend_mag"].dropna()
    rev = wd[wd["trend_dir"] == -1]["trend_mag"].dropna()
    if flw.empty or rev.empty:
        return (float("nan"), dr, n)
    return (dr * float(flw.mean()) - (1 - dr) * float(rev.mean()), dr, n)


def _bucket(n: int) -> str:
    if n >= 3:
        return "≥3"
    return str(n)


def _format_report(df: pd.DataFrame) -> tuple[str, bool]:
    df = df.copy()
    df["bucket"] = df["n_signs"].apply(_bucket)
    buckets = ["1", "2", "≥3"]
    fys = _FY_TRAINING + [_FY_OOS]

    lines = [
        f"\n{_SECTION_HEADER}",
        f"\nProbe run: {datetime.date.today()}.  v2 of bullish-confluence — "
        "uses each sign's `valid_bars` (3 or 5 trading days per the detector "
        "defaults) so a fire counts toward confluence on every trade_date "
        "within its validity window, not only the calendar day it fired.",
        "",
        "Bullish set + valid_bars: "
        + ", ".join(f"{s}({_VALID_BARS[s]})" for s in _BULLISH_SIGNS),
        "",
        "Outcome at trade_date = next confirmed zigzag peak from that date "
        f"(ZZ size={_ZZ_SIZE}, mid={_ZZ_MID_SIZE}, cap={_TREND_CAP} bars) — "
        "same convention as benchmark.md.  Trade_dates with zero valid signs "
        "are skipped (not investable in this framework).",
        "",
        "**Pre-registered gate** (unchanged from v1):",
        f"  - EV[≥3 signs] − EV[1 sign] ≥ +{_GATE_UPLIFT_3*100:.1f}pp",
        f"  - EV[≥2 signs] − EV[1 sign] ≥ +{_GATE_UPLIFT_2*100:.1f}pp",
        f"  - uplift sign consistent in ≥{_GATE_FY_CONSIST} of {len(_FY_TRAINING)} training FYs",
        f"  - FY2025 OOS uplift sign matches pooled training sign",
        f"  - n[≥3 signs] in FY2025 ≥ {_GATE_OOS_N_MIN}",
        "",
        "### Confluence buckets — pooled (training)",
        "",
        "| Bucket | n_train | n_oos | DR (train) | EV (train) | EV (FY2025) | mean signs/day |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    pooled_train: dict[str, tuple[float, float, int]] = {}
    pooled_oos: dict[str, tuple[float, float, int]] = {}
    for b in buckets:
        sub = df[df["bucket"] == b]
        sub_t = sub[sub["fy"].isin(_FY_TRAINING)]
        sub_o = sub[sub["fy"] == _FY_OOS]
        pooled_train[b] = _ev(sub_t)
        pooled_oos[b]   = _ev(sub_o)
        ev_t, dr_t, n_t = pooled_train[b]
        ev_o, _, n_o    = pooled_oos[b]
        mean_signs = float(sub["n_signs"].mean()) if not sub.empty else float("nan")
        ev_t_s = f"{ev_t:+.4f}" if not math.isnan(ev_t) else "—"
        ev_o_s = f"{ev_o:+.4f}" if not math.isnan(ev_o) else "—"
        dr_t_s = f"{dr_t*100:.1f}%" if not math.isnan(dr_t) else "—"
        lines.append(f"| {b} | {n_t} | {n_o} | {dr_t_s} | {ev_t_s} | {ev_o_s} | {mean_signs:.2f} |")

    ev_1_t = pooled_train["1"][0]
    ev_2_t = pooled_train["2"][0]
    ev_3_t = pooled_train["≥3"][0]
    ev_1_o = pooled_oos["1"][0]
    ev_3_o = pooled_oos["≥3"][0]
    up_2 = ev_2_t - ev_1_t if not (math.isnan(ev_2_t) or math.isnan(ev_1_t)) else float("nan")
    up_3 = ev_3_t - ev_1_t if not (math.isnan(ev_3_t) or math.isnan(ev_1_t)) else float("nan")
    up_3_o = ev_3_o - ev_1_o if not (math.isnan(ev_3_o) or math.isnan(ev_1_o)) else float("nan")

    lines += [
        "",
        "### Pooled uplifts (training)",
        "",
        f"- EV[≥2 signs] − EV[1 sign] = **{up_2*100:+.2f}pp**  (gate ≥ +{_GATE_UPLIFT_2*100:.1f}pp)",
        f"- EV[≥3 signs] − EV[1 sign] = **{up_3*100:+.2f}pp**  (gate ≥ +{_GATE_UPLIFT_3*100:.1f}pp)",
        f"- FY2025 OOS uplift EV[≥3] − EV[1] = **{up_3_o*100:+.2f}pp**",
        "",
        "### Per-FY EV by confluence bucket",
        "",
        "| FY | " + " | ".join([f"EV[{b}] (n)" for b in buckets]) + " | Uplift[≥3]−[1] |",
        "|----|" + ":---:|" * (len(buckets) + 1),
    ]
    per_fy_uplift: list[float] = []
    for fy in fys:
        cells: list[str] = []
        evs: dict[str, float] = {}
        for b in buckets:
            sub = df[(df["fy"] == fy) & (df["bucket"] == b)]
            e, _, n = _ev(sub)
            cells.append(f"{e:+.4f} (n={n})" if not math.isnan(e) else "—")
            evs[b] = e
        up = (evs["≥3"] - evs["1"]) if not (math.isnan(evs["≥3"]) or math.isnan(evs["1"])) else float("nan")
        cells.append(f"**{up*100:+.2f}pp**" if not math.isnan(up) else "—")
        lines.append(f"| {fy} | " + " | ".join(cells) + " |")
        if fy in _FY_TRAINING and not math.isnan(up):
            per_fy_uplift.append(up)

    # Gate
    ok = True
    notes: list[str] = []
    if math.isnan(up_3) or up_3 < _GATE_UPLIFT_3:
        ok = False
        notes.append(f"pooled uplift[≥3] {up_3*100:+.2f}pp < +{_GATE_UPLIFT_3*100:.1f}pp")
    if math.isnan(up_2) or up_2 < _GATE_UPLIFT_2:
        ok = False
        notes.append(f"pooled uplift[≥2] {up_2*100:+.2f}pp < +{_GATE_UPLIFT_2*100:.1f}pp")
    if not math.isnan(up_3):
        ps = math.copysign(1, up_3)
        consist = sum(1 for u in per_fy_uplift if math.copysign(1, u) == ps)
        if consist < _GATE_FY_CONSIST:
            ok = False
            notes.append(f"only {consist}/{len(_FY_TRAINING)} training FYs uplift-consistent (<{_GATE_FY_CONSIST})")
        if math.isnan(up_3_o) or math.copysign(1, up_3_o) != ps:
            ok = False
            notes.append(f"FY2025 uplift sign mismatch ({up_3_o*100:+.2f}pp vs pooled {up_3*100:+.2f}pp)")
    n_oos_3 = pooled_oos["≥3"][2]
    if n_oos_3 < _GATE_OOS_N_MIN:
        ok = False
        notes.append(f"FY2025 n[≥3 signs] = {n_oos_3} < {_GATE_OOS_N_MIN}")

    lines += [
        "",
        "### Gate verdict",
        "",
        f"**{'PASS' if ok else 'FAIL'}** — " +
        ("all gates clear" if ok else "gate notes: " + "; ".join(notes)),
        "",
    ]
    if ok:
        lines.append(
            "**Confluence framework (validity-windowed) is empirically real.**  "
            "Authorize brk_nhi as a sign that feeds the confluence tally; "
            "re-run this probe with brk_nhi included to verify incremental value."
        )
    else:
        lines.append(
            "**Validity-windowed confluence also fails the gate.**  Even when "
            "signs are credited for their full validity window, multi-sign "
            "agreement on the same stock-date does not produce material EV "
            "uplift on this universe.  Hypothesis (multi-sign agreement → "
            "stronger directional bet) is not supported by existing-sign data."
        )
    return "\n".join(lines), ok


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
    by_stock = _load_fires()
    all_rows: list[dict] = []
    for fy_label, start_str, end_str, cluster_year in _FY_CONFIG:
        bench_start = datetime.date.fromisoformat(start_str)
        bench_end   = datetime.date.fromisoformat(end_str)
        codes = _stocks_for_fy(f"classified{cluster_year}")
        if not codes:
            logger.warning("No cluster for {} — skipping", fy_label)
            continue
        logger.info("{}: probing {} stocks", fy_label, len(codes))
        for i, code in enumerate(codes):
            if i and i % 50 == 0:
                logger.info("  {}: {}/{} stocks done, rows so far={}",
                            fy_label, i, len(codes), len(all_rows))
            fires = by_stock.get(code, [])
            if not fires:
                continue
            all_rows.extend(_probe_stock(code, fires, fy_label, bench_start, bench_end))
        logger.info("{}: cumulative rows={}", fy_label, len(all_rows))

    df = pd.DataFrame(all_rows)
    if df.empty:
        logger.error("No rows — aborting")
        return
    report, _ = _format_report(df)
    print(report)
    _append_to_benchmark(report)


if __name__ == "__main__":
    main()
