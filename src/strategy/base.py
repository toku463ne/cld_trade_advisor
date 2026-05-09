"""Strategy base class — all strategies must implement this interface."""

from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Sequence, TypeVar

from src.simulator.bar import BarData
from src.simulator.simulator import TradeSimulator

if TYPE_CHECKING:
    from src.strategy.proposal import SignalProposal

P = TypeVar("P")


@dataclass(frozen=True)
class ParamSpec:
    """Describes one dimension of a continuous parameter search space.

    Used by :class:`GATrainer` (and any future continuous optimizer) to
    encode / decode strategy parameters as float vectors.

    Attributes:
        name:  Matches the field name in the strategy's Params dataclass.
        low:   Inclusive lower bound (in real-valued space).
        high:  Inclusive upper bound (in real-valued space).
        dtype: ``int`` or ``float``.  Int params are rounded after decode.
    """

    name: str
    low: float
    high: float
    dtype: type = float  # int or float


class Strategy(ABC):
    """Abstract base for all trading strategies.

    The backtest runner calls ``on_bar`` once per bar after the simulator
    has processed that bar's orders.  Strategies submit new orders via
    *sim* and must be fully restartable via ``reset()`` so the trainer
    can run thousands of iterations without re-allocating objects.
    """

    @abstractmethod
    def on_bar(self, bar: BarData, sim: TradeSimulator) -> None:
        """React to a completed bar and optionally submit orders."""
        ...

    @abstractmethod
    def reset(self) -> None:
        """Reset all mutable state to initial conditions (no data reload)."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Short human-readable identifier including key parameters."""
        ...


class StrategyPlugin(ABC, Generic[P]):
    """Encapsulates everything needed to run, train, and report on a strategy.

    To add a new strategy:
    1. Define Params dataclass, Strategy subclass, and Plugin subclass in one
       module under src/strategy/.
    2. Call ``registry.register(YourPlugin())`` at the bottom of that module.
    3. No other files need editing — the registry auto-discovers the module.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Registry key and DB strategy_name (e.g. ``'SMABreakout'``)."""
        ...

    @property
    @abstractmethod
    def cli_name(self) -> str:
        """Slug used in --strategy CLI arg (e.g. ``'sma_breakout'``)."""
        ...

    @abstractmethod
    def setup_cache(self, cache: Any, units: int) -> None:
        """Add required indicators to *cache* (SMA, RSTD, etc.)."""
        ...

    @abstractmethod
    def make_grid(self, units: int) -> list[P]:
        """Return the full parameter grid for grid-search training."""
        ...

    @abstractmethod
    def make_strategy(self, params: P) -> Strategy:
        """Instantiate and return a Strategy for the given params."""
        ...

    @abstractmethod
    def param_labels(self) -> list[tuple[str, str, str]]:
        """Return ``[(param_key, display_name, description), ...]`` for reports.

        Exclude ``units`` — it is shown separately in the report header.
        """
        ...

    @abstractmethod
    def entry_exit_lines(self) -> list[str]:
        """Return markdown bullet lines describing entry/exit logic."""
        ...

    # ------------------------------------------------------------------
    # Continuous-optimizer interface (optional — implement for GA / RL)
    # ------------------------------------------------------------------

    def param_space(self) -> list[ParamSpec]:
        """Return the continuous search space as a list of :class:`ParamSpec`.

        One entry per evolvable parameter (exclude ``units``).
        Raise ``NotImplementedError`` if this plugin does not support
        continuous optimizers.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not define param_space(). "
            "Override it to enable GA / continuous training."
        )

    def encode_params(self, params: P) -> list[float]:
        """Convert a typed Params instance → float chromosome.

        Inverse of :meth:`decode_params`.  Used to seed the GA population
        with known-good parameter sets.
        """
        import dataclasses as _dc
        d = _dc.asdict(params)  # type: ignore[arg-type]
        return [float(d[spec.name]) for spec in self.param_space()]

    def default_seeds(self) -> list[P]:
        """Return known-good parameter sets to inject into the GA's initial population.

        Override to provide domain knowledge as starting points so the GA
        begins from productive regions of the search space rather than
        exploring blindly from a fully random population.
        """
        return []

    def setup_cache_for_params(self, cache: Any, params: P) -> None:
        """Ensure all indicators required by *params* are loaded into *cache*.

        Called by :class:`GATrainer` before each unique backtest so that
        parameter sets with arbitrary indicator periods (e.g. SMA73) are
        computed and injected on demand.

        The default implementation is a no-op.  Override when the indicator
        period is part of the evolving parameter set.
        """

    def decode_params(self, vector: Sequence[float], units: int) -> P:
        """Convert a float chromosome → typed Params instance.

        The default implementation uses :meth:`param_space` to build a
        keyword-argument dict and reconstructs the Params dataclass.
        Override when the default is insufficient (e.g. coupled constraints).
        """
        space = self.param_space()
        kwargs: dict[str, Any] = {"units": units}
        for spec, raw in zip(space, vector):
            clipped = min(spec.high, max(spec.low, float(raw)))
            kwargs[spec.name] = int(round(clipped)) if spec.dtype is int else clipped
        grid = self.make_grid(units)
        return type(grid[0])(**kwargs)

    def generate_report(
        self,
        stock_code: str,
        gran: str,
        start: datetime.datetime,
        end: datetime.datetime,
        results: list[Any],
        top_n: int = 20,
    ) -> Path:
        """Write a markdown report and return its path.

        The default implementation delegates to the generic report builder.
        Override for a fully custom layout.
        """
        from src.backtest.generic_report import generate_report as _gen  # noqa: PLC0415
        return _gen(self, stock_code, gran, start, end, results, top_n)


class ProposalStrategy(ABC):
    """Abstract base for multi-stock proposal scanners.

    Unlike :class:`Strategy` (which processes one stock's bars through a simulator),
    a ProposalStrategy scans the full universe each day and returns ranked candidates
    for human review.  No orders are submitted; output is a list of
    :class:`~src.strategy.proposal.SignalProposal` objects.
    """

    @abstractmethod
    def propose(
        self,
        as_of: datetime.datetime,
        mode: str | None = None,
    ) -> list[SignalProposal]:
        """Return signal proposals for *as_of*.

        Args:
            as_of: The evaluation datetime (typically the daily close).
            mode:  Optional override for the instance's default mode.

        Returns:
            Ordered list of proposals, best first.  Empty list when no
            qualifying candidates exist.
        """
        ...

    def propose_range(
        self,
        start: datetime.datetime,
        end:   datetime.datetime,
    ) -> dict[datetime.date, list[SignalProposal]]:
        """Run :meth:`propose` for every trading date in ``[start, end]``.

        The default implementation raises :exc:`NotImplementedError`.
        Override when the subclass has access to a pre-loaded bar index
        that can drive the date iteration efficiently.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement propose_range(). "
            "Override it or iterate propose() manually."
        )
