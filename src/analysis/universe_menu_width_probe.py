"""Stage-0 universe-expansion "menu-width" probe (read-only).

Question (root unblocker, reinforced by the PEAD sleeve REJECT 2026-05-27): the confluence
book ties the equal-weight universe on Sharpe (only a drawdown edge), and every selection /
harvest rule dies to the fill-order null because ~36 trades/yr in the 225-name Nikkei cohort
has too little slot contention. Does expanding to a broader **liquid ∩ affordable 単元株
tier** of the full ~4,495-stock J-Quants universe actually WIDEN the menu of *genuinely
low-correlation* candidates available on a given day — i.e. give more real diversification
(and thus real slot contention) than the 225 we trade now?

DESIGN (pre-agreed, project_jquants_pead_universe.md):
- Correlate vs **TOPIX + PAIRWISE, never ^N225**. Membership endogeneity: a Nikkei
  constituent's ρ-to-N225 is partly self-inclusion, and ^N225 (price-weighted large-cap) is
  the wrong market proxy for non-members (they may be low-ρ to N225 but high-ρ to TOPIX /
  sector peers = false diversification).
- **Affordable** = raw close ≤ ¥2,000,000 / 6 slots / 100 shares ≈ ¥3,333 (≥1 単元 fits a slot).
- **Liquid** = median trailing-60-bar `turnover_value` ≥ ¥100M/day (retail 寄付 tradability).
- Per a monthly as-of grid, on trailing-60-bar daily returns, report for the CURRENT
  (225-cohort, affordable) vs EXPANDED (liquid∩affordable) menus:
    (a) count of genuinely-low-corr-to-TOPIX names (|ρ60| ≤ 0.30) — the alpha-carrier menu,
    (b) best-achievable 6-name basket max & mean pairwise |ρ| (greedy least-correlated),
    (c) distinct sector33 breadth,
    (d) how many low-corr names expansion adds that are NOT already in the 225.

NOT a decision — Stage 0. Only if the menu MATERIALLY widens does Stage 1 (pipeline rebuild
+ held-out β-stripped backtest) get justified. If the 225 already saturates diversification,
expansion is a no-op and the equal-weight tie stands.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.universe_menu_width_probe
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

_PRICE_CEIL = 2_000_000 / 6 / 100      # ≈ ¥3,333 — one 単元 fits a ¥2M/6 slot
_TURN_FLOOR = 100_000_000.0            # ¥100M median daily turnover
_CORR_WIN = 60
_CORR_TOPIX_LO = 0.30                  # |ρ to TOPIX| ≤ this = "genuinely low-corr"
_BASKET = 6
_BASKET_POOL_CAP = 300                 # cap the low-corr pool fed to the greedy basket (cost)
_START = datetime.date(2018, 4, 1)


def _greedy_basket(corr: np.ndarray, k: int) -> tuple[float, float]:
    """Greedy least-correlated k-basket from a |corr| matrix. Seed = the pair with the
    lowest |ρ|; then add the name minimizing the max |ρ| to the current basket. Returns
    (max pairwise |ρ|, mean pairwise |ρ|) of the chosen basket. nan if < k names."""
    n = corr.shape[0]
    if n < k:
        return float("nan"), float("nan")
    iu = np.triu_indices(n, 1)
    p = int(np.argmin(corr[iu]))
    i, j = iu[0][p], iu[1][p]
    chosen = [int(i), int(j)]
    while len(chosen) < k:
        rest = [r for r in range(n) if r not in chosen]
        # name minimizing the max |ρ| to the already-chosen basket
        nxt = min(rest, key=lambda r: max(corr[r, c] for c in chosen))
        chosen.append(int(nxt))
    sub = corr[np.ix_(chosen, chosen)]
    su = sub[np.triu_indices(len(chosen), 1)]
    return float(su.max()), float(su.mean())


def run() -> None:  # noqa: C901 — single linear assembly + reporting
    from src.data.db import get_session
    from src.data.jquants_collector import to_yf_code
    from src.data.jquants_models import JqDailyQuote, JqListed, JqTopix
    from src.data.models import Ohlcv1d

    with get_session() as s:
        topix = s.execute(select(JqTopix.date, JqTopix.close)
                          .where(JqTopix.close.isnot(None)).order_by(JqTopix.date)).all()
        cohort = {c for (c,) in s.execute(select(Ohlcv1d.stock_code).distinct())}
        codes = [c for (c,) in s.execute(select(JqDailyQuote.code).distinct()
                                         .order_by(JqDailyQuote.code))]
        sector_of = {c: (s33 or "—") for c, s33 in
                     s.execute(select(JqListed.code, JqListed.sector33_name))}
        cal = [d for d, _ in topix]
        col_of = {d: i for i, d in enumerate(cal)}
        row_of = {c: i for i, c in enumerate(codes)}
        topix_arr = np.array([float(c) for _, c in topix], dtype=np.float64)
        nC, nD = len(codes), len(cal)
        adj = np.full((nC, nD), np.nan, dtype=np.float32)
        rawc = np.full((nC, nD), np.nan, dtype=np.float32)
        turn = np.full((nC, nD), np.nan, dtype=np.float32)
        stream = s.connection().execution_options(stream_results=True, yield_per=200_000)
        for code, d, ac, cl, tv in stream.execute(
                select(JqDailyQuote.code, JqDailyQuote.date, JqDailyQuote.adj_close,
                       JqDailyQuote.close, JqDailyQuote.turnover_value)):
            ci = col_of.get(d)
            ri = row_of.get(code)
            if ci is None or ri is None:
                continue
            if ac is not None:
                adj[ri, ci] = float(ac)
            if cl is not None:
                rawc[ri, ci] = float(cl)
            if tv is not None:
                turn[ri, ci] = float(tv)

    if not topix:
        logger.warning("jq_topix empty — backfill not loaded")
        return
    in_cohort = np.array([to_yf_code(c) in cohort for c in codes])
    logger.info("loaded {} codes ({} in 225 cohort), {} cal days",
                nC, int(in_cohort.sum()), nD)

    # monthly as-of grid (first trading day of each month with a full trailing window)
    asof = []
    seen_ym = set()
    for i, d in enumerate(cal):
        if i < _CORR_WIN + 1 or d < _START:
            continue
        ym = (d.year, d.month)
        if ym not in seen_ym:
            seen_ym.add(ym)
            asof.append(i)
    logger.info("as-of grid: {} month-starts {}..{}", len(asof), cal[asof[0]], cal[asof[-1]])

    # accumulators (per-date values → report medians)
    rec = defaultdict(list)   # metric -> list over dates

    for ai in asof:
        w = slice(ai - _CORR_WIN, ai + 1)             # 61 prices → 60 returns
        wa = adj[:, w]
        valid = np.isfinite(wa).all(axis=1) & (wa[:, 0] > 0)
        # trailing-60 returns
        rets = np.full((nC, _CORR_WIN), np.nan, dtype=np.float64)
        rets[valid] = wa[valid, 1:] / wa[valid, :-1] - 1.0
        t_ret = topix_arr[ai - _CORR_WIN + 1:ai + 1] / topix_arr[ai - _CORR_WIN:ai] - 1.0
        # corr to TOPIX (standardized dot)
        rs = rets - np.nanmean(rets, axis=1, keepdims=True)
        rsd = np.nanstd(rets, axis=1)
        ts = t_ret - t_ret.mean()
        tsd = t_ret.std()
        with np.errstate(invalid="ignore", divide="ignore"):
            corr_topix = (rs @ ts) / (_CORR_WIN * rsd * tsd)
        # liquidity + affordability at as-of
        med_turn = np.nanmedian(turn[:, ai - _CORR_WIN:ai + 1], axis=1)
        price = rawc[:, ai]
        liquid = np.nan_to_num(med_turn, nan=0.0) >= _TURN_FLOOR
        afford = np.isfinite(price) & (price > 0) & (price <= _PRICE_CEIL)
        usable = valid & np.isfinite(corr_topix)

        cur = usable & in_cohort & afford                       # current menu (affordable 225)
        exp = usable & liquid & afford                          # expanded menu
        lowcur = cur & (np.abs(corr_topix) <= _CORR_TOPIX_LO)
        lowexp = exp & (np.abs(corr_topix) <= _CORR_TOPIX_LO)
        # genuinely-new low-corr names expansion adds (not in the 225)
        lownew = lowexp & (~in_cohort)

        rec["cur_n"].append(int(cur.sum()))
        rec["exp_n"].append(int(exp.sum()))
        rec["lowcur"].append(int(lowcur.sum()))
        rec["lowexp"].append(int(lowexp.sum()))
        rec["lownew"].append(int(lownew.sum()))
        rec["sec_cur"].append(len({sector_of.get(codes[i], "—") for i in np.where(lowcur)[0]}))
        rec["sec_exp"].append(len({sector_of.get(codes[i], "—") for i in np.where(lowexp)[0]}))

        # best 6-basket pairwise corr, from the low-corr pool (cap for cost)
        for tag, mask in (("cur", lowcur), ("exp", lowexp)):
            idx = np.where(mask)[0]
            if idx.size > _BASKET_POOL_CAP:                      # keep the lowest |ρ-to-TOPIX|
                idx = idx[np.argsort(np.abs(corr_topix[idx]))[:_BASKET_POOL_CAP]]
            if idx.size < _BASKET:
                rec[f"bmax_{tag}"].append(np.nan)
                rec[f"bmean_{tag}"].append(np.nan)
                continue
            R = rets[idx]
            C = np.abs(np.corrcoef(R))
            np.fill_diagonal(C, 0.0)
            bmax, bmean = _greedy_basket(C, _BASKET)
            rec[f"bmax_{tag}"].append(bmax)
            rec[f"bmean_{tag}"].append(bmean)

    def med(k):
        a = np.array(rec[k], dtype=np.float64)
        a = a[np.isfinite(a)]
        return float(np.median(a)) if a.size else float("nan")

    print("\n" + "=" * 92)
    print("UNIVERSE MENU-WIDTH PROBE — current 225 (affordable) vs expanded liquid∩affordable")
    print("=" * 92)
    print(f"as-of grid: {len(asof)} month-starts | corr win {_CORR_WIN}b vs TOPIX | "
          f"price ≤ ¥{_PRICE_CEIL:,.0f} | turnover ≥ ¥{_TURN_FLOOR:,.0f}/d")
    print(f"\n{'metric':<46}{'current':>12}{'expanded':>12}")
    rows = [
        ("candidate menu size (names/day, median)", "cur_n", "exp_n"),
        (f"low-corr-to-TOPIX names (|ρ|≤{_CORR_TOPIX_LO}, median/day)", "lowcur", "lowexp"),
        ("  └ distinct sector33 among low-corr (median)", "sec_cur", "sec_exp"),
        ("best 6-basket MAX pairwise |ρ| (median, ↓=better)", "bmax_cur", "bmax_exp"),
        ("best 6-basket MEAN pairwise |ρ| (median, ↓=better)", "bmean_cur", "bmean_exp"),
    ]
    for lab, kc, ke in rows:
        vc, ve = med(kc), med(ke)
        fmt = (lambda x: f"{x:>12.2f}") if "ρ" in lab or "sector" in lab else (lambda x: f"{x:>12.0f}")
        print(f"{lab:<46}{fmt(vc)}{fmt(ve)}")
    print(f"\n  low-corr names expansion ADDS that are NOT in the 225 (median/day): "
          f"{med('lownew'):.0f}")

    # verdict heuristic
    widen_names = med("lowexp") >= 1.5 * max(med("lowcur"), 1)
    widen_basket = (np.isfinite(med("bmax_exp")) and np.isfinite(med("bmax_cur"))
                    and med("bmax_exp") <= med("bmax_cur") - 0.05)
    new_supply = med("lownew") >= max(5.0, 0.5 * med("lowcur"))
    print("\n" + "-" * 92)
    print("STAGE-0 READ (heuristic, not a gate):")
    print(f"  more low-corr names?   expanded {med('lowexp'):.0f} vs current {med('lowcur'):.0f}"
          f"  → {'YES (≥1.5×)' if widen_names else 'no'}")
    print(f"  better basket corr?    max|ρ| {med('bmax_exp'):.2f} vs {med('bmax_cur'):.2f}"
          f"  → {'YES (≥0.05 lower)' if widen_basket else 'no'}")
    print(f"  genuinely-new supply?  +{med('lownew'):.0f} low-corr names not in the 225"
          f"  → {'YES' if new_supply else 'no'}")
    widens = sum([widen_names, widen_basket, new_supply])
    print(f"\n  VERDICT: {'MENU WIDENS — Stage-1 (pipeline rebuild + held-out backtest) justified' if widens >= 2 else 'MENU DOES NOT MATERIALLY WIDEN — 225 ~saturates diversification; expansion likely a no-op'}"
          f"  ({widens}/3 signals)")
    print("  Stage 1 is gated on this; a no-widen result keeps the equal-weight-universe tie "
          "standing and closes universe expansion as a harvest path at current sizing.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
