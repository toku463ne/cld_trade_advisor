"""Post-trigger no_supply/low-volume VETO vs fill-order null: paired shuffles (read-only).

BINDING test for project_confluence_no_supply_veto_nearmiss (Stage-0 near-miss: a confluence
trigger followed by no_supply/low-vol in its first ~2 days is weak; the realizable
delayed-confirmation veto lifted per-fire DR ~+2pp at W=2 with ~90% retention).  Per CLAUDE.md
Methodology, a per-fire edge must clear the paired fill-order null on the capital-aware 6-slot
book: P(Δ Sharpe>0) >= 0.95 AND 95% CI lower bound > 0.

The veto operates on the CONFLUENCE CANDIDATE pool (confluence formation identical across arms):
  BASE  : production candidates (enter open[F+1]).
  VR2   : VETO real (no_supply, W=2) — drop candidates with a no_supply bar (down & vmult<=0.7)
          in (F, F+2]; SURVIVORS delayed to enter at F+2 (open[F+3]) — the realizable policy.
  VLA2  : VETO look-ahead (no_supply, W=2) — drop the same vetoed candidates but keep survivors
          at F (NOT realizable; upper bound to bracket the delay cost).
  LV2   : VETO real (low_vol, W=2) — veto trigger = any bar vmult<=0.7 in (F, F+2]; survivors
          delayed to F+2.
Paired by shared seed (different pools → pair via rng(k)).  6-slot book, K=200.

REAL if a veto arm's Δ Sharpe vs BASE has P(Δ>0) >= 0.95 AND 95% CI excludes 0.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_no_supply_veto_null
"""
from __future__ import annotations

import datetime
import random
import sys

import numpy as np
import pandas as pd
from loguru import logger

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.confluence_benchmark import _FYS
from src.analysis.confluence_capacity_null import _closes, _fy_returns, _metrics, _sharpe
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.base import EntryCandidate
from src.simulator.cache import DataCache

_WINDOWED = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
             "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200
_SLOTS = 6
_W = 2
_V_DRY = 0.7
_VLOOK = 20


def _veto_feats(cache):
    """Per stock: (sorted dates, {date:idx}, close[], vmult[], ret1[])."""
    seen, dts, cl, vol = set(), [], [], []
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cl.append(b.close); vol.append(float(b.volume))
    cl = np.asarray(cl); vol = np.asarray(vol)
    ret1 = np.concatenate([[np.nan], cl[1:] / cl[:-1] - 1.0]) if len(cl) > 1 else np.array([np.nan])
    vavg = pd.Series(vol).rolling(_VLOOK).mean().shift(1).to_numpy()
    vmult = vol / np.where(vavg > 0, vavg, np.nan)
    idx = {d: i for i, d in enumerate(dts)}
    return dts, idx, cl, vmult, ret1


def _nosup(vm, r):
    return np.isfinite(vm) and np.isfinite(r) and vm <= _V_DRY and r < 0


def _lowvol(vm, r):
    return np.isfinite(vm) and vm <= _V_DRY


def _apply_veto(cands, feats, corr_maps, zs_maps, trigger, delay):
    out = []
    for cand in cands:
        ft = feats.get(cand.stock_code)
        if ft is None:
            out.append(cand); continue
        dts, idx, cl, vmult, ret1 = ft
        fi = idx.get(cand.entry_date)
        if fi is None:
            out.append(cand); continue
        hi = min(fi + _W, len(dts) - 1)
        vetoed = any(trigger(vmult[j], ret1[j]) for j in range(fi + 1, hi + 1))
        if vetoed:
            continue                              # drop the weak trigger
        if delay:
            ti = fi + _W
            if ti >= len(dts):
                continue                          # window runs off data → cannot confirm
            nd = dts[ti]
            out.append(EntryCandidate(
                stock_code=cand.stock_code, entry_date=nd, entry_price=float(cl[ti]),
                corr_mode=corr_maps.get(cand.stock_code, {}).get(nd, "mid"),
                corr_n225=0.0, zs_history=zs_maps.get(cand.stock_code, {}).get(nd, ())))
        else:
            out.append(cand)
    return out


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    cbt._VALID_BARS = dict(_WINDOWED)
    exsim._MAX_LOW_CORR = _SLOTS - 1
    fires = cbt._load_bullish_fires_by_stock()

    arms = ["BASE", "VR2", "VLA2", "LV2"]
    st = {a: [[] for _ in range(_K)] for a in arms}
    pool_sizes = {a: 0 for a in arms}

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=260)
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
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        feats = {code: _veto_feats(c) for code, c in caches.items()}
        n_dts, _ = _closes(n225)
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        base = []
        for code in caches:
            base += cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE)
        pools = {
            "BASE": base,
            "VR2": _apply_veto(base, feats, corr_maps, zs_maps, _nosup, delay=True),
            "VLA2": _apply_veto(base, feats, corr_maps, zs_maps, _nosup, delay=False),
            "LV2": _apply_veto(base, feats, corr_maps, zs_maps, _lowvol, delay=True),
        }
        for a in arms:
            pool_sizes[a] += len(pools[a])
        for k in range(_K):
            for a in arms:
                p = pools[a][:]
                random.Random(k).shuffle(p)
                st[a][k] += _fy_returns(p, caches, cfg, stock_dts, cal, _SLOTS)[1:]
        logger.info("  {} done (pools: {})", cfg.label, {a: len(pools[a]) for a in arms})

    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in arms}
    rt = {a: np.array([_metrics(st[a][k])[0] for k in range(_K)]) for a in arms}
    dd = {a: np.array([_metrics(st[a][k])[2] for k in range(_K)]) for a in arms}

    print("\n" + "=" * 88)
    print(f"POST-TRIGGER no_supply/low-vol VETO vs FILL-ORDER NULL — {_K} paired shuffles, "
          f"{_SLOTS}-slot, W={_W}")
    print("=" * 88)
    print("  VR2 = no_supply veto, survivors delayed to F+2 (REALIZABLE)")
    print("  VLA2 = no_supply veto, survivors kept at F (LOOK-AHEAD upper bound)")
    print("  LV2 = low_vol veto, survivors delayed to F+2 (REALIZABLE)")
    print(f"\n  total candidates over FYs: " +
          "  ".join(f"{a}={pool_sizes[a]}" for a in arms))
    print(f"\n{'arm':<8}{'Sharpe':>10}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}{'ret%':>8}{'DD%':>7}")
    for a in arms:
        s_ = sh[a]
        print(f"{a:<8}{s_.mean():>10.2f}{s_.std():>7.2f}{np.percentile(s_,5):>8.2f}"
              f"{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}"
              f"{rt[a].mean()*100:>7.0f}%{dd[a].mean()*100:>6.0f}%")

    for a in ["VR2", "VLA2", "LV2"]:
        d = sh[a] - sh["BASE"]; dr = rt[a] - rt["BASE"]
        sep = (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
        print(f"\n[{a} − BASE, paired]")
        print(f"  Δ Sharpe mean {d.mean():+.3f} | 95% CI [{np.percentile(d,2.5):+.3f}, "
              f"{np.percentile(d,97.5):+.3f}] | P(Δ>0)={(d>0).mean():.3f} | Δ ret {dr.mean()*100:+.1f}pp")
        print(f"  -> {'REAL (separated)' if sep else 'NOT separated'}")
    print()


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
