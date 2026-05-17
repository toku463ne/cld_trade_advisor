"""str_hold_feature_probe — does gap / candle data carry signal on str_hold?

Read-only diagnostic.  Loads every str_hold fire event in the multi-year
benchmark (run_ids >= 47), enriches each with four candidate bar features
derived from the daily OHLCV bar at the fire date and the prior bar,
buckets each feature, and reports DR / EV per FY with bootstrap CI on
the tercile spread.

Two of the four features depend on the *fire-bar's close*.  The detector
itself fires at the *start* of the fire-bar (`date_to_first[d]`) but its
qualifying logic at lines 79-103 of `src/signs/str_hold.py` consumes the
fire-bar's close to compute pct_change.  So body_pct[T] and body_frac[T]
are NOT fire-time legal — they are end-of-day only.  The probe surfaces
that distinction in a `fire_time_legal` column so any follow-up gate
proposal can avoid building a look-ahead detector.

Pre-registered accept gate per /sign-debate 2026-05-17 (must hold ALL):
  - pooled |Δ EV (top tercile − bottom tercile)| ≥ 0.5pp
  - 95% bootstrap CI on Δ EV excludes 0
  - per-FY direction of Δ EV consistent in ≥4 of 5 training FYs
  - FY2025 OOS sign matches pooled training sign
  - per-FY CI excludes 0 in ≥2 of 5 training FYs

If no feature clears the gate → Q1 (add candle features) and Q2 (GA-tune)
both rejected; str_hold detector left as-is.

The largest-|ΔEV| feature also gets one ADX × Kumo cross-tab to detect
conditional signal masked by univariate-pooled scope.

Output: markdown table appended to src/analysis/benchmark.md under
`## str_hold Feature Probe`.  No DB writes; no detector edits.
"""
from __future__ import annotations

import datetime
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import (
    N225RegimeSnapshot,
    SignBenchmarkEvent,
    SignBenchmarkRun,
)
from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP

_MULTIYEAR_MIN_RUN_ID = 47
_SIGN = "str_hold"
_BOOTSTRAP_ITERS = 2000
_RNG_SEED = 20260517
_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SECTION_HEADER = "## str_hold Feature Probe"

# FY mapping (from sign_benchmark_multiyear.FY_CONFIG)
_FY_TRAINING = ["FY2020", "FY2021", "FY2022", "FY2023", "FY2024"]
_FY_OOS = "FY2025"

# Critic-tightened gate (judge-approved, 2026-05-17)
_GATE_POOLED_EV_MIN = 0.005   # 0.5pp
_GATE_FY_CONSIST    = 4       # of 5 training FYs direction-consistent
_GATE_FY_CI_PASS    = 2       # of 5 training FYs with CI excluding 0
_ADX_CHOPPY = 20.0


def _fiscal_label(d: datetime.date) -> str | None:
    """JP fiscal year — Apr d to Mar d+1 = FY(d)."""
    y = d.year if d.month >= 4 else d.year - 1
    return f"FY{y}"


# ── 1. Load fire events ───────────────────────────────────────────────


@dataclass
class _Event:
    stock_code: str
    fire_date:  datetime.date
    direction:  int           # +1 follow, -1 reverse
    magnitude:  float | None
    fy:         str


def _load_events() -> list[_Event]:
    with get_session() as s:
        rows = s.execute(
            select(
                SignBenchmarkEvent.stock_code,
                SignBenchmarkEvent.fired_at,
                SignBenchmarkEvent.trend_direction,
                SignBenchmarkEvent.trend_magnitude,
            )
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(
                SignBenchmarkRun.sign_type == _SIGN,
                SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID,
                SignBenchmarkEvent.trend_direction.isnot(None),
            )
        ).all()

    events: list[_Event] = []
    for r in rows:
        d = r.fired_at.date() if hasattr(r.fired_at, "date") else r.fired_at
        fy = _fiscal_label(d)
        if fy is None:
            continue
        events.append(_Event(r.stock_code, d, int(r.trend_direction),
                             float(r.trend_magnitude) if r.trend_magnitude is not None else None,
                             fy))
    logger.info("Loaded {} str_hold events ({} FYs)", len(events),
                len({e.fy for e in events}))
    return events


# ── 2. Enrich events with bar features ─────────────────────────────────


def _enrich(events: list[_Event]) -> pd.DataFrame:
    """For each event, compute (gap_pct, body_dir_prev, body_pct_T, body_frac_T)."""
    Ohlcv = OHLCV_MODEL_MAP["1d"]
    by_stock: dict[str, list[_Event]] = defaultdict(list)
    for e in events:
        by_stock[e.stock_code].append(e)

    rows: list[dict] = []
    with get_session() as s:
        for code, evs in by_stock.items():
            dates_needed = {e.fire_date for e in evs}
            min_d = min(dates_needed) - datetime.timedelta(days=10)
            max_d = max(dates_needed) + datetime.timedelta(days=2)
            bars = s.execute(
                select(Ohlcv.ts, Ohlcv.open_price, Ohlcv.high_price,
                       Ohlcv.low_price, Ohlcv.close_price)
                .where(
                    Ohlcv.stock_code == code,
                    Ohlcv.ts >= datetime.datetime.combine(min_d, datetime.time.min,
                                                         tzinfo=datetime.timezone.utc),
                    Ohlcv.ts <= datetime.datetime.combine(max_d, datetime.time.max,
                                                         tzinfo=datetime.timezone.utc),
                )
                .order_by(Ohlcv.ts)
            ).all()
            if not bars:
                continue
            by_date: dict[datetime.date, tuple] = {}
            sorted_dates: list[datetime.date] = []
            for ts, o, h, l, c in bars:
                d = ts.date() if hasattr(ts, "date") else ts
                if d not in by_date:
                    by_date[d] = (float(o), float(h), float(l), float(c))
                    sorted_dates.append(d)
            for ev in evs:
                if ev.fire_date not in by_date:
                    continue
                fi = sorted_dates.index(ev.fire_date) if ev.fire_date in sorted_dates else -1
                if fi <= 0:
                    continue   # no prior bar
                o_t, h_t, l_t, c_t = by_date[ev.fire_date]
                o_p, h_p, l_p, c_p = by_date[sorted_dates[fi - 1]]
                if c_p <= 0 or o_t <= 0 or (h_t - l_t) <= 0 or o_p <= 0:
                    continue
                gap_pct        = (o_t - c_p) / c_p
                body_dir_prev  =  1 if c_p > o_p else (-1 if c_p < o_p else 0)
                body_pct_T     = (c_t - o_t) / o_t
                body_frac_T    = abs(c_t - o_t) / (h_t - l_t)
                rows.append({
                    "stock":         ev.stock_code,
                    "fire_date":     ev.fire_date,
                    "fy":            ev.fy,
                    "direction":     ev.direction,
                    "magnitude":     ev.magnitude,
                    "gap_pct":       gap_pct,
                    "body_dir_prev": body_dir_prev,
                    "body_pct_T":    body_pct_T,
                    "body_frac_T":   body_frac_T,
                })
    df = pd.DataFrame(rows)
    logger.info("Enriched {} events with bar features (dropped {})",
                len(df), len(events) - len(df))
    return df


# ── 3. EV computation + bootstrap ─────────────────────────────────────


def _ev_of_subset(sub: pd.DataFrame) -> tuple[float, int, float]:
    """Return (EV, n, DR) on a subset using benchmark-event direction & magnitude."""
    if sub.empty:
        return (float("nan"), 0, float("nan"))
    flw = sub[sub["direction"] ==  1]["magnitude"].dropna()
    rev = sub[sub["direction"] == -1]["magnitude"].dropna()
    n = len(sub)
    dr = (sub["direction"] == 1).sum() / n
    if flw.empty or rev.empty:
        return (float("nan"), n, dr)
    mag_flw = float(flw.mean())
    mag_rev = float(rev.mean())
    ev = dr * mag_flw - (1.0 - dr) * mag_rev
    return (ev, n, dr)


def _bootstrap_ev_delta(low: pd.DataFrame, high: pd.DataFrame,
                        iters: int = _BOOTSTRAP_ITERS,
                        rng: random.Random | None = None) -> tuple[float, float, float]:
    """Return (point, ci_lo, ci_hi) for EV(high) − EV(low)."""
    if rng is None:
        rng = random.Random(_RNG_SEED)
    low_arr  = low [["direction", "magnitude"]].to_numpy()
    high_arr = high[["direction", "magnitude"]].to_numpy()
    def _ev_arr(a: np.ndarray) -> float:
        if len(a) == 0:
            return float("nan")
        dir_  = a[:, 0]
        mag   = a[:, 1].astype(float)
        flw   = mag[(dir_ ==  1) & ~np.isnan(mag)]
        rev   = mag[(dir_ == -1) & ~np.isnan(mag)]
        if len(flw) == 0 or len(rev) == 0:
            return float("nan")
        dr    = (dir_ == 1).sum() / len(a)
        return dr * flw.mean() - (1 - dr) * rev.mean()
    point = _ev_arr(high_arr) - _ev_arr(low_arr)
    deltas: list[float] = []
    n_low, n_high = len(low_arr), len(high_arr)
    nprng = np.random.default_rng(_RNG_SEED)
    for _ in range(iters):
        bl = low_arr [nprng.integers(0, n_low,  n_low )]
        bh = high_arr[nprng.integers(0, n_high, n_high)]
        d = _ev_arr(bh) - _ev_arr(bl)
        if not math.isnan(d):
            deltas.append(d)
    if not deltas:
        return (point, float("nan"), float("nan"))
    lo = float(np.quantile(deltas, 0.025))
    hi = float(np.quantile(deltas, 0.975))
    return (point, lo, hi)


# ── 4. Per-feature analysis ───────────────────────────────────────────


@dataclass
class _FeatureCell:
    feature:        str
    fire_time_legal: bool
    bucket:         str       # "lo" / "mid" / "hi" or "-1" / "+1"
    n:              int
    dr:             float
    ev:             float


@dataclass
class _FeatureSummary:
    feature:         str
    fire_time_legal: bool
    cells:           list[_FeatureCell]
    pooled_delta:    float    # EV(hi) − EV(lo)
    ci_lo:           float
    ci_hi:           float
    per_fy_delta:    dict[str, tuple[float, float, float]]   # fy → (delta, ci_lo, ci_hi)
    oos_delta:       float
    gate_pass:       bool
    gate_notes:      list[str]


_FEATURES: list[tuple[str, bool, str]] = [
    # (column, fire_time_legal, kind)  — kind: "continuous" or "binary"
    ("gap_pct",       True,  "continuous"),
    ("body_dir_prev", True,  "binary"),
    ("body_pct_T",    False, "continuous"),
    ("body_frac_T",   False, "continuous"),
]


def _bucket_continuous(df: pd.DataFrame, col: str) -> pd.Series:
    """Return 'lo'/'mid'/'hi' per row using cohort-pooled terciles."""
    q1 = df[col].quantile(1/3)
    q2 = df[col].quantile(2/3)
    return df[col].apply(lambda x: "lo" if x <= q1 else ("hi" if x >= q2 else "mid"))


def _analyze(df: pd.DataFrame) -> list[_FeatureSummary]:
    summaries: list[_FeatureSummary] = []
    df_train = df[df["fy"].isin(_FY_TRAINING)]
    df_oos   = df[df["fy"] == _FY_OOS]

    for col, legal, kind in _FEATURES:
        sub = df.dropna(subset=[col])
        if sub.empty:
            continue

        if kind == "continuous":
            sub = sub.assign(_b=_bucket_continuous(sub, col))
            buckets_pair = ("lo", "hi")
            cells: list[_FeatureCell] = []
            for b in ["lo", "mid", "hi"]:
                ev, n, dr = _ev_of_subset(sub[sub["_b"] == b])
                cells.append(_FeatureCell(col, legal, b, n, dr, ev))
        else:
            sub = sub.assign(_b=sub["body_dir_prev"].astype(int).astype(str))
            buckets_pair = ("-1", "1")
            cells = []
            for b in ["-1", "0", "1"]:
                ev, n, dr = _ev_of_subset(sub[sub["_b"] == b])
                cells.append(_FeatureCell(col, legal, b, n, dr, ev))

        train = sub[sub["fy"].isin(_FY_TRAINING)]
        oos   = sub[sub["fy"] == _FY_OOS]

        lo_sub  = train[train["_b"] == buckets_pair[0]]
        hi_sub  = train[train["_b"] == buckets_pair[1]]
        if lo_sub.empty or hi_sub.empty:
            continue
        point, ci_lo, ci_hi = _bootstrap_ev_delta(lo_sub, hi_sub)

        per_fy: dict[str, tuple[float, float, float]] = {}
        for fy in _FY_TRAINING:
            f_lo = train[(train["fy"] == fy) & (train["_b"] == buckets_pair[0])]
            f_hi = train[(train["fy"] == fy) & (train["_b"] == buckets_pair[1])]
            if f_lo.empty or f_hi.empty:
                per_fy[fy] = (float("nan"), float("nan"), float("nan"))
            else:
                per_fy[fy] = _bootstrap_ev_delta(f_lo, f_hi, iters=500)

        o_lo = oos[oos["_b"] == buckets_pair[0]]
        o_hi = oos[oos["_b"] == buckets_pair[1]]
        oos_d = (_ev_of_subset(o_hi)[0] - _ev_of_subset(o_lo)[0]) \
                if not o_lo.empty and not o_hi.empty else float("nan")

        # Apply gate
        notes: list[str] = []
        ok = True
        # Pooled magnitude
        if abs(point) < _GATE_POOLED_EV_MIN:
            ok = False
            notes.append(f"pooled |ΔEV| {abs(point)*100:.2f}pp < 0.5pp")
        # Pooled CI excludes 0
        if not (math.isnan(ci_lo) or math.isnan(ci_hi)) and ci_lo <= 0 <= ci_hi:
            ok = False
            notes.append(f"pooled CI [{ci_lo*100:+.2f},{ci_hi*100:+.2f}]pp includes 0")
        # Direction consistency
        sign_pooled = math.copysign(1, point) if not math.isnan(point) else 0
        consist = sum(
            1 for v in per_fy.values()
            if not math.isnan(v[0]) and math.copysign(1, v[0]) == sign_pooled
        )
        if consist < _GATE_FY_CONSIST:
            ok = False
            notes.append(f"only {consist}/{len(_FY_TRAINING)} FYs direction-consistent (<{_GATE_FY_CONSIST})")
        # OOS sign match
        if math.isnan(oos_d) or math.copysign(1, oos_d) != sign_pooled:
            ok = False
            notes.append(f"OOS sign mismatch (oos Δ={oos_d*100:+.2f}pp)")
        # Per-FY CI excludes 0
        fy_ci_pass = sum(
            1 for v in per_fy.values()
            if not math.isnan(v[1]) and not math.isnan(v[2])
            and (v[1] > 0 or v[2] < 0)
        )
        if fy_ci_pass < _GATE_FY_CI_PASS:
            ok = False
            notes.append(f"only {fy_ci_pass}/{len(_FY_TRAINING)} FYs with CI excluding 0 (<{_GATE_FY_CI_PASS})")

        summaries.append(_FeatureSummary(
            feature=col, fire_time_legal=legal,
            cells=cells, pooled_delta=point, ci_lo=ci_lo, ci_hi=ci_hi,
            per_fy_delta=per_fy, oos_delta=oos_d,
            gate_pass=ok, gate_notes=notes,
        ))
    return summaries


# ── 5. ADX × Kumo cross-tab on largest |ΔEV| feature ───────────────────


def _cross_tab(df: pd.DataFrame, feature: str, kind: str) -> str:
    """Return markdown cross-tab of EV by ADX state × Kumo state for top feature."""
    sub = df.dropna(subset=[feature]).copy()
    if kind == "continuous":
        sub["_b"] = _bucket_continuous(sub, feature)
        buckets = ("lo", "hi")
    else:
        sub["_b"] = sub["body_dir_prev"].astype(int).astype(str)
        buckets = ("-1", "1")

    # Load N225 regime snapshots
    with get_session() as s:
        snaps = s.execute(select(N225RegimeSnapshot)).scalars().all()
    snap_map = {snap.date: snap for snap in snaps}

    def _adx_state(d: datetime.date) -> str | None:
        snap = snap_map.get(d)
        if snap is None or snap.adx is None or snap.adx_pos is None or snap.adx_neg is None:
            return None
        if snap.adx < _ADX_CHOPPY:
            return "choppy"
        return "bull" if snap.adx_pos > snap.adx_neg else "bear"

    def _kumo(d: datetime.date) -> int | None:
        snap = snap_map.get(d)
        return snap.kumo_state if snap else None

    sub["adx_state"]  = sub["fire_date"].apply(_adx_state)
    sub["kumo_state"] = sub["fire_date"].apply(_kumo)

    out = [
        f"\n### Cross-tab: {feature} × (ADX state × Kumo state) — Δ EV (top − bottom bucket)\n",
        "| ADX | Kumo above | Kumo inside | Kumo below |",
        "|-----|------------|-------------|------------|",
    ]
    for adx in ["choppy", "bull", "bear"]:
        cells = [f"| **{adx}** |"]
        for k, _label in [(1, "above"), (0, "inside"), (-1, "below")]:
            cell = sub[(sub["adx_state"] == adx) & (sub["kumo_state"] == k)]
            if cell.empty:
                cells.append(" — |")
                continue
            lo = cell[cell["_b"] == buckets[0]]
            hi = cell[cell["_b"] == buckets[1]]
            if lo.empty or hi.empty:
                cells.append(f" n={len(cell)} |")
                continue
            ev_lo = _ev_of_subset(lo)[0]
            ev_hi = _ev_of_subset(hi)[0]
            d = ev_hi - ev_lo
            cells.append(f" {d*100:+.2f}pp (n={len(cell)}) |")
        out.append("".join(cells))
    return "\n".join(out)


# ── 6. Report ─────────────────────────────────────────────────────────


def _format_report(summaries: list[_FeatureSummary],
                   total_n: int, cross: str) -> str:
    lines = [
        f"\n{_SECTION_HEADER}",
        f"\nProbe run: {datetime.date.today()}.  Read-only diagnostic — does any "
        "of (gap_pct, body_dir_prev, body_pct_T, body_frac_T) carry marginal "
        f"signal on the {total_n:,} str_hold fire events?",
        "",
        "**Pre-registered gate** (per /sign-debate 2026-05-17):",
        f"  - pooled |Δ EV (top − bottom bucket)| ≥ {_GATE_POOLED_EV_MIN*100:.1f}pp",
        "  - pooled 95% bootstrap CI excludes 0",
        f"  - per-FY direction consistent in ≥{_GATE_FY_CONSIST} of {len(_FY_TRAINING)} training FYs",
        f"  - FY2025 OOS Δ EV sign matches training-pooled sign",
        f"  - per-FY CI excludes 0 in ≥{_GATE_FY_CI_PASS} of {len(_FY_TRAINING)} training FYs",
        "",
        "`fire_time_legal=False` features use bar-T close (str_hold detector "
        "consumes close[T] at qualify time per src/signs/str_hold.py:79-103); "
        "any production gate built on these would be look-ahead.",
        "",
        "### Per-feature buckets",
        "",
        "| Feature | fire_time_legal | bucket | n | DR | EV |",
        "|---------|:---:|:---:|---:|---:|---:|",
    ]
    for s in summaries:
        for c in s.cells:
            dr_s = f"{c.dr*100:.1f}%" if not math.isnan(c.dr) else "—"
            ev_s = f"{c.ev*100:+.2f}pp" if not math.isnan(c.ev) else "—"
            legal = "✓" if c.fire_time_legal else "✗"
            lines.append(f"| {c.feature} | {legal} | {c.bucket} | {c.n} | {dr_s} | {ev_s} |")
    lines += [
        "",
        "### Pooled Δ EV (top − bottom) + bootstrap CI",
        "",
        "| Feature | legal | pooled ΔEV | 95% CI | FY2025 OOS Δ | FY consistent | FY CI-pass | Gate |",
        "|---------|:---:|---:|---|---:|:---:|:---:|:---:|",
    ]
    for s in summaries:
        sign_pooled = math.copysign(1, s.pooled_delta) if not math.isnan(s.pooled_delta) else 0
        consist = sum(
            1 for v in s.per_fy_delta.values()
            if not math.isnan(v[0]) and math.copysign(1, v[0]) == sign_pooled
        )
        fy_ci = sum(
            1 for v in s.per_fy_delta.values()
            if not math.isnan(v[1]) and not math.isnan(v[2])
            and (v[1] > 0 or v[2] < 0)
        )
        ci = (f"[{s.ci_lo*100:+.2f}, {s.ci_hi*100:+.2f}]pp"
              if not (math.isnan(s.ci_lo) or math.isnan(s.ci_hi)) else "—")
        oos_s = f"{s.oos_delta*100:+.2f}pp" if not math.isnan(s.oos_delta) else "—"
        gate  = "PASS" if s.gate_pass else "FAIL"
        legal = "✓" if s.fire_time_legal else "✗"
        lines.append(
            f"| {s.feature} | {legal} | {s.pooled_delta*100:+.2f}pp | {ci} | "
            f"{oos_s} | {consist}/{len(_FY_TRAINING)} | {fy_ci}/{len(_FY_TRAINING)} | "
            f"**{gate}** |"
        )
        if s.gate_notes:
            lines.append(f"|  |  | gate notes: {'; '.join(s.gate_notes)} |  |  |  |  |  |")

    lines += ["", "### Per-FY Δ EV (top − bottom)", "",
              "| Feature | " + " | ".join(_FY_TRAINING + [_FY_OOS]) + " |",
              "|---------|" + ":---:|" * (len(_FY_TRAINING) + 1)]
    for s in summaries:
        cols: list[str] = []
        for fy in _FY_TRAINING:
            v = s.per_fy_delta.get(fy, (float("nan"),) * 3)
            cols.append(f"{v[0]*100:+.2f}pp" if not math.isnan(v[0]) else "—")
        cols.append(f"{s.oos_delta*100:+.2f}pp" if not math.isnan(s.oos_delta) else "—")
        lines.append(f"| {s.feature} | " + " | ".join(cols) + " |")

    lines.append(cross)
    lines += ["",
        "### Verdict",
        "",
    ]
    any_pass = any(s.gate_pass for s in summaries)
    if any_pass:
        passes = [s.feature for s in summaries if s.gate_pass]
        lines.append(f"**At least one feature cleared the gate** ({', '.join(passes)}). "
                     "Q1 (add candle features) is conditionally on the table; the next "
                     "step is a faithful composite-walk A/B vs the live ZsTpSl exit on the "
                     "fire-time-legal subset only.  Q2 (GA) remains hard-blocked at "
                     "1.75 FY/parameter.")
    else:
        lines.append("**No feature cleared the gate.**  Q1 (add candle/gap features) "
                     "and Q2 (GA-tune the 4 thresholds) are both rejected for this "
                     "iteration.  str_hold detector unchanged.")
    lines.append("")
    return "\n".join(lines)


def _append_to_benchmark(md: str) -> None:
    existing = _BENCH_MD.read_text() if _BENCH_MD.exists() else ""
    # Drop any previous version of this section
    marker = _SECTION_HEADER
    if marker in existing:
        idx = existing.index(marker)
        # find next top-level "## " after idx
        rest = existing[idx + len(marker):]
        next_hdr = rest.find("\n## ")
        if next_hdr < 0:
            existing = existing[:idx].rstrip() + "\n"
        else:
            existing = existing[:idx].rstrip() + "\n" + rest[next_hdr:].lstrip("\n")
    _BENCH_MD.write_text(existing.rstrip() + "\n" + md.lstrip("\n"))
    logger.info("Appended report to {}", _BENCH_MD)


def main() -> None:
    events = _load_events()
    df = _enrich(events)
    if df.empty:
        logger.error("No enriched events — aborting")
        return
    summaries = _analyze(df)

    # Pick largest |pooled ΔEV| feature for cross-tab (regardless of legality)
    top = max(summaries, key=lambda s: abs(s.pooled_delta) if not math.isnan(s.pooled_delta) else 0)
    top_kind = "binary" if top.feature == "body_dir_prev" else "continuous"
    cross = _cross_tab(df, top.feature, top_kind)

    report = _format_report(summaries, len(df), cross)
    print(report)
    _append_to_benchmark(report)


if __name__ == "__main__":
    main()
