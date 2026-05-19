"""regime_sign_trend_score_ceiling_ab — Stage 1 path A (ceiling) for regime_sign.

Stage 0 ([[project-trend-score-stage0]]) identified four regime_sign-relevant
signs whose D8-D10 (trend_score > ~75) decile is materially weaker than
their D1-D7 average — semantic anti-trend signs that fire best when the
multi-feature trend is NOT bullish: **rev_nlo, rev_hi, str_lead,
str_lag**.

Arms
----
- A baseline = current regime_sign run (no filter).
- B +ceiling = same, but drop proposals whose sign ∈ ceiling AND
  trend_score at fired_at date > 75.

If trend_score is missing for the (stock, date), the proposal is KEPT
(missing-score baseline equivalence — same convention as the floor A/B).

Output: docs/analysis/trend_score_stage1.md § Regime_sign ceiling A/B
        + src/analysis/benchmark.md mirror

Pre-registered ship gate (locked 2026-05-19 before run):
  - avg Sharpe (across FY2019-FY2025) in B ≥ A
  - ≥ 5 / 7 FYs non-negative ΔSharpe
  - FY2024 + FY2025 both non-negative ΔSharpe (holdout)
"""
from __future__ import annotations

import datetime
import statistics
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

import src.analysis.regime_sign_backtest as rsb
from src.analysis._marginal import compute_marginal, marginal_table
from src.analysis._trend_score import compute_trend_score
from src.analysis.exit_benchmark import _metrics
from src.data.db import get_session
from src.simulator.cache import DataCache
from src.strategy.proposal import SignalProposal

_DOC_PATH = Path("docs/analysis/trend_score_stage1.md")
_SECTION  = "## Regime_sign ceiling A/B (rev_nlo, rev_hi, str_lead, str_lag ≤ 75)"

_CEILING_SIGNS = frozenset({"rev_nlo", "rev_hi", "str_lead", "str_lag"})
_CEILING_SCORE = 75.0

# Score cache needs ~500 trading days of lookback → 900 calendar days.
_LOOKBACK_DAYS = 900


@dataclass
class _ArmRow:
    fy:        str
    n_trades:  int
    n_props:   int
    mean_r:    float | None
    sharpe:    float | None
    win_rate:  float | None
    hold_bars: float | None
    sortino:   float | None = None
    avg_win:   float | None = None
    avg_loss:  float | None = None


def _row_from_metrics(fy: str, n_props: int, m) -> _ArmRow:
    import math
    safe = lambda v: v if not math.isnan(v) else None
    return _ArmRow(
        fy=fy, n_trades=m.n, n_props=n_props,
        mean_r=m.mean_r if m.n > 0 else None,
        sharpe=safe(m.sharpe) if m.n > 0 else None,
        win_rate=m.win_rate if m.n > 0 else None,
        hold_bars=m.hold_bars if m.n > 0 else None,
        sortino=safe(m.sortino) if m.n > 0 else None,
        avg_win=m.avg_win if (m.n > 0 and m.avg_win != 0.0) else None,
        avg_loss=m.avg_loss if (m.n > 0 and m.avg_loss != 0.0) else None,
    )


def _build_score_map_for_fy(cfg) -> dict[str, dict[datetime.date, float]]:
    """Load stock caches with ~900 days lookback and compute trend_score."""
    # _stocks_for_fy lives in confluence_strategy_backtest — reuse it.
    from src.analysis.confluence_strategy_backtest import _stocks_for_fy
    codes = _stocks_for_fy(cfg.stock_set)
    span_s = cfg.start - datetime.timedelta(days=_LOOKBACK_DAYS)
    span_e = cfg.end   + datetime.timedelta(days=60)
    s_dt = datetime.datetime.combine(span_s, datetime.time.min, tzinfo=datetime.timezone.utc)
    e_dt = datetime.datetime.combine(span_e, datetime.time.max, tzinfo=datetime.timezone.utc)
    out: dict[str, dict[datetime.date, float]] = {}
    with get_session() as s:
        for i, code in enumerate(codes):
            c = DataCache(code, "1d")
            try:
                c.load(s, s_dt, e_dt)
            except Exception as exc:
                logger.warning("  {}: load failed — {}", code, exc)
                continue
            if not c.bars:
                continue
            out[code] = compute_trend_score(c)
    n_obs = sum(len(v) for v in out.values())
    logger.info("  [{}] score_map: {} obs across {} stocks",
                cfg.label, n_obs, sum(1 for v in out.values() if v))
    return out


def _make_proposal_filter(score_map: dict[str, dict[datetime.date, float]]):
    def _filter(p: SignalProposal) -> bool:
        if p.sign_type not in _CEILING_SIGNS:
            return True
        d  = p.fired_at.date()
        ts = score_map.get(p.stock_code, {}).get(d)
        if ts is None:
            return True
        return ts <= _CEILING_SCORE
    return _filter


def _run_arms() -> tuple[list[_ArmRow], list[_ArmRow], list, list]:
    a_rows: list[_ArmRow] = []
    b_rows: list[_ArmRow] = []
    a_results: list = []
    b_results: list = []
    for cfg in rsb.RS_FY_CONFIGS:
        logger.info("=== {} ===", cfg.label)
        # Build score_map once per FY (load is the expensive part).
        score_map = _build_score_map_for_fy(cfg)

        # Arm A — baseline (no filter)
        rsb.PROPOSAL_FILTER = None
        logger.info("  -- arm A (baseline)")
        res_a = rsb.run_fy(cfg)
        m_a = _metrics(res_a.results)
        a_rows.append(_row_from_metrics(cfg.label, res_a.n_proposals, m_a))
        a_results.extend(res_a.results)

        # Arm B — ceiling filter
        rsb.PROPOSAL_FILTER = _make_proposal_filter(score_map)
        logger.info("  -- arm B (+ceiling)")
        res_b = rsb.run_fy(cfg)
        m_b = _metrics(res_b.results)
        b_rows.append(_row_from_metrics(cfg.label, res_b.n_proposals, m_b))
        b_results.extend(res_b.results)

        rsb.PROPOSAL_FILTER = None  # leave clean for any subsequent caller
    return a_rows, b_rows, a_results, b_results


def _format_report(a_rows, b_rows, a_results, b_results) -> str:
    lines = [
        "",
        _SECTION,
        "",
        f"Probe run: {datetime.date.today()}.  Stage 1 path A for "
        "trend_score: drop ceiling-sign proposals when trend_score > "
        f"{_CEILING_SCORE:.0f}.",
        "",
        f"- **Ceiling signs**: {', '.join(sorted(_CEILING_SIGNS))}",
        f"- **Ceiling**: trend_score > {_CEILING_SCORE:.0f} → proposal dropped",
        "- **Score**: 5-feature 250-bar pct-rank per stock "
        "(`src.analysis._trend_score`)",
        "- Missing-score proposals are KEPT (same convention as floor A/B)",
        "",
        "### Per-FY",
        "",
        "| FY | A trades | A Sh | A mean_r | B trades | B Sh | B mean_r | ΔSh | ΔmeanR |",
        "|----|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    by_a = {r.fy: r for r in a_rows}
    by_b = {r.fy: r for r in b_rows}
    for fy in sorted(by_a):
        ra = by_a[fy]
        rb = by_b.get(fy)
        if rb is None:
            continue
        d_sh = ((rb.sharpe - ra.sharpe)
                if (rb.sharpe is not None and ra.sharpe is not None) else None)
        d_mr = ((rb.mean_r - ra.mean_r)
                if (rb.mean_r is not None and ra.mean_r is not None) else None)
        f_s = lambda v: "—" if v is None else f"{v:+.2f}"
        f_m = lambda v: "—" if v is None else f"{v*100:+.2f}%"
        lines.append(
            f"| {fy} | {ra.n_trades} | {f_s(ra.sharpe)} | {f_m(ra.mean_r)}"
            f" | {rb.n_trades} | {f_s(rb.sharpe)} | {f_m(rb.mean_r)}"
            f" | **{f_s(d_sh)}** | **{f_m(d_mr)}** |"
        )

    def _agg(rows: list[_ArmRow]):
        sh = [r.sharpe for r in rows if r.sharpe is not None]
        mr = [r.mean_r for r in rows if r.mean_r is not None]
        return (sum(r.n_trades for r in rows),
                statistics.mean(sh) if sh else None,
                statistics.mean(mr) if mr else None)

    n_a, sh_a, mr_a = _agg(a_rows)
    n_b, sh_b, mr_b = _agg(b_rows)
    d_sh = (sh_b - sh_a) if (sh_a is not None and sh_b is not None) else None
    d_mr = (mr_b - mr_a) if (mr_a is not None and mr_b is not None) else None
    f_s = lambda v: "—" if v is None else f"{v:+.2f}"
    f_m = lambda v: "—" if v is None else f"{v*100:+.2f}%"

    lines += [
        "",
        "### Aggregate (FY-equal-weighted)",
        "",
        f"- A baseline: total trades {n_a}, avg Sharpe {f_s(sh_a)}, "
        f"avg mean_r {f_m(mr_a)}",
        f"- B +ceiling: total trades {n_b}, avg Sharpe {f_s(sh_b)}, "
        f"avg mean_r {f_m(mr_b)}",
        f"- **ΔSharpe = {f_s(d_sh)}** ; **ΔmeanR = {f_m(d_mr)}**",
        "",
    ]

    def _ev_row(label: str, rows: list[_ArmRow]) -> str:
        sh = [r.sharpe   for r in rows if r.sharpe   is not None]
        so = [r.sortino  for r in rows if r.sortino  is not None]
        pw = [r.win_rate for r in rows if r.win_rate is not None]
        aw = [r.avg_win  for r in rows if r.avg_win  is not None]
        al = [r.avg_loss for r in rows if r.avg_loss is not None]
        avg_sh = statistics.mean(sh) if sh else None
        avg_so = statistics.mean(so) if so else None
        avg_pw = statistics.mean(pw) if pw else None
        avg_aw = statistics.mean(aw) if aw else None
        avg_al = statistics.mean(al) if al else None
        ev_chk = (avg_pw * avg_aw + (1 - avg_pw) * avg_al) \
                 if (avg_pw is not None and avg_aw is not None and avg_al is not None) \
                 else None
        f_v = lambda v, p=False: ("—" if v is None
                                   else (f"{v*100:+.2f}%" if p else f"{v:+.2f}"))
        return (f"| {label} | {f_v(avg_sh)} | **{f_v(avg_so)}** | "
                f"{'—' if avg_pw is None else f'{avg_pw*100:.1f}%'} | "
                f"{f_v(avg_aw, p=True)} | {f_v(avg_al, p=True)} | "
                f"{f_v(ev_chk, p=True)} |")

    lines += [
        "### Sortino + EV decomposition",
        "",
        "| arm | Sharpe | Sortino | P(win) | avg_win | avg_loss | EV check |",
        "|-----|---:|---:|---:|---:|---:|---:|",
        _ev_row("A baseline", a_rows),
        _ev_row("B +ceiling", b_rows),
        "",
    ]

    if a_results and b_results:
        m = compute_marginal(a_results, b_results)
        lines.append("### Marginal contribution (B vs A)")
        lines.append(marginal_table(m, a_label="A baseline", b_label="B +ceiling"))

    lines += [
        "",
        "### Ship gate",
        "",
        "Pre-registered (locked before run):",
        "- avg Sharpe (FY-equal-weighted) in B ≥ A",
        "- ≥ 5 / 7 FYs non-negative ΔSharpe",
        "- FY2024 + FY2025 both non-negative ΔSharpe (holdout)",
        "",
    ]
    return "\n".join(lines)


def _append_to_doc(md: str) -> None:
    _DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = _DOC_PATH.read_text() if _DOC_PATH.exists() else \
               "# trend_score Stage 1 — A/B reports\n"
    if _SECTION in existing:
        idx = existing.index(_SECTION)
        rest = existing[idx + len(_SECTION):]
        nxt = rest.find("\n## ")
        existing = (existing[:idx].rstrip() + "\n") if nxt < 0 \
                   else (existing[:idx].rstrip() + "\n" + rest[nxt:].lstrip("\n"))
    _DOC_PATH.write_text(existing.rstrip() + "\n" + md)
    logger.info("Appended report to {}", _DOC_PATH)


def main() -> None:
    a_rows, b_rows, a_results, b_results = _run_arms()
    report = _format_report(a_rows, b_rows, a_results, b_results)
    print(report)
    _append_to_doc(report)


if __name__ == "__main__":
    main()
