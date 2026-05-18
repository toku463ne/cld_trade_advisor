"""brk_sma_variant_probe — evaluate operator's brk_sma variant (2026-05-18).

Operator request: evaluate "low[T] > sma[T] AND low[T-i] ≤ sma[T-i] for
all i ∈ {1, 2, 3}" — i.e., switch the cross from close to low (strict
whole-bar) AND reduce the prior-bar lookback from K=5 to K=3.

Current production: close + K=5 + 1.5× volume gate.
Operator variant : low   + K=3 + 1.5× volume gate (vol preserved).

Plus two diagnostic controls so we can attribute any uplift:
  - low + K=5   (control: just the close→low swap)
  - close + K=3 (control: just the K=5→K=3 swap)

Output: per-FY canonical-style DR + EV table per arm, appended to
`docs/analysis/brk_sma_variant.md`.

Reuses sign_benchmark._first_zigzag_peak for outcome determination.
"""
from __future__ import annotations

import datetime
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from src.analysis.models import StockClusterMember, StockClusterRun
from src.analysis.sign_benchmark import _first_zigzag_peak
from src.analysis.sign_benchmark_multiyear import FY_CONFIG
from src.data.db import get_session
from src.signs import BrkSmaDetector
from src.simulator.cache import DataCache

_TREND_CAP   = 30
_ZZ_SIZE     = 5
_ZZ_MID_SIZE = 5

# (label, gate_use_low, min_below_bars)
_ARMS = [
    ("close,K=5 [production]", False, 5),
    ("low,K=3   [operator]  ", True,  3),
    ("low,K=5   [control]   ", True,  5),
    ("close,K=3 [control]   ", False, 3),
]

_DOC_PATH = Path("docs/analysis/brk_sma_variant.md")


@dataclass
class _Stats:
    fy: str; arm: str
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


def _run_arm(fy_label, start, end, arm_label, gate_use_low, K, stock_caches):
    n = 0; n_win = 0; total_r = 0.0
    for cache in stock_caches.values():
        if not cache.bars:
            continue
        det = BrkSmaDetector(cache, window=20, min_below_bars=K,
                             gate_use_low=gate_use_low)
        for bar_idx, _score in det._fire_events:
            fired_at = cache.bars[bar_idx].dt
            if fired_at < start or fired_at > end:
                continue
            trend_dir, _bars, magnitude = _first_zigzag_peak(
                fired_at, cache.bars, cap=_TREND_CAP,
                zz_size=_ZZ_SIZE, zz_mid_size=_ZZ_MID_SIZE,
            )
            if trend_dir is None:
                continue
            n += 1
            r = trend_dir * magnitude
            total_r += r
            if trend_dir > 0:
                n_win += 1
    return _Stats(
        fy=fy_label, arm=arm_label,
        n=n, n_win=n_win,
        dr=n_win / n if n else 0.0,
        mean_r=total_r / n if n else 0.0,
    )


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    rows: list[_Stats] = []
    for fy_label, start_s, end_s, cluster_year in FY_CONFIG:
        start = _utc(start_s); end = _utc(end_s)
        with get_session() as s:
            codes = _load_cluster(s, cluster_year)
        if not codes:
            logger.warning("{} no cluster", fy_label); continue

        cache_start = start - datetime.timedelta(days=60)
        cache_end   = end   + datetime.timedelta(days=60)
        stock_caches: dict[str, DataCache] = {}
        with get_session() as s:
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, cache_start, cache_end)
                if c.bars:
                    stock_caches[code] = c
        logger.info("{} {} stock caches", fy_label, len(stock_caches))

        for arm_label, gate_use_low, K in _ARMS:
            r = _run_arm(fy_label, start, end, arm_label, gate_use_low, K, stock_caches)
            logger.info("  {} n={}, DR={:.1%}, mean_r={:+.2%}",
                        arm_label, r.n, r.dr, r.mean_r)
            rows.append(r)

    # ── Report ────────────────────────────────────────────────────────
    today = datetime.date.today().isoformat()
    lines = [
        f"# brk_sma variant probe ({today})",
        "",
        "Operator request (2026-05-18): evaluate `low[T] > sma[T] AND "
        "low[T-i] ≤ sma[T-i] for i ∈ {1,2,3}` — i.e., **low-based strict "
        "whole-bar** cross with **K=3** prior-bar lookback.",
        "",
        "Current production: `close, K=5, vol_mult=1.5`.",
        "All arms preserve the volume gate (1.5× rolling-mean) for "
        "consistency; only cross-edge (close vs low) and K (5 vs 3) vary.",
        "",
        "### Per-FY results",
        "",
        "| FY | arm | n | DR | mean_r |",
        "|----|-----|---:|---:|---:|",
    ]
    for fy_label, _s, _e, _c in FY_CONFIG:
        for arm_label, _gul, _K in _ARMS:
            r = next((r for r in rows if r.fy == fy_label and r.arm == arm_label), None)
            if r is None:
                continue
            lines.append(
                f"| {r.fy} | `{r.arm.strip()}` | {r.n} | {r.dr*100:.1f}%"
                f" | {r.mean_r*100:+.2f}% |"
            )

    # Pooled
    lines += [
        "",
        "### Pooled (FY2018–FY2025)",
        "",
        "| arm | total_n | pooled DR | pooled mean_r |",
        "|-----|---:|---:|---:|",
    ]
    for arm_label, _gul, _K in _ARMS:
        arm_rows = [r for r in rows if r.arm == arm_label]
        total_n = sum(r.n for r in arm_rows)
        total_w = sum(r.n_win for r in arm_rows)
        weighted_r = (sum(r.n * r.mean_r for r in arm_rows) / total_n) if total_n else 0.0
        dr = total_w / total_n if total_n else 0.0
        lines.append(
            f"| `{arm_label.strip()}` | **{total_n}** | **{dr*100:.1f}%**"
            f" | **{weighted_r*100:+.2f}%** |"
        )

    lines.append("")
    report = "\n".join(lines)
    print(report)

    _DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DOC_PATH.write_text(report)
    logger.info("Wrote report to {}", _DOC_PATH)


if __name__ == "__main__":
    main()
