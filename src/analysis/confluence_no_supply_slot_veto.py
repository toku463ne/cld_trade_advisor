"""WAITING-slot selection-time no_supply veto vs no-veto (read-only).

Operator model (2026-06-27): the canonical confluence engine admits-or-DROPS a candidate on
its fire day (run_simulation, no waiting).  The operator's veto needs a WAITING-slot model:
a confluence trigger stays eligible across its validity window and competes for a slot
whenever one frees up; at slot-selection time we DISQUALIFY any candidate that has printed a
no_supply bar between its fire date and today (inclusive).  No entry delay — the candidate is
simply excluded from today's eligible list.  The new mechanism vs all prior tests: vetoing a
weak candidate FREES the slot for another WAITING candidate (backfill).

This builds the waiting-slot sim and compares WITH the veto vs WITHOUT, paired on the same
per-seed fill-order priority (so any difference is the veto + its backfill, not luck).

SIMPLIFICATION (Stage-0): fixed HOLD bars instead of the ZsTpSl exit, held identical across
both arms, so the slot-occupancy / contention dynamics are modeled but the exit rule is a
constant.  If the veto shows a real Δ here, escalate to the real-exit waiting sim.

Eligibility window per trigger = the confluence BURST (consecutive days count>=3 from F).
Slots: 1 high-corr + 5 low/mid-corr = 6.  Fill at next bar open (two-bar).  Daily
mark-to-market portfolio returns → Sharpe per seed; Δ Sharpe (VETO−BASE) paired over K seeds.
REAL if P(Δ>0) >= 0.95 AND 95% CI excludes 0.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_no_supply_slot_veto
"""
from __future__ import annotations

import datetime
import random
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from loguru import logger

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.bullish_confluence_v2_probe import _BULLISH_SIGNS, _VALID_BARS
from src.analysis.confluence_benchmark import _FYS
from src.analysis.confluence_capacity_null import _metrics, _sharpe
from src.data.db import get_session
from src.simulator.cache import DataCache

_WINDOWED = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
             "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_COOLDOWN = 10
_MAX_HIGH = 1
_MAX_LOW = 5
_SLOTS = 6
_K = 200
_HOLD_SWEEP = [10, 20]
_V_DRY = 0.7
_VLOOK = 20


class _Pack:
    __slots__ = ("sidx", "open", "close", "nosup", "count", "dates")

    def __init__(self, cache, fires_for_stock):
        seen, dts, op, cl, vol = set(), [], [], [], []
        for b in cache.bars:
            d = b.dt.date()
            if d in seen:
                continue
            seen.add(d); dts.append(d); op.append(b.open); cl.append(b.close); vol.append(float(b.volume))
        self.dates = dts
        self.sidx = {d: i for i, d in enumerate(dts)}
        self.open = np.asarray(op); self.close = np.asarray(cl)
        cl = self.close
        ret1 = np.concatenate([[np.nan], cl[1:] / cl[:-1] - 1.0]) if len(cl) > 1 else np.array([np.nan])
        vavg = pd.Series(vol).rolling(_VLOOK).mean().shift(1).to_numpy()
        vmult = np.asarray(vol) / np.where(vavg > 0, vavg, np.nan)
        self.nosup = (ret1 < 0) & (vmult <= _V_DRY)
        N = len(dts)
        valid = [set() for _ in range(N)]
        for sign, fd in fires_for_stock:
            fi = self.sidx.get(fd if not hasattr(fd, "date") else fd)
            if fi is None:
                continue
            vb = _VALID_BARS.get(sign, 5)
            for j in range(fi, min(fi + vb + 1, N)):
                valid[j].add(sign)
        self.count = np.array([len(s) for s in valid])

    def burst_end(self, fi):
        be = fi
        while be + 1 < len(self.count) and self.count[be + 1] >= _N_GATE:
            be += 1
        return be

    def first_nosup(self, fi, be):
        for j in range(fi, be + 1):
            if self.nosup[j]:
                return j
        return None


class _Cand:
    __slots__ = ("pack", "corr", "F_gi", "we_gi", "fns_gi")


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    cbt._VALID_BARS = dict(_WINDOWED)
    fires = cbt._load_bullish_fires_by_stock()

    for HOLD in _HOLD_SWEEP:
        arms = ["BASE", "VETO"]
        st = {a: [[] for _ in range(_K)] for a in arms}
        book = {a: [] for a in arms}
        ntr = {a: 0.0 for a in arms}

        for cfg in _FYS:
            codes = cbt._stocks_for_fy(cfg.stock_set)
            if not codes:
                continue
            ss = cfg.start - datetime.timedelta(days=260)
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
            zs_maps = {code: {} for code in caches}
            packs = {code: _Pack(c, fires.get(code, [])) for code, c in caches.items()}

            seen_g, gcal = set(), []
            for b in n225.bars:
                d = b.dt.date()
                if d not in seen_g:
                    seen_g.add(d); gcal.append(d)
            gcal.sort()
            ggidx = {d: i for i, d in enumerate(gcal)}
            fy_gi = [i for i, d in enumerate(gcal) if cfg.start <= d <= cfg.end]
            fy_dates = [gcal[i] for i in fy_gi]
            end_gi = max(fy_gi) if fy_gi else 0

            cands = []
            for code in caches:
                raws = cbt._candidates_for_stock(
                    code, fires.get(code, []), caches[code],
                    corr_maps.get(code, {}), zs_maps.get(code, {}),
                    cfg.start, cfg.end, _N_GATE)
                pk = packs[code]
                for r in raws:
                    fi = pk.sidx.get(r.entry_date)
                    if fi is None:
                        continue
                    F_gi = ggidx.get(r.entry_date)
                    if F_gi is None:
                        continue
                    be = pk.burst_end(fi)
                    we_gi = ggidx.get(pk.dates[be], F_gi)
                    fns = pk.first_nosup(fi, be)
                    fns_gi = ggidx.get(pk.dates[fns]) if fns is not None else None
                    c = _Cand()
                    c.pack = pk; c.corr = r.corr_mode
                    c.F_gi = F_gi; c.we_gi = we_gi if we_gi is not None else F_gi
                    c.fns_gi = fns_gi
                    cands.append(c)

            active = defaultdict(list)
            for ci, c in enumerate(cands):
                for gi in range(c.F_gi, min(c.we_gi, end_gi - 1) + 1):
                    active[gi].append(ci)

            for k in range(_K):
                order = list(range(len(cands)))
                random.Random(k).shuffle(order)
                rankpos = {ci: p for p, ci in enumerate(order)}
                for a in arms:
                    daily = defaultdict(float)
                    entered = [False] * len(cands)
                    occ = []
                    for gi in range(end_gi + 1):
                        if occ:
                            occ = [o for o in occ if o[1] > gi]
                        act = active.get(gi)
                        if not act:
                            continue
                        hi = sum(1 for o in occ if o[0] == "high"); lo = len(occ) - hi
                        elig = []
                        for ci in act:
                            if entered[ci]:
                                continue
                            c = cands[ci]
                            if a == "VETO" and c.fns_gi is not None and c.fns_gi <= gi:
                                continue
                            elig.append(ci)
                        elig.sort(key=lambda ci: rankpos[ci])
                        td = gcal[gi]
                        for ci in elig:
                            c = cands[ci]
                            if c.corr == "high" and hi >= _MAX_HIGH:
                                continue
                            if c.corr != "high" and lo >= _MAX_LOW:
                                continue
                            si = c.pack.sidx.get(td)
                            if si is None or si + HOLD >= len(c.pack.close):
                                continue
                            fopen = c.pack.open[si + 1]
                            if not (fopen > 0):
                                continue
                            prev = fopen
                            for hh in range(HOLD):
                                px = c.pack.close[si + 1 + hh]
                                if not (px > 0):
                                    continue
                                daily[c.pack.dates[si + 1 + hh]] += (px / prev - 1.0) / _SLOTS
                                prev = px
                            entered[ci] = True
                            occ.append((c.corr, gi + HOLD))
                            if c.corr == "high":
                                hi += 1
                            else:
                                lo += 1
                            if k < 5:
                                book[a].append(c.pack.close[si + HOLD] / fopen - 1.0)
                            if k == 0:
                                ntr[a] += 1
                    st[a][k] += [daily.get(d, 0.0) for d in fy_dates][1:]
            logger.info("  HOLD={} {} done (cands={})", HOLD, cfg.label, len(cands))

        sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in arms}
        rt = {a: np.array([_metrics(st[a][k])[0] for k in range(_K)]) for a in arms}
        dd = {a: np.array([_metrics(st[a][k])[2] for k in range(_K)]) for a in arms}

        print("\n" + "=" * 84)
        print(f"WAITING-slot no_supply selection veto vs no-veto — {_K} paired shuffles, "
              f"6-slot, HOLD={HOLD}")
        print("=" * 84)
        for a in arms:
            bk = np.asarray(book[a])
            mr = bk.mean() * 100 if bk.size else float("nan")
            win = (bk > 0).mean() * 100 if bk.size else float("nan")
            print(f"  {a:<6} trades/yr~{ntr[a]/8:.0f}  per-trade mean_r {mr:+.2f}%  win {win:.1f}%  "
                  f"(book n={bk.size})")
        print(f"\n{'arm':<8}{'Sharpe':>10}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}{'ret%':>8}{'DD%':>7}")
        for a in arms:
            s_ = sh[a]
            print(f"{a:<8}{s_.mean():>10.2f}{s_.std():>7.2f}{np.percentile(s_,5):>8.2f}"
                  f"{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}"
                  f"{rt[a].mean()*100:>7.0f}%{dd[a].mean()*100:>6.0f}%")
        d = sh["VETO"] - sh["BASE"]; dr = rt["VETO"] - rt["BASE"]
        sep = (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
        print(f"\n[VETO − BASE, paired]  Δ Sharpe mean {d.mean():+.3f} | "
              f"95% CI [{np.percentile(d,2.5):+.3f}, {np.percentile(d,97.5):+.3f}] | "
              f"P(Δ>0)={(d>0).mean():.3f} | Δ ret {dr.mean()*100:+.1f}pp")
        print(f"  -> {'REAL (separated)' if sep else 'NOT separated'}\n")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
