"""Per-FY fire frequency for ALL signs in the catalogue (read-only).

Companion to confluence_sign_combo.py.  The operator wanted the same per-FY
composition view, but across EVERY sign in sign_benchmark (not just the 10
bullish confluence signs) — i.e. which signs were more / less active each year,
to see regime-driven compositional shifts (e.g. did FY2021 fire fewer breakouts
and more mean-reversion across the whole catalogue, not only inside confluence).

Raw fires (one row per SignBenchmarkEvent), bucketed to FY by fired_at.
Reports:
  A. FY x sign matrix of raw fire COUNTS (+ per-FY total).
  B. FY x sign matrix of per-FY SHARE (% of that FY's total fires) — the
     composition view; lets you compare a sign's prominence across years
     independent of how many total fires that year had.

NOT deduped, NOT slot-filtered — this is the raw signal population per sign.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.sign_fire_frequency_by_fy
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

from loguru import logger
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session

_FY_BOUNDS = [(f"FY{y}", datetime.date(y, 4, 1), datetime.date(y + 1, 3, 31))
              for y in range(2018, 2026)]
# break-ish signs (level breakouts) flagged for the operator's hypothesis
_BREAK = {"brk_sma", "brk_bol", "brk_kumo_hi", "brk_tenkan_hi", "brk_wall", "brk_floor"}


def _fy_of(d):
    for lbl, a, b in _FY_BOUNDS:
        if a <= d <= b:
            return lbl
    return None


def run() -> None:
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
        ).all()

    # count[sign][fy]
    count: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    fy_total: dict[str, int] = defaultdict(int)
    for sign, fa in rows:
        d = fa.date() if hasattr(fa, "date") else fa
        fy = _fy_of(d)
        if fy is None:
            continue
        count[sign][fy] += 1
        fy_total[fy] += 1

    signs = sorted(count, key=lambda sg: (sg not in _BREAK, sg))  # break signs first
    fys = [lbl for lbl, _, _ in _FY_BOUNDS if fy_total.get(lbl)]

    def _hdr():
        return f"  {'sign':<16}" + "".join(f"{fy[2:]:>7}" for fy in fys) + f"{'total':>8}{'':>4}"

    print("\n" + "=" * 100)
    print("ALL-SIGN FIRE FREQUENCY by FY — raw fires (one row per benchmark event), "
          "break signs listed first")
    print("=" * 100)

    # A. raw counts
    print("\nA. RAW FIRE COUNTS")
    print(_hdr())
    print("  " + "-" * (len(_hdr()) - 2))
    for sg in signs:
        tot = sum(count[sg].values())
        tag = " *brk" if sg in _BREAK else ""
        print(f"  {sg:<16}" + "".join(f"{count[sg].get(fy,0):>7}" for fy in fys)
              + f"{tot:>8}{tag:>5}")
    print("  " + "-" * (len(_hdr()) - 2))
    print(f"  {'TOTAL':<16}" + "".join(f"{fy_total[fy]:>7}" for fy in fys)
          + f"{sum(fy_total.values()):>8}")

    # B. per-FY share (%)
    print("\nB. PER-FY SHARE (% of that FY's total fires)  — composition view")
    print(_hdr())
    print("  " + "-" * (len(_hdr()) - 2))
    for sg in signs:
        tag = " *brk" if sg in _BREAK else ""
        cells = "".join(f"{100.0*count[sg].get(fy,0)/fy_total[fy]:>6.1f}%" for fy in fys)
        tot = sum(count[sg].values())
        print(f"  {sg:<16}{cells}{100.0*tot/sum(fy_total.values()):>7.1f}%{tag:>5}")

    # C. break-sign share aggregate per FY (the operator's hypothesis, all break signs)
    print("\nC. AGGREGATE BREAK-SIGN SHARE per FY  "
          f"(union of {', '.join(sorted(_BREAK))})")
    print(f"  {'FY':<9}{'break fires':>13}{'total':>9}{'break share':>14}")
    for fy in fys:
        bf = sum(count[sg].get(fy, 0) for sg in _BREAK if sg in count)
        note = "  <-- worst confluence FY" if fy == "FY2021" else ("  OOS" if fy == "FY2025" else "")
        print(f"  {fy:<9}{bf:>13}{fy_total[fy]:>9}{100.0*bf/fy_total[fy]:>13.1f}%{note}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
