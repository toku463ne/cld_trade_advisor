"""PEAD — peer-relative forecast-revision surprise (read-only).

Implements docs/analysis/pead_peer_relative_revision_preregistration.md. An independent
signal from the absolute revision surprise (signal 1, ACCEPT): own revision surprise
minus a peer-group reference, drift measured on a PEER-β-stripped (sector-neutral) basis.

Two peer definitions run in ONE pass and judged side-by-side (data, not preference, picks):
  Variant A — sector33 peers (+ sector17 fallback)   [fundamental peers]
  Variant B — trailing-120d top-K=20 corr peers ≥0.30 [economic peers, conglomerate-robust]

The peer-reference / peer-relative-surprise / peer-portfolio logic lives in pure functions
(unit-tested in tests/test_pead_peer_relative_revision.py). run() is a thin driver.

Reuses signal-1 pure functions where identical. The peer measure is accepted only if it
clears the FULL gate stack incl. gate 7 (incremental over the absolute measure) and the
BINDING gate 8 (N225 deployment cohort).

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.pead_peer_relative_revision
"""
from __future__ import annotations

import bisect
import datetime
import math
import sys

import numpy as np
from loguru import logger

from src.analysis.pead_forecast_revision import (
    Disclosure, beta, doc_basis, pair_same_fy_revisions, revision_surprise,
    tradable_entry_day, _spearman_up, _welch_t,
)

_BETA_WIN = 60
_HORIZONS = (20, 60)
_W_DAYS = 90            # peer-reference trailing window (calendar days)
_PEER_FLOOR = 3        # ≥3 distinct usable peer revisions (else fallback, then exclude)
_CORR_K = 20           # variant B: top-K corr peers
_CORR_WIN = 120        # variant B: trailing corr window (trading bars)
_CORR_FLOOR = 0.30     # variant B: minimum trailing corr to qualify as a peer


# ── pure logic (unit-tested) ─────────────────────────────────────────────────
def peer_reference_surprise(
    peer_rev_lists: list[list[tuple[datetime.date, float]]],
    t: datetime.date, window_days: int = _W_DAYS, floor: int = _PEER_FLOOR,
) -> float | None:
    """Median of peers' most-recent absolute revision surprise in [t−window, t).

    Each element of `peer_rev_lists` is one peer's date-sorted (disclosed_date, surprise)
    history. A peer contributes its most recent surprise strictly before `t` and within
    `window_days`. Returns None if fewer than `floor` distinct peers contribute.
    """
    lo = t - datetime.timedelta(days=window_days)
    vals: list[float] = []
    for hist in peer_rev_lists:
        if not hist:
            continue
        dates = [d for d, _ in hist]
        j = bisect.bisect_left(dates, t) - 1     # most recent strictly before t
        if j >= 0 and dates[j] >= lo:
            vals.append(hist[j][1])
    if len(vals) < floor:
        return None
    return float(np.median(vals))


def bin_edges(values: np.ndarray, nbins: int) -> list[float]:
    qs = [100.0 * i / nbins for i in range(1, nbins)]
    return list(np.percentile(values, qs)) if len(values) else []


def bin_of(x: float, edges: list[float]) -> int:
    return bisect.bisect_right(edges, x)


# ── thin DB driver ───────────────────────────────────────────────────────────
def _load() -> tuple:
    from collections import Counter, defaultdict

    from sqlalchemy import select

    from src.data.db import get_session
    from src.data.jquants_collector import to_yf_code
    from src.data.jquants_models import JqDailyQuote, JqListed, JqStatement, JqTopix
    from src.data.models import Ohlcv1d

    with get_session() as s:
        stmts = s.execute(
            select(JqStatement.local_code, JqStatement.disclosed_date,
                   JqStatement.disclosed_time, JqStatement.current_fiscal_year_end_date,
                   JqStatement.forecast_earnings_per_share, JqStatement.type_of_document)).all()
        topix = s.execute(select(JqTopix.date, JqTopix.close)
                          .where(JqTopix.close.isnot(None)).order_by(JqTopix.date)).all()
        listed = s.execute(select(JqListed.code, JqListed.sector33_code,
                                  JqListed.sector17_code, JqListed.scale_category)).all()
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

    sec33 = {c: s33 for c, s33, _s17, _sc in listed}
    sec17 = {c: s17 for c, _s33, s17, _sc in listed}
    scale_of = {c: sc for c, _s33, _s17, sc in listed}
    sec33_members: dict[str, list[str]] = defaultdict(list)
    sec17_members: dict[str, list[str]] = defaultdict(list)
    for c in codes:
        if sec33.get(c):
            sec33_members[sec33[c]].append(c)
        if sec17.get(c):
            sec17_members[sec17[c]].append(c)

    # disclosures per code, modal-basis resolved (same as signal 1)
    raw: dict[str, list[tuple]] = defaultdict(list)
    for code, dd, dt, fy, feps, tod in stmts:
        raw[code].append((dd, dt, fy, feps, tod))
    by_code: dict[str, list[Disclosure]] = {}
    for code, rows in raw.items():
        fin = Counter(b for b in (doc_basis(r[4]) for r in rows) if b)
        modal = fin.most_common(1)[0][0] if fin else None
        by_code[code] = [Disclosure(dd, dt, fy, feps, doc_basis(tod) or modal)
                         for dd, dt, fy, feps, tod in rows]

    return (stmts, topix, cal, col_of, row_of, codes, arr, topix_arr, cohort,
            sec33, sec17, scale_of, sec33_members, sec17_members, by_code, to_yf_code)


def _event_cars(arr, topix_arr, ri, peer_ris, ei):
    """(car60_peer, car20_peer, car60_double) or None. Peer-β-strip + double (TOPIX+peer)."""
    lo, hi = ei - _BETA_WIN - 1, ei + max(_HORIZONS)
    if lo < 0 or hi >= arr.shape[1] or not peer_ris:
        return None
    s = arr[ri]
    if not (s[ei] > 0):
        return None
    # peer-portfolio daily returns over cal days [lo+1 .. hi], equal-weight, priced-only
    sub = arr[np.asarray(peer_ris), lo:hi + 1]
    with np.errstate(invalid="ignore"):
        prr = sub[:, 1:] / sub[:, :-1] - 1.0
    prm = np.nanmean(np.where(np.isfinite(prr), prr, np.nan), axis=0)   # idx k → cal day lo+1+k
    if np.isnan(prm).all():
        return None

    def cum(a: int, b: int) -> float | None:           # cal days a+1..b, peer portfolio
        seg = prm[(a + 1) - (lo + 1):(b) - (lo + 1) + 1]
        seg = seg[np.isfinite(seg)]
        return float(np.prod(1.0 + seg) - 1.0) if len(seg) else None

    s_tr = s[ei - _BETA_WIN:ei] / s[ei - _BETA_WIN - 1:ei - 1] - 1.0
    p_tr = prm[0:_BETA_WIN]
    b_peer = beta(s_tr, p_tr)
    if b_peer is None:
        return None
    pc60, pc20 = cum(ei, ei + 60), cum(ei, ei + 20)
    if pc60 is None or pc20 is None:
        return None
    sc60 = float(s[ei + 60] / s[ei] - 1.0)
    sc20 = float(s[ei + 20] / s[ei] - 1.0)
    car60 = sc60 - b_peer * pc60
    car20 = sc20 - b_peer * pc20
    # double strip: also remove univariate TOPIX beta (gate-5 robustness; betas univariate)
    m_tr = topix_arr[ei - _BETA_WIN:ei] / topix_arr[ei - _BETA_WIN - 1:ei - 1] - 1.0
    b_mkt = beta(s_tr, m_tr)
    tc60 = float(topix_arr[ei + 60] / topix_arr[ei] - 1.0)
    car60d = car60 - (b_mkt * tc60 if b_mkt is not None else 0.0)
    if any(math.isnan(x) for x in (car60, car20, car60d)):
        return None
    return car60, car20, car60d


def _corr_peers(ret, ri, ei, k=_CORR_K, win=_CORR_WIN, floor=_CORR_FLOOR):
    """Variant-B peer rows: top-k codes by trailing-`win` corr to ri, corr≥floor, excl self.

    `ret[:, j]` = return on cal day j+1, so trailing `win` returns ending day ei−1 are
    columns [ei−1−win : ei−1]."""
    c0 = ei - 1 - win
    if c0 < 0:
        return []
    W = ret[:, c0:ei - 1]                       # codes × win
    valid = np.isfinite(W).all(axis=1)
    if not valid[ri]:
        return []
    x = W[ri]
    xz = (x - x.mean()) / (x.std() or 1.0)
    idxs = np.where(valid)[0]
    Rv = W[idxs]
    mu = Rv.mean(1, keepdims=True)
    sd = Rv.std(1, keepdims=True)
    sd[sd == 0] = 1.0
    corr = ((Rv - mu) / sd) @ xz / win
    order = np.argsort(-corr)
    out: list[int] = []
    for j in order:
        gi = int(idxs[j])
        if gi == ri or corr[j] < floor:
            if corr[j] < floor:
                break                            # sorted desc → nothing more qualifies
            continue
        out.append(gi)
        if len(out) >= k:
            break
    return out


def run() -> None:  # noqa: C901
    (stmts, topix, cal, col_of, row_of, codes, arr, topix_arr, cohort,
     sec33, sec17, scale_of, sec33_members, sec17_members, by_code, to_yf_code) = _load()
    if not stmts or not topix:
        logger.warning("jq_* not populated (statements={}, topix={}). Load the backfill.",
                       len(stmts), len(topix))
        return
    logger.info("loaded {} statements, {} cal days, {} priced codes, cohort {}",
                len(stmts), len(cal), len(codes), len(cohort))

    with np.errstate(invalid="ignore"):
        ret = arr[:, 1:] / arr[:, :-1] - 1.0     # codes × (T-1); ret[:,j] = day j+1 return

    # per-code absolute revision history (for peer references) + evaluation events
    peer_rev: dict[str, list[tuple[datetime.date, float]]] = {}
    events: list[tuple] = []          # (code, ri, ei, own_surprise, fy_year, in_coh, scale)
    n_pairs = 0
    for code, discs in by_code.items():
        ri = row_of.get(code)
        if ri is None:
            continue
        srow = arr[ri]
        hist: list[tuple[datetime.date, float]] = []
        in_coh = to_yf_code(code) in cohort
        sc = scale_of.get(code)
        for prev, curr in pair_same_fy_revisions(discs):
            n_pairs += 1
            if curr.fy_end is None:
                continue
            entry = tradable_entry_day(curr.disclosed_date, curr.disclosed_time, cal)
            if entry is None:
                continue
            ei = col_of[entry]
            price = srow[ei - 1] if ei >= 1 else None
            sp = revision_surprise(prev.forecast_eps, curr.forecast_eps,
                                   float(price) if price and price > 0 else None)
            if sp is None:
                continue
            hist.append((curr.disclosed_date, sp))
            if _BETA_WIN + 1 <= ei and ei + max(_HORIZONS) < len(cal):
                events.append((code, ri, ei, sp, curr.fy_end.year, in_coh, sc))
        if hist:
            hist.sort(key=lambda x: x[0])
            peer_rev[code] = hist
    logger.info("formed {} pairs; {} candidate events; {} codes with revision history",
                n_pairs, len(events), len(peer_rev))

    # ── assemble per-event peer-relative records for BOTH variants in one pass ──
    rows_a: list[dict] = []
    rows_b: list[dict] = []
    for n_done, (code, ri, ei, own, fyy, in_coh, sc) in enumerate(events, 1):
        if n_done % 5000 == 0:
            logger.info("  processed {}/{} events (A kept {}, B kept {})",
                        n_done, len(events), len(rows_a), len(rows_b))
        t = cal[ei]
        # ----- Variant A: sector33 (+ sector17 fallback) -----
        for members_lvl in (sec33_members.get(sec33.get(code) or "", []),
                            sec17_members.get(sec17.get(code) or "", [])):
            peers = [c for c in members_lvl if c != code]
            R = peer_reference_surprise([peer_rev.get(c, []) for c in peers], t)
            if R is None:
                continue
            peer_ris = [row_of[c] for c in peers if c in row_of]
            cars = _event_cars(arr, topix_arr, ri, peer_ris, ei)
            if cars is None:
                continue
            rows_a.append(dict(rel=own - R, R=R, own=own, c60=cars[0], c20=cars[1],
                               c60d=cars[2], fy=fyy, coh=in_coh, scale=sc))
            break
        # ----- Variant B: trailing-corr top-K peers -----
        peer_ris_b = _corr_peers(ret, ri, ei)
        if peer_ris_b:
            peer_codes_b = [codes[g] for g in peer_ris_b]
            R = peer_reference_surprise([peer_rev.get(c, []) for c in peer_codes_b], t)
            if R is not None:
                cars = _event_cars(arr, topix_arr, ri, peer_ris_b, ei)
                if cars is not None:
                    rows_b.append(dict(rel=own - R, R=R, own=own, c60=cars[0], c20=cars[1],
                                       c60d=cars[2], fy=fyy, coh=in_coh, scale=sc))

    data_end = cal[-1]
    print("\n" + "=" * 96)
    print("PEAD — PEER-RELATIVE FORECAST-REVISION SURPRISE (peer-β-stripped, H=60)")
    print("=" * 96)
    va = _evaluate("A  sector33(+17 fallback)", rows_a, data_end)
    vb = _evaluate("B  trailing-corr top-20", rows_b, data_end)

    print("\n" + "=" * 96)
    print("BOTH VERDICTS (data, not preference, decides — per the pre-registration):")
    print(f"  Variant A (sector):          {va}")
    print(f"  Variant B (trailing-corr):   {vb}")
    if va == "ACCEPT" and vb == "ACCEPT":
        print("  → both clear; if wired jointly, A/B as CORRELATED votes (not additive) vs signal 1.")
    elif "ACCEPT" in (va, vb):
        print("  → exactly one peer concept carries the drift (evidence about which).")
    else:
        print("  → neither clears: peer-relative revision signal REJECTED. Do not iterate to a "
              "third peer definition without a new pre-registration.")


def _signed_ls(vals: np.ndarray, car: np.ndarray):
    """(pos−neg, t, n_neg, n_pos) using SIGNED terciles {v<0 / v=0 / v>0}.

    The pre-registered degeneracy fallback: rel (and the reaffirmation R) carry a mass
    point at exactly 0 (own=peer-median), so value-percentile terciles collapse the middle
    bin. Sign-grouping is the faithful tool, identical to signal 1's signed terciles."""
    g = np.where(vals < 0, 0, np.where(vals > 0, 2, 1))
    neg, pos = car[g == 0], car[g == 2]
    if len(neg) < 2 or len(pos) < 2:
        return float("nan"), float("nan"), len(neg), len(pos)
    return float(pos.mean() - neg.mean()), _welch_t(pos, neg), len(neg), len(pos)


def _evaluate(name: str, rows: list[dict], data_end: datetime.date) -> str:  # noqa: C901
    print("\n" + "-" * 96)
    print(f"VARIANT {name}   —   n_events = {len(rows)}")
    if len(rows) < 100:
        print("  too few events — UNTESTABLE")
        return "REJECT"
    rel = np.array([r["rel"] for r in rows])
    c60 = np.array([r["c60"] for r in rows])
    c20 = np.array([r["c20"] for r in rows])
    c60d = np.array([r["c60d"] for r in rows])
    fy = np.array([r["fy"] for r in rows])
    coh = np.array([r["coh"] for r in rows])
    own = np.array([r["own"] for r in rows])
    Rref = np.array([r["R"] for r in rows])
    scales = np.array([r["scale"] for r in rows], dtype=object)
    lo, hi = np.percentile(rel, [0.5, 99.5])
    rel = np.clip(rel, lo, hi)

    # binning: value quintiles, fall back to signed terciles if any quintile < 5%
    edges = bin_edges(rel, 5)
    gq = np.array([bin_of(v, edges) for v in rel])
    counts5 = [int((gq == i).sum()) for i in range(5)]
    use_quint = min(counts5) >= 0.05 * len(rel)
    if use_quint:
        nb, g, blab = 5, gq, "QUINTILES"
    else:
        g = np.where(rel < 0, 0, np.where(rel > 0, 2, 1))   # pre-registered signed-tercile fallback
        nb, blab = 3, "SIGNED TERCILES {rel<0 / rel=0 / rel>0} (pre-registered quintile-degeneracy fallback)"

    def stats(mask, car):
        gg, cm = g[mask], car[mask]
        return [(float(cm[gg == i].mean()) if (gg == i).any() else float("nan"),
                 int((gg == i).sum()), cm[gg == i]) for i in range(nb)]

    full = np.ones(len(rel), dtype=bool)
    sm = stats(full, c60)
    means = [m for m, _, _ in sm]
    counts = [c for _, c, _ in sm]
    ls60 = means[-1] - means[0]
    t_ls = _welch_t(sm[-1][2], sm[0][2])
    sm20 = stats(full, c20); ls20 = sm20[-1][0] - sm20[0][0]
    smd = stats(full, c60d); ls60d = smd[-1][0] - smd[0][0]

    print(f"  binning: {blab}   winsor rel∈[{lo:+.4f},{hi:+.4f}]")
    print(f"  {'bin':<6}{'n':>8}{'peerCAR60':>12}{'peerCAR20':>12}{'doubleCAR60':>14}")
    for i in range(nb):
        print(f"  {('Q' if nb == 5 else 'T')+str(i+1):<6}{counts[i]:>8}"
              f"{means[i]*100:>11.2f}%{sm20[i][0]*100:>11.2f}%{smd[i][0]*100:>13.2f}%")
    print(f"  (top−bottom) peer-β-stripped 60-bar CAR = {ls60*100:+.2f}%   naive Welch t={t_ls:+.2f}")
    print(f"  Spearman(bin, meanCAR60) = {_spearman_up(means):+.3f}")

    # OOS — most-recent complete FY
    yr_last = {int(y): max(r["fy"] for r in rows if r["fy"] == int(y)) for y in set(fy)}
    # fy stores YEAR only; approximate fy_end as Mar-31 of that year for the 135d rule
    complete = [y for y in set(int(x) for x in fy)
                if datetime.date(y, 3, 31) + datetime.timedelta(days=135) <= data_end]
    oos = max(complete) if complete else int(fy.max())
    so = stats(fy == oos, c60)
    ls_oos = so[-1][0] - so[0][0]

    print("\n  PER-FY (top−bottom) peer-β-stripped 60-bar CAR:")
    for yr in sorted(set(int(x) for x in fy)):
        ym = stats(fy == yr, c60)
        tag = "  ← OOS (gate 4)" if yr == oos else ""
        print(f"    FY{yr}  n={int((fy==yr).sum()):>6}  (top−bot)={(ym[-1][0]-ym[0][0])*100:+.2f}%{tag}")

    print("\n  SIZE-GRADIENT ((top−bottom) peer-β-stripped 60-bar CAR by TOPIX scale):")
    for scn in ["TOPIX Core30", "TOPIX Large70", "TOPIX Mid400", "TOPIX Small 1",
                "TOPIX Small 2", "-"]:
        m = scales == scn
        if m.sum() < 50:
            continue
        sg = stats(m, c60)
        print(f"    {scn:<16} n={int(m.sum()):>6}  (top−bot)={(sg[-1][0]-sg[0][0])*100:+.2f}%")

    # gate 7a — incremental: within each own-revision group, peer-relative still sorts
    print("\n  GATE 7a (incremental within own-revision groups, tercile T3−T1):")
    g7a = True
    for gi, glab in [(own < 0, "down"), (own == 0, "reaffirm"), (own > 0, "up")]:
        sub_rel, sub_car = rel[gi], c60[gi]
        d, tt, n1, n3 = _signed_ls(sub_rel, sub_car)
        ok = (not math.isnan(d)) and d > 0 and math.copysign(1, d) == math.copysign(1, ls60)
        g7a = g7a and ok
        print(f"    {glab:<9} n={int(gi.sum()):>6}  (T3−T1)={d*100:+.2f}% t={tt:+.2f}  "
              f"{'ok' if ok else 'NO'}")

    # gate 7b — reaffirmation subgroup, peer-ref terciles, monotone DECREASING in R
    reaff = own == 0
    nra = int(reaff.sum())
    g7b_detail = f"n={nra}"
    g7b = False
    if nra >= 9:
        Rsub, csub = Rref[reaff], c60[reaff]
        gR = np.where(Rsub < 0, 0, np.where(Rsub > 0, 2, 1))   # signed: cut / flat / raise
        cut = csub[gR == 0]      # peers cutting (R < 0)
        rai = csub[gR == 2]      # peers raising (R > 0)
        if len(cut) >= 60 and len(rai) >= 60 and nra >= 300:
            dd = float(cut.mean() - rai.mean())
            g7b = dd > 0
            g7b_detail = (f"n={nra} (cut n={len(cut)} {cut.mean()*100:+.2f}% / "
                          f"raise n={len(rai)} {rai.mean()*100:+.2f}%) → (cut−raise)={dd*100:+.2f}%")
        else:
            g7b_detail = f"n={nra} but extremes/sample thin (cut={len(cut)},raise={len(rai)},need≥300)"
    print(f"  GATE 7b (reaffirm-while-peers-cut > reaffirm-while-peers-raise): {g7b_detail} "
          f"→ {'PASS' if g7b else 'FAIL'}")

    # gate 8 — N225 deployment cohort (BINDING)
    cn = int(coh.sum())
    gc = g[coh]; cc = c60[coh]
    if cn < 200:
        coh_line, g8 = f"n={cn} < 200 → N-THIN / UNTESTABLE for our book", False
    else:
        # signed-tercile long-short on cohort (robust to the rel=0 mass point)
        rel_c = rel[coh]
        d, tt, n1, n3 = _signed_ls(rel_c, cc)
        if n1 < 40 or n3 < 40:
            coh_line, g8 = f"n={cn} but extremes thin (neg={n1},pos={n3}<40) → UNTESTABLE", False
        else:
            same = d > 0 and math.copysign(1, d) == math.copysign(1, ls60)
            coh_line = f"n={cn}  (pos−neg)={d*100:+.2f}% t={tt:+.2f}  same-sign={'yes' if same else 'NO'}"
            g8 = same
    print(f"\n  N225 COHORT (gate 8, BINDING): {coh_line}")

    g1 = _spearman_up(means) > 0 and means[-1] > means[0]
    g2 = ls60 > 0 and t_ls > 2.0
    g3 = len(rel) >= 1000 and min(counts) >= 100
    g4 = ls_oos > 0 and math.copysign(1, ls_oos) == math.copysign(1, ls60)
    g5 = ls60d > 0 and math.copysign(1, ls60d) == math.copysign(1, ls60)
    g6 = math.copysign(1, ls20) == math.copysign(1, ls60)
    print("\n  GATES:")
    for nm, ok, det in [
        ("1 monotone        ", g1, f"ρ={_spearman_up(means):+.3f}, top−bot={ls60*100:+.2f}%"),
        ("2 long-short t>2   ", g2, f"t={t_ls:+.2f}"),
        ("3 sample≥1000/≥100 ", g3, f"n={len(rel)} min/bin={min(counts)}"),
        ("4 OOS same sign    ", g4, f"FY{oos} (top−bot)={ls_oos*100:+.2f}%"),
        ("5 double-β survives", g5, f"double (top−bot)={ls60d*100:+.2f}%"),
        ("6 H20≈H60 sign     ", g6, f"H20 (top−bot)={ls20*100:+.2f}%"),
        ("7a incremental     ", g7a, "within-own-group T3−T1 > 0"),
        ("7b reaffirm cut>raise", g7b, g7b_detail.split(" →")[0]),
        ("8 N225 cohort      ", g8, "binding"),
    ]:
        print(f"    [{'PASS' if ok else 'FAIL'}] gate {nm} {det}")
    accept = all([g1, g2, g3, g4, g5, g6, g7a, g7b, g8])
    verdict = "ACCEPT" if accept else "REJECT"
    print(f"  VARIANT {name.split()[0]} VERDICT: {verdict}")
    return verdict


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
