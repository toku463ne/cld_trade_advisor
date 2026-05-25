"""PEAD — management-forecast-revision surprise (read-only).

Implements the pre-registered definition in
`docs/analysis/pead_forecast_revision_preregistration.md`. Surprise = change in full-year
management EPS guidance scaled by price; ride the ~60-bar drift, β-stripped vs TOPIX.

The surprise / pairing / event-timing / CAR logic lives in PURE functions (unit-tested in
tests/test_pead_forecast_revision.py — verifiable now, before any data exists). `run()` is a
thin driver that assembles the per-event table from the jq_* tables and prints the quintile
drift; it only produces output once the J-Quants Standard 10-yr backfill is loaded (the
Free-plan 12-week window is too short to form revision pairs + a 60-bar forward window).

Run (after backfill): PYTHONPATH=. uv run --env-file devenv python -m src.analysis.pead_forecast_revision
"""
from __future__ import annotations

import bisect
import datetime
import math
import sys
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
from loguru import logger

_TSE_CLOSE = datetime.time(15, 0)
_BETA_WIN = 60
_HORIZONS = (20, 60)


# ── pure logic (unit-tested) ─────────────────────────────────────────────────
@dataclass(frozen=True)
class Disclosure:
    """One earnings disclosure, reduced to the fields the revision surprise needs."""
    disclosed_date: datetime.date
    disclosed_time: datetime.time | None
    fy_end: datetime.date | None              # current_fiscal_year_end_date
    forecast_eps: Decimal | None              # full-year management EPS guidance
    doc_basis: str | None = None              # type_of_document (accounting basis)


def _sort_key(d: Disclosure) -> tuple:
    return (d.disclosed_date, d.disclosed_time or datetime.time.min)


def pair_same_fy_revisions(discs: list[Disclosure]) -> list[tuple[Disclosure, Disclosure]]:
    """Pair each disclosure with the most recent prior same-FY disclosure carrying a
    forecast. Returns (prev, curr) pairs eligible for a revision surprise.

    Excludes (per pre-registration): missing forecast_eps on either side, differing
    fiscal-year target, or differing accounting basis (type_of_document).
    """
    ordered = sorted(discs, key=_sort_key)
    pairs: list[tuple[Disclosure, Disclosure]] = []
    for i, curr in enumerate(ordered):
        if curr.forecast_eps is None or curr.fy_end is None:
            continue
        for j in range(i - 1, -1, -1):
            prev = ordered[j]
            # most recent prior disclosure that is a same-FY, same-basis forecast;
            # skip (don't abort on) rows that don't qualify so a one-off mismatched
            # intermediary can't block an otherwise-clean revision pair.
            if (prev.fy_end != curr.fy_end or prev.forecast_eps is None
                    or prev.doc_basis != curr.doc_basis):
                continue
            pairs.append((prev, curr))
            break
    return pairs


def revision_surprise(prev_eps: Decimal | None, curr_eps: Decimal | None,
                      price: float | None) -> float | None:
    """ΔFEPS / price — positive means guidance was raised. None if inputs unusable."""
    if prev_eps is None or curr_eps is None or not price:
        return None
    return float((curr_eps - prev_eps) / Decimal(str(price)))


def tradable_entry_day(disclosed_date: datetime.date, disclosed_time: datetime.time | None,
                       trading_days: list[datetime.date]) -> datetime.date | None:
    """First trading day on/after the effective announcement day. After-close (>=15:00)
    pushes the effective day to the next trading day. `trading_days` must be sorted."""
    effective = disclosed_date
    if disclosed_time is not None and disclosed_time >= _TSE_CLOSE:
        idx = bisect.bisect_right(trading_days, disclosed_date)
        if idx >= len(trading_days):
            return None
        effective = trading_days[idx]
    idx = bisect.bisect_left(trading_days, effective)
    return trading_days[idx] if idx < len(trading_days) else None


def beta(stock_rets: np.ndarray, mkt_rets: np.ndarray) -> float | None:
    m = ~(np.isnan(stock_rets) | np.isnan(mkt_rets))
    s, k = stock_rets[m], mkt_rets[m]
    if len(k) < 30 or k.var() == 0:
        return None
    return float(np.cov(s, k)[0, 1] / k.var())


def beta_stripped_car(stock_closes: np.ndarray, mkt_closes: np.ndarray,
                      entry_idx: int, horizon: int, b: float) -> float | None:
    """Cumulative abnormal return (stock − β·market) over `horizon` bars from entry_idx."""
    last = entry_idx + horizon
    if entry_idx < 1 or last >= len(stock_closes) or last >= len(mkt_closes):
        return None
    s = stock_closes[entry_idx:last + 1]
    k = mkt_closes[entry_idx:last + 1]
    if s[0] <= 0 or k[0] <= 0:
        return None
    s_ret = s[-1] / s[0] - 1.0
    k_ret = k[-1] / k[0] - 1.0
    return float(s_ret - b * k_ret)


def quintile_edges(values: list[float]) -> list[float]:
    return list(np.percentile(values, [20, 40, 60, 80])) if values else []


def quintile_of(x: float, edges: list[float]) -> int:
    return bisect.bisect_right(edges, x)          # 0..4


def doc_basis(type_of_document: str | None) -> str | None:
    """Accounting basis (Consolidated_JP / NonConsolidated_IFRS / …) from type_of_document.

    The pre-registered exclusion is on *accounting basis* (JP/IFRS, Consolidated/NC) — not
    the document type. `…FinancialStatements_<Basis>` exposes the basis as a suffix;
    `EarnForecast*` (a pure forecast revision) carries no basis token → None, to be resolved
    to the code's prevailing basis so a revision pairs against its standing quarterly chain.
    """
    if type_of_document and "FinancialStatements_" in type_of_document:
        return type_of_document.split("FinancialStatements_", 1)[1] or None
    return None


def _spearman_up(means: list[float]) -> float:
    """Spearman ρ between quintile index 0..k-1 and per-quintile mean CAR (monotone check)."""
    xs = list(range(len(means)))
    order = sorted(range(len(means)), key=lambda i: means[i])
    ranks = [0.0] * len(means)
    for r, i in enumerate(order):
        ranks[i] = float(r)
    n = len(means)
    mx, mr = (n - 1) / 2.0, (n - 1) / 2.0
    num = sum((xs[i] - mx) * (ranks[i] - mr) for i in range(n))
    den = math.sqrt(sum((xs[i] - mx) ** 2 for i in range(n))
                    * sum((ranks[i] - mr) ** 2 for i in range(n)))
    return num / den if den else 0.0


def _welch_t(a: np.ndarray, b: np.ndarray) -> float:
    """Welch t for mean(a) − mean(b); naive (no Newey-West) → upper bound on significance."""
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    se = math.sqrt(va + vb)
    return float((a.mean() - b.mean()) / se) if se > 0 else float("nan")


# ── thin DB driver (runs once the 10-yr backfill exists) ─────────────────────
def run() -> None:  # noqa: C901 — single linear assembly + reporting, kept in one place
    from collections import Counter, defaultdict
    from dataclasses import replace

    from sqlalchemy import select

    from src.data.db import get_session
    from src.data.jquants_collector import to_yf_code
    from src.data.jquants_models import JqDailyQuote, JqListed, JqStatement, JqTopix
    from src.data.models import Ohlcv1d

    with get_session() as s:
        stmts = s.execute(
            select(JqStatement.local_code, JqStatement.disclosed_date,
                   JqStatement.disclosed_time, JqStatement.current_fiscal_year_end_date,
                   JqStatement.forecast_earnings_per_share, JqStatement.type_of_document)
        ).all()
        topix = s.execute(select(JqTopix.date, JqTopix.close)
                          .where(JqTopix.close.isnot(None)).order_by(JqTopix.date)).all()
        scale_of: dict[str, str | None] = {
            c: sc for c, sc in s.execute(select(JqListed.code, JqListed.scale_category))}
        cohort = {c for (c,) in s.execute(select(Ohlcv1d.stock_code).distinct())}
        codes = [c for (c,) in s.execute(select(JqDailyQuote.code).distinct()
                                         .order_by(JqDailyQuote.code))]
        # Trading calendar = TOPIX dates (jq_trading_calendar.holiday_division is unpopulated);
        # TOPIX also being the β-strip market series guarantees stock/market are date-aligned.
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

    if not stmts or not topix:
        logger.warning("jq_* not populated (statements={}, topix={}). Load the backfill.",
                       len(stmts), len(topix))
        return
    logger.info("loaded {} statements, {} cal days, {} priced codes, cohort {} (ohlcv_1d)",
                len(stmts), len(cal), len(codes), len(cohort))

    # group statements per code; resolve each disclosure's accounting basis (forecast-revision
    # rows inherit the code's modal basis so they pair into the same-FY quarterly chain).
    raw: dict[str, list[tuple]] = defaultdict(list)
    for code, dd, dt, fy, feps, tod in stmts:
        raw[code].append((dd, dt, fy, feps, tod))
    by_code: dict[str, list[Disclosure]] = {}
    for code, rows in raw.items():
        fin = Counter(b for b in (doc_basis(r[4]) for r in rows) if b)
        modal = fin.most_common(1)[0][0] if fin else None
        by_code[code] = [Disclosure(dd, dt, fy, feps, doc_basis(tod) or modal)
                         for dd, dt, fy, feps, tod in rows]

    # ── per-event surprise + β-stripped CAR ──────────────────────────────────
    surp_l, c60_l, c20_l, raw60_l, fy_l, scale_l, coh_l, fyend_l = ([] for _ in range(8))
    n_pairs = 0
    for code, discs in by_code.items():
        ri = row_of.get(code)
        if ri is None:
            continue
        srow = arr[ri]
        in_coh = to_yf_code(code) in cohort
        sc = scale_of.get(code)
        for prev, curr in pair_same_fy_revisions(discs):
            n_pairs += 1
            if curr.fy_end is None:                    # pairing guarantees this, satisfy mypy
                continue
            entry = tradable_entry_day(curr.disclosed_date, curr.disclosed_time, cal)
            if entry is None:
                continue
            ei = col_of[entry]
            if ei < _BETA_WIN + 1 or ei + max(_HORIZONS) >= len(cal):
                continue
            price = srow[ei - 1]                       # adj close strictly before entry
            if not (price > 0):
                continue
            sp = revision_surprise(prev.forecast_eps, curr.forecast_eps, float(price))
            if sp is None:
                continue
            sw, mw = srow[ei - _BETA_WIN - 1:ei], topix_arr[ei - _BETA_WIN - 1:ei]
            b = beta(sw[1:] / sw[:-1] - 1.0, mw[1:] / mw[:-1] - 1.0)
            if b is None:
                continue
            e60 = beta_stripped_car(srow, topix_arr, ei, 60, b)
            e20 = beta_stripped_car(srow, topix_arr, ei, 20, b)
            eraw = beta_stripped_car(srow, topix_arr, ei, 60, 0.0)
            if e60 is None or e20 is None or eraw is None or math.isnan(e60) \
                    or math.isnan(e20) or math.isnan(eraw):
                continue
            surp_l.append(sp); c60_l.append(e60); c20_l.append(e20)    # noqa: E702
            raw60_l.append(eraw); fy_l.append(curr.fy_end.year)        # noqa: E702
            scale_l.append(sc); coh_l.append(in_coh); fyend_l.append(curr.fy_end)  # noqa: E702

    n = len(surp_l)
    logger.info("formed {} same-FY pairs; {} usable events (price+β+CAR)", n_pairs, n)
    if n < 100:
        logger.warning("too few usable events ({}) — cannot evaluate gates", n)
        return

    surp = np.array(surp_l); c60 = np.array(c60_l); c20 = np.array(c20_l)
    raw60 = np.array(raw60_l); fy = np.array(fy_l); coh = np.array(coh_l)
    scales = np.array(scale_l, dtype=object)
    lo, hi = np.percentile(surp, [0.5, 99.5])           # winsorize magnitude (sign preserved)
    surp = np.clip(surp, lo, hi)

    # Binning amended 2026-05-25 to SIGNED TERCILES — the surprise has a ~58% mass point at 0
    # (forecast reaffirmations), so value-percentile quintiles are degenerate. Group by sign of
    # ΔFEPS: 0=down (<0), 1=reaffirm (=0), 2=up (>0); long-short = (up − down).
    GROUPS = ["down(<0)", "reaffirm(=0)", "up(>0)"]

    def _grp(s: np.ndarray) -> np.ndarray:
        return np.where(s < 0, 0, np.where(s > 0, 2, 1))

    g = _grp(surp)

    def _gstats(mask: np.ndarray, car: np.ndarray):
        gg, cm = g[mask], car[mask]
        return [(float(cm[gg == i].mean()) if (gg == i).any() else float("nan"),
                 int((gg == i).sum()), cm[gg == i]) for i in range(3)]

    full = np.ones(n, dtype=bool)
    gm = _gstats(full, c60)
    means = [m for m, _, _ in gm]
    counts = [c for _, c, _ in gm]
    ls60 = means[2] - means[0]                          # up − down
    t_ls = _welch_t(gm[2][2], gm[0][2])

    # gate 4 — OOS hold out the most-recent COMPLETE fiscal year. A FY is "complete" only if
    # its full disclosure cycle is coverable: annual results land ~50d after fy_end and need a
    # further 60-bar (~85 cal-day) forward window, so require fy_end + ~135d ≤ data end. This
    # excludes the truncated trailing FY (its late-FY disclosures are dropped for lack of a
    # forward window), per the pre-reg's "most-recent FULL fiscal year" wording.
    yr_max = int(fy.max())
    data_end = cal[-1]
    fyend = np.array(fyend_l, dtype=object)
    yr_lastend = {yr: max(d for d, y in zip(fyend, fy) if y == yr)
                  for yr in set(int(x) for x in fy)}
    complete_yrs = [yr for yr, e in yr_lastend.items()
                    if e + datetime.timedelta(days=135) <= data_end]
    oos_yr = max(complete_yrs) if complete_yrs else yr_max
    go = _gstats(fy == oos_yr, c60)
    ls60_oos = go[2][0] - go[0][0]
    partial_yrs = sorted(yr for yr in yr_lastend if yr > oos_yr)

    gm20 = _gstats(full, c20); ls20 = gm20[2][0] - gm20[0][0]      # gate 6 — H20 sign
    gmraw = _gstats(full, raw60); ls60_raw = gmraw[2][0] - gmraw[0][0]   # gate 5 — raw

    nco = int(coh.sum())                                # gate 7 — N225 deployment cohort
    cohort_line, g7 = _cohort_gate(coh, g, c60, sign_ref=ls60)

    print("\n" + "=" * 96)
    print("PEAD — MANAGEMENT-FORECAST-REVISION SURPRISE (β-stripped vs TOPIX, H=60)")
    print("=" * 96)
    print(f"usable events n={n}  (pairs formed {n_pairs})  winsor surp∈[{lo:+.4f},{hi:+.4f}]")
    print("\nPOOLED DISCOVERY — SIGNED TERCILES (gates 1–6, full universe — power only):")
    print(f"  {'group':<14}{'n':>8}{'meanCAR60':>12}{'meanCAR20':>12}")
    for i in range(3):
        print(f"  {GROUPS[i]:<14}{counts[i]:>8}{means[i]*100:>11.2f}%{gm20[i][0]*100:>11.2f}%")
    print(f"  (up − down) β-stripped 60-bar CAR = {ls60*100:+.2f}%   naive Welch t = {t_ls:+.2f}")
    print(f"  Spearman(group, meanCAR60) = {_spearman_up(means):+.3f}")

    print("\nSIZE-GRADIENT DIAGNOSTIC ((up − down) β-stripped 60-bar CAR by TOPIX scale):")
    for sc in ["TOPIX Core30", "TOPIX Large70", "TOPIX Mid400",
               "TOPIX Small 1", "TOPIX Small 2", "-"]:
        m = scales == sc
        if m.sum() < 50:
            print(f"  {sc:<16} n={int(m.sum()):>5}  (too thin)")
            continue
        sg = _gstats(m, c60)
        print(f"  {sc:<16} n={int(m.sum()):>5}  (up−down)={(sg[2][0]-sg[0][0])*100:+.2f}%")

    print("\nPER-FISCAL-YEAR (up − down) β-stripped 60-bar CAR — robustness behind gate 4:")
    for yr in sorted(set(int(x) for x in fy)):
        m = fy == yr
        ym = _gstats(m, c60)
        tag = ("  ← OOS holdout (gate 4)" if yr == oos_yr
               else "  (partial — excluded from OOS)" if yr in partial_yrs else "")
        print(f"  FY{yr}  n={int(m.sum()):>6}  down={ym[0][1]:>5}/up={ym[2][1]:>5}  "
              f"(up−down)={(ym[2][0]-ym[0][0])*100:+.2f}%{tag}")

    print(f"\nN225 DEPLOYMENT COHORT (gate 7, BINDING) — n={nco} of {n}:")
    print("  " + cohort_line)

    # ── gate verdicts ────────────────────────────────────────────────────────
    g1 = _spearman_up(means) > 0 and means[2] > means[0]
    g2 = ls60 > 0 and (t_ls > 2.0)
    g3 = n >= 1000 and min(counts) >= 100               # ≥100 in each signed group
    g4 = (ls60_oos > 0) and (math.copysign(1, ls60_oos) == math.copysign(1, ls60))
    g5 = ls60_raw > 0
    g6 = math.copysign(1, ls20) == math.copysign(1, ls60)
    print("\nGATES (ALL must hold to ACCEPT):")
    for name, ok, detail in [
        ("1 monotone         ", g1, f"ρ>0 & up>down  (up−down={ls60*100:+.2f}%)"),
        ("2 long-short t>2    ", g2, f"t={t_ls:+.2f}"),
        ("3 sample n≥1000/grp ", g3, f"n={n}, min/group={min(counts)}"),
        ("4 OOS same sign     ", g4, f"FY{oos_yr} (up−down)={ls60_oos*100:+.2f}%"
                                     f"  [FY{yr_max} partial={partial_yrs}]"),
        ("5 β-strip survives  ", g5, f"raw (up−down)={ls60_raw*100:+.2f}%"),
        ("6 H20≈H60 sign      ", g6, f"H20 (up−down)={ls20*100:+.2f}%"),
        ("7 N225 cohort       ", g7, "see cohort line above"),
    ]:
        print(f"  [{'PASS' if ok else 'FAIL'}] gate {name} {detail}")
    verdict = "ACCEPT" if all([g1, g2, g3, g4, g5, g6, g7]) else "REJECT"
    print(f"\n  VERDICT (gates 1–7): {verdict}")
    print("  NOTE: gates 1–6 = existence on full universe (power only); gate 7 is the binding "
          "deployment decision. Per the pre-registration, a small-cap-only effect is NEVER "
          "fallen down to N225 — see docs/analysis/pead_forecast_revision_preregistration.md")


def _cohort_gate(coh, g, c60, sign_ref) -> tuple[str, bool]:
    """Gate 7: (up − down) β-stripped 60-bar CAR on the N225 cohort (signed terciles).

    Needs ≥200 cohort events, ≥40 in each of the down/up groups, and the same sign as the
    pooled (up − down). Otherwise n-thin / untestable for our book.
    """
    cg, cc = g[coh], c60[coh]
    nco = len(cg)
    if nco < 200:
        return f"n={nco} < 200 → N-THIN / UNTESTABLE for our book (do NOT use pooled number)", False
    n_dn, n_up = int((cg == 0).sum()), int((cg == 2).sum())
    if n_dn < 40 or n_up < 40:
        return f"n={nco} but extremes thin (down={n_dn}, up={n_up} < 40) → UNTESTABLE", False
    ls = float(cc[cg == 2].mean() - cc[cg == 0].mean())
    same = ls > 0 and (math.copysign(1, ls) == math.copysign(1, sign_ref))
    return (f"down n={n_dn} / up n={n_up}  (up−down)={ls*100:+.2f}%  "
            f"same-sign-as-pooled={'yes' if same else 'NO'}"), same


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
