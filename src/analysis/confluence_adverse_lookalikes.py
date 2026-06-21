"""Show the REAL past trades that look like the 2432.T conf3 adverse entry.

Operator intuition (2026-06-21): hard to believe setups like 2432 (conf3 =
brk_kumo_hi + brk_tenkan_hi + str_lead, stale breakouts, sharp down bar at entry)
won in the backtest — "there's something more than 3 signs + the validity date".

This enumerates the CANONICAL entry (burst day-0 = first >=3-valid-sign day, fill
open[T+1]) with an adverse signal bar (dr = close[T]/close[T-1]-1 <= -3%), records
each entry's full constituent composition + per-sign staleness (bars since that
sign fired; 0 = fired ON the signal bar) + outcome via the real ZsTpSl exit, then:
  - reports win/mean for the whole adverse-day-0 cohort and sub-cohorts
    (conf count, contains the 2432 core set, exact-3 == the 2432 set),
  - compares winners vs losers on staleness (does the operator's "validity date"
    hunch separate them?),
  - DUMPS the closest look-alikes (stock, dates, entry/exit, return, reason,
    constituents w/ staleness) so they can be charted.

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_adverse_lookalikes
"""
from __future__ import annotations

import datetime
from collections import defaultdict

import numpy as np
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.confluence_benchmark import _FYS
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import EntryCandidate, run_simulation
from src.simulator.cache import DataCache
from src.strategy.confluence_sign import _BULLISH_SIGNS, _VALID_BARS

_N_GATE = 3
_ADVERSE = -0.03
_CORE = frozenset({"brk_kumo_hi", "brk_tenkan_hi", "str_lead"})   # the 2432 set


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    exsim._MAX_LOW_CORR = 10_000
    exsim._MAX_HIGH_CORR = 10_000
    rule = cbt._EXIT_RULE

    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH_SIGNS)))).all()
    fires_by_code: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for sg, st, fa in rows:
        fires_by_code[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    records: list[dict] = []     # one per adverse day-0 entry

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 90)
        se = cfg.end + datetime.timedelta(days=60)
        with get_session() as s:
            n225 = DataCache("^N225", "1d")
            n225.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                      datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
            caches = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    caches[code] = c
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}

        cands: list[EntryCandidate] = []
        meta: dict[tuple[str, datetime.date], dict] = {}
        for code, c in caches.items():
            closes: dict[datetime.date, float] = {}
            cal: list[datetime.date] = []
            seen: set[datetime.date] = set()
            for b in c.bars:
                d = b.dt.date()
                if d in seen:
                    continue
                seen.add(d); closes[d] = b.close; cal.append(d)
            cal.sort()
            idx = {d: i for i, d in enumerate(cal)}
            zsm = zs_maps.get(code, {})
            fire_idx: dict[str, list[int]] = defaultdict(list)
            for sg, fd in fires_by_code.get(code, []):
                if fd in idx:
                    fire_idx[sg].append(idx[fd])

            def valid_stale(i: int) -> dict[str, int]:
                out: dict[str, int] = {}
                for sg in _BULLISH_SIGNS:
                    vb = _VALID_BARS.get(sg, 5)
                    best = None
                    for fj in fire_idx.get(sg, []):
                        if fj <= i <= fj + vb:
                            best = i - fj
                    if best is not None:
                        out[sg] = best
                return out

            counts = [len(valid_stale(i)) for i in range(len(cal))]
            i = 0
            while i < len(cal):
                if counts[i] < _N_GATE:
                    i += 1
                    continue
                # burst day-0 = i
                d0 = cal[i]
                if cfg.start <= d0 <= cfg.end and i >= 1 and i + 1 < len(cal):
                    dr = closes[d0] / closes[cal[i - 1]] - 1.0
                    if dr <= _ADVERSE:
                        st = valid_stale(i)
                        cands.append(EntryCandidate(
                            stock_code=code, entry_date=d0, entry_price=closes[d0],
                            corr_mode="low", corr_n225=0.0, zs_history=zsm.get(d0, ()),
                        ))
                        meta[(code, d0)] = {"dr": dr, "stale": st, "fy": cfg.label}
                # advance past this burst
                j = i
                while j < len(cal) and counts[j] >= _N_GATE:
                    j += 1
                i = j

        results = run_simulation(cands, rule, caches, cfg.end)
        for p in results:
            if not p.entry_price:
                continue
            m = meta.get((p.stock_code, p.entry_date))
            if m is None:
                continue
            ret = p.exit_price / p.entry_price - 1.0
            records.append({
                "code": p.stock_code, "sig": p.entry_date, "fill": p.fill_date if hasattr(p, "fill_date") else None,
                "exit": p.exit_date, "entry": p.entry_price, "exitp": p.exit_price,
                "ret": ret, "reason": p.exit_reason, "hold": p.hold_bars,
                "dr": m["dr"], "stale": m["stale"], "fy": m["fy"],
            })

    _report(records)


def _ws(stale: dict[str, int]) -> str:
    return ",".join(f"{k}({v})" for k, v in sorted(stale.items(), key=lambda kv: kv[1]))


def _coh(name: str, rr: list[float]) -> str:
    if not rr:
        return f"  {name:<34}: n=0"
    a = np.asarray(rr)
    return (f"  {name:<34}: n={len(a):>4}  mean_r={a.mean()*100:+.2f}%  "
            f"win%={float((a > 0).mean()*100):.1f}%  med={np.median(a)*100:+.2f}%")


def _report(records: list[dict]) -> None:
    print("\n=== Past adverse-entry conf trades that look like 2432.T (canonical day-0 entry) ===")
    print(f"adverse day-0 entries (dr<=-3%): {len(records)} over FY2018-FY2025\n")
    allr = [r["ret"] for r in records]
    print(_coh("ALL adverse day-0", allr))

    conf3 = [r["ret"] for r in records if len(r["stale"]) == 3]
    conf4 = [r["ret"] for r in records if len(r["stale"]) >= 4]
    print(_coh("  conf==3", conf3))
    print(_coh("  conf>=4", conf4))

    core = [r for r in records if _CORE <= set(r["stale"])]
    exact = [r for r in records if set(r["stale"]) == _CORE]
    print(_coh("  contains 2432 core {kumo,tenkan,strlead}", [r["ret"] for r in core]))
    print(_coh("  EXACT == 2432 core (conf3)", [r["ret"] for r in exact]))

    # winners vs losers on staleness (operator's "validity date" hunch)
    wins = [r for r in records if r["ret"] > 0]
    loss = [r for r in records if r["ret"] <= 0]
    def avg_maxstale(rs): return np.mean([max(r["stale"].values()) for r in rs]) if rs else float("nan")
    def avg_nfresh(rs):   return np.mean([sum(1 for v in r["stale"].values() if v == 0) for r in rs]) if rs else float("nan")
    print(f"\n  winners ({len(wins)}): avg max-staleness {avg_maxstale(wins):.2f}, avg #fresh-on-bar {avg_nfresh(wins):.2f}")
    print(f"  losers  ({len(loss)}): avg max-staleness {avg_maxstale(loss):.2f}, avg #fresh-on-bar {avg_nfresh(loss):.2f}")

    # closest look-alikes: exact core first, then supersets, dump up to 24
    rank = sorted(records, key=lambda r: (set(r["stale"]) != _CORE, not (_CORE <= set(r["stale"])), -abs(r["dr"])))
    show = rank[:24]
    print(f"\n  closest look-alikes to 2432 (exact-core first), {len(show)} of {len(core)} core / {len(exact)} exact:")
    print(f"    {'stock':<8} {'signal':<11} {'exit':<11} {'dr%':>6} {'ret%':>7} {'reason':<6} {'hold':>4}  constituents(staleness)")
    for r in show:
        tag = "**" if set(r["stale"]) == _CORE else ("* " if _CORE <= set(r["stale"]) else "  ")
        print(f"  {tag}{r['code']:<8} {str(r['sig']):<11} {str(r['exit']):<11} "
              f"{r['dr']*100:>6.1f} {r['ret']*100:>+7.1f} {r['reason']:<6} {r['hold']:>4}  {_ws(r['stale'])}")
    print("\n  (** = exact 2432 set, * = contains the 2432 core)\n")


if __name__ == "__main__":
    run()
