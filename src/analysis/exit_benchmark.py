"""Exit-rule benchmark — multi-FY runner with markdown report.

Evaluates a set of exit rules across multiple fiscal years.  Each FY uses
the stock set built from the *previous* year's classified universe, which
mirrors the live-trading setup (you classify stocks at year-end, then trade
them the following year).

Entry: every *early LOW* zigzag trough (direction == -1) detected on any
       representative stock in the year's classified set.

Portfolio constraints (applied by exit_simulator):
    ≤ 1 high-corr position at a time
    ≤ 3 low-corr (or mid-corr) positions at a time

Usage
-----
    # Full FY2018-FY2024 run → writes src/exit/benchmark.md
    uv run --env-file devenv python -m src.analysis.exit_benchmark

    # Single custom FY (prints to stdout only):
    uv run --env-file devenv python -m src.analysis.exit_benchmark \\
        --start 2025-04-01 --end 2026-03-31 --stock-set classified2024
"""

from __future__ import annotations

import argparse
import datetime
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from src.analysis.models import StockClusterMember, StockClusterRun
from src.data.db import get_session
from src.exit.adx_adaptive import AdxAdaptiveRule
from src.exit.adx_trail import AdxTrail
from src.exit.atr_trail import AtrTrail
from src.exit.base import EntryCandidate, ExitResult, ExitRule
from src.exit.entry_scanner import scan_confirmed_entries, scan_entries
from src.exit.exit_simulator import run_simulation
from src.exit.next_peak import NextPeakExit
from src.exit.time_stop import TimeStop
from src.exit.zs_dr_tp_sl import ZsDrTpSl
from src.exit.zs_momentum import ZsMomentumTpSl
from src.exit.zs_tp_sl import ZsTpSl
from src.simulator.cache import DataCache

_N225      = "^N225"
_GRAN      = "1d"
_ADX_PERIOD = 14

_REPORT_PATH        = Path(__file__).parent.parent / "exit" / "benchmark.md"
_ORACLE_REPORT_PATH = Path(__file__).parent.parent / "exit" / "benchmark_oracle.md"

# ── FY configuration: each FY uses the *previous* year's classified set ───────

@dataclass(frozen=True)
class FyConfig:
    label:     str
    start:     datetime.date
    end:       datetime.date
    stock_set: str


FY_CONFIGS: list[FyConfig] = [
    FyConfig("FY2018", datetime.date(2018, 4,  1), datetime.date(2019, 3, 31), "classified2017"),
    FyConfig("FY2019", datetime.date(2019, 4,  1), datetime.date(2020, 3, 31), "classified2018"),
    FyConfig("FY2020", datetime.date(2020, 4,  1), datetime.date(2021, 3, 31), "classified2019"),
    FyConfig("FY2021", datetime.date(2021, 4,  1), datetime.date(2022, 3, 31), "classified2020"),
    FyConfig("FY2022", datetime.date(2022, 4,  1), datetime.date(2023, 3, 31), "classified2021"),
    FyConfig("FY2023", datetime.date(2023, 4,  1), datetime.date(2024, 3, 31), "classified2022"),
    FyConfig("FY2024", datetime.date(2024, 4,  1), datetime.date(2025, 3, 31), "classified2023"),
]


# ── Exit rules ────────────────────────────────────────────────────────────────

def _default_rules() -> list[ExitRule]:
    return [
        TimeStop(max_bars=10),
        TimeStop(max_bars=20),
        TimeStop(max_bars=40),
        ZsTpSl(tp_mult=1.0, sl_mult=1.0),
        ZsTpSl(tp_mult=1.5, sl_mult=1.0),
        ZsTpSl(tp_mult=2.0, sl_mult=1.0),
        ZsTpSl(tp_mult=2.0, sl_mult=2.0),
        AdxTrail(drop_threshold=3.0, min_bars=3),
        AdxTrail(drop_threshold=5.0, min_bars=5),
        AdxTrail(drop_threshold=8.0, min_bars=5),
        ZsDrTpSl(base_tp=1.5, base_sl=1.0, k=0.5),
        ZsDrTpSl(base_tp=2.0, base_sl=1.0, k=0.5),
        ZsDrTpSl(base_tp=1.5, base_sl=1.0, k=1.0),
        ZsDrTpSl(base_tp=2.0, base_sl=2.0, k=0.5),
    ]


def _oracle_rules() -> list[ExitRule]:
    """Rules evaluated in the oracle-entry benchmark (best + adaptive variants)."""
    return [
        TimeStop(max_bars=10),
        TimeStop(max_bars=20),
        TimeStop(max_bars=40),
        ZsTpSl(tp_mult=2.0, sl_mult=2.0),
        AdxTrail(drop_threshold=5.0, min_bars=5),
        AdxTrail(drop_threshold=8.0, min_bars=5),
        AtrTrail(k=1.5, atr_period=5, max_bars=15),
        AtrTrail(k=2.0, atr_period=5, max_bars=20),
        NextPeakExit(size=5, middle_size=2, max_bars=20),
        AdxAdaptiveRule(),
        ZsMomentumTpSl(base_tp=1.0, base_sl=0.75),
        ZsDrTpSl(base_tp=1.5, base_sl=1.0, k=0.5),
        ZsDrTpSl(base_tp=2.0, base_sl=1.0, k=0.5),
        ZsDrTpSl(base_tp=1.5, base_sl=1.0, k=1.0),
        ZsDrTpSl(base_tp=2.0, base_sl=2.0, k=0.5),
    ]


# ── Data loading helpers ──────────────────────────────────────────────────────

def _load_rep_codes(stock_set: str) -> list[str]:
    with get_session() as session:
        run = session.execute(
            select(StockClusterRun)
            .where(StockClusterRun.fiscal_year == stock_set)
            .order_by(StockClusterRun.created_at.desc())
        ).scalars().first()
        if run is None:
            raise ValueError(f"No StockClusterRun found for {stock_set!r}")
        members = session.execute(
            select(StockClusterMember.stock_code)
            .where(
                StockClusterMember.run_id == run.id,
                StockClusterMember.is_representative.is_(True),
            )
        ).scalars().all()
    return list(members)


def _load_cache(code: str, start: datetime.date, end: datetime.date) -> DataCache | None:
    start_dt    = datetime.datetime(start.year, start.month, start.day, tzinfo=datetime.timezone.utc)
    lookback_dt = start_dt - datetime.timedelta(days=200)
    end_dt      = datetime.datetime(end.year, end.month, end.day,
                                    23, 59, 59, tzinfo=datetime.timezone.utc)
    cache = DataCache(code, _GRAN)
    try:
        with get_session() as session:
            cache.load(session, lookback_dt, end_dt)
    except Exception as exc:
        logger.warning("Could not load cache for {}: {}", code, exc)
        return None
    if not cache.bars:
        return None
    _add_adx(cache)
    return cache


def _add_adx(cache: DataCache) -> None:
    import numpy as np

    bars = cache.bars
    n    = len(bars)
    if n < _ADX_PERIOD + 1:
        return

    highs  = np.array([b.high  for b in bars], dtype=float)
    lows   = np.array([b.low   for b in bars], dtype=float)
    closes = np.array([b.close for b in bars], dtype=float)

    prev_close    = np.roll(closes, 1); prev_close[0] = closes[0]
    tr = np.maximum(highs - lows,
         np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close)))

    up   = highs - np.roll(highs, 1); up[0]   = 0.0
    down = np.roll(lows, 1) - lows;   down[0] = 0.0
    dm_p = np.where((up > down)   & (up > 0),   up,   0.0)
    dm_n = np.where((down > up)   & (down > 0), down, 0.0)

    p = _ADX_PERIOD
    atr = np.zeros(n); di_p = np.zeros(n); di_n = np.zeros(n)
    atr[p-1]  = tr[:p].sum()
    di_p[p-1] = dm_p[:p].sum()
    di_n[p-1] = dm_n[:p].sum()
    for i in range(p, n):
        atr[i]  = atr[i-1]  - atr[i-1]  / p + tr[i]
        di_p[i] = di_p[i-1] - di_p[i-1] / p + dm_p[i]
        di_n[i] = di_n[i-1] - di_n[i-1] / p + dm_n[i]

    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = np.where(atr > 0, 100.0 * di_p / atr, 0.0)
        ndi = np.where(atr > 0, 100.0 * di_n / atr, 0.0)
        dx  = np.where((pdi + ndi) > 0, 100.0 * np.abs(pdi - ndi) / (pdi + ndi), 0.0)

    adx = np.zeros(n)
    start_adx = 2 * p - 1
    if start_adx < n:
        adx[start_adx] = dx[p:start_adx+1].mean()
        for i in range(start_adx+1, n):
            adx[i] = (adx[i-1] * (p-1) + dx[i]) / p

    for i, bar in enumerate(bars):
        if adx[i] > 0:
            bar.indicators["ADX14"]     = float(adx[i])
            bar.indicators["ADX14_POS"] = float(pdi[i])
            bar.indicators["ADX14_NEG"] = float(ndi[i])


# ── Metrics ───────────────────────────────────────────────────────────────────

@dataclass
class Metrics:
    n:         int     = 0
    mean_r:    float   = 0.0
    mean_rb:   float   = 0.0   # mean return per bar
    sharpe:    float   = float("nan")
    win_rate:  float   = 0.0
    hold_bars: float   = 0.0
    reasons:   dict[str, int] = field(default_factory=dict)

    def fmt_mean_r(self)  -> str: return f"{self.mean_r*100:+.2f}%"
    def fmt_mean_rb(self) -> str: return f"{self.mean_rb*100:+.4f}%"
    def fmt_sharpe(self)  -> str: return f"{self.sharpe:.2f}" if not math.isnan(self.sharpe) else "—"
    def fmt_win(self)     -> str: return f"{self.win_rate*100:.1f}%"
    def fmt_hold(self)    -> str: return f"{self.hold_bars:.1f}"
    def fmt_reasons(self) -> str:
        return "  ".join(f"{k}:{v}" for k, v in sorted(self.reasons.items()))


def _metrics(results: list[ExitResult]) -> Metrics:
    if not results:
        return Metrics()
    rets  = [r.return_pct for r in results]
    holds = [r.hold_bars  for r in results]
    n     = len(rets)
    mr    = statistics.mean(rets)
    mh    = statistics.mean(holds)
    mrb   = mr / mh if mh > 0 else 0.0
    try:
        std   = statistics.stdev(rets)
        sh    = (mr / std * math.sqrt(252)) if std > 0 else float("nan")
    except statistics.StatisticsError:
        sh = float("nan")
    wr  = sum(1 for r in rets if r > 0) / n
    rdict: dict[str, int] = defaultdict(int)
    for r in results:
        rdict[r.exit_reason] += 1
    return Metrics(n=n, mean_r=mr, mean_rb=mrb, sharpe=sh,
                   win_rate=wr, hold_bars=mh, reasons=dict(rdict))


# ── Per-FY runner ─────────────────────────────────────────────────────────────

@dataclass
class FyRunResult:
    config:       FyConfig
    n_stocks:     int
    n_candidates: int
    rule_results: dict[str, list[ExitResult]]   # rule_name → trades


def run_fy(config: FyConfig, rules: list[ExitRule], oracle: bool = False) -> FyRunResult:
    logger.info("── {} ({} – {}) stock_set={} ──",
                config.label, config.start, config.end, config.stock_set)

    rep_codes = _load_rep_codes(config.stock_set)
    logger.info("  {} representatives", len(rep_codes))

    n225_cache = _load_cache(_N225, config.start, config.end)
    if n225_cache is None:
        raise RuntimeError(f"Failed to load ^N225 data for {config.label}")

    stock_caches: dict[str, DataCache] = {}
    for code in rep_codes:
        c = _load_cache(code, config.start, config.end)
        if c:
            stock_caches[code] = c
    logger.info("  {}/{} caches loaded", len(stock_caches), len(rep_codes))

    scanner = scan_confirmed_entries if oracle else scan_entries
    all_candidates: list[EntryCandidate] = []
    for code, cache in stock_caches.items():
        all_candidates.extend(scanner(cache, n225_cache, config.start, config.end))
    all_candidates.sort(key=lambda c: c.entry_date)
    logger.info("  {} {} candidates", len(all_candidates),
                "oracle" if oracle else "early-LOW")

    rule_results: dict[str, list[ExitResult]] = {}
    for rule in rules:
        res = run_simulation(all_candidates, rule, stock_caches, config.end)
        rule_results[rule.name] = res
    logger.info("  simulations done — best rule by trades: {}",
                max(rule_results, key=lambda k: len(rule_results[k])))

    return FyRunResult(
        config=config,
        n_stocks=len(stock_caches),
        n_candidates=len(all_candidates),
        rule_results=rule_results,
    )


# ── Markdown generation ───────────────────────────────────────────────────────

_MD_COLS = ["n", "mean_r", "mean_r/bar", "sharpe", "win_rate", "hold_bars"]


def _md_row(rule_name: str, m: Metrics) -> str:
    if m.n == 0:
        return f"| {rule_name} | — | — | — | — | — | — |"
    return (
        f"| {rule_name} "
        f"| {m.n} "
        f"| {m.fmt_mean_r()} "
        f"| {m.fmt_mean_rb()} "
        f"| {m.fmt_sharpe()} "
        f"| {m.fmt_win()} "
        f"| {m.fmt_hold()} |"
    )


def _md_table(rules: list[ExitRule], rule_to_results: dict[str, list[ExitResult]]) -> list[str]:
    lines = [
        "| rule | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |",
        "|------|--:|-------:|----------:|-------:|---------:|----------:|",
    ]
    for rule in rules:
        m = _metrics(rule_to_results.get(rule.name, []))
        lines.append(_md_row(rule.name, m))
    return lines


def _md_corr_section(
    rules: list[ExitRule],
    rule_to_results: dict[str, list[ExitResult]],
) -> list[str]:
    lines: list[str] = []
    for mode in ("high", "mid", "low"):
        sub_map = {
            r.name: [x for x in rule_to_results.get(r.name, []) if x.corr_mode == mode]
            for r in rules
        }
        if all(len(v) == 0 for v in sub_map.values()):
            continue
        lines.append(f"\n**corr_mode = {mode}**\n")
        lines += _md_table(rules, sub_map)
    return lines


def _md_reasons_table(rules: list[ExitRule], rule_to_results: dict[str, list[ExitResult]]) -> list[str]:
    lines = ["| rule | exit reasons |", "|------|-------------|"]
    for rule in rules:
        m = _metrics(rule_to_results.get(rule.name, []))
        lines.append(f"| {rule.name} | {m.fmt_reasons()} |")
    return lines


def _generate_report(
    fy_results: list[FyRunResult],
    rules:      list[ExitRule],
    run_date:   datetime.date,
    oracle:     bool = False,
) -> str:
    lines: list[str] = []

    title = "Exit-Rule Benchmark Report — Oracle Entries" if oracle else "Exit-Rule Benchmark Report"
    entry_note = (
        "confirmed LOW zigzag troughs (`detect_peaks` direction = −2); "
        "entry date = early-detection date (trough + middle_size bars), "
        "entry price = trough low. **Upper-bound scores — entry quality removed.**"
        if oracle else
        "early LOW zigzag troughs (`detect_peaks` direction = −1)"
    )
    lines += [
        f"# {title}",
        "",
        f"Generated: {run_date}",
        "",
        "## Configuration",
        "",
        f"- Stock sets: each FY uses the classified set from the *previous* year",
        f"- Entry: {entry_note}",
        f"- Portfolio: ≤ 1 high-corr position, ≤ 3 low/mid-corr positions",
        f"- Fill model: two-bar rule (signal on day T, fill at open of T+1)",
        "",
        "## Rules evaluated",
        "",
        "| rule | description |",
        "|------|-------------|",
    ]
    for rule in rules:
        desc = rule.__class__.__name__
        lines.append(f"| `{rule.name}` | {desc} |")

    lines += ["", "---", ""]

    # ── Per-FY sections ───────────────────────────────────────────────────────
    for fyr in fy_results:
        cfg = fyr.config
        lines += [
            f"## {cfg.label}  ({cfg.start} – {cfg.end})",
            "",
            f"- Stock set   : `{cfg.stock_set}` ({fyr.n_stocks} representatives loaded)",
            f"- Candidates  : {fyr.n_candidates} {'oracle' if oracle else 'early-LOW'} entries",
            "",
        ]
        lines += _md_table(rules, fyr.rule_results)
        lines += ["", "### Exit reasons", ""]
        lines += _md_reasons_table(rules, fyr.rule_results)
        lines += ["", "### By corr_mode", ""]
        lines += _md_corr_section(rules, fyr.rule_results)
        lines += ["", "---", ""]

    # ── Aggregate section ─────────────────────────────────────────────────────
    agg: dict[str, list[ExitResult]] = {rule.name: [] for rule in rules}
    total_cands = 0
    for fyr in fy_results:
        total_cands += fyr.n_candidates
        for rule in rules:
            agg[rule.name].extend(fyr.rule_results.get(rule.name, []))

    lines += [
        "## Aggregate (all FYs combined)",
        "",
        f"- FYs: {fy_results[0].config.label} – {fy_results[-1].config.label}",
        f"- Total candidates: {total_cands}",
        "",
    ]
    lines += _md_table(rules, agg)
    lines += ["", "### Exit reasons (aggregate)", ""]
    lines += _md_reasons_table(rules, agg)
    lines += ["", "### By corr_mode (aggregate)", ""]
    lines += _md_corr_section(rules, agg)
    lines += [""]

    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def run_multify_benchmark(
    fy_configs:  list[FyConfig],
    rules:       list[ExitRule],
    output_path: Path | None = None,
    oracle:      bool = False,
) -> list[FyRunResult]:
    fy_results: list[FyRunResult] = []
    for cfg in fy_configs:
        fy_results.append(run_fy(cfg, rules, oracle=oracle))

    report_md = _generate_report(fy_results, rules, datetime.date.today(), oracle=oracle)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_md, encoding="utf-8")
        logger.info("Report written to {}", output_path)

    print(report_md)
    return fy_results


def _generate_combined_report(
    real_results:   list[FyRunResult],
    oracle_results: list[FyRunResult],
    rules:          list[ExitRule],
    run_date:       datetime.date,
) -> str:
    """Single markdown combining aggregate comparison + full real + oracle sections."""

    def _agg(fy_results: list[FyRunResult]) -> dict[str, list[ExitResult]]:
        agg: dict[str, list[ExitResult]] = {r.name: [] for r in rules}
        for fyr in fy_results:
            for r in rules:
                agg[r.name].extend(fyr.rule_results.get(r.name, []))
        return agg

    real_agg   = _agg(real_results)
    oracle_agg = _agg(oracle_results)

    total_real   = sum(fyr.n_candidates for fyr in real_results)
    total_oracle = sum(fyr.n_candidates for fyr in oracle_results)

    lines: list[str] = [
        "# Exit-Rule Benchmark Report",
        "",
        f"Generated: {run_date}",
        "",
        "## Configuration",
        "",
        "- Stock sets: each FY uses the classified set from the *previous* year",
        "- **Real entries**: early LOW zigzag troughs (`detect_peaks` direction = −1)",
        "- **Oracle entries**: confirmed LOW troughs (direction = −2); entry date = "
        "trough + middle_size bars (no price lookahead). Upper-bound scores.",
        "- Portfolio: ≤ 1 high-corr position, ≤ 3 low/mid-corr positions",
        "- Fill model: two-bar rule (signal on day T, fill at open of T+1)",
        "",
        "---",
        "",
        "## Aggregate comparison — Real vs Oracle (FY2018–FY2024)",
        "",
        f"Real candidates: {total_real}  |  Oracle candidates: {total_oracle}",
        "",
        "| rule | real n | real mean_r | real sharpe | real win% | real hold"
        " | oracle n | oracle mean_r | oracle sharpe | oracle win% | oracle hold |",
        "|------|-------:|------------:|------------:|----------:|----------:"
        "|--------:|-------------:|--------------:|------------:|------------:|",
    ]
    for rule in rules:
        rm = _metrics(real_agg.get(rule.name, []))
        om = _metrics(oracle_agg.get(rule.name, []))
        if rm.n == 0 and om.n == 0:
            lines.append(f"| {rule.name} | — | — | — | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {rule.name} "
            f"| {rm.n} | {rm.fmt_mean_r()} | {rm.fmt_sharpe()} "
            f"| {rm.fmt_win()} | {rm.fmt_hold()} "
            f"| {om.n} | {om.fmt_mean_r()} | {om.fmt_sharpe()} "
            f"| {om.fmt_win()} | {om.fmt_hold()} |"
        )

    lines += ["", "---", ""]

    # Full real section
    lines.append(_generate_report(real_results, rules, run_date, oracle=False))
    lines += ["", "---", ""]

    # Full oracle section
    lines.append(_generate_report(oracle_results, rules, run_date, oracle=True))

    return "\n".join(lines)


def run_benchmark(
    stock_set: str,
    start:     datetime.date,
    end:       datetime.date,
    rules:     list[ExitRule],
) -> None:
    """Single-FY convenience wrapper (used by ad-hoc runs)."""
    cfg = FyConfig(label=f"{start}–{end}", start=start, end=end, stock_set=stock_set)
    fyr = run_fy(cfg, rules)
    report = _generate_report([fyr], rules, datetime.date.today())
    print(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Exit-rule benchmark study")
    parser.add_argument("--stock-set", default=None,
                        help="Override stock set (single-FY mode)")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end",   default=None)
    parser.add_argument("--output", default=None,
                        help="Output markdown path (multi-FY mode only)")
    parser.add_argument("--oracle", action="store_true",
                        help="Oracle entries only (no combined report)")
    parser.add_argument("--real-only", action="store_true",
                        help="Real entries only (no combined report)")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else _REPORT_PATH

    if args.stock_set or args.start or args.end:
        # single-FY mode
        start     = datetime.date.fromisoformat(args.start) if args.start else datetime.date(2018, 4, 1)
        end       = datetime.date.fromisoformat(args.end)   if args.end   else datetime.date(2025, 3, 31)
        stock_set = args.stock_set or "classified2024"
        oracle    = args.oracle
        rules     = _oracle_rules() if oracle else _default_rules()
        cfg = FyConfig(label=f"{start}–{end}", start=start, end=end, stock_set=stock_set)
        fyr = run_fy(cfg, rules, oracle=oracle)
        print(_generate_report([fyr], rules, datetime.date.today(), oracle=oracle))
        return

    if args.oracle:
        rules = _oracle_rules()
        run_multify_benchmark(FY_CONFIGS, rules, output_path=_ORACLE_REPORT_PATH, oracle=True)
        return

    if args.real_only:
        rules = _default_rules()
        run_multify_benchmark(FY_CONFIGS, rules, output_path=output_path, oracle=False)
        return

    # Default: combined real + oracle report
    rules = _oracle_rules()
    logger.info("Running real-entry benchmark …")
    real_results = [run_fy(cfg, rules, oracle=False) for cfg in FY_CONFIGS]
    logger.info("Running oracle-entry benchmark …")
    oracle_results = [run_fy(cfg, rules, oracle=True) for cfg in FY_CONFIGS]

    report_md = _generate_combined_report(
        real_results, oracle_results, rules, datetime.date.today()
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_md, encoding="utf-8")
    logger.info("Combined report written to {}", output_path)
    print(report_md)


if __name__ == "__main__":
    main()
