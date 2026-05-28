"""Single-index Time-Series Momentum (TSMOM) overlay — discovery probe (read-only, advisory).

The map's only NON-cross-sectional, breadth-immune candidate ([[project-program-direction-2026]]
direction 3 / 5): hold a Japan index ETF when its trailing 12-month return > 0, else go flat (or
short, now that shorting is available); 1 position, monthly — trivial to execute manually. Unlike
PEAD/value this is NOT a cross-sectional play, so the breadth wall that killed those does not apply.
Question: does index TSMOM beat buy-and-hold (Sharpe, and especially DRAWDOWN — TSMOM's documented
value is crisis protection, not alpha), net of cost, robustly across lookbacks?

Two series:
  • jq_topix (in-DB, 2016–2026) — the honest recent decade we'd actually trade. SHORT for a slow
    timing rule (~10 independent 12-mo signals) → treat as indicative only.
  • ^N225 monthly via yfinance (multi-decade, ~1985–2026) — robustness across real bears (GFC 2008,
    2011, 2018, 2020). The decision should lean on this longer history. Graceful fallback if offline.

Caveats: a price index (^N225) and TOPIX exclude dividends → buy-hold's edge is slightly understated
(TSMOM sits in cash part-time, so dividend omission cuts BOTH books, roughly a wash). MOP's headline
Sharpe 1.31 is the 58-instrument diversified FUTURES program — single-index equity TSMOM is much more
modest (~0.3–0.5) and regime-dependent (rode Abenomics; whipsaws in chop). Read-only. Run:
  PYTHONPATH=. uv run --env-file devenv python -m src.analysis.tsmom_index_probe
"""
from __future__ import annotations

import datetime
import math
import sys

import numpy as np
from loguru import logger

_COST_BPS = 30.0          # per position switch (one-way notional traded)
_BORROW_YR = 0.011        # 制度信用 borrow while short
_LOOKBACKS = (3, 6, 9, 12)
_CANON = 12               # pre-registered canonical lookback (Moskowitz–Ooi–Pedersen)


def _monthly_closes_topix():
    """Last-trading-day close of each month from jq_topix (in-DB)."""
    from sqlalchemy import select
    from src.data.db import get_session
    from src.data.jquants_models import JqTopix
    with get_session() as s:
        rows = s.execute(select(JqTopix.date, JqTopix.close)
                         .where(JqTopix.close.isnot(None)).order_by(JqTopix.date)).all()
    by_month: dict[tuple, tuple] = {}
    for d, c in rows:
        by_month[(d.year, d.month)] = (d, float(c))      # last write per month wins (sorted asc)
    months = sorted(by_month)
    dates = [by_month[m][0] for m in months]
    closes = np.array([by_month[m][1] for m in months], dtype=np.float64)
    return dates, closes


def _monthly_closes_yf(ticker: str = "^N225"):
    """Monthly closes via yfinance (multi-decade). Returns (dates, closes) or None if unavailable."""
    try:
        import yfinance as yf
        df = yf.download(ticker, start="1985-01-01", interval="1mo",
                         progress=False, auto_adjust=False)
        if df is None or df.empty:
            return None
        close = df["Close"]
        if hasattr(close, "columns"):                    # yfinance MultiIndex frame
            close = close.iloc[:, 0]
        close = close.dropna()
        dates = [d.date() for d in close.index]
        return dates, close.to_numpy(dtype=np.float64)
    except Exception as e:                                # offline / API change → skip gracefully
        logger.warning("yfinance {} unavailable ({}) — long-history arm skipped", ticker, e)
        return None


def _ann_sharpe(r: np.ndarray) -> float:
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(12)) if sd > 0 else float("nan")


def _cagr(r: np.ndarray) -> float:
    return float(np.prod(1.0 + r) ** (12.0 / len(r)) - 1.0) if len(r) else float("nan")


def _ann_vol(r: np.ndarray) -> float:
    return float(r.std(ddof=1) * math.sqrt(12))


def _max_dd(r: np.ndarray) -> float:
    cum = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(cum)
    return float((cum / peak - 1.0).min())


def _tsmom_book(closes: np.ndarray, lookback: int, allow_short: bool):
    """Monthly TSMOM book over months [lookback+1 .. end]. Signal at end of month i-1
    (sign of trailing-`lookback` return) sets the position held through month i.
    Returns (book_monthly, bh_monthly, n_switches, frac_long, frac_short)."""
    M = len(closes)
    book, bh, pos_prev = [], [], 0
    n_switch = 0
    n_long = n_short = 0
    for i in range(lookback + 1, M):
        sig = closes[i - 1] / closes[i - 1 - lookback] - 1.0
        pos = 1 if sig > 0 else (-1 if allow_short else 0)
        ret = closes[i] / closes[i - 1] - 1.0
        cost = (_COST_BPS / 10_000.0) * abs(pos - pos_prev)   # notional traded on the switch
        borrow = (_BORROW_YR / 12.0) if pos < 0 else 0.0
        book.append(pos * ret - cost - borrow)
        bh.append(ret)
        if pos != pos_prev:
            n_switch += 1
        n_long += pos > 0
        n_short += pos < 0
        pos_prev = pos
    n = len(book)
    return (np.array(book), np.array(bh), n_switch,
            n_long / n if n else 0.0, n_short / n if n else 0.0)


def _report_series(name: str, dates, closes) -> None:
    yrs = (dates[-1] - dates[0]).days / 365.25
    print("\n" + "=" * 92)
    print(f"{name}  |  {dates[0]} → {dates[-1]}  ({len(closes)} monthly bars, {yrs:.1f} yr)")
    print("=" * 92)
    if yrs < 5:
        print("  (too short)")
        return

    # buy-hold reference over the canonical-lookback-trimmed window
    _, bh, _, _, _ = _tsmom_book(closes, _CANON, False)
    print(f"  {'book':<26}{'CAGR':>8}{'vol':>8}{'Sharpe':>8}{'maxDD':>9}{'switch':>8}{'%long':>7}")
    print(f"  {'buy & hold':<26}{_cagr(bh) * 100:>7.1f}%{_ann_vol(bh) * 100:>7.1f}%"
          f"{_ann_sharpe(bh):>8.2f}{_max_dd(bh) * 100:>8.1f}%{'—':>8}{'100':>6}%")
    for allow_short, tag in [(False, "TSMOM long/flat  L=12"), (True, "TSMOM long/short L=12")]:
        bk, _bh, nsw, fl, fs = _tsmom_book(closes, _CANON, allow_short)
        print(f"  {tag:<26}{_cagr(bk) * 100:>7.1f}%{_ann_vol(bk) * 100:>7.1f}%"
              f"{_ann_sharpe(bk):>8.2f}{_max_dd(bk) * 100:>8.1f}%{nsw:>8}{fl * 100:>6.0f}%")

    # lookback robustness (long/flat) — is the 12-mo result a cherry-pick or stable?
    print(f"\n  lookback robustness (long/flat) — Sharpe / maxDD vs buy-hold "
          f"(bh Sharpe {_ann_sharpe(bh):+.2f}, maxDD {_max_dd(bh) * 100:.0f}%):")
    for L in _LOOKBACKS:
        bk, bhh, nsw, fl, _fs = _tsmom_book(closes, L, False)
        print(f"    L={L:>2}mo: Sharpe {_ann_sharpe(bk):+.2f}  maxDD {_max_dd(bk) * 100:>6.1f}%  "
              f"CAGR {_cagr(bk) * 100:>5.1f}%  switches {nsw}")


def _crisis_table(dates, closes) -> None:
    """TSMOM long/flat vs buy-hold across named bear windows (the crisis-alpha case)."""
    bk, _bh, _, _, _ = _tsmom_book(closes, _CANON, False)
    bh_full = np.array([closes[i] / closes[i - 1] - 1.0 for i in range(_CANON + 1, len(closes))])
    bk_dates = dates[_CANON + 1:]
    windows = [("GFC 2008", datetime.date(2008, 6, 1), datetime.date(2009, 3, 31)),
               ("EU/quake 2011", datetime.date(2011, 4, 1), datetime.date(2011, 11, 30)),
               ("2015–16 China", datetime.date(2015, 6, 1), datetime.date(2016, 2, 29)),
               ("2018 Q4", datetime.date(2018, 10, 1), datetime.date(2018, 12, 31)),
               ("COVID 2020", datetime.date(2020, 1, 1), datetime.date(2020, 3, 31)),
               ("2022 chop", datetime.date(2022, 1, 1), datetime.date(2022, 12, 31)),
               ("2025 drawdown", datetime.date(2025, 1, 1), datetime.date(2025, 12, 31))]
    print("\n  CRISIS BEHAVIOR — TSMOM long/flat vs buy-hold (cumulative return in window):")
    any_row = False
    for lab, lo, hi in windows:
        idx = [j for j, d in enumerate(bk_dates) if lo <= d <= hi]
        if len(idx) < 2:
            continue
        any_row = True
        tk = float(np.prod(1.0 + bk[idx]) - 1.0)
        th = float(np.prod(1.0 + bh_full[idx]) - 1.0)
        print(f"    {lab:<16} buy-hold {th * 100:>7.1f}%   TSMOM {tk * 100:>7.1f}%   "
              f"protection {(tk - th) * 100:>+6.1f}pp")
    if not any_row:
        print("    (no crisis windows in this series' span)")


def run() -> None:
    # 1) in-DB recent decade (honest, what we'd trade) ----------------------------------
    d_tx, c_tx = _monthly_closes_topix()
    _report_series("TOPIX (jq_topix, in-DB)", d_tx, c_tx)
    _crisis_table(d_tx, c_tx)

    # 2) long-history robustness via yfinance -------------------------------------------
    yf = _monthly_closes_yf("^N225")
    if yf is not None:
        d_n, c_n = yf
        _report_series("Nikkei 225 (^N225, yfinance long history)", d_n, c_n)
        _crisis_table(d_n, c_n)

    print("\n" + "=" * 92)
    print("HOW TO READ")
    print("=" * 92)
    print("• TSMOM's selling point is DRAWDOWN, not CAGR: a smaller maxDD and positive 'protection' in\n"
          "  the crisis windows (esp. GFC 2008 in the long series) = the rule earns its keep as a risk\n"
          "  overlay. If Sharpe ≈ buy-hold but maxDD is materially smaller + crisis protection positive\n"
          "  → viable defensive overlay. If Sharpe ≤ buy-hold AND maxDD not improved → whipsaw cost\n"
          "  exceeds the timing benefit (chop-dominated regime).\n"
          "• Lookback robustness: the L=12 result must not stand alone — if L=3/6/9 disagree wildly the\n"
          "  edge is a lookback cherry-pick. • The in-DB decade is too short (~10 signals) to conclude;\n"
          "  the ^N225 long history is the binding evidence. Breadth-immune (1 position) so it sidesteps\n"
          "  the wall that killed PEAD/value — judged as a DIVERSIFIER/overlay, not a primary alpha.\n"
          "  DISCOVERY ONLY; a pre-reg with a frozen rule + OOS split follows if this clears.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
