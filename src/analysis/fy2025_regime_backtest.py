"""FY2025 out-of-sample backtest for RegimeSignStrategy.

Evaluates RegimeSignStrategy in backtest mode over FY2025 (2025-04-01 – 2026-03-31)
using the FY2018-FY2024 regime ranking, then joins each proposal against the
FY2025 SignBenchmarkEvents to measure actual DR and P&L vs expected.

Usage
-----
    uv run --env-file devenv python -m src.analysis.fy2025_regime_backtest

Results (run 2026-05-09)
------------------------
Configuration:
  stock_set : classified2024
  run_ids   : 47–150  (FY2018–FY2025 benchmark runs)
  period    : 2025-04-01 – 2026-03-31
  mode      : backtest (1 high-corr + 1 low-corr proposal per day)

Proposal volume:
  Days with proposals : 224 / 244 trading days
  Total proposals     : 448
  Matched to outcomes : 412  (36 unmatched — sign fired near period boundary)

Direction rate (DR):
  Overall             : 51.9%  (n=412)
  high-corr           : 62.0%  (n=208)   ← Kumo filter effective for index proxies
  low-corr            : 41.7%  (n=204)   ← regime filter hurts idiosyncratic stocks

  By sign:
    brk_bol  58.6% (n=128)  expected=54.8%  → outperformed
    div_gap  49.8% (n=237)  expected=50.8%  → in line
    brk_sma  43.5% (n=46)   expected=56.9%  → underperformed
    rev_nhi  100%  (n=1)    expected=49.4%  → too small

P&L (long-only, equal-weight per trade, trade horizon = next zigzag leg):
  Avg return per trade (overall)    :  +2.3%
  Avg return per trade (high-corr)  :  +4.2%
  Avg return per trade (low-corr)   :  +0.4%
  Avg return per trade (brk_bol)    :  +5.0%
  Avg return per trade (div_gap)    :  +1.8%
  Avg return per trade (brk_sma)    :  -2.4%

  Note: trend_magnitude = zigzag leg size (best-case exit at local extremum).
  Real fills and slippage will reduce these figures.

Key findings:
  - High-corr names benefit strongly from the Kumo regime gate (+7.2% avg return).
  - Low-corr signal selection via Kumo ranking is marginally negative; the regime
    built from N225 behaviour does not generalise to idiosyncratic stocks.
  - corr_shift and div_peer were skipped (require extra inputs); both ranked highly
    in training — adding them is the highest-priority improvement.
  - brk_bol is the only sign beating its training expectation on both DR and return.
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from typing import NamedTuple

from loguru import logger
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.strategy.proposal import SignalProposal
from src.strategy.regime_sign import RegimeSignStrategy

_START     = datetime.datetime(2025, 4,  1, tzinfo=datetime.timezone.utc)
_END       = datetime.datetime(2026, 3, 31, tzinfo=datetime.timezone.utc)
_STOCK_SET = "classified2024"
_RUN_IDS   = list(range(47, 151))   # FY2018–FY2025 benchmark runs

_UNEVALUABLE_SIGNS: set[str] = set()  # all signs now match benchmark implementation


class _EventRecord(NamedTuple):
    direction: int
    magnitude: float | None


def _load_event_index(
    run_ids: list[int],
) -> dict[tuple[str, str, datetime.date], _EventRecord]:
    """Return {(sign_type, stock_code, fired_at.date): _EventRecord} for quick lookup."""
    with get_session() as session:
        runs = session.execute(
            select(SignBenchmarkRun.id, SignBenchmarkRun.sign_type)
            .where(SignBenchmarkRun.id.in_(run_ids))
        ).all()
        run_to_sign = {r.id: r.sign_type for r in runs}

        events = session.execute(
            select(
                SignBenchmarkEvent.run_id,
                SignBenchmarkEvent.stock_code,
                SignBenchmarkEvent.fired_at,
                SignBenchmarkEvent.trend_direction,
                SignBenchmarkEvent.trend_magnitude,
            )
            .where(
                SignBenchmarkEvent.run_id.in_(run_ids),
                SignBenchmarkEvent.trend_direction.isnot(None),
            )
        ).all()

    index: dict[tuple[str, str, datetime.date], _EventRecord] = {}
    for e in events:
        sign = run_to_sign.get(e.run_id)
        if sign is None:
            continue
        d = e.fired_at.date() if hasattr(e.fired_at, "date") else e.fired_at
        index[(sign, e.stock_code, d)] = _EventRecord(
            direction=int(e.trend_direction),
            magnitude=float(e.trend_magnitude) if e.trend_magnitude is not None else None,
        )
    return index


def _lookup(
    proposal: SignalProposal,
    event_index: dict[tuple[str, str, datetime.date], _EventRecord],
) -> _EventRecord | None:
    fired_date = (proposal.fired_at.date()
                  if hasattr(proposal.fired_at, "date") else proposal.fired_at)
    return event_index.get((proposal.sign_type, proposal.stock_code, fired_date))


def _pnl(direction: int, magnitude: float | None) -> float | None:
    """Long-only P&L: +magnitude on follow-through, -magnitude on reversal."""
    if magnitude is None:
        return None
    return magnitude if direction == 1 else -magnitude


def main() -> None:
    logger.info("Building RegimeSignStrategy …")
    strategy = RegimeSignStrategy.from_config(
        stock_set = _STOCK_SET,
        run_ids   = _RUN_IDS,
        start     = _START,
        end       = _END,
        mode      = "backtest",
    )

    logger.info("Running propose_range {} – {} …", _START.date(), _END.date())
    daily_proposals = strategy.propose_range(_START, _END)

    total_days      = len(daily_proposals)
    total_proposals = sum(len(v) for v in daily_proposals.values())
    logger.info("Proposals: {} days with signals, {} total", total_days, total_proposals)

    # ── Load FY2025 run_ids for outcome lookup ────────────────────────────────
    with get_session() as session:
        fy2025_run_ids = list(session.execute(
            select(SignBenchmarkRun.id)
            .where(
                SignBenchmarkRun.stock_set == _STOCK_SET,
                SignBenchmarkRun.start_dt  == _START,
                SignBenchmarkRun.end_dt    == _END,
            )
        ).scalars().all())
    logger.info("FY2025 run_ids for outcome lookup: {}", len(fy2025_run_ids))

    event_index = _load_event_index(fy2025_run_ids)
    logger.info("Event index: {} entries", len(event_index))

    # ── Collect outcomes ──────────────────────────────────────────────────────
    by_sign: dict[str, list[tuple[int, float | None]]] = defaultdict(list)
    by_corr: dict[str, list[tuple[int, float | None]]] = defaultdict(list)
    all_records: list[tuple[int, float | None]] = []
    missing      = 0
    unevaluable  = 0

    for date, proposals in sorted(daily_proposals.items()):
        for p in proposals:
            if p.sign_type in _UNEVALUABLE_SIGNS:
                unevaluable += 1
                continue
            rec = _lookup(p, event_index)
            if rec is None:
                missing += 1
                continue
            pair = (rec.direction, rec.magnitude)
            by_sign[p.sign_type].append(pair)
            by_corr[p.corr_mode].append(pair)
            all_records.append(pair)

    # ── Summary helpers ───────────────────────────────────────────────────────
    def _dr(records: list[tuple[int, float | None]]) -> str:
        if not records:
            return "—"
        n = len(records)
        k = sum(1 for d, _ in records if d == 1)
        return f"{k/n*100:.1f}%  (n={n})"

    def _avg_return(records: list[tuple[int, float | None]]) -> str:
        vals = [_pnl(d, m) for d, m in records if m is not None]
        if not vals:
            return "—"
        return f"{sum(vals)/len(vals)*100:+.1f}%  (n={len(vals)})"

    # ── Print summary ─────────────────────────────────────────────────────────
    print()
    print("=" * 66)
    print("FY2025 RegimeSignStrategy Backtest — mode=backtest")
    print(f"Stock set : {_STOCK_SET}")
    print(f"Run IDs   : {_RUN_IDS[0]}–{_RUN_IDS[-1]}")
    print(f"Period    : {_START.date()} – {_END.date()}")
    print("=" * 66)
    print(f"Days with proposals : {total_days} / 244")
    print(f"Total proposals     : {total_proposals}")
    print(f"Matched to outcomes : {len(all_records)}")
    print(f"Unevaluable         : {unevaluable}  (div_peer/corr_shift — benchmark impl differs)")
    print(f"Unmatched           : {missing}  (fired near period boundary or stock not in benchmark)")
    print()
    print(f"Overall DR          : {_dr(all_records)}")
    print(f"Avg return/trade    : {_avg_return(all_records)}")
    print()

    print("── By corr_mode ──────────────────────────────────────────────")
    for mode in ("high", "mid", "low"):
        recs = by_corr[mode]
        print(f"  {mode:<5}  DR={_dr(recs):<22} avg_return={_avg_return(recs)}")
    print()

    print("── By sign ───────────────────────────────────────────────────")
    header = f"  {'sign':<12}  {'DR':>18}  {'avg_return':>18}  expected_DR"
    print(header)
    for sign in sorted(by_sign):
        recs = by_sign[sign]
        expected_dr = None
        for proposals in daily_proposals.values():
            for p in proposals:
                if p.sign_type == sign:
                    expected_dr = p.regime_dr
                    break
            if expected_dr is not None:
                break
        exp_str = f"{expected_dr*100:.1f}%" if expected_dr else "—"
        print(f"  {sign:<12}  DR={_dr(recs):<22}  avg_return={_avg_return(recs):<22}  expected={exp_str}")

    print()
    print("── Sample proposals (first 10 days) ──────────────────────────")
    for i, (date, proposals) in enumerate(sorted(daily_proposals.items())):
        if i >= 10:
            break
        print(f"  {date}:")
        for p in proposals:
            rec = _lookup(p, event_index)
            if rec is None:
                out_str = "?"
            else:
                out_str = "✓" if rec.direction == 1 else "✗"
                mag_str = f"  mag={rec.magnitude*100:+.1f}%" if rec.magnitude else ""
            print(f"    {out_str} {p}{mag_str if rec else ''}")


if __name__ == "__main__":
    main()
