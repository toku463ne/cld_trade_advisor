"""wait_iv_early_cut_probe — does a K=3-close early-exit gate beat naked ZsTpSl?

Faithful composite walk of live exit: entry at open[fire+1] (two-bar fill);
for bars 1..3 fire on whichever triggers first (ZsTpSl TP, ZsTpSl SL, or K=3
close gate exiting at open[fire+5]); if no exit by bar 3, continue ZsTpSl to
max_bars=40. Long-only, matching regime_sign_backtest's EntryCandidate.

Accept gate on div_gap × mid × Q4 (PRIMARY): Δmean_r ≥ +0.30pp AND frac_cut ∈
[5%, 25%] AND MFE_03 < |MAE_03| in cut cohort (mechanism a) AND mean_r|not_cut
≥ baseline − 0.10pp (mechanism b). Sign-flip falsifier: if rev_nlo × low × Q4
also lifts Δmean_r ≥ +0.20pp at the same θ → REJECT (generic filter).

CLI: uv run --env-file devenv python -m src.analysis.wait_iv_early_cut_probe
"""

from __future__ import annotations

import bisect
import csv
import datetime
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.sign_wait_iv import _classify_corr
from src.data.db import get_session
from src.data.models import Ohlcv1d
from src.exit.zs_tp_sl import ZsTpSl
from src.indicators.zigzag import detect_peaks
from src.portfolio.crud import _ZS_LOOKBACK, _ZZ_SIZE, _ZZ_MIDDLE

_BENCH_MD     = Path(__file__).parent / "benchmark.md"
_CSV_DIR      = Path(__file__).parent.parent.parent / "data" / "analysis" / "wait_iv_early_cut_probe"

_MULTIYEAR_MIN_RUN_ID = 47
_SIGNS                = ("div_gap", "rev_nlo")
_CORR_WINDOW          = 20
_CORR_MIN_PERIODS     = 10
_N225_CODE            = "^N225"
_LOOKBACK_DAYS        = 400
_K_GATE               = 3                       # observe close at position bar 3
_MAX_BARS             = 40                      # ZsTpSl default time-stop
_TP_MULT              = 2.0
_SL_MULT              = 2.0
_ALPHA                = 0.3
_FALLBACK_PCT         = 0.05
_MIN_LEGS             = 3
_THETAS               = (-0.01, -0.02, -0.03)

# Cells to report. (sign, corr_mode, quartile_label, role).
_TARGETS = [
    ("div_gap", "mid",  "Q4", "PRIMARY"),
    ("div_gap", "high", "Q4", "confirm"),
    ("div_gap", "mid",  "Q3", "confirm"),
    ("div_gap", "high", "Q3", "confirm"),
    ("rev_nlo", "low",  "Q4", "sign-flip"),
]


class _EventRow(NamedTuple):
    sign:           str
    stock:          str
    fire_date:      datetime.date
    score:          float
    corr_mode:      str
    r_k3:           float | None
    mfe_03:         float | None
    mae_03:         float | None
    baseline_r:     float
    cut_flags:      tuple[bool, ...]    # one per θ
    policy_rs:      tuple[float, ...]   # one per θ


def _load_bars(code: str, start: datetime.date, end: datetime.date):
    start_dt = datetime.datetime.combine(start, datetime.time.min, tzinfo=datetime.timezone.utc)
    end_dt   = datetime.datetime.combine(end,   datetime.time.max, tzinfo=datetime.timezone.utc)
    with get_session() as s:
        rows = s.execute(
            select(Ohlcv1d.ts, Ohlcv1d.open_price, Ohlcv1d.high_price,
                   Ohlcv1d.low_price, Ohlcv1d.close_price)
            .where(Ohlcv1d.stock_code == code)
            .where(Ohlcv1d.ts >= start_dt)
            .where(Ohlcv1d.ts <= end_dt)
            .order_by(Ohlcv1d.ts)
        ).all()
    dates, opens, highs, lows, closes = [], [], [], [], []
    seen: set[datetime.date] = set()
    for ts, op, hi, lo, cl in rows:
        d = ts.date()
        if d in seen:
            continue
        seen.add(d)
        dates.append(d)
        opens.append(float(op))
        highs.append(float(hi))
        lows.append(float(lo))
        closes.append(float(cl))
    return dates, opens, highs, lows, closes


def _zs_legs_at(bars, n225_dates_set: set, idx_upto: int) -> tuple[float, ...]:
    dates, _, highs, lows, _ = bars
    pairs = [(dates[i], highs[i], lows[i]) for i in range(0, idx_upto + 1)
             if dates[i] in n225_dates_set]
    if len(pairs) < _ZZ_SIZE * 2 + 1:
        return ()
    hs = [p[1] for p in pairs]
    ls = [p[2] for p in pairs]
    peaks = sorted(detect_peaks(hs, ls, size=_ZZ_SIZE, middle_size=_ZZ_MIDDLE),
                   key=lambda p: p.bar_index)
    legs: list[float] = []
    prev_price: float | None = None
    for p in peaks:
        if prev_price is not None:
            legs.append(abs(p.price - prev_price))
        prev_price = p.price
    return tuple(legs[-_ZS_LOOKBACK:])


def _walk_zs_tp_sl(
    highs: list[float], lows: list[float], closes: list[float], opens: list[float],
    entry_idx: int, tp_price: float, sl_price: float, start_offset: int, max_bars: int,
) -> tuple[float, str, int]:
    """Walk ZsTpSl from position bar start_offset onward. Return (exit_price, reason, bars_held).

    bars_held is the position-bar index at which exit fires (0=entry day).
    Long-only: TP on high ≥ tp_price, SL on low ≤ sl_price.
    """
    n = len(highs)
    for offset in range(start_offset, max_bars + 1):
        pos = entry_idx + offset
        if pos >= n:
            # End of data — exit at last close
            last = n - 1
            return closes[last], "end_of_data", last - entry_idx
        hi = highs[pos]
        lo = lows[pos]
        if hi >= tp_price:
            return tp_price, "tp", offset
        if lo <= sl_price:
            return sl_price, "sl", offset
    # Max bars reached
    pos = min(entry_idx + max_bars, n - 1)
    return closes[pos], "time", pos - entry_idx


def _composite_walk(
    bars, entry_idx: int, tp_price: float, sl_price: float,
) -> tuple[float | None, float | None, float | None, float, tuple[bool, ...], tuple[float, ...]]:
    """Return (r_k3, mfe_03, mae_03, baseline_r, cut_flags[θ], policy_rs[θ])."""
    _, opens, highs, lows, closes = bars
    n = len(opens)
    entry_price = opens[entry_idx]

    zs_hit_offset: int | None = None
    zs_hit_price: float = 0.0
    mfe = -math.inf
    mae = math.inf
    for offset in range(1, _K_GATE + 1):
        pos = entry_idx + offset
        if pos >= n:
            break
        hi, lo = highs[pos], lows[pos]
        mfe = max(mfe, (hi - entry_price) / entry_price)
        mae = min(mae, (lo - entry_price) / entry_price)
        if zs_hit_offset is None:
            if hi >= tp_price:
                zs_hit_offset, zs_hit_price = offset, tp_price
            elif lo <= sl_price:
                zs_hit_offset, zs_hit_price = offset, sl_price

    gate_pos = entry_idx + _K_GATE
    r_k3 = (closes[gate_pos] - entry_price) / entry_price if gate_pos < n else None
    mfe_03 = mfe if mfe != -math.inf else None
    mae_03 = mae if mae !=  math.inf else None

    if zs_hit_offset is not None:
        baseline_r = (zs_hit_price - entry_price) / entry_price
    else:
        exit_price, _, _ = _walk_zs_tp_sl(
            highs, lows, closes, opens, entry_idx,
            tp_price, sl_price, _K_GATE + 1, _MAX_BARS,
        )
        baseline_r = (exit_price - entry_price) / entry_price

    cut_exit_pos = entry_idx + _K_GATE + 1
    cut_exit_price = opens[cut_exit_pos] if cut_exit_pos < n else None
    cut_flags: list[bool] = []
    policy_rs: list[float] = []
    for theta in _THETAS:
        if zs_hit_offset is not None or r_k3 is None or r_k3 > theta:
            cut_flags.append(False)
            policy_rs.append(baseline_r)
            continue
        policy_r = r_k3 if cut_exit_price is None else (cut_exit_price - entry_price) / entry_price
        cut_flags.append(True)
        policy_rs.append(policy_r)

    return r_k3, mfe_03, mae_03, baseline_r, tuple(cut_flags), tuple(policy_rs)


def phase_walk_all() -> list[_EventRow]:
    # 1. Load events
    with get_session() as s:
        runs = s.execute(
            select(SignBenchmarkRun)
            .where(SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID)
            .where(SignBenchmarkRun.sign_type.in_(list(_SIGNS)))
        ).scalars().all()
    run_map = {r.id: r.sign_type for r in runs}
    if not run_map:
        logger.error("No multi-year runs for signs {}", _SIGNS)
        return []
    run_ids = list(run_map)

    events: list[dict] = []
    for i in range(0, len(run_ids), 500):
        with get_session() as s:
            chunk = s.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(run_ids[i:i + 500]))
            ).scalars().all()
        for e in chunk:
            if e.trend_direction is None or e.sign_score is None:
                continue
            events.append({
                "sign":      run_map[e.run_id],
                "stock":     e.stock_code,
                "fire_date": e.fired_at.date(),
                "score":     float(e.sign_score),
            })
    if not events:
        return []
    df = pd.DataFrame(events)
    logger.info("Loaded {:,} events across {}", len(df), _SIGNS)

    # 2. Date range
    min_d = df["fire_date"].min() - datetime.timedelta(days=_LOOKBACK_DAYS + 30)
    max_d = df["fire_date"].max() + datetime.timedelta(days=_MAX_BARS + 30)

    # 3. N225 for corr + zigzag dates
    n225_dates, _, _, _, n225_cl = _load_bars(_N225_CODE, min_d, max_d)
    n225_close = pd.Series(n225_cl, index=n225_dates).sort_index()
    n225_ret   = n225_close.pct_change()
    n225_dates_set = set(n225_dates)

    rule = ZsTpSl(tp_mult=_TP_MULT, sl_mult=_SL_MULT, alpha=_ALPHA,
                  min_legs=_MIN_LEGS, fallback_pct=_FALLBACK_PCT, max_bars=_MAX_BARS)

    out: list[_EventRow] = []
    skipped: dict[str, int] = defaultdict(int)

    for stock, sub in df.groupby("stock"):
        bars = _load_bars(stock, min_d, max_d)
        s_dates, s_opens, s_highs, s_lows, s_closes = bars
        if len(s_dates) < _CORR_WINDOW + 5:
            skipped["no_bars"] += len(sub)
            continue
        s_close_ser = pd.Series(s_closes, index=s_dates)
        s_ret = s_close_ser.pct_change()
        common = s_ret.index.intersection(n225_ret.index)
        if len(common) < _CORR_WINDOW + 5:
            skipped["no_corr"] += len(sub)
            continue
        corr = (
            s_ret.reindex(common)
                 .rolling(_CORR_WINDOW, min_periods=_CORR_MIN_PERIODS)
                 .corr(n225_ret.reindex(common))
        )

        for _, e in sub.iterrows():
            fd: datetime.date = e["fire_date"]
            fire_idx = bisect.bisect_right(s_dates, fd) - 1
            if fire_idx < 0 or fire_idx + 1 >= len(s_dates):
                skipped["no_entry"] += 1
                continue
            entry_idx = fire_idx + 1
            entry_price = s_opens[entry_idx]
            if entry_price <= 0:
                skipped["bad_entry"] += 1
                continue
            mode = _classify_corr(corr.get(s_dates[fire_idx], float("nan")))
            if mode == "unknown":
                skipped["no_corr_tag"] += 1
                continue
            legs = _zs_legs_at(bars, n225_dates_set, fire_idx)
            tp_price, sl_price = rule.preview_levels(entry_price, legs)
            r_k3, mfe, mae, base_r, cuts, pols = _composite_walk(
                bars, entry_idx, tp_price, sl_price,
            )
            out.append(_EventRow(
                sign=e["sign"], stock=stock, fire_date=fd, score=e["score"],
                corr_mode=mode, r_k3=r_k3, mfe_03=mfe, mae_03=mae,
                baseline_r=base_r, cut_flags=cuts, policy_rs=pols,
            ))

    if skipped:
        logger.info("Skipped: {}", dict(skipped))
    logger.info("Walked {:,} events", len(out))
    return out


def phase_aggregate(rows: list[_EventRow]) -> dict:
    if not rows:
        return {}
    df = pd.DataFrame([r._asdict() for r in rows])
    # Quartile within (sign, corr_mode)
    df["q"] = None
    for (sign, mode), grp in df.groupby(["sign", "corr_mode"]):
        try:
            qs = pd.qcut(grp["score"], 4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop")
            df.loc[grp.index, "q"] = qs.astype(str)
        except ValueError:
            continue

    out: dict = {}
    for sign, mode, ql, role in _TARGETS:
        sub = df[(df["sign"] == sign) & (df["corr_mode"] == mode) & (df["q"] == ql)]
        n = len(sub)
        cell_key = (sign, mode, ql)
        if n == 0:
            out[cell_key] = {"role": role, "n": 0, "stats": []}
            continue
        baseline_mean = float(sub["baseline_r"].mean())
        per_theta: list[dict] = []
        for i, theta in enumerate(_THETAS):
            cut_mask = sub["cut_flags"].apply(lambda t: t[i])
            policy_r = sub["policy_rs"].apply(lambda t: t[i])
            n_cut    = int(cut_mask.sum())
            frac_cut = n_cut / n if n > 0 else 0.0
            policy_mean = float(policy_r.mean()) if n > 0 else 0.0
            delta_mean = policy_mean - baseline_mean
            if n_cut > 0:
                cut_sub      = sub[cut_mask]
                cut_baseline = float(cut_sub["baseline_r"].mean())
                cut_policy   = float(policy_r[cut_mask].mean())
                mfe_cut      = float(cut_sub["mfe_03"].mean(skipna=True))
                mae_cut      = float(cut_sub["mae_03"].mean(skipna=True))
            else:
                cut_baseline = cut_policy = mfe_cut = mae_cut = float("nan")
            not_cut = sub[~cut_mask]
            mean_not_cut = float(not_cut["baseline_r"].mean()) if len(not_cut) > 0 else float("nan")
            per_theta.append({
                "theta":         theta,
                "n_cut":         n_cut,
                "frac_cut":      frac_cut,
                "baseline_mean": baseline_mean,
                "policy_mean":   policy_mean,
                "delta_mean":    delta_mean,
                "cut_baseline":  cut_baseline,
                "cut_policy":    cut_policy,
                "mfe_cut":       mfe_cut,
                "mae_cut":       mae_cut,
                "mean_not_cut":  mean_not_cut,
            })
        out[cell_key] = {"role": role, "n": n, "stats": per_theta}
    return out, df


def _verdict_primary(stats: list[dict], baseline_mean_not_cut: float) -> tuple[str, list[str]]:
    """Apply the 4-condition accept gate on the primary cell. Return (verdict, reasons)."""
    if not stats:
        return "INSUFFICIENT", ["empty cell"]
    best: tuple[str, list[str]] = ("REJECT", ["no θ cleared all four"])
    for s in stats:
        notes: list[str] = []
        ok_delta   = s["delta_mean"]     >= 0.0030
        ok_frac    = 0.05 <= s["frac_cut"] <= 0.25
        # Mechanism (a) check: MFE in cut cohort should be less than |MAE| — i.e.,
        # cut events were already losing more than they had recovered.
        ok_mech_a  = (not math.isnan(s["mfe_cut"])) and \
                     (not math.isnan(s["mae_cut"])) and \
                     (s["mfe_cut"] < abs(s["mae_cut"]))
        ok_mech_b  = s["mean_not_cut"] >= s["baseline_mean"] - 0.0010
        notes.append(f"Δmean_r={s['delta_mean']*100:+.2f}pp ({'✓' if ok_delta else '✗'})")
        notes.append(f"frac_cut={s['frac_cut']*100:.1f}% ({'✓' if ok_frac else '✗'})")
        notes.append(f"MFE<|MAE| ({'✓' if ok_mech_a else '✗'})")
        notes.append(f"not_cut≥baseline−0.10pp ({'✓' if ok_mech_b else '✗'})")
        if ok_delta and ok_frac and ok_mech_a and ok_mech_b:
            return "ACCEPT", [f"θ={s['theta']*100:.0f}%: " + ", ".join(notes)]
        best = (best[0], [f"θ={s['theta']*100:.0f}%: " + ", ".join(notes)] + best[1])
    return best


def _verdict_sign_flip(stats: list[dict]) -> tuple[str, str]:
    """If rev_nlo×low×Q4 also lifts Δmean_r ≥ +0.20pp at any θ, gate is generic → REJECT."""
    if not stats:
        return "PASS", "empty cell"
    for s in stats:
        if s["delta_mean"] >= 0.0020:
            return "FAIL", f"θ={s['theta']*100:.0f}%: Δmean_r={s['delta_mean']*100:+.2f}pp (generic filter)"
    return "PASS", "no θ ≥ +0.20pp on rev_nlo×low×Q4"


def phase_report(agg: dict, ev_df: pd.DataFrame) -> None:
    if not agg:
        logger.warning("No aggregate")
        return
    _CSV_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()

    # Per-event CSV
    csv_path = _CSV_DIR / f"events_{today}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sign", "stock", "fire_date", "score", "corr_mode", "q",
                    "r_k3", "mfe_03", "mae_03", "baseline_r"]
                   + [f"cut_θ{t*100:+.0f}" for t in _THETAS]
                   + [f"policy_r_θ{t*100:+.0f}" for t in _THETAS])
        for _, e in ev_df.iterrows():
            w.writerow([
                e["sign"], e["stock"], e["fire_date"], f"{e['score']:.6f}",
                e["corr_mode"], e.get("q") or "",
                "" if e["r_k3"] is None else f"{e['r_k3']:.6f}",
                "" if e["mfe_03"] is None else f"{e['mfe_03']:.6f}",
                "" if e["mae_03"] is None else f"{e['mae_03']:.6f}",
                f"{e['baseline_r']:.6f}",
                *[int(c) for c in e["cut_flags"]],
                *[f"{r:.6f}" for r in e["policy_rs"]],
            ])
    logger.info("Wrote per-event CSV to {}", csv_path)

    # Markdown summary
    md = [
        "", "---", "",
        "## Wait-IV Early-Cut Probe (FY2018–FY2024)",
        "",
        f"Generated: {today}  ",
        f"Faithful composite walk of `ZsTpSl(tp={_TP_MULT}, sl={_SL_MULT}, α={_ALPHA})` "
        f"plus a K={_K_GATE}-close gate that exits at open of bar {_K_GATE + 1} (two-bar fill) "
        f"if signed_return at K={_K_GATE} close ≤ θ.  ",
        "Long-only — matches `regime_sign_backtest` which builds `EntryCandidate` "
        "without a direction field.  ",
        "ZsTpSl TP/SL is checked on bars 1..3 each bar; whichever fires first "
        "(TP, SL, or gate) determines the exit. baseline = no gate; policy = with gate.  ",
        "",
        "### Per-cell × θ table",
        "",
        "| sign | corr | Q | n | θ | frac_cut | baseline_r | policy_r | Δmean_r | "
        "MFE\\|cut | MAE\\|cut | not_cut_r | role |",
        "|------|------|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|------|",
    ]
    for (sign, mode, ql), info in agg.items():
        n = info["n"]
        role = info["role"]
        if n == 0:
            md.append(f"| {sign} | {mode} | {ql} | 0 | — | — | — | — | — | — | — | — | {role} |")
            continue
        for s in info["stats"]:
            md.append(
                f"| {sign} | {mode} | {ql} | {n} | "
                f"{s['theta']*100:+.0f}% | {s['frac_cut']*100:.1f}% | "
                f"{s['baseline_mean']*100:+.2f}pp | {s['policy_mean']*100:+.2f}pp | "
                f"**{s['delta_mean']*100:+.2f}pp** | "
                f"{s['mfe_cut']*100:+.2f}pp | {s['mae_cut']*100:+.2f}pp | "
                f"{s['mean_not_cut']*100:+.2f}pp | {role} |"
            )
    md.append("")

    # Verdict on PRIMARY cell
    primary_key = ("div_gap", "mid", "Q4")
    primary = agg.get(primary_key, {"stats": []})
    primary_verdict, primary_reasons = _verdict_primary(primary.get("stats", []), 0.0)

    # Sign-flip falsifier on rev_nlo × low × Q4
    flip_key = ("rev_nlo", "low", "Q4")
    flip_info = agg.get(flip_key, {"stats": []})
    flip_verdict, flip_note = _verdict_sign_flip(flip_info.get("stats", []))

    md += [
        "### Accept gate — div_gap × mid × Q4 (PRIMARY)",
        "",
        f"Required (all four): Δmean_r ≥ +0.30pp; frac_cut ∈ [5%, 25%]; "
        f"MFE_03 < |MAE_03| in cut cohort; mean_r|not_cut ≥ baseline − 0.10pp.  ",
        "",
    ]
    for r in primary_reasons:
        md.append(f"- {r}")
    md.append("")
    md.append(f"**Primary verdict: {primary_verdict}**  ")
    md.append("")
    md += [
        "### Sign-flip falsifier — rev_nlo × low × Q4",
        "",
        "If rev_nlo × low × Q4 also lifts Δmean_r ≥ +0.20pp at any θ, the gate "
        "is generic noise reduction (not a div_gap cohort identifier) → overall REJECT.  ",
        "",
        f"- {flip_note}",
        "",
        f"**Sign-flip falsifier: {flip_verdict}**  ",
        "",
    ]
    if primary_verdict == "ACCEPT" and flip_verdict == "PASS":
        overall = "PROCEED to env-gated A/B (new `CompositeExitRule(ZsTpSl + KCloseGate)` exit rule)"
    elif flip_verdict == "FAIL":
        overall = "REJECT — generic noise filter, not cohort-specific"
    else:
        overall = "REJECT — accept gate not met on primary cell"
    md.append(f"### Overall: **{overall}**")
    md.append("")

    with _BENCH_MD.open("a", encoding="utf-8") as f:
        f.write("\n".join(md))
    logger.info("Appended Wait-IV Early-Cut Probe section to {}", _BENCH_MD)


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    rows = phase_walk_all()
    if not rows:
        logger.error("No event rows")
        return
    agg, ev_df = phase_aggregate(rows)
    phase_report(agg, ev_df)


if __name__ == "__main__":
    main()
