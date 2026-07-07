"""Per-stock sign-quality as a SAME-DAY CONTENTION TIEBREAKER (proxy test).

Context: the K=200 6-slot fill-order null REJECTED quality as a portfolio reordering rule
(per_stock_sign_quality_null.py) — but that test is dominated by non-contention days and
book dynamics.  The operator's ACTUAL decision point is narrower: at a moment when several
viable candidates compete for ONE open slot (after a manual risk-scan), pick one instead of
choosing arbitrarily.  The risk-scan can't be backtested, but the CONTENTION structure can:
on days where >=2 candidates fire, does picking the highest sign-quality candidate beat a
random (arbitrary) pick?

This sidesteps the slot simulator entirely — a single-slot choice has no book dynamics.
Outcome = fixed-horizon realized return (fill open[T+1], exit close[T+1+H]); the question is
purely "which competitor was the better single trade."  Quality key is the SAME look-ahead-
safe per-valid-sign trailing track record used in the null.

For each contention day d with m>=2 scored competitors:
  skill pick   = argmax quality -> its return  (ties averaged)
  random pick  = mean return over the m competitors  (= E[arbitrary pick])
  worst pick   = argmin quality -> its return  (full spread)
Statistic = mean over days of (r_skill - r_random).  Significance: permutation null (within
each day replace the skill pick with a random competitor) + bootstrap-over-days CI.  Reported
for all >=2 days, the same-corr-bucket subset (closer to a real slot contest), the 2-only
head-to-head, and per-FY.  Power (number of contention days) reported up front.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.per_stock_sign_quality_contention
"""
from __future__ import annotations

import datetime
import random
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.per_stock_sign_quality_null import (
    _BULLISH, _N_GATE, _build_cands, _candidate_quality, _closes, _load_qmap,
)
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.simulator.cache import DataCache

_H = 10
_WINSOR = 0.60
_KPERM = 2000


def _closes_opens(cache):
    dts, cmap, omap, seen = [], {}, {}, set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cmap[d] = b.close; omap[d] = b.open
    dts.sort()
    return dts, cmap, omap


def _fwd(dts, cmap, omap, entry_date, h):
    """fill open[T+1], exit close[T+1+h]; T = entry_date (signal day)."""
    try:
        ti = dts.index(entry_date)
    except ValueError:
        return np.nan
    if ti + 1 + h >= len(dts):
        return np.nan
    o = omap[dts[ti + 1]]; c = cmap[dts[ti + 1 + h]]
    if not o:
        return np.nan
    return float(np.clip(c / o - 1.0, -_WINSOR, _WINSOR))


def _report(days, label):
    """days = list of dicts {q: [..], r: [..], fy, bucket}.  Skill=argmax q vs random=mean."""
    rows = [d for d in days if len(d["q"]) >= 2 and np.isfinite(d["r"]).sum() >= 2]
    if len(rows) < 10:
        print(f"\n[{label}] only {len(rows)} contention days — underpowered, skipped")
        return
    skill, rnd, worst = [], [], []
    for d in rows:
        q = np.asarray(d["q"]); r = np.asarray(d["r"])
        ok = np.isfinite(r)
        q, r = q[ok], r[ok]
        if len(r) < 2:
            continue
        mx = q == q.max(); mn = q == q.min()
        skill.append(r[mx].mean()); worst.append(r[mn].mean()); rnd.append(r.mean())
    skill = np.asarray(skill); rnd = np.asarray(rnd); worst = np.asarray(worst)
    delta = skill - rnd
    obs = float(delta.mean())
    # bootstrap over days
    rs = np.random.RandomState(0)
    boot = np.array([delta[rs.randint(0, len(delta), len(delta))].mean() for _ in range(2000)])
    ci_lo, ci_hi = np.percentile(boot, 2.5), np.percentile(boot, 97.5)
    # permutation: within each day pick a random competitor instead of argmax-q
    perm = np.empty(_KPERM)
    for k in range(_KPERM):
        rk = random.Random(k)
        pr = np.array([rk.choice(np.asarray(d["r"])[np.isfinite(d["r"])]) for d in rows])
        perm[k] = (pr - rnd).mean()
    p_perm = float((perm >= obs).mean())
    print(f"\n=== [{label}]  contention days={len(skill)} ===")
    print(f"  skill(max-q) mean_r {skill.mean()*100:+.3f}%  | random mean_r {rnd.mean()*100:+.3f}%"
          f"  | worst(min-q) {worst.mean()*100:+.3f}%")
    print(f"  Δ(skill−random) = {obs*100:+.3f}pp  95% CI [{ci_lo*100:+.3f}, {ci_hi*100:+.3f}]"
          f"  | day win-rate {(delta>0).mean()*100:.1f}%")
    print(f"  permutation P(random >= skill) = {p_perm:.3f}  "
          f"({'SIGNIFICANT' if p_perm < 0.05 else 'ns'})  "
          f"| skill−worst spread {(skill.mean()-worst.mean())*100:+.3f}pp")
    return rows


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

    all_days: list[dict] = []          # per (fy, date): competitors with q + r
    bucket_days: list[dict] = []       # per (fy, date, corr_bucket)
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
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        co = {code: _closes_opens(c) for code, c in caches.items()}

        cands = _build_cands(base_fires, caches, corr_maps, zs_maps, cfg)
        qual = _candidate_quality(cands, base_fires, stock_dts, qmap)

        by_day: dict[datetime.date, dict] = defaultdict(lambda: {"q": [], "r": [], "bucket": []})
        for c in cands:
            v = qual.get((c.stock_code, c.entry_date))
            if v is None:                    # unscored — operator can't rank it; exclude
                continue
            dts, cmap, omap = co[c.stock_code]
            r = _fwd(dts, cmap, omap, c.entry_date, _H)
            if not np.isfinite(r):
                continue
            by_day[c.entry_date]["q"].append(v)
            by_day[c.entry_date]["r"].append(r)
            by_day[c.entry_date]["bucket"].append(c.corr_mode)
        for d, rec in by_day.items():
            all_days.append({"q": rec["q"], "r": rec["r"], "fy": cfg.label})
            # split same-day competitors by corr bucket (a real slot contest is within-bucket)
            bsplit: dict[str, dict] = defaultdict(lambda: {"q": [], "r": []})
            for q, r, b in zip(rec["q"], rec["r"], rec["bucket"]):
                bsplit[b]["q"].append(q); bsplit[b]["r"].append(r)
            for b, br in bsplit.items():
                bucket_days.append({"q": br["q"], "r": br["r"], "fy": cfg.label, "bucket": b})
        logger.info("  {} done ({} cands, {} days, {} >=2-cand days)",
                    cfg.label, len(cands), len(by_day),
                    sum(1 for r in by_day.values() if len(r["q"]) >= 2))

    print("\n" + "=" * 88)
    print(f"PER-STOCK SIGN-QUALITY as SAME-DAY CONTENTION TIEBREAKER (proxy, h{_H}) ")
    print("=" * 88)
    n_ge2 = sum(1 for d in all_days if len(d["q"]) >= 2)
    msz = [len(d["q"]) for d in all_days if len(d["q"]) >= 2]
    print(f"\nPOWER: {len(all_days)} scored-candidate days total; {n_ge2} have >=2 scored "
          f"competitors (median competitors/day = {int(np.median(msz)) if msz else 0}, "
          f"max {max(msz) if msz else 0})")

    _report(all_days, "ALL >=2 scored competitors/day")
    _report([d for d in all_days if len(d["q"]) >= 3], "RICHER (>=3 competitors/day)")
    _report([d for d in all_days if len(d["q"]) == 2], "HEAD-TO-HEAD (exactly 2/day)")
    _report(bucket_days, "WITHIN corr-bucket (real slot contest)")

    # per-FY for the ALL cut
    print("\nPER-FY Δ(skill−random) [ALL >=2/day]:")
    for cfg in fys:
        sub = [d for d in all_days if d["fy"] == cfg.label and len(d["q"]) >= 2]
        deltas = []
        for d in sub:
            q = np.asarray(d["q"]); r = np.asarray(d["r"]); ok = np.isfinite(r)
            q, r = q[ok], r[ok]
            if len(r) >= 2:
                deltas.append(r[q == q.max()].mean() - r.mean())
        if len(deltas) >= 10:
            dd = np.asarray(deltas)
            tag = "  ← OOS" if cfg.label == "FY2025" else ""
            print(f"  {cfg.label}  Δ {dd.mean()*100:+.3f}pp  (n={len(dd)} days, "
                  f"win {(dd>0).mean()*100:.0f}%){tag}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
