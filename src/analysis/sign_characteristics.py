"""sign_characteristics — discover/validate/holdout characterization of signs.

Consumes the per-fire feature/label table from `sign_features.py` and answers
"this sign is more bullish in this situation" with a temporal split that keeps
the characterization honest (mined on FY2010-16, filtered on FY2017-21, tested
out-of-sample on FY2022-24, assembled-rule blind-tested on FY2025).

Method (locked design, 2026-05-20):
  Primary metric  = mean fixed-H forward return (fwd_ret_h); DR = P(next-peak
                    HIGH) reported alongside.
  Buckets         = domain thresholds where natural (|corr| 0.3/0.6 ; line
                    distances above/below 0 ; co-fire integer counts), terciles
                    for own sign_score (cutoffs frozen from discover → no leak).
  Effect          = mean_fwd(top bucket) − mean_fwd(bottom bucket), signed.
  Significance    = Welch z-test per cell, BH-FDR across the grid (per split).
  Survival        = same sign as discover ∧ |validate effect| ≥ 0.5·|discover|
                    ∧ clears validate FDR.
  Holdout         = survivors get a bootstrap CI on (favorable − rest) fwd_ret.
  FY2025          = per-sign "characteristic score" (# favorable conditions met)
                    top vs bottom forward-return gap, fully blind.

Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.sign_characteristics \\
        --pkl /tmp/sign_features.pkl --out docs/analysis/sign_characteristics.md
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd
from loguru import logger

_FDR_ALPHA = 0.10
_MIN_N = 50          # min fires per bucket for a contrast to be considered
_N_BOOT = 10000
_SEED = 20260520


def _split(fy: str) -> str:
    y = int(fy[2:])
    return ("discover" if y <= 2016 else "validate" if y <= 2021
            else "holdout" if y <= 2024 else "strategy")


# ── Feature → bucket label mapping (thresholds frozen / domain-fixed) ─────────

@dataclass
class Feat:
    name: str
    col: str
    top: str        # label of the "high" anchor bucket
    bottom: str     # label of the "low" anchor bucket


def _corr_bucket(c: float) -> str | None:
    if pd.isna(c):
        return None
    a = abs(c)
    return "high" if a >= 0.6 else "low" if a <= 0.3 else "mid"


def _sign_bucket(v: float) -> str | None:
    if pd.isna(v):
        return None
    return "above" if v >= 0 else "below"


def _count_bucket_b(n: float) -> str | None:        # bullish co-fire / generic
    if pd.isna(n):
        return None
    n = int(n)
    return "0" if n == 0 else "1" if n == 1 else "2" if n == 2 else "3+"


def _count_bucket_w(n: float) -> str | None:        # bearish co-fire (sparser)
    if pd.isna(n):
        return None
    n = int(n)
    return "0" if n == 0 else "1" if n == 1 else "2+"


def _present_bucket(n: float) -> str | None:        # N225 directional presence
    if pd.isna(n):
        return None
    return "1+" if int(n) >= 1 else "0"


FEATURES: list[Feat] = [
    Feat("corr_n225", "b_corr_n225", "high", "low"),
    Feat("corr_gspc", "b_corr_gspc", "high", "low"),
    Feat("corr_hsi", "b_corr_hsi", "high", "low"),
    Feat("sma_dist", "b_sma_dist", "above", "below"),
    Feat("kumo_dist", "b_kumo_dist", "above", "below"),
    Feat("chiko_dist", "b_chiko_dist", "above", "below"),
    Feat("tenkan_dist", "b_tenkan_dist", "above", "below"),
    Feat("zz_momentum", "b_zz_momentum", "above", "below"),
    Feat("bullish_cofire", "b_bull", "3+", "0"),
    Feat("bearish_cofire", "b_bear", "2+", "0"),
    Feat("n225_bullish", "b_n225_bull", "1+", "0"),
    Feat("n225_bearish", "b_n225_bear", "1+", "0"),
    Feat("own_score", "b_score", "T3", "T1"),
]

# Directionality is NOT hard-coded here — it is derived from discover data at
# runtime (see `_derive_direction`) and applied to the co-fire / n225 features.
# The a-priori design labels disagreed with measured returns ~8/17 times.


def _derive_direction(disc: pd.DataFrame, thresh: float = 0.001
                      ) -> tuple[set, set, list]:
    """Classify each sign bullish/bearish/neutral from DISCOVER measured returns.

    Replaces the a-priori design labels (which disagree with the data ~half the
    time). Frozen from discover and applied forward → no leakage. A sign is
    bullish if its discover mean fwd_ret exceeds the discover universe mean by
    `thresh`, bearish if below by `thresh`, else neutral.
    """
    base = disc["fwd_ret_h"].mean()
    bull, bear, table = set(), set(), []
    for sg, g in disc.groupby("sign_type"):
        exc = g["fwd_ret_h"].mean() - base
        lab = "bullish" if exc > thresh else "bearish" if exc < -thresh else "neutral"
        if lab == "bullish":
            bull.add(sg)
        elif lab == "bearish":
            bear.add(sg)
        table.append((sg, lab, exc, len(g)))
    table.sort(key=lambda t: t[2])
    return bull, bear, table


def _prep(df: pd.DataFrame, bull_set: set, bear_set: set) -> pd.DataFrame:
    df = df.copy()
    df["split"] = df["fy"].map(_split)
    df["b_corr_n225"] = df["corr_n225"].map(_corr_bucket)
    df["b_corr_gspc"] = df["corr_gspc"].map(_corr_bucket)
    df["b_corr_hsi"] = df["corr_hsi"].map(_corr_bucket)
    for c in ("sma_dist", "kumo_dist", "chiko_dist", "tenkan_dist", "zz_momentum"):
        df[f"b_{c}"] = df[c].map(_sign_bucket)
    # Co-fire direction counts use DATA-DERIVED (discover-frozen) directionality,
    # not a-priori design labels. Includes self — constant offset per sign, so
    # within-sign bucket contrasts are unaffected.
    bull_cols = [f"valid_{s}" for s in bull_set if f"valid_{s}" in df.columns]
    bear_cols = [f"valid_{s}" for s in bear_set if f"valid_{s}" in df.columns]
    df["b_bull"] = df[bull_cols].notna().sum(axis=1).map(_count_bucket_b)
    df["b_bear"] = df[bear_cols].notna().sum(axis=1).map(_count_bucket_w)
    # ^N225 directional presence — same data-derived labels applied to its signs
    n225_bull = [f"n225_{s}" for s in bull_set if f"n225_{s}" in df.columns]
    n225_bear = [f"n225_{s}" for s in bear_set if f"n225_{s}" in df.columns]
    df["b_n225_bull"] = (df[n225_bull].notna().sum(axis=1).map(_present_bucket)
                         if n225_bull else None)
    df["b_n225_bear"] = (df[n225_bear].notna().sum(axis=1).map(_present_bucket)
                         if n225_bear else None)
    return df


def _freeze_score_terciles(disc: pd.DataFrame) -> dict[str, tuple[float, float]]:
    """Per-sign own_score tercile cutoffs from DISCOVER only (frozen → no leak)."""
    cuts: dict[str, tuple[float, float]] = {}
    for sg, g in disc.groupby("sign_type"):
        s = g["sign_score"].dropna()
        if len(s) >= 3 * _MIN_N:
            cuts[sg] = (float(s.quantile(1 / 3)), float(s.quantile(2 / 3)))
    return cuts


def _apply_score_bucket(df: pd.DataFrame, cuts: dict[str, tuple[float, float]]) -> pd.DataFrame:
    def b(row) -> str | None:
        c = cuts.get(row["sign_type"])
        v = row["sign_score"]
        if c is None or pd.isna(v):
            return None
        return "T1" if v <= c[0] else "T3" if v > c[1] else "T2"
    df["b_score"] = df.apply(b, axis=1)
    return df


# ── Statistics ────────────────────────────────────────────────────────────────

def _welch(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """Return (mean_diff a-b, z, two-sided p) via Welch normal approximation."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan"), float("nan"), 1.0
    diff = a.mean() - b.mean()
    se = math.sqrt(a.var(ddof=1) / na + b.var(ddof=1) / nb)
    if se == 0:
        return diff, float("nan"), 1.0
    z = diff / se
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2))))
    return diff, z, p


def _bh_fdr(pvals: list[float], alpha: float = _FDR_ALPHA) -> list[bool]:
    """Benjamini-Hochberg: returns reject flags aligned to input order."""
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    reject = [False] * m
    kmax = -1
    for rank, i in enumerate(order, 1):
        if pvals[i] <= rank / m * alpha:
            kmax = rank
    for rank, i in enumerate(order, 1):
        if rank <= kmax:
            reject[i] = True
    return reject


def _boot_ci(fav: np.ndarray, rest: np.ndarray, rng: np.random.Generator) -> tuple[float, float, float]:
    """Bootstrap 95% CI on mean(fav) - mean(rest)."""
    if len(fav) < 5 or len(rest) < 5:
        return float("nan"), float("nan"), float("nan")
    diffs = np.empty(_N_BOOT)
    for i in range(_N_BOOT):
        diffs[i] = (rng.choice(fav, len(fav), replace=True).mean()
                    - rng.choice(rest, len(rest), replace=True).mean())
    return float(fav.mean() - rest.mean()), float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


# ── Per-cell contrast ─────────────────────────────────────────────────────────

@dataclass
class Cell:
    sign: str
    feat: str
    top_lbl: str
    bot_lbl: str
    eff_ret: float      # mean_fwd(top) - mean_fwd(bottom), in return units
    eff_dr: float       # DR(top) - DR(bottom)
    n_top: int
    n_bot: int
    p: float
    fdr: bool = False


# Stock-state features have an all-stock-day universe baseline (universe_baseline.py);
# their effects are residualized against it → sign-specific EXCESS, not inherited
# beta. Fire-context features (co-fire, n225 co-fire, own_score) have no
# all-stock-day analog and are reported raw.
STOCK_STATE = {"corr_n225", "corr_gspc", "corr_hsi",
               "sma_dist", "kumo_dist", "chiko_dist", "tenkan_dist", "zz_momentum"}


def _resid(sub: pd.DataFrame, f: "Feat", split: str, univ: dict) -> np.ndarray:
    """fwd_ret residualized against the all-stock-day universe bucket mean.

    For stock-state features, subtract univ[feature][split][bucket] per fire so
    the result is excess over the universe tilt. Fire-context features unchanged.
    """
    fwd = sub["fwd_ret_h"].to_numpy()
    if f.name not in STOCK_STATE:
        return fwd
    ub = univ.get(f.name, {}).get(split, {})
    if not ub:
        return fwd
    base = sub[f.col].map(lambda b: ub.get(b, 0.0)).to_numpy()
    return fwd - base


def _scan(df: pd.DataFrame, signs: list[str], split: str, univ: dict) -> list[Cell]:
    cells: list[Cell] = []
    for sg in signs:
        s = df[df["sign_type"] == sg]
        for f in FEATURES:
            top = s[s[f.col] == f.top]
            bot = s[s[f.col] == f.bottom]
            if len(top) < _MIN_N or len(bot) < _MIN_N:
                continue
            ar = _resid(top, f, split, univ)
            br = _resid(bot, f, split, univ)
            eff, _, p = _welch(ar, br)
            dr_t = (top["out_direction"] == 1).mean()
            dr_b = (bot["out_direction"] == 1).mean()
            cells.append(Cell(sg, f.name, f.top, f.bottom, eff, dr_t - dr_b,
                              len(top), len(bot), p))
    flags = _bh_fdr([c.p for c in cells])
    for c, fl in zip(cells, flags):
        c.fdr = fl
    return cells


# ── Report ────────────────────────────────────────────────────────────────────

def _fmt_pp(x: float) -> str:
    return "—" if (x is None or (isinstance(x, float) and math.isnan(x))) else f"{x*100:+.2f}"


def run(pkl: str, out: str, univ_path: str = "/tmp/universe_baseline.pkl") -> None:
    logger.info("Loading {}", pkl)
    raw = pd.read_pickle(pkl)
    raw = raw[raw["fwd_ret_h"].notna()].copy()
    # Winsorize forward returns (must match universe_baseline._WINSOR) so the
    # rare extreme-move fire and the baseline are on the same robust scale.
    raw["fwd_ret_h"] = raw["fwd_ret_h"].clip(-0.6, 0.6)
    raw["split"] = raw["fy"].map(_split)

    # Universe baseline (all-stock-day) for residualization → sign-specific excess.
    try:
        univ = pd.read_pickle(univ_path)
    except FileNotFoundError:
        raise SystemExit(f"Universe baseline {univ_path} missing — run "
                         f"`python -m src.analysis.universe_baseline` first.")

    # Data-derived directionality (frozen from discover), replacing design labels.
    bull_set, bear_set, dir_table = _derive_direction(raw[raw["split"] == "discover"])
    logger.info("data-derived direction: {} bullish, {} bearish, {} neutral",
                len(bull_set), len(bear_set),
                len(dir_table) - len(bull_set) - len(bear_set))

    df = _prep(raw, bull_set, bear_set)
    disc = df[df["split"] == "discover"].copy()
    val = df[df["split"] == "validate"].copy()
    hold = df[df["split"] == "holdout"].copy()
    strat = df[df["split"] == "strategy"].copy()

    cuts = _freeze_score_terciles(disc)
    for part in (disc, val, hold, strat):
        _apply_score_bucket(part, cuts)
    df = pd.concat([disc, val, hold, strat])

    signs = sorted(df["sign_type"].unique())
    logger.info("discover scan: {} signs × {} features", len(signs), len(FEATURES))
    dcells = {(c.sign, c.feat): c for c in _scan(disc, signs, "discover", univ)}
    vcells = {(c.sign, c.feat): c for c in _scan(val, signs, "validate", univ)}

    # ── Survival: same sign + >=50% magnitude + validate FDR ─────────────────
    survivors: list[tuple[str, str, str, float, float]] = []  # sign,feat,fav_lbl,disc_eff,val_eff
    for key, dc in dcells.items():
        if not dc.fdr or math.isnan(dc.eff_ret):
            continue
        vc = vcells.get(key)
        if vc is None or math.isnan(vc.eff_ret):
            continue
        same_sign = (dc.eff_ret > 0) == (vc.eff_ret > 0)
        mag_ok = abs(vc.eff_ret) >= 0.5 * abs(dc.eff_ret)
        if same_sign and mag_ok and vc.fdr:
            fav = dc.top_lbl if dc.eff_ret > 0 else dc.bot_lbl
            survivors.append((dc.sign, dc.feat, fav, dc.eff_ret, vc.eff_ret))

    logger.info("{} discover-FDR cells, {} survivors", sum(c.fdr for c in dcells.values()), len(survivors))

    # ── Holdout bootstrap on survivors ───────────────────────────────────────
    rng = np.random.default_rng(_SEED)
    feat_by_name = {f.name: f for f in FEATURES}
    holdout_rows = []
    for sign, feat, fav, deff, veff in survivors:
        f = feat_by_name[feat]
        s = hold[hold["sign_type"] == sign]
        favm = _resid(s[s[f.col] == fav], f, "holdout", univ)
        rest = _resid(s[(s[f.col].notna()) & (s[f.col] != fav)], f, "holdout", univ)
        d, lo, hi = _boot_ci(favm, rest, rng)
        holdout_rows.append((sign, feat, fav, deff, veff, d, lo, hi, len(favm)))

    # ── FY2025 assembled-rule blind test ─────────────────────────────────────
    fav_by_sign: dict[str, list[tuple[str, str]]] = {}
    for sign, feat, fav, *_ in survivors:
        fav_by_sign.setdefault(sign, []).append((feat_by_name[feat].col, fav))
    strat = strat.copy()

    def _char_score(row) -> float | None:
        conds = fav_by_sign.get(row["sign_type"])
        if not conds:
            return None
        return sum(1 for col, lbl in conds if row[col] == lbl)
    strat["char_score"] = strat.apply(_char_score, axis=1)
    cov = strat[strat["char_score"].notna()]
    fy25_lines = []
    if len(cov) >= 2 * _MIN_N:
        med = cov["char_score"].median()
        hi_g = cov[cov["char_score"] > med]["fwd_ret_h"]
        lo_g = cov[cov["char_score"] <= med]["fwd_ret_h"]
        if len(hi_g) >= 20 and len(lo_g) >= 20:
            fy25_lines.append(
                f"FY2025 blind: fires above median characteristic-score "
                f"mean_fwd={_fmt_pp(hi_g.mean())}pp (n={len(hi_g)}) vs "
                f"at/below {_fmt_pp(lo_g.mean())}pp (n={len(lo_g)}) → "
                f"Δ={_fmt_pp(hi_g.mean()-lo_g.mean())}pp")
    else:
        fy25_lines.append(f"FY2025 blind: only {len(cov)} fires carry a surviving "
                          f"characteristic — too thin for the assembled-score test.")

    _write_report(out, df, signs, dcells, survivors, holdout_rows, fy25_lines, dir_table)
    logger.info("Wrote report → {}", out)


def _write_report(out, df, signs, dcells, survivors, holdout_rows, fy25_lines,
                  dir_table) -> None:
    import os
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    L: list[str] = []
    L.append("# Sign Characteristics — discover / validate / holdout\n")
    L.append(f"_Generated by `sign_characteristics.py`. Primary metric: fixed-H forward "
             f"return (winsorized ±60%). Splits: discover FY2010-16 (n={int((df.split=='discover').sum())}), "
             f"validate FY2017-21 (n={int((df.split=='validate').sum())}), holdout FY2022-24 "
             f"(n={int((df.split=='holdout').sum())}), strategy FY2025 "
             f"(n={int((df.split=='strategy').sum())})._\n")
    L.append("**Stock-state features (corr_*, *_dist, zz_momentum) are RESIDUALIZED** "
             "against the all-stock-day universe baseline (`universe_baseline.py`), so "
             "their effect = the sign's *excess* over the universe tilt, not inherited "
             "beta. Fire-context features (co-fire, n225, own_score) are raw.\n")
    L.append("Effect = mean_fwd(top) − mean_fwd(bottom), pp. `*` = clears discover "
             "BH-FDR (α=0.10). `✓` = survived validation.\n")

    # ── Data-derived directionality (replaces a-priori labels) ───────────────
    L.append("## Data-derived directionality (discover FY2010-16)\n")
    L.append("Each sign classified by measured discover excess vs the universe mean — "
             "NOT design intent. Co-fire direction features use these (frozen) labels.\n")
    L.append("| direction | signs |")
    L.append("|---|---|")
    for lab in ("bullish", "neutral", "bearish"):
        sgs = [f"`{s}`" for s, l, *_ in dir_table if l == lab]
        L.append(f"| {lab} | {', '.join(sgs) if sgs else '—'} |")
    L.append("")

    # ── Master matrix ────────────────────────────────────────────────────────
    L.append("## Master matrix — discover effect (pp forward return)\n")
    feats = [f.name for f in FEATURES]
    surv_keys = {(s, f) for s, f, *_ in survivors}
    L.append("| sign | " + " | ".join(feats) + " |")
    L.append("|" + "---|" * (len(feats) + 1))
    for sg in signs:
        cells = []
        for fn in feats:
            c = dcells.get((sg, fn))
            if c is None or math.isnan(c.eff_ret):
                cells.append("·")
            else:
                mark = "✓" if (sg, fn) in surv_keys else ("*" if c.fdr else "")
                cells.append(f"{c.eff_ret*100:+.1f}{mark}")
        L.append(f"| {sg} | " + " | ".join(cells) + " |")
    L.append("\n`✓` = survived validation (same sign + ≥50% magnitude + validate FDR).\n")

    # ── Per-sign cards ───────────────────────────────────────────────────────
    L.append("## Per-sign characteristics\n")
    for sg in signs:
        cs = [dcells[(sg, f.name)] for f in FEATURES if (sg, f.name) in dcells]
        cs = [c for c in cs if not math.isnan(c.eff_ret)]
        if not cs:
            continue
        cs.sort(key=lambda c: abs(c.eff_ret), reverse=True)
        L.append(f"### {sg}\n")
        top = cs[0]
        bull_lbl = top.top_lbl if top.eff_ret > 0 else top.bot_lbl
        bear_lbl = top.bot_lbl if top.eff_ret > 0 else top.top_lbl
        L.append(f"- Strongest context: **{top.feat}** — more bullish when "
                 f"`{bull_lbl}` (Δ {_fmt_pp(abs(top.eff_ret))}pp fwd, "
                 f"DR Δ {_fmt_pp(abs(top.eff_dr))}pp), more bearish when `{bear_lbl}`."
                 f"{'  *(FDR-sig)*' if top.fdr else ''}")
        L.append("")
        L.append("| feature | top−bottom | effect pp | DR Δ pp | n_top | n_bot | FDR | survived |")
        L.append("|---|---|---|---|---|---|---|---|")
        for c in cs:
            surv = "✓" if (sg, c.feat) in surv_keys else ""
            L.append(f"| {c.feat} | {c.top_lbl}−{c.bot_lbl} | {_fmt_pp(c.eff_ret)} | "
                     f"{_fmt_pp(c.eff_dr)} | {c.n_top} | {c.n_bot} | "
                     f"{'*' if c.fdr else ''} | {surv} |")
        L.append("")

    # ── Survivors + holdout ──────────────────────────────────────────────────
    L.append("## Surviving characteristics — holdout (FY2022-24) confirmation\n")
    if holdout_rows:
        L.append("| sign | feature | favorable | disc pp | val pp | holdout Δ pp | 95% CI | n_fav | OOS✓ |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for sign, feat, fav, deff, veff, d, lo, hi, nfav in holdout_rows:
            ok = "✓" if (not math.isnan(lo) and lo > 0) else ""
            ci = "—" if math.isnan(lo) else f"[{lo*100:+.2f}, {hi*100:+.2f}]"
            L.append(f"| {sign} | {feat} | {fav} | {_fmt_pp(deff)} | {_fmt_pp(veff)} | "
                     f"{_fmt_pp(d)} | {ci} | {nfav} | {ok} |")
        n_oos = sum(1 for r in holdout_rows if not math.isnan(r[6]) and r[6] > 0)
        L.append(f"\n**{n_oos}/{len(holdout_rows)} survivors hold positive direction in the "
                 f"holdout; {sum(1 for r in holdout_rows if not math.isnan(r[6]) and r[6] > 0 and r[7] > 0)} "
                 f"with CI excluding 0.**\n")
    else:
        L.append("_No characteristics survived discover-FDR → validation._\n")

    L.append("## FY2025 — assembled-rule blind test\n")
    for ln in fy25_lines:
        L.append(f"- {ln}")
    L.append("")
    L.append("---")
    L.append("_Note: co-fire count features include the firing sign itself; within-sign "
             "bucket contrasts are unaffected (constant offset), but cross-sign count "
             "levels are not directly comparable. Distance/correlation/N225 features are "
             "fire-time legal; outcome labels are forward-looking and never used as inputs._")
    with open(out, "w") as fh:
        fh.write("\n".join(L) + "\n")


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    p = argparse.ArgumentParser(prog="python -m src.analysis.sign_characteristics")
    p.add_argument("--pkl", default="/tmp/sign_features.pkl")
    p.add_argument("--out", default="docs/analysis/sign_characteristics.md")
    args = p.parse_args(argv)
    run(args.pkl, args.out)


if __name__ == "__main__":
    main()
