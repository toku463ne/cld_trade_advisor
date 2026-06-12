"""Stage 0 — does adding persistent STATE votes discriminate forward EV, or is it a thermometer?

Operator (2026-05-30): keep the EVENT signs as-is, but ALSO add a persistent-state vote
(e.g. price above tenkan) so a fresh breakout that still holds gives +2 toward N, and
re-tune N. The decisive question: does `aug_count = event_count + state_count` carry EV
that `event_count` alone doesn't — or is the state term just a trend label (flat EV within
each event level = thermometer)?

  event_count : # distinct bullish-sign fires currently valid (validity-windowed, the
                production confluence count), from SignBenchmark fires.
  state_count : # of {close>tenkan, close>SMA20, close>kumo_top} true today — the
                persistent states matching the 3 breakout event-signs (brk_tenkan_hi,
                brk_sma, brk_kumo_hi). Range 0–3.

2D table: forward +20-bar return (entry T+1 open) by (event_count × state_count). If EV
rises with state_count WITHIN an event_count row → state adds info (escalate to count×N
A/B). If flat within each row → state is redundant trend strength (thermometer; settled).

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_state_vote_stage0
"""
from __future__ import annotations

import datetime
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_benchmark import _FYS
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.indicators.ichimoku import calc_ichimoku
from src.simulator.cache import DataCache
from src.strategy.confluence_sign import _BULLISH_SIGNS, _VALID_BARS

_H = 20
_SMA_W = 20
_TEN_P, _KIJ_P, _SSB_P, _DISP = 9, 26, 52, 26


def _fires(signs):
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(signs)))).all()
    f = defaultdict(list)
    for sg, st, fa in rows:
        f[st].append((sg, fa.date() if hasattr(fa, "date") else fa))
    return f


def _levels(cache):
    """deduped (dts, open, close, tenkan, sma, kumo_top)."""
    seen, dts, o, hi, lo, cl = set(), [], [], [], [], []
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            hi[-1] = max(hi[-1], b.high); lo[-1] = min(lo[-1], b.low); cl[-1] = b.close
            continue
        seen.add(d); dts.append(d); o.append(b.open); hi.append(b.high); lo.append(b.low); cl.append(b.close)
    n = len(dts)
    if n < _SSB_P + _DISP + 2:
        return None
    ichi = calc_ichimoku(hi, lo, cl, tenkan_period=_TEN_P, kijun_period=_KIJ_P,
                         senkou_b_period=_SSB_P, displacement=_DISP)
    ten = np.array(ichi["tenkan"], float)
    ssa = np.array(ichi["senkou_a"], float); ssb = np.array(ichi["senkou_b"], float)
    d = int(ichi["displacement"])
    top = np.full(n, np.nan); top[d:] = np.maximum(ssa[:n - d], ssb[:n - d])
    sma = pd.Series(cl).rolling(_SMA_W).mean().to_numpy()
    return dts, np.array(o), np.array(cl), ten, sma, top


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    fires = _fires(_BULLISH_SIGNS)
    # rows: (event_count, state_count, fwd_ret)
    rows: list[tuple[int, int, float]] = []

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=260)
        se = cfg.end + datetime.timedelta(days=90)
        with get_session() as s:
            caches = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    caches[code] = c
        for code, c in caches.items():
            lv = _levels(c)
            if lv is None:
                continue
            dts, o, cl, ten, sma, top = lv
            n = len(dts)
            date_to_idx = {d: i for i, d in enumerate(dts)}
            # event_count per day (validity-windowed), from DB fires
            valid: dict[int, set] = defaultdict(set)
            for sg, fd in fires.get(code, []):
                fi = date_to_idx.get(fd)
                if fi is None:
                    continue
                vb = _VALID_BARS.get(sg, 5)
                for j in range(fi, min(fi + vb + 1, n)):
                    valid[j].add(sg)
            for i, d in enumerate(dts):
                if not (cfg.start <= d <= cfg.end) or i + 1 >= n:
                    continue
                ec = len(valid.get(i, ()))
                # state votes
                sc = 0
                if not np.isnan(ten[i]) and cl[i] > ten[i]:
                    sc += 1
                if not np.isnan(sma[i]) and cl[i] > sma[i]:
                    sc += 1
                if not np.isnan(top[i]) and top[i] > 0 and cl[i] > top[i]:
                    sc += 1
                if ec + sc < 2:                       # only the firing-relevant region
                    continue
                entry = o[i + 1]
                if not entry:
                    continue
                exit_i = min(i + 1 + _H, n - 1)
                rows.append((ec, sc, float(cl[exit_i] / entry - 1.0)))
        logger.info("  {} done ({} rows so far)", cfg.label, len(rows))

    arr_e = np.array([r[0] for r in rows]); arr_s = np.array([r[1] for r in rows])
    arr_r = np.array([r[2] for r in rows])

    def _cell(mask):
        if mask.sum() == 0:
            return None
        rr = arr_r[mask]
        return len(rr), (rr > 0).mean(), rr.mean()

    e_buckets = [(0, "0"), (1, "1"), (2, "2"), (3, "≥3")]
    print("\n" + "=" * 86)
    print(f"STATE-VOTE DISCRIMINATION — forward +{_H}-bar return by event_count × state_count")
    print("=" * 86)
    print("Q: within an event_count ROW, does mean_ret RISE with state_count? "
          "(yes=info, flat=thermometer)\n")
    print(f"{'event\\state':<12}" + "".join(f"{f'state={s}':>16}" for s in (0, 1, 2, 3)))
    for ev, elab in e_buckets:
        emask = (arr_e >= 3) if ev == 3 else (arr_e == ev)
        line = f"{elab:<12}"
        for s in (0, 1, 2, 3):
            cell = _cell(emask & (arr_s == s))
            line += (f"{f'{cell[2]*100:+.2f}%/{cell[1]*100:.0f}%/{cell[0]}':>16}" if cell
                     else f"{'—':>16}")
        print(line)
    print("\n(cell = mean_ret / DR / n)")

    print("\n--- marginals ---")
    print(f"{'event_count':<14}{'n':>8}{'DR':>7}{'mean':>9}     {'aug=e+s':<10}{'n':>8}{'DR':>7}{'mean':>9}")
    for ev, elab in [(0, "0"), (1, "1"), (2, "2"), (3, "≥3")]:
        m = (arr_e >= 3) if ev == 3 else (arr_e == ev)
        c = _cell(m)
        aug = arr_e + arr_s
        al = {3: "≥3", 4: "4", 5: "5", 6: "≥6"}.get(ev + 3 if ev < 3 else 6) if False else None
        # aug rows: 3,4,5,>=6
        amap = {0: 3, 1: 4, 2: 5, 3: 6}
        av = amap[ev]
        am = (aug >= 6) if av == 6 else (aug == av)
        ca = _cell(am)
        left = f"{elab:<14}{c[0]:>8}{c[1]*100:>6.0f}%{c[2]*100:>+8.2f}%" if c else f"{elab:<14}{'—':>8}"
        alab = "≥6" if av == 6 else str(av)
        right = f"{alab:<10}{ca[0]:>8}{ca[1]*100:>6.0f}%{ca[2]*100:>+8.2f}%" if ca else f"{alab:<10}{'—':>8}"
        print(f"{left}     {right}")

    # Decisive contrast: aug>=3 fired via STATE (event light) vs via EVENTS
    aug = arr_e + arr_s
    via_state = _cell((aug >= 3) & (arr_e <= 1))
    via_event = _cell((aug >= 3) & (arr_e >= 3))
    mixed     = _cell((aug >= 3) & (arr_e == 2))
    print("\n--- decisive: composition of an aug>=3 firing ---")
    for lab, c in [("event-light (event≤1, state-driven)", via_state),
                   ("mixed (event=2)", mixed),
                   ("event-heavy (event≥3, current gate)", via_event)]:
        print(f"  {lab:<40} " + (f"n={c[0]:<6} DR={c[1]*100:.0f}% mean={c[2]*100:+.2f}%" if c else "—"))
    print("\nIf event-light aug>=3 firings are MUCH worse than event-heavy, the state vote "
          "lets the gate fire early on weak setups (dilution) — REJECT the idea.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
