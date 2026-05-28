"""ConfluenceSignStrategy × TSMOM entry filter — discovery probe (read-only, advisory).

Idea (operator): gate ConfluenceSignStrategy ENTRIES by the index TSMOM signal — only open a new
position when the TOPIX trailing-12-month return > 0 (TSMOM "on"); when "off", skip new entries
(open positions run to their normal exits — this is an ENTRY filter, not a forced de-risk). The
hope: cut the book's exposure in sustained downtrends → lower drawdown / higher Sharpe.

STRONG PRIOR AGAINST (must be reported honestly — this is why a clean A/B is warranted):
  • [[project-confluence-fy-attribution]]: "go cash when N225 weak" REFUTED — FY2024 N225 −10.5%
    (worst tape) yet book +1.18% / alpha +1.97%. Down market ≠ bad year.
  • [[project-confluence-market-neutral]]: confluence alpha is REGIME-INVERSE — biggest raw years
    evaporate, bearish FY2024 IMPROVES. CLAUDE.md caveat: per-fire sign EV is anti-correlated with
    N225 trend → "skip longs when index bearish" is the WRONG direction.
  A TSMOM-off gate skips entries in exactly the regime where confluence's alpha is best. The two
  priors collide (TSMOM cuts sustained-bear drawdown; confluence WANTS bear entries) → test it.

Method mirrors confluence_benchmark's capital-aware 6-slot budget book (¥2M, 100-sh lots). Baseline
= all affordable candidates; gated = affordable candidates whose entry_date is TSMOM-on. Same exit
rule, same slots. TSMOM signal = TOPIX (jq_topix) 252-trading-day trailing return > 0. Read-only.
  PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_tsmom_gate_probe
"""
from __future__ import annotations

import bisect
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

_LB = 252            # 12-month trailing-return lookback (trading days)


def _load_tsmom():
    """TOPIX 252-bar trailing-return sign per date. Returns (sorted_dates, {date: on_bool})."""
    from src.data.jquants_models import JqTopix
    with get_session() as s:
        rows = s.execute(select(JqTopix.date, JqTopix.close)
                         .where(JqTopix.close.isnot(None)).order_by(JqTopix.date)).all()
    dates = [d for d, _ in rows]
    closes = np.array([float(c) for _, c in rows])
    on = {}
    for i, d in enumerate(dates):
        on[d] = bool(closes[i] > closes[i - _LB]) if i >= _LB else True   # undefined early → allow
    return dates, on


def _tsmom_on(dates, on_map, d) -> bool:
    i = bisect.bisect_right(dates, d) - 1
    return on_map[dates[i]] if i >= 0 else True


def _bw_daily(results, stock_dts, cal):
    """Budget-constrained (¥2M, deployed-capital weight) daily returns over cal."""
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

    tdates, ton = _load_tsmom()
    logger.info("TSMOM signal: TOPIX {}..{}, on-fraction overall {:.0%}",
                tdates[0], tdates[-1], np.mean([ton[d] for d in tdates]))

    per_fy = {}
    st_base: list[float] = []
    st_gate: list[float] = []

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
        n_dts, _ = cb._closes(n225)
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
        cands_gate = [c for c in cands_aff if _tsmom_on(tdates, ton, c.entry_date)]

        res_base = run_simulation(cands_aff, cbt._EXIT_RULE, caches, cfg.end)
        res_gate = run_simulation(cands_gate, cbt._EXIT_RULE, caches, cfg.end)

        rets_base = _bw_daily(res_base, stock_dts, cal)
        rets_gate = _bw_daily(res_gate, stock_dts, cal)
        on_frac = float(np.mean([_tsmom_on(tdates, ton, d) for d in cal])) if cal else float("nan")
        skip = 1.0 - (len(cands_gate) / len(cands_aff)) if cands_aff else 0.0
        per_fy[cfg.label] = (cb._book(rets_base), cb._book(rets_gate),
                             len(res_base), len(res_gate), on_frac, skip)
        st_base += rets_base[1:]
        st_gate += rets_gate[1:]
        logger.info("  {} done (base {} / gated {} trades, TSMOM-on {:.0%} of days)",
                    cfg.label, len(res_base), len(res_gate), on_frac)

    print("\n" + "=" * 100)
    print("CONFLUENCE × TSMOM ENTRY FILTER — capital-aware ¥2M 6-slot book (total return)")
    print("=" * 100)
    print(f"  gate = open new entries only when TOPIX 12-mo (252b) return > 0; "
          f"open positions run to normal exits")
    print(f"\n{'FY':<8}{'TSMOMon':>8}{'skip%':>7}  || "
          f"{'baseSh':>7}{'baseTot':>8}{'baseDD':>7}{'nBase':>6}  || "
          f"{'gateSh':>7}{'gateTot':>8}{'gateDD':>7}{'nGate':>6}  | ΔSharpe")
    helped = 0
    for cfg in cb._FYS:
        if cfg.label not in per_fy:
            continue
        (bsh, btot, bdd), (gsh, gtot, gdd), nb, ng, onf, sk = per_fy[cfg.label]
        d = gsh - bsh
        helped += d > 0
        tag = "  ← good-in-bear yr" if cfg.label == "FY2024" else ""
        print(f"{cfg.label:<8}{onf * 100:>7.0f}%{sk * 100:>6.0f}%  || "
              f"{bsh:>7.2f}{btot * 100:>7.1f}%{bdd * 100:>6.0f}%{nb:>6}  || "
              f"{gsh:>7.2f}{gtot * 100:>7.1f}%{gdd * 100:>6.0f}%{ng:>6}  | {d:>+6.2f}{tag}")

    bsh, btot, bdd = cb._book(st_base)
    gsh, gtot, gdd = cb._book(st_gate)
    print("\nSTITCHED (all FYs):")
    print(f"  baseline : Sharpe {bsh:+.2f}  total {btot * 100:+.1f}%  maxDD {bdd * 100:.1f}%  "
          f"({len(st_base)} days)")
    print(f"  TSMOM-gated: Sharpe {gsh:+.2f}  total {gtot * 100:+.1f}%  maxDD {gdd * 100:.1f}%")
    print(f"  Δ: Sharpe {gsh - bsh:+.2f}  total {(gtot - btot) * 100:+.1f}pp  "
          f"maxDD {(gdd - bdd) * 100:+.1f}pp  | per-FY gate helped {helped}/{len(per_fy)}")

    print("\nHOW TO READ:")
    print("• The gate is only worth pursuing to a paired null if it BOTH raises stitched Sharpe AND\n"
          "  cuts maxDD — and does so WITHOUT gutting FY2024 (the documented good-in-bearish year). If\n"
          "  it cuts FY2024 hard, it is the refuted 'go cash when index weak' trap: confluence alpha is\n"
          "  regime-inverse, so a 12-mo-downtrend gate skips its best entries.\n"
          "• maxDD improvement with flat/worse Sharpe = the TSMOM tail-insurance pattern (defensive,\n"
          "  not alpha). Sharpe AND maxDD both worse = the gate fights the strategy's regime edge.\n"
          "• DISCOVERY ONLY (single deterministic A/B). A favorable result escalates to the paired\n"
          "  fill-order null on the 6-slot book (the project's binding gate); per-trade/point estimates\n"
          "  do not decide. This is a TIMING gate (turns the book on/off), a different axis than the\n"
          "  fill-order-null selection rules — but the portfolio-Sharpe bar is the same.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
