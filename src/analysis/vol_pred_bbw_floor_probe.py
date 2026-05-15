"""vol_pred_bbw_floor_probe — does BB-width compression predict a forward volume spike?

/sign-debate Round 1 verdict: Accept (probe-first) with Critic's top-3 tightenings:
  F1: pooled train lift ≥ +5pp
  F2: FY2025 holdout lift ≥ +3pp
  F3: date-stratified cross-stock shuffle (preserves daily fire-count) →
      excess_lift = observed − shuffle_mean ≥ +3pp AND shuffle p < 0.05
      (closes the cross-sectional macro-regime confounder)
  F4: calendar-anniversary leverage check → reject if >40% of Y_T=1 fires
      have a same-stock same-ISO-week Y=1 in the prior fiscal year
      (closes the earnings/ex-div calendar-predictability confounder)

Fire condition at bar T:  bbw_pct60[T] ≤ 0.10
Target:                   Y_T = max(vol[T+1..T+5]) ≥ 2.0 × vol_ma20[T]

Probe-only — no production sign, no DB mutation. Output goes to
  data/analysis/vol_pred_bbw_floor/report.md
"""

from __future__ import annotations

import datetime
import random
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.data.db import get_session
from src.data.models import Ohlcv1d

_OUT_DIR = Path(__file__).parent.parent.parent / "data" / "analysis" / "vol_pred_bbw_floor"
_BB_WINDOW   = 20
_BB_STD      = 2.0
_PCT_WINDOW  = 60
_PCT_THRESH  = 0.10
_VOL_WINDOW  = 20
_K_FORWARD   = 5
_M_VOL       = 2.0
_FIRE_MIN_DATE = datetime.date(2019, 4, 1)
_HOLDOUT_FY_START = datetime.date(2025, 4, 1)
_HOLDOUT_FY_END   = datetime.date(2026, 3, 31)
_N_SHUFFLES  = 1000
_RNG_SEED    = 20260515
_CAL_LEVERAGE_REJECT = 0.40


def _load_ohlcv(code: str, session) -> pd.DataFrame:
    rows = session.execute(
        select(Ohlcv1d.ts, Ohlcv1d.close_price, Ohlcv1d.volume)
        .where(Ohlcv1d.stock_code == code)
        .order_by(Ohlcv1d.ts)
    ).all()
    if not rows:
        return pd.DataFrame()
    idx = pd.Index([r.ts.date() for r in rows], name="date")
    return pd.DataFrame({
        "close":  [float(r.close_price) for r in rows],
        "volume": [float(r.volume)      for r in rows],
    }, index=idx).sort_index()


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    sma20 = df["close"].rolling(_BB_WINDOW, min_periods=_BB_WINDOW).mean()
    std20 = df["close"].rolling(_BB_WINDOW, min_periods=_BB_WINDOW).std()
    upper = sma20 + _BB_STD * std20
    lower = sma20 - _BB_STD * std20
    bbw = (upper - lower) / sma20
    bbw_pct60 = bbw.rolling(_PCT_WINDOW, min_periods=_PCT_WINDOW).rank(pct=True)
    vol_ma20 = df["volume"].rolling(_VOL_WINDOW, min_periods=_VOL_WINDOW).mean()
    fwd_max_vol = (df["volume"].shift(-1)
                   .rolling(_K_FORWARD, min_periods=_K_FORWARD).max()
                   .shift(-(_K_FORWARD - 1)))
    Y = (fwd_max_vol >= _M_VOL * vol_ma20).astype(float)
    eligible = vol_ma20.notna() & fwd_max_vol.notna() & bbw_pct60.notna()
    return pd.DataFrame({
        "bbw_pct60": bbw_pct60,
        "vol_ma20":  vol_ma20,
        "Y":         Y,
        "eligible":  eligible,
        "fired":     (bbw_pct60 <= _PCT_THRESH) & eligible,
    }, index=df.index)


def _fy_label(d: datetime.date) -> str:
    fy_start = d.year if d.month >= 4 else d.year - 1
    return f"FY{fy_start}"


def _compute_lift(events: pd.DataFrame) -> tuple[float, float, int, int]:
    """Return (gated_rate, baseline_rate, n_fires, n_eligible). All input rows are eligible by construction."""
    if events.empty:
        return float("nan"), float("nan"), 0, 0
    baseline = float(events["Y"].mean())
    fires = events[events["fired"]]
    gated = float(fires["Y"].mean()) if len(fires) else float("nan")
    return gated, baseline, int(len(fires)), int(len(events))


def main() -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    rng = random.Random(_RNG_SEED)
    np_rng = np.random.default_rng(_RNG_SEED)

    with get_session() as session:
        from src.data.models import Stock
        from sqlalchemy import select as _sel
        codes = session.execute(
            _sel(Stock.code).where(Stock.is_active.is_(True)).order_by(Stock.code)
        ).scalars().all()
        codes = [c for c in codes if not c.startswith("^") and "=" not in c]
        logger.info("Universe: {} active stocks", len(codes))

        # ── Build per-stock features and an event-long table ──────────────────
        rows: list[dict] = []
        n_evaluated = 0
        for i, code in enumerate(codes, 1):
            df = _load_ohlcv(code, session)
            if len(df) < _BB_WINDOW + _PCT_WINDOW + _K_FORWARD + 5:
                continue
            n_evaluated += 1
            feat = _build_features(df)
            el = feat[feat["eligible"]]
            el = el[el.index >= _FIRE_MIN_DATE]
            for d, row in el.iterrows():
                rows.append({
                    "stock": code, "date": d,
                    "fired": bool(row["fired"]),
                    "Y":     int(row["Y"]),
                    "iso_week": d.isocalendar()[1],
                    "fy":    _fy_label(d),
                })
            if i % 50 == 0:
                logger.info("  processed {}/{}  rows_so_far={}",
                            i, len(codes), len(rows))

    events = pd.DataFrame(rows)
    if events.empty:
        raise SystemExit("no events collected")
    train_mask = events["date"] < _HOLDOUT_FY_START
    holdout_mask = events["date"] >= _HOLDOUT_FY_START
    logger.info("events: {}  train={}  holdout={}",
                len(events), int(train_mask.sum()), int(holdout_mask.sum()))

    # ── Falsifier F1 / F2: pooled-train + FY2025 holdout lift ─────────────────
    g_train, b_train, n_fires_t, n_el_t = _compute_lift(events[train_mask])
    g_hold,  b_hold,  n_fires_h, n_el_h = _compute_lift(events[holdout_mask])
    lift_train = (g_train - b_train) if not (np.isnan(g_train) or np.isnan(b_train)) else float("nan")
    lift_hold  = (g_hold  - b_hold)  if not (np.isnan(g_hold)  or np.isnan(b_hold))  else float("nan")

    # ── Per-FY breakdown ──────────────────────────────────────────────────────
    by_fy: list[tuple[str, float, float, float, int]] = []
    for fy, sub in events.groupby("fy"):
        g, b, nf, _ = _compute_lift(sub)
        by_fy.append((fy, g, b, (g - b if not (np.isnan(g) or np.isnan(b)) else float("nan")), nf))
    by_fy.sort(key=lambda r: r[0])

    # ── Falsifier F3: date-stratified cross-stock shuffle ─────────────────────
    train = events[train_mask].copy()
    by_date = train.groupby("date")
    daily_fire_count = by_date["fired"].sum().astype(int)
    daily_eligible_n = by_date.size().astype(int)
    daily_Y = by_date["Y"].apply(lambda s: s.values.astype(float)).to_dict()

    shuffle_gated_rates = np.empty(_N_SHUFFLES, dtype=float)
    for s_idx in range(_N_SHUFFLES):
        num = 0.0
        den = 0
        for d, n_eligible in daily_eligible_n.items():
            n_fire = int(daily_fire_count.loc[d])
            if n_fire == 0:
                continue
            y_vals = daily_Y[d]
            picks = np_rng.choice(n_eligible, size=n_fire, replace=False)
            num += y_vals[picks].sum()
            den += n_fire
        shuffle_gated_rates[s_idx] = (num / den) if den else float("nan")
    shuffle_mean = float(np.nanmean(shuffle_gated_rates))
    shuffle_lift_mean = shuffle_mean - b_train
    excess_lift = (g_train - shuffle_mean) if not np.isnan(g_train) else float("nan")
    shuffle_p = float(np.mean(shuffle_gated_rates >= g_train)) if not np.isnan(g_train) else float("nan")

    # ── Falsifier F4: calendar-anniversary leverage check ─────────────────────
    by_sw: dict[tuple[str, int, int], int] = {}
    for _, r in events.iterrows():
        key = (r["stock"], r["date"].year, r["iso_week"])
        if r["Y"] == 1:
            by_sw[key] = by_sw.get(key, 0) + 1
    pos_fires = events[(events["fired"]) & (events["Y"] == 1)]
    n_pos = len(pos_fires)
    n_cal_aligned = 0
    for _, r in pos_fires.iterrows():
        prior_key = (r["stock"], r["date"].year - 1, r["iso_week"])
        if by_sw.get(prior_key, 0) > 0:
            n_cal_aligned += 1
    cal_aligned_frac = (n_cal_aligned / n_pos) if n_pos else float("nan")

    # ── Median per-stock lift (M#9 catch-net) ────────────────────────────────
    per_stock_rows = []
    for stock, sub in events[train_mask].groupby("stock"):
        g, b, nf, _ = _compute_lift(sub)
        if nf < 20 or np.isnan(g) or np.isnan(b):
            continue
        per_stock_rows.append((stock, nf, g - b))
    n_pos_stocks = sum(1 for _, _, dl in per_stock_rows if dl > 0)
    frac_pos_stocks = (n_pos_stocks / len(per_stock_rows)) if per_stock_rows else float("nan")
    median_per_stock_lift = float(np.median([dl for _, _, dl in per_stock_rows])) if per_stock_rows else float("nan")

    # ── Verdict ───────────────────────────────────────────────────────────────
    f1_pass = (not np.isnan(lift_train)) and lift_train >= 0.05
    f2_pass = (not np.isnan(lift_hold))  and lift_hold  >= 0.03
    f3_pass = (not np.isnan(excess_lift)) and excess_lift >= 0.03 and shuffle_p < 0.05
    f4_pass = (not np.isnan(cal_aligned_frac)) and cal_aligned_frac <= _CAL_LEVERAGE_REJECT
    overall_pass = f1_pass and f2_pass and f3_pass and f4_pass

    verdict = "PASS" if overall_pass else "FAIL"

    md: list[str] = [
        f"# vol_pred_bbw_floor probe — terminal falsifier (round 1)",
        "",
        f"Generated: {today}  ",
        f"Universe: {n_evaluated} active stocks  ·  total events: {len(events):,}  ",
        f"Train FY2019-FY2024: {int(train_mask.sum()):,} bars  ·  Holdout FY2025: {int(holdout_mask.sum()):,} bars  ",
        f"Fire rule: bbw_pct60 ≤ {_PCT_THRESH}  ·  target: max(vol[T+1..T+{_K_FORWARD}]) ≥ {_M_VOL}×vol_ma{_VOL_WINDOW}",
        "",
        f"## Verdict: **{verdict}**",
        "",
        f"| falsifier | observed | threshold | pass? |",
        f"|---|---|---|---|",
        f"| F1 pooled-train lift          | {lift_train*100:+.2f}pp | ≥ +5.00pp | {'✓' if f1_pass else '✗'} |",
        f"| F2 FY2025 holdout lift        | {lift_hold*100:+.2f}pp  | ≥ +3.00pp | {'✓' if f2_pass else '✗'} |",
        f"| F3 excess_lift vs date-shuffle| {excess_lift*100:+.2f}pp | ≥ +3.00pp | {'✓' if f3_pass else '✗'} |",
        f"|    shuffle p-value            | {shuffle_p:.4f}        | < 0.05    | {'✓' if (not np.isnan(shuffle_p) and shuffle_p < 0.05) else '✗'} |",
        f"| F4 calendar-anniversary frac  | {cal_aligned_frac*100:.1f}%   | ≤ 40.0%   | {'✓' if f4_pass else '✗'} |",
        "",
        "## Headline rates",
        "",
        f"- Train: gated P(Y=1) = **{g_train*100:.2f}%**  vs baseline P(Y=1) = {b_train*100:.2f}%  (n_fires={n_fires_t}, n_el={n_el_t})",
        f"- Holdout FY2025: gated **{g_hold*100:.2f}%**  vs baseline {b_hold*100:.2f}%  (n_fires={n_fires_h}, n_el={n_el_h})",
        f"- Date-shuffle mean gated rate: {shuffle_mean*100:.2f}%  (so shuffle_lift_vs_baseline = {shuffle_lift_mean*100:+.2f}pp)",
        "",
        "## Per-FY breakdown",
        "",
        "| FY | n_fires | gated P(Y=1) | baseline P(Y=1) | lift |",
        "|----|---------|--------------|-----------------|------|",
    ]
    for fy, g, b, dl, nf in by_fy:
        gstr = f"{g*100:.2f}%" if not np.isnan(g) else "—"
        bstr = f"{b*100:.2f}%" if not np.isnan(b) else "—"
        dlstr = f"{dl*100:+.2f}pp" if not np.isnan(dl) else "—"
        md.append(f"| {fy} | {nf} | {gstr} | {bstr} | {dlstr} |")
    md += [
        "",
        "## Per-stock distribution (train, stocks with ≥20 fires)",
        "",
        f"- Stocks evaluated: {len(per_stock_rows)}",
        f"- Stocks with positive lift: {n_pos_stocks} ({frac_pos_stocks*100:.1f}%)  "
        f"[critic-asked threshold: ≥60%]",
        f"- Median per-stock lift: {median_per_stock_lift*100:+.2f}pp",
        "",
        "## Notes",
        "- This is a probe, not a sign. No production code touched.",
        "- §5.11 multi-factor display rubric explicitly OUT OF SCOPE; even if all 4 falsifiers pass, wiring this to ranking or sizing is a separate proposal requiring an A/B falsifier (|ΔSharpe| ≥ 0.05).",
        "- Universe constrained by current OHLCV coverage (223 stocks); larger universe would lower variance on shuffle p but not change the verdict structure.",
        "",
    ]

    path = _OUT_DIR / f"report_{today}.md"
    path.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote report to {}", path)
    print("\n".join(md))


if __name__ == "__main__":
    main()
