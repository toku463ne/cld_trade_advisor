"""div_peer × cluster-size per-FY stratification probe.

Pre-registered cluster-size-probe from /sign-debate (2026-05-16):

  - SHIP min_peer_count gate only if size≥3 EV ≥ +0.015 at n ≥ 30
    per FY in EACH of FY2023, FY2024, FY2025 (the §5.1-compliant
    version of the proposer's per-FY gate).
  - Otherwise: hold; surface cluster_size in the UI per Option F
    and let operator discretion decide on a per-trade basis.

The probe joins every historical `div_peer` SignBenchmarkEvent with
the firing stock's cluster size at the run's stock_set (the same
mapping `regime_sign` uses), then aggregates by (cluster_size × FY).

Run:
    uv run --env-file devenv python -m src.analysis.div_peer_cluster_size_probe
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import select

from src.analysis.models import (
    SignBenchmarkEvent,
    SignBenchmarkRun,
    StockClusterMember,
    StockClusterRun,
)
from src.data.db import get_session

# classified201X stock-set → FY label (matches regime_sign convention).
_FY_OF_SET: dict[str, str] = {
    "classified2018": "FY2019",
    "classified2019": "FY2020",
    "classified2020": "FY2021",
    "classified2021": "FY2022",
    "classified2022": "FY2023",
    "classified2023": "FY2024",
    "classified2024": "FY2025",
}


@dataclass
class Cell:
    """Per-(size_bucket × FY) aggregate."""
    n_events:     int   = 0
    n_up:         int   = 0
    sum_mag_flw:  float = 0.0
    sum_mag_rev:  float = 0.0

    @property
    def dr(self) -> float:
        return self.n_up / self.n_events if self.n_events else float("nan")

    @property
    def mag_flw(self) -> float:
        n_flw = self.n_up
        return self.sum_mag_flw / n_flw if n_flw else 0.0

    @property
    def mag_rev(self) -> float:
        n_rev = self.n_events - self.n_up
        return self.sum_mag_rev / n_rev if n_rev else 0.0

    @property
    def ev(self) -> float:
        if self.n_events == 0:
            return float("nan")
        return self.dr * self.mag_flw - (1 - self.dr) * self.mag_rev


def _size_bucket(n_members: int) -> str:
    if n_members <= 2: return "size=2"
    if n_members == 3: return "size=3"
    if n_members == 4: return "size=4"
    return "size≥5"


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    width  = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - width), min(1.0, centre + width)


def main() -> None:
    cells: dict[tuple[str, str], Cell] = defaultdict(Cell)
    cells_pooled: dict[str, Cell]      = defaultdict(Cell)        # by size bucket only
    fy_totals: dict[str, Cell]          = defaultdict(Cell)        # by FY only
    skipped_no_cluster = 0
    skipped_no_direction = 0

    with get_session() as s:
        # All div_peer runs grouped by stock_set
        runs = list(s.execute(
            select(SignBenchmarkRun.id, SignBenchmarkRun.stock_set)
            .where(SignBenchmarkRun.sign_type == "div_peer")
        ).all())
        if not runs:
            print("No div_peer SignBenchmarkRun rows in DB. Run sign_benchmark first.")
            return

        # For each stock_set, build the cluster-size lookup once
        sets = sorted({r.stock_set for r in runs})
        cluster_size_by_set: dict[str, dict[str, int]] = {}
        for sset in sets:
            cr = s.execute(
                select(StockClusterRun.id)
                .where(StockClusterRun.fiscal_year == sset)
                .order_by(StockClusterRun.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if cr is None:
                logger.warning("No StockClusterRun found for {} — events skipped", sset)
                cluster_size_by_set[sset] = {}
                continue
            rows = s.execute(
                select(StockClusterMember.stock_code, StockClusterMember.cluster_id)
                .where(StockClusterMember.run_id == cr)
            ).all()
            # cluster_id → list of stock_codes
            cluster_members: dict[int, list[str]] = defaultdict(list)
            for code, cid in rows:
                cluster_members[cid].append(code)
            # stock_code → cluster size (n_total + 1 since we include the stock itself)
            # NB: DivPeerDetector counts ONLY peers (excludes the stock), so n_peers = size − 1
            # We report cluster_size here for clarity, then the n_peers gate maps to size-1.
            size_by_stock: dict[str, int] = {}
            for cid, codes in cluster_members.items():
                for code in codes:
                    size_by_stock[code] = len(codes)
            cluster_size_by_set[sset] = size_by_stock

        for run in runs:
            fy = _FY_OF_SET.get(run.stock_set, run.stock_set)
            size_lookup = cluster_size_by_set.get(run.stock_set, {})
            events = list(s.execute(
                select(SignBenchmarkEvent.stock_code,
                       SignBenchmarkEvent.trend_direction,
                       SignBenchmarkEvent.trend_magnitude)
                .where(SignBenchmarkEvent.run_id == run.id)
            ).all())
            for code, td, tm in events:
                if td is None or tm is None:
                    skipped_no_direction += 1
                    continue
                csize = size_lookup.get(code)
                if csize is None:
                    skipped_no_cluster += 1
                    continue
                bucket = _size_bucket(csize)
                up = (td == 1)
                for target in (cells[(bucket, fy)], cells_pooled[bucket], fy_totals[fy]):
                    target.n_events += 1
                    if up:
                        target.n_up += 1
                        target.sum_mag_flw += float(tm)
                    else:
                        target.sum_mag_rev += float(tm)

    print("\n" + "="*108)
    print("div_peer × cluster-size × FY stratification")
    print("="*108)
    print(f"  Skipped events (no cluster lookup): {skipped_no_cluster}")
    print(f"  Skipped events (no direction):      {skipped_no_direction}")

    fys = sorted({fy for (_b, fy) in cells} | set(fy_totals))
    buckets = ["size=2", "size=3", "size=4", "size≥5"]

    print("\n— Per-(size × FY) cell: n / DR / EV —")
    header = "  bucket    " + "".join(f"{fy:>22}" for fy in fys) + "     pooled"
    print(header)
    print("  " + "-"*(len(header) - 2))
    for b in buckets:
        cells_row = []
        for fy in fys:
            c = cells.get((b, fy), Cell())
            if c.n_events == 0:
                cells_row.append(f"{'n=0':>22}")
            else:
                s = f"n={c.n_events} DR={c.dr*100:.1f}% EV={c.ev*100:+.2f}%"
                cells_row.append(f"{s:>22}")
        p = cells_pooled.get(b, Cell())
        if p.n_events == 0:
            pooled = "     n=0"
        else:
            pooled = f"  n={p.n_events:>3}  DR={p.dr*100:>5.1f}%  EV={p.ev*100:>+6.2f}%"
        print(f"  {b:<10}" + "".join(cells_row) + pooled)

    print("\n— Per-FY totals (all sizes) —")
    for fy in fys:
        c = fy_totals[fy]
        if c.n_events == 0:
            print(f"  {fy:<10}  n=0")
            continue
        print(f"  {fy:<10}  n={c.n_events:>4}  DR={c.dr*100:>5.1f}%  EV={c.ev*100:>+6.2f}%")

    # ── Pre-registered gate check ──
    print("\n" + "="*108)
    print("PRE-REGISTERED GATE — Option A defensibility")
    print("="*108)
    print("  Per the judge's falsifier:")
    print("    SHIP min_peer_count gate ⇔ size≥3 EV ≥ +0.015 at n ≥ 30 per FY in EACH of")
    print("    FY2023, FY2024, FY2025.")
    target_fys = ["FY2023", "FY2024", "FY2025"]
    target_buckets = ["size=3", "size=4", "size≥5"]
    cell_results = []
    for fy in target_fys:
        # Pool size≥3 within each FY
        n = up = 0
        sum_flw = sum_rev = 0.0
        for b in target_buckets:
            c = cells.get((b, fy), Cell())
            n      += c.n_events
            up     += c.n_up
            sum_flw += c.sum_mag_flw
            sum_rev += c.sum_mag_rev
        if n == 0:
            print(f"  {fy}: size≥3 has n=0 — gate cannot be tested")
            cell_results.append(("FAIL", n, float("nan")))
            continue
        dr = up / n
        ev = dr * (sum_flw / up if up else 0) - (1 - dr) * (sum_rev / (n - up) if n - up else 0)
        n_ok = n >= 30
        ev_ok = ev >= 0.015
        verdict = "PASS" if (n_ok and ev_ok) else "FAIL"
        n_str = "✓" if n_ok else "✗"
        ev_str = "✓" if ev_ok else "✗"
        print(f"  {fy}: size≥3  n={n} {n_str}  DR={dr*100:.1f}%  EV={ev*100:+.2f}% {ev_str}  → {verdict}")
        cell_results.append((verdict, n, ev))

    all_pass = all(r[0] == "PASS" for r in cell_results)
    print()
    if all_pass:
        print("  → GATE CLEARED: size≥3 sub-cohort robust across FY2023/2024/2025.")
        print("    Justifies shipping Option A (min_peer_count=3) after composite walk probe.")
    else:
        n_pass = sum(1 for r in cell_results if r[0] == "PASS")
        print(f"  → GATE FAILED ({n_pass}/3): cluster-size effect is not per-FY robust.")
        print("    Option F (UI-side disclosure, no parameter change) remains the correct path.")
    print("="*108 + "\n")


if __name__ == "__main__":
    main()
