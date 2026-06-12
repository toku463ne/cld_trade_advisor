"""Stage 0 — pure-stock accumulation (volume) sign, orthogonal to the breakout cluster.

Operator (2026-05-30): after brk_previous_2peaks REJECT (collinear with the 5 breakout
signs, see project_brk_prev2peaks_stage0_reject), scope an ORTHOGONAL confluence member.
The unoccupied axis: heavy buying visible in VOLUME while price is still contained in its
base — before any breakout. All existing "volume" signs (div_gap, str_hold, div_peer,
div_vol) are N225-RELATIVE; none is a pure single-stock accumulation sign.

Two N225-agnostic, causal operationalizations:

  A  obv_div   — OBV makes a new L-bar high while PRICE does NOT (bullish OBV divergence),
                 transition-gated (OBV was not at a new high the prior bar).
  B  vol_absorb— over L bars up-close volume >= R x down-close volume (effort) yet net
                 price change ~ 0 (no result = absorption), price still under L-bar high,
                 transition-gated.

Both fire on CONTAINED price → structurally orthogonal to brk_* by construction. This
probe verifies that empirically and checks the pattern carries.

Gates (kill criteria explicit):
  (A) STANDALONE DR — must clear 50% on the FY2018-2024 POOL (not just the FY2025 bull,
      the beta trap that sank brk_previous_2peaks). FY2025 OOS reported separately.
  (B) ORTHOGONALITY (binding) — fresh% vs the FULL 10-sign confluence bullish set. We
      WANT high fresh% here (opposite of 2peaks' 6-9%). Low fresh% => DOA, stop.

Forward outcome = entry T+1 open, held H=20 bars, exit close.  Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.accum_volume_stage0
"""
from __future__ import annotations

import datetime
import math

import numpy as np
from loguru import logger

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_benchmark import _FYS
from src.data.db import get_session
from src.simulator.cache import DataCache
from src.strategy.confluence_sign import _BULLISH_SIGNS, _VALID_BARS, _build_detector

_H = 20            # forward horizon
_L = 20            # accumulation lookback window
_FLAT = 0.03       # |net price change| over L for "no result"
_R = 1.8           # up/down volume ratio for absorption
_COOLDOWN = 5      # bars between fires per stock (dedupe consecutive)


def _daily(cache):
    seen: set[datetime.date] = set()
    dts: list[datetime.date] = []
    o: list[float] = []; hi: list[float] = []; lo: list[float] = []
    cl: list[float] = []; vol: list[float] = []; fbi: list[int] = []
    for i, b in enumerate(cache.bars):
        d = b.dt.date()
        if d in seen:
            if b.high > hi[-1]:
                hi[-1] = b.high
            if b.low < lo[-1]:
                lo[-1] = b.low
            cl[-1] = b.close
            vol[-1] += float(b.volume)
            continue
        seen.add(d); dts.append(d)
        o.append(b.open); hi.append(b.high); lo.append(b.low)
        cl.append(b.close); vol.append(float(b.volume)); fbi.append(i)
    return (dts, np.array(o), np.array(hi), np.array(lo),
            np.array(cl), np.array(vol), fbi)


def _obv(cl: np.ndarray, vol: np.ndarray) -> np.ndarray:
    obv = np.zeros(len(cl))
    for t in range(1, len(cl)):
        if cl[t] > cl[t - 1]:
            obv[t] = obv[t - 1] + vol[t]
        elif cl[t] < cl[t - 1]:
            obv[t] = obv[t - 1] - vol[t]
        else:
            obv[t] = obv[t - 1]
    return obv


def _fires_obv_div(dts, hi, cl, vol):
    n = len(dts)
    if n < _L + 2:
        return
    obv = _obv(cl, vol)
    last_fire = -10_000

    def _obv_newhigh(t):
        return obv[t] > obv[t - _L:t].max()

    def _price_newhigh(t):
        return cl[t] > cl[t - _L:t].max()

    for T in range(_L + 1, n):
        if T - last_fire < _COOLDOWN:
            continue
        if _obv_newhigh(T) and not _price_newhigh(T) and not _obv_newhigh(T - 1):
            yield T
            last_fire = T


def _fires_vol_absorb(dts, hi, cl, vol):
    n = len(dts)
    if n < _L + 2:
        return
    last_fire = -10_000
    for T in range(_L + 1, n):
        if T - last_fire < _COOLDOWN:
            continue
        win_cl = cl[T - _L:T + 1]
        win_vol = vol[T - _L + 1:T + 1]
        dc = np.diff(win_cl)                      # length _L
        up_vol = win_vol[dc > 0].sum()
        dn_vol = win_vol[dc < 0].sum()
        if dn_vol <= 0:
            continue
        ratio = up_vol / dn_vol
        if cl[T - _L] <= 0:
            continue
        net = abs(cl[T] / cl[T - _L] - 1.0)
        contained = cl[T] <= hi[T - _L:T].max()
        if ratio >= _R and net <= _FLAT and contained:
            yield T
            last_fire = T


_VARIANTS = {"obv_div": _fires_obv_div, "vol_absorb": _fires_vol_absorb}


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    # rows[variant] = list of (fy, fwd_ret, n_covalid_full10)
    rows: dict[str, list[tuple[str, float, int]]] = {v: [] for v in _VARIANTS}

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=160)
        se = cfg.end + datetime.timedelta(days=90)
        with get_session() as s:
            n225 = DataCache("^N225", "1d")
            n225.load(
                s,
                datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc),
            )
            caches: dict[str, DataCache] = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(
                    s,
                    datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                    datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc),
                )
                if c.bars:
                    caches[code] = c
        logger.info("{}: {} caches", cfg.label, len(caches))

        for code, c in caches.items():
            dts, o, hi, lo, cl, vol, fbi = _daily(c)
            n = len(dts)
            if n < _L + _H + 2:
                continue
            # full 10-sign bullish detectors for orthogonality
            dets = {}
            for sg in _BULLISH_SIGNS:
                try:
                    dets[sg] = _build_detector(sg, c, n225, 20)
                except Exception as e:  # noqa: BLE001
                    logger.warning("build {} {}: {}", sg, code, e)
                    dets[sg] = None

            for vname, gen in _VARIANTS.items():
                for T in gen(dts, hi, cl, vol):
                    if dts[T] < cfg.start or dts[T] > cfg.end:
                        continue
                    if T + 1 >= n:
                        continue
                    entry = o[T + 1]
                    if entry <= 0:
                        continue
                    xi = min(T + _H, n - 1)
                    ret = cl[xi] / entry - 1.0
                    as_of = c.bars[fbi[T]].dt
                    co = 0
                    for sg, det in dets.items():
                        if det is None:
                            continue
                        r = det.detect(as_of, valid_bars=_VALID_BARS.get(sg, 5))
                        if r is not None:
                            co += 1
                    rows[vname].append((cfg.label, float(ret), co))

    _report(rows)


def _stats(rets):
    if not rets:
        return 0, float("nan"), float("nan")
    a = np.array(rets)
    return len(a), float((a > 0).mean() * 100.0), float(a.mean() * 100.0)


def _binom_p(n, dr_pct):
    if n == 0:
        return float("nan")
    z = (dr_pct / 100.0 - 0.5) / math.sqrt(0.25 / n)
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _report(rows):
    print("\n=== accumulation (volume) signs — Stage 0 ===\n")
    print(f"params: L={_L} H={_H} flat={_FLAT} ratio>={_R} cooldown={_COOLDOWN}\n")
    print("(A) STANDALONE DR  (T+1 open entry)\n")
    print(f"{'variant':>11} {'cohort':>16} {'n':>6} {'DR%':>7} {'mean_r%':>9} {'p(DR>50)':>10}")
    for v in _VARIANTS:
        data = rows[v]
        pool = [r for fy, r, _ in data if fy != "FY2025"]
        oos = [r for fy, r, _ in data if fy == "FY2025"]
        for label, sub in (("FY18-24 pool", pool), ("FY2025 OOS", oos)):
            n, dr, mr = _stats(sub)
            print(f"{v:>11} {label:>16} {n:>6} {dr:>7.1f} {mr:>9.2f} {_binom_p(n, dr):>10.4f}")
        print()

    print("(B) ORTHOGONALITY vs FULL 10-sign bullish set (WANT high fresh%)\n")
    print(f"{'variant':>11} {'n':>6} {'fresh%':>8} {'mean_co':>8}  DR(fresh) vs DR(co)")
    for v in _VARIANTS:
        data = rows[v]
        if not data:
            print(f"{v:>11}      0      —        —   (no fires)")
            continue
        co = np.array([c for _, _, c in data])
        rets = np.array([r for _, r, _ in data])
        fresh = co == 0
        drf = float((rets[fresh] > 0).mean() * 100.0) if fresh.any() else float("nan")
        drc = float((rets[~fresh] > 0).mean() * 100.0) if (~fresh).any() else float("nan")
        print(f"{v:>11} {len(data):>6} {fresh.mean()*100:>8.1f} {co.mean():>8.2f}  "
              f"fresh n={int(fresh.sum())} DR={drf:.1f} | co n={int((~fresh).sum())} DR={drc:.1f}")
    print()


if __name__ == "__main__":
    run()
