"""Drop-brk_tenkan_hi vs fill-order null: paired shuffles (read-only).

The LOO A/B (confluence_drop_tenkan_ab.py) showed dropping brk_tenkan_hi lifts the
deterministic stitched book +0.72 -> +1.05 with trade count PRESERVED (slot
saturation — selection change, not n-loss). But per-FY Δ sign-flips (FY2018 -1.05,
FY2023 -0.56 vs FY2025 +1.76) — the fingerprint of fill-order luck. A's +0.72 is a
known ~p44 draw of the +0.6..1.2 band; B may just be a luckier draw.

This is the binding test (cf. confluence_capacity_null.py, and the lesson that
PAIRED null > single-arm-vs-null killed ADX-priority p92 -> +0.029). Unlike capacity
(identical pool, perfect pairing), the two arms have DIFFERENT candidate pools, so
we pair by SHARED SEED: for each shuffle k, shuffle arm A's pool and arm B's pool
each with rng(k); record stitched Sharpe/return/maxDD per arm; Δ = B − A per seed.

Arm A: full 10-sign set.   Arm B: drop brk_tenkan_hi (9 signs).   Both 6-slot.

REAL if B's distribution sits above A net of fill-order luck: P(Δ>0) >= 0.95 AND
95% CI of paired Δ excludes 0. Otherwise the LOO gain is within fill-order noise.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_drop_tenkan_null
"""
from __future__ import annotations

import datetime
import math
import random
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.confluence_benchmark import _FYS
from src.analysis.confluence_capacity_null import (
    _closes, _fy_returns, _metrics, _sharpe,
)
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.simulator.cache import DataCache

_FULL = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
         "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_DROP = "brk_tenkan_hi"
_DROPSET = {k: v for k, v in _FULL.items() if k != _DROP}
_N_GATE = 3
_K = 200
_SLOTS = 6           # 6-slot production book (1 high + 5 low)
_LOW = 5


def _fires(signs):
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(signs)))).all()
    f = defaultdict(list)
    for sg, st, fa in rows:
        f[st].append((sg, fa.date() if hasattr(fa, "date") else fa))
    return f


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    fires_a = _fires(_FULL)
    fires_b = _fires(_DROPSET)
    exsim._MAX_LOW_CORR = _LOW

    # stitched daily series per (arm, shuffle k)
    st = {"A": [[] for _ in range(_K)], "B": [[] for _ in range(_K)]}

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
        se = cfg.end + datetime.timedelta(days=60)
        with get_session() as s:
            n225 = DataCache("^N225", "1d")
            n225.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                      datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
            caches = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    caches[code] = c
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        n_dts, _ = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        def _pool(bullish, fires):
            cbt._VALID_BARS = dict(bullish)
            out = []
            for code in caches:
                out += cbt._candidates_for_stock(
                    code, fires.get(code, []), caches[code],
                    corr_maps.get(code, {}), zs_maps.get(code, {}),
                    cfg.start, cfg.end, _N_GATE)
            return out

        pool_a = _pool(_FULL, fires_a)
        pool_b = _pool(_DROPSET, fires_b)

        for k in range(_K):
            ra, rb = random.Random(k), random.Random(k)
            pa, pb = pool_a[:], pool_b[:]
            ra.shuffle(pa); rb.shuffle(pb)
            st["A"][k] += _fy_returns(pa, caches, cfg, stock_dts, cal, _SLOTS)[1:]
            st["B"][k] += _fy_returns(pb, caches, cfg, stock_dts, cal, _SLOTS)[1:]
        logger.info("  {} done (A {} / B {} candidates, {} paired shuffles)",
                    cfg.label, len(pool_a), len(pool_b), _K)

    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in ("A", "B")}
    rt = {a: np.array([_metrics(st[a][k])[0] for k in range(_K)]) for a in ("A", "B")}
    dd = {a: np.array([_metrics(st[a][k])[2] for k in range(_K)]) for a in ("A", "B")}

    print("\n" + "=" * 82)
    print(f"DROP-{_DROP} vs FILL-ORDER NULL — {_K} paired shuffles, 6-slot book")
    print("=" * 82)
    print(f"\n{'arm':<22}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}"
          f"{'ret mean':>10}{'DD mean':>9}")
    for a, lbl in [("A", "full 10-sign"), ("B", f"drop {_DROP}")]:
        s_ = sh[a]
        print(f"{lbl:<22}{s_.mean():>13.2f}{s_.std():>7.2f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}"
              f"{rt[a].mean()*100:>9.0f}%{dd[a].mean()*100:>8.0f}%")

    d = sh["B"] - sh["A"]
    print(f"\n[paired Δ Sharpe = drop − full, same seed each draw]")
    print(f"  mean {d.mean():+.3f} | sd {d.std():.3f} | "
          f"95% CI [{np.percentile(d,2.5):+.3f}, {np.percentile(d,97.5):+.3f}]")
    print(f"  P(Δ > 0) = {(d > 0).mean():.3f}  ({int((d>0).sum())}/{_K} shuffles)")
    dr = rt["B"] - rt["A"]
    print(f"  paired Δ return mean {dr.mean()*100:+.0f}pp | P(Δ>0)={(dr>0).mean():.3f}")
    ddd = dd["B"] - dd["A"]
    print(f"  paired Δ maxDD mean {ddd.mean()*100:+.1f}pp (positive = drop has shallower DD)")

    verdict = ("REAL — drop-tenkan band sits above full net of fill-order luck"
               if (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
               else "NOT separated — LOO gain is within fill-order noise")
    print(f"\n  VERDICT: {verdict}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
