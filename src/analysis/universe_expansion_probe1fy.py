"""1-FY memory + contention probe for the expanded universe (read-only).

Before the full Stage-1 null: confirm (a) building the expanded ACTIVE confluence book for ONE
FY (loading ~2,600 fired-code DataCaches) FITS IN MEMORY — the rebuild already OOM'd once — and
(b) expansion actually INCREASES contention (more candidates / filled trades than the 225).

Mirrors confluence_benchmark's per-FY block for FY2024 on classifiedexp2023, logs peak RSS and
candidate/trade counts, vs the 225 (classified2023). If 1 FY fits, the FY-by-FY null is safe
(peak memory = one FY, freed between).

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.universe_expansion_probe1fy
"""
from __future__ import annotations

import datetime
import resource
import sys
from collections import defaultdict

from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_benchmark import _BULLISH, _N_GATE, _closes
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_UTC = datetime.timezone.utc


def _rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)   # KB→GB on linux


def _build_fy(cluster_set: str, start: datetime.date, end: datetime.date) -> tuple[int, int, int]:
    """Build the active confluence book for one FY. Returns (n_codes, n_candidates, n_trades)."""
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    codes = cbt._stocks_for_fy(cluster_set)
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.stock_set == cluster_set,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    ss = start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
    se = end + datetime.timedelta(days=60)
    ssd = datetime.datetime.combine(ss, datetime.time.min, tzinfo=_UTC)
    sed = datetime.datetime.combine(se, datetime.time.max, tzinfo=_UTC)
    with get_session() as s:
        n225 = DataCache("^N225", "1d"); n225.load(s, ssd, sed)
        caches = {}
        for code in codes:
            c = DataCache(code, "1d"); c.load(s, ssd, sed)
            if c.bars:
                caches[code] = c
        logger.info("  {} caches loaded | peak RSS {:.2f} GB", len(caches), _rss_gb())
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        logger.info("  corr+zs maps built | peak RSS {:.2f} GB", _rss_gb())
    cands = []
    for code in caches:
        cands += cbt._candidates_for_stock(code, fires.get(code, []), caches[code],
                                            corr_maps[code], zs_maps[code], start, end, _N_GATE)
    results = run_simulation(cands, cbt._EXIT_RULE, caches, end)
    logger.info("  candidates {} | filled trades {} | peak RSS {:.2f} GB",
                len(cands), len(results), _rss_gb())
    return len(caches), len(cands), len(results)


def run() -> None:
    print("\n" + "=" * 78)
    print("1-FY MEMORY + CONTENTION PROBE — FY2024, expanded vs 225")
    print("=" * 78)
    logger.info("225 baseline (classified2023) …")
    c0, cand0, t0 = _build_fy("classified2023", datetime.date(2024, 4, 1), datetime.date(2025, 3, 31))
    logger.info("EXPANDED (classifiedexp2023) …")
    c1, cand1, t1 = _build_fy("classifiedexp2023", datetime.date(2024, 4, 1), datetime.date(2025, 3, 31))
    print(f"\n  {'book':<12}{'codes':>8}{'candidates':>12}{'filled':>9}")
    print(f"  {'225':<12}{c0:>8}{cand0:>12}{t0:>9}")
    print(f"  {'expanded':<12}{c1:>8}{cand1:>12}{t1:>9}")
    print(f"\n  contention: expanded has {cand1/max(cand0,1):.1f}× the candidates, "
          f"{t1/max(t0,1):.1f}× the filled trades of the 225")
    print(f"  PEAK RSS for one expanded FY: {_rss_gb():.2f} GB "
          f"({'FITS — FY-by-FY null is safe' if _rss_gb() < 5.0 else 'TIGHT — null must chunk/stream'})")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
