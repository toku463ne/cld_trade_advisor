"""MN-PEAD breadth/cost feasibility probe (read-only, advisory — NOT a pre-reg null).

The validated edge ([[project-jquants-pead-universe]]) is a CROSS-SECTIONAL spread:
(up-revision − down-revision) β-stripped 60-bar CAR = +2.51% on the N225 cohort (n=4,679).
That number is a mean over ~thousands of events. The binding question for a DEPLOYABLE
market-neutral book at ¥2M / manual is BREADTH:

    when you can only hold ~K names per side (not 1,792 up / 1,092 down), does the realized
    long-up / short-down book still capture the +2.51% spread net of cost, or does the
    idiosyncratic variance of K names per side swamp it?

This probe answers that directly. It builds a dollar/beta-neutral PEAD book (long up-revision
cohort names, short down-revision cohort names, equal-weight among held, 60-bar TimeStop, ≤K
slots/side, skip-when-full) and reports:
  • the K=∞ ceiling (hold ALL available) — confirms the machinery reproduces ~+2.51%;
  • a BREADTH SWEEP K∈{3,6,10,20,∞}: selection-bootstrap (200 random fill orders) Sharpe & annual
    return — the band width IS the idiosyncratic/breadth noise the operator asked about;
  • a COST sweep (0/30/60 bps roundtrip + 1.1%/yr short borrow);
  • per-FY sign stability and a circular block-bootstrap (L=60) time-CI on the K=6 net book.

MN harvests the full spread regardless of where zero sits (book = long CARs − short CARs) and
nets out market beta — strictly better than the rejected long-only sleeve on both counts. The
only open question is whether 6/side is enough names. Read-only.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.mn_pead_feasibility_probe
"""
from __future__ import annotations

import datetime
import math
import sys
from collections import Counter, defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

from src.analysis.pead_forecast_revision import (
    Disclosure, beta, doc_basis, pair_same_fy_revisions, revision_surprise,
    tradable_entry_day,
)
from src.data.db import get_session

_BETA_WIN = 60
_HOLD = 60
_BORROW_YR = 0.011                       # 制度信用 borrow on the short leg
_FY_START = datetime.date(2018, 4, 1)    # complete-FY range (matches sleeve null stitched span)
_FY_END = datetime.date(2025, 3, 31)
_KS = [3, 6, 10, 20, 10_000]             # 10_000 ≈ ∞ (no cap)
_N_SEL = 200                             # selection-bootstrap draws (idiosyncratic noise)
_N_BLOCK = 2000                          # time block-bootstrap draws
_BLOCK = 60


class MnEvent:
    __slots__ = ("code", "entry_date", "ei", "sign", "car60", "path", "in225")

    def __init__(self, code, entry_date, ei, sign, car60, path, in225):
        self.code = code            # jq local code (identity only)
        self.entry_date = entry_date
        self.ei = ei                # index into the TOPIX trading calendar
        self.sign = sign            # +1 = up-revision (long) / -1 = down-revision (short)
        self.car60 = car60          # β-stripped 60-bar CAR (for the ceiling sanity check)
        self.path = path            # dict{date: daily β-stripped return} over the 60-bar hold
        self.in225 = in225          # True if a 225 N225-deployment-cohort name


def _fy_label(d: datetime.date) -> int:
    """JP fiscal year by END year (project convention): Apr2024–Mar2025 → 2025."""
    return d.year + 1 if d.month >= 4 else d.year


def _load_cohort225() -> set[str]:
    """The clean 225 N225 deployment cohort = union of production cluster-run members
    (classified2017..2024). NOT ohlcv_1d distinct — that is now contaminated by the 2,576
    inert universe-expansion bridge codes ([[project-jquants-pead-universe]])."""
    from src.analysis.models import StockClusterMember, StockClusterRun
    with get_session() as s:
        ids = [i for (i, fy) in s.execute(
            select(StockClusterRun.id, StockClusterRun.fiscal_year)).all()
            if fy.startswith("classified") and "exp" not in fy]
        if not ids:
            return set()
        return set(s.execute(select(StockClusterMember.stock_code)
                             .where(StockClusterMember.run_id.in_(ids))).scalars().all())


# ── build cohort up & down revision events with β-stripped daily paths ────────────
def _build_events():
    from src.data.jquants_collector import to_yf_code
    from src.data.jquants_models import JqDailyQuote, JqStatement, JqTopix
    from src.data.models import Ohlcv1d

    cohort225 = _load_cohort225()
    with get_session() as s:
        stmts = s.execute(
            select(JqStatement.local_code, JqStatement.disclosed_date,
                   JqStatement.disclosed_time, JqStatement.current_fiscal_year_end_date,
                   JqStatement.forecast_earnings_per_share, JqStatement.type_of_document)
        ).all()
        topix = s.execute(select(JqTopix.date, JqTopix.close)
                          .where(JqTopix.close.isnot(None)).order_by(JqTopix.date)).all()
        # WIDE universe = ohlcv_1d (225 + 2,576 inert bridge codes = liquid∩affordable tier)
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
            ci, ri = col_of.get(d), row_of.get(code)
            if ci is not None and ri is not None and ac is not None:
                arr[ri, ci] = float(ac)

    topix_dod = np.full(len(cal), np.nan)
    topix_dod[1:] = topix_arr[1:] / topix_arr[:-1] - 1.0

    raw = defaultdict(list)
    for code, dd, dt, fy, feps, tod in stmts:
        raw[code].append((dd, dt, fy, feps, tod))
    by_code = {}
    for code, rows in raw.items():
        fin = Counter(b for b in (doc_basis(r[4]) for r in rows) if b)
        modal = fin.most_common(1)[0][0] if fin else None
        by_code[code] = [Disclosure(dd, dt, fy, feps, doc_basis(tod) or modal)
                         for dd, dt, fy, feps, tod in rows]

    events: list[MnEvent] = []
    n_up = n_dn = 0
    for code, discs in by_code.items():
        ri = row_of.get(code)
        yf = to_yf_code(code)
        if ri is None or yf not in cohort:
            continue
        in225 = yf in cohort225
        srow = arr[ri]
        for prev, curr in pair_same_fy_revisions(discs):
            if curr.fy_end is None:
                continue
            entry = tradable_entry_day(curr.disclosed_date, curr.disclosed_time, cal)
            if entry is None or not (_FY_START <= entry <= _FY_END):
                continue
            ei = col_of[entry]
            if ei < _BETA_WIN + 1 or ei + _HOLD >= len(cal):
                continue
            price = srow[ei - 1]
            if not (price > 0):
                continue
            sp = revision_surprise(prev.forecast_eps, curr.forecast_eps, float(price))
            if sp is None or sp == 0:                       # skip reaffirmations
                continue
            sw, mw = srow[ei - _BETA_WIN - 1:ei], topix_arr[ei - _BETA_WIN - 1:ei]
            b = beta(sw[1:] / sw[:-1] - 1.0, mw[1:] / mw[:-1] - 1.0)
            if b is None:
                continue
            path, ok, car = {}, True, 0.0
            base_s, base_t = srow[ei], topix_arr[ei]
            for j in range(ei + 1, ei + _HOLD + 1):
                s0, s1 = srow[j - 1], srow[j]
                if not (s0 > 0) or not (s1 > 0) or not np.isfinite(topix_dod[j]):
                    ok = False
                    break
                path[cal[j]] = (s1 / s0 - 1.0) - b * topix_dod[j]
            if not ok:
                continue
            last = ei + _HOLD
            car = (srow[last] / base_s - 1.0) - b * (topix_arr[last] / base_t - 1.0)
            sign = 1 if sp > 0 else -1
            n_up += sign == 1
            n_dn += sign == -1
            events.append(MnEvent(code, entry, ei, sign, float(car), path, in225))
    n225 = sum(e.in225 for e in events)
    logger.info("revision events FY2018-2025: {} up / {} down  ({} are 225-cohort)",
                n_up, n_dn, n225)
    cal_active = [d for d in cal if _FY_START <= d <= _FY_END]
    return events, cal_active


# ── slot fill (per side): ≤K held, 60-bar hold, skip-when-full, shuffled order ─────
def _fill_side(side_events: list[MnEvent], K: int, rng) -> list[MnEvent]:
    by_day = defaultdict(list)
    for e in side_events:
        by_day[e.entry_date].append(e)
    held: list[MnEvent] = []
    filled: list[MnEvent] = []
    for d in sorted(by_day):
        ei_d = by_day[d][0].ei
        held = [h for h in held if h.ei + _HOLD >= ei_d]
        free = K - len(held)
        if free <= 0:
            continue
        elig = by_day[d][:]
        if rng is not None:
            rng.shuffle(elig)
        for e in elig[:free]:
            held.append(e)
            filled.append(e)
    return filled


def _book_daily(events, K, cal, rng):
    """Dollar/beta-neutral daily book return over `cal`: mean(held-long β-stripped) −
    mean(held-short β-stripped), equal-weight among currently-held (gross 1.0/side).
    Returns (daily_array, n_long_filled, n_short_filled, frac_days_short_deployed)."""
    longs = _fill_side([e for e in events if e.sign > 0], K, rng)
    shorts = _fill_side([e for e in events if e.sign < 0], K, rng)
    lsum, lcnt = defaultdict(float), defaultdict(int)
    ssum, scnt = defaultdict(float), defaultdict(int)
    for e in longs:
        for d, r in e.path.items():
            lsum[d] += r
            lcnt[d] += 1
    for e in shorts:
        for d, r in e.path.items():
            ssum[d] += r
            scnt[d] += 1
    daily = np.zeros(len(cal))
    short_days = 0
    for i, d in enumerate(cal):
        lr = lsum[d] / lcnt[d] if lcnt[d] else 0.0
        sr = ssum[d] / scnt[d] if scnt[d] else 0.0
        daily[i] = lr - sr
        short_days += scnt[d] > 0
    return daily, len(longs), len(shorts), short_days / max(len(cal), 1)


def _sharpe(a: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64)
    sd = a.std(ddof=1)
    return float(a.mean() / sd * math.sqrt(252)) if sd > 0 else float("nan")


def _annual_return(a: np.ndarray) -> float:
    return float(a.mean() * 252)


def _annual_cost(cost_bps: float, borrow: float, short_frac: float) -> float:
    """Constant annual drag. Both sides turn over fully every 60 bars → 252/60≈4.2 roundtrips/yr;
    a roundtrip trades 2.0 gross (1 long+1 short) twice (in+out) = 4.0 one-way gross/cycle →
    16.8 gross-units/yr × cost_bps. Plus borrow on the short side's deployed fraction."""
    turnover = (252.0 / _HOLD) * 4.0 * (cost_bps / 10_000.0)
    return turnover + borrow * short_frac


def _net(daily: np.ndarray, cost_bps: float, short_frac: float) -> np.ndarray:
    return daily - _annual_cost(cost_bps, _BORROW_YR, short_frac) / 252.0


def _block_boot_sharpe(daily: np.ndarray, K_draws: int, block: int):
    n = len(daily)
    out = np.empty(K_draws)
    for k in range(K_draws):
        rng = np.random.default_rng(7_000_000 + k)
        idx = np.empty(n, dtype=np.int64)
        f = 0
        while f < n:
            st = rng.integers(0, n)
            take = min(block, n - f)
            idx[f:f + take] = (np.arange(st, st + take)) % n
            f += take
        out[k] = _sharpe(daily[idx])
    return out


def _report(events, cal, title: str) -> None:
    n_up = sum(e.sign > 0 for e in events)
    n_dn = sum(e.sign < 0 for e in events)
    print("\n" + "=" * 96)
    print(f"MN-PEAD FEASIBILITY — {title}")
    print("  long up-revision / short down-revision, β/dollar-neutral, TimeStop(60)")
    print("=" * 96)
    print(f"revision events FY2018–2025: {n_up} up (long) / {n_dn} down (short)  "
          f"| stitched {len(cal)} trading days")
    if min(n_up, n_dn) < 50:
        print("  too few events — skipping.")
        return

    # ── K=∞ ceiling: hold ALL available → should reproduce the validated cross-sectional spread ──
    up_cars = np.array([e.car60 for e in events if e.sign > 0])
    dn_cars = np.array([e.car60 for e in events if e.sign < 0])
    xsec_spread = up_cars.mean() - dn_cars.mean()
    print(f"\nCROSS-SECTIONAL CHECK (all events, β-stripped 60-bar CAR):")
    print(f"  up mean {up_cars.mean()*100:+.2f}%  down mean {dn_cars.mean()*100:+.2f}%  "
          f"(up−down) = {xsec_spread*100:+.2f}%   [validated 225-cohort spread ≈ +2.51%]")

    # ── breadth sweep ──────────────────────────────────────────────────────────────
    print(f"\nBREADTH SWEEP — realized book Sharpe & annual return ({_N_SEL} random fill orders "
          f"per finite K; cost = 30bps roundtrip + {_BORROW_YR*100:.1f}%/yr borrow):")
    print(f"  {'K/side':>7} | {'gross Sharpe (sel-band)':<32} | {'net@30bps Sharpe':<24} | "
          f"{'net ann.ret':<22} | filled L/S")
    k6_net = None
    for K in _KS:
        if K >= 10_000:                                  # ceiling: deterministic, hold all
            daily, nl, ns, sf = _book_daily(events, K, cal, None)
            net = _net(daily, 30.0, sf)
            gband = f"{_sharpe(daily):+.2f} (hold-all)"
            nband = f"{_sharpe(net):+.2f}"
            rband = f"{_annual_return(net)*100:+.1f}%/yr"
            print(f"  {'inf':>7} | {gband:<32}| {nband:<24}| {rband:<22}| {nl}/{ns}")
            continue
        gss, nss, nars = [], [], []
        nls = nss_cnt = 0
        sf_acc = []
        for j in range(_N_SEL):
            rng = np.random.default_rng(j)
            daily, nl, ns, sf = _book_daily(events, K, cal, rng)
            gss.append(_sharpe(daily))
            net = _net(daily, 30.0, sf)
            nss.append(_sharpe(net))
            nars.append(_annual_return(net))
            nls, nss_cnt = nl, ns
            sf_acc.append(sf)
        gss, nss, nars = np.array(gss), np.array(nss), np.array(nars)
        glo, ghi = np.percentile(gss, [2.5, 97.5])
        nlo, nhi = np.percentile(nss, [2.5, 97.5])
        rlo, rhi = np.percentile(nars, [2.5, 97.5])
        gband = f"{gss.mean():+.2f} [{glo:+.2f},{ghi:+.2f}]"
        nband = f"{nss.mean():+.2f} [{nlo:+.2f},{nhi:+.2f}]"
        rband = f"{nars.mean()*100:+.1f}% [{rlo*100:+.0f},{rhi*100:+.0f}]%"
        print(f"  {K:>7} | {gband:<32}| {nband:<24}| {rband:<22}| {nls}/{nss_cnt}")
        if K == 6:
            k6_net = nss

    # ── cost sweep at K=6 (mean over selection draws) ───────────────────────────────
    print(f"\nCOST SWEEP at K=6/side (mean realized net Sharpe over {_N_SEL} fill orders):")
    for cb in (0.0, 30.0, 60.0):
        for borrow_on in (True, False):
            ss = []
            for j in range(_N_SEL):
                rng = np.random.default_rng(j)
                daily, _, _, sf = _book_daily(events, 6, cal, rng)
                drag = _annual_cost(cb, _BORROW_YR if borrow_on else 0.0, sf) / 252.0
                ss.append(_sharpe(daily - drag))
            tag = "borrow on " if borrow_on else "borrow off"
            print(f"  {cb:>4.0f}bps  {tag}: net Sharpe {np.mean(ss):+.2f}  "
                  f"ann.cost {_annual_cost(cb, _BORROW_YR if borrow_on else 0.0, sf)*100:.1f}%/yr")

    # ── per-FY sign stability at K=6 (deterministic time-order book) ────────────────
    print(f"\nPER-FY realized net@30bps return — K=6, time-order fill (sign stability):")
    daily6, _, _, sf6 = _book_daily(events, 6, cal, np.random.default_rng(0))
    net6 = _net(daily6, 30.0, sf6)
    by_fy = defaultdict(list)
    for d, r in zip(cal, net6):
        by_fy[_fy_label(d)].append(r)
    npos = 0
    for fy in sorted(by_fy):
        arr = np.array(by_fy[fy])
        tot = float(np.prod(1.0 + arr) - 1.0)
        npos += tot > 0
        tag = "  ← OOS FY2025" if fy == 2025 else ""
        print(f"  FY{fy}: {tot*100:+.2f}%  (Sharpe {_sharpe(arr):+.2f}){tag}")
    print(f"  sign-positive FYs: {npos}/{len(by_fy)}")

    # ── time block-bootstrap CI on the K=6 net book ─────────────────────────────────
    if k6_net is not None:
        dist = _block_boot_sharpe(net6, _N_BLOCK, _BLOCK)
        p = (dist > 0).mean()
        lo, mid, hi = np.percentile(dist, [2.5, 50, 97.5])
        print(f"\nTIME CI — K=6 net@30bps, circular block-bootstrap L={_BLOCK} ({_N_BLOCK} draws):")
        print(f"  Sharpe median {mid:+.2f} | 95% CI [{lo:+.2f}, {hi:+.2f}] | P(Sharpe>0) {p:.3f}")
        print(f"  selection-band net@30bps Sharpe (idiosyncratic noise): "
              f"mean {k6_net.mean():+.2f}, 95% [{np.percentile(k6_net,2.5):+.2f}, "
              f"{np.percentile(k6_net,97.5):+.2f}]")


def run() -> None:
    events, cal = _build_events()
    if not events:
        logger.warning("no events — jq_* not loaded?")
        return
    _report([e for e in events if e.in225], cal, "N225 COHORT (~225, validated deployment cohort)")
    _report(events, cal, "WIDE TIER (liquid∩affordable ~2,785 bridge universe)")

    print("\n" + "=" * 96)
    print("HOW TO READ")
    print("=" * 96)
    print("• K=∞ ceiling ≈ the validated cross-sectional spread = machinery check (no breadth/cost "
          "limit).\n"
          "• BREADTH-SWEEP band width = idiosyncratic noise of holding only K names/side. If the\n"
          "  K=6 net selection-band straddles 0 (or net Sharpe < ~0.3), idiosyncratic variance\n"
          "  swamps the spread at deployable breadth → MN-PEAD is sub-power at ¥2M (the breadth\n"
          "  wall; same family as the long-only sleeve reject). K=6 net clearly >0 + tight band\n"
          "  → warrants a full pre-reg null.\n"
          "• The N225 cohort tests the VALIDATED +2.51% signal as deployable today. The WIDE tier\n"
          "  tests the directions-doc claim that PEAD's flat size-gradient could beat the breadth\n"
          "  wall by widening the menu (more names/side). ADVISORY ONLY — no deployment decision.")
    print("  CAVEATS: capital-normalized (gross 1.0/side), close-to-close adj returns. (1) Lot\n"
          "  granularity at ¥2M (¥~167k/slot → 1 lot needs price ≲¥1,670/sh) is an extra\n"
          "  affordability drag not modeled (sleeve lost ~30% of names at ¥0.3M/slot). (2) Short\n"
          "  leg assumes every name is borrowable — on the WIDE tier many mid/small-caps are NOT\n"
          "  貸借銘柄 (制度信用 shortable), so the wide short leg is partly unrealizable. (3) Cost\n"
          "  model = quarterly full two-side turnover; 30bps→~6%/yr, the dominant drag.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
