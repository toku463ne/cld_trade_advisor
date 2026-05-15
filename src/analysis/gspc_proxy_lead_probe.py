"""gspc_proxy_lead_probe — Round-1 H#2 falsifier for /sign-debate "find a new sign".

The Proposer's `gspc_proxy_lead` rests on the claim that ρ(stock, ^GSPC) is a
DIFFERENT observable from ρ(stock, ^N225). The Critic flagged this as the
heaviest H-hole: ^GSPC and ^N225 are both broad-equity benchmarks and co-move
heavily in risk-on/risk-off episodes — the two rolling correlations may move
in lockstep, in which case the proposed sign is just a finer slice of the
N225-corr axis (same trap as the May 8-null streak).

This probe answers ONE question before any production work proceeds:

    What is the pooled correlation between corr20(s, ^GSPC) and
    corr20(s, ^N225) across the universe?

Falsifier (pre-registered):
    |pooled Pearson ρ| > 0.6 OR |pooled Spearman ρ| > 0.6 → KILL the proposal.

Also emits a population count for the proposed cell
(corr_gspc ≥ 0.55 AND |corr_n225| ≤ 0.30) so the Critic's H#1 sample-size
concern can be quantified before the full probe is built.

CLI: uv run --env-file devenv python -m src.analysis.gspc_proxy_lead_probe
"""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from scipy import stats
from sqlalchemy import select

from src.data.db import get_session
from src.data.models import Ohlcv1d, Stock

_OUT_DIR = Path(__file__).parent.parent.parent / "data" / "analysis" / "gspc_proxy_lead"
_N225 = "^N225"
_GSPC = "^GSPC"
_WINDOW = 20
_MIN_PERIODS = 10
_FIRE_MIN_DATE = datetime.date(2020, 6, 1)  # post-warmup
_PROP_GSPC_THRESH = 0.55   # proposed corr_gspc floor
_PROP_N225_THRESH = 0.30   # proposed |corr_n225| ceiling
_KILL_ABS_RHO = 0.60       # pre-registered falsifier


def _load_close(code: str, session) -> pd.Series:
    rows = session.execute(
        select(Ohlcv1d.ts, Ohlcv1d.close_price)
        .where(Ohlcv1d.stock_code == code)
        .order_by(Ohlcv1d.ts)
    ).all()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.Index([r.ts.date() for r in rows], name="date")
    return pd.Series([float(r.close_price) for r in rows], index=idx, name=code).sort_index()


def _rolling_return_corr(stock: pd.Series, ind: pd.Series) -> pd.Series:
    df = pd.concat([stock.pct_change().rename("s"), ind.pct_change().rename("i")], axis=1)
    return df["s"].rolling(_WINDOW, min_periods=_MIN_PERIODS).corr(df["i"])


def main() -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()

    with get_session() as session:
        logger.info("Loading ^N225 and ^GSPC close series…")
        n225 = _load_close(_N225, session)
        gspc = _load_close(_GSPC, session)
        if n225.empty or gspc.empty:
            raise SystemExit("missing ^N225 or ^GSPC bars in dev DB")

        stocks = session.execute(
            select(Stock.code).where(Stock.is_active.is_(True)).order_by(Stock.code)
        ).scalars().all()
        stocks = [c for c in stocks if not c.startswith("^") and "=" not in c]
        logger.info("Universe: {} active stocks", len(stocks))

        pair_rows: list[tuple[float, float]] = []
        per_stock_counts: list[dict] = []
        for i, code in enumerate(stocks, 1):
            close = _load_close(code, session)
            if len(close) < _WINDOW * 6:
                continue
            corr_g = _rolling_return_corr(close, gspc)
            corr_n = _rolling_return_corr(close, n225)
            aligned = pd.concat([corr_g.rename("g"), corr_n.rename("n")], axis=1).dropna()
            aligned = aligned[aligned.index >= _FIRE_MIN_DATE]
            if aligned.empty:
                continue
            for g_val, n_val in zip(aligned["g"].values, aligned["n"].values):
                pair_rows.append((float(g_val), float(n_val)))

            in_cell = aligned[(aligned["g"] >= _PROP_GSPC_THRESH)
                              & (aligned["n"].abs() <= _PROP_N225_THRESH)]
            per_stock_counts.append({
                "stock": code,
                "n_bars": int(len(aligned)),
                "n_in_cell": int(len(in_cell)),
            })
            if i % 50 == 0:
                logger.info("  processed {}/{}", i, len(stocks))

    if not pair_rows:
        raise SystemExit("no (corr_gspc, corr_n225) pairs collected")

    df = pd.DataFrame(pair_rows, columns=["corr_gspc", "corr_n225"])
    pearson_r, pearson_p = stats.pearsonr(df["corr_gspc"], df["corr_n225"])
    spearman_r, spearman_p = stats.spearmanr(df["corr_gspc"], df["corr_n225"])

    counts_df = pd.DataFrame(per_stock_counts)
    total_pairs = int(len(df))
    total_in_cell = int(counts_df["n_in_cell"].sum()) if not counts_df.empty else 0
    n_stocks_with_any_cell = int((counts_df["n_in_cell"] > 0).sum()) if not counts_df.empty else 0

    kill_h2 = (abs(pearson_r) > _KILL_ABS_RHO) or (abs(spearman_r) > _KILL_ABS_RHO)
    verdict = ("KILL — H#2 falsifier triggered" if kill_h2
               else "PASS — H#2 cleared; proceed to next falsifier (H#4 zigzag, H#5 composite-walk)")

    md: list[str] = [
        "# gspc_proxy_lead probe — Round-1 H#2 falsifier",
        "",
        f"Generated: {today}  ",
        f"Window: {_WINDOW}d return-correlation; pooled across stocks × dates "
        f"from {_FIRE_MIN_DATE.isoformat()} onward.  ",
        f"Universe: {len(stocks)} active stocks; total bar-rows pooled: {total_pairs:,}",
        "",
        f"## Verdict: **{verdict}**",
        "",
        "## H#2 — Is `corr20(s, ^GSPC)` a different observable from `corr20(s, ^N225)`?",
        "",
        "Pre-registered kill threshold: |Pearson ρ| > 0.60 OR |Spearman ρ| > 0.60.",
        "",
        "| measure | value | p |",
        "|---|---|---|",
        f"| pooled Pearson ρ(corr_gspc, corr_n225) | **{pearson_r:+.4f}** | {pearson_p:.2e} |",
        f"| pooled Spearman ρ(corr_gspc, corr_n225) | **{spearman_r:+.4f}** | {spearman_p:.2e} |",
        "",
        f"- |Pearson| {abs(pearson_r):.3f} {'>' if abs(pearson_r) > _KILL_ABS_RHO else '≤'} 0.60",
        f"- |Spearman| {abs(spearman_r):.3f} {'>' if abs(spearman_r) > _KILL_ABS_RHO else '≤'} 0.60",
        "",
        "## H#1 — Cell-population sanity check",
        "",
        f"Proposed cell: corr_gspc ≥ {_PROP_GSPC_THRESH} AND |corr_n225| ≤ {_PROP_N225_THRESH}",
        "",
        f"- Bar-days in cell, pooled across universe: **{total_in_cell:,}** "
        f"({100.0 * total_in_cell / max(1, total_pairs):.2f}% of all bar-days)",
        f"- Stocks with at least one bar in the cell: **{n_stocks_with_any_cell}** "
        f"of {len(counts_df)} stocks evaluated",
        "",
        "These are bar-days, not entry events; the real event count after the "
        "ZigZag-LOW trigger would be much smaller. If bar-days < ~10,000 the "
        "downstream event count is unlikely to clear §5.1's n≥100 floor in any "
        "stratum.",
        "",
        "## Distribution snapshot",
        "",
        f"- corr_gspc:  mean {df['corr_gspc'].mean():+.3f}  std {df['corr_gspc'].std():.3f}  "
        f"q25 {df['corr_gspc'].quantile(0.25):+.3f}  q75 {df['corr_gspc'].quantile(0.75):+.3f}",
        f"- corr_n225:  mean {df['corr_n225'].mean():+.3f}  std {df['corr_n225'].std():.3f}  "
        f"q25 {df['corr_n225'].quantile(0.25):+.3f}  q75 {df['corr_n225'].quantile(0.75):+.3f}",
        "",
    ]

    out = _OUT_DIR / f"probe_h2_{today}.md"
    out.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote report to {}", out)
    print("\n".join(md))


if __name__ == "__main__":
    main()
