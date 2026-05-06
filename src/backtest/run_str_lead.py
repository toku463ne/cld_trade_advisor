"""Backtest runner for StrLeadRegimeStrategy.

Runs the strategy over all representative stocks in a cluster set and
prints per-stock metrics plus an aggregate summary.

CLI:
    uv run --env-file devenv python -m src.backtest.run_str_lead \\
        --cluster-set classified2023 --start 2024-05-01 --end 2025-03-31

    # vary params:
    uv run --env-file devenv python -m src.backtest.run_str_lead \\
        --cluster-set classified2023 --start 2024-05-01 --end 2025-03-31 \\
        --hold 10 --atr-stop 1.5 --capital-pct 0.10
"""

from __future__ import annotations

import argparse
import csv
import datetime
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from loguru import logger
from sqlalchemy import select

from src.analysis.models import MovingCorr, StockClusterMember, StockClusterRun
from src.backtest.metrics import BacktestMetrics, compute_metrics
from src.backtest.runner import BacktestResult, run_backtest
from src.data.db import get_session
from src.simulator.cache import DataCache
from src.simulator.simulator import TradeSimulator
from src.strategy.str_lead_regime import StrLeadRegimeParams, StrLeadRegimeStrategy

_N225 = "^N225"


# ── Per-stock result ───────────────────────────────────────────────────────────

@dataclass
class StockResult:
    stock_code:      str
    n_trades:        int
    n_open:          int          # positions still open at end of period
    win_rate_pct:    float | None
    total_ret_pct:   float
    ann_ret_pct:     float
    sharpe:          float
    max_dd_pct:      float
    profit_factor:   float
    avg_hold_days:   float


def _run_one(
    stock_code: str,
    cache_1h: DataCache,
    n225_1h: DataCache,
    params: StrLeadRegimeParams,
    capital: float,
    corr_n225_1h: dict[datetime.date, float] | None = None,
    allowed_dates: set[datetime.date] | None = None,
) -> StockResult | None:
    if not cache_1h.bars:
        return None
    try:
        strategy = StrLeadRegimeStrategy(
            cache_1h, n225_1h, params, corr_n225_1h, allowed_dates
        )
        sim      = TradeSimulator(cache_1h, initial_capital=capital)
        result   = run_backtest(strategy, sim, cache_1h)
        metrics  = compute_metrics(result, granularity="1h")
        # An open position at end = buy fired but no matching sell
        from src.simulator.order import OrderSide
        n_buys  = sum(1 for t in result.trades if t.side == OrderSide.BUY)
        n_sells = sum(1 for t in result.trades if t.side == OrderSide.SELL)
        n_open  = max(0, n_buys - n_sells)
        return StockResult(
            stock_code=stock_code,
            n_trades=metrics.total_trades,
            n_open=n_open,
            win_rate_pct=metrics.win_rate_pct if metrics.total_trades > 0 else None,
            total_ret_pct=metrics.total_return_pct,
            ann_ret_pct=metrics.annualized_return_pct,
            sharpe=metrics.sharpe_ratio,
            max_dd_pct=metrics.max_drawdown_pct,
            profit_factor=metrics.profit_factor,
            avg_hold_days=metrics.avg_holding_days,
        )
    except Exception as exc:
        logger.warning("  {} failed: {}", stock_code, exc)
        return None


# ── Aggregate helpers ─────────────────────────────────────────────────────────

def _fmt_pct(v: float | None, decimals: int = 1) -> str:
    return "—" if v is None else f"{v:+.{decimals}f}%"


def _fmt_f(v: float | None, decimals: int = 2) -> str:
    return "—" if v is None else f"{v:.{decimals}f}"


# ── Main run ──────────────────────────────────────────────────────────────────

def run(
    stock_codes: list[str],
    stock_set: str,
    start: datetime.datetime,
    end: datetime.datetime,
    params: StrLeadRegimeParams,
    capital: float = 1_000_000.0,
    top_k: int = 0,
    out_csv: Path | None = None,
) -> list[StockResult]:
    with get_session() as session:
        logger.info("Loading ^N225 1h cache …")
        n225 = DataCache(_N225, "1h")
        n225.load(session, start, end)

        # Load 1h rolling corr vs ^N225 (window_bars=100) for all stocks at once
        logger.info("Loading moving_corr (1h, ^N225, window=100) …")
        corr_rows = session.execute(
            select(MovingCorr.stock_code, MovingCorr.ts, MovingCorr.corr_value)
            .where(
                MovingCorr.indicator   == _N225,
                MovingCorr.granularity == "1h",
                MovingCorr.window_bars == 100,
                MovingCorr.ts          >= start,
                MovingCorr.ts          <= end,
                MovingCorr.corr_value.is_not(None),
            )
        ).fetchall()
        corr_by_stock: dict[str, dict[datetime.date, float]] = {}
        for row in corr_rows:
            corr_by_stock.setdefault(row.stock_code, {})[row.ts.date()] = float(row.corr_value)
        logger.info("  {} corr rows loaded for {} stocks", len(corr_rows), len(corr_by_stock))

        # ── Top-K pre-scan ───────────────────────────────────────────────
        # Per N225 event (confirm_date), keep only the top-K scoring stocks.
        # allowed_dates[code] = set of confirm_dates this stock is allowed to enter.
        allowed_dates_by_stock: dict[str, set[datetime.date]] | None = None
        if top_k > 0:
            from collections import defaultdict
            from src.signs.str_lead import StrLeadDetector
            logger.info("Top-K pre-scan (k={}) …", top_k)
            # {confirm_date: [(score, code), ...]}
            events_by_date: dict[datetime.date, list[tuple[float, str]]] = defaultdict(list)
            for code in stock_codes:
                cache = DataCache(code, "1h")
                cache.load(session, start, end)
                if not cache.bars:
                    continue
                det = StrLeadDetector(cache, n225, corr_by_stock.get(code))
                for _, confirm_date, score in det.fire_events:
                    if score >= params.min_score:
                        if not params.use_regime_gate or True:  # regime gate applied at runtime
                            events_by_date[confirm_date].append((score, code))

            allowed_dates_by_stock = defaultdict(set)
            total_selected = 0
            for confirm_date, candidates in sorted(events_by_date.items()):
                top = sorted(candidates, reverse=True)[:top_k]
                for score, code in top:
                    allowed_dates_by_stock[code].add(confirm_date)
                    total_selected += 1
                logger.debug(
                    "  {} → {} candidates, selected {} ({})",
                    confirm_date,
                    len(candidates),
                    len(top),
                    ", ".join(f"{c}({s:.3f})" for s, c in top),
                )
            logger.info("  {} N225 events → {} selected entries across {} stocks",
                        len(events_by_date), total_selected,
                        len(allowed_dates_by_stock))

        results: list[StockResult] = []
        for i, code in enumerate(stock_codes, 1):
            logger.debug("  [{}/{}] {}", i, len(stock_codes), code)
            cache = DataCache(code, "1h")
            cache.load(session, start, end)
            allowed = (
                allowed_dates_by_stock.get(code)
                if allowed_dates_by_stock is not None else None
            )
            # Skip stocks with no allowed entries in top-K mode
            if allowed_dates_by_stock is not None and not allowed:
                continue
            r = _run_one(code, cache, n225, params, capital,
                         corr_by_stock.get(code), allowed)
            if r is not None:
                results.append(r)

    # ── Print per-stock table ────────────────────────────────────────────
    header = f"\n{'─'*80}\n{params}\nUniverse: {stock_set}   {start.date()} – {end.date()}\n{'─'*80}"
    print(header)
    print(f"{'stock':>8}  {'trades':>6}  {'open':>4}  {'win%':>6}  {'tot%':>7}  {'ann%':>7}  {'sharpe':>6}  {'maxDD%':>7}  {'pf':>5}  {'hold_d':>6}")
    results_sorted = sorted(results, key=lambda r: r.total_ret_pct, reverse=True)
    for r in results_sorted:
        open_flag = f"+{r.n_open}" if r.n_open > 0 else "  —"
        print(
            f"{r.stock_code:>8}  "
            f"{r.n_trades:>6}  "
            f"{open_flag:>4}  "
            f"{_fmt_pct(r.win_rate_pct, 1):>6}  "
            f"{_fmt_pct(r.total_ret_pct, 2):>7}  "
            f"{_fmt_pct(r.ann_ret_pct, 2):>7}  "
            f"{_fmt_f(r.sharpe, 2):>6}  "
            f"{_fmt_pct(r.max_dd_pct, 2):>7}  "
            f"{_fmt_f(r.profit_factor, 2):>5}  "
            f"{_fmt_f(r.avg_hold_days, 1):>6}"
        )

    # ── Aggregate ────────────────────────────────────────────────────────
    traded   = [r for r in results if r.n_trades > 0]
    n_open_total = sum(r.n_open for r in results)
    print(f"\n{'─'*80}")
    print(f"AGGREGATE  ({len(results)} stocks, {len(traded)} closed ≥1 trade, {n_open_total} open at end)")
    if traded:
        all_trades  = sum(r.n_trades for r in traded)
        win_rates   = [r.win_rate_pct for r in traded if r.win_rate_pct is not None]
        tot_rets    = [r.total_ret_pct for r in results]
        ann_rets    = [r.ann_ret_pct for r in results]
        sharpes     = [r.sharpe for r in results if not (r.sharpe != r.sharpe)]  # drop NaN
        profitable  = sum(1 for r in results if r.total_ret_pct > 0)
        hold_days   = [r.avg_hold_days for r in traded if r.avg_hold_days > 0]

        print(f"  Total trades:        {all_trades}")
        print(f"  Stocks with profit:  {profitable} / {len(results)}  ({profitable/len(results):.1%})")
        print(f"  Avg win rate:        {np.mean(win_rates):+.1f}%")
        print(f"  Mean total return:   {np.mean(tot_rets):+.2f}%")
        print(f"  Mean annual return:  {np.mean(ann_rets):+.2f}%")
        print(f"  Mean Sharpe:         {np.mean(sharpes):.2f}")
        print(f"  Mean hold (days):    {np.mean(hold_days):.1f}")
        dd_vals = [r.max_dd_pct for r in results]
        print(f"  Mean max drawdown:   {np.mean(dd_vals):+.2f}%")
    print(f"{'─'*80}\n")

    # ── CSV export ───────────────────────────────────────────────────────
    if out_csv:
        with open(out_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["stock_code", "n_trades", "n_open", "win_rate_pct",
                        "total_ret_pct", "ann_ret_pct", "sharpe",
                        "max_dd_pct", "profit_factor", "avg_hold_days"])
            for r in results_sorted:
                w.writerow([
                    r.stock_code, r.n_trades, r.n_open,
                    f"{r.win_rate_pct:.1f}" if r.win_rate_pct is not None else "",
                    f"{r.total_ret_pct:.3f}", f"{r.ann_ret_pct:.3f}",
                    f"{r.sharpe:.3f}", f"{r.max_dd_pct:.3f}",
                    f"{r.profit_factor:.3f}", f"{r.avg_hold_days:.1f}",
                ])
        logger.info("CSV saved → {}", out_csv)

    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(prog="python -m src.backtest.run_str_lead")
    p.add_argument("--cluster-set", required=True, metavar="LABEL")
    p.add_argument("--start",       required=True)
    p.add_argument("--end",         required=True)
    p.add_argument("--hold",        type=int,   default=10,  metavar="DAYS",
                   help="Time-stop in trading days (default 10)")
    p.add_argument("--atr-stop",    type=float, default=1.5, metavar="MULT",
                   help="Daily ATR14 multiplier for hard stop (default 1.5)")
    p.add_argument("--min-hold-bars", type=int, default=8, metavar="BARS",
                   help="Min hourly bars before zigzag exit triggers (default 8 = ~1 td)")
    p.add_argument("--daily-zz-exit", action="store_true",
                   help="Use daily zigzag HIGH for exit instead of hourly")
    p.add_argument("--regime-gate", action="store_true", default=False,
                   help="Enable N225>SMA20 regime filter (default off)")
    p.add_argument("--min-score",   type=float, default=0.0, metavar="SCORE",
                   help="Minimum sign score to enter a position (default 0 = no filter)")
    p.add_argument("--top-k",       type=int,   default=0, metavar="K",
                   help="Per N225 event, enter only the top-K scoring stocks (0 = all)")
    p.add_argument("--capital-pct", type=float, default=0.10, metavar="FRAC",
                   help="Fraction of equity per trade (default 0.10 = 10pct)")
    p.add_argument("--units",       type=int,   default=0,
                   help="Fixed share count (overrides --capital-pct when >0)")
    p.add_argument("--capital",     type=float, default=1_000_000.0)
    p.add_argument("--out-csv",     metavar="PATH")
    args = p.parse_args(argv)

    with get_session() as session:
        cluster_run = session.execute(
            select(StockClusterRun)
            .where(StockClusterRun.fiscal_year == args.cluster_set)
        ).scalar_one_or_none()
        if cluster_run is None:
            raise SystemExit(f"No StockClusterRun for {args.cluster_set!r}")
        codes = list(session.execute(
            select(StockClusterMember.stock_code)
            .where(StockClusterMember.run_id == cluster_run.id,
                   StockClusterMember.is_representative.is_(True))
        ).scalars().all())
    logger.info("Loaded {} stocks from [{}]", len(codes), args.cluster_set)

    params = StrLeadRegimeParams(
        max_hold_days=args.hold,
        atr_stop_mult=args.atr_stop,
        min_hold_bars=args.min_hold_bars,
        daily_zz_exit=args.daily_zz_exit,
        use_regime_gate=args.regime_gate,
        capital_pct=args.capital_pct,
        units=args.units,
        min_score=args.min_score,
    )
    run(
        stock_codes=codes,
        stock_set=args.cluster_set,
        start=_parse_dt(args.start),
        end=_parse_dt(args.end),
        params=params,
        capital=args.capital,
        top_k=args.top_k,
        out_csv=Path(args.out_csv) if args.out_csv else None,
    )


if __name__ == "__main__":
    main()
