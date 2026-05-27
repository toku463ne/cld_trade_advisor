"""PEAD sleeve — selection-alpha null (the binding gate of the pre-registration).

Pre-reg: docs/analysis/pead_sleeve_preregistration.md (frozen 2026-05-27). Tests whether a
standalone ¥0.6M PEAD sleeve (cohort up-revisions, 2 slots, diversification-priority fill,
TimeStop(60), no TP/SL) adds SELECTION alpha to a ¥1.4M confluence book on a shared ¥2.0M
account — net of leverage and pro-cyclical timing, surviving earnings-cluster bootstrap.

Benchmark ladder (all blended ¥2.0M; ¥1.4M confluence slice identical & paired across 2-4):
  book1 = confluence ¥2.0M (sleeve-off)                                  — baseline
  book2 = ¥1.4M conf + ¥0.6M index at constant avg exposure             — strips leverage
  book3 = ¥1.4M conf + ¥0.6M index on the sleeve's actual hold-days, at  — strips timing
          each filled position's β & weight (= portfolio aggregate of the β-stripped CARs)
  book4 = ¥1.4M conf + the real ¥0.6M sleeve                            — full sleeve

BINDING: ΔSharpe(book4 − book3) on the full ¥2.0M stitched daily curve. Two paired layers
per seed (same draws to both books): (1) sleeve fill-order shuffle, (2) circular block
bootstrap L=60 of the blended daily series. K=2000. Gate: P(Δ>0)≥0.95 AND 2.5pct>0, no
effect-size floor. (3 vs 2)=timing and (2 vs 1)=leverage are DIAGNOSTIC ONLY.

Implementation note (faithful to the pre-reg's "confluence identical & paired"): the ¥1.4M
confluence slice is held at its canonical (deterministic) realization, computed once per FY
— identical across books 2-4 so it cancels in (4 vs 3); its volatility backdrop still enters
the portfolio Sharpe and is resampled by the layer-2 block bootstrap. The randomization that
matters for the gate (which up-names fill the 2 slots) IS shuffled and paired.

Read-only. Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.pead_sleeve_null
"""
from __future__ import annotations

import datetime
import math
import statistics
import sys
from collections import Counter, defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_benchmark import _BULLISH, _N_GATE, _closes, _pos_daily
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.pead_forecast_revision import (
    Disclosure, beta, doc_basis, pair_same_fy_revisions, revision_surprise,
    tradable_entry_day,
)
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.portfolio.sizing import position_weight, recommended_lots
from src.simulator.cache import DataCache

# locked design (pre-reg §1)
_CONF_BUDGET = 1_400_000
_SLEEVE_BUDGET = 600_000
_TOTAL = 2_000_000
_SLEEVE_SLOTS = 2
_HOLD = 60
_BETA_WIN = 60
_K = 2000
_BLOCK = 60                      # block-bootstrap length (robustness rows at 20, 120)
_W_CONF = _CONF_BUDGET / _TOTAL   # 0.70 — confluence slice's share of the ¥2.0M curve
_W_SLV = _SLEEVE_BUDGET / _TOTAL  # 0.30 — sleeve slice's share
_FYS = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                 "classified2016"),
        FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)


def _sharpe(rets) -> float:
    a = np.asarray(rets, dtype=np.float64)
    if a.size < 2:
        return float("nan")
    sd = a.std(ddof=1)
    return float(a.mean() / sd * math.sqrt(252)) if sd > 0 else float("nan")


def _total_return(rets) -> float:
    a = np.asarray(rets, dtype=np.float64)
    return float(np.prod(1.0 + a) - 1.0) if a.size else float("nan")


# ── confluence ¥B slice: canonical (deterministic) per-FY daily returns ──────────
def _confluence_fy_returns(cfg, caches, stock_dts, cal, budget: int) -> list[float]:
    """Capital-aware confluence daily returns over `cal`, budget-weighted (mirrors
    confluence_benchmark's budget book) at its production 6-slot config."""
    cal_set = set(cal)
    corr_maps = _CORR_MAPS[cfg.label]
    cands = []
    for code in caches:
        cands += cbt._candidates_for_stock(
            code, _FIRES.get(code, []), caches[code], corr_maps[code],
            _ZS_MAPS[cfg.label][code], cfg.start, cfg.end, _N_GATE)
    cands.sort(key=lambda c: c.entry_date)

    def _affordable(c) -> bool:
        _, cmap = stock_dts.get(c.stock_code, ([], {}))
        px = cmap.get(c.entry_date)
        return px is not None and recommended_lots(budget, float(px), cbt_slots()) > 0

    cands_aff = [c for c in cands if _affordable(c)]
    results = run_simulation(cands_aff, cbt._EXIT_RULE, caches, cfg.end)
    day = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        lots = recommended_lots(budget, float(p.entry_price), cbt_slots())
        w = position_weight(lots, float(p.entry_price), budget)
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day[d] += r * w
    return [day.get(d, 0.0) for d in cal]


def cbt_slots() -> int:
    import src.exit.exit_simulator as exsim
    return 1 + exsim._MAX_LOW_CORR     # production 6-slot


# globals filled in run()
_FIRES: dict = {}
_CORR_MAPS: dict = {}
_ZS_MAPS: dict = {}


# ── sleeve event model ───────────────────────────────────────────────────────
class SleeveEvent:
    __slots__ = ("code", "entry_date", "ei", "beta", "w", "contrib4", "contrib3")

    def __init__(self, code, entry_date, ei, b, w, contrib4, contrib3):
        self.code = code
        self.entry_date = entry_date
        self.ei = ei
        self.beta = b
        self.w = w
        self.contrib4 = contrib4   # dict{date: w * stock_dod}
        self.contrib3 = contrib3   # dict{date: w * beta * topix_dod}


def _build_sleeve_events():
    """Load jq_* once → cohort up-revision SleeveEvents with precomputed daily contribs,
    plus per-code trailing-return arrays for the diversification corr. Returns
    (events, cal_topix, ret_arr_by_code, row_of)."""
    from src.data.jquants_collector import to_yf_code
    from src.data.jquants_models import JqDailyQuote, JqStatement, JqTopix
    from src.data.models import Ohlcv1d

    with get_session() as s:
        stmts = s.execute(
            select(JqStatement.local_code, JqStatement.disclosed_date,
                   JqStatement.disclosed_time, JqStatement.current_fiscal_year_end_date,
                   JqStatement.forecast_earnings_per_share, JqStatement.type_of_document)
        ).all()
        topix = s.execute(select(JqTopix.date, JqTopix.close)
                          .where(JqTopix.close.isnot(None)).order_by(JqTopix.date)).all()
        cohort = {c for (c,) in s.execute(select(Ohlcv1d.stock_code).distinct())}
        codes = [c for (c,) in s.execute(select(JqDailyQuote.code).distinct()
                                         .order_by(JqDailyQuote.code))]
        cal = [d for d, _ in topix]
        col_of = {d: i for i, d in enumerate(cal)}
        row_of = {c: i for i, c in enumerate(codes)}
        topix_arr = np.array([float(c) for _, c in topix], dtype=np.float64)
        arr = np.full((len(codes), len(cal)), np.nan, dtype=np.float32)
        stream = s.connection().execution_options(stream_results=True, yield_per=200_000)
        for code, d, ac in stream.execute(
                select(JqDailyQuote.code, JqDailyQuote.date, JqDailyQuote.adj_close)):
            ci = col_of.get(d)
            ri = row_of.get(code)
            if ci is not None and ri is not None and ac is not None:
                arr[ri, ci] = float(ac)

    topix_dod = np.full(len(cal), np.nan)
    topix_dod[1:] = topix_arr[1:] / topix_arr[:-1] - 1.0
    global _TOPIX_DOD
    _TOPIX_DOD = topix_dod

    raw = defaultdict(list)
    for code, dd, dt, fy, feps, tod in stmts:
        raw[code].append((dd, dt, fy, feps, tod))
    by_code = {}
    for code, rows in raw.items():
        fin = Counter(b for b in (doc_basis(r[4]) for r in rows) if b)
        modal = fin.most_common(1)[0][0] if fin else None
        by_code[code] = [Disclosure(dd, dt, fy, feps, doc_basis(tod) or modal)
                         for dd, dt, fy, feps, tod in rows]

    ret_arr = {}     # code -> daily-return array over cal (for corr)
    events: list[SleeveEvent] = []
    n_seen = n_aff = 0
    for code, discs in by_code.items():
        ri = row_of.get(code)
        if ri is None or to_yf_code(code) not in cohort:
            continue
        srow = arr[ri]
        rr = np.full(len(cal), np.nan)
        rr[1:] = srow[1:] / srow[:-1] - 1.0
        ret_arr[code] = rr
        for prev, curr in pair_same_fy_revisions(discs):
            if curr.fy_end is None:
                continue
            entry = tradable_entry_day(curr.disclosed_date, curr.disclosed_time, cal)
            if entry is None:
                continue
            ei = col_of[entry]
            if ei < _BETA_WIN + 1 or ei + _HOLD >= len(cal):
                continue
            price = srow[ei - 1]
            if not (price > 0):
                continue
            sp = revision_surprise(prev.forecast_eps, curr.forecast_eps, float(price))
            if sp is None or sp <= 0:                       # UP-revisions only
                continue
            sw, mw = srow[ei - _BETA_WIN - 1:ei], topix_arr[ei - _BETA_WIN - 1:ei]
            b = beta(sw[1:] / sw[:-1] - 1.0, mw[1:] / mw[:-1] - 1.0)
            if b is None:
                continue
            entry_px = float(srow[ei])
            if not (entry_px > 0):
                continue
            n_seen += 1
            lots = recommended_lots(_SLEEVE_BUDGET, entry_px, _SLEEVE_SLOTS)
            if lots <= 0:                                    # affordability skip (1 lot > ¥0.3M)
                continue
            w = position_weight(lots, entry_px, _SLEEVE_BUDGET)
            contrib4, contrib3 = {}, {}
            ok = True
            for j in range(ei + 1, ei + _HOLD + 1):
                s0, s1 = srow[j - 1], srow[j]
                if not (s0 > 0) or not (s1 > 0) or not np.isfinite(topix_dod[j]):
                    ok = False
                    break
                d = cal[j]
                contrib4[d] = w * (s1 / s0 - 1.0)
                contrib3[d] = w * b * topix_dod[j]
            if not ok:
                continue
            n_aff += 1
            events.append(SleeveEvent(code, entry, ei, b, w, contrib4, contrib3))
    logger.info("sleeve events: {} up-revisions w/ window, {} affordable+clean (¥0.3M/slot ceiling)",
                n_seen, n_aff)
    return events, cal, ret_arr


def _maxcorr(cand: SleeveEvent, holds: list[SleeveEvent], ret_arr, lo: int, hi: int) -> float:
    """max |trailing-60-bar corr| of candidate vs current holdings (0 if no holdings)."""
    if not holds:
        return 0.0
    a = ret_arr[cand.code][lo:hi]
    best = 0.0
    for h in holds:
        b = ret_arr[h.code][lo:hi]
        m = np.isfinite(a) & np.isfinite(b)
        if m.sum() < 20:
            continue
        av, bv = a[m], b[m]
        if av.std() == 0 or bv.std() == 0:
            continue
        c = abs(float(np.corrcoef(av, bv)[0, 1]))
        best = max(best, c)
    return best


def _sleeve_fill(events: list[SleeveEvent], ret_arr, rng) -> list[SleeveEvent]:
    """Diversification-priority, skip-not-queue, eligibility = entry day only, cap 2 slots.
    Returns the events that actually filled a slot (their contribs are then summed)."""
    by_day: dict[datetime.date, list[SleeveEvent]] = defaultdict(list)
    for e in events:
        by_day[e.entry_date].append(e)
    holds: list[SleeveEvent] = []
    filled: list[SleeveEvent] = []
    for d in sorted(by_day):
        ei_d = by_day[d][0].ei                       # all events on date d share this index
        holds = [h for h in holds if h.ei + _HOLD >= ei_d]     # free expired slots
        free = _SLEEVE_SLOTS - len(holds)
        if free <= 0:
            continue
        elig = by_day[d][:]
        rng.shuffle(elig)                            # fill-order shuffle (tiebreak teeth)
        while free > 0 and elig:
            lo, hi = ei_d - _BETA_WIN, ei_d
            pick = min(elig, key=lambda e: _maxcorr(e, holds, ret_arr, lo, hi))
            elig.remove(pick)
            holds.append(pick)
            filled.append(pick)
            free -= 1
    return filled


def _sleeve_daily(filled: list[SleeveEvent], cal_dates) -> tuple[list[float], list[float], float]:
    """Sum filled positions' daily contribs onto `cal_dates`. Returns (book4, book3 arrays,
    mean daily Σ(w·β) exposure for the book2 constant-exposure leg)."""
    cset = set(cal_dates)
    b4 = defaultdict(float)
    b3 = defaultdict(float)
    expo = defaultdict(float)
    for e in filled:
        for d, v in e.contrib4.items():
            if d in cset:
                b4[d] += v
        for d, v in e.contrib3.items():
            if d in cset:
                b3[d] += v
                expo[d] += e.w * e.beta
    r4 = [b4.get(d, 0.0) for d in cal_dates]
    r3 = [b3.get(d, 0.0) for d in cal_dates]
    mean_expo = float(np.mean([expo.get(d, 0.0) for d in cal_dates])) if cal_dates else 0.0
    return r4, r3, mean_expo


def _block_boot(idx_rng, n: int, block: int) -> np.ndarray:
    """Circular block-bootstrap index vector of length n, block length `block`."""
    out = np.empty(n, dtype=np.int64)
    filled = 0
    while filled < n:
        start = idx_rng.integers(0, n)
        take = min(block, n - filled)
        for k in range(take):
            out[filled + k] = (start + k) % n
        filled += take
    return out


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    global _FIRES
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    _FIRES = defaultdict(list)
    for sg, st, fa in rows:
        _FIRES[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    events, cal_topix, ret_arr = _build_sleeve_events()

    # stitched per-FY: confluence (1.4M, 2.0M) deterministic + sleeve event partition
    stitched_dates: list[datetime.date] = []
    conf14: list[float] = []
    conf20: list[float] = []
    fy_of_date: dict[datetime.date, str] = {}
    sleeve_events_fy: dict[str, list[SleeveEvent]] = {}

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
        if not caches:
            continue
        _CORR_MAPS[cfg.label] = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        _ZS_MAPS[cfg.label] = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        n_dts, _ = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        r14 = _confluence_fy_returns(cfg, caches, stock_dts, cal, _CONF_BUDGET)
        r20 = _confluence_fy_returns(cfg, caches, stock_dts, cal, _TOTAL)
        # drop first day per FY (matches benchmark stitching)
        for d in cal[1:]:
            fy_of_date[d] = cfg.label
        stitched_dates += cal[1:]
        conf14 += r14[1:]
        conf20 += r20[1:]
        sleeve_events_fy[cfg.label] = [e for e in events if cfg.start <= e.entry_date <= cfg.end]
        logger.info("  {} confluence done ({} sleeve up-events in window)",
                    cfg.label, len(sleeve_events_fy[cfg.label]))

    conf14_a = np.asarray(conf14)
    conf20_a = np.asarray(conf20)
    N = len(stitched_dates)
    logger.info("stitched {} trading days across {} FYs", N, len(sleeve_events_fy))

    # precompute each FY's contiguous [start,end) slice in the stitched timeline (once)
    fy_ranges = []
    pos = 0
    for cfg in _FYS:
        if cfg.label not in sleeve_events_fy:
            continue
        cal_fy = [d for d in stitched_dates[pos:] if fy_of_date.get(d) == cfg.label]
        ln = len(cal_fy)
        fy_ranges.append((cfg.label, pos, ln, cal_fy))
        pos += ln

    # ── per-seed: paired fill shuffle + block bootstrap → ΔSharpe(4-3) ────────────
    def _seed_curves(seed: int):
        rng = np.random.default_rng(seed)
        s4 = np.zeros(N)
        s3 = np.zeros(N)
        expo_acc = []
        for label, start, ln, cal_fy in fy_ranges:
            filled = _sleeve_fill(sleeve_events_fy[label], ret_arr, rng)
            r4, r3, mexpo = _sleeve_daily(filled, cal_fy)
            s4[start:start + ln] = r4
            s3[start:start + ln] = r3
            expo_acc.append(mexpo)
        b4 = conf14_a * _W_CONF + _W_SLV * s4
        b3 = conf14_a * _W_CONF + _W_SLV * s3
        return b4, b3, float(np.mean(expo_acc)) if expo_acc else 0.0

    def _delta_dist(block: int):
        d = np.empty(_K)
        for k in range(_K):
            b4, b3 = _CURVES4[k], _CURVES3[k]
            ridx = _block_boot(np.random.default_rng(1_000_000 + k), N, block)
            d[k] = _sharpe(b4[ridx]) - _sharpe(b3[ridx])
        return d

    # cache the per-seed curves once (fill shuffle), reuse for L sweep
    _CURVES4, _CURVES3, expos = [], [], []
    for k in range(_K):
        b4, b3, mexpo = _seed_curves(k)
        _CURVES4.append(b4)
        _CURVES3.append(b3)
        expos.append(mexpo)
        if (k + 1) % 500 == 0:
            logger.info("  built {} / {} seed curves", k + 1, _K)

    d60 = _delta_dist(_BLOCK)
    d20 = _delta_dist(20)
    d120 = _delta_dist(120)

    # point curves (seed 0, no bootstrap) for the diagnostics
    b4_0, b3_0 = _CURVES4[0], _CURVES3[0]
    mean_expo = float(np.mean(expos))
    book2 = conf14_a * _W_CONF + _W_SLV * mean_expo * _topix_aligned(stitched_dates, cal_topix)
    book1 = conf20_a

    print("\n" + "=" * 96)
    print("PEAD SLEEVE — SELECTION-ALPHA NULL (book4 real − book3 β·index, ¥2.0M blended)")
    print("=" * 96)
    print(f"K={_K} paired seeds | stitched {N} days | sleeve up-events filled (seed0) — "
          f"see per-FY below")
    print(f"\nPoint curves (seed 0, no bootstrap):")
    for lab, bk in [("book1 conf¥2.0M (off)", book1), ("book2 +const-index", book2),
                    ("book3 +β·index-sched", b3_0), ("book4 +real sleeve", b4_0)]:
        print(f"  {lab:<24} Sharpe {_sharpe(bk):+.3f}  total {_total_return(bk)*100:+.1f}%")

    print(f"\n  ladder deltas (seed0 point):")
    print(f"    (4 vs 3) selection = {_sharpe(b4_0)-_sharpe(b3_0):+.3f}  [BINDING — null below]")
    print(f"    (3 vs 2) timing    = {_sharpe(b3_0)-_sharpe(book2):+.3f}  [diagnostic only]")
    print(f"    (2 vs 1) leverage  = {_sharpe(book2)-_sharpe(book1):+.3f}  [never credited]")

    print(f"\nBINDING NULL — ΔSharpe(book4 − book3), {_K} paired (fill-shuffle + block-boot):")
    for lab, d in [("L=60 (primary)", d60), ("L=20 (robust)", d20), ("L=120 (robust)", d120)]:
        p = (d > 0).mean()
        lo, mid, hi = np.percentile(d, [2.5, 50, 97.5])
        gate = "PASS" if (p >= 0.95 and lo > 0) else "FAIL"
        print(f"  {lab:<16} mean {d.mean():+.3f} | P(Δ>0) {p:.3f} | "
              f"95% CI [{lo:+.3f}, {hi:+.3f}] | median {mid:+.3f}  → gate {gate}")

    # independence (β-stripped sleeve-alpha daily vs confluence β-stripped daily)
    sleeve_alpha = (b4_0 - b3_0)                     # already the β-stripped sleeve stream (×W)
    conf_alpha = _beta_strip(conf14_a, stitched_dates, cal_topix)
    m = np.isfinite(sleeve_alpha) & np.isfinite(conf_alpha)
    nz = m & ((sleeve_alpha != 0) | (conf_alpha != 0))
    icorr = float(np.corrcoef(sleeve_alpha[nz], conf_alpha[nz])[0, 1]) if nz.sum() > 30 else float("nan")
    print(f"\nSECONDARY GUARDRAILS:")
    print(f"  independence: β-stripped sleeve vs confluence daily corr = {icorr:+.3f} "
          f"(<0.50 required) → {'OK' if abs(icorr) < 0.5 else 'FAIL'}")

    # OOS direction + do-no-harm per FY (blended raw book4 vs book1)
    print(f"\n  per-FY blended RAW return (book4 vs book1=conf¥2.0M, seed0):")
    worst_harm = None
    for cfg in _FYS:
        if cfg.label not in sleeve_events_fy:
            continue
        idx = [i for i, d in enumerate(stitched_dates) if fy_of_date.get(d) == cfg.label]
        if not idx:
            continue
        r4 = _total_return(b4_0[idx]); r1 = _total_return(book1[idx])
        diff = (r4 - r1) * 100
        tag = "  ← OOS FY2025" if cfg.label == "FY2025" else ""
        if worst_harm is None or diff < worst_harm[1]:
            worst_harm = (cfg.label, diff)
        print(f"    {cfg.label}: book4 {r4*100:+.1f}%  book1 {r1*100:+.1f}%  Δ {diff:+.2f}pp{tag}")
    full_diff = (_total_return(b4_0) - _total_return(book1)) * 100
    print(f"  do-no-harm: worst-FY Δ = {worst_harm[1]:+.2f}pp ({worst_harm[0]}); "
          f"bound ≥ −1.5pp → {'OK' if worst_harm[1] >= -1.5 else 'BREACH (review)'}")
    print(f"  OOS direction: full-sample Δ {full_diff:+.2f}pp; FY2025 diagnostic above "
          f"(sign-agreement = direction check, not a gate)")

    p60 = (d60 > 0).mean(); lo60 = np.percentile(d60, 2.5)
    verdict = "DEPLOY to T1" if (p60 >= 0.95 and lo60 > 0 and abs(icorr) < 0.5) else "REJECT"
    print(f"\n  VERDICT: {verdict}  (binding gate L=60 {'PASS' if p60>=0.95 and lo60>0 else 'FAIL'}"
          f", independence {'OK' if abs(icorr)<0.5 else 'FAIL'})")
    print("  No-iteration clause: this design is final; a reject is a signpost toward capacity/"
          "universe expansion (separate pre-reg), not a re-run.")


def _topix_aligned(stitched_dates, cal_topix) -> np.ndarray:
    """TOPIX day-over-day return aligned to stitched_dates (0 where missing)."""
    col = {d: i for i, d in enumerate(cal_topix)}
    # need topix series; rebuild dod from a fresh query-free path: stored on first call
    arr = np.zeros(len(stitched_dates))
    for i, d in enumerate(stitched_dates):
        j = col.get(d)
        if j is not None and j > 0:
            arr[i] = _TOPIX_DOD[j]
    return arr


def _beta_strip(conf_daily, stitched_dates, cal_topix) -> np.ndarray:
    """Confluence β-stripped daily stream: conf − β̂·topix (β̂ via OLS over the curve)."""
    tx = _topix_aligned(stitched_dates, cal_topix)
    m = np.isfinite(conf_daily) & np.isfinite(tx)
    if m.sum() < 30 or tx[m].std() == 0:
        return np.asarray(conf_daily)
    b = float(np.cov(conf_daily[m], tx[m])[0, 1] / tx[m].var())
    return conf_daily - b * tx


_TOPIX_DOD: np.ndarray = np.zeros(0)


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
