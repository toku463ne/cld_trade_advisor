"""bullish_confluence_probe — does multi-sign agreement predict EV uplift?

Read-only diagnostic asking: when ≥N bullish signs fire on the same
(stock, date), is the forward outcome better than when only 1 fires?

Motivation: standalone-EV gates have rejected several plausible
breakout signs.  Operator hypothesis (2026-05-17): individual signs
decay quickly, but multi-sign agreement may persist as a stronger
directional bet.  This probe tests whether the confluence framework
is empirically real BEFORE authorizing a new sign (brk_nhi) to feed
into it.

Bullish-set (entry direction = long):
    str_hold, str_lead, str_lag, brk_sma, brk_bol, rev_lo, rev_nlo

For each (stock, date) tuple that has any bullish fire, count the
number of distinct bullish sign types that fired that day.  Dedupe
to one row per (stock, date) — confluence is a date-level property.
Outcome = same trend_direction / trend_magnitude convention as
benchmark.md (next confirmed zigzag, ZZ size=5/mid=2, cap=30 bars).

Pre-registered gate (must all hold):
    EV[≥3 signs] − EV[1 sign] ≥ +0.010 (1pp uplift)
    EV[≥2 signs] − EV[1 sign] ≥ +0.005 (0.5pp uplift)
    Uplift sign consistent in ≥4 of 6 training FYs (FY2019-FY2024
    has data; FY2018 likely empty)
    FY2025 uplift sign matches pooled
    n[≥3 signs] ≥ 50/FY in FY2025 (sample-size floor for the
    cohort the operator would actually use)

If gate passes → confluence framework is real; authorize brk_nhi as
a sign that feeds into it (separate ship cycle).  If gate fails →
the confluence-as-factor pattern doesn't work on existing signs;
adding brk_nhi to a non-functional tally is pointless.

Output: src/analysis/benchmark.md § Bullish Confluence Probe.
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_SECTION_HEADER = "## Bullish Confluence Probe"
_MULTIYEAR_MIN_RUN_ID = 47

_BULLISH_SIGNS = (
    "str_hold", "str_lead", "str_lag",
    "brk_sma", "brk_bol",
    "rev_lo", "rev_nlo",
)

# FY config
_FY_TRAINING = ["FY2019", "FY2020", "FY2021", "FY2022", "FY2023", "FY2024"]
_FY_OOS = "FY2025"

# Pre-registered gate
_GATE_UPLIFT_3 = 0.010
_GATE_UPLIFT_2 = 0.005
_GATE_FY_CONSIST = 4
_GATE_OOS_N_MIN = 50


def _fiscal_label(d: datetime.date) -> str | None:
    y = d.year if d.month >= 4 else d.year - 1
    return f"FY{y}"


# ── 1. Load and dedupe per (stock, date) ──────────────────────────────


@dataclass
class _StockDateRow:
    stock:        str
    fire_date:    datetime.date
    fy:           str
    n_signs:      int
    signs:        frozenset[str]
    trend_dir:    int | None
    trend_mag:    float | None


def _load_and_aggregate() -> pd.DataFrame:
    """Return DataFrame with one row per (stock, date) carrying confluence count."""
    with get_session() as s:
        rows = s.execute(
            select(
                SignBenchmarkRun.sign_type,
                SignBenchmarkEvent.stock_code,
                SignBenchmarkEvent.fired_at,
                SignBenchmarkEvent.trend_direction,
                SignBenchmarkEvent.trend_magnitude,
            )
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(
                SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID,
                SignBenchmarkRun.sign_type.in_(_BULLISH_SIGNS),
                SignBenchmarkEvent.trend_direction.isnot(None),
            )
        ).all()

    logger.info("Loaded {} bullish-sign events", len(rows))

    # Build per (stock, date) aggregation
    agg: dict[tuple[str, datetime.date], dict] = {}
    for sign, stock, fired_at, tdir, tmag in rows:
        d = fired_at.date() if hasattr(fired_at, "date") else fired_at
        key = (stock, d)
        if key not in agg:
            agg[key] = {
                "stock":     stock,
                "fire_date": d,
                "fy":        _fiscal_label(d),
                "signs":     set(),
                # We'll record one trend outcome per stock-date — they
                # measure from the same date so should match across signs.
                # Use the first-seen.
                "trend_dir": int(tdir) if tdir is not None else None,
                "trend_mag": float(tmag) if tmag is not None else None,
            }
        agg[key]["signs"].add(sign)

    df = pd.DataFrame([
        {
            "stock":     v["stock"],
            "fire_date": v["fire_date"],
            "fy":        v["fy"],
            "n_signs":   len(v["signs"]),
            "signs":     ",".join(sorted(v["signs"])),
            "trend_dir": v["trend_dir"],
            "trend_mag": v["trend_mag"],
        }
        for v in agg.values()
    ])
    logger.info("Aggregated to {} unique (stock, date) rows", len(df))
    return df


# ── 2. Confluence buckets + EV ────────────────────────────────────────


def _ev(sub: pd.DataFrame) -> tuple[float, float, int]:
    """(EV, DR, n) on trend_direction × trend_magnitude."""
    n = len(sub)
    if n == 0:
        return (float("nan"), float("nan"), 0)
    with_dir = sub.dropna(subset=["trend_dir"])
    if with_dir.empty:
        return (float("nan"), float("nan"), n)
    dr = (with_dir["trend_dir"] == 1).sum() / len(with_dir)
    flw = with_dir[with_dir["trend_dir"] == 1]["trend_mag"].dropna()
    rev = with_dir[with_dir["trend_dir"] == -1]["trend_mag"].dropna()
    if flw.empty or rev.empty:
        return (float("nan"), dr, n)
    ev = dr * float(flw.mean()) - (1 - dr) * float(rev.mean())
    return (ev, dr, n)


def _bucket(n: int) -> str:
    if n >= 3:
        return "≥3"
    return str(n)


# ── 3. Report ─────────────────────────────────────────────────────────


def _format_report(df: pd.DataFrame) -> tuple[str, bool]:
    df = df.copy()
    df["bucket"] = df["n_signs"].apply(_bucket)
    buckets = ["1", "2", "≥3"]
    fys = _FY_TRAINING + [_FY_OOS]

    lines = [
        f"\n{_SECTION_HEADER}",
        f"\nProbe run: {datetime.date.today()}.  Read-only diagnostic — "
        "does multi-sign confluence on a (stock, date) predict EV uplift "
        "vs single-sign fires?",
        "",
        f"Bullish sign set: {', '.join(_BULLISH_SIGNS)}",
        "",
        "**Pre-registered gate**:",
        f"  - EV[≥3 signs] − EV[1 sign] ≥ +{_GATE_UPLIFT_3*100:.1f}pp",
        f"  - EV[≥2 signs] − EV[1 sign] ≥ +{_GATE_UPLIFT_2*100:.1f}pp",
        f"  - uplift sign consistent in ≥{_GATE_FY_CONSIST} of {len(_FY_TRAINING)} training FYs",
        f"  - FY2025 OOS uplift sign matches pooled training sign",
        f"  - n[≥3 signs] in FY2025 ≥ {_GATE_OOS_N_MIN}",
        "",
        "### Confluence buckets — pooled (training)",
        "",
        "| Bucket | n_train | n_oos | DR (train) | EV (train) | EV (FY2025) | mean signs/day |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    pooled_train: dict[str, tuple[float, float, int]] = {}
    pooled_oos: dict[str, tuple[float, float, int]] = {}
    for b in buckets:
        sub = df[df["bucket"] == b]
        sub_t = sub[sub["fy"].isin(_FY_TRAINING)]
        sub_o = sub[sub["fy"] == _FY_OOS]
        pooled_train[b] = _ev(sub_t)
        pooled_oos[b]   = _ev(sub_o)
        ev_t, dr_t, n_t = pooled_train[b]
        ev_o, _, n_o    = pooled_oos[b]
        mean_signs = float(sub["n_signs"].mean()) if not sub.empty else float("nan")
        ev_o_s = f"{ev_o:+.4f}" if not math.isnan(ev_o) else "—"
        ev_t_s = f"{ev_t:+.4f}" if not math.isnan(ev_t) else "—"
        dr_t_s = f"{dr_t*100:.1f}%" if not math.isnan(dr_t) else "—"
        lines.append(f"| {b} | {n_t} | {n_o} | {dr_t_s} | {ev_t_s} | {ev_o_s} | {mean_signs:.2f} |")

    # Uplifts
    ev_1_t  = pooled_train["1"][0]
    ev_2_t  = pooled_train["2"][0]
    ev_3_t  = pooled_train["≥3"][0]
    ev_1_o  = pooled_oos["1"][0]
    ev_3_o  = pooled_oos["≥3"][0]
    uplift_2 = ev_2_t - ev_1_t if not (math.isnan(ev_2_t) or math.isnan(ev_1_t)) else float("nan")
    uplift_3 = ev_3_t - ev_1_t if not (math.isnan(ev_3_t) or math.isnan(ev_1_t)) else float("nan")
    uplift_3_oos = ev_3_o - ev_1_o if not (math.isnan(ev_3_o) or math.isnan(ev_1_o)) else float("nan")

    lines += [
        "",
        "### Pooled uplifts (training)",
        "",
        f"- EV[≥2 signs] − EV[1 sign] = **{uplift_2*100:+.2f}pp**  "
        f"(gate ≥ +{_GATE_UPLIFT_2*100:.1f}pp)",
        f"- EV[≥3 signs] − EV[1 sign] = **{uplift_3*100:+.2f}pp**  "
        f"(gate ≥ +{_GATE_UPLIFT_3*100:.1f}pp)",
        f"- FY2025 OOS uplift EV[≥3] − EV[1] = **{uplift_3_oos*100:+.2f}pp**",
        "",
        "### Per-FY EV by confluence bucket",
        "",
        "| FY | " + " | ".join([f"EV[{b}] (n)" for b in buckets])
        + " | Uplift[≥3]−[1] |",
        "|----|" + ":---:|" * (len(buckets) + 1),
    ]
    per_fy_uplift_signs: list[float] = []
    for fy in fys:
        cells: list[str] = []
        evs: dict[str, float] = {}
        for b in buckets:
            sub = df[(df["fy"] == fy) & (df["bucket"] == b)]
            e, _, n = _ev(sub)
            cells.append(f"{e:+.4f} (n={n})" if not math.isnan(e) else "—")
            evs[b] = e
        up = (evs["≥3"] - evs["1"]) if not (math.isnan(evs["≥3"]) or math.isnan(evs["1"])) else float("nan")
        cells.append(f"**{up*100:+.2f}pp**" if not math.isnan(up) else "—")
        lines.append(f"| {fy} | " + " | ".join(cells) + " |")
        if fy in _FY_TRAINING and not math.isnan(up):
            per_fy_uplift_signs.append(up)

    # Gate evaluation
    notes: list[str] = []
    ok = True
    if math.isnan(uplift_3) or uplift_3 < _GATE_UPLIFT_3:
        ok = False
        notes.append(f"pooled uplift[≥3] {uplift_3*100:+.2f}pp < +{_GATE_UPLIFT_3*100:.1f}pp")
    if math.isnan(uplift_2) or uplift_2 < _GATE_UPLIFT_2:
        ok = False
        notes.append(f"pooled uplift[≥2] {uplift_2*100:+.2f}pp < +{_GATE_UPLIFT_2*100:.1f}pp")
    if not math.isnan(uplift_3):
        pooled_sign = math.copysign(1, uplift_3)
        consist = sum(1 for u in per_fy_uplift_signs
                      if math.copysign(1, u) == pooled_sign)
        if consist < _GATE_FY_CONSIST:
            ok = False
            notes.append(f"only {consist}/{len(_FY_TRAINING)} training FYs uplift-consistent (<{_GATE_FY_CONSIST})")
        if math.isnan(uplift_3_oos) or math.copysign(1, uplift_3_oos) != pooled_sign:
            ok = False
            notes.append(f"FY2025 uplift sign mismatch ({uplift_3_oos*100:+.2f}pp vs pooled {uplift_3*100:+.2f}pp)")
    n_oos_3 = pooled_oos["≥3"][2]
    if n_oos_3 < _GATE_OOS_N_MIN:
        ok = False
        notes.append(f"FY2025 n[≥3 signs] = {n_oos_3} < {_GATE_OOS_N_MIN}")

    lines += [
        "",
        "### Gate verdict",
        "",
        f"**{'PASS' if ok else 'FAIL'}** — {'all gates clear' if ok else 'gate notes: ' + '; '.join(notes)}",
        "",
    ]

    if ok:
        lines.append(
            "**Confluence framework is empirically real.**  Authorize "
            "brk_nhi as a sign that feeds the confluence tally (separate "
            "ship cycle: add detector → add to bullish-set → re-run this "
            "probe → measure whether brk_nhi inclusion improves uplift). "
            "Decision-factors panel / UI presentation deferred to that cycle."
        )
    else:
        lines.append(
            "**Confluence framework does NOT clear the gate on existing "
            "signs.**  Adding brk_nhi to a non-functional tally is "
            "pointless.  Two interpretations: (a) bullish-sign-set "
            "definition is wrong (try a narrower set), or (b) confluence "
            "as a factor doesn't carry signal on this universe — same "
            "events, same outcomes regardless of co-fire count.  Operator "
            "decision required before next probe."
        )
    return "\n".join(lines), ok


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
    df = _load_and_aggregate()
    if df.empty:
        logger.error("No bullish-sign events found — aborting")
        return
    report, _ok = _format_report(df)
    print(report)
    _append_to_benchmark(report)


if __name__ == "__main__":
    main()
