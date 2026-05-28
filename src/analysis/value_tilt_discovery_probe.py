"""Value / value+momentum tilt — discovery probe (read-only, advisory — NOT a pre-reg null).

After MN-PEAD was refuted at deployable breadth ([[project-jquants-pead-universe]]), the program
front-runner is a CONCENTRATED VALUE LONG TILT ([[project-program-direction-2026]] direction 4):
breadth-tolerant, long-only (no short-leg cost/borrow), the documented Japan winner (Asness:
momentum fails in Japan but VALUE works; value+momentum combined ≈0.65 Sharpe via their −0.55
return correlation). This probe asks three discovery questions on OUR data:

  Q1 (existence)  — does the Japan value premium show up at all? Universe decile long-short
                    (cheapest − priciest), B/M and E/P, monthly.
  Q2 (deployable) — does it survive as a CONCENTRATED LONG TILT at K∈{6,12,20} on the 223-name
                    cohort, vs the equal-weight cohort benchmark, net of 30bps, β-stripped vs TOPIX?
                    (This is the real ¥2M-manual question — the breadth-tolerant claim.)
  Q3 (combo)      — is value+momentum better than either alone, and is the value/momentum return
                    correlation negative (the diversification that earns the ~0.65 combined Sharpe)?

POINT-IN-TIME & SPLIT-ROBUST: a name's value metric uses its latest FY disclosure with
disclosed_date ≤ rebalance (no look-ahead) and fy_end within 18 months. Market cap is anchored at
the disclosure date (raw close × FY net shares — same date, no split mismatch) and carried to the
rebalance by the ADJUSTED-price ratio adj(disc)/adj(t) (split-robust, shares cancel):
    B/M_t = equity / mktcap_disc × adj(disc)/adj(t)   ;   E/P_t = profit / mktcap_disc × adj(disc)/adj(t)
Momentum = 12-1 month adjusted-price return (skip last 21 bars). Returns use adj_close (splits
adjusted; J-Quants adj does NOT add back dividends → a small downward bias on all books equally).

DISCOVERY ONLY — rich diagnostics, no binding gate; a pre-reg with a frozen gate follows if this
clears. Read-only. Run:
  PYTHONPATH=. uv run --env-file devenv python -m src.analysis.value_tilt_discovery_probe
"""
from __future__ import annotations

import bisect
import datetime
import math
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

from src.data.db import get_session

_START = datetime.date(2017, 7, 1)     # need 252-bar momentum lookback before the first rebalance
_END = datetime.date(2026, 4, 30)
_MOM_LB, _MOM_SKIP = 252, 21
_MAX_STALE_D = 18 * 30                  # fundamental no older than ~18 months
_COST_BPS = 30.0
_KS = (6, 12, 20)


def _load_cohort225() -> set[str]:
    from src.analysis.models import StockClusterMember, StockClusterRun
    with get_session() as s:
        ids = [i for (i, fy) in s.execute(
            select(StockClusterRun.id, StockClusterRun.fiscal_year)).all()
            if fy.startswith("classified") and "exp" not in fy]
        return set(s.execute(select(StockClusterMember.stock_code)
                             .where(StockClusterMember.run_id.in_(ids))).scalars().all())


def _load():
    """Returns cal, col_of, codes, row_of, adj, rawc, topix_adj, fundamentals, cohort_rows."""
    from src.data.jquants_collector import to_yf_code
    from src.data.jquants_models import JqDailyQuote, JqStatement, JqTopix

    cohort225 = _load_cohort225()
    with get_session() as s:
        topix = s.execute(select(JqTopix.date, JqTopix.close)
                          .where(JqTopix.close.isnot(None)).order_by(JqTopix.date)).all()
        codes = [c for (c,) in s.execute(select(JqDailyQuote.code).distinct()
                                         .order_by(JqDailyQuote.code))]
        fy = s.execute(
            select(JqStatement.local_code, JqStatement.disclosed_date,
                   JqStatement.current_fiscal_year_end_date, JqStatement.equity,
                   JqStatement.profit, JqStatement.shares_outstanding_fy,
                   JqStatement.treasury_shares_fy)
            .where(JqStatement.type_of_current_period == "FY")).all()
        cal = [d for d, _ in topix]
        col_of = {d: i for i, d in enumerate(cal)}
        row_of = {c: i for i, c in enumerate(codes)}
        topix_adj = np.array([float(c) for _, c in topix], dtype=np.float64)
        adj = np.full((len(codes), len(cal)), np.nan, dtype=np.float32)
        rawc = np.full((len(codes), len(cal)), np.nan, dtype=np.float32)
        stream = s.connection().execution_options(stream_results=True, yield_per=200_000)
        for code, d, ac, cl in stream.execute(
                select(JqDailyQuote.code, JqDailyQuote.date,
                       JqDailyQuote.adj_close, JqDailyQuote.close)):
            ci, ri = col_of.get(d), row_of.get(code)
            if ci is not None and ri is not None:
                if ac is not None:
                    adj[ri, ci] = float(ac)
                if cl is not None:
                    rawc[ri, ci] = float(cl)

    # per code: list of (disclosed_idx, fy_end, equity, profit, net_shares) anchored to a cal idx
    funds: dict[str, list[tuple]] = defaultdict(list)
    cohort_rows: set[int] = set()
    for lc, dd, fend, eq, profit, sh, tr in fy:
        ri = row_of.get(lc)
        if ri is None or dd is None or fend is None or eq is None or not sh:
            continue
        di = bisect.bisect_right(cal, dd) - 1            # nearest trading day ≤ disclosed_date
        if di < 0:
            continue
        net_sh = float(sh) - float(tr or 0)
        if net_sh <= 0:
            continue
        funds[lc].append((di, fend, float(eq), float(profit) if profit is not None else None, net_sh))
        if to_yf_code(lc) in cohort225:
            cohort_rows.add(ri)
    for lc in funds:
        funds[lc].sort(key=lambda r: r[0])
    logger.info("loaded {} codes, {} cal days, {} codes w/ FY funds ({} cohort rows)",
                len(codes), len(cal), len(funds), len(cohort_rows))
    return cal, col_of, codes, row_of, adj, rawc, topix_adj, funds, cohort_rows


def _rebalance_indices(cal):
    """First trading-day index of each month within [_START, _END]."""
    out, seen = [], set()
    for i, d in enumerate(cal):
        if d < _START or d > _END:
            continue
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def _metrics_at(ri, ti, adj, rawc, funds, code, row_of):
    """(bm, ey, mom) at cal index ti for code, or None for any unavailable. Split-robust."""
    recs = funds.get(code)
    if not recs:
        return None
    at = adj[ri, ti]
    if not (at > 0):
        return None
    # latest FY disclosure ≤ ti, fy_end within staleness window
    pick = None
    for di, fend, eq, profit, net_sh in reversed(recs):
        if di > ti:
            continue
        d_t = _cal_date_gap(ti, di)
        if d_t > _MAX_STALE_D:
            break
        pick = (di, fend, eq, profit, net_sh)
        break
    if pick is None:
        return None
    di, fend, eq, profit, net_sh = pick
    rc, ad = rawc[ri, di], adj[ri, di]
    if not (rc > 0) or not (ad > 0):
        return None
    mktcap_disc = rc * net_sh
    if mktcap_disc <= 0:
        return None
    pf = ad / at                                          # adj(disc)/adj(t) — split-robust carry
    bm = (eq / mktcap_disc) * pf
    ey = (profit / mktcap_disc) * pf if profit is not None else None
    # 12-1 momentum
    j0, j1 = ti - _MOM_LB, ti - _MOM_SKIP
    mom = None
    if j0 >= 0:
        a0, a1 = adj[ri, j0], adj[ri, j1]
        if a0 > 0 and a1 > 0:
            mom = a1 / a0 - 1.0
    return bm, ey, mom


_CAL_REF: list = []


def _cal_date_gap(ti, di) -> int:
    return (_CAL_REF[ti] - _CAL_REF[di]).days


def _xrank(vals: np.ndarray) -> np.ndarray:
    """Cross-sectional rank in [0,1] (higher input → higher rank). NaN → NaN."""
    out = np.full(len(vals), np.nan)
    m = np.isfinite(vals)
    if m.sum() < 2:
        return out
    order = np.argsort(vals[m])
    r = np.empty(m.sum())
    r[order] = np.linspace(0.0, 1.0, m.sum())
    out[m] = r
    return out


def _ann_sharpe(monthly: np.ndarray) -> float:
    a = np.asarray(monthly, dtype=np.float64)
    sd = a.std(ddof=1)
    return float(a.mean() / sd * math.sqrt(12)) if sd > 0 else float("nan")


def _cagr(monthly: np.ndarray) -> float:
    a = np.asarray(monthly, dtype=np.float64)
    if a.size == 0:
        return float("nan")
    return float((np.prod(1.0 + a)) ** (12.0 / a.size) - 1.0)


def _tstat(monthly: np.ndarray) -> float:
    a = np.asarray(monthly, dtype=np.float64)
    sd = a.std(ddof=1)
    return float(a.mean() / (sd / math.sqrt(a.size))) if sd > 0 and a.size > 1 else float("nan")


def _alpha_beta(book: np.ndarray, mkt: np.ndarray):
    m = np.isfinite(book) & np.isfinite(mkt)
    if m.sum() < 12 or mkt[m].var() == 0:
        return float("nan"), float("nan")
    b = float(np.cov(book[m], mkt[m])[0, 1] / mkt[m].var())
    alpha_m = float(book[m].mean() - b * mkt[m].mean())
    return alpha_m * 12.0, b               # annualized alpha, beta


def run() -> None:
    cal, col_of, codes, row_of, adj, rawc, topix_adj, funds, cohort_rows = _load()
    global _CAL_REF
    _CAL_REF = cal
    rebs = _rebalance_indices(cal)
    if len(rebs) < 24:
        logger.warning("too few rebalances ({})", len(rebs))
        return

    # per rebalance: assemble eligible names with (bm, ey, mom, fwd_ret), for universe & cohort
    months: list[tuple] = []      # (ti, ti_next, fy_label, rows[], bm[], ey[], mom[], fwd[], coh_mask[])
    for k in range(len(rebs) - 1):
        ti, tn = rebs[k], rebs[k + 1]
        rrows, bms, eys, moms, fwds, cohm = [], [], [], [], [], []
        for code, ri in row_of.items():
            mt = _metrics_at(ri, ti, adj, rawc, funds, code, row_of)
            if mt is None:
                continue
            bm, ey, mom = mt
            a_t, a_n = adj[ri, ti], adj[ri, tn]
            if not (a_t > 0) or not (a_n > 0):
                continue
            rrows.append(ri); bms.append(bm); eys.append(ey if ey is not None else np.nan)
            moms.append(mom if mom is not None else np.nan); fwds.append(a_n / a_t - 1.0)
            cohm.append(ri in cohort_rows)
        if len(rrows) < 50:
            continue
        d = cal[ti]
        fy_label = d.year + 1 if d.month >= 4 else d.year
        months.append((ti, tn, fy_label, np.array(rrows), np.array(bms), np.array(eys),
                       np.array(moms), np.array(fwds), np.array(cohm)))
    logger.info("usable rebalances: {} ({}–{})", len(months), cal[months[0][0]], cal[months[-1][0]])

    # TOPIX monthly return aligned to the rebalances
    tx_m = np.array([topix_adj[tn] / topix_adj[ti] - 1.0 for ti, tn, *_ in months])

    # composite scores: value = avg xrank(bm)+xrank(ey); mom = xrank(mom); v+m = avg(value, mom)
    def _scores(bm, ey, mom):
        rb, re, rm = _xrank(bm), _xrank(ey), _xrank(mom)
        val = np.nanmean(np.vstack([rb, re]), axis=0)
        vm = np.nanmean(np.vstack([val, rm]), axis=0)
        return val, rm, vm

    # ── Q1: universe decile long-short (cheapest − priciest), per signal ──────────────
    def _universe_ls(which: str) -> np.ndarray:
        out = []
        for (ti, tn, fy, rrows, bm, ey, mom, fwd, coh) in months:
            val, rmom, vm = _scores(bm, ey, mom)
            score = {"value": val, "mom": rmom, "v+m": vm}[which]
            m = np.isfinite(score) & np.isfinite(fwd)
            sc, fw = score[m], fwd[m]
            n = len(sc)
            if n < 50:
                out.append(np.nan); continue
            k = max(5, n // 10)
            order = np.argsort(sc)
            short = fw[order[:k]].mean()          # lowest score = priciest / loser
            long = fw[order[-k:]].mean()           # highest score = cheapest / winner
            out.append(long - short)
        return np.array(out)

    print("\n" + "=" * 96)
    print("VALUE-TILT DISCOVERY — Japan value / value+momentum, monthly rebalanced")
    print("=" * 96)
    print(f"{len(months)} monthly rebalances {cal[months[0][0]]}–{cal[months[-1][0]]}  |  "
          f"univ ~{int(np.median([len(m[3]) for m in months]))} names/mo  |  "
          f"cohort ~{int(np.median([m[8].sum() for m in months]))} names/mo")

    print("\nQ1 — UNIVERSE DECILE LONG-SHORT (cheapest−priciest decile, gross, monthly):")
    print(f"  {'signal':<8}{'ann.Sharpe':>12}{'CAGR':>10}{'mean/mo':>10}{'t-stat':>9}")
    ls_series = {}
    for which in ("value", "mom", "v+m"):
        s = _universe_ls(which)
        s = s[np.isfinite(s)]
        ls_series[which] = s
        print(f"  {which:<8}{_ann_sharpe(s):>12.2f}{_cagr(s)*100:>9.1f}%"
              f"{s.mean()*100:>9.2f}%{_tstat(s):>9.2f}")

    # Q3 value/momentum return correlation (the −0.55 diversification claim)
    v, mo = ls_series["value"], ls_series["mom"]
    n = min(len(v), len(mo))
    vmcorr = float(np.corrcoef(v[-n:], mo[-n:])[0, 1]) if n > 12 else float("nan")
    print(f"\nQ3 — value vs momentum L/S monthly-return correlation = {vmcorr:+.2f}  "
          f"(Asness ≈ −0.55 → the diversification that earns the ~0.65 combined Sharpe)")

    # ── Q2: deployable CONCENTRATED LONG TILT on the cohort, net of cost, β-stripped ──
    def _cohort_tilt(which: str, K: int):
        """Returns (monthly_net, ew_monthly, turnover_mean). Holds cheapest/best K cohort names."""
        rets, ew, prev = [], [], set()
        turn = []
        for (ti, tn, fy, rrows, bm, ey, mom, fwd, coh) in months:
            cm = coh
            if cm.sum() < K + 2:
                rets.append(np.nan); ew.append(np.nan); continue
            val, rmom, vm = _scores(bm[cm], ey[cm], mom[cm])
            score = {"value": val, "mom": rmom, "v+m": vm}[which]
            cf, cr = fwd[cm], rrows[cm]
            m = np.isfinite(score) & np.isfinite(cf)
            sc, fw, ids = score[m], cf[m], cr[m]
            if len(sc) < K + 2:
                rets.append(np.nan); ew.append(np.nan); continue
            order = np.argsort(sc)[::-1]              # best (cheapest/winner) first
            top = order[:K]
            held = set(ids[top].tolist())
            gross = float(fw[top].mean())
            t = 0.0 if not prev else (len(held - prev) / K)
            cost = 2.0 * t * (_COST_BPS / 10_000.0)   # buy new + sell old
            rets.append(gross - cost)
            ew.append(float(fw.mean()))
            turn.append(t)
            prev = held
        return np.array(rets), np.array(ew), float(np.mean(turn)) if turn else 0.0

    print(f"\nQ2 — DEPLOYABLE COHORT LONG TILT (cheapest/best K of ~{int(np.median([m[8].sum() for m in months]))}, "
          f"net@{_COST_BPS:.0f}bps, vs equal-weight cohort):")
    print(f"  {'signal':<8}{'K':>4}{'tilt Shrp':>11}{'tilt CAGR':>11}{'EW CAGR':>10}"
          f"{'excess/yr':>11}{'α vs TOPIX':>12}{'β':>6}{'turn':>7}")
    ew_ref = None
    for which in ("value", "mom", "v+m"):
        for K in _KS:
            r, ew, turn = _cohort_tilt(which, K)
            m = np.isfinite(r) & np.isfinite(ew)
            r, ew2, txm = r[m], ew[m], tx_m[m]
            if ew_ref is None:
                ew_ref = (ew2, txm)
            alpha, beta = _alpha_beta(r, txm)
            print(f"  {which:<8}{K:>4}{_ann_sharpe(r):>11.2f}{_cagr(r)*100:>10.1f}%"
                  f"{_cagr(ew2)*100:>9.1f}%{(_cagr(r)-_cagr(ew2))*100:>10.1f}%"
                  f"{alpha*100:>11.1f}%{beta:>6.2f}{turn*100:>6.0f}%")

    # per-FY for the headline lead (value, K=12) — sign stability + OOS FY2025
    print(f"\nPER-FY — value tilt K=12 net@{_COST_BPS:.0f}bps vs equal-weight cohort:")
    r12, ew12, _ = _cohort_tilt("value", 12)
    by_fy = defaultdict(lambda: [[], []])
    for (mi, (ti, tn, fy, *_)) in enumerate(months):
        if np.isfinite(r12[mi]) and np.isfinite(ew12[mi]):
            by_fy[fy][0].append(r12[mi]); by_fy[fy][1].append(ew12[mi])
    npos = 0
    for fy in sorted(by_fy):
        rr, ee = np.array(by_fy[fy][0]), np.array(by_fy[fy][1])
        tot = float(np.prod(1 + rr) - 1); ewt = float(np.prod(1 + ee) - 1)
        npos += (tot - ewt) > 0
        tag = "  ← OOS FY2025" if fy == 2025 else ""
        print(f"  FY{fy}: tilt {tot*100:+.1f}%  EW {ewt*100:+.1f}%  excess {(tot-ewt)*100:+.1f}pp{tag}")
    print(f"  tilt-beats-EW FYs: {npos}/{len(by_fy)}")

    print("\nHOW TO READ:")
    print("• Q1 universe L/S Sharpe>0 + t>2 on VALUE = the Japan value premium exists in our data\n"
          "  (Asness prior: value yes, momentum ~0). If momentum L/S ≈0/neg here → replicates the\n"
          "  Japan momentum failure; the v+m combo earns its keep only if the value/mom return corr\n"
          "  (Q3) is clearly negative.\n"
          "• Q2 is the binding deployable question: does the cheapest-K cohort tilt BEAT the\n"
          "  equal-weight cohort (the honest benchmark — beating TOPIX is easy, beating EW is the\n"
          "  bar, per the confluence buy-and-hold lesson) net of cost, with positive α and stable\n"
          "  per-FY? If yes at K=6/12 → write a value pre-reg. If the excess is ≤0 or sign-unstable\n"
          "  → the documented premium doesn't survive at ¥2M concentration. DISCOVERY ONLY.")
    print("  CAVEATS: (1) survivorship — cohort = CURRENT cluster members + universe = codes with\n"
          "  data now (delisted value-traps under-represented → value premium likely OVERstated).\n"
          "  (2) adj_close excludes dividends (value names are high-yield → their total return is\n"
          "  UNDERstated here; bias against the tilt). (3) split-robust B/M carry, but fundamentals\n"
          "  ≤18mo stale. (4) affordability at ¥2M not modeled (cheapest-value names skew low-price\n"
          "  → likely AFFORDABLE, unlike MN-PEAD, but verify at pre-reg).")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
