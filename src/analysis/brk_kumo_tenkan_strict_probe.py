"""brk_kumo_tenkan_strict_probe — sweep gate_lookback K ∈ {1, 5} for
brk_kumo and brk_tenkan detectors.

Operator (2026-05-18) requested evaluating the strict K=5 variant of
the breakout transition gate:

  K=1 (current production):
    fire[T] iff low[T] > level[T] AND low[T-1] ≤ level[T-1]
  K=5 (strict):
    fire[T] iff low[T] > level[T] AND low[T-i] ≤ level[T-i] for ALL i ∈ 1..5

Strict K=5 requires 5 consecutive prior bars all on the opposite side
before the breakout, making each fire a "definitive" stage change with
no recent chop.

Output: side-by-side canonical-style numbers (fires + DR + bench_flw)
per (sign, side, K, FY).  Reads OHLCV from devenv DB.  Does NOT write
to DB.  Appends results section to `docs/analysis/ichimoku_signs.md`.

Reusable helpers borrowed from `sign_benchmark.py`:
  _first_zigzag_peak — windowed 35-bar lookahead zigzag detection.
"""
from __future__ import annotations

import datetime
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from src.analysis.models import StockClusterMember, StockClusterRun
from src.analysis.sign_benchmark import _first_zigzag_peak
from src.analysis.sign_benchmark_multiyear import FY_CONFIG
from src.data.db import get_session
from src.signs import BrkKumoDetector, BrkTenkanDetector
from src.simulator.cache import DataCache

# Match sign_benchmark.py defaults
_TREND_CAP   = 30
_ZZ_SIZE     = 5
_ZZ_MID_SIZE = 5

_K_VALUES   = [1, 5]
_DETECTORS  = [
    ("brk_kumo",   BrkKumoDetector),
    ("brk_tenkan", BrkTenkanDetector),
]
_SIDES = ["hi", "lo"]

_DOC_PATH      = Path("docs/analysis/ichimoku_signs.md")
_PROBE_HEADER  = "## Strict-K probe (2026-05-18)"


@dataclass
class _Stats:
    fy:     str
    sign:   str
    side:   str
    K:      int
    n:      int
    n_win:  int
    dr:     float
    mean_r: float


def _utc(dt_str: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(dt_str).replace(
        tzinfo=datetime.timezone.utc)


def _load_cluster(session, cluster_year: str) -> list[str]:
    fy_label = f"classified{cluster_year}"
    run = session.execute(
        select(StockClusterRun).where(StockClusterRun.fiscal_year == fy_label)
    ).scalar_one_or_none()
    if run is None:
        return []
    return list(session.execute(
        select(StockClusterMember.stock_code)
        .where(StockClusterMember.run_id == run.id,
               StockClusterMember.is_representative.is_(True))
    ).scalars().all())


def _run_fy_sign_side_K(
    fy_label: str, start: datetime.datetime, end: datetime.datetime,
    sign_module: str, det_cls, side: str, K: int,
    stock_caches: dict[str, DataCache],
) -> _Stats:
    n = 0; n_win = 0; total_r = 0.0
    for code, cache in stock_caches.items():
        if not cache.bars:
            continue
        det = det_cls(cache, side=side, gate_lookback=K)
        # Find fires within FY window
        # _fire_events: list[(bar_idx, score)]
        for bar_idx, _score in det._fire_events:
            fired_at = cache.bars[bar_idx].dt
            if fired_at < start or fired_at > end:
                continue
            # Outcome via windowed zigzag (lookahead)
            trend_dir, _trend_bars, magnitude = _first_zigzag_peak(
                fired_at,
                cache.bars,
                cap=_TREND_CAP,
                zz_size=_ZZ_SIZE,
                zz_mid_size=_ZZ_MID_SIZE,
            )
            if trend_dir is None:
                continue
            # DR convention: HIGH=+1 → long wins
            # Per-fire return = trend_dir * magnitude (signed long-return)
            n += 1
            r = trend_dir * magnitude
            total_r += r
            if trend_dir > 0:
                n_win += 1
    dr     = n_win / n if n else 0.0
    mean_r = total_r / n if n else 0.0
    return _Stats(
        fy=fy_label, sign=sign_module, side=side, K=K,
        n=n, n_win=n_win, dr=dr, mean_r=mean_r,
    )


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    rows: list[_Stats] = []
    for fy_label, start_str, end_str, cluster_year in FY_CONFIG:
        start = _utc(start_str)
        end   = _utc(end_str)
        with get_session() as s:
            codes = _load_cluster(s, cluster_year)
        if not codes:
            logger.warning("{} no cluster, skip", fy_label)
            continue

        # Load caches once per FY (lookahead for zigzag = +60 days)
        cache_start = start - datetime.timedelta(days=120)  # for K=5 lookback + ichimoku
        cache_end   = end   + datetime.timedelta(days=60)
        stock_caches: dict[str, DataCache] = {}
        with get_session() as s:
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, cache_start, cache_end)
                if c.bars:
                    stock_caches[code] = c
        logger.info("{}: loaded {} stock caches", fy_label, len(stock_caches))

        for sign_module, det_cls in _DETECTORS:
            for side in _SIDES:
                for K in _K_VALUES:
                    r = _run_fy_sign_side_K(
                        fy_label, start, end,
                        sign_module, det_cls, side, K,
                        stock_caches,
                    )
                    logger.info(
                        "  {}/{}_{} K={}: n={}, DR={:.1%}, mean_r={:+.2%}",
                        fy_label, sign_module, side, K, r.n, r.dr, r.mean_r,
                    )
                    rows.append(r)

    # ── Report ────────────────────────────────────────────────────────
    today = datetime.date.today().isoformat()
    lines: list[str] = [
        "",
        _PROBE_HEADER,
        "",
        f"Probe run: {today}.  Strict K=5 fires require the 5 prior bars to "
        "all be on the opposite side of the level (low ≤ level for hi, "
        "high ≥ level for lo) before today's whole-bar breakout.",
        "",
    ]

    # Side-by-side per (sign, side) — K=1 vs K=5
    for sign in ("brk_kumo", "brk_tenkan"):
        for side in _SIDES:
            lines += [
                f"### {sign}_{side} — K=1 (current) vs K=5 (strict)",
                "",
                "| FY | K=1 n | K=1 DR | K=1 mean_r | K=5 n | K=5 DR | K=5 mean_r | Δn | Δ DR | Δ mean_r |",
                "|----|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
            by_fy_K = {(r.fy, r.K): r for r in rows
                       if r.sign == sign and r.side == side}
            for fy_label, _s, _e, _c in FY_CONFIG:
                k1 = by_fy_K.get((fy_label, 1))
                k5 = by_fy_K.get((fy_label, 5))
                if k1 is None or k5 is None:
                    continue
                d_n  = k5.n - k1.n
                d_dr = k5.dr - k1.dr
                d_r  = k5.mean_r - k1.mean_r
                lines.append(
                    f"| {fy_label} | {k1.n} | {k1.dr*100:.1f}% | {k1.mean_r*100:+.2f}%"
                    f" | {k5.n} | {k5.dr*100:.1f}% | {k5.mean_r*100:+.2f}%"
                    f" | {d_n:+} | {d_dr*100:+.1f}pp | {d_r*100:+.2f}pp |"
                )
            # Pooled
            k1_rows = [r for r in rows if r.sign == sign and r.side == side and r.K == 1]
            k5_rows = [r for r in rows if r.sign == sign and r.side == side and r.K == 5]
            k1_n = sum(r.n for r in k1_rows); k5_n = sum(r.n for r in k5_rows)
            k1_w = sum(r.n_win for r in k1_rows); k5_w = sum(r.n_win for r in k5_rows)
            k1_r = (sum(r.n * r.mean_r for r in k1_rows) / k1_n) if k1_n else 0.0
            k5_r = (sum(r.n * r.mean_r for r in k5_rows) / k5_n) if k5_n else 0.0
            k1_dr = k1_w / k1_n if k1_n else 0.0
            k5_dr = k5_w / k5_n if k5_n else 0.0
            lines.append(
                f"| **Pooled** | **{k1_n}** | **{k1_dr*100:.1f}%** | **{k1_r*100:+.2f}%**"
                f" | **{k5_n}** | **{k5_dr*100:.1f}%** | **{k5_r*100:+.2f}%**"
                f" | {k5_n - k1_n:+} | {(k5_dr - k1_dr)*100:+.1f}pp"
                f" | {(k5_r - k1_r)*100:+.2f}pp |"
            )
            lines.append("")

    report = "\n".join(lines)
    print(report)

    # Append (or replace) the probe section in ichimoku_signs.md
    if _DOC_PATH.exists():
        existing = _DOC_PATH.read_text()
        if _PROBE_HEADER in existing:
            idx = existing.index(_PROBE_HEADER)
            # truncate at next "## " or EOF
            rest = existing[idx + len(_PROBE_HEADER):]
            nxt = rest.find("\n## ")
            existing = (existing[:idx].rstrip() + "\n") if nxt < 0 \
                       else (existing[:idx].rstrip() + "\n" + rest[nxt:].lstrip("\n"))
        _DOC_PATH.write_text(existing.rstrip() + "\n" + report)
        logger.info("Appended probe results to {}", _DOC_PATH)


if __name__ == "__main__":
    main()
