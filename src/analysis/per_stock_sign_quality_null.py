"""Per-stock sign-quality as a candidate PRIORITY vs the fill-order null — 6-slot book.

Binding test for the operator's idea (2026-06-28): at a confluence trigger, look at the
individual signs valid for THAT stock and prefer to enter candidates whose valid signs have
a GOOD trailing track record on that specific stock; de-prioritize / skip the rest ("go to
another candidate").

Premise PASSED at Stage-0 (per_stock_sign_quality_stage0.py): trailing per-(stock,sign)
quality predicts forward per-(stock,sign) return — monotone, positive in all 7 FYs, survives
stripping the global sign effect; leave-one-sign-out shows ~half is a real stock x sign
interaction.  But per-fire edge is NOT the portfolio metric.  Every selection/ordering rule —
including the PEAD score-booster, whose key was real, exogenous and cross-sectionally
validated (+2.51% cohort) — has DIED at this null because reordering rarely changes which 6
of the contention names actually fill (confluence_pead_boost_null.py: Δ +0.016, P=0.495).

Quality key (look-ahead-safe, deployable):
  * candidate at signal day T on stock X with valid sign-set S(T).
  * for each s in S(T): q_s = mean forward-h10 return of PRIOR fires of (X, s) whose forward
    window already RESOLVED by T (fire_idx + 1 + H <= idx(T)); need >= MIN_PRIOR such fires.
  * candidate quality = mean of q_s over valid signs with enough resolved history; if none
    qualify -> None (neutral, sorts at the median, never vetoed).

Arms (K paired shuffles, ONE shared pool, same Random(k) fill order per seed):
  A random      : run_simulation(shuffle)
  B good-first  : stable-sort the shuffle by quality DESC (None -> median) — upper bound of
                  any "prefer good signs" priority; adds/drops ZERO candidates.
  C veto-neg    : drop candidates whose quality < 0 (negative trailing track record on ALL
                  their valid signs); keep None/positive — the literal "go to another candidate".

Binding gate (B): P(Δ Sharpe > 0) >= 0.95 AND 95% CI lower bound > 0.  Capital-aware r/6.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.per_stock_sign_quality_null
"""
from __future__ import annotations

import datetime
import math
import random
import statistics
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200
_LOW = 5            # production 6-slot book = 1 high + 5 low
_H = 10            # horizon for the trailing quality key
_MIN_PRIOR = 3     # resolved prior fires of (stock,sign) needed to score that sign
_QCACHE = ("/tmp/claude-1000/-home-ubuntu-cld-trade-advisor/"
           "6663b296-30c5-4b1a-89da-a580ff2781bc/scratchpad/per_stock_sign_events.pkl")


def _closes(cache):
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


def _metrics(rets):
    if len(rets) < 2:
        return float("nan"), float("nan"), float("nan")
    eq = np.cumprod(1.0 + np.asarray(rets))
    sd = statistics.stdev(rets)
    sh = statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")
    runmax = np.maximum.accumulate(eq)
    return float(eq[-1] - 1.0), sh, float((eq / runmax - 1.0).min())


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _fy_returns(pool, caches, cfg, stock_dts, cal, n_slots):
    cal_set = set(cal)
    results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
    day_contrib: defaultdict[datetime.date, float] = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_contrib[d] += r / n_slots
    return [day_contrib.get(d, 0.0) for d in cal], results


def _build_cands(fires, caches, corr_maps, zs_maps, cfg):
    out = []
    for code in caches:
        out.extend(cbt._candidates_for_stock(
            code, fires.get(code, []), caches[code],
            corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
    return out


def _load_qmap():
    """{(stock,sign): sorted [(signal_date, h{_H}_return)]} from the Stage-0 event cache."""
    df = pd.read_pickle(_QCACHE)
    col = f"h{_H}"
    qmap: dict[tuple[str, str], list[tuple[datetime.date, float]]] = defaultdict(list)
    for code, sign, d, r in zip(df["code"], df["sign"], df["date"], df[col]):
        qmap[(code, sign)].append((pd.Timestamp(d).date(), float(r)))
    for k in qmap:
        qmap[k].sort()
    return qmap


def _candidate_quality(cands, base_fires, stock_dts, qmap):
    """{(stock, entry_date): quality or None}.  Look-ahead-safe per-valid-sign track record."""
    out: dict[tuple[str, datetime.date], float | None] = {}
    # per-stock: date->idx, and resolved-fire index lists per sign
    for code in {c.stock_code for c in cands}:
        dts, _ = stock_dts.get(code, ([], {}))
        if not dts:
            continue
        d2i = {d: i for i, d in enumerate(dts)}
        # resolved fire (idx, ret) per sign for this stock, from the global qmap
        sign_hist: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for (c_code, sign), lst in qmap.items():
            if c_code != code:
                continue
            for sd, r in lst:
                if sd in d2i:
                    sign_hist[sign].append((d2i[sd], r))
        for s in sign_hist:
            sign_hist[s].sort()
        # validity-windowed valid sign set per index (from base_fires)
        valid_per_idx: dict[int, set[str]] = defaultdict(set)
        for sign, fd in base_fires.get(code, []):
            fi = d2i.get(fd)
            if fi is None:
                continue
            vb = _BULLISH.get(sign, 5)
            for j in range(fi, min(fi + vb + 1, len(dts))):
                valid_per_idx[j].add(sign)
        for c in cands:
            if c.stock_code != code:
                continue
            ti = d2i.get(c.entry_date)
            if ti is None:
                out[(code, c.entry_date)] = None
                continue
            valid = valid_per_idx.get(ti, set())
            qs = []
            for s in valid:
                # prior fires of (code,s) RESOLVED by T: fire_idx + 1 + _H <= ti
                rr = [r for fi, r in sign_hist.get(s, []) if fi + 1 + _H <= ti]
                if len(rr) >= _MIN_PRIOR:
                    qs.append(float(np.mean(rr)))
            out[(code, c.entry_date)] = float(np.mean(qs)) if qs else None
    return out


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    cbt._VALID_BARS = dict(_BULLISH)
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    base_fires: defaultdict[str, list] = defaultdict(list)
    for sg, stk, fa in rows:
        base_fires[stk].append((sg, fa.date() if hasattr(fa, "date") else fa))
    qmap = _load_qmap()

    fys = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                    "classified2016"),
           FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                    "classified2017")] + list(RS_FY_CONFIGS)

    st = {"A": [[] for _ in range(_K)], "B": [[] for _ in range(_K)],
          "C": [[] for _ in range(_K)]}
    fy_sh: dict[tuple[str, str], np.ndarray] = {}
    n_filled = {"A": 0, "B": 0, "C": 0}
    good_r: list[float] = []      # mechanism (seed-0, arm B): above-median-quality fills
    bad_r: list[float] = []

    exsim._MAX_LOW_CORR = _LOW
    for cfg in fys:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 180)
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

        cands = _build_cands(base_fires, caches, corr_maps, zs_maps, cfg)   # SHARED pool
        qual = _candidate_quality(cands, base_fires, stock_dts, qmap)
        known = [qual[(c.stock_code, c.entry_date)] for c in cands
                 if qual.get((c.stock_code, c.entry_date)) is not None]
        med = float(np.median(known)) if known else 0.0
        n_scored = len(known)

        def qof(c):
            v = qual.get((c.stock_code, c.entry_date))
            return med if v is None else v

        rets_a: list[list[float]] = []
        rets_b: list[list[float]] = []
        rets_c: list[list[float]] = []
        for k in range(_K):
            shuf = cands[:]; random.Random(k).shuffle(shuf)
            ra, res_a = _fy_returns(shuf, caches, cfg, stock_dts, cal, 1 + _LOW)
            # arm B: good-first (quality desc) stable sort on the SAME shuffle
            sb = sorted(shuf, key=lambda c: -qof(c))
            rb, res_b = _fy_returns(sb, caches, cfg, stock_dts, cal, 1 + _LOW)
            # arm C: veto candidates with negative trailing track record (keep None/positive)
            sc = [c for c in shuf
                  if (qual.get((c.stock_code, c.entry_date)) is None
                      or qual[(c.stock_code, c.entry_date)] >= 0.0)]
            rc, res_c = _fy_returns(sc, caches, cfg, stock_dts, cal, 1 + _LOW)
            rets_a.append(ra); rets_b.append(rb); rets_c.append(rc)
            st["A"][k] += ra[1:]; st["B"][k] += rb[1:]; st["C"][k] += rc[1:]
            if k == 0:
                n_filled["A"] += len(res_a); n_filled["B"] += len(res_b)
                n_filled["C"] += len(res_c)
                for p in res_b:
                    r = p.exit_price / p.entry_price - 1.0
                    v = qual.get((p.stock_code, p.entry_date))
                    if v is None:
                        continue
                    (good_r if v >= med else bad_r).append(r)
        fy_sh[(cfg.label, "A")] = np.array([_sharpe(r) for r in rets_a])
        fy_sh[(cfg.label, "B")] = np.array([_sharpe(r) for r in rets_b])
        fy_sh[(cfg.label, "C")] = np.array([_sharpe(r) for r in rets_c])
        logger.info("  {} done ({} cands, {} scored, med q={:.4f}, {} shuffles)",
                    cfg.label, len(cands), n_scored, med, _K)
    exsim._MAX_LOW_CORR = 5

    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in ("A", "B", "C")}
    rt = {a: np.array([_metrics(st[a][k])[0] for k in range(_K)]) for a in ("A", "B", "C")}
    dd = {a: np.array([_metrics(st[a][k])[2] for k in range(_K)]) for a in ("A", "B", "C")}

    print("\n" + "=" * 90)
    print(f"PER-STOCK SIGN-QUALITY PRIORITY vs FILL-ORDER NULL — {_K} paired shuffles, 6-slot")
    print("=" * 90)
    print(f"\n{'arm':<18}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}"
          f"{'ret mean':>10}{'DD mean':>9}")
    for a, name in [("A", "random"), ("B", "good-first"), ("C", "veto-neg")]:
        s_ = sh[a]
        print(f"{name:<18}{s_.mean():>13.2f}{s_.std():>7.2f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}"
              f"{rt[a].mean()*100:>9.0f}%{dd[a].mean()*100:>8.0f}%")

    for arm, name in [("B", "good-first"), ("C", "veto-neg")]:
        d = sh[arm] - sh["A"]
        p_pos = float((d > 0).mean())
        ci_lo, ci_hi = float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))
        print(f"\n[paired Δ Sharpe = {name} − random, same fill order each draw]")
        print(f"  mean {d.mean():+.3f} | sd {d.std():.3f} | 95% CI [{ci_lo:+.3f}, {ci_hi:+.3f}]")
        print(f"  P(Δ > 0) = {p_pos:.3f}  ({int((d>0).sum())}/{_K})")
        dr = rt[arm] - rt["A"]
        print(f"  paired Δ return mean {dr.mean()*100:+.0f}pp | P(Δ>0)={(dr>0).mean():.3f}")
        certified = p_pos >= 0.95 and ci_lo > 0
        print(f"  GATE (BINDING): P(Δ>0)={p_pos:.3f} (>=0.95), CI lo {ci_lo:+.3f} (>0) "
              f"→ {'PASS' if certified else 'FAIL'}")

    # per-FY for arm B
    print("\nPER-FY paired Δ Sharpe (good-first − random):")
    pos_fy = n_fy = 0
    for cfg in fys:
        if (cfg.label, "A") not in fy_sh:
            continue
        n_fy += 1
        dfy = fy_sh[(cfg.label, "B")] - fy_sh[(cfg.label, "A")]
        dfy = dfy[~np.isnan(dfy)]
        if len(dfy) == 0:
            continue
        m = float(dfy.mean())
        pos_fy += m > 0
        tag = "  ← OOS" if cfg.label == "FY2025" else ""
        print(f"  {cfg.label}  Δ mean {m:+.3f} | P(Δ>0)={(dfy>0).mean():.2f}{tag}")
    print(f"  ({pos_fy}/{n_fy} FYs Δ>0)")

    # mechanism (seed-0): do above-median-quality fills outperform inside the book?
    gm = statistics.mean(good_r) if good_r else float("nan")
    bmn = statistics.mean(bad_r) if bad_r else float("nan")
    gw = (sum(r > 0 for r in good_r) / len(good_r)) if good_r else float("nan")
    bw = (sum(r > 0 for r in bad_r) / len(bad_r)) if bad_r else float("nan")
    print(f"\n  MECHANISM (seed-0 arm B fills): good(q>=med) n={len(good_r)} "
          f"mean_r {gm*100:+.2f}% win {gw*100:.0f}% | "
          f"bad(q<med) n={len(bad_r)} mean_r {bmn*100:+.2f}% win {bw*100:.0f}%")
    print(f"  filled-trade counts (seed-0): A={n_filled['A']} B={n_filled['B']} "
          f"C={n_filled['C']} (B==A invariant; C drops vetoed)")

    db = sh["B"] - sh["A"]
    cert = float((db > 0).mean()) >= 0.95 and float(np.percentile(db, 2.5)) > 0
    print("\n" + "-" * 90)
    if cert:
        print("  VERDICT: PASS — good-first clears the binding null. Promote to a deeper build.")
    else:
        print("  VERDICT: REJECT — within fill-order noise; same fate as PEAD-boost and every "
              "selection rule.\n           Per-fire premise was real; reordering still doesn't "
              "change which 6 fill.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
