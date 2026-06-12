"""Earnings-announcement risk on the canonical confluence book — Stage-0 decomposition.

Proposal #2 (2026-06-13): nothing in the system measures how the canonical 6-slot
book behaves AROUND its holdings' own earnings disclosures. `jq_statements.disclosed_date`
(+ disclosed_time, after-close shift) gives every historical announcement; this script
answers, read-only:

  (A) ENTRY PROXIMITY — are fills entered shortly BEFORE a disclosure a worse cohort?
      Per fill: trading bars from entry to the next announcement *reaction bar*
      (`tradable_entry_day`: first bar whose open reflects the disclosure).
      Buckets 0 / 1-5 / 6-10 / 11-20 / 21-40 / >40-or-none.
      Binding contrast: NEAR = dist 1-10 vs FAR = dist >20 (dist 0 excluded — the
      news is already absorbed at that open for after-close disclosures).
  (B) PnL DECOMPOSITION — share of held-book PnL / bars / Σr² landing on reaction
      bars (W0 = reaction bar; W1 = reaction bar +1), vs non-announcement bars.
      Plus held-through cohort: positions holding ≥1 reaction bar vs none.
  (C) COARSENESS — what a "skip entries within 10 bars of disclosure" gate would
      thin (% of fills), and how often the book holds through announcements.

This is event-RISK timing on the existing book — distinct from PEAD (post-announcement
drift as a selection key, fully rejected). Fill-order robustness: the whole thing is
run over K=20 fill-order shuffles; the NEAR−FAR spread is reported as mean ± sd
ACROSS orders (a spread that flips sign across orders is fill-order noise).

Pre-stated Stage-0 gates (decided before running):
  - GATE-path escalation (skip/delay entries near disclosure) requires NEAR−FAR
    mean_r spread ≤ −0.5pp, same sign in ≥6/8 FYs, and stable across fill orders.
    (Thinning gates on confluence have leaned negative 3×; weak spread = stop.)
  - SIZING-path escalation requires the same spread plus material coarseness.
  - If reaction bars contribute NET POSITIVE PnL, any exit-before-announcement
    rule is dead on arrival (it would skip winners — the limit-entry lesson).
  - Otherwise: descriptive only, REJECT, write the memory and stop.

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_earnings_window_stage0
"""
from __future__ import annotations

import bisect
import datetime
import random
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.confluence_benchmark import _FYS
from src.analysis.confluence_evtilt_null import _closes, _pos_daily
from src.analysis.confluence_pead_inclusion_ab import _load_pead_statements
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.pead_forecast_revision import tradable_entry_day
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache
from src.strategy.confluence_sign import _BULLISH_SIGNS

_SLOTS = 6
_N_GATE = 3
_K_ORDERS = 20
_NEAR = (1, 10)          # binding contrast: entered 1-10 bars before next reaction bar
_FAR_MIN = 21            # FAR = dist >= 21 (incl. no disclosure within horizon)
_BUCKETS = [(0, 0, "0 (reaction bar)"), (1, 5, "1-5"), (6, 10, "6-10"),
            (11, 20, "11-20"), (21, 40, "21-40")]


def _impact_index(stmts_by_yf: dict[str, list[tuple]],
                  caches: dict[str, DataCache]) -> dict[str, tuple[list[datetime.date], dict[datetime.date, int]]]:
    """Per stock: sorted reaction-bar dates + {date: calendar index} for its own calendar."""
    out: dict[str, tuple[list[datetime.date], dict[datetime.date, int]]] = {}
    for code, cache in caches.items():
        cal = sorted({b.dt.date() for b in cache.bars})
        idx = {d: i for i, d in enumerate(cal)}
        impacts: set[datetime.date] = set()
        for dd, dt, _fy, _feps, _tod in stmts_by_yf.get(code, []):
            day = tradable_entry_day(dd, dt, cal)
            if day is not None:
                impacts.add(day)
        out[code] = (sorted(impacts), idx)
    return out


def _dist_to_next(code: str, entry: datetime.date,
                  impact_ix: dict[str, tuple[list[datetime.date], dict[datetime.date, int]]],
                  cal_of: dict[str, list[datetime.date]]) -> int | None:
    """Trading bars from entry to the next reaction bar on the stock's calendar.

    None = stock has no statements at all (excluded from cohorts, tracked separately).
    Large sentinel (10**6) = has statements but none on/after entry.
    """
    impacts, idx = impact_ix.get(code, ([], {}))
    if not impacts:
        return None
    ei = idx.get(entry)
    if ei is None:
        return None
    cal = cal_of[code]
    j = bisect.bisect_left(impacts, entry)
    if j >= len(impacts):
        return 10**6
    return idx[impacts[j]] - ei


def _bucket(dist: int) -> str:
    for lo, hi, lab in _BUCKETS:
        if lo <= dist <= hi:
            return lab
    return ">40/none"


def _coh_line(name: str, rets: list[float]) -> str:
    if not rets:
        return f"  {name:>18}: n=0"
    a = np.asarray(rets)
    return (f"  {name:>18}: n={len(a):>5}  mean_r={a.mean()*100:+.2f}%  "
            f"DR={float((a > 0).mean()*100):.1f}%  med={np.median(a)*100:+.2f}%")


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    exsim._MAX_LOW_CORR = 5     # production 6-slot
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH_SIGNS)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    stmts = _load_pead_statements()

    buckets: defaultdict[str, list[float]] = defaultdict(list)        # pooled, all orders
    coh_through: dict[bool, list[float]] = {True: [], False: []}      # holds >=1 reaction bar
    no_stmt: list[float] = []
    # decomposition accumulators: label -> [pnl_sum, n_bars, sum_sq]
    decomp = {"W0": [0.0, 0, 0.0], "W1": [0.0, 0, 0.0], "other": [0.0, 0, 0.0]}
    per_fy: dict[str, dict] = {}

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 90)
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
        impact_ix = _impact_index(stmts, caches)
        cal_of = {code: sorted({b.dt.date() for b in c.bars}) for code, c in caches.items()}
        covered = sum(1 for code in caches if impact_ix[code][0])

        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))

        fy_spreads: list[float] = []
        fy_near, fy_far, fy_fills, fy_through = 0, 0, 0, 0
        for seed in range(_K_ORDERS):
            pool = cands[:]
            random.Random(seed).shuffle(pool)
            results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
            near_r, far_r = [], []
            for p in results:
                if not p.entry_price:
                    continue
                ret = p.exit_price / p.entry_price - 1.0
                dist = _dist_to_next(p.stock_code, p.entry_date, impact_ix, cal_of)
                if dist is None:
                    no_stmt.append(ret)
                else:
                    buckets[_bucket(dist)].append(ret)
                    if _NEAR[0] <= dist <= _NEAR[1]:
                        near_r.append(ret)
                    elif dist >= _FAR_MIN:
                        far_r.append(ret)
                # held-through + PnL decomposition on this position's daily series
                impacts, idx = impact_ix.get(p.stock_code, ([], {}))
                imp_set = set(impacts)
                cal = cal_of.get(p.stock_code, [])
                imp_next = {cal[idx[d] + 1] for d in impacts if idx[d] + 1 < len(cal)}
                daily = _pos_daily(p, *stock_dts.get(p.stock_code, ([], {})))
                through = False
                for d, r in daily.items():
                    if d in imp_set:
                        key = "W0"
                        through = True
                    elif d in imp_next:
                        key = "W1"
                    else:
                        key = "other"
                    acc = decomp[key]
                    acc[0] += r; acc[1] += 1; acc[2] += r * r
                coh_through[through].append(ret)
                fy_through += through
            fy_fills += len(results)
            fy_near += len(near_r)
            fy_far += len(far_r)
            if near_r and far_r:
                fy_spreads.append(float(np.mean(near_r) - np.mean(far_r)))
        per_fy[cfg.label] = {
            "coverage": f"{covered}/{len(caches)}",
            "fills": fy_fills / _K_ORDERS,
            "near_frac": fy_near / fy_fills if fy_fills else float("nan"),
            "through_frac": fy_through / fy_fills if fy_fills else float("nan"),
            "spreads": fy_spreads,
        }
        logger.info("  {} done ({} cands, {:.0f} fills/order, stmt coverage {})",
                    cfg.label, len(cands), fy_fills / _K_ORDERS, per_fy[cfg.label]["coverage"])

    _report(buckets, coh_through, no_stmt, decomp, per_fy)


def _report(buckets, coh_through, no_stmt, decomp, per_fy) -> None:
    print("\n=== Earnings-announcement window — Stage-0 decomposition (canonical 6-slot book) ===")
    print(f"params: K_ORDERS={_K_ORDERS}  N_GATE={_N_GATE}  NEAR=dist {_NEAR[0]}-{_NEAR[1]}  "
          f"FAR=dist>={_FAR_MIN}  FY2018-FY2025 (FY2025=OOS)\n")

    print("(A) ENTRY PROXIMITY — per-fill total return by bars-to-next-reaction-bar "
          f"(pooled over FYs x {_K_ORDERS} orders):")
    order = [lab for _, _, lab in _BUCKETS] + [">40/none"]
    for lab in order:
        print(_coh_line(lab, buckets.get(lab, [])))
    print(_coh_line("no-statements", no_stmt))

    near = buckets.get("1-5", []) + buckets.get("6-10", [])
    far = buckets.get("21-40", []) + buckets.get(">40/none", [])
    if near and far:
        spread = (np.mean(near) - np.mean(far)) * 100
        print(f"\n   binding contrast NEAR(1-10) − FAR(>=21): {spread:+.2f}pp "
              f"(n={len(near)} vs {len(far)})")

    print("\n   per-FY NEAR−FAR spread (mean ± sd across fill orders):")
    neg_fy = pos_fy = 0
    for lab, d in per_fy.items():
        sp = d["spreads"]
        if sp:
            m, sd = float(np.mean(sp)), float(np.std(sp))
            neg_fy += m < 0; pos_fy += m > 0
            print(f"     {lab:<8} {m*100:+6.2f}pp ± {sd*100:4.2f}  "
                  f"(fills/ord {d['fills']:.0f}, NEAR {d['near_frac']*100:4.1f}%, "
                  f"thru {d['through_frac']*100:4.1f}%, stmts {d['coverage']})")
        else:
            print(f"     {lab:<8} (no NEAR or FAR fills)")
    print(f"   sign consistency: {neg_fy} FYs negative / {pos_fy} positive")

    print("\n(B) HELD-BOOK PnL DECOMPOSITION (daily position returns, all orders):")
    tot_pnl = sum(v[0] for v in decomp.values())
    tot_bars = sum(v[1] for v in decomp.values())
    tot_sq = sum(v[2] for v in decomp.values())
    for key, label in [("W0", "reaction bar"), ("W1", "reaction bar +1"), ("other", "non-announcement")]:
        pnl, n, sq = decomp[key]
        if n == 0:
            continue
        print(f"  {label:>18}: bars {n/tot_bars*100:5.1f}%  PnL share {pnl/tot_pnl*100:+6.1f}%  "
              f"Σr² share {sq/tot_sq*100:5.1f}%  per-bar mean {pnl/n*100:+.3f}%  "
              f"per-bar sd {np.sqrt(sq/n - (pnl/n)**2)*100:.2f}%")

    print("\n  held-through cohort (position holds >=1 reaction bar):")
    print(_coh_line("through", coh_through[True]))
    print(_coh_line("not-through", coh_through[False]))

    print("\n(C) VERDICT inputs (gates pre-stated in module docstring):")
    if near and far:
        sp = (np.mean(near) - np.mean(far)) * 100
        gate_ok = sp <= -0.5 and neg_fy >= 6
        w0_pos = decomp["W0"][0] > 0
        print(f"  NEAR−FAR spread {sp:+.2f}pp ({'meets' if sp <= -0.5 else 'fails'} ≤ −0.5pp); "
              f"FY consistency {neg_fy}/8 ({'meets' if neg_fy >= 6 else 'fails'} ≥6 negative)")
        print(f"  reaction-bar PnL net {'POSITIVE → exit-before-announcement DOA' if w0_pos else 'negative'}")
        print(f"  → {'ESCALATE (paired fill-order null on gate/sizing)' if gate_ok else 'REJECT — descriptive only, write memory and stop'}")
    print()


if __name__ == "__main__":
    run()
