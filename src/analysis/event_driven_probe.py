"""Event-driven catalysts — dividend-signaling + buyback-proxy drift (read-only, advisory).

Map direction "event-driven catalysts" ([[project-program-direction-2026]]): the category that fits
small capital BECAUSE the per-name edge is large (few names suffice → breadth-immune), unlike PEAD /
value (small edge, breadth-bound). DATA CONSTRAINT: we have NO index-membership history and NO
corporate-action / TDnet table, and the /fins/dividend endpoint is premium-gated — so true index
RECONSTITUTION and announcement-timed BUYBACKS are not testable here. What IS testable from data in
hand:
  • DIVIDEND SIGNALING — initiation (0→+) / hike / cut / omission (+→0), from the annual DPS (`DivAnn`)
    just ingested. The dividend-signaling literature: initiations/hikes drift up, cuts/omissions down.
  • BUYBACK PROXY — YoY reduction in net shares outstanding (shares_outstanding_fy − treasury_shares_fy)
    as a coarse buyback flag.
Event anchor = the FY-results disclosure that announces the new annual dividend / share count
(`disclosed_date`, after-close→next session). β-stripped 60-bar CAR vs TOPIX, signed groups, N225
cohort gate (the binding deployment test). THE KEY QUESTION is MAGNITUDE: is the per-event drift LARGE
(→ a few catalyst names suffice at ¥2M, the deployability differentiator) or small like PEAD's +2.5%
(→ another breadth-bound play)?

Caveats: YoY realized dividend change is partly anticipated (a cleaner forecast-revision version needs
the FDivAnn field, not yet ingested) → understates the announcement surprise; buyback proxy is annual,
not announcement-timed; survivorship (current jq universe). Read-only. Run:
  PYTHONPATH=. uv run --env-file devenv python -m src.analysis.event_driven_probe
"""
from __future__ import annotations

import datetime
import math
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

from src.analysis.pead_forecast_revision import beta, beta_stripped_car, tradable_entry_day, _welch_t
from src.data.db import get_session

_BETA_WIN = 60
_H = 60
_HIKE, _CUT = 1.10, 0.90       # ±10% annual DPS change
_BB, _ISS = 0.99, 1.01         # ±1% net-share-count change (buyback / issuance proxy)


def _load_cohort225() -> set[str]:
    from src.analysis.models import StockClusterMember, StockClusterRun
    with get_session() as s:
        ids = [i for (i, fy) in s.execute(
            select(StockClusterRun.id, StockClusterRun.fiscal_year)).all()
            if fy.startswith("classified") and "exp" not in fy]
        return set(s.execute(select(StockClusterMember.stock_code)
                             .where(StockClusterMember.run_id.in_(ids))).scalars().all())


class Evt:
    __slots__ = ("div", "bb", "car", "fy", "in225")

    def __init__(self, div, bb, car, fy, in225):
        self.div = div      # 'initiation'|'hike'|'flat'|'cut'|'omission'
        self.bb = bb        # 'buyback'|'issuance'|'none'
        self.car = car      # β-stripped 60-bar CAR
        self.fy = fy
        self.in225 = in225


def _build():
    from src.data.jquants_collector import to_yf_code
    from src.data.jquants_models import JqDailyQuote, JqStatement, JqTopix
    cohort = _load_cohort225()
    with get_session() as s:
        stmts = s.execute(
            select(JqStatement.local_code, JqStatement.disclosed_date, JqStatement.disclosed_time,
                   JqStatement.current_fiscal_year_end_date, JqStatement.dividend_per_share_annual,
                   JqStatement.shares_outstanding_fy, JqStatement.treasury_shares_fy)
            .where(JqStatement.type_of_current_period == "FY")).all()
        topix = s.execute(select(JqTopix.date, JqTopix.close)
                          .where(JqTopix.close.isnot(None)).order_by(JqTopix.date)).all()
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

    by_code = defaultdict(list)
    for lc, dd, dt, fend, dv, sh, tr in stmts:
        if fend is None or dd is None:
            continue
        by_code[lc].append((fend, dd, dt, dv, sh, tr))

    events: list[Evt] = []
    for lc, recs in by_code.items():
        ri = row_of.get(lc)
        if ri is None:
            continue
        srow = arr[ri]
        in225 = to_yf_code(lc) in cohort
        recs.sort(key=lambda r: r[0])
        for i in range(1, len(recs)):
            (f0, _, _, d0, s0, t0), (f1, dd1, dt1, d1, s1, t1) = recs[i - 1], recs[i]
            if f0 == f1:
                continue
            # dividend signal
            div = None
            if d0 is not None and d1 is not None:
                a, b = float(d0), float(d1)
                if a == 0 and b > 0:
                    div = "initiation"
                elif a > 0 and b == 0:
                    div = "omission"
                elif b > a * _HIKE:
                    div = "hike"
                elif b < a * _CUT:
                    div = "cut"
                else:
                    div = "flat"
            # buyback proxy
            bb = "none"
            if s0 and s1:
                n0, n1 = float(s0) - float(t0 or 0), float(s1) - float(t1 or 0)
                if n0 > 0 and n1 < n0 * _BB:
                    bb = "buyback"
                elif n0 > 0 and n1 > n0 * _ISS:
                    bb = "issuance"
            if div is None and bb == "none":
                continue
            entry = tradable_entry_day(dd1, dt1, cal)
            if entry is None:
                continue
            ei = col_of[entry]
            if ei < _BETA_WIN + 1 or ei + _H >= len(cal):
                continue
            sw, mw = srow[ei - _BETA_WIN - 1:ei], topix_arr[ei - _BETA_WIN - 1:ei]
            bcoef = beta(sw[1:] / sw[:-1] - 1.0, mw[1:] / mw[:-1] - 1.0)
            if bcoef is None:
                continue
            car = beta_stripped_car(srow, topix_arr, ei, _H, bcoef)
            if car is None or math.isnan(car):
                continue
            events.append(Evt(div, bb, float(car), f1.year, in225))
    logger.info("built {} catalyst events ({} on 225 cohort)",
                len(events), sum(e.in225 for e in events))
    return events


def _grp(events, key, val, in225=None):
    cs = [e.car for e in events if getattr(e, key) == val and (in225 is None or e.in225 == in225)]
    return (float(np.mean(cs)) if cs else float("nan"), len(cs), np.array(cs))


def run() -> None:
    events = _build()
    if not events:
        logger.warning("no events"); return

    print("\n" + "=" * 92)
    print("EVENT-DRIVEN — dividend signaling + buyback proxy (β-stripped 60-bar CAR vs TOPIX)")
    print("=" * 92)

    def _div_table(in225, label):
        print(f"\nDIVIDEND-SIGNAL groups — {label}:")
        print(f"  {'group':<12}{'n':>7}{'meanCAR60':>12}")
        for g in ("initiation", "hike", "flat", "cut", "omission"):
            m, n, _ = _grp(events, "div", g, in225)
            print(f"  {g:<12}{n:>7}{m * 100:>11.2f}%")
        up = np.concatenate([_grp(events, "div", g, in225)[2] for g in ("initiation", "hike")])
        dn = np.concatenate([_grp(events, "div", g, in225)[2] for g in ("cut", "omission")])
        if len(up) > 1 and len(dn) > 1:
            sp = up.mean() - dn.mean()
            print(f"  (initiation+hike) − (cut+omission) = {sp * 100:+.2f}%   "
                  f"Welch t = {_welch_t(up, dn):+.2f}   [n_up={len(up)}, n_dn={len(dn)}]")
        # the magnitude / few-names check: the BIG catalysts
        mi, ni, _ = _grp(events, "div", "initiation", in225)
        mo, no, _ = _grp(events, "div", "omission", in225)
        print(f"  big-catalyst magnitude: initiation {mi * 100:+.2f}% (n={ni}), "
              f"omission {mo * 100:+.2f}% (n={no})")

    _div_table(None, "WIDE universe (power)")
    _div_table(True, "N225 COHORT (binding — deployable)")

    print("\nBUYBACK PROXY (YoY net-share change) — mean β-stripped 60-bar CAR:")
    print(f"  {'group':<10}{'wide n':>8}{'wide CAR':>11}{'coh n':>8}{'coh CAR':>11}")
    for g in ("buyback", "none", "issuance"):
        mw, nw, _ = _grp(events, "bb", g, None)
        mc, nc, _ = _grp(events, "bb", g, True)
        print(f"  {g:<10}{nw:>8}{mw * 100:>10.2f}%{nc:>8}{mc * 100:>10.2f}%")

    print("\nPER-FY — dividend catalyst spread (initiation+hike − cut+omission), wide universe:")
    for yr in sorted(set(e.fy for e in events)):
        sub = [e for e in events if e.fy == yr]
        up = np.array([e.car for e in sub if e.div in ("initiation", "hike")])
        dn = np.array([e.car for e in sub if e.div in ("cut", "omission")])
        if len(up) < 5 or len(dn) < 5:
            continue
        print(f"  FY{yr}: spread {(up.mean() - dn.mean()) * 100:+.2f}%  "
              f"(up n={len(up)}, dn n={len(dn)})")

    print("\nHOW TO READ:")
    print("• Event-driven's whole premise is LARGE per-name edge (few names suffice → breadth-immune).\n"
          "  Check the big-catalyst MAGNITUDE on the COHORT: if initiation/omission drift is large\n"
          "  (e.g. |CAR| ≳ 5–8%) and cohort-significant, that is a deployable few-names edge UNLIKE\n"
          "  PEAD's +2.5% breadth-bound spread. If the spread is small (~PEAD-sized) and/or cohort n is\n"
          "  thin, it is another breadth-bound cross-sectional play that won't deploy at ¥2M (same wall).\n"
          "• Direction check: initiation/hike should be > cut/omission (signaling), buyback > issuance.\n"
          "  DISCOVERY ONLY. CAVEATS: realized YoY dividend change is partly ANTICIPATED (forecast) →\n"
          "  understates the true announcement surprise (a forecast-revision FDivAnn version would be\n"
          "  cleaner); buyback proxy is annual not announcement-timed; no index-reconstitution data;\n"
          "  survivorship. A large cohort edge escalates to a deployability/cost probe, then pre-reg.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
