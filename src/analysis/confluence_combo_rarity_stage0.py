"""Should minimum-count (conf=3) + rare-combination entries be rejected? — Stage-0.

Operator proposal (2026-06-21): "a combination of too few samples with the minimum number
of members should simply be rejected." I.e. veto conf==3 entries whose exact sign-combination
has little historical precedent — we have no statistical basis for them.

Key distinction this tests: does RARITY predict BADNESS (rare combos actually underperform →
the veto has merit), or is rarity just LESS DATA (same mean, more variance → rejecting forgoes
unmeasurable EV)? Only the former justifies a systematic rule; the latter is a per-trade
low-confidence judgement, not a backtest edge.

Construction: all canonical day-0 confluence entries (first >=3 day, fill open[T+1], real ZsTpSl
exit, isolated), FY2018-FY2025. Each entry's exact valid-sign SET is its "combo". Combo frequency
is counted over the whole sample (in-sample — FAVOURABLE to the veto, so a non-separation is a
robust reject). Axes: conf count (3 / 4 / 5+) and combo-frequency tier.

Pre-stated Stage-0 gates (decided BEFORE running):
  - The veto has merit only if conf==3 & rare-combo (freq <= 3) entries are MATERIALLY WORSE:
    mean_r <= baseline - 0.5pp AND win% <= baseline - 5pp AND >=6/8 FY same-sign.
  - If rare-combo outcomes ~= common (rarity = noise not badness), REJECT the systematic rule:
    don't veto on rarity (per-trade low-confidence skip remains a valid manual judgement).

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_combo_rarity_stage0
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
_CORE = frozenset({"brk_kumo_hi", "brk_tenkan_hi", "str_lead"})
_FREQ_TIERS = [(1, 3, "very-rare(<=3)"), (4, 10, "rare(4-10)"),
               (11, 30, "uncommon(11-30)"), (31, 10**9, "common(>30)")]


def _tier(f: int) -> str:
    for lo, hi, lab in _FREQ_TIERS:
        if lo <= f <= hi:
            return lab
    return "?"


def _coh(name: str, rr: list[float], bw: float | None = None) -> str:
    if not rr:
        return f"  {name:<22}: n=0"
    a = np.asarray(rr)
    w = float((a > 0).mean() * 100)
    d = f"  (Δwin {w - bw:+.1f})" if bw is not None else ""
    return (f"  {name:<22}: n={len(a):>5}  mean_r={a.mean()*100:+.2f}%  win%={w:.1f}%  "
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

            def valid_set(i: int) -> frozenset[str]:
                out = set()
                for sg in _BULLISH_SIGNS:
                    vb = _VALID_BARS.get(sg, 5)
                    if any(fj <= i <= fj + vb for fj in fire_idx.get(sg, [])):
                        out.add(sg)
                return frozenset(out)

            counts = [len(valid_set(i)) for i in range(len(cal))]
            i = 0
            while i < len(cal):
                if counts[i] < _N_GATE:
                    i += 1
                    continue
                d0 = cal[i]
                if cfg.start <= d0 <= cfg.end and i >= 1 and i + 1 < len(cal):
                    combo = valid_set(i)
                    cands.append(EntryCandidate(
                        stock_code=code, entry_date=d0, entry_price=closes[d0],
                        corr_mode="low", corr_n225=0.0, zs_history=zsm.get(d0, ()),
                    ))
                    meta[(code, d0)] = {"combo": combo, "n": len(combo), "fy": cfg.label}
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
    combo_freq: defaultdict[frozenset, int] = defaultdict(int)
    for r in recs:
        combo_freq[r["combo"]] += 1
    for r in recs:
        r["freq"] = combo_freq[r["combo"]]

    allr = [r["ret"] for r in recs]
    bw = float((np.asarray(allr) > 0).mean() * 100)
    print("\n=== conf=3 + rare-combo veto — Stage-0 ===")
    print(f"day-0 entries FY2018-FY2025: n={len(recs)}  distinct combos: {len(combo_freq)}\n")
    print(_coh("ALL (baseline)", allr))

    print("\n(A) by conf count (the 'minimum members' axis):")
    for k in (3, 4):
        print(_coh(f"conf=={k}", [r["ret"] for r in recs if r["n"] == k], bw))
    print(_coh("conf>=5", [r["ret"] for r in recs if r["n"] >= 5], bw))

    print("\n(B) by combo-frequency tier (all conf counts):")
    for _, _, lab in _FREQ_TIERS:
        print(_coh(lab, [r["ret"] for r in recs if _tier(r["freq"]) == lab], bw))

    print("\n(C) conf==3 split by combo frequency (the operator's class):")
    rare3 = [r["ret"] for r in recs if r["n"] == 3 and r["freq"] <= 3]
    com3 = [r["ret"] for r in recs if r["n"] == 3 and r["freq"] > 30]
    print(_coh("conf3 & very-rare(<=3)", rare3, bw))
    print(_coh("conf3 & common(>30)", com3, bw))

    # the exact 2432 combo
    n2432 = combo_freq.get(_CORE, 0)
    r2432 = [r["ret"] for r in recs if r["combo"] == _CORE]
    print(f"\n(D) exact 2432 combo {{brk_kumo_hi,brk_tenkan_hi,str_lead}} total count "
          f"(any bar): {n2432}")
    print(_coh("  2432 combo", r2432, bw))

    print("\n(E) per-FY: conf3&rare vs conf3&common mean_r:")
    neg = 0; tot = 0
    for cfg in _FYS:
        fy = cfg.label
        rr = [r["ret"] for r in recs if r["n"] == 3 and r["freq"] <= 3 and r["fy"] == fy]
        cc = [r["ret"] for r in recs if r["n"] == 3 and r["freq"] > 30 and r["fy"] == fy]
        if rr and cc:
            sp = (np.mean(rr) - np.mean(cc)) * 100
            neg += sp < 0; tot += 1
            print(f"     {fy:<8} rare−common {sp:+6.2f}pp  (rare n={len(rr)}, common n={len(cc)})")
        else:
            print(f"     {fy:<8} (insufficient: rare n={len(rr)}, common n={len(cc)})")
    print(f"   sign consistency: {neg}/{tot} FYs rare<common")

    print("\n(F) VERDICT (gates pre-stated in docstring):")
    if rare3 and com3:
        rm, cm = np.mean(rare3) * 100, np.mean(com3) * 100
        rw = float((np.asarray(rare3) > 0).mean() * 100)
        cw = float((np.asarray(com3) > 0).mean() * 100)
        merit = (rm <= np.mean(allr) * 100 - 0.5) and (rw <= bw - 5) and neg >= 6
        print(f"  conf3&rare mean_r {rm:+.2f}% vs common {cm:+.2f}% (base {np.mean(allr)*100:+.2f}); "
              f"win {rw:.1f}% vs {cw:.1f}% (base {bw:.1f})")
        print(f"  → {'rare combos materially worse — rule has merit, take to fill-order null' if merit else 'rarity = noise not badness; REJECT systematic veto (per-trade low-confidence skip still valid)'}")
    print()


if __name__ == "__main__":
    run()
