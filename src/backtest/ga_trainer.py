"""Genetic-algorithm trainer.

Evolves a population of parameter chromosomes over G generations.
Fitness = ``compute_metrics(run_backtest(...)).score``.

When multiple DataCaches (stocks) are provided, each chromosome is evaluated
on all stocks and the scores are aggregated (see ``GAConfig.score_agg``).

Duplicate chromosomes (after rounding int params) are evaluated only once,
so the final result list contains only unique parameter sets.

CLI usage (via trainer.py)
--------------------------
    # Single stock:
    uv run --env-file devenv python -m src.backtest.trainer \\
        --trainer ga --strategy sma_breakout \\
        --code 7203.T --start 2022-01-01 --ga-pop 60 --ga-gen 40

    # Multiple stocks (parameters optimised across all):
    uv run --env-file devenv python -m src.backtest.trainer \\
        --trainer ga --strategy sma_breakout \\
        --code 1716.T 1720.T 1973.T --start 2022-01-01

Algorithm
---------
- Initial population : plugin.default_seeds() + random uniform fill
- Selection          : tournament (size 3)
- Crossover          : arithmetic blend  child = α·p1 + (1−α)·p2
- Mutation           : Gaussian  Δ ~ N(0, σ·range), clipped to bounds
- Elitism            : top ``elite_fraction`` of population survives unchanged
"""

from __future__ import annotations

import dataclasses
import random
from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.backtest.metrics import compute_metrics
from src.backtest.runner import run_backtest
from src.backtest.trainer import TrainResult, _aggregate_results
from src.simulator.cache import DataCache
from src.simulator.simulator import TradeSimulator
from src.strategy.base import ParamSpec, StrategyPlugin


@dataclass
class GAConfig:
    population_size: int   = 60
    generations: int       = 40
    elite_fraction: float  = 0.15   # fraction of pop that survives unchanged
    crossover_prob: float  = 0.80   # probability of crossover vs. direct clone
    mutation_rate: float   = 0.30   # per-gene mutation probability
    mutation_sigma: float  = 0.15   # step size = sigma × param_range
    tournament_size: int   = 3      # competitors in each tournament selection
    score_agg: str         = "mean" # how to aggregate scores across stocks: mean/min/median
    stagnation_patience: int = 5    # gens without improvement before reinit; 0 = disabled
    seed: int | None       = None


class GATrainer:
    """Genetic-algorithm parameter search.

    Returns ``list[TrainResult]`` sorted by score (best first), containing
    every *unique* parameter set that was evaluated across all generations.
    When multiple caches are given the score is aggregated across all stocks.
    """

    def __init__(self, config: GAConfig | None = None) -> None:
        self._cfg = config or GAConfig()

    def train(
        self,
        caches: DataCache | list[DataCache],
        plugin: StrategyPlugin[Any],
        units: int = 100,
        initial_capital: float = 1_000_000.0,
    ) -> list[TrainResult[Any]]:
        cfg = self._cfg
        rng = random.Random(cfg.seed)
        space = plugin.param_space()

        if not isinstance(caches, list):
            caches = [caches]

        sims = [TradeSimulator(c, initial_capital) for c in caches]
        seen: dict[tuple[Any, ...], TrainResult[Any]] = {}

        def _evaluate(chrom: list[float]) -> tuple[float, TrainResult[Any]]:
            params = plugin.decode_params(chrom, units)
            key = _chrom_key(params)
            if key in seen:
                return seen[key].metrics.score, seen[key]

            # Ensure required indicators exist for this params set on every cache
            for cache in caches:
                plugin.setup_cache_for_params(cache, params)

            per_stock: list[TrainResult[Any]] = []
            for sim, cache in zip(sims, caches):
                strategy = plugin.make_strategy(params)
                bt = run_backtest(strategy, sim, cache)
                metrics = compute_metrics(bt, cache.gran)
                per_stock.append(TrainResult(params=params, metrics=metrics, result=bt))

            tr = _aggregate_results(per_stock, cfg.score_agg)
            seen[key] = tr
            return tr.metrics.score, tr

        elite_k = max(1, int(cfg.population_size * cfg.elite_fraction))

        # ── Initial population: plugin seeds first, then random fill ──────
        seeds = plugin.default_seeds()
        seed_chroms = [plugin.encode_params(p) for p in seeds]
        n_random = max(0, cfg.population_size - len(seed_chroms))
        population = seed_chroms + [
            [rng.uniform(spec.low, spec.high) for spec in space]
            for _ in range(n_random)
        ]

        # ── Evolution loop ────────────────────────────────────────────────
        best_score: float = float("-inf")
        stagnation: int = 0

        for gen in range(cfg.generations):
            scored = [(chrom, *_evaluate(chrom)) for chrom in population]
            scored.sort(key=lambda x: x[1], reverse=True)

            current_best = scored[0][1]
            if current_best > best_score + 1e-9:
                best_score = current_best
                stagnation = 0
            else:
                stagnation += 1

            logger.info(
                "GA gen {}/{} | best={:.4f} | stocks={} | unique_evals={}",
                gen + 1, cfg.generations, current_best, len(caches), len(seen),
            )

            elites = [row[0] for row in scored[:elite_k]]

            if cfg.stagnation_patience > 0 and stagnation >= cfg.stagnation_patience:
                logger.info(
                    "GA stagnation restart at gen {} (no improvement for {} gens)",
                    gen + 1, stagnation,
                )
                n_reinit = cfg.population_size - elite_k
                new_pop: list[list[float]] = list(elites) + [
                    [rng.uniform(spec.low, spec.high) for spec in space]
                    for _ in range(n_reinit)
                ]
                stagnation = 0
            else:
                new_pop = list(elites)
                while len(new_pop) < cfg.population_size:
                    p1 = _tournament(scored, rng, cfg.tournament_size)
                    p2 = _tournament(scored, rng, cfg.tournament_size)
                    child = _crossover(p1, p2, rng, cfg.crossover_prob)
                    child = _mutate(child, space, rng, cfg.mutation_rate, cfg.mutation_sigma)
                    new_pop.append(child)

            population = new_pop

        # Evaluate any unevaluated chromosomes in the final population
        for chrom in population:
            _evaluate(chrom)

        logger.info("GA complete — {} unique combinations evaluated.", len(seen))
        return sorted(seen.values(), key=lambda r: r.metrics.score, reverse=True)


# ---------------------------------------------------------------------------
# GA operators
# ---------------------------------------------------------------------------


def _chrom_key(params: Any) -> tuple[Any, ...]:
    return tuple(dataclasses.asdict(params).values())


def _tournament(
    scored: list[tuple[list[float], float, Any]],
    rng: random.Random,
    k: int,
) -> list[float]:
    competitors = rng.sample(scored, min(k, len(scored)))
    return max(competitors, key=lambda x: x[1])[0]


def _crossover(
    p1: list[float],
    p2: list[float],
    rng: random.Random,
    crossover_prob: float,
) -> list[float]:
    if rng.random() > crossover_prob:
        return list(p1)
    alpha = rng.random()
    return [alpha * a + (1.0 - alpha) * b for a, b in zip(p1, p2)]


def _mutate(
    chrom: list[float],
    space: list[ParamSpec],
    rng: random.Random,
    mutation_rate: float,
    mutation_sigma: float,
) -> list[float]:
    result = []
    for gene, spec in zip(chrom, space):
        if rng.random() < mutation_rate:
            delta = rng.gauss(0.0, (spec.high - spec.low) * mutation_sigma)
            gene = min(spec.high, max(spec.low, gene + delta))
        result.append(gene)
    return result
