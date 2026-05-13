"""sizing_decorator_power_probe — can we even detect ΔSharpe ≥ +0.05 here?

Quick power-check before committing to a 250-LOC walk-forward decorator A/B.

Approach: compute the IN-SAMPLE (look-ahead-biased) ΔSharpe under the proposed
schema using the cycle-7 cache. This is an upper bound on what a properly
walk-forward implementation could achieve. Bootstrap 1000x for CI.

If in-sample ΔSharpe < +0.05 → walk-forward will be worse → reject the full probe.
If in-sample ΔSharpe > +0.10 → walk-forward has room to land above +0.05.

Schema (Analyst's sketch):
  bull/choppy × T1 = 0.5×, T2 = 1.0×, T3 = 1.5×
  bear/unknown = 1.0×

CLI: uv run --env-file devenv python -m src.analysis.sizing_decorator_power_probe
"""

from __future__ import annotations

import datetime
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import N225RegimeSnapshot
from src.data.db import get_session

_CSV_PATH = Path(__file__).parent.parent.parent / "data" / "analysis" / "wait_iv_early_cut_probe" / "events_2026-05-14.csv"
_OUT_DIR  = Path(__file__).parent.parent.parent / "data" / "analysis" / "k_bar_stationarity"

_ADX_CHOPPY  = 20.0
_N_TERT_MIN  = 10
_BOOTSTRAP_N = 1000
_RNG_SEED    = 20260514

_SCHEMA = {
    ("bull",    1): 0.5,  ("bull",    2): 1.0,  ("bull",    3): 1.5,
    ("choppy",  1): 0.5,  ("choppy",  2): 1.0,  ("choppy",  3): 1.5,
    ("bear",    1): 1.0,  ("bear",    2): 1.0,  ("bear",    3): 1.0,
    ("unknown", 1): 1.0,  ("unknown", 2): 1.0,  ("unknown", 3): 1.0,
}


def _load_events() -> pd.DataFrame:
    df = pd.read_csv(_CSV_PATH, parse_dates=["fire_date"])
    df["fire_date"] = df["fire_date"].dt.date
    df = df.dropna(subset=["mae_03", "baseline_r"]).copy()
    return df


def _load_regime_map() -> dict[datetime.date, str]:
    with get_session() as s:
        rows = s.execute(select(N225RegimeSnapshot)).scalars().all()
    out: dict[datetime.date, str] = {}
    for r in rows:
        if r.adx is None or r.adx_pos is None or r.adx_neg is None:
            out[r.date] = "unknown"
        elif r.adx < _ADX_CHOPPY:
            out[r.date] = "choppy"
        elif r.adx_pos > r.adx_neg:
            out[r.date] = "bull"
        else:
            out[r.date] = "bear"
    return out


def _assign_tertiles(df: pd.DataFrame) -> pd.Series:
    """In-sample lifetime tertile per event (look-ahead-biased on purpose —
    this is the upper-bound power probe). 0 = unknown (not enough events)."""
    counts = df["stock"].value_counts()
    valid = counts[counts >= _N_TERT_MIN].index
    medians = df[df["stock"].isin(valid)].groupby("stock")["mae_03"].median()
    if len(medians) < 6:
        return pd.Series(0, index=df.index)
    cuts = np.percentile(medians.to_numpy(), [33.333, 66.667])
    stock_tertile = medians.apply(lambda m: 1 if m <= cuts[0] else (2 if m <= cuts[1] else 3))
    return df["stock"].map(stock_tertile).fillna(0).astype(int)


def _sharpe(rs: np.ndarray) -> float:
    if len(rs) == 0:
        return float("nan")
    sd = float(rs.std(ddof=1))
    if sd == 0:
        return float("nan")
    return float(rs.mean() / sd)


def _decorated_returns(df: pd.DataFrame) -> np.ndarray:
    mult = df.apply(lambda r: _SCHEMA.get((r["regime"], int(r["tertile"])), 1.0), axis=1)
    return (df["baseline_r"].to_numpy() * mult.to_numpy())


def _bootstrap_dsharpe(df: pd.DataFrame, n_boot: int, rng: np.random.Generator) -> tuple[float, float]:
    n = len(df)
    base = df["baseline_r"].to_numpy()
    dec  = _decorated_returns(df)
    deltas = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        deltas[i] = _sharpe(dec[idx]) - _sharpe(base[idx])
    return float(np.percentile(deltas, 2.5)), float(np.percentile(deltas, 97.5))


def _mde_sharpe(n: int) -> float:
    """Minimum detectable ΔSharpe at α=0.05 two-sided, power=0.80.

    Lo (2002) approx: var(Sharpe) ≈ (1 + 0.5*S^2)/n. For paired comparison
    on same events this overestimates noise (positive cov), so this is
    a conservative upper bound.
    """
    if n < 2:
        return float("nan")
    return float(2.8 * math.sqrt(2.0 / n))


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    df = _load_events()
    regime_map = _load_regime_map()
    df["regime"] = df["fire_date"].map(regime_map).fillna("unknown")
    df["tertile"] = _assign_tertiles(df)

    # ── Per (regime, tertile) cell counts and Sharpe ──
    cell_rows: list[dict] = []
    for regime in ("bull", "choppy", "bear", "unknown"):
        for tertile in (1, 2, 3, 0):
            sub = df[(df["regime"] == regime) & (df["tertile"] == tertile)]
            n = len(sub)
            if n == 0:
                continue
            rs = sub["baseline_r"].to_numpy()
            cell_rows.append({
                "regime":    regime,
                "tertile":   tertile,
                "n":         n,
                "mean_r":    float(rs.mean()),
                "std_r":     float(rs.std(ddof=1)) if n > 1 else float("nan"),
                "sharpe":    _sharpe(rs),
                "mult":      _SCHEMA.get((regime, tertile), 1.0),
            })

    # ── Overall (in-sample upper bound) decorated vs flat ──
    base = df["baseline_r"].to_numpy()
    dec  = _decorated_returns(df)
    s_base = _sharpe(base)
    s_dec  = _sharpe(dec)
    d_sharpe = s_dec - s_base

    rng = np.random.default_rng(_RNG_SEED)
    lo, hi = _bootstrap_dsharpe(df, _BOOTSTRAP_N, rng)

    # ── MDE per gated bucket ──
    gated_n = int(((df["regime"].isin(["bull", "choppy"])) & (df["tertile"].isin([1, 3]))).sum())
    mde_global = _mde_sharpe(len(df))
    mde_gated  = _mde_sharpe(gated_n)

    # ── Report ──
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    path = _OUT_DIR / f"power_probe_{today}.md"

    md: list[str] = [
        "# Sizing Decorator Power Probe (in-sample upper bound)",
        "",
        f"Generated: {today}  ",
        f"Source: `{_CSV_PATH.name}` (cycle-7 cache, {len(df):,} events)",
        "",
        "## Per (regime × tertile) cells",
        "",
        "| regime | tertile | mult | n | mean_r | std_r | Sharpe |",
        "|--------|---------|-----:|--:|-------:|------:|-------:|",
    ]
    for r in cell_rows:
        md.append(
            f"| {r['regime']} | {r['tertile']} | {r['mult']:.1f}× | {r['n']} | "
            f"{r['mean_r']*100:+.2f}pp | {r['std_r']*100:.2f}pp | {r['sharpe']:+.3f} |"
        )

    md += [
        "",
        "## In-sample ΔSharpe (upper bound — uses lifetime tertile labels)",
        "",
        f"- Baseline (flat 1.0×) per-trade Sharpe: **{s_base:+.4f}**",
        f"- Decorated (schema) per-trade Sharpe: **{s_dec:+.4f}**",
        f"- **ΔSharpe (in-sample): {d_sharpe:+.4f}**  ",
        f"- Bootstrap 95% CI: [{lo:+.4f}, {hi:+.4f}] ({_BOOTSTRAP_N} resamples)",
        "",
        "## Detection floor (MDE @ α=0.05, power=0.80, conservative)",
        "",
        f"- All events (n={len(df):,}): MDE ΔSharpe ≈ **{mde_global:+.3f}**",
        f"- Gated events only — bull/choppy × T1/T3 (n={gated_n}): "
        f"MDE ΔSharpe ≈ **{mde_gated:+.3f}**",
        "",
        "## Power verdict",
        "",
    ]

    if d_sharpe < 0.05:
        verdict = (
            "**REJECT full probe** — in-sample upper-bound ΔSharpe is below the "
            "+0.05 accept gate. Walk-forward implementation will be worse. "
            "The sizing schema cannot detectably lift Sharpe at this n."
        )
    elif d_sharpe < 0.10:
        verdict = (
            "**MARGINAL** — in-sample ΔSharpe is in [+0.05, +0.10). Walk-forward "
            "will degrade by 30-50%; expect final landing below the +0.05 gate. "
            "Recommend reject full probe unless ΔSharpe CI low > +0.06."
        )
    else:
        verdict = (
            "**PROCEED to full probe** — in-sample ΔSharpe ≥ +0.10 leaves room for "
            "walk-forward shrinkage. Critic fixes (#2 OOS, #3 walk-forward cuts, "
            "#4 within-regime perm) must still be folded in."
        )
    md.append(verdict)
    md.append("")
    path.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote {}", path)
    print("\n=== POWER PROBE ===")
    print(f"In-sample ΔSharpe = {d_sharpe:+.4f}  CI [{lo:+.4f}, {hi:+.4f}]")
    print(f"MDE @ gated n={gated_n}: {mde_gated:+.3f}")
    print(verdict)


if __name__ == "__main__":
    main()
