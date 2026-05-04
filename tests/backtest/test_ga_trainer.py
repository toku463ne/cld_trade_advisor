"""Unit tests for GATrainer — no DB or real DataCache required."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.backtest.ga_trainer import GAConfig, GATrainer, _crossover, _mutate, _tournament
from src.simulator.bar import BarData
from src.simulator.cache import DataCache
from src.simulator.order import OrderType
from src.simulator.simulator import TradeSimulator
from src.strategy.base import ParamSpec, Strategy, StrategyPlugin

UTC = datetime.timezone.utc


# ---------------------------------------------------------------------------
# Minimal strategy + plugin for testing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Params:
    threshold: float
    units: int = 100


class _ThresholdStrategy(Strategy):
    """Buys when close > threshold, sells on next bar. Trivially testable."""

    def __init__(self, params: _Params) -> None:
        self._p = params
        self._bought = False

    @property
    def name(self) -> str:
        return f"Threshold({self._p.threshold})"

    def reset(self) -> None:
        self._bought = False

    def on_bar(self, bar: BarData, sim: TradeSimulator) -> None:
        if not self._bought and bar.close > self._p.threshold:
            sim.buy(self._p.units, OrderType.MARKET)
            self._bought = True
        elif self._bought and sim.position.quantity > 0:
            sim.sell(self._p.units, OrderType.MARKET)
            self._bought = False


class _TestPlugin(StrategyPlugin["_Params"]):
    @property
    def name(self) -> str:
        return "TestStrategy"

    @property
    def cli_name(self) -> str:
        return "test_strategy"

    def setup_cache(self, cache: Any, _units: int) -> None:
        pass

    def make_grid(self, units: int) -> list[_Params]:
        return [_Params(threshold=t, units=units) for t in (90.0, 100.0, 110.0)]

    def make_strategy(self, params: _Params) -> _ThresholdStrategy:
        return _ThresholdStrategy(params)

    def param_labels(self) -> list[tuple[str, str, str]]:
        return [("threshold", "Threshold", "Buy trigger")]

    def entry_exit_lines(self) -> list[str]:
        return ["- Buy when close > threshold."]

    def param_space(self) -> list[ParamSpec]:
        return [ParamSpec("threshold", low=50.0, high=200.0, dtype=float)]


def _make_cache() -> DataCache:
    bars = [
        BarData(
            dt=datetime.datetime(2024, 1, d, tzinfo=UTC),
            open=100.0, high=105.0, low=95.0, close=102.0, volume=1000,
            indicators={},
        )
        for d in range(1, 21)
    ]
    cache = MagicMock(spec=DataCache)
    cache.gran = "1d"
    cache.bars = bars
    cache.datetimes = [b.dt for b in bars]
    # sim.tick(dt) calls cache.tick(dt) to get the bar
    _by_dt = {b.dt: b for b in bars}
    cache.tick.side_effect = lambda dt: _by_dt.get(dt)
    return cache


# ---------------------------------------------------------------------------
# GA operator unit tests
# ---------------------------------------------------------------------------


class TestGAOperators:
    def test_tournament_returns_fittest(self) -> None:
        rng = __import__("random").Random(0)
        scored = [
            ([0.1], 0.5, None),
            ([0.2], 0.9, None),
            ([0.3], 0.1, None),
        ]
        winner = _tournament(scored, rng, k=3)
        assert winner == [0.2]

    def test_crossover_between_parents(self) -> None:
        rng = __import__("random").Random(42)
        p1, p2 = [0.0, 0.0], [1.0, 1.0]
        child = _crossover(p1, p2, rng, crossover_prob=1.0)
        for gene in child:
            assert 0.0 <= gene <= 1.0

    def test_crossover_clone_when_below_prob(self) -> None:
        rng = __import__("random").Random(0)
        p1, p2 = [1.0], [2.0]
        child = _crossover(p1, p2, rng, crossover_prob=0.0)
        assert child == p1

    def test_mutate_stays_in_bounds(self) -> None:
        rng = __import__("random").Random(0)
        space = [ParamSpec("x", low=0.0, high=1.0)]
        for _ in range(100):
            chrom = [rng.uniform(0.0, 1.0)]
            mutated = _mutate(chrom, space, rng, mutation_rate=1.0, mutation_sigma=0.5)
            assert 0.0 <= mutated[0] <= 1.0

    def test_mutate_rate_zero_leaves_unchanged(self) -> None:
        rng = __import__("random").Random(0)
        space = [ParamSpec("x", low=0.0, high=1.0)]
        chrom = [0.5]
        mutated = _mutate(chrom, space, rng, mutation_rate=0.0, mutation_sigma=0.5)
        assert mutated == chrom


# ---------------------------------------------------------------------------
# GATrainer integration tests
# ---------------------------------------------------------------------------


class TestGATrainer:
    def test_returns_sorted_results(self) -> None:
        plugin = _TestPlugin()
        cache = _make_cache()
        cfg = GAConfig(population_size=10, generations=3, seed=42)
        results = GATrainer(cfg).train(cache, plugin, units=100)
        scores = [r.metrics.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_no_duplicate_params(self) -> None:
        plugin = _TestPlugin()
        cache = _make_cache()
        cfg = GAConfig(population_size=15, generations=5, seed=0)
        results = GATrainer(cfg).train(cache, plugin, units=100)
        keys = [r.params.threshold for r in results]
        assert len(keys) == len(set(keys))

    def test_result_count_positive(self) -> None:
        plugin = _TestPlugin()
        cache = _make_cache()
        cfg = GAConfig(population_size=8, generations=2, seed=1)
        results = GATrainer(cfg).train(cache, plugin, units=100)
        assert len(results) > 0

    def test_seed_reproducibility(self) -> None:
        plugin = _TestPlugin()
        cache = _make_cache()
        cfg = GAConfig(population_size=10, generations=3, seed=99)
        r1 = GATrainer(cfg).train(cache, plugin, units=100)
        r2 = GATrainer(cfg).train(cache, plugin, units=100)
        assert [r.params.threshold for r in r1] == [r.params.threshold for r in r2]


# ---------------------------------------------------------------------------
# StrategyPlugin.decode_params default implementation
# ---------------------------------------------------------------------------


class TestDecodeParams:
    def test_decode_clips_to_bounds(self) -> None:
        plugin = _TestPlugin()
        params = plugin.decode_params([9999.0], units=100)
        assert params.threshold == pytest.approx(200.0)

    def test_decode_correct_value(self) -> None:
        plugin = _TestPlugin()
        params = plugin.decode_params([120.0], units=100)
        assert params.threshold == pytest.approx(120.0)

    def test_decode_int_rounding(self) -> None:
        from src.strategy.sma_breakout import SMABreakoutPlugin
        plugin = SMABreakoutPlugin()
        # sma_period is int; 22.7 should round to 23
        space = plugin.param_space()
        vec = [22.7, 3.0, 10.0, 0.05, 0.02]
        params = plugin.decode_params(vec, units=100)
        assert params.sma_period == 23
        assert isinstance(params.sma_period, int)
