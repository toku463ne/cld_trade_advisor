"""sign_pick_robustness — does the sign_score tiebreak carry signal, or is
any pick from the EV-tied top group equivalent?

regime_sign_backtest picks the daily entry by argmax of the composite key
(-(regime_ev + sector_bonus), -sign_score, stock). regime_ev is a
(sign, kumo)-cell aggregate, so the daily top-EV group is often several
stocks tied on EV — the argmax then resolves them by sign_score. This probe
A/Bs that argmax pick against picking uniformly at random from the EV-tied
top group, across N seeds, over the full FY2019-FY2025 walk-forward.

- argmax arm  : RS_PICK_MODE unset — byte-identical to regime_sign_backtest.
- random arms : RS_PICK_MODE=random_evtie, RS_PICK_SEED=1..N.

Reading the result:
- if argmax Sharpe sits INSIDE the random arms' spread → the sub-EV tiebreak
  is noise; the recommendation tier is the honest precision level.
- if argmax CLEARS the random spread → the fine ranking carries signal.
- if the top-EV group rarely has >1 member (low tie frequency) → the
  question is moot regardless; the tiebreak seldom has anything to break.

CLI: uv run --env-file devenv python -m src.analysis.sign_pick_robustness
"""

from __future__ import annotations

import datetime
import os
import statistics
from pathlib import Path

from loguru import logger

from src.analysis.exit_benchmark import Metrics, _metrics
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, run_fy
from src.strategy import regime_sign

_OUT_DIR = Path(__file__).parent.parent.parent / "data" / "analysis" / "sign_pick_robustness"
_N_SEEDS = 4
_FLAT_RATE_FYS = {"FY2019", "FY2020", "FY2021"}


def _run_arm(name: str, env: dict[str, str]) -> tuple[dict[str, list], dict[str, int]]:
    """Run the full FY2019-FY2025 walk-forward for one pick arm."""
    logger.info("══ arm: {} ══", name)
    for k in ("RS_PICK_MODE", "RS_PICK_SEED"):
        os.environ.pop(k, None)
    os.environ.update(env)
    regime_sign._PICK_STATS["picks"] = 0
    regime_sign._PICK_STATS["tied_picks"] = 0
    by_fy: dict[str, list] = {}
    for cfg in RS_FY_CONFIGS:
        by_fy[cfg.label] = run_fy(cfg).results
    stats = dict(regime_sign._PICK_STATS)
    for k in ("RS_PICK_MODE", "RS_PICK_SEED"):
        os.environ.pop(k, None)
    return by_fy, stats


def _row(name: str, m: Metrics) -> str:
    if m.n == 0:
        return f"| {name} | — | — | — | — |"
    return (f"| {name} | {m.n} | {m.fmt_mean_r()} | {m.fmt_sharpe()} "
            f"| {m.fmt_win()} |")


def main() -> None:
    arms: dict[str, dict[str, str]] = {"argmax": {}}
    for s in range(1, _N_SEEDS + 1):
        arms[f"rnd_s{s}"] = {"RS_PICK_MODE": "random_evtie", "RS_PICK_SEED": str(s)}

    arm_by_fy: dict[str, dict[str, list]] = {}
    arm_stats: dict[str, dict[str, int]] = {}
    for name, env in arms.items():
        arm_by_fy[name], arm_stats[name] = _run_arm(name, env)

    def _agg(name: str) -> Metrics:
        return _metrics([r for rs in arm_by_fy[name].values() for r in rs])

    argmax_m = _agg("argmax")
    rnd_names = [n for n in arms if n != "argmax"]
    rnd_ms = {n: _agg(n) for n in rnd_names}
    rnd_sharpes = [m.sharpe for m in rnd_ms.values()]
    rnd_means = [m.mean_r for m in rnd_ms.values()]

    # tie frequency — only the random arms instrument _PICK_STATS
    s1 = arm_stats["rnd_s1"]
    tie_pct = (s1["tied_picks"] / s1["picks"] * 100.0) if s1["picks"] else 0.0

    # verdict
    if tie_pct < 5.0:
        verdict = (f"MOOT — the top-EV group had >1 tied member in only "
                   f"{tie_pct:.1f}% of picks; the sign_score tiebreak rarely "
                   "has anything to break")
    elif argmax_m.sharpe > max(rnd_sharpes):
        verdict = ("ARGMAX CARRIES SIGNAL — argmax Sharpe clears the entire "
                   "random-arm spread; the sub-EV ranking matters and the "
                   "coarse tier alone would lose signal")
    elif min(rnd_sharpes) <= argmax_m.sharpe <= max(rnd_sharpes):
        verdict = ("TIEBREAK IS NOISE — argmax Sharpe sits inside the "
                   "random-arm spread; any pick from the EV-tied top group is "
                   "equivalent, so the recommendation tier is the honest "
                   "precision level")
    else:
        verdict = ("ARGMAX UNDERPERFORMS — argmax Sharpe is below the "
                   "random-arm spread (unexpected — investigate)")

    today = datetime.date.today().isoformat()
    md: list[str] = [
        "# Sign-Pick Robustness — argmax vs random-from-EV-tied-top-group",
        "",
        f"Generated: {today}  ",
        "Walk-forward FY2019-FY2025, ZsTpSl(2.0,2.0,0.3) exit, two-bar fill.  ",
        f"argmax = regime_sign_backtest baseline; random arms = {_N_SEEDS} seeds, "
        "RS_PICK_MODE=random_evtie (uniform pick from the EV-tied top group).  ",
        "",
        f"## Verdict: **{verdict}**",
        "",
        f"- Tie frequency: the top-EV group had >1 tied member in "
        f"**{tie_pct:.1f}%** of picks ({s1['tied_picks']}/{s1['picks']}).",
        f"- argmax Sharpe **{argmax_m.fmt_sharpe()}** vs random arms "
        f"min {min(rnd_sharpes):.2f} / mean {statistics.mean(rnd_sharpes):.2f} / "
        f"max {max(rnd_sharpes):.2f}",
        f"- argmax mean_r **{argmax_m.fmt_mean_r()}** vs random mean "
        f"{statistics.mean(rnd_means) * 100:+.2f}% "
        f"[{min(rnd_means) * 100:+.2f}%, {max(rnd_means) * 100:+.2f}%]",
        "",
        "## Aggregate (FY2019-FY2025)",
        "",
        "| arm | n | mean_r | sharpe | win_rate |",
        "|-----|---|--------|--------|----------|",
        _row("argmax (baseline)", argmax_m),
    ]
    for n in rnd_names:
        md.append(_row(n, rnd_ms[n]))
    md += [
        "",
        "## Per-FY — argmax vs random-arm mean",
        "",
        "| FY | argmax n | argmax mean_r | argmax sharpe | rnd mean_r (mean) | rnd sharpe (mean) |",
        "|----|----------|---------------|---------------|-------------------|-------------------|",
    ]
    for cfg in RS_FY_CONFIGS:
        am = _metrics(arm_by_fy["argmax"][cfg.label])
        rms = [_metrics(arm_by_fy[n][cfg.label]) for n in rnd_names]
        rnd_mr = [m.mean_r for m in rms if m.n]
        rnd_sh = [m.sharpe for m in rms if m.n]
        flag = " *(flat-rate)*" if cfg.label in _FLAT_RATE_FYS else ""
        if am.n == 0:
            md.append(f"| {cfg.label}{flag} | — | — | — | — | — |")
        else:
            mr_txt = f"{statistics.mean(rnd_mr) * 100:+.2f}%" if rnd_mr else "—"
            sh_txt = f"{statistics.mean(rnd_sh):.2f}" if rnd_sh else "—"
            md.append(f"| {cfg.label}{flag} | {am.n} | {am.fmt_mean_r()} | "
                      f"{am.fmt_sharpe()} | {mr_txt} | {sh_txt} |")
    md.append("")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _OUT_DIR / f"probe_{today}.md"
    path.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote report to {}", path)
    print("\n".join(md))


if __name__ == "__main__":
    main()
