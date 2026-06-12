"""MN-Value breadth/cost/borrow feasibility probe (read-only, advisory — NOT a pre-reg null).

Front-runner of the short-selling sleeve map (docs/analysis/20260530_short_sleeve_map.md,
[[project_short_sleeve_map]]). The value LONG-SHORT premium is already CONFIRMED in our data
(value_tilt_discovery_probe Gate A: decile L/S Sharpe ~0.84, t~2.46 with dividends) but the
deployable LONG-ONLY slice captures ~none of it (value_turnover_tier_probe). The missing half is
the SHORT leg. This probe asks the same binding question MN-PEAD failed:

    when you can only hold ~K names per side at ¥2M, does the realized long-cheap / short-expensive
    book still capture the value spread NET of 30bps + 1.1%/yr borrow — and is the short leg
    BORROWABLE (制度信用)?

Value should beat PEAD-MN on all three MN-PEAD walls: (1) ~2× the gross L/S Sharpe; (2) far lower
turnover (monthly value ranks are sticky vs PEAD event churn → milder cost); (3) the short leg =
expensive large-cap growth → borrowable, unlike PEAD-short which leaked to unborrowable small-caps.
This probe measures all three directly.

Structure (mirrors mn_pead_feasibility_probe, adapted to a monthly cross-sectional book):
  • DECILE L/S ceiling — reproduces the validated value spread (machinery check).
  • BREADTH SWEEP K∈{3,6,10,20}: realized net Sharpe & annual return + residual β.
  • COST sweep (0/30/60 bps + borrow on/off) at K=6.
  • BORROWABILITY diagnostic — fraction of the short leg in the borrowable (225 large-cap) set.
  • Two universes: 225 COHORT (both legs borrowable, thin breadth) and WIDE TIER (~2,785, more
    breadth but short leg partly unborrowable) — same dual-universe read as MN-PEAD.
  • PER-FY net@30bps stability (+ OOS FY2025) and a circular block-bootstrap time-CI on the K=6 book.

Dollar-neutral, equal-weight, total-return (annual DPS accrued by holding period). Read-only.
Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.mn_value_feasibility_probe
"""
from __future__ import annotations

import datetime
import math
import sys
from collections import defaultdict

import numpy as np
from loguru import logger

import src.analysis.value_tilt_discovery_probe as vd
from src.analysis.value_tilt_discovery_probe import (
    _ann_sharpe, _cagr, _load, _load_tier_local, _metrics_at, _rebalance_indices,
    _tstat, _xrank,
)

_COST_BPS = 30.0
_BORROW_YR = 0.011
_KS = (3, 6, 10, 20)
_N_BLOCK = 2000
_BLOCK = 12      # months (~1yr block for the monthly series)


def _value_score(bm, ey):
    """Composite value = mean cross-sectional rank of B/M and E/P (higher = cheaper)."""
    return np.nanmean(np.vstack([_xrank(bm), _xrank(ey)]), axis=0)


def _fy_label(d: datetime.date) -> int:
    return d.year + 1 if d.month >= 4 else d.year


def _build_months(cal, row_of, adj, rawc, funds, cohort_rows, tier_rows):
    """Per monthly rebalance: rows, value score, forward total return, borrowable mask, universe masks."""
    rebs = _rebalance_indices(cal)
    months = []
    for k in range(len(rebs) - 1):
        ti, tn = rebs[k], rebs[k + 1]
        frac = (cal[tn] - cal[ti]).days / 365.0
        rrows, bms, eys, fwds, cohm, tierm, borrow = [], [], [], [], [], [], []
        for code, ri in row_of.items():
            mt = _metrics_at(ri, ti, adj, rawc, funds, code, row_of)
            if mt is None:
                continue
            bm, ey, _mom, dy = mt
            a_t, a_n = adj[ri, ti], adj[ri, tn]
            if not (a_t > 0) or not (a_n > 0):
                continue
            rrows.append(ri); bms.append(bm); eys.append(ey if ey is not None else np.nan)
            fwds.append(a_n / a_t - 1.0 + dy * frac)               # total return
            cohm.append(ri in cohort_rows); tierm.append(ri in tier_rows)
            borrow.append(ri in cohort_rows)                       # borrowable proxy = 225 large-cap
        if len(rrows) < 50:
            continue
        score = _value_score(np.array(bms), np.array(eys))
        months.append((ti, tn, _fy_label(cal[ti]), np.array(rrows), score,
                       np.array(fwds), np.array(cohm), np.array(tierm),
                       np.array(borrow), frac))
    return months


def _mn_book(months, K, midx, decile=False, short_borrowable_only=False):
    """Monthly dollar-neutral L/S net series + diagnostics.

    Long = cheapest K (highest value score), Short = priciest K (lowest), within universe `midx`
    (6=225 cohort, 7=mid-cap tier). Returns dict with monthly gross/net/ew arrays, residual-β inputs,
    short-leg borrowable fraction, mean turnover.
    """
    gross, net, fy_of, txm_rows = [], [], [], []
    prevL, prevS = set(), set()
    turns, short_borrow_frac = [], []
    for m in months:
        ti, tn, fy, rrows, score, fwd, cohm, tierm, borrow, frac = m
        uni = m[midx]
        sel = uni & np.isfinite(score) & np.isfinite(fwd)
        if short_borrowable_only:
            sel_short_pool = sel & borrow
        else:
            sel_short_pool = sel
        sc, fw, ids, bo = score[sel], fwd[sel], rrows[sel], borrow[sel]
        if sel.sum() < 2 * K + 4:
            continue
        kk = max(5, int(sel.sum() // 10)) if decile else K
        order = np.argsort(sc)                       # ascending: priciest first, cheapest last
        # short pool may be restricted (borrowable only)
        if short_borrowable_only:
            short_order = [i for i in order if bo[i]]
        else:
            short_order = list(order)
        if len(short_order) < kk:
            continue
        long_idx = order[-kk:]
        short_idx = np.array(short_order[:kk])
        long_r = float(fw[long_idx].mean())
        short_r = float(fw[short_idx].mean())
        g = long_r - short_r
        heldL = set(ids[long_idx].tolist()); heldS = set(ids[short_idx].tolist())
        tL = 0.0 if not prevL else len(heldL - prevL) / kk
        tS = 0.0 if not prevS else len(heldS - prevS) / kk
        cost = 2.0 * (tL + tS) * (_COST_BPS / 10_000.0)
        borrow_cost = _BORROW_YR * frac
        gross.append(g)
        net.append(g - cost - borrow_cost)
        fy_of.append(fy)
        txm_rows.append((m[0], m[1]))
        turns.append((tL + tS) / 2.0)
        short_borrow_frac.append(np.mean([bo[i] for i in short_idx]))
        prevL, prevS = heldL, heldS
    return {
        "gross": np.array(gross), "net": np.array(net), "fy": np.array(fy_of),
        "turn": float(np.mean(turns)) if turns else float("nan"),
        "short_borrow_frac": float(np.mean(short_borrow_frac)) if short_borrow_frac else float("nan"),
    }


def _resid_beta(book, txm):
    m = np.isfinite(book) & np.isfinite(txm)
    if m.sum() < 12 or txm[m].var() == 0:
        return float("nan")
    return float(np.cov(book[m], txm[m])[0, 1] / txm[m].var())


def _block_boot(series):
    n = len(series)
    if n < _BLOCK + 2:
        return float("nan"), float("nan"), float("nan"), float("nan")
    out = np.empty(_N_BLOCK)
    for k in range(_N_BLOCK):
        rng = np.random.default_rng(5_000 + k)
        idx = np.empty(n, dtype=np.int64); f = 0
        while f < n:
            st = rng.integers(0, n); take = min(_BLOCK, n - f)
            idx[f:f + take] = (np.arange(st, st + take)) % n; f += take
        out[k] = _ann_sharpe(series[idx])
    lo, mid, hi = np.percentile(out, [2.5, 50, 97.5])
    return float(lo), float(mid), float(hi), float((out > 0).mean())


def _report(months, cal, topix_adj, midx, title, short_borrowable_only=False):
    txm_all = np.array([topix_adj[m[1]] / topix_adj[m[0]] - 1.0 for m in months])
    med = int(np.median([m[midx].sum() for m in months]))
    print("\n" + "=" * 96)
    print(f"MN-VALUE FEASIBILITY — {title}")
    print(f"  long cheapest / short priciest, dollar-neutral, monthly  |  ~{med} rankable names/mo"
          + ("  |  SHORT restricted to borrowable" if short_borrowable_only else ""))
    print("=" * 96)

    dec = _mn_book(months, 0, midx, decile=True, short_borrowable_only=short_borrowable_only)
    if len(dec["net"]) < 12:
        print("  too few rebalances — skip."); return
    txm = txm_all[:len(dec["gross"])]
    print(f"DECILE L/S ceiling (total return): gross Sharpe {_ann_sharpe(dec['gross']):+.2f}  "
          f"CAGR {_cagr(dec['gross'])*100:+.1f}%  t {_tstat(dec['gross']):+.2f}   "
          f"[validated ≈ +0.84]")

    print(f"\nBREADTH SWEEP (net @ {_COST_BPS:.0f}bps + {_BORROW_YR*100:.1f}%/yr borrow):")
    print(f"  {'K/side':>7} | {'gross Shrp':>10} | {'net Shrp':>9} | {'net ann.ret':>12} | "
          f"{'resid β':>8} | {'turn':>6} | {'shortBorrow%':>12}")
    k6 = None
    for K in _KS:
        b = _mn_book(months, K, midx, short_borrowable_only=short_borrowable_only)
        if len(b["net"]) < 12:
            continue
        tx = txm_all[:len(b["gross"])]
        rb = _resid_beta(b["gross"], tx)
        print(f"  {K:>7} | {_ann_sharpe(b['gross']):>+10.2f} | {_ann_sharpe(b['net']):>+9.2f} | "
              f"{np.mean(b['net'])*12*100:>+11.1f}% | {rb:>+8.2f} | {b['turn']*100:>5.0f}% | "
              f"{b['short_borrow_frac']*100:>11.0f}%")
        if K == 6:
            k6 = b

    if k6 is None:
        return
    print(f"\nCOST SWEEP at K=6 (net Sharpe):")
    base_gross = k6["gross"]; turn = k6["turn"]
    frac_yr = 1.0 / 12.0
    for cb in (0.0, 30.0, 60.0):
        for borrow_on in (True, False):
            cost = 2.0 * (2 * turn) * (cb / 10_000.0)        # both legs, per month
            bc = (_BORROW_YR * frac_yr) if borrow_on else 0.0
            ser = base_gross - cost - bc
            tag = "borrow on " if borrow_on else "borrow off"
            print(f"  {cb:>4.0f}bps {tag}: net Sharpe {_ann_sharpe(ser):>+.2f}")

    print(f"\nPER-FY net@{_COST_BPS:.0f}bps return — K=6 (sign stability):")
    by_fy = defaultdict(list)
    for r, fy in zip(k6["net"], k6["fy"]):
        by_fy[fy].append(r)
    npos = 0
    for fy in sorted(by_fy):
        a = np.array(by_fy[fy]); tot = float(np.prod(1 + a) - 1); npos += tot > 0
        tag = "  ← OOS FY2025" if fy == 2025 else ""
        print(f"  FY{fy}: {tot*100:+.2f}%  (Sharpe {_ann_sharpe(a):+.2f}){tag}")
    print(f"  sign-positive FYs: {npos}/{len(by_fy)}")

    lo, mid, hi, p = _block_boot(k6["net"])
    print(f"\nTIME CI — K=6 net@{_COST_BPS:.0f}bps, circular block-bootstrap L={_BLOCK}mo "
          f"({_N_BLOCK} draws):")
    print(f"  Sharpe median {mid:+.2f} | 95% CI [{lo:+.2f}, {hi:+.2f}] | P(Sharpe>0) {p:.3f}")


def run() -> None:
    cal, col_of, codes, row_of, adj, rawc, topix_adj, funds, cohort_rows, _turn = _load()
    vd._CAL_REF = cal
    tier_rows = {row_of[c] for c in _load_tier_local() if c in row_of}
    logger.info("building monthly value snapshots…")
    months = _build_months(cal, row_of, adj, rawc, funds, cohort_rows, tier_rows)
    logger.info("usable rebalances: {} ({}–{})", len(months),
                cal[months[0][0]], cal[months[-1][0]])

    # midx: 6 = 225 cohort mask, 7 = mid-cap tier mask (indices into the month tuple)
    _report(months, cal, topix_adj, 6, "225 LARGE-CAP COHORT (both legs borrowable)")
    _report(months, cal, topix_adj, 7, "WIDE MID-CAP TIER (~2,785, short partly unborrowable)")
    _report(months, cal, topix_adj, 7,
            "WIDE TIER — SHORT-BORROWABLE-ONLY (realizable wide book)",
            short_borrowable_only=True)

    print("\n" + "=" * 96)
    print("HOW TO READ")
    print("=" * 96)
    print("• DECILE gross Sharpe ≈ +0.84 = machinery reproduces the validated value L/S premium.\n"
          "• BREADTH SWEEP: if K=6 net Sharpe clearly >0 AND the time-CI lower bound >0 AND short\n"
          "  Borrow% is high (short leg actually borrowable) → MN-Value clears where MN-PEAD failed\n"
          "  → warrants a full pre-reg null. If K=6 net straddles 0 or shortBorrow% is low on the\n"
          "  realizable (borrowable-only) wide book → the breadth/borrow wall is universal and the\n"
          "  short sleeve is CLOSED at ¥2M.\n"
          "• 225 cohort = both legs borrowable but thin (~205 names → K=6 each side is a big slice).\n"
          "  Wide tier = more breadth; the SHORT-BORROWABLE-ONLY arm is the honest deployable book.\n"
          "  CAVEATS: dollar-neutral (residual β reported, not stripped); survivorship hits the\n"
          "  SHORT leg hardest (delisted winners-shorts vanish → short P&L overstated); FY2025 was a\n"
          "  value drawdown (post-rotation entry risk); lot-affordability at ¥2M not modeled.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
