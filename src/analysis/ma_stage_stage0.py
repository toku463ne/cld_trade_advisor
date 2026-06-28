"""Stage 0 — 移動平均線大循環分析 (Kojiro "great-cycle") 3-MA stage forward-return study.

Idea (2026-06-28, from docs/books/kojiro.md — 小次郎講師 真・トレーダーズバイブル):
plot 3 SMAs (短期=5, 中期=20, 長期=40) and classify each bar into one of 6 ステージ by the
top-to-bottom ordering of the three lines:

    Stage 1: s>m>l   stable UPTREND      (本仕掛け buy zone — the "perfect order")
    Stage 2: m>s>l   uptrend ending
    Stage 3: m>l>s   entering downtrend
    Stage 4: l>m>s   stable DOWNTREND     (本仕掛け sell zone)
    Stage 5: l>s>m   downtrend ending
    Stage 6: s>l>m   entering uptrend

Book claim: Stage 1 (esp. with all 3 MAs right-shoulder-up = パーフェクトオーダー) carries a
BUY edge; Stage 4 a SELL edge.  This Stage 0 asks the only questions that gate everything
downstream:

  Q1 (structure)   — pooled forward return by stage: is there monotone structure with
                     Stage 1 best / Stage 4 worst, as the book asserts?
  Q2 (BETA strip)  — THE decisive control.  The repo's recurring finding is that MA-stack
                     uptrend signals are pure N225 beta (a Stage-1 stock rises because the
                     market rises, not because it out-selects).  We subtract the same-date
                     equal-weight universe mean forward return from every bar (market-neutral)
                     and re-read the stage panel.  If the structure VANISHES under MN, the
                     stage axis is beta, not alpha.
  Q3 (onset)       — is the Stage-1 ONSET (fresh transition X->1) tradeable vs baseline,
                     and does it survive per-FY (does the edge live only in bull years)?
  Q4 (slope conf)  — does the book's "all 3 MAs rising" (右肩上がり) confirmation add EV on
                     top of bare Stage 1, or is it redundant?

Measurement-only.  Does NOT touch the strategy book.  Only if a beyond-baseline,
BETA-STRIPPED, regime-robust, monotone structure exists does the follow-up (a `ma_stage`
sign or a confluence regime-gate + the paired fill-order null, CLAUDE.md Methodology)
make sense.

Outcome (two-bar fill, per CLAUDE.md): enter open[T+1], exit close[T+h] for h in HORIZONS.
Forward returns winsorized at +/-WINSOR.  Tradeable bars only (avg-turnover floor).

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.ma_stage_stage0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.data.db import get_session

# ---- params ---------------------------------------------------------------
SHORT_N = 5                  # 短期 SMA
MID_N = 20                   # 中期 SMA
LONG_N = 40                  # 長期 SMA
SLOPE_N = 5                  # bars over which an MA must rise to count as 右肩上がり
TURN_MIN = 30_000_000.0      # ¥ AVERAGE turnover floor (close*avg_vol) — tradeable names
TURN_LOOKBACK = 20           # trailing bars for average volume (turnover gate)
HORIZONS = [1, 5, 10, 20]    # forward holding bars
WINSOR = 0.60                # forward-return clip
COOLDOWN = 20                # bars suppressed per stock after an onset fire (dedupe clusters)
_FY_START_MONTH = 4          # JP fiscal year starts in April

_STAGE_LABEL = {
    1: "1 s>m>l UP*", 2: "2 m>s>l up-end", 3: "3 m>l>s dn-entry",
    4: "4 l>m>s DN*", 5: "5 l>s>m dn-end", 6: "6 s>l>m up-entry",
}


def _codes() -> list[str]:
    with get_session() as s:
        rows = s.execute(text(
            "SELECT DISTINCT stock_code FROM ohlcv_1d ORDER BY stock_code"
        )).all()
    return [r[0] for r in rows if not r[0].startswith("^")]


def _load_one(s, code: str) -> pd.DataFrame:
    rows = s.execute(text(
        "SELECT ts, open_price::float8, high_price::float8, low_price::float8, "
        "close_price::float8, volume::float8 FROM ohlcv_1d "
        "WHERE stock_code=:c ORDER BY ts"
    ), {"c": code}).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol"])
    df["date"] = pd.to_datetime(df["ts"]).dt.tz_localize(None).dt.normalize()
    g = df.groupby("date", sort=True)
    return g.agg(open=("open", "first"), high=("high", "max"),
                 low=("low", "min"), close=("close", "last"),
                 vol=("vol", "sum")).reset_index()


def _fy(d: pd.Timestamp) -> int:
    return d.year if d.month >= _FY_START_MONTH else d.year - 1


def _winsor(a: np.ndarray) -> np.ndarray:
    return np.clip(a, -WINSOR, WINSOR)


def _stage_of(s: np.ndarray, m: np.ndarray, l: np.ndarray) -> np.ndarray:
    """Map (short, mid, long) ordering -> stage int 1..6 (0 where any NaN)."""
    stage = np.zeros(s.shape, dtype=np.int8)
    valid = np.isfinite(s) & np.isfinite(m) & np.isfinite(l)
    # 6 strict orderings
    stage[valid & (s > m) & (m > l)] = 1
    stage[valid & (m > s) & (s > l)] = 2
    stage[valid & (m > l) & (l > s)] = 3
    stage[valid & (l > m) & (m > s)] = 4
    stage[valid & (l > s) & (s > m)] = 5
    stage[valid & (s > l) & (l > m)] = 6
    return stage


def _stats(fwd: np.ndarray, label: str) -> dict:
    fwd = fwd[np.isfinite(fwd)]
    if fwd.size == 0:
        return {"label": label, "n": 0, "mean": 0.0, "median": 0.0, "dr": 0.0}
    return {"label": label, "n": int(fwd.size),
            "mean": float(np.mean(fwd) * 100),
            "median": float(np.median(fwd) * 100),
            "dr": float(np.mean(fwd > 0) * 100)}


def main() -> None:
    codes = _codes()
    logger.info("streaming {} stocks ...", len(codes))
    parts = []
    min_len = LONG_N + SLOPE_N + max(HORIZONS) + 2
    keep_cols = (["code", "date", "stage", "onset1", "all_up", "all_dn"]
                 + [f"fwd{h}" for h in HORIZONS])
    with get_session() as s:
        for n, code in enumerate(codes):
            if n % 300 == 0:
                logger.info("  {}/{}", n, len(codes))
            sub = _load_one(s, code)
            if len(sub) < min_len:
                continue
            sub["code"] = code
            c = sub["close"].to_numpy()
            o = sub["open"].to_numpy()
            v = sub["vol"].to_numpy()
            sm = sub["close"].rolling(SHORT_N).mean()
            md = sub["close"].rolling(MID_N).mean()
            lg = sub["close"].rolling(LONG_N).mean()
            s_a, m_a, l_a = sm.to_numpy(), md.to_numpy(), lg.to_numpy()
            stage = _stage_of(s_a, m_a, l_a)
            sub["stage"] = stage
            # onset: fresh transition into stage 1 (X != 1 -> 1)
            prev_stage = np.concatenate([[0], stage[:-1]])
            sub["onset1"] = (stage == 1) & (prev_stage != 1)
            # slope confirmation (右肩上がり / 右肩下がり over SLOPE_N bars)
            sub["all_up"] = ((sm > sm.shift(SLOPE_N)) & (md > md.shift(SLOPE_N))
                             & (lg > lg.shift(SLOPE_N))).to_numpy()
            sub["all_dn"] = ((sm < sm.shift(SLOPE_N)) & (md < md.shift(SLOPE_N))
                             & (lg < lg.shift(SLOPE_N))).to_numpy()
            # tradeable gate (avg turnover floor)
            vavg = sub["vol"].rolling(TURN_LOOKBACK).mean().shift(1).to_numpy()
            turn_avg = c * np.where(vavg > 0, vavg, np.nan)
            # forward returns (two-bar fill)
            entry = np.concatenate([o[1:], [np.nan]])           # open[T+1]
            for hh in HORIZONS:
                exitc = np.concatenate([c[hh:], [np.nan] * hh]) # close[T+hh]
                sub[f"fwd{hh}"] = exitc / entry - 1.0
            keep = sub[(stage > 0) & (turn_avg >= TURN_MIN)].copy()
            if keep.empty:
                continue
            parts.append(keep[keep_cols])
    pop = pd.concat(parts, ignore_index=True)
    pop["fy"] = pop["date"].apply(_fy)
    logger.info("tradeable stage bars (pop): {}", len(pop))

    # ---- market-neutral forward returns (BETA strip, Q2) ------------------
    # subtract same-date equal-weight universe mean fwd return from each bar
    for hh in HORIZONS:
        col = f"fwd{hh}"
        date_mean = pop.groupby("date")[col].transform("mean")
        pop[f"mn{hh}"] = pop[col] - date_mean

    # ---- baseline: all tradeable bars (any stage) -------------------------
    print("\n=== BASELINE: all tradeable stage bars (any stage) ===")
    print(f"n={len(pop)}  stocks={pop['code'].nunique()}  "
          f"FY{pop['fy'].min()}..{pop['fy'].max()}")
    print(f"{'horizon':>8} {'n':>9} {'mean%':>8} {'med%':>8} {'DR%':>7} "
          f"{'MNmean%':>8} {'MN_DR%':>7}")
    base = {}
    for hh in HORIZONS:
        st = _stats(_winsor(pop[f"fwd{hh}"].to_numpy()), f"h{hh}")
        mn = _stats(_winsor(pop[f"mn{hh}"].to_numpy()), f"h{hh}")
        base[hh] = st
        base[("mn", hh)] = mn
        print(f"{'h'+str(hh):>8} {st['n']:>9} {st['mean']:>8.2f} "
              f"{st['median']:>8.2f} {st['dr']:>7.1f} "
              f"{mn['mean']:>8.2f} {mn['dr']:>7.1f}")

    # ---- Q1+Q2: pooled forward return BY STAGE (raw + market-neutral) -----
    print("\n=== Q1/Q2: forward return by STAGE (h=10) — raw vs market-neutral ===")
    print("  (book: Stage 1 best for longs, Stage 4 worst. MN strips N225 beta.)")
    print(f"{'stage':>18} {'n':>9} {'raw_mean%':>10} {'raw_DR%':>8} "
          f"{'MN_mean%':>9} {'MN_DR%':>7}")
    for sg in range(1, 7):
        msk = pop["stage"].to_numpy() == sg
        raw = _stats(_winsor(pop.loc[msk, "fwd10"].to_numpy()), str(sg))
        mn = _stats(_winsor(pop.loc[msk, "mn10"].to_numpy()), str(sg))
        print(f"{_STAGE_LABEL[sg]:>18} {raw['n']:>9} {raw['mean']:>10.2f} "
              f"{raw['dr']:>8.1f} {mn['mean']:>9.2f} {mn['dr']:>7.1f}")
    # Stage1 - Stage4 spread (the book's directional claim), raw and MN
    s1 = pop["stage"].to_numpy() == 1
    s4 = pop["stage"].to_numpy() == 4
    for tag, col in [("raw", "fwd10"), ("MN", "mn10")]:
        a = _winsor(pop.loc[s1, col].to_numpy())
        b = _winsor(pop.loc[s4, col].to_numpy())
        a, b = a[np.isfinite(a)], b[np.isfinite(b)]
        print(f"  Stage1 - Stage4 {tag} h10 mean spread: "
              f"{(np.mean(a)-np.mean(b))*100:+.2f}pp")

    # ---- Q1/Q2 full-horizon stage-1 vs stage-4 panel ----------------------
    print("\n=== Stage 1 vs Stage 4 across horizons (raw / MN mean%) ===")
    print(f"{'horizon':>8} {'S1_raw':>8} {'S1_MN':>8} {'S4_raw':>8} {'S4_MN':>8}")
    for hh in HORIZONS:
        a1 = _stats(_winsor(pop.loc[s1, f"fwd{hh}"].to_numpy()), "")
        a1m = _stats(_winsor(pop.loc[s1, f"mn{hh}"].to_numpy()), "")
        a4 = _stats(_winsor(pop.loc[s4, f"fwd{hh}"].to_numpy()), "")
        a4m = _stats(_winsor(pop.loc[s4, f"mn{hh}"].to_numpy()), "")
        print(f"{'h'+str(hh):>8} {a1['mean']:>8.2f} {a1m['mean']:>8.2f} "
              f"{a4['mean']:>8.2f} {a4m['mean']:>8.2f}")

    # ---- event reporter ---------------------------------------------------
    def _dedupe(ev: pd.DataFrame) -> pd.DataFrame:
        ev = ev.sort_values(["code", "date"])
        keep_idx, last = [], {}
        for idx, code, d in zip(ev.index, ev["code"], ev["date"]):
            prev = last.get(code)
            if prev is None or (d - prev).days > COOLDOWN * 7 / 5:
                keep_idx.append(idx)
                last[code] = d
        return ev.loc[keep_idx]

    def _report(ev: pd.DataFrame, name: str, dedupe: bool = True) -> None:
        if dedupe:
            ev = _dedupe(ev)
        print(f"\n=== EVENT: {name} ===")
        print(f"fires: {len(ev)}  stocks: {ev['code'].nunique()}  "
              f"({100*len(ev)/max(len(pop),1):.2f}% of pop)")
        print(f"{'horizon':>8} {'n':>6} {'mean%':>8} {'med%':>8} {'DR%':>7} "
              f"{'exc_mn':>7} {'MNmean%':>8} {'MN_exc':>7}")
        for hh in HORIZONS:
            st = _stats(_winsor(ev[f"fwd{hh}"].to_numpy()), name)
            mn = _stats(_winsor(ev[f"mn{hh}"].to_numpy()), name)
            b, bm = base[hh], base[("mn", hh)]
            if st["n"] == 0:
                continue
            print(f"{'h'+str(hh):>8} {st['n']:>6} {st['mean']:>8.2f} "
                  f"{st['median']:>8.2f} {st['dr']:>7.1f} "
                  f"{st['mean']-b['mean']:>7.2f} {mn['mean']:>8.2f} "
                  f"{mn['mean']-bm['mean']:>7.2f}")
        print("  per-FY (h=10 raw mean% / MN mean% / n):")
        line = []
        for fy in sorted(ev["fy"].unique()):
            sf = _winsor(ev[ev["fy"] == fy]["fwd10"].to_numpy())
            sfm = _winsor(ev[ev["fy"] == fy]["mn10"].to_numpy())
            sf, sfm = sf[np.isfinite(sf)], sfm[np.isfinite(sfm)]
            m = np.mean(sf) * 100 if sf.size else 0.0
            mm = np.mean(sfm) * 100 if sfm.size else 0.0
            line.append(f"FY{fy}:{m:+.1f}/{mm:+.1f}(n{sf.size})")
        print("    " + "  ".join(line))

    # ---- Q3: Stage-1 onset (fresh transition into Stage 1) ----------------
    onset = pop[pop["onset1"]]
    _report(onset, "Q3 Stage-1 ONSET (fresh X->1)")

    # ---- Q4: slope-confirmation gate on Stage 1 ---------------------------
    # bare Stage-1 holding bars vs Stage-1 + all-3-rising (パーフェクトオーダー)
    s1_all = pop[pop["stage"] == 1]
    _report(s1_all, "Q4a Stage-1 ALL bars (holding, no dedupe)", dedupe=False)
    s1_po = pop[(pop["stage"] == 1) & pop["all_up"]]
    _report(s1_po, "Q4b Stage-1 + all-3-rising (perfect order, no dedupe)",
            dedupe=False)
    # onset + perfect order (the tradeable book entry)
    onset_po = pop[pop["onset1"] & pop["all_up"]]
    _report(onset_po, "Q4c Stage-1 ONSET + all-3-rising")


if __name__ == "__main__":
    main()
