"""sign_sector_axis_probe — is TSE sector a non-redundant per-sign confidence factor?

Probe spec (Critic v1a/v1b split, 2026-05-14):

Step 0 (user-run prerequisite, already done): populate `Stock.sector17` via
`uv run --env-file devenv python -m src.data.collect stocks --update`.

This probe is read-only. Two parts:

**v1a — pooled (sign, sector17) effect, with OOS holdout + orthogonality gate.**
- Join SignBenchmarkEvent (run_id ≥ 47) → Stock.sector17.
- For each (sign, sector17) cell: DR, EV vs the SAME sign's leave-one-out
  pool (same sign, *other* sectors) — avoids self-referential ΔDR.
- OOS split: train = fire_date < 2024-04-01 (FY2021-FY2023), test = fire_date
  ≥ 2024-04-01 (FY2024-FY2025). The only executable window — run≥47 data
  starts FY2021 (dev DB ^N225 begins 2020-05-11).
- Per-CELL sector-shuffle permutation test (1000×): shuffle the sector label
  across that sign's events, recompute *that cell's* ΔDR vs its leave-one-out
  pool. p per cell — no max-over-cells multiple-comparisons inflation.
- Dual-axis orthogonality gate: for each surviving cell, re-compute ΔDR within
  N225-corr and USDJPY-corr tertile strata (6 strata total). "holds" requires
  ΔDR ≥ +1pp in ≥ 2 valid strata (n ≥ 60); "collapsed" if ≥ 2 valid strata but
  fewer than 2 survive; "indeterminate" if < 2 valid strata.

**v1b — regime-stratified descriptive table for the 4 largest signs only.**
- str_hold / rev_hi / rev_nhi / rev_lo hold ~70% of events; only they survive
  a sector × ADX-regime split. Reported (not gated): (sign, sector17,
  adx_state) cells with n shown, sub-100 flagged. Everything else is n-starved.

Accept gate (v1a): ≥1 (sign, sector17) cell with train n ≥ 100 AND train ΔDR
≥ +2pp AND ΔEV ≥ 0 AND per-cell shuffle p < 0.05 AND test n ≥ 100 AND test
ΔDR > 0 AND orthogonality "holds".

CLI: uv run --env-file devenv python -m src.analysis.sign_sector_axis_probe
"""

from __future__ import annotations

import datetime
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import (
    N225RegimeSnapshot,
    SignBenchmarkEvent,
    SignBenchmarkRun,
)
from src.analysis.usdjpy_corr_axis_probe import (
    _N225_CODE,
    _USDJPY_CODE,
    _compute_event_corrs,
    _load_bars,
)
from src.data.db import get_session
from src.data.models import Stock

_OUT_DIR = Path(__file__).parent.parent.parent / "data" / "analysis" / "sign_sector_axis"
_BENCH_MD = Path(__file__).parent / "benchmark.md"

_MULTIYEAR_MIN_RUN_ID = 47
_FIRE_MIN_DATE = datetime.date(2020, 6, 1)
_SPLIT_DATE = datetime.date(2024, 4, 1)        # train < this, test >= this
_CELL_MIN_N = 100
_N_SHUFFLES = 1000
_RNG_SEED = 20260514
_DELTA_DR_ACCEPT = 0.020                        # +2pp (§3: n<1000 cells)
_SHUFFLE_P_GATE = 0.05
_ADX_CHOPPY = 20.0
_STRATUM_MIN_N = 60                             # below this, "collapse" is indeterminate
_COLLAPSE_DR = 0.010                            # within-stratum ΔDR < 1pp ⇒ collapsed
_BIG_SIGNS = ("str_hold", "rev_hi", "rev_nhi", "rev_lo")


@dataclass
class _Cell:
    sign: str
    sector: str
    n: int
    dr: float
    ev: float
    pool_n: int
    pool_dr: float
    pool_ev: float
    delta_dr: float
    delta_ev: float


# ──────────────────────────────────────────────────────────────────────────
# Loading
# ──────────────────────────────────────────────────────────────────────────

def _load_events_with_sector() -> pd.DataFrame:
    """SignBenchmarkEvent (run≥47) joined to Stock.sector17 and N225 ADX state."""
    with get_session() as s:
        runs = s.execute(
            select(SignBenchmarkRun).where(SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID)
        ).scalars().all()
        run_map = {r.id: r.sign_type for r in runs}
        sector_map = {
            code: sec
            for code, sec in s.execute(select(Stock.code, Stock.sector17)).all()
        }
        snaps = s.execute(select(N225RegimeSnapshot)).scalars().all()
    if not run_map:
        raise RuntimeError("No multi-year runs found")
    snap_map = {sn.date: sn for sn in snaps}

    run_ids = list(run_map)
    rows: list[dict] = []
    for i in range(0, len(run_ids), 500):
        chunk = run_ids[i:i + 500]
        with get_session() as s:
            evts = s.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(chunk))
            ).scalars().all()
        for e in evts:
            if e.trend_direction is None or e.trend_magnitude is None:
                continue
            d = e.fired_at.date()
            if d < _FIRE_MIN_DATE:
                continue
            sector = sector_map.get(e.stock_code)
            if not sector or sector == "-":
                continue
            snap = snap_map.get(d)
            adx_state = "unknown"
            if snap and snap.adx is not None and snap.adx_pos is not None and snap.adx_neg is not None:
                if snap.adx < _ADX_CHOPPY:
                    adx_state = "choppy"
                elif snap.adx_pos > snap.adx_neg:
                    adx_state = "bull"
                else:
                    adx_state = "bear"
            rows.append({
                "sign": run_map[e.run_id],
                "stock": e.stock_code,
                "fire_date": d,
                "dir": int(e.trend_direction),
                "mag": float(e.trend_magnitude),
                "sector": sector,
                "adx_state": adx_state,
            })
    df = pd.DataFrame(rows)
    logger.info("Loaded {:,} sector-labelled events", len(df))
    return df


# ──────────────────────────────────────────────────────────────────────────
# Cell stats with leave-one-out same-sign pool
# ──────────────────────────────────────────────────────────────────────────

def _ev(sub: pd.DataFrame) -> tuple[float, float]:
    n = len(sub)
    if n == 0:
        return float("nan"), float("nan")
    dr = float((sub["dir"] == 1).mean())
    flw = sub[sub["dir"] == 1]["mag"]
    rev = sub[sub["dir"] == -1]["mag"]
    mag_flw = float(flw.mean()) if len(flw) else 0.0
    mag_rev = float(rev.mean()) if len(rev) else 0.0
    return dr, dr * mag_flw - (1 - dr) * mag_rev


def _cell_stats(df: pd.DataFrame) -> list[_Cell]:
    out: list[_Cell] = []
    for (sign, sector), sub in df.groupby(["sign", "sector"]):
        dr, ev = _ev(sub)
        pool = df[(df["sign"] == sign) & (df["sector"] != sector)]
        pdr, pev = _ev(pool)
        out.append(_Cell(
            sign=sign, sector=sector, n=len(sub), dr=dr, ev=ev,
            pool_n=len(pool), pool_dr=pdr, pool_ev=pev,
            delta_dr=dr - pdr, delta_ev=ev - pev,
        ))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Per-sign sector-shuffle permutation test
# ──────────────────────────────────────────────────────────────────────────

def _per_cell_shuffle_p(df: pd.DataFrame) -> dict[tuple[str, str], float]:
    """Per-cell sector-shuffle. For each (sign, sector) cell with n ≥ _CELL_MIN_N,
    shuffle the sector labels across that sign's events and recompute *that
    cell's* ΔDR vs its leave-one-out pool. p = fraction of perms with shuffled
    ΔDR ≥ observed. Honest per-cell p — no max-over-cells inflation. Permuting
    labels preserves each sector's event count, so the LOO pool size is fixed."""
    rng = np.random.default_rng(_RNG_SEED)
    out: dict[tuple[str, str], float] = {}
    for sign, sub in df.groupby("sign"):
        dirs = (sub["dir"].to_numpy() == 1).astype(float)
        sectors = sub["sector"].to_numpy()
        total_n = len(dirs)
        total_flw = float(dirs.sum())
        uniq = [s for s in np.unique(sectors)
                if int((sectors == s).sum()) >= _CELL_MIN_N]
        if not uniq:
            continue

        def _dds(sec_arr: np.ndarray) -> dict[str, float]:
            res: dict[str, float] = {}
            for sec in uniq:
                mask = sec_arr == sec
                cn = int(mask.sum())
                pool_n = total_n - cn
                if pool_n == 0:
                    continue
                cell_dr = float(dirs[mask].mean())
                pool_dr = (total_flw - dirs[mask].sum()) / pool_n
                res[sec] = cell_dr - pool_dr
            return res

        observed = _dds(sectors)
        ge = {sec: 0 for sec in observed}
        for _ in range(_N_SHUFFLES):
            for sec, dd in _dds(sectors[rng.permutation(total_n)]).items():
                if dd >= observed[sec]:
                    ge[sec] += 1
        for sec, c in ge.items():
            out[(sign, sec)] = c / _N_SHUFFLES
    return out


# ──────────────────────────────────────────────────────────────────────────
# Dual-axis orthogonality gate
# ──────────────────────────────────────────────────────────────────────────

def _tertile_labels(values: pd.Series) -> pd.Series:
    q1, q2 = np.percentile(values.dropna().to_numpy(), [33.333, 66.667])
    return pd.cut(values, bins=[-1.01, q1, q2, 1.01], labels=["L", "M", "H"]).astype(str)


def _orthogonality(df: pd.DataFrame, cell: _Cell) -> tuple[str, list[str]]:
    """Re-test the cell's ΔDR within N225-corr and USDJPY-corr tertile strata
    (6 strata total). "holds" requires ΔDR ≥ +1pp in ≥ 2 valid strata (each
    with in-cell n ≥ _STRATUM_MIN_N); "collapsed" if ≥ 2 valid strata but < 2
    survive; "indeterminate" if < 2 valid strata."""
    notes: list[str] = []
    sign_df = df[df["sign"] == cell.sign]
    valid: list[float] = []
    for axis, col in (("N225", "n_bucket"), ("USDJPY", "u_bucket")):
        strata_dd: list[tuple[str, int, float]] = []
        for stratum in ("L", "M", "H"):
            s_df = sign_df[sign_df[col] == stratum]
            in_cell = s_df[s_df["sector"] == cell.sector]
            pool = s_df[s_df["sector"] != cell.sector]
            if len(in_cell) < _STRATUM_MIN_N or len(pool) == 0:
                strata_dd.append((stratum, len(in_cell), float("nan")))
                continue
            dd = float((in_cell["dir"] == 1).mean()) - float((pool["dir"] == 1).mean())
            strata_dd.append((stratum, len(in_cell), dd))
            valid.append(dd)
        detail = ", ".join(
            f"{st}(n={n} ΔDR={dd*100:+.1f}pp)" if not math.isnan(dd) else f"{st}(n={n} —)"
            for st, n, dd in strata_dd
        )
        notes.append(f"{axis}: {detail}")
    surviving = [dd for dd in valid if dd >= _COLLAPSE_DR]
    if len(valid) < 2:
        status = "indeterminate"
    elif len(surviving) >= 2:
        status = "holds"
    else:
        status = "collapsed"
    notes.insert(0, f"{len(valid)} valid strata (n≥{_STRATUM_MIN_N}), "
                    f"{len(surviving)} with ΔDR≥+1pp → {status}")
    return status, notes


# ──────────────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────────────

def _fmt_cell_table(cells: list[_Cell], min_n: int) -> list[str]:
    out = [
        "| sign | sector17 | n | DR | EV | pool_n | pool_DR | ΔDR | ΔEV |",
        "|------|----------|--:|---:|---:|-------:|--------:|----:|----:|",
    ]
    for c in sorted(cells, key=lambda x: -x.delta_dr):
        if c.n < min_n:
            continue
        out.append(
            f"| {c.sign} | {c.sector} | {c.n} | {c.dr*100:.1f}% | {c.ev*100:+.2f}pp | "
            f"{c.pool_n} | {c.pool_dr*100:.1f}% | **{c.delta_dr*100:+.2f}pp** | {c.delta_ev*100:+.2f}pp |"
        )
    return out


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    events = _load_events_with_sector()

    # corr axes for the orthogonality gate
    n225_ret = _load_bars(_N225_CODE).pct_change()
    usdjpy_ret_shifted = _load_bars(_USDJPY_CODE).pct_change().shift(1)
    events = _compute_event_corrs(events, n225_ret, usdjpy_ret_shifted)
    events = events.dropna(subset=["corr_n225", "corr_usdjpy"]).copy()
    events["n_bucket"] = _tertile_labels(events["corr_n225"])
    events["u_bucket"] = _tertile_labels(events["corr_usdjpy"])
    logger.info("Events with sector + both corrs: {:,}", len(events))

    train = events[events["fire_date"] < _SPLIT_DATE].copy()
    test = events[events["fire_date"] >= _SPLIT_DATE].copy()
    logger.info("Split @ {}: train={:,}  test={:,}", _SPLIT_DATE, len(train), len(test))

    # ── v1a ──
    train_cells = _cell_stats(train)
    test_idx = {(c.sign, c.sector): c for c in _cell_stats(test)}

    discovered = [c for c in train_cells
                  if c.n >= _CELL_MIN_N and c.delta_dr >= _DELTA_DR_ACCEPT and c.delta_ev >= 0]
    discovered.sort(key=lambda c: -c.delta_dr)
    logger.info("Discovered train cells (n≥{}, ΔDR≥+2pp, ΔEV≥0): {}",
                _CELL_MIN_N, len(discovered))

    logger.info("Running per-cell sector-shuffle ({} perms)…", _N_SHUFFLES)
    shuffle_p = _per_cell_shuffle_p(train)

    survivors: list[tuple[_Cell, _Cell | None, float, str, list[str]]] = []
    for c in discovered:
        p = shuffle_p.get((c.sign, c.sector), float("nan"))
        tc = test_idx.get((c.sign, c.sector))
        ortho, notes = _orthogonality(events, c)
        survivors.append((c, tc, p, ortho, notes))

    # accept gate: per-cell shuffle p<0.05 ∧ test n≥100 ∧ test ΔDR>0 ∧ orthogonality holds
    accepted = [
        (c, tc, p, ortho)
        for c, tc, p, ortho, _ in survivors
        if not math.isnan(p) and p < _SHUFFLE_P_GATE
        and tc is not None and tc.n >= _CELL_MIN_N
        and not math.isnan(tc.delta_dr) and tc.delta_dr > 0
        and ortho == "holds"
    ]
    sig_cells = [s for s in survivors if not math.isnan(s[2]) and s[2] < _SHUFFLE_P_GATE]
    redundant = [(c, ortho) for c, _, p, ortho, _ in survivors
                 if not math.isnan(p) and p < _SHUFFLE_P_GATE and ortho == "collapsed"]

    if accepted:
        verdict = "ACCEPT (probe-first) — sector is a non-redundant per-sign factor"
    elif discovered and not sig_cells:
        verdict = "REJECT — no discovered cell clears the per-cell shuffle test"
    elif redundant and not accepted:
        verdict = "REJECT — surviving cells collapse into corr_mode (redundant)"
    elif discovered:
        verdict = "INSUFFICIENT — cells clear in-sample but fail OOS (test n<100 or ΔDR≤0) or orthogonality"
    else:
        verdict = "REJECT — no (sign, sector17) cell clears n≥100 ∧ ΔDR≥+2pp"

    # ── v1b: regime-stratified descriptive table, big signs only ──
    v1b_rows: list[str] = [
        "| sign | sector17 | adx_state | n | DR | EV |",
        "|------|----------|-----------|--:|---:|---:|",
    ]
    big = events[events["sign"].isin(_BIG_SIGNS)]
    for (sign, sector, adx), sub in big.groupby(["sign", "sector", "adx_state"]):
        dr, ev = _ev(sub)
        flag = "" if len(sub) >= _CELL_MIN_N else " ⚠n<100"
        v1b_rows.append(
            f"| {sign} | {sector} | {adx}{flag} | {len(sub)} | {dr*100:.1f}% | {ev*100:+.2f}pp |"
        )

    # ── write ──
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    path = _OUT_DIR / f"probe_{today}.md"
    md: list[str] = [
        "# Sign × Sector Axis Probe (v1a + v1b)",
        "",
        f"Generated: {today}  ",
        f"Source: SignBenchmarkEvent run_id ≥ {_MULTIYEAR_MIN_RUN_ID}, joined to Stock.sector17.  ",
        f"OOS split: train fire_date < {_SPLIT_DATE} ({len(train):,}), "
        f"test ≥ {_SPLIT_DATE} ({len(test):,}).  ",
        "Baseline: leave-one-out same-sign, other-sector pool.  ",
        f"Shuffle: per-CELL sector-label permutation, {_N_SHUFFLES} perms.  ",
        "",
        f"## Verdict: **{verdict}**",
        "",
        f"- Discovered train cells (n≥{_CELL_MIN_N}, ΔDR≥+2pp, ΔEV≥0): **{len(discovered)}**",
        f"- Cleared per-cell shuffle p<{_SHUFFLE_P_GATE}: **{len(sig_cells)}**",
        f"- ACCEPTED (shuffle ∧ test n≥{_CELL_MIN_N} ∧ test ΔDR>0 ∧ orthogonality holds): **{len(accepted)}**",
        f"- Flagged redundant with corr_mode: **{len(redundant)}**",
        "",
        "## v1a — discovered cells: train vs OOS vs orthogonality",
        "",
        "| sign | sector17 | train n | train ΔDR | train ΔEV | shuffle p | test n | test ΔDR | orthogonality |",
        "|------|----------|--------:|----------:|----------:|----------:|-------:|---------:|---------------|",
    ]
    for c, tc, p, ortho, notes in survivors:
        tn = tc.n if tc else 0
        tdd = f"{tc.delta_dr*100:+.2f}pp" if tc and not math.isnan(tc.delta_dr) else "—"
        pstr = f"{p:.3f}" if not math.isnan(p) else "—"
        md.append(
            f"| {c.sign} | {c.sector} | {c.n} | {c.delta_dr*100:+.2f}pp | "
            f"{c.delta_ev*100:+.2f}pp | {pstr} | {tn} | {tdd} | {ortho} |"
        )
    md.append("")
    md.append("### Orthogonality detail (per discovered cell)")
    md.append("")
    for c, _, _, ortho, notes in survivors:
        md.append(f"- **{c.sign} × {c.sector}** → {ortho}")
        for nt in notes:
            md.append(f"  - {nt}")
    md += [
        "",
        f"## v1a — all (sign, sector17) cells with n ≥ {_CELL_MIN_N} (full window)",
        "",
    ]
    md += _fmt_cell_table(_cell_stats(events), _CELL_MIN_N)
    md += [
        "",
        f"## v1b — regime-stratified table, 4 largest signs only ({', '.join(_BIG_SIGNS)})",
        "",
        "Descriptive only (not gated). Cells flagged ⚠ have n<100 — read as n-starved.",
        "",
    ]
    md += v1b_rows
    md.append("")
    path.write_text("\n".join(md), encoding="utf-8")
    logger.info("Wrote report to {}", path)

    with _BENCH_MD.open("a", encoding="utf-8") as f:
        f.write("\n".join([
            "", "---", "",
            f"## Sign × Sector Axis Probe — {today}",
            "",
            f"Probe-only. Full table at `{path.relative_to(Path(__file__).parent.parent.parent)}`.  ",
            f"Verdict: **{verdict}**  ",
            f"Discovered train cells: {len(discovered)}; accepted (shuffle ∧ OOS ∧ "
            f"orthogonality): {len(accepted)}; redundant with corr_mode: {len(redundant)}.",
            "",
        ]))
    logger.info("Appended summary to {}", _BENCH_MD)

    print("\n=== SIGN × SECTOR AXIS PROBE ===")
    print(verdict)
    print(f"  - discovered train cells: {len(discovered)}")
    print(f"  - accepted: {len(accepted)}  redundant: {len(redundant)}")


if __name__ == "__main__":
    main()
