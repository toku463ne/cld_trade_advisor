"""FY2019 beta-stripped sign-decomposition — was the 'loser pattern' a sign-combination or beta?

Operator question (2026-06-01): the monthly winner/loser sign-list idea was rejected as a
selection rule, but the sharper claim is — "in FY2019 the LOSER trades were a similar
COMBINATION of signs; continuing to feed those signs isn't clever." The standing counter
(project_confluence_fy_attribution / project_confluence_market_neutral) is that FY2019's
losses were ~89% N225 beta, not a distinguishable sign-combination. This script tests that
claim DIRECTLY on the canonical filled book.

For each FILLED trade in the canonical 6-slot confluence book (10-sign bullish set, N>=3,
ZsTpSl, run_simulation slot caps — identical to confluence_market_neutral / alpha_contrast):
    raw_r  = return_pct                                  (what the book booked)
    beta   = cov(r_stock, r_N225)/var(r_N225), trailing _BETA_WIN bars ending at entry
             (look-ahead-safe)
    n225_r = N225 close-to-close return over the trade's exact hold window
    alpha  = raw_r - beta * n225_r                       (beta-stripped)
    signs  = the validity-windowed bullish sign SET valid at entry (the "combination")

FY2019 (primary) is contrasted with FY2020 + FY2023 (bull years where the SAME signs won) to
test label stability — if a sign's beta-stripped alpha sign FLIPS year to year, a trailing
loser-list whipsaws.

Decisive cuts on FY2019:
  1. Pooled raw vs alpha (how much of the book's FY2019 PnL is beta).
  2. Winners vs losers (by raw_r sign): are losers just HIGHER-beta longs into a worse tide,
     or do they carry distinctly negative ALPHA?  If beta(loser)>beta(winner) and
     alpha(loser)~=alpha(winner)~=0, the loser pattern is beta, not selection.
  3. Per-sign: mean raw_r vs mean alpha among fills where each sign was valid. If the raw
     spread across signs >> the alpha spread, the sign separation is beta-driven (collapses
     once the tide is removed).
  4. Sign-combination: most common valid-sign SETS among losers, with their alpha.
  5. Stability: per-sign mean alpha FY2019 vs FY2020 vs FY2023 (does the label flip?).

n-thin: ~50 filled trades/FY; any sub-split is descriptive, not inferential. Read-only.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_2019_beta_strip
"""
from __future__ import annotations

import datetime
import statistics
import sys
from bisect import bisect_right
from collections import Counter, defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_market_neutral import _close_map, _ret_series
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_BETA_WIN = 60
_PRIMARY = "FY2019"
_CONTRAST = ["FY2020", "FY2023"]          # bull years; same signs won
_FYS = [c for c in RS_FY_CONFIGS if c.label in ([_PRIMARY] + _CONTRAST)]


def _valid_map_for_stock(fires, cache) -> dict[datetime.date, frozenset]:
    """date -> frozenset of bullish signs valid that day (validity-windowed), per
    the exact logic in cbt._candidates_for_stock."""
    tds, seen = [], set()
    for b in cache.bars:
        d = b.dt.date()
        if d not in seen:
            seen.add(d)
            tds.append(d)
    tds.sort()
    idx = {d: i for i, d in enumerate(tds)}
    vpd: dict[int, set] = defaultdict(set)
    for sign, fd in fires:
        if fd not in idx:
            continue
        fi = idx[fd]
        vb = _BULLISH.get(sign, 5)
        for j in range(fi, min(fi + vb + 1, len(tds))):
            vpd[j].add(sign)
    return {d: frozenset(vpd.get(i, set())) for i, d in enumerate(tds)}


def _build_fy(cfg, fires):
    """Return list of per-fill records for one FY."""
    codes = cbt._stocks_for_fy(cfg.stock_set)
    if not codes:
        return []
    ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 120)
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
    valid_maps = {code: _valid_map_for_stock(fires.get(code, []), c) for code, c in caches.items()}
    cands = []
    for code in caches:
        cands.extend(cbt._candidates_for_stock(
            code, fires.get(code, []), caches[code],
            corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
    results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)

    n_dts, n_ret = _ret_series(n225)
    n_didx = {d: i for i, d in enumerate(n_dts)}
    n_cdts, n_cls = _close_map(n225)

    def _cls_at(d):
        i = bisect_right(n_cdts, d) - 1
        return n_cls[i] if i >= 0 else None

    recs = []
    for r in results:
        c = caches.get(r.stock_code)
        if c is None:
            continue
        s_dts, s_ret = _ret_series(c)
        s_didx = {d: i for i, d in enumerate(s_dts)}
        ei = s_didx.get(r.entry_date)
        if ei is None or ei < _BETA_WIN:
            continue
        common = [d for d in s_dts[ei - _BETA_WIN:ei] if d in n_didx]
        rs = np.array([s_ret[s_didx[d]] for d in common])
        rn = np.array([n_ret[n_didx[d]] for d in common])
        m = ~(np.isnan(rs) | np.isnan(rn))
        rs, rn = rs[m], rn[m]
        if len(rn) < 30 or rn.var() == 0:
            continue
        beta = float(np.cov(rs, rn)[0, 1] / rn.var())
        ne, nx = _cls_at(r.entry_date), _cls_at(r.exit_date)
        if not ne or not nx:
            continue
        n225_r = (nx - ne) / ne
        alpha = r.return_pct - beta * n225_r
        signs = valid_maps.get(r.stock_code, {}).get(r.entry_date, frozenset())
        recs.append(dict(fy=cfg.label, entry_date=r.entry_date, raw=r.return_pct,
                         alpha=alpha, beta=beta, n225_r=n225_r, corr=r.corr_mode, signs=signs))
    logger.info("  {} built ({} fills)", cfg.label, len(recs))
    return recs


def _mp(xs, k):
    v = [x[k] for x in xs]
    return statistics.mean(v) * 100 if v else float("nan")


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    all_recs = []
    for cfg in _FYS:
        all_recs.extend(_build_fy(cfg, fires))

    p = [x for x in all_recs if x["fy"] == _PRIMARY]
    win = [x for x in p if x["raw"] > 0]
    los = [x for x in p if x["raw"] <= 0]

    print("\n" + "=" * 84)
    print(f"FY2019 BETA-STRIPPED SIGN DECOMPOSITION  (canonical 6-slot book, n={len(p)} fills)")
    print("=" * 84)

    # 1. pooled
    raw_m = _mp(p, "raw"); a_m = _mp(p, "alpha")
    beta_share = (1 - a_m / raw_m) * 100 if raw_m else float("nan")
    print(f"\n1. POOLED FY2019:")
    print(f"   raw mean_r {raw_m:+.2f}%   alpha(beta-stripped) {a_m:+.2f}%   "
          f"avg beta {statistics.mean([x['beta'] for x in p]):.2f}")
    print(f"   => beta explains ~{beta_share:.0f}% of the booked FY2019 return.")

    # 2. winners vs losers
    print(f"\n2. WINNERS vs LOSERS (split by raw_r sign):")
    print(f"   {'cohort':<10}{'n':>4}{'raw':>9}{'alpha':>9}{'beta':>7}{'N225 hold':>11}")
    for lab, xs in [("winners", win), ("losers", los)]:
        if not xs:
            continue
        print(f"   {lab:<10}{len(xs):>4}{_mp(xs,'raw'):>+8.2f}%{_mp(xs,'alpha'):>+8.2f}%"
              f"{statistics.mean([x['beta'] for x in xs]):>7.2f}{_mp(xs,'n225_r'):>+10.2f}%")
    print("   KEY: if losers have HIGHER beta + worse N225-hold but alpha ~= winners' alpha,")
    print("        the loser pattern is BETA (high-beta longs into a falling tide), not signs.")

    # 3. per-sign raw vs alpha
    print(f"\n3. PER-SIGN (fills where sign was valid at entry) — FY2019:")
    print(f"   {'sign':<14}{'n':>4}{'raw':>9}{'alpha':>9}{'beta':>7}")
    sign_rows = []
    for sg in _BULLISH:
        xs = [x for x in p if sg in x["signs"]]
        if len(xs) < 5:
            sign_rows.append((sg, len(xs), float("nan"), float("nan")))
            print(f"   {sg:<14}{len(xs):>4}{'  (n<5)':>9}")
            continue
        rm, am = _mp(xs, "raw"), _mp(xs, "alpha")
        sign_rows.append((sg, len(xs), rm, am))
        print(f"   {sg:<14}{len(xs):>4}{rm:>+8.2f}%{am:>+8.2f}%"
              f"{statistics.mean([x['beta'] for x in xs]):>7.2f}")
    fin = [r for r in sign_rows if r[2] == r[2]]
    if fin:
        raw_spread = max(r[2] for r in fin) - min(r[2] for r in fin)
        a_spread = max(r[3] for r in fin) - min(r[3] for r in fin)
        print(f"\n   cross-sign SPREAD (max-min mean):  raw {raw_spread:.2f}pp   alpha {a_spread:.2f}pp")
        print(f"   => if alpha spread << raw spread, sign separation is beta, not a real loser combo.")

    # 4. loser sign-combinations
    print(f"\n4. MOST COMMON SIGN-SETS AMONG FY2019 LOSERS (the 'loser combination'):")
    cnt = Counter(x["signs"] for x in los)
    for combo, n in cnt.most_common(6):
        xs = [x for x in p if x["signs"] == combo]
        nm = ",".join(sorted(combo))
        print(f"   n={n:<3} alpha {_mp(xs,'alpha'):>+7.2f}%  raw {_mp(xs,'raw'):>+7.2f}%  {{{nm}}}")
    print("   (if these same sets appear among WINNERS too / have ~0 alpha, the combo isn't a loser)")

    # 5. stability across years
    print(f"\n5. PER-SIGN ALPHA STABILITY — does the label flip? (mean alpha%, n):")
    print(f"   {'sign':<14}{'FY2019':>14}{'FY2020':>14}{'FY2023':>14}")
    for sg in _BULLISH:
        cells = []
        for fy in [_PRIMARY] + _CONTRAST:
            xs = [x for x in all_recs if x["fy"] == fy and sg in x["signs"]]
            cells.append(f"{_mp(xs,'alpha'):>+7.2f}%({len(xs):>2})" if len(xs) >= 5 else f"{'n<5':>10}")
        print(f"   {sg:<14}{cells[0]:>14}{cells[1]:>14}{cells[2]:>14}")
    print("\n   VERDICT: a sign whose FY2019 alpha is negative but FY2020/FY2023 alpha is positive")
    print("   is a label that FLIPS — a trailing monthly loser-list would cut it right before it")
    print("   recovers (the operator's own 'same signs won another year' = the whipsaw proof).")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
