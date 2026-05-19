"""rev_nhi_synthetic_flip_probe — does rev_nhi point the wrong way?

Operator question (2026-05-19): rev_nhi has aggregate Sharpe −6.75 / win
27% / mean_r −3.81% in the regime_sign backtest.  Would taking the
OPPOSITE position have wins ~73% / Sharpe positive?

The simulator (`src.exit.exit_simulator`) is LONG-ONLY by design — it
can't be flipped to short trades.  This probe does a measurement-only
synthetic flip: take all rev_nhi-originated trades from a fresh
regime_sign baseline, compute `return_pct_flipped = -return_pct`, and
re-aggregate Sharpe / win% / EV.

**Caveat**: This is a ROUGH approximation.  In a real short, the SL
would be hit when price RISES (not falls).  ZsTpSl with symmetric
multipliers (tp×2, sl×2) on shorts would have a different time-to-hit
distribution than the long it mirrors — especially under skew.  The
flip-the-sign math is correct only at trade-close time; trades that
hit TP early on long might hit SL early on the mirror, etc.

If this probe shows a clean Sharpe positive after flip, the next step
is to build a mirrored short simulator and test against the real path
dynamics.  If it shows still-middling Sharpe, rev_nhi is just noisy at
this universe size and direction-flip won't save it.

Output: docs/analysis/rev_nhi_synthetic_flip_probe.md
"""
from __future__ import annotations

import datetime
import math
import statistics
from collections import defaultdict
from pathlib import Path

from loguru import logger

import src.analysis.regime_sign_backtest as rsb

_SIGN = "rev_nhi"
_REPORT = Path("docs/analysis/rev_nhi_synthetic_flip_probe.md")


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return float("nan")
    m = statistics.mean(returns)
    try:
        s = statistics.stdev(returns)
    except statistics.StatisticsError:
        return float("nan")
    if s <= 0:
        return float("nan")
    return m / s * math.sqrt(252)


def _stats(returns: list[float]) -> dict:
    if not returns:
        return {"n": 0, "mean_r": None, "sharpe": None,
                "win_rate": None, "avg_win": None, "avg_loss": None}
    wins  = [r for r in returns if r > 0]
    loses = [r for r in returns if r <= 0]
    return {
        "n":         len(returns),
        "mean_r":    statistics.mean(returns),
        "sharpe":    _sharpe(returns),
        "win_rate":  len(wins) / len(returns),
        "avg_win":   statistics.mean(wins)  if wins  else 0.0,
        "avg_loss":  statistics.mean(loses) if loses else 0.0,
    }


def _fmt(v, pp=False, pct=False):
    if v is None: return "—"
    if pct: return f"{v*100:+.2f}%"
    if pp:  return f"{v*100:.1f}%"
    return f"{v:+.2f}"


def _row(label: str, s: dict) -> str:
    ev = (s["win_rate"] * s["avg_win"] +
          (1 - s["win_rate"]) * s["avg_loss"]) if s["n"] else None
    return (f"| {label} | {s['n']} | "
            f"{_fmt(s['mean_r'], pct=True)} | "
            f"**{_fmt(s['sharpe'])}** | "
            f"{_fmt(s['win_rate'], pp=True)} | "
            f"{_fmt(s['avg_win'],  pct=True)} | "
            f"{_fmt(s['avg_loss'], pct=True)} | "
            f"{_fmt(ev, pct=True)} |")


def main() -> None:
    rsb.EXCLUDE_SIGNS = frozenset()
    logger.info("Running regime_sign baseline across all FYs …")
    fy_results = [rsb.run_fy(cfg) for cfg in rsb.RS_FY_CONFIGS]

    # Collect rev_nhi-originated trades with their corr_mode.
    # Each trade carries a (stock, entry_date) key; the sign_map maps
    # those to the sign_type that proposed them.
    rev_nhi_trades: list[tuple] = []  # (corr_mode, return_pct)
    for fyr in fy_results:
        for r in fyr.results:
            sign = fyr.sign_map.get((r.stock_code, r.entry_date))
            if sign == _SIGN:
                rev_nhi_trades.append((r.corr_mode, r.return_pct))

    logger.info("Found {} rev_nhi trades across all FYs", len(rev_nhi_trades))
    if not rev_nhi_trades:
        logger.warning("No rev_nhi trades found — nothing to flip")
        return

    # All-mode aggregate
    all_long  = [r for _, r in rev_nhi_trades]
    all_flip  = [-r for r in all_long]

    # Per corr_mode
    by_mode: dict[str, list[float]] = defaultdict(list)
    for cm, r in rev_nhi_trades:
        by_mode[cm].append(r)

    lines = [
        f"# rev_nhi synthetic-flip probe",
        "",
        f"Probe run: {datetime.date.today()}.  Re-runs regime_sign baseline "
        "(no exclusions), filters to rev_nhi-originated trades only, and "
        "compares original-long return distribution against "
        "synthetic-flipped (`return_pct → −return_pct`).",
        "",
        "## Setup",
        "",
        f"- Sign: `{_SIGN}`",
        f"- Total rev_nhi trades across FY2019-FY2025: **{len(all_long)}**",
        "- Strategy: regime_sign with default min_dr=0.52, ZsTpSl(2.0,2.0,0.3)",
        "- **Caveat**: Synthetic flip only inverts the return sign at close.  "
        "A real short would hit SL when price RISES (not falls), so the "
        "time-to-exit and which trades hit TP-vs-SL would differ.  If this "
        "probe shows a clean positive after flip, the proper next step is "
        "a mirrored short simulator.",
        "",
        "## Aggregate",
        "",
        "| arm | n | mean_r | Sharpe | win% | avg_win | avg_loss | EV check |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        _row("rev_nhi long (original)", _stats(all_long)),
        _row("rev_nhi flipped (synthetic short)", _stats(all_flip)),
        "",
    ]

    # Per corr_mode
    lines += [
        "## Per corr_mode",
        "",
        "| corr_mode | arm | n | mean_r | Sharpe | win% | avg_win | avg_loss | EV check |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cm in ("high", "mid", "low"):
        rs = by_mode.get(cm, [])
        if not rs:
            continue
        lines.append(f"| **{cm}** | long original | " + _row("", _stats(rs)).split("|", 2)[2])
        lines.append(f"| {cm} | flipped       | " + _row("", _stats([-r for r in rs])).split("|", 2)[2])

    lines += [
        "",
        "## Verdict",
        "",
    ]
    orig = _stats(all_long)
    flip = _stats(all_flip)
    delta_sh = (flip["sharpe"] - orig["sharpe"]) \
               if (orig["sharpe"] is not None and flip["sharpe"] is not None) \
               else None
    if orig["sharpe"] is None or flip["sharpe"] is None:
        lines.append("Cannot compute Δ Sharpe (insufficient n).")
    else:
        if flip["sharpe"] > 1.0:
            verdict = (
                "**SIGNAL POINTS WRONG WAY (proceed to mirrored short simulator)**\n\n"
                "Flipped Sharpe is meaningfully positive.  rev_nhi's bearish "
                "framing IS detecting something, but the directional prediction "
                "is inverted — selling-pressure signal is being treated as a "
                "buy trigger.  Next step: build a mirrored short simulator "
                "and re-run to confirm under real TP/SL hit dynamics."
            )
        elif flip["sharpe"] > 0:
            verdict = (
                "**MILD INVERSE EFFECT — borderline**\n\n"
                "Flipped Sharpe is positive but modest.  rev_nhi's signal "
                "value may be marginal at this universe size; building a "
                "short simulator might not be worth the effort.  Consider "
                "leaving rev_nhi UI-hidden (current production state) and "
                "revisit if universe expansion lifts effective n."
            )
        else:
            verdict = (
                "**FLIP DOESN'T HELP — rev_nhi is just noisy**\n\n"
                "Flipped Sharpe is still negative.  rev_nhi isn't a "
                "wrong-direction signal — it's a noisy / unreliable one at "
                "this universe size.  UI-only salvage (current state) is "
                "the right outcome."
            )
        lines.append(verdict)
        lines += [
            "",
            f"- Original long: n={orig['n']}, Sharpe **{orig['sharpe']:+.2f}**, "
            f"win {orig['win_rate']*100:.0f}%, mean_r {orig['mean_r']*100:+.2f}%",
            f"- Flipped:       n={flip['n']}, Sharpe **{flip['sharpe']:+.2f}**, "
            f"win {flip['win_rate']*100:.0f}%, mean_r {flip['mean_r']*100:+.2f}%",
            f"- **Δ Sharpe = {delta_sh:+.2f}**",
        ]

    _REPORT.parent.mkdir(parents=True, exist_ok=True)
    _REPORT.write_text("\n".join(lines) + "\n")
    logger.info("Wrote {}", _REPORT)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
