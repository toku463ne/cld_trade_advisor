"""brk_wall_k_sweep — sweep K ∈ {10,15,20,30} for brk_wall + score calibration.

Operator request (2026-05-18): "want to tune brk_wall's possible parameters.
ex: K=20.  Also want to know if the score is informative."

For each K:
  1. Compute fires per stock per FY using BrkWallDetector(K=K)
  2. Per-fire outcome via windowed zigzag (_first_zigzag_peak)
  3. Aggregate DR + EV per FY
  4. Score calibration:
     - Pooled Spearman ρ of (score, signed_return)
     - 4-quartile EV table to see if top-quartile score wins

Read-only.  Output: docs/analysis/brk_wall_tuning.md.
"""
from __future__ import annotations

import datetime
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from loguru import logger
from scipy.stats import spearmanr
from sqlalchemy import select

from src.analysis.models import StockClusterMember, StockClusterRun
from src.analysis.sign_benchmark import _first_zigzag_peak
from src.analysis.sign_benchmark_multiyear import FY_CONFIG
from src.data.db import get_session
from src.signs import BrkWallDetector
from src.simulator.cache import DataCache

_TREND_CAP   = 30
_ZZ_SIZE     = 5
_ZZ_MID_SIZE = 5

_K_VALUES = [10, 15, 20, 30]

_DOC_PATH = Path("docs/analysis/brk_wall_tuning.md")


@dataclass
class _Fire:
    fy: str; K: int; score: float; signed_r: float; trend_dir: int


@dataclass
class _FyStat:
    fy: str; K: int
    n: int; n_win: int; dr: float; mean_r: float


def _utc(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc)


def _load_cluster(session, cluster_year: str) -> list[str]:
    fy = f"classified{cluster_year}"
    run = session.execute(
        select(StockClusterRun).where(StockClusterRun.fiscal_year == fy)
    ).scalar_one_or_none()
    if run is None:
        return []
    return list(session.execute(
        select(StockClusterMember.stock_code)
        .where(StockClusterMember.run_id == run.id,
               StockClusterMember.is_representative.is_(True))
    ).scalars().all())


def _run_fy_K(fy_label, start, end, K, stock_caches) -> tuple[_FyStat, list[_Fire]]:
    fires: list[_Fire] = []
    for cache in stock_caches.values():
        if not cache.bars:
            continue
        det = BrkWallDetector(cache, K=K)
        for bar_idx, score in det._fire_events:
            fired_at = cache.bars[bar_idx].dt
            if fired_at < start or fired_at > end:
                continue
            trend_dir, _bars, magnitude = _first_zigzag_peak(
                fired_at, cache.bars, cap=_TREND_CAP,
                zz_size=_ZZ_SIZE, zz_mid_size=_ZZ_MID_SIZE,
            )
            if trend_dir is None:
                continue
            r = trend_dir * magnitude
            fires.append(_Fire(fy=fy_label, K=K, score=score, signed_r=r, trend_dir=trend_dir))
    n     = len(fires)
    n_win = sum(1 for f in fires if f.trend_dir > 0)
    mean_r = sum(f.signed_r for f in fires) / n if n else 0.0
    stat = _FyStat(fy=fy_label, K=K, n=n, n_win=n_win,
                   dr=n_win/n if n else 0.0, mean_r=mean_r)
    return stat, fires


def _quartile_table(scores: list[float], rs: list[float]) -> list[tuple[int, int, float, float]]:
    """Return [(q, n, mean_r, dr), ...] where q is 1..4 (Q1=lowest score)."""
    if not scores:
        return []
    arr_s = np.array(scores); arr_r = np.array(rs)
    # Use rank-based quartile to handle ties
    n = len(arr_s)
    order = np.argsort(arr_s)
    sorted_s = arr_s[order]; sorted_r = arr_r[order]
    out = []
    for q in range(4):
        lo = q * n // 4
        hi = (q + 1) * n // 4
        if hi <= lo:
            continue
        chunk = sorted_r[lo:hi]
        n_q  = len(chunk)
        dr_q = float((chunk > 0).mean())
        mr_q = float(chunk.mean())
        out.append((q + 1, n_q, mr_q, dr_q))
    return out


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    stats: list[_FyStat] = []
    all_fires: list[_Fire] = []

    for fy_label, start_s, end_s, cluster_year in FY_CONFIG:
        start = _utc(start_s); end = _utc(end_s)
        with get_session() as s:
            codes = _load_cluster(s, cluster_year)
        if not codes:
            continue

        cache_start = start - datetime.timedelta(days=180)  # need lookback for walls
        cache_end   = end   + datetime.timedelta(days=60)
        stock_caches: dict[str, DataCache] = {}
        with get_session() as s:
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, cache_start, cache_end)
                if c.bars:
                    stock_caches[code] = c
        logger.info("{} {} stock caches", fy_label, len(stock_caches))

        for K in _K_VALUES:
            stat, fires = _run_fy_K(fy_label, start, end, K, stock_caches)
            logger.info("  K={}: n={}, DR={:.1%}, mean_r={:+.2%}",
                        K, stat.n, stat.dr, stat.mean_r)
            stats.append(stat)
            all_fires.extend(fires)

    # ── Format report ────────────────────────────────────────────────
    today = datetime.date.today().isoformat()
    lines = [
        f"# brk_wall K-sweep + score calibration ({today})",
        "",
        "Operator request (2026-05-18): tune brk_wall's K parameter and "
        "check if the sign_score is informative.  Production currently uses "
        "K=10, theta=0.05, lookback=120.",
        "",
        "Score formula: `(close − wall) / wall`, capped at 5% (so score ∈ "
        "[0, 1] where 1 = breakout ≥5% above wall).",
        "",
        "## Per-FY DR + EV by K",
        "",
        "| FY | K | n | DR | mean_r |",
        "|----|---|---:|---:|---:|",
    ]
    for fy_label, _s, _e, _c in FY_CONFIG:
        for K in _K_VALUES:
            st = next((s for s in stats if s.fy == fy_label and s.K == K), None)
            if st is None:
                continue
            lines.append(
                f"| {st.fy} | K={K} | {st.n} | {st.dr*100:.1f}%"
                f" | {st.mean_r*100:+.2f}% |"
            )

    # Pooled across all FYs
    lines += [
        "",
        "## Pooled across FYs",
        "",
        "| K | total_n | pooled DR | pooled mean_r |",
        "|---|---:|---:|---:|",
    ]
    for K in _K_VALUES:
        k_stats = [s for s in stats if s.K == K]
        total_n = sum(s.n for s in k_stats)
        total_w = sum(s.n_win for s in k_stats)
        weighted_r = (sum(s.n * s.mean_r for s in k_stats) / total_n) if total_n else 0.0
        dr = total_w / total_n if total_n else 0.0
        lines.append(
            f"| K={K} | **{total_n}** | **{dr*100:.1f}%** | **{weighted_r*100:+.2f}%** |"
        )

    # Score calibration per K
    lines += [
        "",
        "## Score informativeness",
        "",
        "Pooled across all FYs.  Spearman ρ of (score, signed_return) "
        "tests whether higher scores predict better signed returns.  "
        "Quartile table splits fires by score (Q1=lowest) and shows DR + "
        "mean_r per quartile — if scores are informative, Q4 should beat Q1.",
        "",
        "### Spearman ρ (score vs signed_return)",
        "",
        "| K | n | Spearman ρ | p |",
        "|---|---:|---:|---:|",
    ]
    for K in _K_VALUES:
        k_fires = [f for f in all_fires if f.K == K]
        if len(k_fires) < 30:
            lines.append(f"| K={K} | {len(k_fires)} | — | (n too small) |")
            continue
        scores = [f.score for f in k_fires]
        rs     = [f.signed_r for f in k_fires]
        rho, p = spearmanr(scores, rs)
        lines.append(
            f"| K={K} | {len(k_fires)} | **{rho:+.3f}** | {p:.3f} |"
        )

    lines += [
        "",
        "### Quartile EV (Q1=lowest score, Q4=highest)",
        "",
    ]
    for K in _K_VALUES:
        k_fires = [f for f in all_fires if f.K == K]
        if len(k_fires) < 40:
            lines.append(f"K={K}: only {len(k_fires)} fires — skipping quartile table.")
            lines.append("")
            continue
        rows = _quartile_table([f.score for f in k_fires],
                                [f.signed_r for f in k_fires])
        lines.append(f"**K={K}** (total n={len(k_fires)}):")
        lines.append("")
        lines.append("| Quartile | n | DR | mean_r |")
        lines.append("|----------|---:|---:|---:|")
        for q, n_q, mr_q, dr_q in rows:
            lines.append(f"| Q{q} | {n_q} | {dr_q*100:.1f}% | {mr_q*100:+.2f}% |")
        # Q4-Q1 spread
        if len(rows) == 4:
            q1_mr = rows[0][2]; q4_mr = rows[3][2]
            spread = (q4_mr - q1_mr) * 100
            verdict = "INFORMATIVE" if spread > 2.0 else ("WEAK" if spread > 0.5 else "NOISE")
            lines.append(f"")
            lines.append(f"  Q4−Q1 mean_r spread: **{spread:+.2f}pp** — **{verdict}**")
        lines.append("")

    report = "\n".join(lines)
    print(report)

    _DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DOC_PATH.write_text(report)
    logger.info("Wrote report to {}", _DOC_PATH)


if __name__ == "__main__":
    main()
