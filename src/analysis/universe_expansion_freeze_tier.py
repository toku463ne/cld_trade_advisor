"""Freeze the universe-expansion tier (Stage-1 pre-reg §8.2, read-only).

Materializes the liquid∩affordable 単元株 tier as a checked-in, auditable code list so
membership is reproducible and cannot be re-tuned on rebuilt-data results. The tier RULE is
frozen in the pre-reg (`docs/analysis/universe_expansion_stage1_preregistration.md` §2):

  affordable : raw close ≤ ¥2,000,000 / 6 / 100 ≈ ¥3,333  (one 単元 fits a ¥2M/6 slot)
  liquid     : median trailing-60-bar turnover_value ≥ ¥100,000,000 / day
  equity     : market ∈ {Prime, Standard, Growth} AND sector33_code present
  as-of      : evaluated per trading day (membership is dynamic in the backtest)

The BRIDGE set written here = the UNION of codes that meet the rule on ≥1 as-of day across
the backtest span (FY2018-04-01 → data end). Loading a code's OHLCV is all-or-nothing, so we
bridge any code that is ever tradable; the backtest then applies the as-of rule dynamically
(a code only produces candidates on days it actually qualifies). "≥1 day" is the maximal safe
set — no extra threshold parameter is introduced.

Writes: docs/analysis/universe_expansion_tier.txt  (col 1 = jq local code; the bridge parser
reads col 1). Run:
  PYTHONPATH=. uv run --env-file devenv python -m src.analysis.universe_expansion_freeze_tier
"""
from __future__ import annotations

import datetime
import sys

import numpy as np
from loguru import logger
from sqlalchemy import select

_PRICE_CEIL = 2_000_000 / 6 / 100          # ≈ ¥3,333
_TURN_FLOOR = 100_000_000.0                # ¥100M median daily
_CORR_WIN = 60                             # trailing window for the turnover median
_START = datetime.date(2018, 4, 1)
_EQUITY_MARKETS = {"プライム", "スタンダード", "グロース"}   # Prime / Standard / Growth
_OUT = "docs/analysis/universe_expansion_tier.txt"


def run() -> None:
    from src.data.db import get_session
    from src.data.jquants_collector import to_yf_code
    from src.data.jquants_models import JqDailyQuote, JqListed, JqTopix
    from src.data.models import Ohlcv1d

    with get_session() as s:
        cal = [d for (d,) in s.execute(select(JqTopix.date).where(JqTopix.close.isnot(None))
                                       .order_by(JqTopix.date))]
        cohort = {c for (c,) in s.execute(select(Ohlcv1d.stock_code).distinct())}
        listed = {c: (s33 or "", mkt or "", nm or "") for c, s33, mkt, nm in s.execute(
            select(JqListed.code, JqListed.sector33_name, JqListed.market_code_name,
                   JqListed.company_name))}
        codes = [c for (c,) in s.execute(select(JqDailyQuote.code).distinct()
                                         .order_by(JqDailyQuote.code))]
        col_of = {d: i for i, d in enumerate(cal)}
        row_of = {c: i for i, c in enumerate(codes)}
        nC, nD = len(codes), len(cal)
        rawc = np.full((nC, nD), np.nan, dtype=np.float32)
        turn = np.full((nC, nD), np.nan, dtype=np.float32)
        stream = s.connection().execution_options(stream_results=True, yield_per=200_000)
        for code, d, cl, tv in stream.execute(
                select(JqDailyQuote.code, JqDailyQuote.date, JqDailyQuote.close,
                       JqDailyQuote.turnover_value)):
            ci = col_of.get(d)
            ri = row_of.get(code)
            if ci is None or ri is None:
                continue
            if cl is not None:
                rawc[ri, ci] = float(cl)
            if tv is not None:
                turn[ri, ci] = float(tv)

    span = np.array([i for i, d in enumerate(cal) if d >= _START and i >= _CORR_WIN])
    n_span = len(span)
    logger.info("loaded {} codes, {} cal days; {} as-of span days {}..{}",
                nC, nD, n_span, cal[span[0]], cal[span[-1]])
    from numpy.lib.stride_tricks import sliding_window_view

    rows = []
    per_day_count = np.zeros(n_span, dtype=np.int64)
    with np.errstate(invalid="ignore"):
        for code in codes:
            s33, mkt, nm = listed.get(code, ("", "", ""))
            if mkt not in _EQUITY_MARKETS or not s33:           # equity filter
                continue
            ri = row_of[code]
            price = rawc[ri]
            tv = turn[ri].astype(np.float64)
            # rolling median turnover: window j (len 61) covers tv[ai-60:ai+1] at ai=j+_CORR_WIN
            med_all = np.nanmedian(sliding_window_view(tv, _CORR_WIN + 1), axis=1)
            p_span = price[span]
            mt_span = med_all[span - _CORR_WIN]
            ok = (p_span > 0) & (p_span <= _PRICE_CEIL) & (mt_span >= _TURN_FLOOR)
            qd = int(ok.sum())
            if qd < 1:
                continue
            per_day_count += ok.astype(np.int64)
            last = np.where(ok)[0][-1]
            rows.append((code, to_yf_code(code), to_yf_code(code) in cohort, s33, mkt,
                         qd, qd / n_span, float(p_span[last]), float(mt_span[last])))

    rows.sort(key=lambda r: r[5], reverse=True)
    n_in225 = sum(1 for r in rows if r[2])
    med_perday = int(np.median(per_day_count))
    logger.info("tier union: {} equity codes ever-qualify ({} in 225); median {}/day qualify",
                len(rows), n_in225, med_perday)

    with open(_OUT, "w") as f:
        f.write(f"# Universe-expansion tier — FROZEN {datetime.date.today()} "
                f"(Stage-1 pre-reg §8.2)\n")
        f.write(f"# rule: close ≤ ¥{_PRICE_CEIL:,.0f} AND median-{_CORR_WIN}b turnover ≥ "
                f"¥{_TURN_FLOOR:,.0f}/d AND market∈{{Prime,Standard,Growth}} AND sector33\n")
        f.write(f"# span: {cal[span[0]]} .. {cal[span[-1]]} ({n_span} as-of days); "
                f"bridge set = ever-qualifies (≥1 day)\n")
        f.write(f"# tier size: {len(rows)} codes ({n_in225} already in the 225 cohort); "
                f"median {med_perday} qualify/day (probe reported ~1008 affordable∩liquid)\n")
        f.write(f"# columns: code  yf_code  in225  qual_days  frac_days  last_close  "
                f"med_turnover  sector33\n")
        for code, yf, in225, s33, mkt, qd, frac, lp, lt in rows:
            f.write(f"{code}\t{yf}\t{int(in225)}\t{qd}\t{frac:.3f}\t"
                    f"{lp:.0f}\t{lt:.0f}\t{s33}\n")
    print(f"\nwrote {len(rows)} codes -> {_OUT}")
    print(f"  {n_in225} already in the 225 cohort; {len(rows)-n_in225} NEW codes to bridge")
    print(f"  median {med_perday} qualify on a given day")
    # affordability cut among the 225 (context): how many of the 225 are price-eligible at all
    print(f"\n  top 8 by qualifying-day count:")
    for code, yf, in225, s33, mkt, qd, frac, lp, lt in rows[:8]:
        print(f"    {code:<7}{yf:<10}{'[225]' if in225 else '     '} qual {qd:>4}d "
              f"({frac*100:.0f}%) ¥{lp:.0f} {s33}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
