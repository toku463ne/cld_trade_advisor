"""Value tilt — turnover-band tier definition probe (read-only, advisory).

The frozen tier uses a PRICE CEILING (≤¥3,333) ∩ turnover≥¥100M/d. The price ceiling was an
affordability proxy, but it is a POOR coverage proxy: a stock split drops a heavily
institution-covered large-cap under ¥3,333 while it stays efficiently priced (no value premium),
and the premium thesis is that value lives in UNDER-COVERED names — better captured by ¥ turnover
than price. This probe replaces the price ceiling with a TURNOVER BAND and keeps affordability as
a SEPARATE hard constraint. Three questions:

  Q1 — how many names qualify under each turnover-band universe (breadth)?
  Q2 — does the value premium improve or worsen vs the price-ceiling tier?
  Q3 — what turnover UPPER limit best separates 'institutional-dominated' (efficiently priced, no
       premium) from 'under-covered' (premium lives here)?

Method mirrors value_tilt_discovery_probe (monthly rebalance, point-in-time split-robust B/M+E/P,
TOTAL RETURN incl. dividends). Turnover = trailing-60-bar MEDIAN ¥ turnover_value at the rebalance.
Read-only. Run:
  PYTHONPATH=. uv run --env-file devenv python -m src.analysis.value_turnover_tier_probe
"""
from __future__ import annotations

import sys

import numpy as np
from loguru import logger

import src.analysis.value_tilt_discovery_probe as vt

_TURN_LB = 60
_BUDGET = 2_000_000
_KS = (6, 12, 20)
_INF = float("inf")
_M = 1_000_000.0


def _scores(bm, ey, mom):
    rb, re_, rm = vt._xrank(bm), vt._xrank(ey), vt._xrank(mom)
    val = np.nanmean(np.vstack([rb, re_]), axis=0)
    vm = np.nanmean(np.vstack([val, rm]), axis=0)
    return val, rm, vm


def _fwd_total(m):
    return m["fwd"] + m["dy"] * m["frac"]


def _band_ls(months, lo, hi):
    """Value long-short (top-third cheap − bottom-third expensive) total return, monthly, among
    names whose trailing-median turnover ∈ [lo, hi). Returns (monthly_series, mean_n)."""
    out, ns = [], []
    for m in months:
        cm = (m["mturn"] >= lo) & (m["mturn"] < hi)
        if cm.sum() < 30:
            out.append(np.nan)
            continue
        fwd = _fwd_total(m)[cm]
        val, _, _ = _scores(m["bm"][cm], m["ey"][cm], m["mom"][cm])
        msk = np.isfinite(val) & np.isfinite(fwd)
        sc, fw = val[msk], fwd[msk]
        if len(sc) < 30:
            out.append(np.nan)
            continue
        k = max(5, len(sc) // 3)
        order = np.argsort(sc)
        out.append(float(fw[order[-k:]].mean() - fw[order[:k]].mean()))
        ns.append(len(sc))
    return np.array(out), (float(np.mean(ns)) if ns else 0.0)


def _tilt(months, which, K, maskfn, afford_ceil=None):
    """Cheapest/best-K long tilt (total return) vs equal-weight of the masked universe.
    maskfn(m) → bool array over m's names. afford_ceil (¥/share) optionally drops names whose
    1 lot would exceed the per-slot budget. Returns (monthly_net, ew_monthly, mean_turnover, mean_n)."""
    rets, ew, prev, turn_acc, ns = [], [], set(), [], []
    for m in months:
        cm = maskfn(m)
        if afford_ceil is not None:
            cm = cm & (m["price"] <= afford_ceil)
        fwd = _fwd_total(m)
        if cm.sum() < K + 2:
            rets.append(np.nan); ew.append(np.nan); continue
        val, rmom, vm = _scores(m["bm"][cm], m["ey"][cm], m["mom"][cm])
        score = {"value": val, "mom": rmom, "v+m": vm}[which]
        cf, cr = fwd[cm], m["rows"][cm]
        msk = np.isfinite(score) & np.isfinite(cf)
        sc, fw, ids = score[msk], cf[msk], cr[msk]
        if len(sc) < K + 2:
            rets.append(np.nan); ew.append(np.nan); continue
        order = np.argsort(sc)[::-1]
        top = order[:K]
        held = set(ids[top].tolist())
        t = 0.0 if not prev else (len(held - prev) / K)
        rets.append(float(fw[top].mean()) - 2.0 * t * (vt._COST_BPS / 10_000.0))
        ew.append(float(fw.mean()))
        turn_acc.append(t); ns.append(int(msk.sum())); prev = held
    return (np.array(rets), np.array(ew),
            float(np.mean(turn_acc)) if turn_acc else 0.0,
            float(np.mean(ns)) if ns else 0.0)


def run() -> None:
    cal, col_of, codes, row_of, adj, rawc, topix_adj, funds, cohort_rows, turn = \
        vt._load(with_turnover=True)
    vt._CAL_REF = cal
    rebs = vt._rebalance_indices(cal)

    months = []
    for k in range(len(rebs) - 1):
        ti, tn = rebs[k], rebs[k + 1]
        rows, bm, ey, mom, dy, fwd, mturn, price = ([] for _ in range(8))
        for code, ri in row_of.items():
            mt = vt._metrics_at(ri, ti, adj, rawc, funds, code, row_of)
            if mt is None:
                continue
            a_t, a_n = adj[ri, ti], adj[ri, tn]
            if not (a_t > 0) or not (a_n > 0):
                continue
            w = turn[ri, max(0, ti - _TURN_LB):ti]
            w = w[np.isfinite(w)]
            if len(w) < 20:
                continue
            pr = rawc[ri, ti]
            if not (pr > 0):
                continue
            b, e, mo, d = mt
            rows.append(ri); bm.append(b); ey.append(e if e is not None else np.nan)
            mom.append(mo if mo is not None else np.nan); dy.append(d)
            fwd.append(a_n / a_t - 1.0); mturn.append(float(np.median(w))); price.append(float(pr))
        if len(rows) < 50:
            continue
        months.append({"ti": ti, "tn": tn, "rows": np.array(rows), "bm": np.array(bm),
                       "ey": np.array(ey), "mom": np.array(mom), "dy": np.array(dy),
                       "fwd": np.array(fwd), "mturn": np.array(mturn), "price": np.array(price),
                       "frac": (cal[tn] - cal[ti]).days / 365.0})
    tx = np.array([topix_adj[m["tn"]] / topix_adj[m["ti"]] - 1.0 for m in months])
    logger.info("usable rebalances: {} ({}–{})", len(months), cal[months[0]["ti"]],
                cal[months[-1]["ti"]])

    print("\n" + "=" * 96)
    print("VALUE TILT — TURNOVER-BAND TIER PROBE (total return; turnover = trailing-60b median ¥)")
    print("=" * 96)
    print(f"{len(months)} monthly rebalances {cal[months[0]['ti']]}–{cal[months[-1]['ti']]}  |  "
          f"univ ~{int(np.median([len(m['rows']) for m in months]))} priced names/mo")

    # ── Q3: where does the value premium live along the turnover axis? ───────────────
    print("\nQ3 — VALUE LONG-SHORT (top−bottom third by value) BY TURNOVER BAND (total return):")
    print(f"  {'turnover band':<22}{'ann.Sharpe':>11}{'CAGR':>9}{'t-stat':>8}{'~names/mo':>11}")
    bands1 = [(0, 100 * _M, "<¥100M (untradeable)"), (100 * _M, 300 * _M, "¥100–300M"),
              (300 * _M, 500 * _M, "¥300–500M"), (500 * _M, 1000 * _M, "¥500M–1B"),
              (1000 * _M, 3000 * _M, "¥1–3B"), (3000 * _M, _INF, "≥¥3B (institutional)")]
    for lo, hi, lab in bands1:
        s, n = _band_ls(months, lo, hi)
        s = s[np.isfinite(s)]
        if len(s) < 12:
            print(f"  {lab:<22}{'(too thin)':>11}")
            continue
        print(f"  {lab:<22}{vt._ann_sharpe(s):>11.2f}{vt._cagr(s) * 100:>8.1f}%"
              f"{vt._tstat(s):>8.2f}{n:>11.0f}")

    # ── Q1 + Q2: deployable long tilt under each turnover-band universe (vs price-ceiling tier) ──
    print("\nQ1+Q2 — DEPLOYABLE VALUE TILT (K=12) by UNIVERSE DEFINITION "
          "(net@30bps, total return, vs equal-weight of that universe):")
    print(f"  {'universe (lower=¥100M)':<26}{'~names/mo':>10}{'tilt Shrp':>10}{'excess/yr':>11}"
          f"{'α TOPIX':>9}{'β':>6}")
    unis = [(100 * _M, _INF, "≥¥100M (no upper cap)"), (100 * _M, 300 * _M, "¥100–300M"),
            (100 * _M, 500 * _M, "¥100–500M"), (100 * _M, 1000 * _M, "¥100M–1B"),
            (100 * _M, 2000 * _M, "¥100M–2B")]

    def _band_mask(lo, hi):
        return lambda m: (m["mturn"] >= lo) & (m["mturn"] < hi)

    def _row(label, maskfn, afford=None):
        r, ew, _, n = _tilt(months, "value", 12, maskfn, afford)
        msk = np.isfinite(r) & np.isfinite(ew)
        r2, ew2, txm = r[msk], ew[msk], tx[msk]
        a, b = vt._alpha_beta(r2, txm)
        print(f"  {label:<26}{n:>10.0f}{vt._ann_sharpe(r2):>10.2f}"
              f"{(vt._cagr(r2) - vt._cagr(ew2)) * 100:>10.1f}%{a * 100:>8.1f}%{b:>6.2f}")

    for lo, hi, lab in unis:
        _row(lab, _band_mask(lo, hi))
    # the current price-ceiling tier, recomputed POINT-IN-TIME for an apples-to-apples comparison
    _row("price≤¥3,333 ∩ ≥¥100M (PIT)", lambda m: (m["price"] <= 3333) & (m["mturn"] >= 100 * _M))
    # A/B: the FROZEN tier's "ever-qualifies" membership (the universe the earlier +4.2% used).
    # If this reproduces a much larger excess than the PIT same-criteria row above, the headline
    # was a look-ahead artifact of fixed ever-qualifies membership, not a real deployable edge.
    is_tier = np.zeros(len(codes), dtype=bool)
    for c in vt._load_tier_local():
        if c in row_of:
            is_tier[row_of[c]] = True
    _row("FROZEN tier (ever-qualifies)", lambda m: is_tier[m["rows"]])

    # ── Q3 (deployable view): same tilt but sweeping the UPPER cap, to see where α peaks ──
    print("\nUPPER-CAP SWEEP — value tilt K=12 excess & α vs equal-weight, lower=¥100M:")
    print(f"  {'upper cap':<14}{'~names/mo':>10}{'excess/yr':>11}{'α TOPIX':>9}{'tilt Shrp':>11}")
    for cap_m in (200, 300, 500, 700, 1000, 1500, 2000, 100000):
        hi = cap_m * _M
        r, ew, _, n = _tilt(months, "value", 12, _band_mask(100 * _M, hi))
        msk = np.isfinite(r) & np.isfinite(ew)
        r2, ew2, txm = r[msk], ew[msk], tx[msk]
        a, _b = vt._alpha_beta(r2, txm)
        cap_lab = "none" if cap_m >= 100000 else f"¥{cap_m}M"
        print(f"  {cap_lab:<14}{n:>10.0f}{(vt._cagr(r2) - vt._cagr(ew2)) * 100:>10.1f}%"
              f"{a * 100:>8.1f}%{vt._ann_sharpe(r2):>11.2f}")

    # ── Part 3: affordability as a SEPARATE hard constraint (¥100–500M band) ──────────
    print(f"\nAFFORDABILITY (separate hard constraint) — ¥100–500M band, value tilt K=12:")
    band = _band_mask(100 * _M, 500 * _M)
    for K, ceil in [(6, _BUDGET / 6 / 100), (12, _BUDGET / 12 / 100)]:
        r0, ew0, _, n0 = _tilt(months, "value", K, band)
        r1, ew1, _, n1 = _tilt(months, "value", K, band, afford_ceil=ceil)
        m0 = np.isfinite(r0) & np.isfinite(ew0)
        m1 = np.isfinite(r1) & np.isfinite(ew1)
        # affordable fraction of the band
        fr = float(np.mean([(m["price"][band(m)] <= ceil).mean()
                            for m in months if band(m).sum() > 0]))
        e0 = (vt._cagr(r0[m0]) - vt._cagr(ew0[m0])) * 100
        e1 = (vt._cagr(r1[m1]) - vt._cagr(ew1[m1])) * 100
        print(f"  K={K:<2} (slot ¥{_BUDGET // K // 1000}k → 1 lot needs price ≤¥{ceil:,.0f}): "
              f"{fr * 100:.0f}% of band affordable; excess no-afford {e0:+.1f}%/yr → "
              f"with-afford {e1:+.1f}%/yr")

    print("\nHOW TO READ:")
    print("• Q3 (top table + upper-cap sweep): if the value L/S Sharpe/α is HIGH at low turnover and\n"
          "  DECAYS toward ≥¥1–3B, the under-coverage thesis holds and the cap belongs where α stops\n"
          "  rising. A flat/rising profile would mean turnover is NOT the right axis.\n"
          "• Q1/Q2: breadth (~names/mo) and tilt excess/α for each turnover-band universe vs the\n"
          "  current price-ceiling tier — does dropping the price ceiling for a turnover band improve\n"
          "  the premium? • Part 3: affordability is applied AS A SEPARATE filter on the held set — it\n"
          "  is split-proof (a split changes price but not which names you can afford a lot of) and\n"
          "  decoupled from the coverage definition. DISCOVERY ONLY; survivorship caveat still applies.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
