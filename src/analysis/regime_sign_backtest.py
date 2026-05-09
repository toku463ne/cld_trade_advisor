"""Regime-Sign strategy backtest: FY2019–FY2024.

Walk-forward design
-------------------
Each FY uses:
  - Stock set : previous year's classified universe
  - Ranking   : all available prior-year SignBenchmarkRun IDs (cumulative, max 5 yrs)
  - Entry     : RegimeSignStrategy.propose_range() — Kumo gate + ADX veto + sign rank
                backtest mode: 1 high-corr + 1 low-corr candidate per day
  - Exit      : ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
  - Fill      : two-bar rule (signal on T, fill at T+1 open)
  - Portfolio : ≤1 high-corr, ≤3 low/mid-corr simultaneous positions

Prior benchmark window per FY
------------------------------
  FY2019 classified2018 → classified2017 (1 yr)
  FY2020 classified2019 → classified2017–2018 (2 yr)
  FY2021 classified2020 → classified2017–2019 (3 yr)
  FY2022 classified2021 → classified2017–2020 (4 yr)
  FY2023 classified2022 → classified2017–2021 (5 yr)
  FY2024 classified2023 → classified2018–2022 (5 yr rolling)

Usage
-----
    uv run --env-file devenv python -m src.analysis.regime_sign_backtest
"""

from __future__ import annotations

import datetime
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from src.analysis.exit_benchmark import FyConfig, Metrics, _add_adx, _load_cache, _metrics
from src.analysis.models import SignBenchmarkRun
from src.data.db import get_session
from src.exit.base import EntryCandidate, ExitResult
from src.exit.exit_simulator import run_simulation
from src.exit.zs_tp_sl import ZsTpSl
from src.indicators.zigzag import detect_peaks
from src.simulator.cache import DataCache
from src.strategy.proposal import SignalProposal
from src.strategy.regime_sign import RegimeSignStrategy

_N225          = "^N225"
_ZZ_SIZE       = 5
_ZZ_MIDDLE     = 2
_ZS_LOOKBACK   = 16
_LOOKBACK_DAYS = 200

EXIT_RULE   = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
MIN_DR      = 0.52   # exclude (sign, kumo) cells with DR ≤ this threshold
REPORT_PATH = Path(__file__).parent / "regime_sign_backtest.md"

# ── FY configurations ─────────────────────────────────────────────────────────

RS_FY_CONFIGS: list[FyConfig] = [
    FyConfig("FY2019", datetime.date(2019, 4, 1), datetime.date(2020, 3, 31), "classified2018"),
    FyConfig("FY2020", datetime.date(2020, 4, 1), datetime.date(2021, 3, 31), "classified2019"),
    FyConfig("FY2021", datetime.date(2021, 4, 1), datetime.date(2022, 3, 31), "classified2020"),
    FyConfig("FY2022", datetime.date(2022, 4, 1), datetime.date(2023, 3, 31), "classified2021"),
    FyConfig("FY2023", datetime.date(2023, 4, 1), datetime.date(2024, 3, 31), "classified2022"),
    FyConfig("FY2024", datetime.date(2024, 4, 1), datetime.date(2025, 3, 31), "classified2023"),
    # FY2025: true out-of-sample — classified2024 stock set, 5-yr prior window
    FyConfig("FY2025", datetime.date(2025, 4, 1), datetime.date(2026, 3, 31), "classified2024"),
]

PRIOR_BENCH_SETS: dict[str, list[str]] = {
    "classified2018": ["classified2017"],
    "classified2019": ["classified2017", "classified2018"],
    "classified2020": ["classified2017", "classified2018", "classified2019"],
    "classified2021": ["classified2017", "classified2018", "classified2019", "classified2020"],
    "classified2022": ["classified2017", "classified2018", "classified2019", "classified2020", "classified2021"],
    "classified2023": ["classified2018", "classified2019", "classified2020", "classified2021", "classified2022"],
    "classified2024": ["classified2019", "classified2020", "classified2021", "classified2022", "classified2023"],
}

EXCLUDE_SIGNS: frozenset[str] = frozenset()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_run_ids(prior_sets: list[str]) -> list[int]:
    with get_session() as session:
        rows = session.execute(
            select(SignBenchmarkRun.id)
            .where(
                SignBenchmarkRun.stock_set.in_(prior_sets),
                SignBenchmarkRun.sign_type.not_in(EXCLUDE_SIGNS),
            )
        ).scalars().all()
    return list(rows)


def _build_zs_map(
    cache:      DataCache,
    n225_cache: DataCache,
) -> dict[datetime.date, tuple[float, ...]]:
    """Return {date: zs_leg_history} for every trading date shared with N225."""
    n225_dates = {b.dt.date() for b in n225_cache.bars}

    groups: dict[datetime.date, list] = {}
    for b in cache.bars:
        groups.setdefault(b.dt.date(), []).append(b)

    days = sorted((d, g) for d, g in groups.items() if d in n225_dates)
    if not days:
        return {}

    dates = [d for d, _ in days]
    highs = [max(b.high for b in g) for _, g in days]
    lows  = [min(b.low  for b in g) for _, g in days]

    peaks = detect_peaks(highs, lows, size=_ZZ_SIZE, middle_size=_ZZ_MIDDLE)

    leg_sizes:   list[float]              = []
    prev_price:  float | None             = None
    leg_size_at: dict[int, list[float]]   = {}
    for p in sorted(peaks, key=lambda x: x.bar_index):
        if prev_price is not None:
            leg_sizes.append(abs(p.price - prev_price))
        leg_size_at[p.bar_index] = list(leg_sizes)
        prev_price = p.price

    peak_idxs = sorted(leg_size_at)
    result: dict[datetime.date, tuple[float, ...]] = {}
    for i, d in enumerate(dates):
        recent = [idx for idx in peak_idxs if idx <= i]
        hist   = leg_size_at[recent[-1]] if recent else []
        result[d] = tuple(hist[-_ZS_LOOKBACK:])
    return result


# ── Per-FY runner ─────────────────────────────────────────────────────────────

@dataclass
class FyBacktestResult:
    config:      FyConfig
    prior_sets:  list[str]
    n_proposals: int
    results:     list[ExitResult]
    sign_map:    dict[tuple[str, datetime.date], str]  # (stock, entry_date) → sign_type


def run_fy(config: FyConfig) -> FyBacktestResult:
    prior_sets = PRIOR_BENCH_SETS[config.stock_set]
    run_ids    = _load_run_ids(prior_sets)
    logger.info("── {} ── stock_set={} | prior={} | {} run_ids",
                config.label, config.stock_set, len(prior_sets), len(run_ids))

    tz = datetime.timezone.utc
    lookback_start = (
        datetime.datetime(config.start.year, config.start.month, config.start.day, tzinfo=tz)
        - datetime.timedelta(days=_LOOKBACK_DAYS)
    )
    fy_start = datetime.datetime(config.start.year, config.start.month, config.start.day, tzinfo=tz)
    fy_end   = datetime.datetime(config.end.year,   config.end.month,   config.end.day,
                                  23, 59, 59, tzinfo=tz)

    # ── Strategy (extended start for Ichimoku / corr / ADX warmup) ────────
    strategy = RegimeSignStrategy.from_config(
        stock_set=config.stock_set,
        run_ids=run_ids,
        start=lookback_start,
        end=fy_end,
        mode="backtest",
        min_dr=MIN_DR,
    )

    # ── Proposals for the FY window only ──────────────────────────────────
    proposals_by_date = strategy.propose_range(fy_start, fy_end)
    all_proposals: list[SignalProposal] = [
        p for ps in proposals_by_date.values() for p in ps
    ]
    logger.info("  {} proposals on {} active dates",
                len(all_proposals), len(proposals_by_date))

    if not all_proposals:
        logger.warning("  No proposals for {} — skipping simulation", config.label)
        return FyBacktestResult(config=config, prior_sets=prior_sets,
                                n_proposals=0, results=[], sign_map={})

    # ── Simulation caches (with 200-day lookback + ADX) ───────────────────
    n225_cache = _load_cache(_N225, config.start, config.end)
    if n225_cache is None:
        raise RuntimeError(f"Failed to load ^N225 for {config.label}")

    stock_codes = {p.stock_code for p in all_proposals}
    stock_caches: dict[str, DataCache] = {}
    for code in stock_codes:
        c = _load_cache(code, config.start, config.end)
        if c:
            stock_caches[code] = c
    logger.info("  {}/{} simulation caches loaded", len(stock_caches), len(stock_codes))

    # ── ZS history maps ───────────────────────────────────────────────────
    zs_maps: dict[str, dict[datetime.date, tuple[float, ...]]] = {
        code: _build_zs_map(cache, n225_cache)
        for code, cache in stock_caches.items()
    }

    # ── daily close per stock for entry_price anchor ──────────────────────
    close_by_date: dict[str, dict[datetime.date, float]] = {}
    for code, cache in stock_caches.items():
        day_map: dict[datetime.date, float] = {}
        for b in cache.bars:
            day_map[b.dt.date()] = b.close
        close_by_date[code] = day_map

    # ── Convert proposals → EntryCandidate ────────────────────────────────
    candidates: list[EntryCandidate] = []
    sign_map:   dict[tuple[str, datetime.date], str] = {}
    seen:       set[tuple[str, datetime.date]] = set()

    for p in sorted(all_proposals, key=lambda x: x.fired_at):
        d   = p.fired_at.date()
        key = (p.stock_code, d)
        if key in seen:
            continue  # one entry per (stock, date)
        seen.add(key)

        if p.stock_code not in stock_caches:
            continue
        close = close_by_date[p.stock_code].get(d)
        if close is None:
            continue

        candidates.append(EntryCandidate(
            stock_code  = p.stock_code,
            entry_date  = d,
            entry_price = close,          # fill price overridden by simulator (next open)
            corr_mode   = p.corr_mode,
            corr_n225   = p.corr_n225,
            zs_history  = zs_maps.get(p.stock_code, {}).get(d, ()),
        ))
        sign_map[key] = p.sign_type

    logger.info("  {} candidates after dedup", len(candidates))

    # ── Run simulation ────────────────────────────────────────────────────
    results = run_simulation(candidates, EXIT_RULE, stock_caches, config.end)
    mean_r  = statistics.mean(r.return_pct for r in results) if results else 0.0
    logger.info("  {} trades completed, mean_r={:+.2%}", len(results), mean_r)

    return FyBacktestResult(
        config      = config,
        prior_sets  = prior_sets,
        n_proposals = len(all_proposals),
        results     = results,
        sign_map    = sign_map,
    )


# ── Reporting ─────────────────────────────────────────────────────────────────

_MD_COLS_HDR = (
    "| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |",
    "|-------|--:|-------:|----------:|-------:|---------:|----------:|",
)


def _md_row(label: str, m: Metrics) -> str:
    if m.n == 0:
        return f"| {label} | — | — | — | — | — | — |"
    return (
        f"| {label} | {m.n} | {m.fmt_mean_r()} | {m.fmt_mean_rb()} "
        f"| {m.fmt_sharpe()} | {m.fmt_win()} | {m.fmt_hold()} |"
    )


def _results_by_sign(
    results:  list[ExitResult],
    sign_map: dict[tuple[str, datetime.date], str],
) -> dict[str, list[ExitResult]]:
    by_sign: dict[str, list[ExitResult]] = defaultdict(list)
    for r in results:
        sign = sign_map.get((r.stock_code, r.entry_date), "unknown")
        by_sign[sign].append(r)
    return dict(by_sign)


def _generate_report(fy_results: list[FyBacktestResult]) -> str:
    lines: list[str] = [
        "# Regime-Sign Strategy Backtest — FY2019–FY2024",
        "",
        f"Generated: {datetime.date.today()}",
        "",
        "## Configuration",
        "",
        f"- Exit rule : `{EXIT_RULE.name}`",
        "- Entry     : `RegimeSignStrategy` (Kumo gate + ADX veto, backtest mode)",
        "- Fill      : two-bar rule (signal on T, fill at T+1 open)",
        "- Portfolio : ≤ 1 high-corr, ≤ 3 low/mid-corr simultaneous positions",
        f"- min_dr    : {MIN_DR}  (sign/kumo cells with DR ≤ this are excluded from ranking)",
        "",
        "## Prior benchmark window per FY",
        "",
        "| FY | stock_set | prior sets | yrs |",
        "|----|-----------|-----------|----:|",
    ]
    for fyr in fy_results:
        lines.append(
            f"| {fyr.config.label} | `{fyr.config.stock_set}` "
            f"| {', '.join(fyr.prior_sets)} | {len(fyr.prior_sets)} |"
        )
    lines += ["", "---", ""]

    # ── Per-FY ────────────────────────────────────────────────────────────
    for fyr in fy_results:
        cfg = fyr.config
        m   = _metrics(fyr.results)
        lines += [
            f"## {cfg.label}  ({cfg.start} – {cfg.end})",
            "",
            f"- Proposals : {fyr.n_proposals}  |  Trades: {m.n}",
            "",
            "### Overall",
            "",
            *_MD_COLS_HDR,
            _md_row("overall", m),
            "",
            "### By corr_mode",
            "",
            *_MD_COLS_HDR,
        ]
        for mode in ("high", "low", "mid"):
            sub = [r for r in fyr.results if r.corr_mode == mode]
            if sub:
                lines.append(_md_row(mode, _metrics(sub)))

        by_sign = _results_by_sign(fyr.results, fyr.sign_map)
        if by_sign:
            lines += ["", "### By sign_type", "", *_MD_COLS_HDR]
            for sign in sorted(by_sign):
                lines.append(_md_row(sign, _metrics(by_sign[sign])))

        lines += [
            "",
            "### Exit reasons",
            "",
            f"`{m.fmt_reasons()}`",
            "",
            "---",
            "",
        ]

    # ── Aggregate ─────────────────────────────────────────────────────────
    all_results = [r for fyr in fy_results for r in fyr.results]
    all_sign_map: dict[tuple[str, datetime.date], str] = {}
    for fyr in fy_results:
        all_sign_map.update(fyr.sign_map)

    total_props = sum(fyr.n_proposals for fyr in fy_results)
    agg_m       = _metrics(all_results)

    lines += [
        "## Aggregate  (FY2019–FY2024)",
        "",
        f"- Total proposals : {total_props}  |  Total trades: {agg_m.n}",
        "",
        "### Overall",
        "",
        *_MD_COLS_HDR,
        _md_row("aggregate", agg_m),
        "",
        "### By corr_mode",
        "",
        *_MD_COLS_HDR,
    ]
    for mode in ("high", "low", "mid"):
        sub = [r for r in all_results if r.corr_mode == mode]
        if sub:
            lines.append(_md_row(mode, _metrics(sub)))

    by_sign_agg = _results_by_sign(all_results, all_sign_map)
    if by_sign_agg:
        lines += ["", "### By sign_type", "", *_MD_COLS_HDR]
        for sign in sorted(by_sign_agg):
            lines.append(_md_row(sign, _metrics(by_sign_agg[sign])))

    lines += [
        "",
        "### Exit reasons (aggregate)",
        "",
        f"`{agg_m.fmt_reasons()}`",
        "",
    ]
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    fy_results = [run_fy(cfg) for cfg in RS_FY_CONFIGS]
    report     = _generate_report(fy_results)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    logger.info("Report written to {}", REPORT_PATH)
    print(report)


if __name__ == "__main__":
    main()
