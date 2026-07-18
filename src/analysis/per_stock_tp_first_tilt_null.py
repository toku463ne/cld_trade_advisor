"""A2 Stage-1 — per-stock TP tilt (tp_first threshold tiers) vs plain ZsTpSl, paired 6-slot null.

Follows the PREMISE PASS in [[project_per_stock_reachability_premise]] (2026-07-18): a stock's
trailing tp_first (does price touch +2·band before −2·band) predicts its forward tp_first,
all-7-FY monotone, band-normalized (geometry not vol). Operator picked the ROBUST design:
tp_first threshold TIERS, TP-side only (sl fixed 2.0 — the SL-side persistence was ~half as
strong, and moving the stop is the historically-harmful half: timestop40/asym pattern).

PRE-REGISTERED tilt (no post-hoc tuning), keyed on the stock's TRAILING tp_first (H=20) as of
entry, from PRIOR fires only (>=45 calendar days back so the 20-bar outcome window is closed;
>= MIN_PRIOR=3 such fires else untilted):
    trailing tp_first < 0.50            -> tp_mult 1.0  (choppy: bank the reachable move early)
    0.50 <= trailing tp_first <= 0.70   -> tp_mult 2.0  (baseline)
    trailing tp_first > 0.70            -> tp_mult 2.5  (trends: let it run)
    < 3 prior fires                     -> tp_mult 2.0  (baseline, untilted)
sl_mult = 2.0 always; alpha 0.3; max_bars 40.

ARMS:
  ctrl        = ZsTpSl(2/2/0.3)                          — production control.
  tilt        = trailing tp_first tiers (REALIZABLE, look-ahead-safe).
  tilt_oracle = full-sample per-stock tp_first tiers (LOOK-AHEAD UPPER BOUND). Decisive cheap
                kill: if even the oracle doesn't separate at the 6-slot book, the realizable
                trailing tilt cannot — it's not estimation noise, the tilt just doesn't change
                which 6 fill / their book PnL enough (the no_supply-veto VLA2 logic).

Binding gate (per project_confluence_exit_ab_reject / evaluation_criteria): tilt certifies only
if paired Δ Sharpe P(Δ>0) >= 0.95 AND 95% CI lower > 0 AND FY2025 OOS Δ > 0. Precedent is sober:
adx_d8 had a real +0.31pp/trade and coin-flipped; per_stock_sign_quality passed premise and died.

Look-ahead safety: tilt map keyed by candidate.zs_history (available at exit-init, effectively
unique per candidate) so NO production change to exit_simulator; maps are MODULE-GLOBAL (rule
holds only a cheap "trail"/"oracle" selector, so _clone_rule's deepcopy stays light).

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.per_stock_tp_first_tilt_null
"""
from __future__ import annotations

import datetime
import math
import random
import statistics
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.exit.zs_tp_sl import ZsTpSl, _ewa
from src.simulator.cache import DataCache

# ── tilt maps: candidate.zs_history tuple -> tp_mult.  MODULE-GLOBAL so deepcopy of the
#    per-position rule clone does NOT duplicate them. ──
_MAP_TRAIL: dict[tuple, float] = {}
_MAP_ORACLE: dict[tuple, float] = {}
_DEFAULT_TP = 2.0

_TPF_CACHE = ("/tmp/claude-1000/-home-ubuntu-cld-trade-advisor/"
              "c170f00b-8659-4fa0-aafe-8f053b6ee1a3/scratchpad/per_stock_reach_events.pkl")
_MIN_PRIOR = 3
_TRAIL_BUFFER_DAYS = 45      # H=20 bars ~= 28 trading days; 45 cal days guarantees window closed
_ORACLE_MIN = 5


def _tier(tpf: float) -> float:
    if tpf < 0.50:
        return 1.0
    if tpf > 0.70:
        return 2.5
    return 2.0


class ZsTpSlTilt(ZsTpSl):
    """ZsTpSl whose tp_mult is looked up per-candidate from a module-global tier map."""

    def __init__(self, which: str, sl_mult: float = 2.0, alpha: float = 0.3,
                 min_legs: int = 3, fallback_pct: float = 0.05, max_bars: int = 40) -> None:
        super().__init__(tp_mult=_DEFAULT_TP, sl_mult=sl_mult, alpha=alpha,
                         min_legs=min_legs, fallback_pct=fallback_pct, max_bars=max_bars)
        self._which = which          # "trail" | "oracle" — cheap to deepcopy

    @property
    def name(self) -> str:
        return f"zs_tilt_{self._which}_sl{self._sl_mult}_a{self._alpha}"

    def _init_levels(self, ctx) -> None:
        entry = ctx.entry_price
        legs = ctx.zs_history
        mp = _MAP_TRAIL if self._which == "trail" else _MAP_ORACLE
        tpm = mp.get(tuple(legs), _DEFAULT_TP)
        if len(legs) >= self._min_legs:
            band = _ewa(legs, self._alpha)
        else:
            band = entry * self._fallback_pct
        self._tp_price = entry + tpm * band
        self._sl_price = entry - self._sl_mult * band


_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_SLOTS = 6
_K = 200
_CTRL = "ctrl"
_FYS = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                 "classified2016"),
        FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)
_BULL_FYS = {"FY2020", "FY2023", "FY2025"}


def _closes(cache):
    dts, cmap, seen = [], {}, set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cmap[d] = b.close
    dts.sort()
    return dts, cmap


def _pos_daily(p, dts, cmap):
    try:
        ie, ix = dts.index(p.entry_date), dts.index(p.exit_date)
    except ValueError:
        return {}
    out = {}
    if ie == ix:
        out[p.entry_date] = p.exit_price / p.entry_price - 1.0
        return out
    span = dts[ie:ix + 1]
    for k, d in enumerate(span):
        if k == 0:
            out[d] = cmap[d] / p.entry_price - 1.0
        elif d == p.exit_date:
            out[d] = p.exit_price / cmap[span[k - 1]] - 1.0
        else:
            out[d] = cmap[d] / cmap[span[k - 1]] - 1.0
    return out


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _fy_returns(cands, rule, caches, cfg, stock_dts, cal):
    cal_set = set(cal)
    results = run_simulation(cands, rule, caches, cfg.end)
    day = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day[d] += r / _SLOTS
    return [day.get(d, 0.0) for d in cal]


def _load_tpf_fires():
    """Per-stock sorted (date, tpf20) from the Stage-0 reachability pickle, plus oracle mean."""
    df = pd.read_pickle(_TPF_CACHE)
    df = df[["code", "date", "tpf20"]].dropna(subset=["tpf20"]).copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    by_code: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    oracle: dict[str, float] = {}
    for code, g in df.sort_values("date").groupby("code"):
        d = np.array(list(g["date"]))
        t = g["tpf20"].to_numpy(dtype=float)
        by_code[code] = (d, t)
        if len(t) >= _ORACLE_MIN:
            oracle[code] = float(t.mean())
    return by_code, oracle


def _trailing_tpf(by_code, code, entry_date) -> float | None:
    ent = by_code.get(code)
    if ent is None:
        return None
    dates, tvals = ent
    cutoff = entry_date - datetime.timedelta(days=_TRAIL_BUFFER_DAYS)
    mask = dates <= cutoff
    if mask.sum() < _MIN_PRIOR:
        return None
    return float(tvals[mask].mean())


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    by_code, oracle = _load_tpf_fires()
    logger.info("tpf fires: {} stocks, {} with oracle", len(by_code), len(oracle))

    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    arms = {"ctrl": ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3, max_bars=40),
            "tilt": ZsTpSlTilt(which="trail"),
            "tilt_oracle": ZsTpSlTilt(which="oracle")}

    eq = {a: [] for a in arms}
    per_fy = {a: {} for a in arms}
    st = {a: [[] for _ in range(_K)] for a in arms}
    # tier diagnostics: (stock, entry_date) -> (trail_tpm, oracle_tpm, trail_tpf, corr_mode)
    cand_meta: dict[tuple, tuple] = {}

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
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
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        n_dts, _ = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        # ── populate tilt maps for this FY's candidates (keyed by zs_history) ──
        for c in cands:
            key = tuple(c.zs_history)
            tr = _trailing_tpf(by_code, c.stock_code, c.entry_date)
            trail_tpm = _tier(tr) if tr is not None else _DEFAULT_TP
            orc = oracle.get(c.stock_code)
            orc_tpm = _tier(orc) if orc is not None else _DEFAULT_TP
            _MAP_TRAIL[key] = trail_tpm
            _MAP_ORACLE[key] = orc_tpm
            cand_meta[(c.stock_code, c.entry_date)] = (trail_tpm, orc_tpm, tr, c.corr_mode)

        # PART 1 — no cap
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = 10 ** 9, 10 ** 9
        for a, rule in arms.items():
            eq[a].extend(run_simulation(cands, rule, caches, cfg.end))
        exsim._MAX_HIGH_CORR, exsim._MAX_LOW_CORR = 1, 5

        # PART 2a — deterministic 6-slot per-FY Sharpe
        base = sorted(cands, key=lambda c: c.entry_date)
        for a, rule in arms.items():
            per_fy[a][cfg.label] = _sharpe(_fy_returns(base, rule, caches, cfg, stock_dts, cal)[1:])

        # PART 2b — paired fill-order null (SAME shuffled order to all arms)
        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)
            for a, rule in arms.items():
                st[a][k] += _fy_returns(pool, rule, caches, cfg, stock_dts, cal)[1:]
        logger.info("  {} done ({} cands)", cfg.label, len(cands))

    # ── tier coverage + carrier check ──
    print("\n" + "=" * 84)
    print("TILT COVERAGE + CARRIER CHECK (trailing tiers over unique candidates)")
    print("=" * 84)
    metas = list(cand_meta.values())
    n = len(metas)
    tilted = sum(1 for m in metas if m[2] is not None)
    print(f"\nunique candidates: {n}  |  with a trailing estimate (>= {_MIN_PRIOR} priors): "
          f"{tilted} ({tilted/n*100:.0f}%)")
    print(f"\n{'trail tp_mult':>14}{'n':>7}{'%':>7}   corr-mode mix (high/mid/low)")
    for tpm in [1.0, 2.0, 2.5]:
        sub = [m for m in metas if m[0] == tpm]
        cm = defaultdict(int)
        for m in sub:
            cm[m[3]] += 1
        tot = max(len(sub), 1)
        print(f"{tpm:>14}{len(sub):>7}{len(sub)/n*100:>7.1f}   "
              f"high {cm['high']/tot*100:.0f}%  mid {cm['mid']/tot*100:.0f}%  low {cm['low']/tot*100:.0f}%")
    print("  (carrier check: if tp_mult=2.5 'let-run' tier is disproportionately HIGH-corr, "
          "the tilt is a momentum/beta proxy, not geometry)")

    # ── PART 1 report: per-trade, identical entries, by arm + by tier ──
    print("\n" + "=" * 84)
    print("PART 1 — per-trade on IDENTICAL entries (no cap), by arm")
    print("=" * 84)
    ctrl_by_key = {(p.stock_code, p.entry_date): p for p in eq["ctrl"]}
    print(f"\n{'arm':<13}{'n':>7}{'mean_r%':>9}{'DR%':>7}   {'hi mean_r%':>11}{'lo mean_r%':>11}")
    for a in arms:
        res = eq[a]
        r = np.array([p.return_pct for p in res])
        hr = np.array([p.return_pct for p in res if p.corr_mode == "high"])
        lr = np.array([p.return_pct for p in res if p.corr_mode != "high"])
        print(f"{a:<13}{len(r):>7}{r.mean()*100:>9.2f}{(r>0).mean()*100:>7.1f}   "
              f"{(hr.mean()*100 if hr.size else float('nan')):>11.2f}"
              f"{(lr.mean()*100 if lr.size else float('nan')):>11.2f}")

    # per-tier: does the tilt help the trades it actually changed?  (tilt vs ctrl on same trades)
    print(f"\n{'tier(trail tpm)':>16}{'n':>7}{'ctrl mean_r%':>14}{'tilt mean_r%':>14}{'Δpp':>8}")
    tilt_by_key = {(p.stock_code, p.entry_date): p for p in eq["tilt"]}
    for tpm in [1.0, 2.0, 2.5]:
        keys = [k for k, m in cand_meta.items() if m[0] == tpm]
        cr = [ctrl_by_key[k].return_pct for k in keys if k in ctrl_by_key]
        tr = [tilt_by_key[k].return_pct for k in keys if k in tilt_by_key]
        if not cr:
            continue
        cm, tm = np.mean(cr) * 100, np.mean(tr) * 100
        print(f"{tpm:>16}{len(cr):>7}{cm:>14.2f}{tm:>14.2f}{tm-cm:>8.2f}")
    print("  (tp_mult=2.0 rows must be identical ctrl==tilt; 1.0 and 2.5 are the changed trades)")

    # ── PART 2a: per-FY deterministic Sharpe ──
    print("\n" + "=" * 84)
    print("PART 2a — deterministic 6-slot per-FY Sharpe (Δ = arm − ctrl)")
    print("=" * 84)
    print(f"\n{'FY':<9}" + "".join(f"{a:>13}" for a in arms)
          + "".join(f"{'Δ'+a:>13}" for a in arms if a != _CTRL))
    bull_d, bear_d = defaultdict(list), defaultdict(list)
    for cfg in _FYS:
        if cfg.label not in per_fy[_CTRL] or math.isnan(per_fy[_CTRL][cfg.label]):
            continue
        row = f"{cfg.label:<9}" + "".join(f"{per_fy[a][cfg.label]:>13.2f}" for a in arms)
        for a in arms:
            if a == _CTRL:
                continue
            dlt = per_fy[a][cfg.label] - per_fy[_CTRL][cfg.label]
            row += f"{dlt:>13.2f}"
            (bull_d if cfg.label in _BULL_FYS else bear_d)[a].append(dlt)
        if cfg.label == "FY2025":
            row += "  OOS"
        print(row)
    for a in arms:
        if a == _CTRL:
            continue
        oos = per_fy[a]["FY2025"] - per_fy[_CTRL]["FY2025"]
        bm = np.mean(bull_d[a]) if bull_d[a] else float("nan")
        br = np.mean(bear_d[a]) if bear_d[a] else float("nan")
        print(f"  {a}: FY2025 OOS Δ {oos:+.2f} | bull-mean Δ {bm:+.2f} | bear-mean Δ {br:+.2f}"
              f"  {'(sign-flip!)' if bm*br<0 else ''}")

    # ── PART 2b: paired fill-order null ──
    print("\n" + "=" * 84)
    print(f"PART 2b — paired fill-order null, {_K} shuffles (6-slot book)")
    print("=" * 84)
    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in arms}
    print(f"\n{'arm':<13}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}")
    for a in arms:
        s_ = sh[a]
        print(f"{a:<13}{s_.mean():>13.2f}{s_.std():>7.2f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}")
    for a in arms:
        if a == _CTRL:
            continue
        d = sh[a] - sh[_CTRL]
        p = (d > 0).mean()
        lo, hi = np.percentile(d, [2.5, 97.5])
        cert = p >= 0.95 and lo > 0
        print(f"\n[paired Δ Sharpe {a} − ctrl]  mean {d.mean():+.3f} | 95% CI [{lo:+.3f}, {hi:+.3f}]"
              f" | P(Δ>0)={p:.3f}")
        print(f"  VERDICT({a}): " + ("CERTIFIED" if cert else "NOT separated"))
    print("\n  (DECISIVE: if tilt_oracle — the look-ahead upper bound — does NOT separate, the "
          "realizable tilt cannot; it's not estimation noise, the tilt just doesn't move the book.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
