"""Does the IDENTITY of the fresh/completing sign predict the confluence outcome? — Stage-0.

Operator hypothesis (2026-06-21), from the adverse look-alike dump: "3 signs + validity
date is too coarse — something more separates winners." In the adverse tail, winners had a
genuine reversal/trough sign FRESH on the entry bar (rev_lo/rev_nlo/chiko fresh), while the
2432 profile (str_lead fresh, two STALE breakouts) leaned to stop-outs. This tests that on
the FULL confluence population (not just the adverse tail), where there is real sample size.

Construction: canonical entry = burst day-0 (first >=3-valid-sign day), fill open[T+1], real
ZsTpSl exit, isolated single positions, FY2018-FY2025. "fresh" sign = staleness 0 at day-0
(fired that bar — the completing sign). Clean attribution via the SINGLE-fresh-sign subset
(exactly one sign fired on the entry bar); also grouped, and the specific 2432 class
(fresh == {str_lead}, all other valid signs are breakouts).

Pre-stated Stage-0 gates (decided BEFORE running):
  - A fresh-sign class is INFORMATIVE if win% differs from the pooled baseline by >=5pp
    (or mean_r by >=1.0pp) with n>=100 and the same-sign gap in >=6/8 FYs.
  - ESCALATE to the paired fill-order null ONLY for a class that is materially WORSE and
    deployable as a veto (the 2432-style "str_lead-on-stale-breakouts" sub-hypothesis), i.e.
    that class mean_r >=0.5pp below pooled AND win% >=5pp below AND >=6/8 FY consistent AND
    n>=50.
  - Otherwise descriptive only — report which fresh signs lead/lag, REJECT the veto.

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_fresh_sign_stage0
"""
from __future__ import annotations

import datetime
from collections import defaultdict

import numpy as np
from loguru import logger
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
_BREAKOUT = {"brk_sma", "brk_bol", "brk_kumo_hi", "brk_tenkan_hi"}
_GROUP = {
    "rev_lo": "reversal", "rev_nlo": "reversal",
    "str_hold": "strength", "str_lag": "strength", "str_lead": "strength",
    "brk_kumo_hi": "ichi_brk", "brk_tenkan_hi": "ichi_brk",
    "brk_sma": "price_brk", "brk_bol": "price_brk",
    "chiko_hi": "chiko",
}


def _coh(name: str, rr: list[float], base_win: float | None = None) -> str:
    if not rr:
        return f"  {name:<26}: n=0"
    a = np.asarray(rr)
    w = float((a > 0).mean() * 100)
    d = f"  (Δwin {w - base_win:+.1f})" if base_win is not None else ""
    return (f"  {name:<26}: n={len(a):>5}  mean_r={a.mean()*100:+.2f}%  win%={w:.1f}%  "
            f"med={np.median(a)*100:+.2f}%{d}")


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

    recs: list[dict] = []
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
                d0 = cal[i]
                if cfg.start <= d0 <= cfg.end and i >= 1 and i + 1 < len(cal):
                    st = valid_stale(i)
                    fresh = [sg for sg, v in st.items() if v == 0]
                    cands.append(EntryCandidate(
                        stock_code=code, entry_date=d0, entry_price=closes[d0],
                        corr_mode="low", corr_n225=0.0, zs_history=zsm.get(d0, ()),
                    ))
                    meta[(code, d0)] = {"stale": st, "fresh": fresh, "fy": cfg.label}
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
            recs.append({"ret": p.exit_price / p.entry_price - 1.0, **m})
        logger.info("  {} done ({} day-0 entries)", cfg.label, len(results))

    _report(recs)


def _report(recs: list[dict]) -> None:
    print("\n=== Fresh/completing-sign identity vs confluence outcome — Stage-0 ===")
    print(f"canonical day-0 entries, isolated, FY2018-FY2025: n={len(recs)}\n")
    allr = [r["ret"] for r in recs]
    base_win = float((np.asarray(allr) > 0).mean() * 100)
    print(_coh("ALL day-0 (baseline)", allr))

    n_single = sum(1 for r in recs if len(r["fresh"]) == 1)
    print(f"\n  single-fresh-sign entries: {n_single}/{len(recs)} "
          f"({n_single/len(recs)*100:.0f}%)  [clean attribution subset]\n")

    print("(A) SINGLE-fresh-sign entries, bucketed by the fresh sign:")
    by_sign: defaultdict[str, list[float]] = defaultdict(list)
    for r in recs:
        if len(r["fresh"]) == 1:
            by_sign[r["fresh"][0]].append(r["ret"])
    for sg in sorted(by_sign, key=lambda k: -np.mean(by_sign[k])):
        print(_coh(f"{sg} ({_GROUP.get(sg,'?')})", by_sign[sg], base_win))

    print("\n(B) by fresh-sign GROUP (single-fresh subset):")
    by_grp: defaultdict[str, list[float]] = defaultdict(list)
    for r in recs:
        if len(r["fresh"]) == 1:
            by_grp[_GROUP.get(r["fresh"][0], "?")].append(r["ret"])
    for g in sorted(by_grp, key=lambda k: -np.mean(by_grp[k])):
        print(_coh(g, by_grp[g], base_win))

    # (C) the 2432 class: fresh=={str_lead}, all other valid signs are breakouts
    c2432, comp = [], []
    for r in recs:
        is2432 = (r["fresh"] == ["str_lead"]
                  and all(sg in _BREAKOUT for sg in r["stale"] if sg != "str_lead"))
        (c2432 if is2432 else comp).append(r["ret"])
    print("\n(C) 2432 class — fresh=={str_lead} AND all other valid signs are breakouts:")
    print(_coh("2432-class", c2432, base_win))
    print(_coh("everything else", comp, base_win))

    # per-FY for the best-separating single sign (max |win gap| with n>=80)
    cand_signs = [(sg, abs(float((np.asarray(v) > 0).mean() * 100) - base_win))
                  for sg, v in by_sign.items() if len(v) >= 80]
    print("\n(D) per-FY win% for notable fresh signs (n>=80):")
    fy_order = [c.label for c in _FYS]
    for sg, _ in sorted(cand_signs, key=lambda t: -t[1])[:4]:
        line = f"     {sg:<14}"
        negc = 0
        for fy in fy_order:
            v = [r["ret"] for r in recs if len(r["fresh"]) == 1 and r["fresh"][0] == sg and r["fy"] == fy]
            if v:
                w = float((np.asarray(v) > 0).mean() * 100)
                line += f" {fy[-2:]}:{w:4.0f}"
                negc += w < base_win
            else:
                line += f" {fy[-2:]}:  --"
        line += f"   ({negc}/8 < base {base_win:.0f})"
        print(line)

    print("\n(E) VERDICT inputs (gates pre-stated in docstring):")
    if c2432:
        cm = np.mean(c2432) * 100
        cw = float((np.asarray(c2432) > 0).mean() * 100)
        worse = (cm <= np.mean(allr) * 100 - 0.5) and (cw <= base_win - 5) and len(c2432) >= 50
        print(f"  2432-class mean_r {cm:+.2f}% (base {np.mean(allr)*100:+.2f}), "
              f"win {cw:.1f}% (base {base_win:.1f}), n={len(c2432)} → "
              f"{'materially worse, candidate for null' if worse else 'not a clean/large enough veto class'}")
    print("  (any single fresh sign clearing >=5pp win gap @ n>=100 with 6/8 FY = informative; see A/D)\n")


if __name__ == "__main__":
    run()
