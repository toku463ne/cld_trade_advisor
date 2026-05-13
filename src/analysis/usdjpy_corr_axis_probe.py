"""usdjpy_corr_axis_probe — does USDJPY-corr at fire-time add directional lift?

Probe (Critic-tightened spec, 2026-05-14):

- Backfill: USDJPY=X 1d bars 2020-05+ (run separately via `src.data.collect`).
- Events: SignBenchmarkEvent rows from multi-year runs (id ≥ 47), restricted to
  fire_date ≥ 2020-06-01 (post-20-bar warmup).
- For each event: compute 20-bar rolling returns-correlation of stock vs
  (a) ^N225 (no shift — both close at Tokyo 15:00 JST), and
  (b) USDJPY=X **shifted +1 trading day forward** (Yahoo USDJPY Close lands at
      NY 17:00 ET ≈ 06:00 JST D+1, so usable correlation at fire on D must use
      USDJPY through D-1). Causal-leak fix per Critic § 5.7.
- Bucket by **empirical tertiles** of each corr across the event population
  (avoids the lopsided N225-borrowed 0.6/0.3 cuts; Critic § 5.3).
- 3×3 = 9 cells per sign. For each (sign × cell): n, DR, mag_flw, mag_rev, EV.
- **Leave-one-out within-N225-bucket pool**: pool = same-N225-bucket events in
  *different* USDJPY-buckets. ΔDR / ΔEV = cell vs LOO pool.
- **Max-over-cells shuffle falsifier**: 1,000 perms shuffling each event's
  corr_usdjpy value across the event set (preserving date and stock columns).
  p-value = fraction of perms where max ΔDR over all (sign × cell) cells with
  n ≥ 100 exceeds the observed max ΔDR.

Accept gate (all four required):
- ≥1 (sign × cell) with n ≥ 100 AND ΔDR ≥ +3.0pp AND ΔEV ≥ 0 AND p < 0.05.

Reject (falsifier triggered):
- No (sign × cell) clears n ≥ 100 AND ΔDR ≥ +1.0pp, OR
- Shuffle p ≥ 0.05 on the observed best.

CLI: uv run --env-file devenv python -m src.analysis.usdjpy_corr_axis_probe
"""

from __future__ import annotations

import datetime
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.data.models import Ohlcv1d

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_OUT_DIR  = Path(__file__).parent.parent.parent / "data" / "analysis" / "usdjpy_corr_axis"

_MULTIYEAR_MIN_RUN_ID = 47
_FIRE_MIN_DATE        = datetime.date(2020, 6, 1)
_N225_CODE            = "^N225"
_USDJPY_CODE          = "USDJPY=X"
_CORR_WINDOW          = 20
_CORR_MIN_PERIODS     = 10
_CELL_MIN_N           = 100
_N_SHUFFLES           = 1000
_RNG_SEED             = 20260514

_DELTA_DR_ACCEPT = 0.030   # +3pp
_DELTA_DR_REJECT = 0.010   # +1pp
_SHUFFLE_P_GATE  = 0.05


@dataclass
class _CellStat:
    sign:        str
    n_bucket:    str
    u_bucket:    str
    n:           int
    dr:          float
    mag_flw:     float
    mag_rev:     float
    ev:          float
    pool_n:      int
    pool_dr:     float
    pool_ev:     float
    delta_dr:    float
    delta_ev:    float


# ──────────────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────────────

def _load_bars(code: str) -> pd.Series:
    """Load 1d close series for a single ticker, indexed by date."""
    with get_session() as s:
        rows = s.execute(
            select(Ohlcv1d.ts, Ohlcv1d.close_price)
            .where(Ohlcv1d.stock_code == code)
            .order_by(Ohlcv1d.ts)
        ).all()
    if not rows:
        raise RuntimeError(f"No bars for {code}")
    dates, closes = [], []
    seen: set[datetime.date] = set()
    for ts, cl in rows:
        d = ts.date()
        if d in seen:
            continue
        seen.add(d)
        dates.append(d)
        closes.append(float(cl))
    return pd.Series(closes, index=pd.Index(dates, name="date")).sort_index()


def _load_events() -> pd.DataFrame:
    with get_session() as s:
        runs = s.execute(
            select(SignBenchmarkRun).where(SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID)
        ).scalars().all()
    run_map = {r.id: r.sign_type for r in runs}
    if not run_map:
        raise RuntimeError("No multi-year runs found")
    run_ids = list(run_map)
    rows: list[dict] = []
    for i in range(0, len(run_ids), 500):
        chunk = run_ids[i:i + 500]
        with get_session() as s:
            evts = s.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(chunk))
            ).scalars().all()
        for e in evts:
            if e.trend_direction is None or e.trend_magnitude is None:
                continue
            d = e.fired_at.date()
            if d < _FIRE_MIN_DATE:
                continue
            rows.append({
                "sign":      run_map[e.run_id],
                "stock":     e.stock_code,
                "fire_date": d,
                "dir":       int(e.trend_direction),
                "mag":       float(e.trend_magnitude),
            })
    df = pd.DataFrame(rows)
    logger.info("Loaded {:,} events with fire_date ≥ {}", len(df), _FIRE_MIN_DATE)
    return df


# ──────────────────────────────────────────────────────────────────────────
# Correlation computation
# ──────────────────────────────────────────────────────────────────────────

def _compute_event_corrs(
    events: pd.DataFrame,
    n225_ret: pd.Series,
    usdjpy_ret_shifted: pd.Series,
) -> pd.DataFrame:
    """For each event, compute corr_n225 and corr_usdjpy at fire_date."""
    out_n225: list[float | None] = []
    out_usdjpy: list[float | None] = []

    for stock, sub in events.groupby("stock"):
        stock_close = _load_bars_safe(stock)
        if stock_close is None or len(stock_close) < _CORR_WINDOW + 5:
            out_n225.extend([None] * len(sub))
            out_usdjpy.extend([None] * len(sub))
            continue
        s_ret = stock_close.pct_change()

        # Align indices for each comparison
        common_n = s_ret.index.intersection(n225_ret.index)
        if len(common_n) < _CORR_WINDOW + 5:
            corr_n = pd.Series(dtype=float)
        else:
            corr_n = (s_ret.reindex(common_n)
                      .rolling(_CORR_WINDOW, min_periods=_CORR_MIN_PERIODS)
                      .corr(n225_ret.reindex(common_n)))

        common_u = s_ret.index.intersection(usdjpy_ret_shifted.index)
        if len(common_u) < _CORR_WINDOW + 5:
            corr_u = pd.Series(dtype=float)
        else:
            corr_u = (s_ret.reindex(common_u)
                      .rolling(_CORR_WINDOW, min_periods=_CORR_MIN_PERIODS)
                      .corr(usdjpy_ret_shifted.reindex(common_u)))

        for _, e in sub.iterrows():
            fd = e["fire_date"]
            cn = corr_n.get(fd) if not corr_n.empty else None
            cu = corr_u.get(fd) if not corr_u.empty else None
            out_n225.append(float(cn) if cn is not None and not (isinstance(cn, float) and math.isnan(cn)) else None)
            out_usdjpy.append(float(cu) if cu is not None and not (isinstance(cu, float) and math.isnan(cu)) else None)

    # events groupby preserves order; assemble by aligning to grouped order
    ordered = events.sort_values("stock", kind="mergesort").reset_index(drop=True)
    ordered["corr_n225"]   = out_n225
    ordered["corr_usdjpy"] = out_usdjpy
    return ordered


_BAR_CACHE: dict[str, pd.Series | None] = {}


def _load_bars_safe(code: str) -> pd.Series | None:
    if code in _BAR_CACHE:
        return _BAR_CACHE[code]
    try:
        s = _load_bars(code)
    except RuntimeError:
        _BAR_CACHE[code] = None
        return None
    _BAR_CACHE[code] = s
    return s


# ──────────────────────────────────────────────────────────────────────────
# Bucketing + per-cell stats
# ──────────────────────────────────────────────────────────────────────────

def _empirical_tertile(s: pd.Series, value: float) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "unknown"
    q1, q2 = np.percentile(s.dropna().to_numpy(), [33.333, 66.667])
    if value <= q1:
        return "L"
    if value <= q2:
        return "M"
    return "H"


def _add_buckets(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["corr_n225", "corr_usdjpy"]).copy()
    nq1, nq2 = np.percentile(df["corr_n225"].to_numpy(),   [33.333, 66.667])
    uq1, uq2 = np.percentile(df["corr_usdjpy"].to_numpy(), [33.333, 66.667])
    df["n_bucket"] = pd.cut(df["corr_n225"],   bins=[-1.01, nq1, nq2, 1.01], labels=["L", "M", "H"]).astype(str)
    df["u_bucket"] = pd.cut(df["corr_usdjpy"], bins=[-1.01, uq1, uq2, 1.01], labels=["L", "M", "H"]).astype(str)
    logger.info("Tertile cutpoints: N225 [{:+.3f}, {:+.3f}]  USDJPY [{:+.3f}, {:+.3f}]",
                nq1, nq2, uq1, uq2)
    return df


def _cell_stats_with_loo(df: pd.DataFrame) -> list[_CellStat]:
    """For each (sign, n_bucket, u_bucket) cell: stats + leave-one-out pool."""
    out: list[_CellStat] = []
    for (sign, nb, ub), sub in df.groupby(["sign", "n_bucket", "u_bucket"]):
        n = len(sub)
        cell_dr = float((sub["dir"] == 1).mean()) if n else float("nan")
        flw = sub[sub["dir"] == 1]["mag"]
        rev = sub[sub["dir"] == -1]["mag"]
        mag_flw = float(flw.mean()) if len(flw) else 0.0
        mag_rev = float(rev.mean()) if len(rev) else 0.0
        cell_ev = cell_dr * mag_flw - (1 - cell_dr) * mag_rev

        # Leave-one-out within same N225 bucket, different USDJPY bucket
        pool = df[(df["sign"] == sign) & (df["n_bucket"] == nb) & (df["u_bucket"] != ub)]
        pn = len(pool)
        if pn == 0:
            pool_dr = pool_ev = float("nan")
        else:
            pool_dr = float((pool["dir"] == 1).mean())
            p_flw = pool[pool["dir"] == 1]["mag"]
            p_rev = pool[pool["dir"] == -1]["mag"]
            p_mag_flw = float(p_flw.mean()) if len(p_flw) else 0.0
            p_mag_rev = float(p_rev.mean()) if len(p_rev) else 0.0
            pool_ev = pool_dr * p_mag_flw - (1 - pool_dr) * p_mag_rev

        out.append(_CellStat(
            sign=sign, n_bucket=nb, u_bucket=ub, n=n,
            dr=cell_dr, mag_flw=mag_flw, mag_rev=mag_rev, ev=cell_ev,
            pool_n=pn, pool_dr=pool_dr, pool_ev=pool_ev,
            delta_dr=cell_dr - pool_dr if pn else float("nan"),
            delta_ev=cell_ev - pool_ev if pn else float("nan"),
        ))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Shuffle falsifier (max over cells with n ≥ _CELL_MIN_N)
# ──────────────────────────────────────────────────────────────────────────

def _max_observed_delta_dr(cells: list[_CellStat]) -> float:
    vals = [c.delta_dr for c in cells if c.n >= _CELL_MIN_N and not math.isnan(c.delta_dr)]
    return float(max(vals)) if vals else float("-inf")


def _shuffle_once(df: pd.DataFrame, rng: np.random.Generator) -> float:
    """One permutation: shuffle u_bucket across all rows independently of sign/n_bucket.
    Re-derive max ΔDR over cells with n ≥ _CELL_MIN_N."""
    df2 = df.copy()
    perm = rng.permutation(len(df2))
    df2["u_bucket"] = df2["u_bucket"].to_numpy()[perm]
    max_dd = float("-inf")
    for (sign, nb, ub), sub in df2.groupby(["sign", "n_bucket", "u_bucket"]):
        n = len(sub)
        if n < _CELL_MIN_N:
            continue
        cell_dr = float((sub["dir"] == 1).mean())
        pool = df2[(df2["sign"] == sign) & (df2["n_bucket"] == nb) & (df2["u_bucket"] != ub)]
        if len(pool) == 0:
            continue
        pool_dr = float((pool["dir"] == 1).mean())
        max_dd = max(max_dd, cell_dr - pool_dr)
    return max_dd


def _shuffle_falsifier(df: pd.DataFrame, observed_max: float) -> tuple[int, float, list[float]]:
    rng = np.random.default_rng(_RNG_SEED)
    perm_max: list[float] = []
    n_ge = 0
    for i in range(_N_SHUFFLES):
        m = _shuffle_once(df, rng)
        perm_max.append(m)
        if m >= observed_max:
            n_ge += 1
        if (i + 1) % 100 == 0:
            logger.info("  shuffle {}/{}  current p={:.3f}", i + 1, _N_SHUFFLES, n_ge / (i + 1))
    return n_ge, n_ge / _N_SHUFFLES, perm_max


# ──────────────────────────────────────────────────────────────────────────
# Decision + report
# ──────────────────────────────────────────────────────────────────────────

def _decide(cells: list[_CellStat], p_value: float) -> tuple[str, list[str], _CellStat | None]:
    notes: list[str] = []
    qualifying = [c for c in cells
                  if c.n >= _CELL_MIN_N
                  and not math.isnan(c.delta_dr)
                  and c.delta_dr >= _DELTA_DR_ACCEPT
                  and c.delta_ev >= 0]
    above_reject = [c for c in cells
                    if c.n >= _CELL_MIN_N
                    and not math.isnan(c.delta_dr)
                    and c.delta_dr >= _DELTA_DR_REJECT]
    best = max(cells, key=lambda c: (c.delta_dr if c.n >= _CELL_MIN_N else float("-inf")))

    notes.append(f"best cell: {best.sign} N={best.n_bucket} U={best.u_bucket} "
                 f"n={best.n} ΔDR={best.delta_dr*100:+.2f}pp ΔEV={best.delta_ev*100:+.2f}pp")
    notes.append(f"shuffle p-value (max-over-cells ≥ observed): {p_value:.4f}")
    notes.append(f"qualifying cells (n≥{_CELL_MIN_N}, ΔDR≥{_DELTA_DR_ACCEPT*100:.0f}pp, "
                 f"ΔEV≥0): {len(qualifying)}")

    if not above_reject:
        return "REJECT (falsifier: no cell ΔDR ≥ +1pp at n≥100)", notes, best
    if p_value >= _SHUFFLE_P_GATE:
        return f"REJECT (falsifier: shuffle p={p_value:.3f} ≥ {_SHUFFLE_P_GATE})", notes, best
    if qualifying:
        return "ACCEPT (proceed to prototype `corr_mode_tuple` extension)", notes, qualifying[0]
    return ("INSUFFICIENT (some cells ≥ +1pp but none cleared all four conditions)",
            notes, best)


def _format_cells_table(cells: list[_CellStat]) -> list[str]:
    out = [
        "| sign | N | U | n | DR | EV | pool_n | pool_DR | pool_EV | ΔDR | ΔEV |",
        "|------|---|---|--:|---:|---:|-------:|--------:|--------:|----:|----:|",
    ]
    for c in sorted(cells, key=lambda x: (x.sign, x.n_bucket, x.u_bucket)):
        out.append(
            f"| {c.sign} | {c.n_bucket} | {c.u_bucket} | {c.n} | "
            f"{c.dr*100:.1f}% | {c.ev*100:+.2f}pp | "
            f"{c.pool_n} | "
            f"{c.pool_dr*100:.1f}% | {c.pool_ev*100:+.2f}pp | "
            f"**{c.delta_dr*100:+.2f}pp** | {c.delta_ev*100:+.2f}pp |"
        )
    return out


def _write_report(cells: list[_CellStat], p_value: float, perm_max: list[float],
                  obs_max: float, verdict: str, notes: list[str]) -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    path = _OUT_DIR / f"probe_{today}.md"

    cells_qual = [c for c in cells if c.n >= _CELL_MIN_N]
    md: list[str] = [
        f"# USDJPY Corr-Axis Probe (FY2021–FY2025 window)",
        "",
        f"Generated: {today}  ",
        f"Source: SignBenchmarkEvent (run_id ≥ {_MULTIYEAR_MIN_RUN_ID}), "
        f"fire_date ≥ {_FIRE_MIN_DATE}  ",
        f"USDJPY=X causal shift: +1 trading day (Yahoo Close lands at NY 17:00 ET ≈ JST 06:00 D+1).  ",
        f"Tertile cuts: empirical per-corr quantiles over event population.  ",
        f"Pool: leave-one-out same-N225-bucket, different-USDJPY-bucket.  ",
        f"Shuffle: max-over-(sign × cell) at n ≥ {_CELL_MIN_N}, {_N_SHUFFLES} perms.  ",
        "",
        "**Window caveat**: dev DB ^N225 starts 2020-05-11, so this is a 4-FY window "
        "(FY2021-FY2025) not the nominal 7-FY benchmark. Project-wide truncation, not specific to this probe.",
        "",
        f"## Cells with n ≥ {_CELL_MIN_N}",
        "",
    ]
    md += _format_cells_table(cells_qual)
    md += [
        "",
        "## Shuffle falsifier",
        "",
        f"Observed max ΔDR (over cells with n ≥ {_CELL_MIN_N}): **{obs_max*100:+.2f}pp**  ",
        f"Permutation max ΔDR distribution: min={min(perm_max)*100:+.2f}pp, "
        f"median={np.median(perm_max)*100:+.2f}pp, "
        f"95th pct={np.percentile(perm_max, 95)*100:+.2f}pp, "
        f"max={max(perm_max)*100:+.2f}pp  ",
        f"p-value = fraction of perms with max ΔDR ≥ observed: **{p_value:.4f}** "
        f"({_N_SHUFFLES} perms)",
        "",
        f"## Verdict: **{verdict}**",
        "",
    ]
    for n in notes:
        md.append(f"- {n}")
    md.append("")
    path.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote report to {}", path)

    # Also append a summary block to src/analysis/benchmark.md
    with _BENCH_MD.open("a", encoding="utf-8") as f:
        f.write("\n".join([
            "", "---", "",
            f"## USDJPY Corr-Axis Probe (FY2021–FY2025) — {today}",
            "",
            f"Probe-only. Full table at `{path.relative_to(Path(__file__).parent.parent.parent)}`.  ",
            f"Verdict: **{verdict}**  ",
            f"Best cell ΔDR: {obs_max*100:+.2f}pp (shuffle p={p_value:.4f}, {_N_SHUFFLES} perms)",
            "",
        ]))
    logger.info("Appended summary to {}", _BENCH_MD)


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    n225_close   = _load_bars(_N225_CODE)
    usdjpy_close = _load_bars(_USDJPY_CODE)
    n225_ret           = n225_close.pct_change()
    usdjpy_ret         = usdjpy_close.pct_change()
    usdjpy_ret_shifted = usdjpy_ret.shift(1)   # causal: row D uses USDJPY ≤ D-1
    logger.info("N225 bars {} – {} ({} rows)", n225_close.index.min(), n225_close.index.max(), len(n225_close))
    logger.info("USDJPY bars {} – {} ({} rows, shifted +1)",
                usdjpy_close.index.min(), usdjpy_close.index.max(), len(usdjpy_close))

    events = _load_events()
    events = _compute_event_corrs(events, n225_ret, usdjpy_ret_shifted)
    logger.info("Events with both corrs: {:,} of {:,}",
                int(events[["corr_n225", "corr_usdjpy"]].dropna().shape[0]), len(events))

    buckets = _add_buckets(events)
    logger.info("Bucketed events: {:,}", len(buckets))

    cells = _cell_stats_with_loo(buckets)
    obs_max = _max_observed_delta_dr(cells)
    logger.info("Observed max ΔDR over n≥{} cells: {:+.4f}", _CELL_MIN_N, obs_max)
    logger.info("Running shuffle falsifier ({} perms)…", _N_SHUFFLES)
    _, p_value, perm_max = _shuffle_falsifier(buckets, obs_max)

    verdict, notes, _best = _decide(cells, p_value)
    _write_report(cells, p_value, perm_max, obs_max, verdict, notes)

    print("\n=== USDJPY CORR-AXIS PROBE ===")
    print(verdict)
    for n in notes:
        print("  -", n)


if __name__ == "__main__":
    main()
