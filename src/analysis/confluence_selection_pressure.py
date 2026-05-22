"""Stage-0 gate: is there selection pressure to rank among in ConfluenceSign?

Cross-sectional ranking of candidates only adds value if, when a slot is free,
there are routinely MORE valid candidates than free slots. This measures that
directly over FY2017-2025, before building any ranker.

Two metrics:
  [A] Co-firing: distribution of how many confluence candidates share the same
      entry (signal) day. The raw "how many proposals on the table at once".
  [B] 4-slot binding: greedy-fill a 4-slot book (each trade occupies a slot for
      its real [entry,exit] span; the ≤1-high-corr rule enforced). Count how
      often an arriving candidate is FORCED to be rejected because no eligible
      slot is free — that is the true selection event a ranker would resolve.

Pre-registered read:
  LOW pressure  (median arrivals/day ≤1 AND blocked-rate <15%)  -> ranking is
                mostly a no-op; STOP this line.
  REAL pressure (multi-candidate days common AND blocked-rate meaningful) ->
                a ranker decides which name fills the slot; PROCEED to step 2.

OUTCOME (2026-05-22): MIXED, leaning low. 326 trades/9y. Same-day co-firing is
rare (88.8% of entry days have ONE candidate). The honest metric — windowed
choice-set at each fill (K=7d shelf) — has median 1 but 25.5% of fills face ≥2
live candidates (~70 events/9y ≈ 8/yr). Book is near-full (3.58/4) and 16% of
valid proposals EXPIRE unfilled for lack of a slot. So a ranker has a real but
thin (~8/yr) decision surface; the binding constraint is CAPACITY, not name
choice. Step 2 (confluence_rs_rerank.py) then REJECTED relative-strength
re-ranking. See memory project_confluence_xsec_ranking_reject.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_selection_pressure
"""
from __future__ import annotations

import datetime
import statistics
import sys
from collections import Counter, defaultdict

from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_SLOTS = 4
_FYS = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                 "classified2016"),
        FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    # collect actual trades (entry_date, exit_date, corr_mode) across all FYs
    trades: list[tuple[datetime.date, datetime.date, str]] = []
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
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)
        for p in results:
            trades.append((p.entry_date, p.exit_date, p.corr_mode))
        logger.info("  {} processed ({} trades)", cfg.label, len(results))

    trades.sort(key=lambda t: t[0])
    n = len(trades)
    yrs = (trades[-1][0] - trades[0][0]).days / 365.25 if n > 1 else 1.0

    print("\n" + "=" * 72)
    print(f"CONFLUENCE SELECTION-PRESSURE GATE — {n} trades over ~{yrs:.1f}y "
          f"({n / yrs:.0f}/yr)")
    print("=" * 72)

    # ── [A] co-firing: arrivals per entry day ──────────────────────────────────
    per_day = Counter()
    for ed, _, _ in trades:
        per_day[ed] += 1
    arrival_days = sorted(per_day)
    sizes = [per_day[d] for d in arrival_days]
    hist = Counter(sizes)
    print(f"\n[A] Co-firing — candidates sharing the same entry (signal) day")
    print(f"    {len(arrival_days)} distinct entry days; arrivals/day "
          f"median={statistics.median(sizes):.0f} mean={statistics.mean(sizes):.2f} "
          f"max={max(sizes)}")
    for k in sorted(hist):
        share = 100.0 * hist[k] / len(arrival_days)
        bar = "█" * int(round(share / 2))
        print(f"      {k} candidate(s): {hist[k]:>4} days ({share:4.1f}%) {bar}")
    multi = sum(v for k, v in hist.items() if k >= 2)
    print(f"    entry days with >=2 simultaneous candidates: "
          f"{multi}/{len(arrival_days)} ({100.0*multi/len(arrival_days):.1f}%)")

    # ── [B] windowed choice-set at fill events (the ranker-relevant number) ─────
    # Event-driven 4-slot sim. A candidate fired on day D stays a LIVE proposal
    # for K calendar days [D, D+K] (≈ its validity window). On every day a slot
    # is free and ≥1 live un-entered candidate exists, we fill greedily (≤1
    # high-corr seat) and record the CHOICE-SET SIZE = how many live eligible
    # candidates were available at that fill.  That set is exactly what a
    # cross-sectional ranker would order.  Carrying blocked candidates forward
    # (unlike [A]) is the honest count: a slot freeing tomorrow can be filled by
    # a name that fired today.
    def _choice_dist(K: int) -> tuple[list[int], int, int]:
        cand = sorted(trades, key=lambda t: t[0])       # (entry_date, exit_date, corr)
        live = []  # pending: (signal_day, expiry, exit_date, corr)
        ci = 0
        free_after = [datetime.date.min] * _SLOTS
        high_busy_until = datetime.date.min
        # iterate over union of all event days (signal days + exit days)
        evt_days = sorted({t[0] for t in trades} | {t[1] for t in trades})
        choice_sizes: list[int] = []
        filled = expired = 0
        for d in evt_days:
            # admit candidates whose signal is today
            while ci < len(cand) and cand[ci][0] <= d:
                ed, xd, cm = cand[ci]
                live.append([ed, ed + datetime.timedelta(days=K), xd, cm])
                ci += 1
            # drop expired (never filled within window)
            before = len(live)
            live = [c for c in live if c[1] >= d]
            expired += before - len(live)
            # free slots
            free_idx = [i for i, fa in enumerate(free_after) if fa < d]
            hi_free = high_busy_until < d
            # fill while a free slot and an eligible live candidate exist
            while free_idx and live:
                eligible = [c for c in live if not (c[3] == "high" and not hi_free)]
                if not eligible:
                    break
                # record the choice set the ranker would order, then take one
                choice_sizes.append(len(eligible))
                pick = eligible[0]            # FIFO placeholder (ranker would reorder)
                live.remove(pick)
                i = free_idx.pop(0)
                free_after[i] = pick[2]
                if pick[3] == "high":
                    high_busy_until = pick[2]
                    hi_free = False
                filled += 1
        return choice_sizes, filled, expired

    for K in (7, 10):
        cs, filled, expired = _choice_dist(K)
        if not cs:
            continue
        ch = Counter(cs)
        med = statistics.median(cs)
        multi = sum(v for k, v in ch.items() if k >= 2)
        print(f"\n[B] Windowed choice-set at each fill (proposal shelf-life K={K} cal-days)")
        print(f"    {filled} fills, {expired} candidates expired unfilled; "
              f"choice-set median={med:.0f} mean={statistics.mean(cs):.2f} max={max(cs)}")
        for k in sorted(ch):
            share = 100.0 * ch[k] / len(cs)
            bar = "█" * int(round(share / 2))
            print(f"      {k} live candidate(s): {ch[k]:>4} fills ({share:4.1f}%) {bar}")
        print(f"    fills facing a REAL choice (>=2 live): "
              f"{multi}/{len(cs)} ({100.0*multi/len(cs):.1f}%)")

    # ── verdict (use K=7) ───────────────────────────────────────────────────────
    cs7, _, _ = _choice_dist(7)
    med_choice = statistics.median(cs7) if cs7 else 0
    real_choice_rate = (sum(1 for x in cs7 if x >= 2) / len(cs7)) if cs7 else 0.0
    print("\n" + "-" * 72)
    print(f"VERDICT: median live choice-set at a fill = {med_choice:.0f}; "
          f"fills with ≥2 live candidates = {real_choice_rate*100:.1f}%")
    if real_choice_rate < 0.20:
        print("  -> LOW selection pressure. When a slot frees there is usually only")
        print("     ONE live candidate — a cross-sectional ranker is mostly a no-op.")
        print("     The binding constraint is slot CAPACITY/timing, not name choice.")
    else:
        print("  -> REAL selection pressure on a meaningful share of fills. A ranker")
        print("     would order ≥2 live candidates. PROCEED to step 2 (single-factor A/B).")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
