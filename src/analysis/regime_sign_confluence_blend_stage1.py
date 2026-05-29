"""Blend Stage 1 — capital-allocation paired fill-order null.

Backlog item 3 (`docs/analysis/regime_sign_improvement_backlog.md`). Stage 0
(`regime_sign_confluence_blend_stage0.py`) found ρ(regime_daily, confluence_daily)
= +0.554 with ~0.3% trade overlap and both books ~+1.0 Sharpe → diversification
available. Stage 1 asks the binding question: does **allocating the same ¥2M
across both books** beat **all-in on the better single book**?

Design — why it is NOT a selection rule (so not pre-killed by the fill-order null):
  - SINGLE arm  : Confluence (the better book, Stage-0 Sharpe +1.06), 6-slot,
                  daily portfolio return = Σ(position r)/6.
  - BLEND arm   : TWO half-capital 6-slot books → daily return = 0.5·reg + 0.5·conf.
                  Same total capital (fully invested) and — crucially — the SAME
                  total high-corr/beta exposure as one 6-slot book (each book's
                  1 high-corr slot at 1/6, halved → two 1/12 ≈ one 1/6). It costs
                  only operational footprint (≤12 names vs 6).
  This changes capital ALLOCATION across two streams, not which names fill slots,
  so the "selection dies on fill-order luck" lesson does not apply.

Paired null (per CLAUDE.md binding spec): K=200 seeds; each seed shuffles BOTH
books' within-day fill order; the SAME confluence order feeds both arms per seed
(perfect pairing on confluence's order luck). Gate vs the BETTER single book
(Confluence): P(Δ Sharpe > 0) ≥ 0.95 AND 95% CI lower bound > 0. Plus per-FY +
FY2025 OOS deterministic Δ for held-out stability.

Read-only. Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.regime_sign_confluence_blend_stage1
"""
from __future__ import annotations

import datetime
import math
import random
import statistics
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_slot_order import _BULLISH, _N_GATE
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import (
    EXIT_RULE,
    RS_FY_CONFIGS,
    _build_zs_map,
    build_fy_candidates,
)
from src.data.db import get_session
from src.exit.exit_simulator import _MAX_HIGH_CORR, _MAX_LOW_CORR, run_simulation
from src.simulator.cache import DataCache

_K = 200
_SLOTS = _MAX_HIGH_CORR + _MAX_LOW_CORR   # 6


def _closes(cache: DataCache):
    dts, cmap, seen = [], {}, set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cmap[d] = b.close
    dts.sort()
    return dts, cmap


def _pos_daily(p, dts, cmap):
    try:
        ie, ix = dts.index(p.entry_date), dts.index(p.exit_date)
    except ValueError:
        return {}
    out = {}
    if ie == ix:
        out[p.entry_date] = p.exit_price / p.entry_price - 1.0
        return out
    span = dts[ie:ix + 1]
    for k, d in enumerate(span):
        if k == 0:
            out[d] = cmap[d] / p.entry_price - 1.0
        elif d == p.exit_date:
            out[d] = p.exit_price / cmap[span[k - 1]] - 1.0
        else:
            out[d] = cmap[d] / cmap[span[k - 1]] - 1.0
    return out


def _daily(results, stock_dts, cal):
    cal_set = set(cal)
    dc = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                dc[d] += r / _SLOTS
    return [dc.get(d, 0.0) for d in cal]


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _total(rets):
    return float(np.prod([1.0 + r for r in rets]) - 1.0)


def _maxdd(rets):
    eq = np.cumprod(1.0 + np.asarray(rets))
    return float((eq / np.maximum.accumulate(eq) - 1.0).min()) if len(rets) else float("nan")


class _FyData:
    """Per-FY pre-built candidate pools + caches for both books."""
    def __init__(self, cfg):
        self.cfg = cfg
        cs = build_fy_candidates(cfg)
        self.ok = bool(cs.candidates) and cs.n225_cache is not None
        if not self.ok:
            return
        n_dts, _ = _closes(cs.n225_cache)
        self.cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        self.reg_cands = cs.candidates
        self.reg_caches = cs.stock_caches
        self.reg_stock_dts = {c: _closes(v) for c, v in cs.stock_caches.items()}

        # Confluence pool/caches (own universe)
        codes = cbt._stocks_for_fy(cfg.stock_set)
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
        se = cfg.end + datetime.timedelta(days=60)
        with get_session() as s:
            n225 = DataCache("^N225", "1d")
            n225.load(s,
                      datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                      datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
            cc = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s,
                       datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    cc[code] = c
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in cc.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in cc.items()}
        conf = []
        for code in cc:
            conf.extend(cbt._candidates_for_stock(
                code, _FIRES.get(code, []), cc[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}),
                cfg.start, cfg.end, _N_GATE))
        self.conf_cands = conf
        self.conf_caches = cc
        self.conf_stock_dts = {c: _closes(v) for c, v in cc.items()}

    def reg_daily(self, order):
        return _daily(run_simulation(order, EXIT_RULE, self.reg_caches, self.cfg.end),
                      self.reg_stock_dts, self.cal)

    def conf_daily(self, order):
        return _daily(run_simulation(order, cbt._EXIT_RULE, self.conf_caches, self.cfg.end),
                      self.conf_stock_dts, self.cal)


_FIRES: dict = {}


def _load_fires():
    cbt._VALID_BARS = dict(_BULLISH)
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    f = defaultdict(list)
    for sg, st, fa in rows:
        f[st].append((sg, fa.date() if hasattr(fa, "date") else fa))
    return f


def run() -> None:
    global _FIRES
    _FIRES = _load_fires()

    fydata = []
    for cfg in RS_FY_CONFIGS:
        fd = _FyData(cfg)
        if fd.ok:
            fydata.append(fd)
            logger.info("  built {} (reg {} cands / conf {} cands / {} days)",
                        cfg.label, len(fd.reg_cands), len(fd.conf_cands), len(fd.cal))

    # ── Shuffle null (paired) ───────────────────────────────────────────────
    single = [[] for _ in range(_K)]   # confluence-only
    blend = [[] for _ in range(_K)]    # 0.5 reg + 0.5 conf
    single_reg = [[] for _ in range(_K)]  # regime-only (context)

    for fd in fydata:
        for k in range(_K):
            rng = random.Random(k)
            rp = fd.reg_cands[:]; rng.shuffle(rp)
            cp = fd.conf_cands[:]; rng.shuffle(cp)
            rd = fd.reg_daily(rp)
            cd = fd.conf_daily(cp)
            single[k] += cd[1:]
            single_reg[k] += rd[1:]
            blend[k] += [0.5 * a + 0.5 * b for a, b in zip(rd, cd)][1:]
        logger.info("  {} shuffles done", fd.cfg.label)

    # ── Deterministic (entry-date order) + per-FY ───────────────────────────
    det_single, det_blend, det_reg = [], [], []
    per_fy = []
    for fd in fydata:
        rd = fd.reg_daily(sorted(fd.reg_cands, key=lambda c: c.entry_date))
        cd = fd.conf_daily(sorted(fd.conf_cands, key=lambda c: c.entry_date))
        bd = [0.5 * a + 0.5 * b for a, b in zip(rd, cd)]
        det_single += cd[1:]; det_reg += rd[1:]; det_blend += bd[1:]
        per_fy.append((fd.cfg.label, _sharpe(cd[1:]), _sharpe(bd[1:]),
                       _sharpe(cd[1:]) and _sharpe(bd[1:]) - _sharpe(cd[1:])))

    sh_single = np.array([_sharpe(s) for s in single])
    sh_blend = np.array([_sharpe(s) for s in blend])
    sh_reg = np.array([_sharpe(s) for s in single_reg])
    d_vs_conf = sh_blend - sh_single
    d_vs_reg = sh_blend - sh_reg

    print("\n" + "=" * 80)
    print(f"BLEND STAGE 1 — capital-allocation paired fill-order null ({_K} shuffles)")
    print(f"(FY2019–FY2025, blend = 0.5·RegimeSign + 0.5·Confluence, same ¥ + same beta)")
    print("=" * 80)
    print(f"\n{'arm':<22}{'Sharpe(mean)':>14}{'ret':>9}{'maxDD':>9}")
    for name, dist, sset in (("Confluence (single)", sh_single, single),
                             ("RegimeSign (single)", sh_reg, single_reg),
                             ("BLEND 50/50", sh_blend, blend)):
        rt = np.mean([_total(s) for s in sset])
        dd = np.mean([_maxdd(s) for s in sset])
        print(f"{name:<22}{dist.mean():>+14.3f}{rt*100:>8.0f}%{dd*100:>8.0f}%")

    def _ci(d):
        return np.percentile(d, 2.5), np.percentile(d, 97.5)

    print(f"\nΔ Sharpe BLEND − Confluence(better) : {d_vs_conf.mean():+.3f} "
          f"| P(Δ>0) {float((d_vs_conf > 0).mean()):.3f} "
          f"| 95% CI [{_ci(d_vs_conf)[0]:+.3f}, {_ci(d_vs_conf)[1]:+.3f}]")
    print(f"Δ Sharpe BLEND − RegimeSign         : {d_vs_reg.mean():+.3f} "
          f"| P(Δ>0) {float((d_vs_reg > 0).mean()):.3f} "
          f"| 95% CI [{_ci(d_vs_reg)[0]:+.3f}, {_ci(d_vs_reg)[1]:+.3f}]")

    print(f"\nDeterministic (entry-date order):")
    print(f"  Confluence {_sharpe(det_single):+.3f} / {_total(det_single)*100:+.0f}% / "
          f"{_maxdd(det_single)*100:.0f}%dd")
    print(f"  RegimeSign {_sharpe(det_reg):+.3f} / {_total(det_reg)*100:+.0f}% / "
          f"{_maxdd(det_reg)*100:.0f}%dd")
    print(f"  BLEND      {_sharpe(det_blend):+.3f} / {_total(det_blend)*100:+.0f}% / "
          f"{_maxdd(det_blend)*100:.0f}%dd  "
          f"(Δ vs Confluence {_sharpe(det_blend)-_sharpe(det_single):+.3f})")

    print(f"\nPer-FY deterministic Sharpe (Confluence → BLEND, Δ):")
    for label, csh, bsh, _ in per_fy:
        print(f"  {label}: {csh:+.2f} → {bsh:+.2f}  ({bsh-csh:+.2f})")

    print("\n" + "-" * 80)
    lo = _ci(d_vs_conf)[0]
    if float((d_vs_conf > 0).mean()) >= 0.95 and lo > 0:
        v = "PASS — blend beats the better single book on the binding gate."
    elif d_vs_conf.mean() > 0:
        v = (f"LEAN-YES but FAILS the binding gate (P={float((d_vs_conf>0).mean()):.2f}, "
             f"CI-lo {lo:+.3f}). Diversification is real but not separated at this sample.")
    else:
        v = "REJECT — blend does not beat the better single book."
    print("VERDICT (vs Confluence):", v)
    print("(Operational caveat: BLEND holds up to 12 names vs 6 — heavier manual book.)")
    print("-" * 80)


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
