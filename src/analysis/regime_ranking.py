"""regime_ranking — Build sign × Kumo regime ranking table from benchmark DB.

Usage
-----
    from src.analysis.regime_ranking import build_regime_ranking, RankEntry
    from src.data.db import get_session

    with get_session() as session:
        ranking = build_regime_ranking(session, run_ids=[47, 48, ..., 137])

    # ranking[(sign_type, kumo_state)] → RankEntry
    entry = ranking.get(("str_hold", -1))
    if entry:
        print(entry.bench_flw, entry.dr, entry.n)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.models import N225RegimeSnapshot, SignBenchmarkEvent, SignBenchmarkRun


@dataclass(frozen=True)
class RankEntry:
    """Benchmark statistics for one (sign, kumo_state) cell."""
    sign_type:  str
    kumo_state: int    # +1 / 0 / -1
    n:          int
    dr:         float  # direction rate (fraction of events where trend_direction == +1)
    mag_flw:    float  # mean magnitude for follow-through events
    bench_flw:  float  # dr × mag_flw  (primary ranking metric)


# Signs whose ADX state is required to be "bear" (ADX≥20 and −DI > +DI).
# Events outside this state are not vetoed at ranking-build time — the veto
# is applied at propose() time by RegimeSignStrategy.
ADX_VETO: dict[str, str] = {
    "str_lead": "bear",
    "rev_nlo":  "bear",
}

_ADX_CHOPPY = 20.0


def build_regime_ranking(
    session: Session,
    run_ids: list[int],
    min_n:  int   = 30,
    min_dr: float = 0.0,
) -> dict[tuple[str, int], RankEntry]:
    """Return {(sign_type, kumo_state): RankEntry} from benchmark event history.

    Args:
        session:  DB session.
        run_ids:  SignBenchmarkRun IDs to include (multi-year runs recommended).
        min_n:    Minimum events in a (sign, kumo) cell to include.
        min_dr:   Minimum direction rate required to include a cell.  Cells
                  where DR ≤ min_dr are excluded (near-random or negative-edge
                  sign/regime combinations).  Default 0.0 (no filter).

    Returns:
        Dict keyed by (sign_type, kumo_state).  Only cells with n >= min_n and
        dr > min_dr are included; cells where mag_flw is undefined are skipped.
    """
    # Load sign metadata
    runs = session.execute(
        select(SignBenchmarkRun.id, SignBenchmarkRun.sign_type)
        .where(SignBenchmarkRun.id.in_(run_ids))
    ).all()
    run_to_sign: dict[int, str] = {r.id: r.sign_type for r in runs}

    # Load snapshots into a date → kumo_state map
    snaps = session.execute(
        select(N225RegimeSnapshot.date, N225RegimeSnapshot.kumo_state)
        .where(N225RegimeSnapshot.kumo_state.isnot(None))
    ).all()
    snap_map: dict = {s.date: s.kumo_state for s in snaps}

    # Load events in batches
    batch_size = 500
    run_id_list = list(run_to_sign)

    # {(sign_type, kumo_state): [(direction, magnitude), ...]}
    cells: dict[tuple[str, int], list[tuple[int, float | None]]] = {}

    for i in range(0, len(run_id_list), batch_size):
        chunk = run_id_list[i:i + batch_size]
        events = session.execute(
            select(
                SignBenchmarkEvent.run_id,
                SignBenchmarkEvent.fired_at,
                SignBenchmarkEvent.trend_direction,
                SignBenchmarkEvent.trend_magnitude,
            )
            .where(
                SignBenchmarkEvent.run_id.in_(chunk),
                SignBenchmarkEvent.trend_direction.isnot(None),
            )
        ).all()

        for e in events:
            sign = run_to_sign.get(e.run_id)
            if sign is None:
                continue
            d = e.fired_at.date() if hasattr(e.fired_at, "date") else e.fired_at
            kumo = snap_map.get(d)
            if kumo is None:
                continue
            key = (sign, int(kumo))
            cells.setdefault(key, []).append((e.trend_direction, e.trend_magnitude))

    # Aggregate
    result: dict[tuple[str, int], RankEntry] = {}
    for (sign, kumo), evts in cells.items():
        n = len(evts)
        if n < min_n:
            continue
        k = sum(1 for d, _ in evts if d == 1)
        dr = k / n
        if dr <= min_dr:
            continue

        flw_mags = [m for d, m in evts if d == 1 and m is not None and not math.isnan(m)]
        if not flw_mags:
            continue
        mag_flw   = float(np.mean(flw_mags))
        bench_flw = dr * mag_flw

        result[(sign, kumo)] = RankEntry(
            sign_type=sign, kumo_state=kumo,
            n=n, dr=dr, mag_flw=mag_flw, bench_flw=bench_flw,
        )

    return result


def rank_for_regime(
    ranking: dict[tuple[str, int], RankEntry],
    kumo_state: int,
    adx: float,
    adx_pos: float,
    adx_neg: float,
) -> list[RankEntry]:
    """Return entries for *kumo_state*, sorted by bench_flw desc, with ADX veto applied.

    ADX veto: signs in ADX_VETO are excluded unless the current ADX state matches
    the required direction (e.g. str_lead requires ADX bear: ADX≥20 and −DI > +DI).
    """
    adx_state: str
    if math.isnan(adx):
        adx_state = "choppy"
    elif adx >= _ADX_CHOPPY and adx_neg > adx_pos:
        adx_state = "bear"
    elif adx >= _ADX_CHOPPY and adx_pos > adx_neg:
        adx_state = "bull"
    else:
        adx_state = "choppy"

    entries: list[RankEntry] = []
    for (sign, k), entry in ranking.items():
        if k != kumo_state:
            continue
        required_adx = ADX_VETO.get(sign)
        if required_adx is not None and adx_state != required_adx:
            continue
        entries.append(entry)

    entries.sort(key=lambda e: -e.bench_flw)
    return entries
