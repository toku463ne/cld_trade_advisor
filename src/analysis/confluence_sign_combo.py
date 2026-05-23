"""Sign-combination composition of confluence fires, per FY (read-only).

Operator (2026-05-23): suspects FY2021 (worst confluence year) had FEWER "break"
signs participating in the ≥3-sign confluences — i.e. the confluences that fired
were carried by str_*/rev_* mean-reversion signs rather than brk_* breakouts.

This DESCRIPTIVE probe reconstructs, for every N≥3 confluence fire (burst-deduped,
the same candidate the backtest emits), the SET of valid bullish signs at the
entry day, then aggregates per FY:
  - n_fires, avg confluence size (# valid signs)
  - per-sign participation rate (% of fires that include each sign)
  - break-sign content: avg # break signs / fire, % of fires with ≥1 break sign

Production bullish set (10 signs, matches confluence_benchmark.py) + N=3 gate +
10-bar cooldown.  Break signs = the 4 level-breakouts.

This is candidate-level (pre-slot), the signal population — NOT the ~50-trade
filled book.  No gate, no ship test.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_sign_combo
"""
from __future__ import annotations

import datetime
import sys
from collections import Counter, defaultdict

from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS
from src.data.db import get_session
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_BREAK = {"brk_sma", "brk_bol", "brk_kumo_hi", "brk_tenkan_hi"}
_N_GATE = 3
_COOLDOWN = 10
_FYS = [FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)
_SIGN_ORDER = ["brk_sma", "brk_bol", "brk_kumo_hi", "brk_tenkan_hi",   # break
               "str_hold", "str_lead", "str_lag", "rev_lo", "rev_nlo", "chiko_hi"]


def _fire_sets_for_stock(fires, cache, fy_start, fy_end):
    """Replicate _candidates_for_stock burst-dedup, but YIELD the valid-sign set."""
    if not cache.bars:
        return []
    trading_dates, seen = [], set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); trading_dates.append(d)
    trading_dates.sort()
    date_to_idx = {d: i for i, d in enumerate(trading_dates)}

    valid_per_date = defaultdict(set)
    for sign, fd in fires:
        if fd not in date_to_idx:
            continue
        fi = date_to_idx[fd]
        vb = _BULLISH.get(sign, 5)
        for j in range(fi, min(fi + vb + 1, len(trading_dates))):
            valid_per_date[j].add(sign)

    out, last = [], -10_000
    for i, d in enumerate(trading_dates):
        if d < fy_start or d > fy_end:
            continue
        sset = valid_per_date.get(i, set())
        if len(sset) < _N_GATE:
            continue
        if i - last < _COOLDOWN:
            continue
        out.append(frozenset(sset))
        last = i
    return out


def run() -> None:
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    per_fy = {}      # fy -> list[frozenset]
    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
        se = cfg.end + datetime.timedelta(days=60)
        sets = []
        with get_session() as s:
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    sets += _fire_sets_for_stock(fires.get(code, []), c, cfg.start, cfg.end)
        per_fy[cfg.label] = sets
        logger.info("  {} done ({} confluence fires)", cfg.label, len(sets))

    print("\n" + "=" * 100)
    print("CONFLUENCE SIGN-COMBINATION COMPOSITION — N>=3, production 10-sign set, "
          "candidate-level (pre-slot)")
    print("=" * 100)

    # ── A. break-content summary per FY ──
    print(f"\nA. BREAK-SIGN CONTENT per FY  (break = {', '.join(sorted(_BREAK))})")
    print(f"  {'FY':<9}{'fires':>7}{'avg size':>10}{'avg #break':>12}{'%>=1 break':>12}"
          f"{'avg #break/size':>17}")
    for cfg in _FYS:
        sets = per_fy.get(cfg.label)
        if not sets:
            continue
        n = len(sets)
        sizes = [len(s) for s in sets]
        nbrk = [len(s & _BREAK) for s in sets]
        pct_brk = 100.0 * sum(1 for x in nbrk if x >= 1) / n
        frac = sum(b / sz for b, sz in zip(nbrk, sizes)) / n
        note = "  <-- worst FY" if cfg.label == "FY2021" else ("  OOS" if cfg.label == "FY2025" else "")
        print(f"  {cfg.label:<9}{n:>7}{sum(sizes)/n:>10.2f}{sum(nbrk)/n:>12.2f}"
              f"{pct_brk:>11.1f}%{frac*100:>16.1f}%{note}")

    # ── B. per-sign participation rate (FY × sign) ──
    print(f"\nB. PER-SIGN PARTICIPATION RATE  (% of that FY's fires whose valid set includes the sign)")
    hdr = f"  {'FY':<9}" + "".join(f"{s.replace('brk_','b.').replace('str_','s.').replace('rev_','r.'):>9}" for s in _SIGN_ORDER)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for cfg in _FYS:
        sets = per_fy.get(cfg.label)
        if not sets:
            continue
        n = len(sets)
        cnt = Counter()
        for s in sets:
            cnt.update(s)
        row = f"  {cfg.label:<9}"
        for sg in _SIGN_ORDER:
            row += f"{100.0*cnt.get(sg,0)/n:>8.0f}%"
        print(row)
    print("  (columns: b.=brk_ s.=str_ r.=rev_ ; first 4 are the BREAK signs)")

    # ── C. most common exact combinations, FY2021 vs a bull FY ──
    for fy in ("FY2021", "FY2020", "FY2025"):
        sets = per_fy.get(fy)
        if not sets:
            continue
        print(f"\nC. {fy}: top-6 exact sign combinations ({len(sets)} fires)")
        top = Counter(tuple(sorted(s)) for s in sets).most_common(6)
        for combo, c in top:
            has = "brk✓" if (set(combo) & _BREAK) else "NObrk"
            print(f"    {c:>4}x [{has}] {', '.join(combo)}")

    print("\n  (Operator hypothesis: FY2021 should show LOWER avg #break, lower %>=1 break, "
          "lower break participation in B than bull FYs.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
