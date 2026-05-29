"""Per-stock β-stripped ALPHA stop — the untested exit quadrant (backlog item 7), discovery screen.

Backlog item 7 (docs/analysis/confluence_improvement_backlog.md), pre-reg
docs/analysis/confluence_alpha_stop_preregistration.md. The confluence exit space is a 2x2
{per-stock, market-regime} x {raw-price, β-stripped alpha}: per-stock-raw-price EXHAUSTED (ZsTpSl≈best),
market-regime REJECT (item 3, regime-inverse trap). OPEN cell = per-stock β-stripped ALPHA: exit a held
name on erosion of its OWN cumulative alpha (β from a pre-entry window), not on the market.

Distinct from item 3: stripping the market means it fires only on names breaking down IDIOSYNCRATICALLY,
so it does NOT fight the regime-inverse bear-recovery alpha. CAVEAT: the −22% drawdown is BETA-driven, so
an alpha stop will NOT cut it → this is a RETURN/alpha lever (cut idiosyncratic losers), judged on
Sharpe/CAGR. PRIOR: pead_sleeve_alpha_stop_probe (β≈1 PEAD) WHIPSAWED at every θ — sets the prior that
transient alpha dips recover and a stop churns.

METHOD (post-hoc exit override, mirrors confluence_regime_exit_probe): reconstruct the production ¥2M
6-slot budget book FY2018-2025; on the baseline filled trades override each exit to the EARLIER of its
ZsTpSl exit or the first bar its alpha-stop triggers; freed slots NOT re-filled (conservative; same
entry set across arms). β = trailing 60-bar daily-return regression of stock on ^N225, strictly before
entry. α_cum[d] = (s[d]/s[entry]−1) − β·(n225[d]/n225[entry]−1), anchored at entry-date close.
  LEVEL lvlNN: exit at first bar α_cum ≤ −NN%   (θ ∈ {5,8,12}%)
  TRAIL trlNN: exit at first bar α_cum ≤ peak−NN% (X ∈ {5,8,12}%)
WHIPSAW check: for each stop, WHIPSAW if α_cum at the BASELINE exit date > α_cum at the stop bar (name
recovered); HELPED otherwise. whip% reported per variant.

DISCOVERY GATE (frozen): escalate iff (Sharpe ≥ base+0.05 OR CAGR ≥ base+1pp) AND whip% < 50% AND FY2024
Sharpe ≥ base FY2024 − 0.30. Else REJECT (no escalation), as item 3.

OUTCOME (2026-05-29, FY2018-2025, 401 baseline trades): REJECT, no escalation — the predicted failure
mode. ALL 6 variants LOSE Sharpe (Δ −0.07..−0.22) AND CAGR (−1.5..−5.4pp) with WHIPSAW ~45-53% (a coin
flip — half the stops cut names that recovered, exactly the PEAD-sleeve prior), and maxDD mostly WORSE
(confirms the caveat: the −22% DD is beta-driven, an alpha stop cannot touch it). Mechanism (per-FY): the
stops crater FY2019 — the sole sustained bear (base 0.64... wait FY2019 base −0.09 → −0.98/−1.15) — and
hurt the post-COVID FY2020 recovery (2.10 → 0.79), i.e. they churn breakout pullbacks that recover.
Lighter stops (lvl12, 34 fires) approach no-op (Δ −0.07) but still whip 53% / lose return; heavier stops
churn more. No operating point helps. CLOSES the per-stock-β-stripped-alpha exit cell → the exit 2×2
{per-stock,market-regime}×{raw-price,alpha} is now FULLY settled (only conditional-EV sizing, item 2,
survives the whole backlog). See backlog item 7 + confluence_alpha_stop_preregistration.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_alpha_stop_probe
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_benchmark as cb
import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.portfolio.sizing import position_weight, recommended_lots
from src.simulator.cache import DataCache

_BETA_WIN = 60
_MIN_PAIRS = 40
_LEVELS = (0.05, 0.08, 0.12)     # α_cum ≤ −θ
_TRAILS = (0.05, 0.08, 0.12)     # α_cum ≤ running-peak − X
_VARIANTS = [f"lvl{int(t*100):02d}" for t in _LEVELS] + [f"trl{int(x*100):02d}" for x in _TRAILS]


def _bw_daily(results, stock_dts, cal):
    cal_set = set(cal)
    day: defaultdict[datetime.date, float] = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        lots = recommended_lots(cb._BUDGET, float(p.entry_price), cb._SLOTS)
        w = position_weight(lots, float(p.entry_price), cb._BUDGET)
        for d, r in cb._pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day[d] += r * w
    return [day.get(d, 0.0) for d in cal]


def _beta(sdts, scmap, n_cmap, entry_date):
    """Trailing 60-bar daily-return β of stock on ^N225, strictly before entry. None if too thin."""
    prior = [d for d in sdts if d < entry_date][-(_BETA_WIN + 1):]
    sr, mr = [], []
    for i in range(1, len(prior)):
        d0, d1 = prior[i - 1], prior[i]
        s0, s1 = scmap.get(d0), scmap.get(d1)
        m0, m1 = n_cmap.get(d0), n_cmap.get(d1)
        if s0 and s1 and m0 and m1:
            sr.append(s1 / s0 - 1.0)
            mr.append(m1 / m0 - 1.0)
    if len(sr) < _MIN_PAIRS:
        return None
    s = np.array(sr); m = np.array(mr)
    vm = float(np.var(m))
    if vm <= 0:
        return None
    return float(np.cov(s, m, bias=True)[0, 1] / vm)


def _alpha_path(r, sdts, scmap, n_cmap, beta):
    """[(date, α_cum)] over the hold (entry, baseline_exit], anchored at entry-date close."""
    try:
        ie, ix = sdts.index(r.entry_date), sdts.index(r.exit_date)
    except ValueError:
        return []
    s0, m0 = scmap.get(r.entry_date), n_cmap.get(r.entry_date)
    if not s0 or not m0:
        return []
    out = []
    for d in sdts[ie + 1:ix + 1]:
        sk, mk = scmap.get(d), n_cmap.get(d)
        if sk and mk:
            out.append((d, (sk / s0 - 1.0) - beta * (mk / m0 - 1.0)))
    return out


def _apply_stop(results, stock_dts, n_cmap, betas, variant):
    """Override exits with the alpha-stop `variant`. Returns (new_results, n_stops, whip, helped)."""
    is_trail = variant.startswith("trl")
    thr = int(variant[3:]) / 100.0
    out, n_stops, whip, helped = [], 0, 0, 0
    for r in results:
        sdts, scmap = stock_dts.get(r.stock_code, ([], {}))
        b = betas.get((r.stock_code, r.entry_date))
        if b is None or not sdts:
            out.append(r); continue
        path = _alpha_path(r, sdts, scmap, n_cmap, b)
        if not path:
            out.append(r); continue
        a_base = path[-1][1]                     # α_cum at the baseline exit date
        hit = None
        peak = -1e9
        for d, a in path[:-1]:                   # cannot pre-empt the final bar (= baseline exit)
            peak = max(peak, a)
            trig = (a <= peak - thr) if is_trail else (a <= -thr)
            if trig:
                hit = (d, a); break
        if hit is None:
            out.append(r); continue
        hd, a_hit = hit
        n_stops += 1
        if a_base > a_hit + 1e-12:
            whip += 1                            # name recovered after the stop → premature
        elif a_base < a_hit - 1e-12:
            helped += 1
        out.append(r._replace(exit_date=hd, exit_price=scmap[hd]))
    return out, n_stops, whip, helped


def _stats(rets):
    sh, tot, dd = cb._book(rets)
    n = len(rets)
    cagr = (1.0 + tot) ** (252.0 / n) - 1.0 if n > 1 and (1.0 + tot) > 0 else float("nan")
    return sh, cagr, tot, dd


def run() -> None:
    cbt._VALID_BARS = dict(cb._BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(cb._BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    stitched = {k: [] for k in (["base"] + _VARIANTS)}
    per_fy = defaultdict(dict)
    stop_tot = defaultdict(int); whip_tot = defaultdict(int); help_tot = defaultdict(int)
    base_tot = 0

    for cfg in cb._FYS:
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
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        n_dts, n_cmap = cb._closes(n225)
        stock_dts = {code: cb._closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        cands = []
        for code in caches:
            cands += cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code], corr_maps[code], zs_maps[code],
                cfg.start, cfg.end, cb._N_GATE)
        cands.sort(key=lambda c: c.entry_date)

        def _affordable(c) -> bool:
            _, cmap = stock_dts.get(c.stock_code, ([], {}))
            px = cmap.get(c.entry_date)
            return px is not None and recommended_lots(cb._BUDGET, float(px), cb._SLOTS) > 0
        cands_aff = [c for c in cands if _affordable(c)]

        res_base = run_simulation(cands_aff, cbt._EXIT_RULE, caches, cfg.end)
        base_tot += len(res_base)
        # per-trade β (cached by (code, entry_date))
        betas = {}
        for r in res_base:
            sdts, scmap = stock_dts.get(r.stock_code, ([], {}))
            betas[(r.stock_code, r.entry_date)] = _beta(sdts, scmap, n_cmap, r.entry_date) if sdts else None

        rb = _bw_daily(res_base, stock_dts, cal)
        stitched["base"] += rb[1:]
        per_fy[cfg.label]["base"] = _stats(rb)
        for v in _VARIANTS:
            res_v, ns, wh, hp = _apply_stop(res_base, stock_dts, n_cmap, betas, v)
            stop_tot[v] += ns; whip_tot[v] += wh; help_tot[v] += hp
            rv = _bw_daily(res_v, stock_dts, cal)
            stitched[v] += rv[1:]
            per_fy[cfg.label][v] = _stats(rv)
        logger.info("  {} done ({} trades, {} with β)", cfg.label, len(res_base),
                    sum(1 for x in betas.values() if x is not None))

    bsh, bcagr, btot, bdd = _stats(stitched["base"])
    print("\n" + "=" * 100)
    print("CONFLUENCE PER-STOCK β-STRIPPED ALPHA STOP — discovery (capital-aware ¥2M 6-slot, FY2018-2025)")
    print("=" * 100)
    print("  exit = earlier of production ZsTpSl OR first alpha-stop bar (post-hoc, same entries, "
          "freed slots NOT re-filled)")
    print(f"\n  {'variant':<10}{'Sharpe':>8}{'CAGR':>8}{'maxDD':>8}{'total':>9}"
          f"{'nStop':>7}{'whip%':>7}{'help%':>7}  ΔSharpe  ΔCAGR")
    print(f"  {'baseline':<10}{bsh:>8.2f}{bcagr*100:>7.1f}%{bdd*100:>7.1f}%{btot*100:>8.1f}%"
          f"{'—':>7}{'—':>7}{'—':>7}")
    rows_for_gate = {}
    for v in _VARIANTS:
        sh, cagr, tot, dd = _stats(stitched[v])
        ns = stop_tot[v]
        whp = 100.0 * whip_tot[v] / ns if ns else float("nan")
        hlp = 100.0 * help_tot[v] / ns if ns else float("nan")
        rows_for_gate[v] = (sh, cagr, whp)
        print(f"  {v:<10}{sh:>8.2f}{cagr*100:>7.1f}%{dd*100:>7.1f}%{tot*100:>8.1f}%"
              f"{ns:>7}{whp:>6.0f}%{hlp:>6.0f}%  {sh-bsh:>+6.2f}  {(cagr-bcagr)*100:>+5.1f}pp")

    print(f"\nPER-FY Sharpe (watch FY2024 = regime-inverse canary):")
    hdr = "  " + f"{'FY':<8}" + "".join(f"{v:>8}" for v in (["base"] + _VARIANTS))
    print(hdr)
    for cfg in cb._FYS:
        if cfg.label not in per_fy:
            continue
        row = f"  {cfg.label:<8}"
        for v in (["base"] + _VARIANTS):
            row += f"{per_fy[cfg.label][v][0]:>8.2f}"
        print(row + ("  ← regime-inverse" if cfg.label == "FY2024" else ""))

    # ---- frozen discovery gate
    base_fy2024 = per_fy.get("FY2024", {}).get("base", (float("nan"),))[0]
    escalate = []
    for v in _VARIANTS:
        sh, cagr, whp = rows_for_gate[v]
        ret_ok = (sh >= bsh + 0.05) or (cagr >= bcagr + 0.01)
        whip_ok = whp < 50.0
        fy24 = per_fy.get("FY2024", {}).get(v, (float("nan"),))[0]
        fy24_ok = (np.isnan(base_fy2024) or fy24 >= base_fy2024 - 0.30)
        if ret_ok and whip_ok and fy24_ok:
            escalate.append(v)

    print("\n" + "-" * 100)
    print("DISCOVERY GATE: escalate iff (Sharpe≥base+0.05 OR CAGR≥base+1pp) AND whip%<50 AND "
          "FY2024 Sharpe≥base−0.30")
    if escalate:
        print(f"  ESCALATE — variant(s) {escalate} clear the discovery gate → run the paired fill-order "
              "null on the 6-slot book (return-judged).")
    else:
        print("  REJECT (no escalation) — no alpha-stop variant lifts return without churning recoveries "
              "(whip%) / gutting FY2024. The β-stripped per-stock exit cell is closed for confluence; the "
              "exit 2×2 is fully settled. Matches the PEAD-sleeve alpha-stop prior (transient alpha dips "
              "recover). The −22% drawdown is beta-driven and an alpha stop cannot touch it.")
    print("  (Binding on escalation: paired fill-order null + the whip% check above.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
