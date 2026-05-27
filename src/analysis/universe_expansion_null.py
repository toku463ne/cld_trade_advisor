"""Stage-1 universe-expansion NULL — gates A (break the equal-weight tie) + B (beat the
fill-order null), on the expanded universe. (read-only; the binding test.)

Pre-reg: docs/analysis/universe_expansion_stage1_preregistration.md. Builds, per FY (stitched,
capital-aware 6-slot, β-stripped vs ^N225, net of cost):
  exp_active  = confluence on the expanded universe, corr-greedy (diversification) fill
  exp_passive = equal-weight of the expanded universe (mean constituent daily return)
  random[k]   = confluence on expanded with a RANDOM fill order (the fill-order null, K draws)
  cur_active / cur_passive = the 225 book + its equal-weight (CONTEXT: the documented ~tie)

GATE A (value): P( Sharpe(exp_active βstrip) > Sharpe(exp_passive βstrip) ) ≥ 0.95 AND CI-lo>0,
  using the fill-order distribution {random[k]} as the active book's spread (does the active book
  beat passive across fill orders?). Breaks the tie the 225 could not.
GATE B (mechanism): P( Sharpe(corr_greedy) > Sharpe(random[k]) ) ≥ 0.95 AND CI-lo>0 — corr-greedy
  in the top 5% of the fill-order null (selection finally bites at 426:1 contention).
Both at 30 bps round-trip (cost sweep 0/30/60 reported), OOS = FY2025+ direction.

DEVIATION (documented): K=200 not the pre-reg's ≥1000 — run_simulation on ~20k expanded
candidates is ~2 s, so K=1000×8FY×books ≈ 6 h+; K=200 is the protocol every prior confluence
null used and its distributions were stable. Memory: ~1 GB/FY (probe-verified), freed between FYs.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.universe_expansion_null
"""
from __future__ import annotations

import datetime
import gc
import math
import random
import statistics
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_benchmark import _BULLISH, _N_GATE, _closes, _pos_daily
from src.analysis.confluence_slot_order import _make_corr_selector
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_K = 200
_SLOTS = 6
_COSTS = (0.0, 0.0030, 0.0060)        # round-trip bps; 30 bps = the gate level
_GATE_COST = 0.0030
_UTC = datetime.timezone.utc
# FY → (225 set, expanded set). Expanded uses classifiedexp{cluster_year}.
_FYS = [("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31), "2017"),
        ("FY2019", datetime.date(2019, 4, 1), datetime.date(2020, 3, 31), "2018"),
        ("FY2020", datetime.date(2020, 4, 1), datetime.date(2021, 3, 31), "2019"),
        ("FY2021", datetime.date(2021, 4, 1), datetime.date(2022, 3, 31), "2020"),
        ("FY2022", datetime.date(2022, 4, 1), datetime.date(2023, 3, 31), "2021"),
        ("FY2023", datetime.date(2023, 4, 1), datetime.date(2024, 3, 31), "2022"),
        ("FY2024", datetime.date(2024, 4, 1), datetime.date(2025, 3, 31), "2023"),
        ("FY2025", datetime.date(2025, 4, 1), datetime.date(2026, 3, 31), "2024")]


def _sharpe(rets) -> float:
    a = np.asarray(rets, dtype=np.float64)
    if a.size < 2 or a.std(ddof=1) == 0:
        return float("nan")
    return float(a.mean() / a.std(ddof=1) * math.sqrt(252))


def _bstrip(series, mkt) -> np.ndarray:
    s, m = np.asarray(series, dtype=np.float64), np.asarray(mkt, dtype=np.float64)
    n = min(len(s), len(m))
    s, m = s[:n], m[:n]
    if n < 30 or m.var() == 0:
        return s
    b = float(np.cov(s, m)[0, 1] / m.var())
    return s - b * m


def _fy_book(cands, caches, end, stock_dts, cal, sel):
    """Capital-aware 6-slot daily returns (equal-weight r/6) + per-day entry counts. Cost is
    applied AFTER (cost is a post-hoc deduction; the trades don't depend on it), so we
    run_simulation ONCE per fill order, not once per cost level."""
    cal_set = set(cal)
    results = run_simulation(cands, cbt._EXIT_RULE, caches, end, day_selector=sel)
    day = defaultdict(float); ent = defaultdict(int)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day[d] += r / _SLOTS
        if p.entry_date in cal_set:
            ent[p.entry_date] += 1
    return [day.get(d, 0.0) for d in cal], [ent.get(d, 0) for d in cal]


def _apply_cost(base, ent, cost):
    """Net base daily returns by `cost` round-trip per entry (deducted on entry day)."""
    return [base[i] - cost * ent[i] / _SLOTS for i in range(len(base))]


def _load_fy(stock_set, start, end):
    """Load caches + fires + candidates for one universe/FY. Returns the pieces _fy_book needs."""
    codes = cbt._stocks_for_fy(stock_set)
    if not codes:
        return None
    ss = start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
    se = end + datetime.timedelta(days=60)
    ssd = datetime.datetime.combine(ss, datetime.time.min, tzinfo=_UTC)
    sed = datetime.datetime.combine(se, datetime.time.max, tzinfo=_UTC)
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.stock_set == stock_set,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
        fires = defaultdict(list)
        for sg, st, fa in rows:
            fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))
        n225 = DataCache("^N225", "1d"); n225.load(s, ssd, sed)
        caches = {}
        for code in codes:
            c = DataCache(code, "1d"); c.load(s, ssd, sed)
            if c.bars:
                caches[code] = c
    corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
    zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
    n_dts, n_cmap = _closes(n225)
    stock_dts = {code: _closes(c) for code, c in caches.items()}
    returns, didx = {}, {}
    for code, (dts, cmap) in stock_dts.items():
        cl = np.array([cmap[d] for d in dts]); r = np.zeros_like(cl, dtype=float)
        if len(cl) > 1:
            r[1:] = cl[1:] / cl[:-1] - 1.0
        returns[code] = r; didx[code] = {d: i for i, d in enumerate(dts)}
    cands = []
    for code in caches:
        cands += cbt._candidates_for_stock(code, fires.get(code, []), caches[code],
                                           corr_maps.get(code, {}), zs_maps.get(code, {}),
                                           start, end, _N_GATE)
    cal = [d for d in n_dts if start <= d <= end]
    mkt = np.zeros(len(cal))
    for i, d in enumerate(cal):
        if i > 0 and n_cmap.get(cal[i - 1]):
            mkt[i] = n_cmap[d] / n_cmap[cal[i - 1]] - 1.0
    # equal-weight passive: mean constituent daily return
    passive = []
    for i, d in enumerate(cal):
        if i == 0:
            passive.append(0.0); continue
        rs = [returns[c][didx[c][d]] for c in returns if d in didx[c] and cal[i - 1] in didx[c]]
        passive.append(float(np.mean(rs)) if rs else 0.0)
    sel = _make_corr_selector(returns, didx)
    return dict(cands=cands, caches=caches, end=end, stock_dts=stock_dts, cal=cal,
                sel=sel, passive=passive, mkt=mkt)


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0

    # stitched base series + entry counts (cost applied post-hoc)
    cg_b, cg_e = [], []                       # expanded corr-greedy active
    rnd_b = [[] for _ in range(_K)]; rnd_e = [[] for _ in range(_K)]
    exp_pass, mkt_all = [], []
    cur_b, cur_e, cur_pass = [], [], []       # 225 context

    for label, start, end, cy in _FYS:
        ex = _load_fy(f"classifiedexp{cy}", start, end)
        if ex is None:
            logger.warning("{}: no expanded universe — skip", label); continue
        b, e = _fy_book(ex["cands"], ex["caches"], ex["end"], ex["stock_dts"], ex["cal"], ex["sel"])
        cg_b += b[1:]; cg_e += e[1:]
        for k in range(_K):
            rng = random.Random(k); pool = ex["cands"][:]; rng.shuffle(pool)
            b, e = _fy_book(pool, ex["caches"], ex["end"], ex["stock_dts"], ex["cal"], None)
            rnd_b[k] += b[1:]; rnd_e[k] += e[1:]
        exp_pass += ex["passive"][1:]; mkt_all += list(ex["mkt"][1:])
        ncand = len(ex["cands"]); del ex; gc.collect()

        cu = _load_fy(f"classified{cy}", start, end)
        if cu is not None:
            b, e = _fy_book(cu["cands"], cu["caches"], cu["end"], cu["stock_dts"], cu["cal"], cu["sel"])
            cur_b += b[1:]; cur_e += e[1:]; cur_pass += cu["passive"][1:]
            del cu; gc.collect()
        logger.info("  {} done (expanded {} candidates, K={})", label, ncand, _K)

    mkt = np.array(mkt_all)
    pass_sh = _sharpe(_bstrip(exp_pass, mkt))
    cur_cg_sh = _sharpe(_bstrip(_apply_cost(cur_b, cur_e, _GATE_COST), mkt[:len(cur_b)]))
    cur_pass_sh = _sharpe(_bstrip(cur_pass, mkt[:len(cur_pass)]))
    cg_sh = {c: _sharpe(_bstrip(_apply_cost(cg_b, cg_e, c), mkt)) for c in _COSTS}
    rand_sh = {c: np.array([_sharpe(_bstrip(_apply_cost(rnd_b[k], rnd_e[k], c), mkt))
                            for k in range(_K)]) for c in _COSTS}

    print("\n" + "=" * 90)
    print("STAGE-1 UNIVERSE-EXPANSION NULL — gates A (break tie) + B (beat fill-order)")
    print("=" * 90)
    print(f"K={_K} fill-order shuffles | β-stripped vs ^N225 | books capital-aware 6-slot\n")
    print(f"CONTEXT — the 225 tie (30bps): cur_active {cur_cg_sh:+.3f} vs cur_passive "
          f"{cur_pass_sh:+.3f}  →  active−passive {cur_cg_sh-cur_pass_sh:+.3f} (≈0 = the wall)")
    print(f"\nEXPANDED β-stripped Sharpe (30bps): corr-greedy active {cg_sh[_GATE_COST]:+.3f} | "
          f"passive {pass_sh:+.3f}")

    print(f"\nGATE A — value (active beats equal-weight passive), fill-order distribution:")
    for cost in _COSTS:
        d = rand_sh[cost] - pass_sh
        p = float(np.mean(d > 0)); lo, hi = np.percentile(d, [2.5, 97.5])
        ga = (p >= 0.95 and lo > 0)
        tag = "  ← GATE" if cost == _GATE_COST else ""
        print(f"  {int(cost*1e4):>3}bps  active−passive: corr-greedy {cg_sh[cost]-pass_sh:+.3f} | "
              f"P(rand>pass) {p:.3f} | CI[{lo:+.3f},{hi:+.3f}] → {'PASS' if ga else 'FAIL'}{tag}")

    print(f"\nGATE B — mechanism (corr-greedy beats random fill), {_K} paired:")
    for cost in _COSTS:
        d = cg_sh[cost] - rand_sh[cost]
        p = float(np.mean(d > 0)); lo, hi = np.percentile(d, [2.5, 97.5])
        gb = (p >= 0.95 and lo > 0)
        tag = "  ← GATE" if cost == _GATE_COST else ""
        print(f"  {int(cost*1e4):>3}bps  Δ(corr-greedy − random) mean {d.mean():+.3f} | "
              f"P(Δ>0) {p:.3f} | CI[{lo:+.3f},{hi:+.3f}] → {'PASS' if gb else 'FAIL'}{tag}")

    dA = rand_sh[_GATE_COST] - pass_sh
    dB = cg_sh[_GATE_COST] - rand_sh[_GATE_COST]
    gateA = (np.mean(dA > 0) >= 0.95 and np.percentile(dA, 2.5) > 0)
    gateB = (np.mean(dB > 0) >= 0.95 and np.percentile(dB, 2.5) > 0)
    verdict = "EXPAND (A∧B pass)" if (gateA and gateB) else "REJECT"
    print(f"\n  VERDICT @30bps: {verdict}  (Gate A {'PASS' if gateA else 'FAIL'}, "
          f"Gate B {'PASS' if gateB else 'FAIL'})")
    print("  A∧B → re-open the parked-rejects queue (prefer_b0/div_peer/PEAD sleeve, one pre-reg "
          "each). Else: tie structural / selection unsupported even at 426:1 contention.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
