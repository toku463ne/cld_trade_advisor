"""Genetic-algorithm trainer.

Evolves a population of parameter chromosomes over G generations.
Fitness = ``compute_metrics(run_backtest(...)).score``.

Duplicate chromosomes (after rounding int params) are evaluated only once,
so the final result list contains only unique parameter sets.

CLI usage (via trainer.py)
--------------------------
    uv run --env-file devenv python -m src.backtest.trainer \\
        --trainer ga \\
        --strategy sma_breakout \\
        --code 7203.T --start 2022-01-01 \\
        --ga-pop 60 --ga-gen 40

Algorithm
---------
- Initial population : random uniform in each param's [low, high]
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
from src.backtest.trainer import TrainResult
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
    seed: int | None       = None


class GATrainer:
    """Genetic-algorithm parameter search.

    Returns ``list[TrainResult]`` sorted by score (best first), containing
    every *unique* parameter set that was evaluated across all generations.
    The interface is intentionally compatible with :class:`GridSearchTrainer`
    so trainer.py can swap them with a single flag.
    """

    def __init__(self, config: GAConfig | None = None) -> None:
        self._cfg = config or GAConfig()

    def train(
        self,
        cache: DataCache,
        plugin: StrategyPlugin[Any],
        units: int = 100,
        initial_capital: float = 1_000_000.0,
    ) -> list[TrainResult[Any]]:
        cfg = self._cfg
        rng = random.Random(cfg.seed)
        space = plugin.param_space()

        sim = TradeSimulator(cache, initial_capital)
        seen: dict[tuple[Any, ...], TrainResult[Any]] = {}

        def _evaluate(chrom: list[float]) -> tuple[float, TrainResult[Any]]:
            params = plugin.decode_params(chrom, units)
            key = _chrom_key(params)
            if key in seen:
                return seen[key].metrics.score, seen[key]
            # Ensure this params set's indicators are loaded (e.g. SMA73)
            plugin.setup_cache_for_params(cache, params)
            strategy = plugin.make_strategy(params)
            bt = run_backtest(strategy, sim, cache)
            metrics = compute_metrics(bt, cache.gran)
            tr = TrainResult(params=params, metrics=metrics, result=bt)
            seen[key] = tr
            return metrics.score, tr

        elite_k = max(1, int(cfg.population_size * cfg.elite_fraction))

        # ── Initial population: seeds first, then random ──────────────────
        seeds = plugin.default_seeds()
        seed_chroms = [plugin.encode_params(p) for p in seeds]
        n_random = max(0, cfg.population_size - len(seed_chroms))
        population = seed_chroms + [
            [rng.uniform(spec.low, spec.high) for spec in space]
            for _ in range(n_random)
        ]

        # ── Evolution loop ────────────────────────────────────────────────
        for gen in range(cfg.generations):
            scored = [(chrom, *_evaluate(chrom)) for chrom in population]
            scored.sort(key=lambda x: x[1], reverse=True)

            logger.info(
                "GA gen {}/{} | best={:.4f} | unique_evals={}",
                gen + 1, cfg.generations, scored[0][1], len(seen),
            )

            elites = [row[0] for row in scored[:elite_k]]

            new_pop: list[list[float]] = list(elites)
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
