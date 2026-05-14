"""sign_sector_ab — A/B backtest of the sector confidence factor.

Tests whether tilting the RegimeSignStrategy ranking toward the 3 sector-axis
cells certified by `sign_sector_axis_probe` (2026-05-14) improves the strategy.

Certified cells (env-gated in `regime_sign._CERTIFIED_SECTOR_BONUS`):
  rev_nhi × 銀行, str_hold × 不動産, rev_nlo × 電機・精密

Design:
- Baseline arm : RS_SECTOR_FACTOR unset — current ranking, byte-identical.
- Variant arm  : RS_SECTOR_FACTOR=1 — certified-cell ΔEV added to regime_ev
  as a ranking tilt (never a hard gate).
- Both arms run the full FY2019–FY2025 walk-forward via `regime_sign_backtest.run_fy`.
- Reported per-FY, aggregate, by corr_mode, and grouped by JGB-rate regime.

JGB-rate regime proxy (JGB yield data is not in the DB): per-FY grouping —
  flat-rate (ZIRP/YCC):   FY2019, FY2020, FY2021
  rising-rate (YCC widen Dec-2022 → NIRP exit Mar-2024 → hikes): FY2022–FY2025
The judge's falsifier is "net-negative in any flat/falling-rate FY", so the
flat-rate FY deltas are the load-bearing check.

CLI: uv run --env-file devenv python -m src.analysis.sign_sector_ab
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from src.analysis.exit_benchmark import Metrics, _metrics
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, FyBacktestResult, run_fy
from src.data.db import get_session
from src.data.models import Stock
from src.strategy.regime_sign import _CERTIFIED_SECTOR_BONUS

_OUT_DIR = Path(__file__).parent.parent.parent / "data" / "analysis" / "sign_sector_axis"
_FLAT_RATE_FYS = {"FY2019", "FY2020", "FY2021"}
_CERTIFIED_SIGNS = {sign for sign, _ in _CERTIFIED_SECTOR_BONUS}


def _run_arm(label: str) -> dict[str, FyBacktestResult]:
    logger.info("══ Running arm: {} ══", label)
    return {cfg.label: run_fy(cfg) for cfg in RS_FY_CONFIGS}


def _delta_row(name: str, base: Metrics, var: Metrics) -> str:
    if base.n == 0 and var.n == 0:
        return f"| {name} | — | — | — | — |"
    d_mean = (var.mean_r - base.mean_r) * 100
    d_sharpe = var.sharpe - base.sharpe
    d_win = (var.win_rate - base.win_rate) * 100
    return (
        f"| {name} | {base.n}→{var.n} | "
        f"{base.fmt_mean_r()}→{var.fmt_mean_r()} ({d_mean:+.2f}pp) | "
        f"{base.fmt_sharpe()}→{var.fmt_sharpe()} ({d_sharpe:+.2f}) | "
        f"{base.fmt_win()}→{var.fmt_win()} ({d_win:+.1f}pp) |"
    )


def _certified_cell_results(
    fyr: FyBacktestResult, sector_map: dict[str, str]
) -> list:
    """ExitResults whose (sign, sector17) is a certified cell."""
    out = []
    for r in fyr.results:
        sign = fyr.sign_map.get((r.stock_code, r.entry_date))
        sector = sector_map.get(r.stock_code)
        if (sign, sector) in _CERTIFIED_SECTOR_BONUS:
            out.append(r)
    return out


def main() -> None:
    with get_session() as s:
        sector_map = {
            code: sec for code, sec in s.execute(select(Stock.code, Stock.sector17)).all() if sec
        }

    os.environ.pop("RS_SECTOR_FACTOR", None)
    base = _run_arm("baseline (sector factor OFF)")

    os.environ["RS_SECTOR_FACTOR"] = "1"
    var = _run_arm("variant (sector factor ON)")
    os.environ.pop("RS_SECTOR_FACTOR", None)

    # ── aggregate ──
    base_all = [r for fyr in base.values() for r in fyr.results]
    var_all = [r for fyr in var.values() for r in fyr.results]
    base_flat = [r for lbl, fyr in base.items() if lbl in _FLAT_RATE_FYS for r in fyr.results]
    var_flat = [r for lbl, fyr in var.items() if lbl in _FLAT_RATE_FYS for r in fyr.results]
    base_rise = [r for lbl, fyr in base.items() if lbl not in _FLAT_RATE_FYS for r in fyr.results]
    var_rise = [r for lbl, fyr in var.items() if lbl not in _FLAT_RATE_FYS for r in fyr.results]

    # ── certified-cell trades (the trades the factor actually steers toward) ──
    base_cert = [r for fyr in base.values() for r in _certified_cell_results(fyr, sector_map)]
    var_cert = [r for fyr in var.values() for r in _certified_cell_results(fyr, sector_map)]

    today = datetime.date.today().isoformat()
    md: list[str] = [
        "# Sign × Sector Factor — A/B Backtest",
        "",
        f"Generated: {today}  ",
        "Baseline = current ranking; Variant = certified-cell ΔEV added to "
        "regime_ev as a ranking tilt (env gate `RS_SECTOR_FACTOR`).  ",
        f"Certified cells: {', '.join(f'{s}×{sec}' for (s, sec) in _CERTIFIED_SECTOR_BONUS)}  ",
        "JGB-rate regime proxy: flat-rate = FY2019–FY2021, rising-rate = FY2022–FY2025.  ",
        "",
        "## Per-FY (overall) — baseline → variant (Δ)",
        "",
        "| FY | n | mean_r | sharpe | win_rate |",
        "|----|---|--------|--------|----------|",
    ]
    for cfg in RS_FY_CONFIGS:
        bm = _metrics(base[cfg.label].results)
        vm = _metrics(var[cfg.label].results)
        flag = " *(flat-rate)*" if cfg.label in _FLAT_RATE_FYS else ""
        md.append(_delta_row(cfg.label + flag, bm, vm))
    md += [
        "",
        "## Grouped",
        "",
        "| group | n | mean_r | sharpe | win_rate |",
        "|-------|---|--------|--------|----------|",
        _delta_row("aggregate (FY2019–FY2025)", _metrics(base_all), _metrics(var_all)),
        _delta_row("flat-rate FYs (2019–2021)", _metrics(base_flat), _metrics(var_flat)),
        _delta_row("rising-rate FYs (2022–2025)", _metrics(base_rise), _metrics(var_rise)),
        "",
        "## Certified-cell trades only",
        "",
        "Trades whose (sign, sector17) is one of the 3 certified cells — the "
        "subset the factor actually steers entry selection toward.",
        "",
        "| group | n | mean_r | sharpe | win_rate |",
        "|-------|---|--------|--------|----------|",
        _delta_row("certified cells (aggregate)", _metrics(base_cert), _metrics(var_cert)),
    ]
    for sign in sorted(_CERTIFIED_SIGNS):
        bsub = [r for fyr in base.values()
                for r in _certified_cell_results(fyr, sector_map)
                if fyr.sign_map.get((r.stock_code, r.entry_date)) == sign]
        vsub = [r for fyr in var.values()
                for r in _certified_cell_results(fyr, sector_map)
                if fyr.sign_map.get((r.stock_code, r.entry_date)) == sign]
        md.append(_delta_row(f"  {sign}", _metrics(bsub), _metrics(vsub)))
    md += [
        "",
        "## By corr_mode (aggregate)",
        "",
        "| corr_mode | n | mean_r | sharpe | win_rate |",
        "|-----------|---|--------|--------|----------|",
    ]
    for mode in ("high", "low", "mid"):
        bsub = [r for r in base_all if r.corr_mode == mode]
        vsub = [r for r in var_all if r.corr_mode == mode]
        md.append(_delta_row(mode, _metrics(bsub), _metrics(vsub)))
    md.append("")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _OUT_DIR / f"ab_{today}.md"
    path.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote A/B report to {}", path)
    print("\n".join(md))


if __name__ == "__main__":
    main()
