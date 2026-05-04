"""Strategy trainer: grid search or genetic algorithm over parameter space.

CLI usage
---------
    # Single stock, grid search:
    uv run --env-file devenv python -m src.backtest.trainer \\
        --strategy sma_breakout --code 7203.T --start 2022-01-01

    # Multiple stocks, GA (parameters optimised across all stocks):
    uv run --env-file devenv python -m src.backtest.trainer \\
        --trainer ga --strategy sma_breakout \\
        --code 1716.T 1720.T 1973.T --start 2022-01-01 \\
        --ga-pop 60 --ga-gen 40 --ga-score-agg mean
"""

from __future__ import annotations

import argparse
import datetime
import sys
from dataclasses import dataclass
from typing import Callable, Generic, Sequence, TypeVar

from loguru import logger

from src.backtest.metrics import BacktestMetrics, compute_metrics
from src.backtest.runner import BacktestResult, run_backtest
from src.simulator.cache import DataCache
from src.simulator.simulator import TradeSimulator
from src.strategy.base import Strategy

P = TypeVar("P")


@dataclass
class TrainResult(Generic[P]):
    """Metrics for one parameter combination (possibly aggregated across stocks)."""

    params: P
    metrics: BacktestMetrics
    result: BacktestResult


def _aggregate_results(
    per_stock: list[TrainResult[P]],
    score_agg: str = "mean",
) -> TrainResult[P]:
    """Merge per-stock TrainResults into one result with averaged metrics.

    The ``score`` field uses *score_agg* (mean / min / median).
    All other metric fields are simple averages.
    ``total_trades`` is the sum across stocks.
    The first stock's ``BacktestResult`` (equity curve, trades) is kept for
    display purposes.
    """
    if len(per_stock) == 1:
        return per_stock[0]

    scores = [r.metrics.score for r in per_stock]
    if score_agg == "min":
        agg_score: float = min(scores)
    elif score_agg == "median":
        srt = sorted(scores)
        n = len(srt)
        agg_score = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2.0
    else:
        agg_score = sum(scores) / len(scores)

    def _avg(fn: Callable[[TrainResult[P]], float]) -> float:
        return sum(fn(r) for r in per_stock) / len(per_stock)

    agg_metrics = BacktestMetrics(
        total_return_pct=_avg(lambda r: r.metrics.total_return_pct),
        annualized_return_pct=_avg(lambda r: r.metrics.annualized_return_pct),
        sharpe_ratio=_avg(lambda r: r.metrics.sharpe_ratio),
        max_drawdown_pct=_avg(lambda r: r.metrics.max_drawdown_pct),
        win_rate_pct=_avg(lambda r: r.metrics.win_rate_pct),
        profit_factor=_avg(lambda r: r.metrics.profit_factor),
        total_trades=int(sum(r.metrics.total_trades for r in per_stock)),
        avg_holding_days=_avg(lambda r: r.metrics.avg_holding_days),
        score=agg_score,
    )
    return TrainResult(
        params=per_stock[0].params,
        metrics=agg_metrics,
        result=per_stock[0].result,
    )


class GridSearchTrainer(Generic[P]):
    """Evaluate every combination in *param_grid* and return ranked results.

    Accepts one or more DataCaches.  When multiple are given, each param set is
    evaluated on all stocks and the metrics are averaged (see *score_agg*).
    """

    def train(
        self,
        caches: DataCache | list[DataCache],
        strategy_factory: Callable[[P], Strategy],
        param_grid: Sequence[P],
        initial_capital: float = 1_000_000.0,
        score_agg: str = "mean",
    ) -> list[TrainResult[P]]:
        if not isinstance(caches, list):
            caches = [caches]

        sims = [TradeSimulator(c, initial_capital) for c in caches]
        results: list[TrainResult[P]] = []

        n = len(param_grid)
        log_every = max(1, n // 10)

        for i, params in enumerate(param_grid):
            if i % log_every == 0:
                logger.info("Training {}/{} combinations …", i, n)

            strategy = strategy_factory(params)
            per_stock: list[TrainResult[P]] = []
            for sim, cache in zip(sims, caches):
                bt_result = run_backtest(strategy, sim, cache)
                metrics = compute_metrics(bt_result, cache.gran)
                per_stock.append(TrainResult(params=params, metrics=metrics, result=bt_result))

            results.append(_aggregate_results(per_stock, score_agg))

        logger.info("Training complete — {} combinations evaluated.", n)
        return sorted(results, key=lambda r: r.metrics.score, reverse=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _build_parser(strategy_choices: list[str]) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.backtest.trainer",
        description="Train strategy parameters via grid search or genetic algorithm",
    )
    p.add_argument(
        "--config", default=None, metavar="YAML",
        help="YAML config file; CLI flags override values in the file",
    )
    p.add_argument(
        "--trainer", default="grid", choices=["grid", "ga"],
        help="Training algorithm: grid (exhaustive) or ga (genetic, default: grid)",
    )
    p.add_argument(
        "--strategy", default=strategy_choices[0], choices=strategy_choices,
        help=f"Strategy to train (default: {strategy_choices[0]})",
    )
    # Stock selection: explicit codes OR a named set from stock_codes.ini
    stock_grp = p.add_mutually_exclusive_group(required=False)
    stock_grp.add_argument(
        "--code", nargs="+", metavar="CODE", default=None,
        help="Explicit stock codes, e.g. --code 7203.T 6758.T",
    )
    stock_grp.add_argument(
        "--stock-set", default=None, metavar="SECTION",
        help="Named stock set from --stock-codes-file, e.g. --stock-set test",
    )
    p.add_argument(
        "--stock-codes-file", default="configs/stock_codes.ini", metavar="INI",
        help="Path to stock_codes.ini (default: configs/stock_codes.ini)",
    )
    p.add_argument("--granularity", default="1d",  help="Bar granularity (default: 1d)")
    p.add_argument("--start",       default=None,  help="Start date YYYY-MM-DD")
    p.add_argument("--end",         default=None,  help="End date (default: today)")
    p.add_argument("--capital",     type=float, default=1_000_000.0,
                   help="Initial capital per stock (default: 1,000,000)")
    p.add_argument("--units",       type=int, default=100,
                   help="Shares per trade (default: 100)")
    p.add_argument("--top",         type=int, default=20,
                   help="Number of top results saved / shown (default: 20)")
    p.add_argument(
        "--score-agg", default="mean", choices=["mean", "min", "median"],
        help="How to aggregate scores across stocks (default: mean)",
    )
    # GA-specific flags (ignored when --trainer grid)
    g = p.add_argument_group("GA options (only used with --trainer ga)")
    g.add_argument("--ga-pop",            type=int,   default=60,
                   help="Population size (default: 60)")
    g.add_argument("--ga-gen",            type=int,   default=40,
                   help="Number of generations (default: 40)")
    g.add_argument("--ga-elite",          type=float, default=0.15,
                   help="Elite fraction kept unchanged (default: 0.15)")
    g.add_argument("--ga-crossover-prob", type=float, default=0.80,
                   help="Crossover probability (default: 0.80)")
    g.add_argument("--ga-mutation-rate",  type=float, default=0.30,
                   help="Per-gene mutation probability (default: 0.30)")
    g.add_argument("--ga-mutation-sigma",      type=float, default=0.15,
                   help="Mutation step = sigma × param_range (default: 0.15)")
    g.add_argument("--ga-stagnation-patience", type=int,   default=5,
                   help="Reinit non-elite if no improvement for N gens; 0=disabled (default: 5)")
    g.add_argument("--ga-seed",                type=int,   default=None,
                   help="RNG seed for reproducibility (default: None)")
    return p


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    from src.strategy.registry import all_cli_names, get_by_cli_name

    parser = _build_parser(all_cli_names())

    # ── Apply YAML defaults before full parse (CLI flags override YAML) ──
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--config", default=None)
    _pre_args, _ = _pre.parse_known_args(argv)
    if _pre_args.config:
        from src.config import load_yaml, trainer_defaults
        cfg = load_yaml(_pre_args.config)
        parser.set_defaults(**trainer_defaults(cfg))

    args = parser.parse_args(argv)
    plugin = get_by_cli_name(args.strategy)

    # ── Resolve stock codes ───────────────────────────────────────────────
    if args.code:
        codes: list[str] = args.code
    elif args.stock_set:
        from src.config import load_stock_codes
        codes = load_stock_codes(args.stock_codes_file, args.stock_set)
        logger.info("Loaded {} codes from [{}] in {}", len(codes), args.stock_set, args.stock_codes_file)
    else:
        parser.error("Provide --code, --stock-set, or set stock_set in a --config file.")
        return  # unreachable; satisfies type checker

    if not args.start:
        parser.error("--start is required (or set data.start in the config file).")

    start = _parse_dt(args.start)
    end   = _parse_dt(args.end) if args.end else datetime.datetime.now(datetime.timezone.utc)

    from src.data.collect import OHLCVCollector
    from src.data.db import get_session

    with get_session() as session:
        logger.info(
            "Ensuring data for {} stock(s), {} {} → {}",
            len(codes), args.granularity, start.date(), end.date(),
        )
        collector = OHLCVCollector(session)
        for code in codes:
            collector.collect(code, args.granularity, start, end)

        logger.info("Loading caches …")
        caches: list[DataCache] = []
        for code in codes:
            cache = DataCache(code, args.granularity)
            cache.load(session, start, end)
            if not cache.bars:
                logger.warning("No data for {} {} — skipping.", code, args.granularity)
                continue
            plugin.setup_cache(cache, args.units)
            caches.append(cache)
            logger.info("  {} → {} bars", code, len(cache.bars))

        if not caches:
            logger.error("No data found for any of the specified codes.")
            sys.exit(1)

        if args.trainer == "ga":
            from src.backtest.ga_trainer import GAConfig, GATrainer
            ga_cfg = GAConfig(
                population_size=args.ga_pop,
                generations=args.ga_gen,
                elite_fraction=args.ga_elite,
                crossover_prob=args.ga_crossover_prob,
                mutation_rate=args.ga_mutation_rate,
                mutation_sigma=args.ga_mutation_sigma,
                stagnation_patience=args.ga_stagnation_patience,
                score_agg=args.score_agg,
                seed=args.ga_seed,
            )
            logger.info(
                "GA trainer: pop={} gen={} elite={:.0%} stagnation_patience={} stocks={} score_agg={}",
                ga_cfg.population_size, ga_cfg.generations, ga_cfg.elite_fraction,
                ga_cfg.stagnation_patience, len(caches), ga_cfg.score_agg,
            )
            results = GATrainer(ga_cfg).train(
                caches, plugin, units=args.units, initial_capital=args.capital
            )
        else:
            grid = plugin.make_grid(args.units)
            logger.info(
                "Grid search: {} combinations × {} stock(s)", len(grid), len(caches)
            )
            results = GridSearchTrainer().train(
                caches, plugin.make_strategy, grid,
                initial_capital=args.capital, score_agg=args.score_agg,
            )

    # ── Report ────────────────────────────────────────────────────────────
    stock_label = ",".join(codes)
    report_path = plugin.generate_report(
        stock_code=stock_label, gran=args.granularity,
        start=start, end=end, results=results, top_n=args.top,
    )
    logger.info("Report written to {}", report_path)

    # ── Save to DB ────────────────────────────────────────────────────────
    from src.backtest.train_models import save_best_to_db

    with get_session() as session:
        run_id = save_best_to_db(
            session,
            strategy_name=plugin.name,
            stock_code=stock_label,
            granularity=args.granularity,
            start_dt=start,
            end_dt=end,
            results=results,
            top_n=args.top,
            initial_capital=args.capital,
            config=args.config,
        )
    logger.info("Saved top {} results to DB (train_run_id={}).", args.top, run_id)


if __name__ == "__main__":
    main()
