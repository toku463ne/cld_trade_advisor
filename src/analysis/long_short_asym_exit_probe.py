"""long_short_asym_exit_probe — 9-pair (long_rule, short_rule) A/B.

Question: does an asymmetric (long_rule, short_rule) default pair beat the
universal-best single rule on aggregate Sharpe?

Reuses the 56k-row fire table from peak5_fire_table.py. Same simplified exit-
rule proxies as peak5_exit_selector_probe.py:
  - TIME20 : exit at fire_bar + 20 (close)
  - TRAIL  : peak-trailing stop, 4×ATR drawdown from running peak
  - TPSL   : TP +3×ATR, SL −2×ATR, 60-bar cap

Pre-registered (LOCKED before data inspection):
- Pair universe: 3×3 = 9 combinations from {TIME20, TRAIL, TPSL}.
- Discover: FY2019-FY2022. Pick pair_best_discover (highest Sharpe).
  If pair_best_discover IS a symmetric pair → REJECT (no asymmetry signal).
- Falsifier gates (ALL must hold to ACCEPT):
  G1: Sharpe(pair_best) − Sharpe(universal_best) ≥ +0.10 on Discover.
  G2: Same pair's Sharpe on Validate ≥ Validate universal-best − 0.05.
  G3: Same pair's Sharpe on OOS ≥ OOS universal-best + 0.05.
  G4: Both long and short arms have n ≥ 1000 on each slice.
- Tie-break: alphabetical by rule name.

This is a probe — no production wiring proposed. Findings (if any) are
provisional given exit-rule proxies and peak-anchored entries.

CLI: uv run --env-file devenv python -m src.analysis.long_short_asym_exit_probe
"""

from __future__ import annotations

import datetime
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.data.db import get_session
from src.data.models import Ohlcv1d

_FIRE_TABLE = Path(__file__).parent.parent.parent / "data" / "analysis" / "peak5_shape" / "fire_table_2026-05-15.csv"
_OUT_DIR = Path(__file__).parent.parent.parent / "data" / "analysis" / "peak5_shape"
_ATR_WIN = 14
_FORWARD_BARS = 80
_DISCOVER_END = datetime.date(2023, 3, 31)
_VALIDATE_END = datetime.date(2025, 3, 31)

_TRAIL_ATR_MULT = 4.0
_TPSL_TP_MULT   = 3.0
_TPSL_SL_MULT   = 2.0
_TPSL_TIME_CAP  = 60

_RULES = ["TIME20", "TRAIL", "TPSL"]


def _load_ohlcv(code: str, session) -> pd.DataFrame:
    rows = session.execute(
        select(Ohlcv1d.ts, Ohlcv1d.open_price, Ohlcv1d.high_price,
               Ohlcv1d.low_price, Ohlcv1d.close_price)
        .where(Ohlcv1d.stock_code == code)
        .order_by(Ohlcv1d.ts)
    ).all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame({
        "open":  [float(r.open_price) for r in rows],
        "high":  [float(r.high_price) for r in rows],
        "low":   [float(r.low_price)  for r in rows],
        "close": [float(r.close_price) for r in rows],
    })


def _atr(df: pd.DataFrame, win: int) -> pd.Series:
    h_l = df["high"] - df["low"]
    h_pc = (df["high"] - df["close"].shift()).abs()
    l_pc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    return tr.rolling(win, min_periods=win).mean()


def _replay_exits(side: str, fire_bar: int, df: pd.DataFrame, atr: float) -> dict:
    n = len(df)
    forward_end = min(fire_bar + _FORWARD_BARS, n - 1)
    if forward_end - fire_bar < 25:
        return {"TIME20": np.nan, "TRAIL": np.nan, "TPSL": np.nan}
    entry = df["open"].iloc[fire_bar]
    if entry <= 0 or atr <= 0:
        return {"TIME20": np.nan, "TRAIL": np.nan, "TPSL": np.nan}
    sign = 1 if side == "long" else -1

    # TIME20
    if fire_bar + 20 < n:
        r_time = sign * np.log(df["close"].iloc[fire_bar + 20] / entry)
    else:
        r_time = np.nan

    # TRAIL
    r_trail = np.nan
    if side == "long":
        peak = entry
        for k in range(1, _FORWARD_BARS + 1):
            j = fire_bar + k
            if j >= n:
                break
            peak = max(peak, df["high"].iloc[j])
            if peak - df["low"].iloc[j] >= _TRAIL_ATR_MULT * atr:
                exit_p = df["open"].iloc[j + 1] if j + 1 < n else df["close"].iloc[j]
                r_trail = np.log(exit_p / entry)
                break
        else:
            r_trail = np.log(df["close"].iloc[forward_end] / entry)
    else:
        trough = entry
        for k in range(1, _FORWARD_BARS + 1):
            j = fire_bar + k
            if j >= n:
                break
            trough = min(trough, df["low"].iloc[j])
            if df["high"].iloc[j] - trough >= _TRAIL_ATR_MULT * atr:
                exit_p = df["open"].iloc[j + 1] if j + 1 < n else df["close"].iloc[j]
                r_trail = -np.log(exit_p / entry)
                break
        else:
            r_trail = -np.log(df["close"].iloc[forward_end] / entry)

    # TPSL
    r_tpsl = np.nan
    tp_l = entry + _TPSL_TP_MULT * atr
    sl_l = entry - _TPSL_SL_MULT * atr
    tp_s = entry - _TPSL_TP_MULT * atr
    sl_s = entry + _TPSL_SL_MULT * atr
    time_limit = min(fire_bar + _TPSL_TIME_CAP, n - 1)
    for k in range(1, _TPSL_TIME_CAP + 1):
        j = fire_bar + k
        if j > time_limit:
            break
        bh = df["high"].iloc[j]
        bl = df["low"].iloc[j]
        if side == "long":
            if bh >= tp_l:
                r_tpsl = np.log(tp_l / entry); break
            if bl <= sl_l:
                r_tpsl = np.log(sl_l / entry); break
        else:
            if bl <= tp_s:
                r_tpsl = -np.log(tp_s / entry); break
            if bh >= sl_s:
                r_tpsl = -np.log(sl_s / entry); break
    if np.isnan(r_tpsl) and time_limit < n:
        r_tpsl = sign * np.log(df["close"].iloc[time_limit] / entry)

    return {"TIME20": r_time, "TRAIL": r_trail, "TPSL": r_tpsl}


def _agg_sharpe(rs: np.ndarray) -> tuple[float, float, int]:
    rs = rs[~np.isnan(rs)]
    if len(rs) < 5:
        return float("nan"), float("nan"), len(rs)
    mu = float(rs.mean())
    sd = float(rs.std())
    sh = (mu / sd * np.sqrt(252)) if sd > 0 else float("nan")
    return mu, sh, len(rs)


def _pair_sharpe(df: pd.DataFrame, long_rule: str, short_rule: str) -> tuple[float, float, int, int]:
    """Returns (mean_r, sharpe, n_long, n_short)."""
    longs = df[df["side"] == "long"][long_rule].values
    shorts = df[df["side"] == "short"][short_rule].values
    combined = np.concatenate([longs, shorts])
    mu, sh, _ = _agg_sharpe(combined)
    n_l = int(np.sum(~np.isnan(longs)))
    n_s = int(np.sum(~np.isnan(shorts)))
    return mu, sh, n_l, n_s


def main() -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()

    if not _FIRE_TABLE.exists():
        raise SystemExit(f"Fire table not found: {_FIRE_TABLE}")
    fires = pd.read_csv(_FIRE_TABLE, parse_dates=["fire_date"])
    fires["fire_date"] = fires["fire_date"].dt.date
    logger.info("Loaded {} fires", len(fires))

    logger.info("Replaying exit rules…")
    results: list[dict] = []
    with get_session() as session:
        for stock, sub in fires.groupby("stock"):
            df = _load_ohlcv(stock, session)
            if df.empty:
                continue
            atr_series = _atr(df, _ATR_WIN)
            for _, row in sub.iterrows():
                p4_bar = int(row["P4_bar"])
                if p4_bar >= len(atr_series):
                    continue
                atr = float(atr_series.iloc[p4_bar])
                if np.isnan(atr) or atr <= 0:
                    continue
                rs = _replay_exits(row["side"], int(row["fire_bar"]), df, atr)
                results.append({"stock": stock, "fire_date": row["fire_date"], **rs})

    rdf = pd.DataFrame(results).set_index(["stock", "fire_date"])
    fires_idx = fires.set_index(["stock", "fire_date"])
    fires = fires_idx.join(rdf, how="inner").reset_index()
    logger.info("Joined: {}", len(fires))

    discover = fires[fires["fire_date"] <= _DISCOVER_END].reset_index(drop=True)
    validate = fires[(fires["fire_date"] > _DISCOVER_END) & (fires["fire_date"] <= _VALIDATE_END)].reset_index(drop=True)
    oos      = fires[fires["fire_date"] > _VALIDATE_END].reset_index(drop=True)
    logger.info("Splits: Discover={} Validate={} OOS={}",
                len(discover), len(validate), len(oos))

    # ── 9-pair grid per slice ────────────────────────────────────────────────
    def _grid(df: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for lr, sr in product(_RULES, _RULES):
            mu, sh, nl, ns = _pair_sharpe(df, lr, sr)
            rows.append({"long_rule": lr, "short_rule": sr,
                         "mean_r": mu, "sharpe": sh, "n_long": nl, "n_short": ns,
                         "symmetric": lr == sr})
        return pd.DataFrame(rows)

    g_disc = _grid(discover)
    g_val  = _grid(validate)
    g_oos  = _grid(oos)

    # ── Pre-registered selection ──────────────────────────────────────────────
    best_disc = g_disc.sort_values(["sharpe", "long_rule", "short_rule"],
                                    ascending=[False, True, True]).iloc[0]
    sym_disc = g_disc[g_disc["symmetric"]].sort_values(
        ["sharpe", "long_rule"], ascending=[False, True]).iloc[0]
    universal_best_disc_rule = sym_disc["long_rule"]
    universal_best_disc_sh   = float(sym_disc["sharpe"])

    pair_is_symmetric = bool(best_disc["symmetric"])
    pair_long = str(best_disc["long_rule"])
    pair_short = str(best_disc["short_rule"])
    pair_sh_disc = float(best_disc["sharpe"])

    # ── Apply gates ──────────────────────────────────────────────────────────
    g1_delta = pair_sh_disc - universal_best_disc_sh
    g1_pass = (not pair_is_symmetric) and g1_delta >= 0.10

    pair_sh_val = float(g_val[(g_val["long_rule"] == pair_long) &
                               (g_val["short_rule"] == pair_short)]["sharpe"].iloc[0])
    universal_best_val = float(g_val[g_val["symmetric"]]
                                .sort_values("sharpe", ascending=False).iloc[0]["sharpe"])
    g2_pass = pair_sh_val >= universal_best_val - 0.05

    pair_sh_oos = float(g_oos[(g_oos["long_rule"] == pair_long) &
                               (g_oos["short_rule"] == pair_short)]["sharpe"].iloc[0])
    universal_best_oos = float(g_oos[g_oos["symmetric"]]
                                .sort_values("sharpe", ascending=False).iloc[0]["sharpe"])
    g3_pass = pair_sh_oos >= universal_best_oos + 0.05

    pair_row_disc = g_disc[(g_disc["long_rule"] == pair_long) &
                            (g_disc["short_rule"] == pair_short)].iloc[0]
    pair_row_val = g_val[(g_val["long_rule"] == pair_long) &
                          (g_val["short_rule"] == pair_short)].iloc[0]
    pair_row_oos = g_oos[(g_oos["long_rule"] == pair_long) &
                          (g_oos["short_rule"] == pair_short)].iloc[0]
    g4_pass = all(int(r["n_long"]) >= 1000 and int(r["n_short"]) >= 1000
                   for r in [pair_row_disc, pair_row_val, pair_row_oos])

    all_pass = g1_pass and g2_pass and g3_pass and g4_pass
    if pair_is_symmetric:
        verdict = "REJECT — best Discover pair is symmetric; no asymmetry signal"
    elif all_pass:
        verdict = "ACCEPT — asymmetric pair beats universal across slices"
    else:
        verdict = "REJECT — at least one gate failed"

    # ── Report ────────────────────────────────────────────────────────────────
    md: list[str] = [
        "# long_short_asym_exit_probe — 9-pair A/B",
        "",
        f"Generated: {today}",
        f"Fires: {len(fires):,} (Discover {len(discover):,} / Validate {len(validate):,} / OOS {len(oos):,})",
        "",
        f"## Verdict: **{verdict}**",
        "",
        "## Pre-registered gates",
        "",
        "| Gate | Observed | Threshold | Pass? |",
        "|------|----------|-----------|-------|",
        f"| G1 Discover ΔSharpe (pair − universal) | {g1_delta:+.3f} | ≥ +0.10 (and pair must be asymmetric) | {'✓' if g1_pass else '✗'} |",
        f"| G2 Validate Sharpe vs Validate universal | {pair_sh_val:.3f} vs {universal_best_val:.3f} | ≥ {universal_best_val - 0.05:.3f} (−0.05 slack) | {'✓' if g2_pass else '✗'} |",
        f"| G3 OOS Sharpe vs OOS universal | {pair_sh_oos:.3f} vs {universal_best_oos:.3f} | ≥ {universal_best_oos + 0.05:.3f} (+0.05 required) | {'✓' if g3_pass else '✗'} |",
        f"| G4 n_long ≥ 1000 AND n_short ≥ 1000 in all slices | min nL={min(int(r['n_long']) for r in [pair_row_disc, pair_row_val, pair_row_oos])}, min nS={min(int(r['n_short']) for r in [pair_row_disc, pair_row_val, pair_row_oos])} | ≥ 1000 | {'✓' if g4_pass else '✗'} |",
        "",
        "## Selected pair (best on Discover)",
        f"- **long_rule = {pair_long}**",
        f"- **short_rule = {pair_short}**",
        f"- Symmetric? {pair_is_symmetric}",
        f"- Discover Sharpe: {pair_sh_disc:.3f}  ·  Validate: {pair_sh_val:.3f}  ·  OOS: {pair_sh_oos:.3f}",
        "",
        f"Universal-best on Discover: **{universal_best_disc_rule}/{universal_best_disc_rule}** (Sharpe {universal_best_disc_sh:.3f})",
        "",
        "## Full 9-pair grid",
        "",
    ]
    for slice_name, gdf in [("Discover", g_disc), ("Validate", g_val), ("OOS", g_oos)]:
        md += [
            f"### {slice_name}",
            "",
            "| long\\short | TIME20 | TRAIL | TPSL |",
            "|------------|--------|-------|------|",
        ]
        for lr in _RULES:
            row = f"| **{lr}** "
            for sr in _RULES:
                sh = float(gdf[(gdf["long_rule"] == lr) & (gdf["short_rule"] == sr)]["sharpe"].iloc[0])
                mark = " *(symm)*" if lr == sr else ""
                row += f"| {sh:.3f}{mark} "
            row += "|"
            md.append(row)
        md.append("")

    md += [
        "## Notes",
        "- Exit-rule proxies (TIME20, TRAIL 4×ATR, TPSL 3/2 ATR 60-bar cap) are simplified;",
        "  not production `src/exit/` rules. Direction of signal portable, absolute Sharpe not.",
        "- Fires are peak-anchored (P4-early). Generalization to sign-driven entries untested.",
        "- Per-pair n's are roughly half of the slice total (one rule each for longs/shorts).",
        "",
    ]

    out = _OUT_DIR / f"long_short_asym_{today}.md"
    out.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote report to {}", out)
    print("\n".join(md))


if __name__ == "__main__":
    main()
