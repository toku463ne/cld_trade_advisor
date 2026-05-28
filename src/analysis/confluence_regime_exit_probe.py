"""Confluence regime-conditional EXIT — drawdown-reduction probe (read-only, discovery).

Backlog item 3 (docs/analysis/confluence_improvement_backlog.md), reframed by the oracle-ceiling result:
the strategy's drawdown is almost entirely EXIT-driven (perfect exit collapses maxDD −22%→−6%). The exit
A/B reject judged exit swaps on Sharpe (coin-flip) and never tested a LIVE regime-conditional exit for
DRAWDOWN. Hypothesis: exiting the (long, β≈0.7) book when the MARKET turns bear caps the market-driven
drawdown. Value proposition = maxDD reduction, NOT return.

Test (post-hoc on the baseline's filled trades — conservative: exits ≤ baseline date, freed slots are NOT
re-filled): each trade exits at the EARLIER of its production ZsTpSl exit or the first bar after fill on
which N225 is in a bear regime. Sweep the bear trigger. Judge maxDD reduction vs the Sharpe/return cost,
per-FY — WATCHING FY2024 (confluence alpha is regime-INVERSE; the TSMOM ENTRY-gate was rejected because
de-risking in bear skipped its best entries — an exit-on-bear could similarly cut into the bear-regime
recovery). A favorable result (materially smaller maxDD, Sharpe ≈ baseline, FY2024 not gutted) escalates
to a paired fill-order null on the 6-slot book WITH A HELD-OUT BULL FY (per the exit-reject's re-open
condition). DISCOVERY ONLY. Read-only.
  PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_regime_exit_probe
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


def _bear_sets(n_dts, n_cmap):
    """Build {trigger_name: set(dates flagged bear)} from the N225 close series."""
    dts = n_dts
    px = np.array([n_cmap[d] for d in dts])
    n = len(dts)
    sma50 = np.full(n, np.nan); sma100 = np.full(n, np.nan)
    mom60 = np.full(n, np.nan); dd60 = np.full(n, np.nan)
    for i in range(n):
        if i >= 49:
            sma50[i] = px[i - 49:i + 1].mean()
        if i >= 99:
            sma100[i] = px[i - 99:i + 1].mean()
        if i >= 60:
            mom60[i] = px[i] / px[i - 60] - 1.0
            dd60[i] = px[i] / px[i - 60:i + 1].max() - 1.0
    out = {
        "sma50":    {dts[i] for i in range(n) if not np.isnan(sma50[i]) and px[i] < sma50[i]},
        "sma100":   {dts[i] for i in range(n) if not np.isnan(sma100[i]) and px[i] < sma100[i]},
        "mom60neg": {dts[i] for i in range(n) if not np.isnan(mom60[i]) and mom60[i] < 0},
        "dd5":      {dts[i] for i in range(n) if not np.isnan(dd60[i]) and dd60[i] < -0.05},
        "dd10":     {dts[i] for i in range(n) if not np.isnan(dd60[i]) and dd60[i] < -0.10},
    }
    return out


def _regime_stop(results, stock_dts, bear_set):
    """Exit each trade at the first stock trading day in (fill, baseline_exit] that is N225-bear."""
    out, cut, holdred = [], 0, []
    for r in results:
        dts, cmap = stock_dts.get(r.stock_code, ([], {}))
        if not dts or r.entry_date not in cmap:
            out.append(r); continue
        ie = dts.index(r.entry_date)
        try:
            ix = dts.index(r.exit_date)
        except ValueError:
            out.append(r); continue
        hit = None
        for d in dts[ie + 1:ix + 1]:
            if d in bear_set:
                hit = d; break
        if hit is not None and hit < r.exit_date:
            cut += 1
            holdred.append((ix - dts.index(hit)))
            out.append(r._replace(exit_date=hit, exit_price=cmap[hit]))
        else:
            out.append(r)
    return out, cut, (float(np.mean(holdred)) if holdred else 0.0)


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

    triggers = ["sma50", "sma100", "mom60neg", "dd5", "dd10"]
    stitched = {k: [] for k in (["base"] + triggers)}
    per_fy = defaultdict(dict)   # fy -> {variant: (sh,cagr,tot,dd)}
    cut_tot = defaultdict(int)
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
        bears = _bear_sets(n_dts, n_cmap)

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
        rb = _bw_daily(res_base, stock_dts, cal)
        stitched["base"] += rb[1:]
        per_fy[cfg.label]["base"] = _stats(rb)
        for trg in triggers:
            res_t, cut, _hr = _regime_stop(res_base, stock_dts, bears[trg])
            cut_tot[trg] += cut
            rt = _bw_daily(res_t, stock_dts, cal)
            stitched[trg] += rt[1:]
            per_fy[cfg.label][trg] = _stats(rt)
        logger.info("  {} done ({} trades)", cfg.label, len(res_base))

    print("\n" + "=" * 96)
    print("CONFLUENCE REGIME-CONDITIONAL EXIT — drawdown probe (capital-aware ¥2M 6-slot, FY2018–2025)")
    print("=" * 96)
    print("  exit = earlier of production ZsTpSl OR first N225-bear bar after fill (post-hoc, same entries)")
    bsh, bcagr, btot, bdd = _stats(stitched["base"])
    print(f"\n  {'variant':<12}{'Sharpe':>8}{'CAGR':>8}{'maxDD':>8}{'total':>9}{'%cut':>7}"
          f"  ΔSharpe  ΔmaxDD")
    print(f"  {'baseline':<12}{bsh:>8.2f}{bcagr * 100:>7.1f}%{bdd * 100:>7.1f}%{btot * 100:>8.1f}%"
          f"{'—':>7}")
    for trg in triggers:
        sh, cagr, tot, dd = _stats(stitched[trg])
        pct = 100.0 * cut_tot[trg] / max(base_tot, 1)
        print(f"  {trg:<12}{sh:>8.2f}{cagr * 100:>7.1f}%{dd * 100:>7.1f}%{tot * 100:>8.1f}%"
              f"{pct:>6.0f}%  {sh - bsh:>+6.2f}  {(dd - bdd) * 100:>+6.1f}pp")

    print(f"\nPER-FY maxDD (baseline vs each trigger) — watch FY2024 (regime-inverse alpha):")
    hdr = "  " + f"{'FY':<8}" + "".join(f"{t:>9}" for t in (["base"] + triggers))
    print(hdr)
    for cfg in cb._FYS:
        if cfg.label not in per_fy:
            continue
        row = f"  {cfg.label:<8}"
        for v in (["base"] + triggers):
            dd = per_fy[cfg.label][v][3]
            row += f"{dd * 100:>8.0f}%"
        tag = "  ← regime-inverse" if cfg.label == "FY2024" else ""
        print(row + tag)

    print(f"\nPER-FY Sharpe (return cost check):")
    print(hdr)
    for cfg in cb._FYS:
        if cfg.label not in per_fy:
            continue
        row = f"  {cfg.label:<8}"
        for v in (["base"] + triggers):
            row += f"{per_fy[cfg.label][v][0]:>9.2f}"
        tag = "  ← regime-inverse" if cfg.label == "FY2024" else ""
        print(row + tag)

    print("\nHOW TO READ:")
    print("• Value proposition is DRAWDOWN: look for a trigger that materially shrinks stitched maxDD\n"
          "  (−21.8% baseline) while keeping ΔSharpe ≈ 0 (small return cost) AND not gutting FY2024 (the\n"
          "  regime-inverse year — if a trigger blows up FY2024's drawdown/Sharpe it is cutting into the\n"
          "  bear-regime recovery, the same trap that sank the TSMOM ENTRY gate).\n"
          "• Post-hoc, same entries, freed slots NOT re-filled → conservative on return (the in-sim\n"
          "  version would re-deploy into the recovery, where confluence's regime-inverse alpha lives,\n"
          "  possibly recovering some cost). A favorable trigger escalates to the paired fill-order null\n"
          "  on the 6-slot book WITH A HELD-OUT BULL FY, judged on maxDD/CDaR. DISCOVERY ONLY.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
