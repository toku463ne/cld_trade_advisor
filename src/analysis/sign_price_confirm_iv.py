"""sign_price_confirm_iv — adaptive-K via price confirmation (Phase 1).

For each event of {rev_nlo, rev_nhold, str_lead}:
  - Re-derive the N225 zigzag LOW that motivated the sign:
    ``low_date = n225_dates[fire_idx_N225 − 5]`` (anchor verified at
    src/signs/{rev_nlo,rev_nhold,str_lead}.py line ~117–129, where
    ``low_date = n225_dates[p.bar_index]`` with fire = bar_index + ZZ_SIZE=5).
  - Reference price = stock close at ``low_date − N=3 trading days``
    (stock's own calendar).
  - C(D): stock_close at D trading days post-fire > reference price.
  - K_dyn = first D ∈ [0, 10] satisfying C; event dropped if no D satisfies.
  - Entry at open of ``fire + 1 + K`` (two-bar rule).
  - Peak preserved: ``peak = entry_K0 × (1 + dir × mag)``;
    ``remaining_ret = (peak − entry_K) / entry_K × dir``.

Phase 2 (str_lag, stock-trough anchor) deferred per /sign-debate
counter-proposal 2026-05-13.

Outputs per (sign × corr_mode) cell with n_total ≥ 100 and n_kept ≥ 30:
  - kept-subset mean_return at K=0, K=3, K_dyn (apples-to-apples)
  - full-population counterfactuals where dropped events are treated as
    return=0 or forced-entered at K=15 against original peak

CLI: uv run --env-file devenv python -m src.analysis.sign_price_confirm_iv
"""

from __future__ import annotations

import bisect
import datetime
import math
import sys
from pathlib import Path
from typing import NamedTuple

import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.data.models import Ohlcv1d

_BENCH_MD = Path(__file__).parent / "benchmark.md"

_MULTIYEAR_MIN_RUN_ID = 47
_SIGNS                = ("rev_nlo", "rev_nhold", "str_lead")
_ZZ_SIZE              = 5         # N225 trading bars from LOW to fire (verified)
_N_REF                = 3         # ref = stock_close at low_date − N_REF trading days
_MAX_WAIT             = 10        # max D for adaptive-K search [0, MAX_WAIT]
_FALLBACK_K           = 15        # forced-entry K for dropped-event counterfactual
_CELL_MIN_N           = 100
_KEPT_MIN_N           = 30
_CORR_WINDOW          = 20
_CORR_MIN_PERIODS     = 10
_HIGH_THRESH          = 0.6
_LOW_THRESH           = 0.3
_N225_CODE            = "^N225"


class _Row(NamedTuple):
    sign:       str
    corr_mode:  str          # "high" | "mid" | "low"
    n_total:    int
    n_kept:     int
    drop_rate:  float
    mean_K_dyn: float | None
    # Apples-to-apples (kept subset only)
    k0_kept:    float | None    # = mean trend_magnitude of kept subset
    k3_kept:    float | None
    kdyn_kept:  float | None
    drop_K15:   float | None    # mean ret of dropped events forced at K=15
    # Full-population counterfactuals
    k0_full:    float | None    # = mean trend_magnitude of full population
    kdyn_zero:  float | None    # dropped contribute 0
    kdyn_k15:   float | None    # dropped forced at K=15


def _classify_corr(c: float) -> str:
    if c is None or (isinstance(c, float) and math.isnan(c)):
        return "unknown"
    a = abs(c)
    if a >= _HIGH_THRESH:
        return "high"
    if a <= _LOW_THRESH:
        return "low"
    return "mid"


def _load_bars(code: str, start: datetime.date, end: datetime.date):
    """Return (dates, opens, closes), sorted, deduped on date."""
    start_dt = datetime.datetime.combine(start, datetime.time.min, tzinfo=datetime.timezone.utc)
    end_dt   = datetime.datetime.combine(end,   datetime.time.max, tzinfo=datetime.timezone.utc)
    with get_session() as s:
        rows = s.execute(
            select(Ohlcv1d.ts, Ohlcv1d.open_price, Ohlcv1d.close_price)
            .where(Ohlcv1d.stock_code == code)
            .where(Ohlcv1d.ts >= start_dt)
            .where(Ohlcv1d.ts <= end_dt)
            .order_by(Ohlcv1d.ts)
        ).all()
    dates:  list[datetime.date] = []
    opens:  list[float]         = []
    closes: list[float]         = []
    seen:   set[datetime.date]  = set()
    for ts, op, cl in rows:
        d = ts.date()
        if d in seen:
            continue
        seen.add(d)
        dates.append(d)
        opens.append(float(op))
        closes.append(float(cl))
    return dates, opens, closes


def phase_analyze() -> list[_Row]:
    # 1. Pull events for the 3 in-scope signs from multi-year runs
    with get_session() as s:
        runs = s.execute(
            select(SignBenchmarkRun)
            .where(SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID)
            .where(SignBenchmarkRun.sign_type.in_(_SIGNS))
        ).scalars().all()
    run_map = {r.id: r.sign_type for r in runs}
    if not run_map:
        logger.warning("No runs for {}", _SIGNS)
        return []
    rows: list[dict] = []
    run_ids = list(run_map)
    for i in range(0, len(run_ids), 500):
        chunk = run_ids[i:i + 500]
        with get_session() as s:
            evts = s.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(chunk))
            ).scalars().all()
        for e in evts:
            if e.trend_direction is None or e.trend_magnitude is None:
                continue
            rows.append({
                "sign":  run_map[e.run_id],
                "stock": e.stock_code,
                "fire":  e.fired_at,
                "dir":   int(e.trend_direction),
                "mag":   float(e.trend_magnitude),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    logger.info("Loaded {:,} events for {}", len(df), _SIGNS)

    # 2. Window for bar load (corr warmup + K=15 lookahead)
    min_d = df["fire"].dt.date.min() - datetime.timedelta(days=90)
    max_d = df["fire"].dt.date.max() + datetime.timedelta(days=45)

    # 3. N225 calendar + return series (for corr-mode classification)
    n225_dates, _, n225_closes = _load_bars(_N225_CODE, min_d, max_d)
    if not n225_dates:
        logger.error("No N225 bars in window")
        return []
    n225_idx_map = {d: i for i, d in enumerate(n225_dates)}
    n225_ret = pd.Series(n225_closes, index=n225_dates).pct_change()

    # 4. Per-stock pass
    records: list[dict] = []
    skip_bars = skip_anchor = skip_ref = skip_corr = skip_entry = 0
    for stock, sub in df.groupby("stock"):
        s_dates, s_opens, s_closes = _load_bars(stock, min_d, max_d)
        if len(s_dates) < _CORR_WINDOW + 5:
            skip_bars += len(sub)
            continue
        s_close_ser = pd.Series(s_closes, index=s_dates)
        s_ret = s_close_ser.pct_change()
        common = s_ret.index.intersection(n225_ret.index)
        if len(common) < _CORR_WINDOW + 5:
            skip_bars += len(sub)
            continue
        corr = (
            s_ret.reindex(common)
                 .rolling(_CORR_WINDOW, min_periods=_CORR_MIN_PERIODS)
                 .corr(n225_ret.reindex(common))
        )

        for _, e in sub.iterrows():
            fire_date = e["fire"].date()
            # N225 anchor: re-derive low_date = n225_dates[fire_idx_N225 − 5]
            fire_idx_n = n225_idx_map.get(fire_date)
            if fire_idx_n is None:
                pos = bisect.bisect_right(n225_dates, fire_date) - 1
                if pos < 0:
                    skip_anchor += 1
                    continue
                fire_idx_n = pos
            low_idx_n = fire_idx_n - _ZZ_SIZE
            if low_idx_n < 0:
                skip_anchor += 1
                continue
            low_date = n225_dates[low_idx_n]

            # Reference price: stock close at low_date − N_REF trading days (stock cal)
            s_low_pos = bisect.bisect_right(s_dates, low_date) - 1
            if s_low_pos - _N_REF < 0:
                skip_ref += 1
                continue
            ref_price = s_closes[s_low_pos - _N_REF]
            if ref_price <= 0:
                skip_ref += 1
                continue

            # Stock anchor: position of fire bar
            s_fire_pos = bisect.bisect_right(s_dates, fire_date) - 1
            if s_fire_pos < 0 or s_fire_pos + 1 >= len(s_dates):
                skip_entry += 1
                continue

            corr_val = corr.get(s_dates[s_fire_pos], float("nan"))
            mode = _classify_corr(corr_val)
            if mode == "unknown":
                skip_corr += 1
                continue

            entry_K0 = s_opens[s_fire_pos + 1]
            if entry_K0 <= 0:
                skip_entry += 1
                continue
            peak = entry_K0 * (1.0 + e["dir"] * e["mag"])

            # K_dyn search across D ∈ [0, _MAX_WAIT]
            K_dyn: int | None = None
            for D in range(0, _MAX_WAIT + 1):
                check_pos = s_fire_pos + D
                if check_pos >= len(s_dates):
                    break
                if s_closes[check_pos] > ref_price:
                    K_dyn = D
                    break

            def ret_at(K: int | None) -> float | None:
                if K is None:
                    return None
                idx = s_fire_pos + 1 + K
                if idx >= len(s_dates):
                    return None
                e_at = s_opens[idx]
                if e_at <= 0:
                    return None
                return (peak - e_at) / e_at * e["dir"]

            records.append({
                "sign":      e["sign"],
                "corr_mode": mode,
                "K_dyn":     K_dyn,
                "ret_K0":    ret_at(0),
                "ret_K3":    ret_at(3),
                "ret_Kdyn":  ret_at(K_dyn),
                "ret_Kfb":   ret_at(_FALLBACK_K),
            })

    for label, n in (("no bars", skip_bars), ("no anchor", skip_anchor),
                     ("no ref", skip_ref), ("no corr", skip_corr),
                     ("no entry", skip_entry)):
        if n:
            logger.info("Skipped ({}): {:,}", label, n)
    ev_df = pd.DataFrame(records)
    ev_df = ev_df[ev_df["ret_K0"].notna()]
    logger.info("Events with valid K=0 baseline: {:,}", len(ev_df))

    # 5. Aggregate per (sign × corr_mode)
    out: list[_Row] = []
    for (sign, mode), grp in ev_df.groupby(["sign", "corr_mode"]):
        n_total = len(grp)
        if n_total < _CELL_MIN_N:
            continue
        # Apples-to-apples: keep events where K_dyn fires AND K_dyn entry AND K=3 entry are valid
        kept = grp[grp["K_dyn"].notna()
                   & grp["ret_Kdyn"].notna()
                   & grp["ret_K3"].notna()]
        n_kept = len(kept)
        if n_kept < _KEPT_MIN_N:
            continue
        dropped = grp[grp["K_dyn"].isna()]
        n_dropped = len(dropped)

        k0_kept   = float(kept["ret_K0"].mean())
        k3_kept   = float(kept["ret_K3"].mean())
        kdyn_kept = float(kept["ret_Kdyn"].mean())
        k0_full   = float(grp["ret_K0"].mean())

        # Dropped events forced at K=15 (full preserve-target counterfactual)
        drop_K15_vals = dropped["ret_Kfb"].dropna()
        drop_K15 = float(drop_K15_vals.mean()) if len(drop_K15_vals) else None

        # Full-pop K_dyn counterfactuals:
        #   (a) dropped contribute 0
        #   (b) dropped forced at K=15 (use ret_Kfb; events with no K=15 bar are excluded)
        kdyn_sum_kept = float(kept["ret_Kdyn"].sum())
        kdyn_zero = (kdyn_sum_kept + 0.0 * n_dropped) / n_total
        n_drop_with_fb = len(drop_K15_vals)
        if n_drop_with_fb + n_kept > 0:
            kdyn_k15 = (kdyn_sum_kept + float(drop_K15_vals.sum())) / (n_kept + n_drop_with_fb)
        else:
            kdyn_k15 = None

        mean_K = float(kept["K_dyn"].mean())

        out.append(_Row(
            sign=sign, corr_mode=mode,
            n_total=n_total, n_kept=n_kept,
            drop_rate=n_dropped / n_total,
            mean_K_dyn=mean_K,
            k0_kept=k0_kept, k3_kept=k3_kept, kdyn_kept=kdyn_kept,
            drop_K15=drop_K15,
            k0_full=k0_full, kdyn_zero=kdyn_zero, kdyn_k15=kdyn_k15,
        ))

    out.sort(key=lambda r: (r.sign, ["high", "mid", "low"].index(r.corr_mode)))
    return out


def _fmt(v: float | None, spec: str = "+.4f") -> str:
    return format(v, spec) if v is not None else "—"


def phase_report(rows: list[_Row]) -> None:
    if not rows:
        logger.warning("No rows to report")
        return
    today = datetime.date.today().isoformat()
    md: list[str] = [
        "", "---", "",
        "## Price-Confirmation Adaptive-K IV (Phase 1, FY2018–FY2024)",
        "",
        f"Generated: {today}  ",
        f"Signs: {', '.join(_SIGNS)} (uniform N225-zigzag-low anchor).  ",
        f"Anchor (verified at src/signs/{{rev_nlo,rev_nhold,str_lead}}.py): "
        f"`low_date = n225_dates[fire_idx_N225 − {_ZZ_SIZE}]`.  ",
        f"Confirmation C(D): `stock_close(D) > stock_close(low_date − {_N_REF} trading days)`.  ",
        f"K_dyn = first D ∈ [0, {_MAX_WAIT}] satisfying C; events with no such D are *dropped*.  ",
        f"Entry fill at open of bar `fire + 1 + K` (two-bar rule); "
        f"`peak = entry_K0 × (1 + dir × mag)`; "
        f"`remaining_ret = (peak − entry_K) / entry_K × dir`.  ",
        f"corr_mode tagged via {_CORR_WINDOW}-bar returns-corr to ^N225 "
        f"(high ≥ {_HIGH_THRESH}, low ≤ {_LOW_THRESH}, mid in between).  ",
        f"Cells gated by n_total ≥ {_CELL_MIN_N} and n_kept ≥ {_KEPT_MIN_N}.  ",
        "",
        "### Apples-to-apples mean_return (kept subset only)",
        "",
        "| Sign | corr | n_total | n_kept | drop% | mean K_dyn | K=0 kept | K=3 kept | K_dyn kept | Δ(K_dyn − K=3) | dropped@K15 |",
        "|------|------|--------:|-------:|------:|-----------:|---------:|---------:|-----------:|---------------:|------------:|",
    ]
    cur_sign: str | None = None
    for r in rows:
        sign_cell = f"**{r.sign}**" if r.sign != cur_sign else ""
        cur_sign = r.sign
        delta = (r.kdyn_kept - r.k3_kept) if (r.k3_kept is not None and r.kdyn_kept is not None) else None
        md.append(
            f"| {sign_cell:<12} | {r.corr_mode:<4} | {r.n_total:>5} | {r.n_kept:>5} | "
            f"{r.drop_rate*100:>5.1f}% | {_fmt(r.mean_K_dyn, '>5.2f'):>5} | "
            f"{_fmt(r.k0_kept):>8} | {_fmt(r.k3_kept):>8} | {_fmt(r.kdyn_kept):>10} | "
            f"{_fmt(delta):>10} | {_fmt(r.drop_K15):>10} |"
        )
    md += [
        "",
        "### Full-population counterfactuals (n_total denominator)",
        "",
        "Detects whether K_dyn lift is selectivity (drops are losers) or "
        "survivorship (drops would have been winners).",
        "",
        "| Sign | corr | n_total | K=0 full pop | K_dyn (drop=0) | K_dyn (drop=K15) |",
        "|------|------|--------:|-------------:|---------------:|-----------------:|",
    ]
    cur_sign = None
    for r in rows:
        sign_cell = f"**{r.sign}**" if r.sign != cur_sign else ""
        cur_sign = r.sign
        md.append(
            f"| {sign_cell:<12} | {r.corr_mode:<4} | {r.n_total:>5} | "
            f"{_fmt(r.k0_full):>8} | {_fmt(r.kdyn_zero):>8} | {_fmt(r.kdyn_k15):>8} |"
        )
    md.append("")
    with open(_BENCH_MD, "a", encoding="utf-8") as f:
        f.write("\n".join(md))
    logger.info("Appended Price-Confirmation IV section to {}", _BENCH_MD)


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    rows = phase_analyze()
    phase_report(rows)


if __name__ == "__main__":
    main()
