"""confluence_strategy_backtest — live ZsTpSl backtest of "trade when ≥N bullish signs valid."

Strategy spec:
    For each trading day d in the FY:
      For each stock in the FY universe:
        count valid bullish signs (using validity-windowed framework
        from bullish_confluence_v2 — each fire counts for its sign's
        valid_bars trading days after firing).
        If count ≥ N AND not already holding this stock recently:
          emit long candidate (entry at d+1 open, two-bar fill).

    Exit:     ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3) — same as
              regime_sign_backtest.
    Portfolio: ≤1 high-corr + ≤3 low/mid-corr (enforced by
              run_simulation natively).
    Cooldown:  no re-entry on same stock within _COOLDOWN_BARS=10 days
              of the prior fire (approximates "wait until exit").
    corr_mode: 20-bar rolling Pearson of daily returns vs ^N225.
               |corr|≥0.6 = high, |corr|≤0.3 = low, else mid.

Bullish sign set (7 signs, brk_wall EXCLUDED per probe finding that
brk_wall dilutes the v2 confluence by −0.83pp):

    str_hold, str_lead, str_lag, brk_sma, brk_bol, rev_lo, rev_nlo

Sweeps N ∈ {1, 2, 3} per FY, reports per-arm Sharpe / mean_r / win%.

Compared baseline: the existing regime_sign strategy
(see src/analysis/regime_sign_backtest.md — Sharpe +1.33 across FY2021-FY2025).

Read-only.  Output: src/analysis/benchmark.md
§ Confluence Strategy A/B (N=1,2,3).
"""
from __future__ import annotations

import datetime
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.bullish_confluence_v2_probe import _BULLISH_SIGNS, _VALID_BARS
from src.analysis.exit_benchmark import FyConfig, _metrics
from src.analysis.models import (
    SignBenchmarkEvent,
    SignBenchmarkRun,
    StockClusterMember,
    StockClusterRun,
)
from src.analysis.regime_sign_backtest import _build_zs_map, RS_FY_CONFIGS
from src.data.db import get_session
from src.exit.base import EntryCandidate
from src.exit.exit_simulator import run_simulation
from src.exit.zs_tp_sl import ZsTpSl
from src.simulator.cache import DataCache

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SECTION_HEADER = "## Confluence Strategy A/B (N=1, 2, 3)"
_MULTIYEAR_MIN_RUN_ID = 47

_EXIT_RULE = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
_COOLDOWN_BARS = 10
_CORR_WINDOW = 20
_HIGH_CORR_THRESH = 0.6
_LOW_CORR_THRESH = 0.3
_N_VALUES = [1, 2, 3]


# ── 1. Universe + fires ───────────────────────────────────────────────


def _stocks_for_fy(cluster_set: str) -> list[str]:
    with get_session() as s:
        run = s.execute(
            select(StockClusterRun).where(StockClusterRun.fiscal_year == cluster_set)
        ).scalar_one_or_none()
        if run is None:
            return []
        return list(s.execute(
            select(StockClusterMember.stock_code)
            .where(StockClusterMember.run_id == run.id,
                   StockClusterMember.is_representative.is_(True))
        ).scalars().all())


def _load_bullish_fires_by_stock() -> dict[str, list[tuple[str, datetime.date]]]:
    with get_session() as s:
        rows = s.execute(
            select(
                SignBenchmarkRun.sign_type,
                SignBenchmarkEvent.stock_code,
                SignBenchmarkEvent.fired_at,
            )
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(
                SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID,
                SignBenchmarkRun.sign_type.in_(_BULLISH_SIGNS),
            )
        ).all()
    by_stock: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for sign, stock, fired_at in rows:
        d = fired_at.date() if hasattr(fired_at, "date") else fired_at
        by_stock[stock].append((sign, d))
    return by_stock


# ── 2. corr_mode per (stock, date) ────────────────────────────────────


def _build_corr_map(cache: DataCache, n225_cache: DataCache) -> dict[datetime.date, str]:
    """Return {date: corr_mode} using 20-bar rolling Pearson of daily returns."""
    n225_dates = {b.dt.date() for b in n225_cache.bars}
    stock_close: dict[datetime.date, float] = {}
    for b in cache.bars:
        d = b.dt.date()
        if d in n225_dates:
            stock_close[d] = b.close
    n225_close: dict[datetime.date, float] = {}
    for b in n225_cache.bars:
        n225_close[b.dt.date()] = b.close

    common = sorted(set(stock_close) & set(n225_close))
    if len(common) < _CORR_WINDOW + 2:
        return {}
    s = pd.Series([stock_close[d] for d in common], index=common).pct_change()
    n = pd.Series([n225_close[d] for d in common], index=common).pct_change()
    corr = s.rolling(_CORR_WINDOW, min_periods=_CORR_WINDOW // 2).corr(n)

    out: dict[datetime.date, str] = {}
    for d, c in corr.items():
        if math.isnan(c):
            continue
        ac = abs(c)
        if ac >= _HIGH_CORR_THRESH:
            out[d] = "high"
        elif ac <= _LOW_CORR_THRESH:
            out[d] = "low"
        else:
            out[d] = "mid"
    return out


# ── 3. Candidate construction per N ───────────────────────────────────


def _candidates_for_stock(
    stock: str, fires: list[tuple[str, datetime.date]],
    cache: DataCache, corr_map: dict[datetime.date, str],
    zs_map: dict[datetime.date, tuple[float, ...]],
    fy_start: datetime.date, fy_end: datetime.date,
    n_gate: int,
) -> list[EntryCandidate]:
    """Emit one candidate per "burst" of consecutive ≥N-confluence days, with cooldown."""
    if not cache.bars:
        return []
    # Per-date set of valid signs (validity-windowed)
    by_date: dict[datetime.date, dict] = {}
    trading_dates: list[datetime.date] = []
    seen: set[datetime.date] = set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d)
        by_date[d] = {"close": b.close}
        trading_dates.append(d)
    trading_dates.sort()
    date_to_idx = {d: i for i, d in enumerate(trading_dates)}

    valid_per_date: dict[int, set[str]] = defaultdict(set)
    for sign, fd in fires:
        if fd not in date_to_idx:
            continue
        fi = date_to_idx[fd]
        vb = _VALID_BARS.get(sign, 5)
        for j in range(fi, min(fi + vb + 1, len(trading_dates))):
            valid_per_date[j].add(sign)

    cands: list[EntryCandidate] = []
    last_fire_idx = -10_000   # cooldown sentinel
    for i, d in enumerate(trading_dates):
        if d < fy_start or d > fy_end:
            continue
        count = len(valid_per_date.get(i, set()))
        if count < n_gate:
            continue
        if i - last_fire_idx < _COOLDOWN_BARS:
            continue
        cm = corr_map.get(d, "mid")
        cands.append(EntryCandidate(
            stock_code  = stock,
            entry_date  = d,
            entry_price = by_date[d]["close"],
            corr_mode   = cm,
            corr_n225   = 0.0,   # not used by run_simulation
            zs_history  = zs_map.get(d, ()),
        ))
        last_fire_idx = i
    return cands


# ── 4. FY runner ──────────────────────────────────────────────────────


@dataclass
class _ArmRow:
    fy:        str
    n_gate:    int
    n_trades:  int
    n_props:   int
    mean_r:    float | None
    sharpe:    float | None
    win_rate:  float | None
    hold_bars: float | None


def _run_fy_arm(cfg: FyConfig, n_gate: int,
                fires_by_stock: dict[str, list[tuple[str, datetime.date]]],
                stock_caches: dict[str, DataCache],
                corr_maps: dict[str, dict[datetime.date, str]],
                zs_maps: dict[str, dict[datetime.date, tuple[float, ...]]],
                ) -> _ArmRow:
    codes = list(stock_caches)
    all_cands: list[EntryCandidate] = []
    for code in codes:
        cands = _candidates_for_stock(
            code, fires_by_stock.get(code, []),
            stock_caches[code], corr_maps.get(code, {}),
            zs_maps.get(code, {}),
            cfg.start, cfg.end, n_gate,
        )
        all_cands.extend(cands)
    logger.info("  N={}: {} candidates from {} stocks", n_gate, len(all_cands), len(codes))
    results = run_simulation(all_cands, _EXIT_RULE, stock_caches, cfg.end)
    m = _metrics(results)
    logger.info("  N={}: {} trades, sharpe={:.2f}, mean_r={:+.2%}",
                n_gate, m.n,
                m.sharpe if not math.isnan(m.sharpe) else float("nan"),
                m.mean_r)
    return _ArmRow(
        fy=cfg.label, n_gate=n_gate, n_trades=m.n, n_props=len(all_cands),
        mean_r=m.mean_r if m.n > 0 else None,
        sharpe=m.sharpe if (m.n > 0 and not math.isnan(m.sharpe)) else None,
        win_rate=m.win_rate if m.n > 0 else None,
        hold_bars=m.hold_bars if m.n > 0 else None,
    )


def _run_fy(cfg: FyConfig, fires_by_stock: dict) -> list[_ArmRow]:
    logger.info("── {} ── stocks={} fy={}..{}", cfg.label,
                cfg.stock_set, cfg.start, cfg.end)
    codes = _stocks_for_fy(cfg.stock_set)
    if not codes:
        logger.warning("  no cluster — skip")
        return []

    # Load N225 + per-stock caches with lookback for corr + zs + lookahead for exit
    span_start = cfg.start - datetime.timedelta(days=_LOOKBACK_DAYS_CACHE)
    span_end   = cfg.end   + datetime.timedelta(days=60)
    with get_session() as s:
        n225 = DataCache("^N225", "1d")
        n225.load(s,
            datetime.datetime.combine(span_start, datetime.time.min, tzinfo=datetime.timezone.utc),
            datetime.datetime.combine(span_end,   datetime.time.max, tzinfo=datetime.timezone.utc),
        )
        stock_caches: dict[str, DataCache] = {}
        for code in codes:
            c = DataCache(code, "1d")
            c.load(s,
                datetime.datetime.combine(span_start, datetime.time.min, tzinfo=datetime.timezone.utc),
                datetime.datetime.combine(span_end,   datetime.time.max, tzinfo=datetime.timezone.utc),
            )
            if c.bars:
                stock_caches[code] = c
    logger.info("  loaded {} stock caches", len(stock_caches))

    corr_maps: dict[str, dict[datetime.date, str]] = {}
    zs_maps:   dict[str, dict[datetime.date, tuple[float, ...]]] = {}
    for code, c in stock_caches.items():
        corr_maps[code] = _build_corr_map(c, n225)
        zs_maps[code]   = _build_zs_map(c, n225)

    out: list[_ArmRow] = []
    for n_gate in _N_VALUES:
        out.append(_run_fy_arm(cfg, n_gate, fires_by_stock, stock_caches,
                               corr_maps, zs_maps))
    return out


_LOOKBACK_DAYS_CACHE = 60   # enough for 20-bar corr + zs legs


# ── 5. Report ─────────────────────────────────────────────────────────


def _format_report(all_rows: list[_ArmRow]) -> str:
    by_n: dict[int, list[_ArmRow]] = defaultdict(list)
    for r in all_rows:
        by_n[r.n_gate].append(r)

    lines = [
        f"\n{_SECTION_HEADER}",
        f"\nProbe run: {datetime.date.today()}.  Live ZsTpSl backtest of "
        '"trade long when ≥N bullish signs are valid for a stock today" '
        "across N ∈ {1, 2, 3}.  Same exit (ZsTpSl 2.0/2.0/0.3), same "
        "portfolio cap (≤1 high-corr + ≤3 low/mid-corr), same two-bar fill, "
        f"{_COOLDOWN_BARS}-bar cooldown between re-entries on same stock.",
        "",
        f"Bullish set (brk_wall excluded per dilution finding): "
        + ", ".join(_BULLISH_SIGNS),
        "",
        "### Per-FY results",
        "",
    ]
    fys = sorted({r.fy for r in all_rows})
    for n_gate in _N_VALUES:
        lines += [
            f"#### N ≥ {n_gate}",
            "",
            "| FY | candidates | trades | mean_r | Sharpe | win% | hold_bars |",
            "|----|---:|---:|---:|---:|---:|---:|",
        ]
        n_rows = {r.fy: r for r in by_n[n_gate]}
        for fy in fys:
            r = n_rows.get(fy)
            if r is None:
                continue
            mr = f"{r.mean_r*100:+.2f}%" if r.mean_r is not None else "—"
            sh = f"{r.sharpe:+.2f}" if r.sharpe is not None else "—"
            wr = f"{r.win_rate*100:.0f}%" if r.win_rate is not None else "—"
            hb = f"{r.hold_bars:.1f}" if r.hold_bars is not None else "—"
            lines.append(f"| {fy} | {r.n_props} | {r.n_trades} | {mr} | {sh} | {wr} | {hb} |")
        lines.append("")

    # Aggregate
    lines += [
        "### Aggregate (FY-equal-weighted across all FYs with trades)",
        "",
        "| N gate | total trades | avg Sharpe | avg mean_r | avg win% |",
        "|--------|---:|---:|---:|---:|",
    ]
    for n_gate in _N_VALUES:
        rows = by_n[n_gate]
        total_n = sum(r.n_trades for r in rows)
        sh = [r.sharpe for r in rows if r.sharpe is not None]
        mr = [r.mean_r for r in rows if r.mean_r is not None]
        wr = [r.win_rate for r in rows if r.win_rate is not None]
        avg_sh = statistics.mean(sh) if sh else None
        avg_mr = statistics.mean(mr) if mr else None
        avg_wr = statistics.mean(wr) if wr else None
        sh_s = f"{avg_sh:+.2f}" if avg_sh is not None else "—"
        mr_s = f"{avg_mr*100:+.2f}%" if avg_mr is not None else "—"
        wr_s = f"{avg_wr*100:.0f}%" if avg_wr is not None else "—"
        lines.append(f"| **N ≥ {n_gate}** | {total_n} | **{sh_s}** | **{mr_s}** | {wr_s} |")
    lines += [
        "",
        "### Baseline reference",
        "",
        "regime_sign_backtest (commit 78f4344, FY2019-FY2025): 171 trades, "
        "avg Sharpe +1.33, avg mean_r +0.77%, win% varies.  That is the "
        "current shipped strategy.  Compare confluence-gated arms against "
        "this number to decide whether confluence-based entry is better, "
        "worse, or different cohort entirely.",
        "",
    ]
    return "\n".join(lines)


def _append_to_benchmark(md: str) -> None:
    existing = _BENCH_MD.read_text() if _BENCH_MD.exists() else ""
    if _SECTION_HEADER in existing:
        idx = existing.index(_SECTION_HEADER)
        rest = existing[idx + len(_SECTION_HEADER):]
        nxt = rest.find("\n## ")
        existing = (existing[:idx].rstrip() + "\n") if nxt < 0 \
                   else (existing[:idx].rstrip() + "\n" + rest[nxt:].lstrip("\n"))
    _BENCH_MD.write_text(existing.rstrip() + "\n" + md.lstrip("\n"))
    logger.info("Appended report to {}", _BENCH_MD)


def main() -> None:
    fires_by_stock = _load_bullish_fires_by_stock()
    logger.info("Loaded {} stocks with bullish fires",
                len(fires_by_stock))

    all_rows: list[_ArmRow] = []
    for cfg in RS_FY_CONFIGS:
        all_rows.extend(_run_fy(cfg, fires_by_stock))

    report = _format_report(all_rows)
    print(report)
    _append_to_benchmark(report)


if __name__ == "__main__":
    main()
