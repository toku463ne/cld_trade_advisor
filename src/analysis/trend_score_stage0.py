"""trend_score Stage 0 — measurement-only framework probe.

A new conference-strategy direction proposed by operator on 2026-05-19:
instead of counting binary sign co-fires (current ConfluenceSignStrategy),
score each (stock, day) by a continuous "trend quality" metric built from
five structural features, then measure per-sign EV per score decile.

This script is the **measurement-only Stage 0** — no strategy, no A/B.
Goal: per-sign decile-EV master table.  Two cohorts reported (train +
holdout, walk-forward) but no weights tuned, no decision rule applied.

Pre-registered design (locked with operator before running)
============================================================

**5 features per (stock, bar):** each designed so higher value = more
bullish.  Each percentile-ranked over the stock's own past 250-bar
history (rolling, no look-ahead) into [0, 100].

  1. f_sma       = (close - sma_50) / sma_50
  2. f_peak      = (latest_peak.price - prev_peak.price) / prev_peak.price
                   — signed magnitude of most recent confirmed zigzag leg
                   that finished completing on/before this bar (look-ahead
                   safe via confirmation-bar offset)
  3. f_kumo      = (close - kumo_midline) / kumo_midline
                   where kumo_midline = (senkou_a + senkou_b) / 2,
                   displaced 26 bars to the visible cloud at the bar
  4. f_chiko     = (close - close[-26]) / close[-26]
  5. f_long_max  = (close - max(close, 252-bar)) / max(close, 252-bar)
                   ≤ 0 always; closer to 0 = closer to long-term high

**trend_score = mean of 5 percentile ranks → in [0, 100]**

  - 50 = "everything average" baseline
  - >70 = strong bullish multi-feature alignment
  - <30 = strong bearish multi-feature alignment

**Decile binning** of trend_score across all (stock, date) observations
(pooled cross-section).

**Cohorts**:
  - Train  : FY2019-FY2023 events (used to *observe* the relationship)
  - Holdout: FY2024-FY2025 events (untouched, future-validate)

**Output**: docs/analysis/trend_score_stage0.md
  - Per-sign decile-EV table (DR, signed_mean by decile) for each cohort
  - "Score-responsive signs" summary (signs with monotone deciles + strong
    decile-10 minus decile-1 lift)

Run:
    uv run --env-file devenv python -m src.analysis.trend_score_stage0

Note on look-ahead:
  - Zigzag peaks use confirmation_bar = peak.bar_index + size, so a peak
    only enters the feature stream once its confirmation bar has passed.
  - SMA / kumo / chiko / long_max are causal by construction.
  - Percentile rank uses pandas.rolling(250).rank() — strictly past window.
"""
from __future__ import annotations

import datetime
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.exit_benchmark import _load_rep_codes
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.indicators.ichimoku import calc_ichimoku
from src.indicators.zigzag import detect_peaks
from src.simulator.cache import DataCache

_UNIVERSE_SET   = "classified2024"
_GRAN           = "1d"
_SPAN_START     = datetime.date(2017, 4, 1)
_SPAN_END       = datetime.date(2026, 4, 1)

_SMA_N          = 50
_PEAK_SIZE      = 5
_PEAK_MID       = 2
_LONG_MAX_N     = 252
_CHIKO_LAG      = 26
_KUMO_DISP      = 26
_ROLLING_WIN    = 250

_TRAIN_FYS      = {"FY2019", "FY2020", "FY2021", "FY2022", "FY2023"}
_HOLDOUT_FYS    = {"FY2024", "FY2025"}
_N_DECILES      = 10

_DOC_PATH = Path("docs/analysis/trend_score_stage0.md")


# ── 1. Feature computation per stock ──────────────────────────────────


def _compute_features(cache: DataCache) -> pd.DataFrame | None:
    """Return DataFrame indexed by date with 5 raw feature columns + trend_score.

    Returns None if cache doesn't have enough history.
    """
    if len(cache.bars) < _LONG_MAX_N + _ROLLING_WIN:
        return None

    dates  = [b.dt.date() for b in cache.bars]
    closes = np.array([b.close for b in cache.bars], dtype=float)
    highs  = np.array([b.high  for b in cache.bars], dtype=float)
    lows   = np.array([b.low   for b in cache.bars], dtype=float)
    n = len(closes)

    # ── 1a. SMA50 ─────────────────────────────────────────────────────
    s_close = pd.Series(closes)
    sma50 = s_close.rolling(_SMA_N).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        f_sma = (closes - sma50) / sma50
    f_sma[~np.isfinite(f_sma)] = np.nan

    # ── 1b. Zigzag peak momentum (look-ahead-safe) ────────────────────
    peaks = detect_peaks(highs.tolist(), lows.tolist(),
                         size=_PEAK_SIZE, middle_size=_PEAK_MID)
    # Confirmed peaks only (direction ±2).  Confirmation bar = bar_index + size.
    confirmed = sorted(
        [(p.bar_index + _PEAK_SIZE, p.bar_index, p.price)
         for p in peaks if abs(p.direction) == 2],
        key=lambda t: t[0],
    )
    f_peak = np.full(n, np.nan, dtype=float)
    for i in range(n):
        # Take all peaks confirmed at or before bar i, last 2 of those
        prior = [(_ci, bi, pr) for (_ci, bi, pr) in confirmed if _ci <= i]
        if len(prior) >= 2:
            _, _, prev_p  = prior[-2]
            _, _, last_p  = prior[-1]
            if prev_p > 0:
                f_peak[i] = (last_p - prev_p) / prev_p

    # ── 1c. Kumo midline at bar (visible cloud) ───────────────────────
    ichi = calc_ichimoku(highs.tolist(), lows.tolist(), closes.tolist())
    senkou_a = np.array(ichi["senkou_a"], dtype=float)
    senkou_b = np.array(ichi["senkou_b"], dtype=float)
    kumo_mid_visible = np.full(n, np.nan, dtype=float)
    for i in range(_KUMO_DISP, n):
        a = senkou_a[i - _KUMO_DISP]
        b = senkou_b[i - _KUMO_DISP]
        if not (math.isnan(a) or math.isnan(b)):
            kumo_mid_visible[i] = (a + b) / 2
    with np.errstate(divide="ignore", invalid="ignore"):
        f_kumo = (closes - kumo_mid_visible) / kumo_mid_visible
    f_kumo[~np.isfinite(f_kumo)] = np.nan

    # ── 1d. Chiko (close vs close[-26]) ───────────────────────────────
    f_chiko = np.full(n, np.nan, dtype=float)
    for i in range(_CHIKO_LAG, n):
        ref = closes[i - _CHIKO_LAG]
        if ref > 0:
            f_chiko[i] = (closes[i] - ref) / ref

    # ── 1e. Long-term max (close vs 252-bar high) ─────────────────────
    max_252 = pd.Series(closes).rolling(_LONG_MAX_N).max().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        f_long_max = (closes - max_252) / max_252
    f_long_max[~np.isfinite(f_long_max)] = np.nan

    df = pd.DataFrame({
        "date":       dates,
        "f_sma":      f_sma,
        "f_peak":     f_peak,
        "f_kumo":     f_kumo,
        "f_chiko":    f_chiko,
        "f_long_max": f_long_max,
    })

    # ── 1f. Rolling-250 percentile rank per feature ────────────────────
    # pct=True gives values in [0, 1]; multiply by 100.
    for col in ("f_sma", "f_peak", "f_kumo", "f_chiko", "f_long_max"):
        df[f"{col}_pct"] = (
            df[col].rolling(_ROLLING_WIN, min_periods=int(_ROLLING_WIN * 0.5))
                   .rank(pct=True) * 100.0
        )

    pct_cols = [f"{c}_pct" for c in
                ("f_sma", "f_peak", "f_kumo", "f_chiko", "f_long_max")]
    df["trend_score"] = df[pct_cols].mean(axis=1, skipna=False)
    return df[["date", "trend_score"] + pct_cols]


# ── 2. Build the (stock, date) → trend_score lookup ───────────────────


def _build_score_table(universe: list[str]
                      ) -> dict[tuple[str, datetime.date], float]:
    span_start_dt = datetime.datetime.combine(_SPAN_START, datetime.time.min,
                                              tzinfo=datetime.timezone.utc)
    span_end_dt   = datetime.datetime.combine(_SPAN_END,   datetime.time.max,
                                              tzinfo=datetime.timezone.utc)
    out: dict[tuple[str, datetime.date], float] = {}
    skipped_short = 0
    skipped_load  = 0
    with get_session() as s:
        for i, code in enumerate(universe):
            cache = DataCache(code, _GRAN)
            try:
                cache.load(s, span_start_dt, span_end_dt)
            except Exception as exc:
                logger.warning("  {}: load failed — {}", code, exc)
                skipped_load += 1
                continue
            if not cache.bars:
                skipped_load += 1
                continue
            df = _compute_features(cache)
            if df is None:
                skipped_short += 1
                continue
            for _, row in df.iterrows():
                if not math.isnan(row["trend_score"]):
                    out[(code, row["date"])] = float(row["trend_score"])
            if (i + 1) % 25 == 0:
                logger.info("  {}/{} stocks processed ({} obs so far)",
                            i + 1, len(universe), len(out))
    logger.info("trend_score table: {} (stock, date) observations from "
                "{} stocks (skipped {} short, {} load-failures)",
                len(out), len(universe) - skipped_short - skipped_load,
                skipped_short, skipped_load)
    return out


# ── 3. Load all sign events & tag ─────────────────────────────────────


@dataclass
class _Tagged:
    sign:        str
    fy:          str
    stock:       str
    date:        datetime.date
    dir:         int          # +1 HIGH first, -1 LOW first
    mag:         float        # |trend_magnitude|
    trend_score: float

    @property
    def signed_mag(self) -> float:
        return self.dir * self.mag


def _fy_label(d: datetime.date) -> str:
    if d.month >= 4:
        return f"FY{d.year}"
    return f"FY{d.year - 1}"


def _load_all_events() -> list[tuple[str, str, datetime.datetime, int, float]]:
    with get_session() as s:
        runs = s.execute(select(SignBenchmarkRun)).scalars().all()
    run_map = {r.id: r.sign_type for r in runs}
    logger.info("Found {} runs across {} sign types",
                len(run_map), len(set(run_map.values())))
    rows: list[tuple] = []
    batch = 500
    ids = list(run_map)
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        with get_session() as s:
            evts = s.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(chunk))
            ).scalars().all()
        for e in evts:
            if e.trend_direction is None or e.trend_magnitude is None:
                continue
            rows.append((run_map[e.run_id], e.stock_code, e.fired_at,
                         int(e.trend_direction), float(e.trend_magnitude)))
    logger.info("Loaded {} events with outcome", len(rows))
    return rows


def _tag(events: list[tuple],
         score_tbl: dict[tuple[str, datetime.date], float]) -> list[_Tagged]:
    out: list[_Tagged] = []
    n_skipped = 0
    for sign, code, fired_at, d, m in events:
        fd = fired_at.date()
        ts = score_tbl.get((code, fd))
        if ts is None:
            n_skipped += 1
            continue
        out.append(_Tagged(
            sign=sign, fy=_fy_label(fd), stock=code, date=fd,
            dir=d, mag=m, trend_score=ts,
        ))
    logger.info("Tagged {} events; skipped {} (no trend_score)",
                len(out), n_skipped)
    return out


# ── 4. Decile binning + per-decile metrics ────────────────────────────


def _decile_table_for_sign(
    rows: list[_Tagged],
    deciles_cutoffs: list[float],
) -> list[dict]:
    """Return one row per decile with n / DR / signed_mean."""
    out: list[dict] = []
    for k in range(_N_DECILES):
        lo = deciles_cutoffs[k]
        hi = deciles_cutoffs[k + 1]
        if k == _N_DECILES - 1:
            bucket = [r for r in rows if lo <= r.trend_score <= hi]
        else:
            bucket = [r for r in rows if lo <= r.trend_score <  hi]
        n = len(bucket)
        if n == 0:
            out.append(dict(decile=k + 1, lo=lo, hi=hi, n=0,
                            dr=None, signed_mean=None))
            continue
        n_flw = sum(1 for r in bucket if r.dir == +1)
        signed = [r.signed_mag for r in bucket]
        out.append(dict(
            decile=k + 1, lo=lo, hi=hi, n=n,
            dr=n_flw / n,
            signed_mean=statistics.mean(signed),
        ))
    return out


def _is_monotone_increasing(values: list[float | None],
                            tol: float = 0.0) -> bool:
    """Loose monotone: each value ≥ prior - tol."""
    last: float | None = None
    for v in values:
        if v is None:
            continue
        if last is not None and v < last - tol:
            return False
        last = v
    return last is not None


def _decile_lift(deciles: list[dict], key: str) -> float | None:
    """Returns deciles[9][key] - deciles[0][key], or None if either missing."""
    d10 = deciles[-1].get(key)
    d1  = deciles[0].get(key)
    if d10 is None or d1 is None:
        return None
    return d10 - d1


# ── 5. Report rendering ──────────────────────────────────────────────


def _fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{v*100:+.2f}%"


def _fmt_dr(v: float | None) -> str:
    return "—" if v is None else f"{v*100:.1f}%"


def _render_decile_block(label: str, deciles: list[dict]) -> list[str]:
    lines = [f"#### {label}", ""]
    lines.append("| dec | range | n | DR | signed_mean |")
    lines.append("|----:|-------|---:|---:|---:|")
    for d in deciles:
        rng = f"{d['lo']:.0f}–{d['hi']:.0f}"
        lines.append(
            f"| {d['decile']} | {rng} | {d['n']:>5} | "
            f"{_fmt_dr(d['dr'])} | {_fmt_pct(d['signed_mean'])} |"
        )
    return lines


def _render_summary_row(sign: str,
                        train_dec: list[dict],
                        hold_dec:  list[dict]) -> str:
    def lift_str(dec: list[dict], key: str) -> str:
        v = _decile_lift(dec, key)
        if v is None:
            return "—"
        return f"{v*100:+.2f}pp" if key == "signed_mean" else f"{v*100:+.1f}pp"
    train_dr_lift  = lift_str(train_dec,  "dr")
    train_sm_lift  = lift_str(train_dec,  "signed_mean")
    hold_dr_lift   = lift_str(hold_dec,   "dr")
    hold_sm_lift   = lift_str(hold_dec,   "signed_mean")
    train_drs = [d["dr"] for d in train_dec]
    hold_drs  = [d["dr"] for d in hold_dec]
    mono_train = "✓" if _is_monotone_increasing(train_drs, tol=0.05) else "·"
    mono_hold  = "✓" if _is_monotone_increasing(hold_drs,  tol=0.05) else "·"
    n_train = sum(d["n"] for d in train_dec)
    n_hold  = sum(d["n"] for d in hold_dec)
    return (f"| {sign} | {n_train} | {n_hold} | "
            f"{train_dr_lift} | {train_sm_lift} | {mono_train} | "
            f"{hold_dr_lift} | {hold_sm_lift} | {mono_hold} |")


def _format_report(tagged: list[_Tagged]) -> str:
    # Global decile cutoffs (cross-section, across all (stock, date)
    # observations in the score table — NOT just events).  This makes
    # decile interpretation consistent and not biased by sign-fire frequency.
    # For simplicity we use the empirical quantiles of trend_score across
    # the tagged event population (since uniform percentile-rank construction
    # of trend_score makes this a stable, near-uniform distribution).
    all_scores = sorted(r.trend_score for r in tagged)
    cuts = [
        all_scores[int(k / _N_DECILES * len(all_scores))]
        for k in range(_N_DECILES)
    ] + [all_scores[-1]]

    by_sign: dict[str, list[_Tagged]] = defaultdict(list)
    for r in tagged:
        by_sign[r.sign].append(r)
    signs = sorted(by_sign)

    lines = [
        "# trend_score Stage 0 — per-sign decile EV master table",
        "",
        f"Probe run: {datetime.date.today()}.  Stage 0 measurement of "
        "operator-proposed continuous-trend-score framework (2026-05-19).",
        "",
        "**Score construction**: 5 features × per-stock 250-bar rolling "
        "percentile rank, averaged → trend_score ∈ [0, 100].",
        "",
        f"Decile cutoffs (across {len(all_scores)} pooled events):",
        "",
        "| dec | low | high |",
        "|----:|----:|----:|",
    ]
    for k in range(_N_DECILES):
        lines.append(f"| {k+1} | {cuts[k]:.1f} | {cuts[k+1]:.1f} |")

    lines += [
        "",
        "## Score-responsive signs — summary",
        "",
        f"For each sign: D10 minus D1 DR + signed_mean lift; "
        "monotone (✓ if DRs increase across deciles within 5pp tolerance).  "
        "Train = {} events; Holdout = {} events.".format(
            sum(1 for r in tagged if r.fy in _TRAIN_FYS),
            sum(1 for r in tagged if r.fy in _HOLDOUT_FYS)),
        "",
        "| sign | n_train | n_hold | Tr Δ DR | Tr Δ sm | mono | Hl Δ DR | Hl Δ sm | mono |",
        "|------|---:|---:|---:|---:|:---:|---:|---:|:---:|",
    ]
    for sign in signs:
        rows = by_sign[sign]
        train = [r for r in rows if r.fy in _TRAIN_FYS]
        hold  = [r for r in rows if r.fy in _HOLDOUT_FYS]
        if not train or not hold:
            continue
        tdec = _decile_table_for_sign(train, cuts)
        hdec = _decile_table_for_sign(hold,  cuts)
        lines.append(_render_summary_row(sign, tdec, hdec))

    lines += ["", "## Per-sign decile detail", ""]
    for sign in signs:
        rows = by_sign[sign]
        train = [r for r in rows if r.fy in _TRAIN_FYS]
        hold  = [r for r in rows if r.fy in _HOLDOUT_FYS]
        if not train and not hold:
            continue
        lines.append(f"### {sign}")
        lines.append("")
        if train:
            lines += _render_decile_block("Train (FY2019-FY2023)",
                                          _decile_table_for_sign(train, cuts))
            lines.append("")
        if hold:
            lines += _render_decile_block("Holdout (FY2024-FY2025)",
                                          _decile_table_for_sign(hold, cuts))
            lines.append("")

    return "\n".join(lines)


def main() -> None:
    universe  = _load_rep_codes(_UNIVERSE_SET)
    logger.info("Loaded {} universe stocks", len(universe))
    score_tbl = _build_score_table(universe)
    events    = _load_all_events()
    tagged    = _tag(events, score_tbl)
    report    = _format_report(tagged)
    _DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DOC_PATH.write_text(report + "\n")
    logger.info("Wrote {}", _DOC_PATH)
    # Also print the summary section to stdout
    head = report.split("## Per-sign decile detail", 1)[0]
    print(head)


if __name__ == "__main__":
    main()
