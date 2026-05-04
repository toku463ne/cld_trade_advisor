"""Strategy trainer: grid search or genetic algorithm over parameter space.

CLI usage
---------
    # Grid search (exhaustive, default):
    uv run --env-file devenv python -m src.backtest.trainer \\
        --strategy sma_breakout \\
        --code 7203.T --start 2022-01-01

    # Genetic algorithm (continuous search):
    uv run --env-file devenv python -m src.backtest.trainer \\
        --trainer ga --strategy bollinger_breakout \\
        --code 7203.T --start 2022-01-01 \\
        --ga-pop 60 --ga-gen 40
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
    """Metrics for one parameter combination."""

    params: P
    metrics: BacktestMetrics
    result: BacktestResult


class GridSearchTrainer(Generic[P]):
    """Evaluate every combination in *param_grid* and return ranked results."""

    def train(
        self,
        cache: DataCache,
        strategy_factory: Callable[[P], Strategy],
        param_grid: Sequence[P],
        initial_capital: float = 1_000_000.0,
    ) -> list[TrainResult[P]]:
        """Run all combinations; return list sorted by score (best first)."""
        sim = TradeSimulator(cache, initial_capital)
        results: list[TrainResult[P]] = []

        n = len(param_grid)
        log_every = max(1, n // 10)

        for i, params in enumerate(param_grid):
            if i % log_every == 0:
                logger.info("Training {}/{} combinations …", i, n)

            strategy = strategy_factory(params)
            bt_result = run_backtest(strategy, sim, cache)
            metrics = compute_metrics(bt_result, cache.gran)
            results.append(TrainResult(params=params, metrics=metrics, result=bt_result))

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
        "--trainer", default="grid", choices=["grid", "ga"],
        help="Training algorithm: grid (exhaustive) or ga (genetic, default: grid)",
    )
    p.add_argument(
        "--strategy", default=strategy_choices[0], choices=strategy_choices,
        help=f"Strategy to train (default: {strategy_choices[0]})",
    )
    p.add_argument("--code",        required=True, help="Stock code, e.g. 7203.T")
    p.add_argument("--granularity", default="1d",  help="Bar granularity (default: 1d)")
    p.add_argument("--start",       required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end",         default=None,  help="End date (default: today)")
    p.add_argument("--capital",     type=float, default=1_000_000.0,
                   help="Initial capital (default: 1,000,000)")
    p.add_argument("--units",       type=int, default=100,
                   help="Shares per trade (default: 100)")
    p.add_argument("--top",         type=int, default=20,
                   help="Number of top results saved / shown (default: 20)")
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
    g.add_argument("--ga-mutation-sigma", type=float, default=0.15,
                   help="Mutation step = sigma × param_range (default: 0.15)")
    g.add_argument("--ga-seed",           type=int,   default=None,
                   help="RNG seed for reproducibility (default: None)")
    return p


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    from src.strategy.registry import all_cli_names, get_by_cli_name

    args = _build_parser(all_cli_names()).parse_args(argv)
    plugin = get_by_cli_name(args.strategy)

    start = _parse_dt(args.start)
    end   = _parse_dt(args.end) if args.end else datetime.datetime.now(datetime.timezone.utc)

    from src.data.db import get_session

    with get_session() as session:
        logger.info(
            "Loading {} {} {} → {}", args.code, args.granularity, start.date(), end.date()
        )
        cache = DataCache(args.code, args.granularity)
        cache.load(session, start, end)

        if not cache.bars:
            logger.error(
                "No data in DB for {} {} — run `src.data.collect` first.",
                args.code, args.granularity,
            )
            sys.exit(1)

        logger.info("Loaded {} bars.", len(cache.bars))

        plugin.setup_cache(cache, args.units)

        if args.trainer == "ga":
            from src.backtest.ga_trainer import GAConfig, GATrainer
            ga_cfg = GAConfig(
                population_size=args.ga_pop,
                generations=args.ga_gen,
                elite_fraction=args.ga_elite,
                crossover_prob=args.ga_crossover_prob,
                mutation_rate=args.ga_mutation_rate,
                mutation_sigma=args.ga_mutation_sigma,
                seed=args.ga_seed,
            )
            logger.info(
                "GA trainer: pop={} gen={} elite={:.0%}",
                ga_cfg.population_size, ga_cfg.generations, ga_cfg.elite_fraction,
            )
            results = GATrainer(ga_cfg).train(
                cache, plugin, units=args.units, initial_capital=args.capital
            )
        else:
            grid = plugin.make_grid(args.units)
            logger.info("Grid size: {} combinations.", len(grid))
            results = GridSearchTrainer().train(
                cache, plugin.make_strategy, grid, initial_capital=args.capital
            )

    # ── Report ────────────────────────────────────────────────────────────
    report_path = plugin.generate_report(
        stock_code=args.code, gran=args.granularity,
        start=start, end=end, results=results, top_n=args.top,
    )
    logger.info("Report written to {}", report_path)

    # ── Save to DB ────────────────────────────────────────────────────────
    from src.backtest.train_models import save_best_to_db

    with get_session() as session:
        run_id = save_best_to_db(
            session,
            strategy_name=plugin.name,
            stock_code=args.code,
            granularity=args.granularity,
            start_dt=start,
            end_dt=end,
            results=results,
            top_n=args.top,
            initial_capital=args.capital,
        )
    logger.info("Saved top {} results to DB (train_run_id={}).", args.top, run_id)


if __name__ == "__main__":
    main()
