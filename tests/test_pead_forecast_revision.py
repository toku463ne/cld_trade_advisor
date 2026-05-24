"""Unit tests for the pure PEAD forecast-revision logic (no DB / no network)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import numpy as np

from src.analysis.pead_forecast_revision import (
    Disclosure, beta, beta_stripped_car, pair_same_fy_revisions, quintile_edges,
    quintile_of, revision_surprise, tradable_entry_day,
)

_FY = datetime.date(2024, 3, 31)
_T = datetime.time


def _d(day, feps, fy=_FY, tm=None, basis="JP"):
    return Disclosure(datetime.date(2024, day // 100, day % 100), tm, fy,
                      None if feps is None else Decimal(str(feps)), basis)


def test_pair_same_fy_takes_most_recent_prior():
    discs = [_d(415, 100), _d(805, 110), _d(1105, 90)]   # 1Q,2Q,3Q same FY
    pairs = pair_same_fy_revisions(discs)
    assert [(float(p.forecast_eps), float(c.forecast_eps)) for p, c in pairs] == [
        (100.0, 110.0), (110.0, 90.0)]


def test_pair_excludes_different_fy_and_missing_and_cross_basis():
    next_fy = datetime.date(2025, 3, 31)
    discs = [
        _d(415, 100),                       # FY24 base
        _d(805, None),                      # missing forecast -> not a valid curr/prev
        _d(1105, 120),                      # FY24, pairs back to 415 (skips the None)
        _d(510, 50, fy=next_fy),            # different FY -> no prior same-FY
        Disclosure(datetime.date(2024, 6, 1), None, _FY, Decimal("130"), "IFRS"),  # basis change
    ]
    pairs = pair_same_fy_revisions(discs)
    pset = {(float(p.forecast_eps), float(c.forecast_eps)) for p, c in pairs}
    assert (100.0, 120.0) in pset          # skipped the null 2Q, paired 3Q->1Q
    assert all(c.forecast_eps != Decimal("50") for _, c in pairs)   # new-FY initial excluded
    # the IFRS row has a JP prior at same FY -> excluded by basis mismatch (break)
    assert (100.0, 130.0) not in pset and (120.0, 130.0) not in pset


def test_revision_surprise_sign_and_scale():
    assert revision_surprise(Decimal("100"), Decimal("110"), 2000.0) == (10.0 / 2000.0)
    assert revision_surprise(Decimal("100"), Decimal("90"), 2000.0) < 0      # guidance cut
    assert revision_surprise(Decimal("100"), None, 2000.0) is None
    assert revision_surprise(Decimal("100"), Decimal("110"), 0) is None      # no price


def test_tradable_entry_day_after_close_shifts_next_trading_day():
    cal = [datetime.date(2024, 5, d) for d in (7, 8, 9, 10)]   # Tue..Fri trading days
    # disclosed 05-08 before close -> entry same day
    assert tradable_entry_day(datetime.date(2024, 5, 8), _T(13, 0), cal) == datetime.date(2024, 5, 8)
    # disclosed 05-08 after close (15:30) -> entry next trading day 05-09
    assert tradable_entry_day(datetime.date(2024, 5, 8), _T(15, 30), cal) == datetime.date(2024, 5, 9)
    # disclosed on a non-trading day (Sat 05-11) -> first trading day on/after (none here)
    assert tradable_entry_day(datetime.date(2024, 5, 11), _T(10, 0), cal) is None


def test_beta_and_car():
    rng = np.random.default_rng(0)
    mkt = rng.normal(0, 0.01, 200)
    stock = 1.5 * mkt + rng.normal(0, 0.001, 200)
    b = beta(stock, mkt)
    assert b is not None and 1.3 < b < 1.7                       # recovers ~1.5

    # market up 10% over the window, stock up exactly beta*market -> abnormal ~0
    mc = np.array([100.0 * (1.10 ** (i / 10)) for i in range(11)])
    sc = np.array([50.0 * (1.10 ** (i / 10)) for i in range(11)])  # same % path as market
    car = beta_stripped_car(sc, mc, entry_idx=1, horizon=9, b=1.0)
    assert car is not None and abs(car) < 1e-9


def test_quintiles():
    vals = list(range(100))
    edges = quintile_edges(vals)
    assert len(edges) == 4
    assert quintile_of(min(vals) - 1, edges) == 0
    assert quintile_of(max(vals) + 1, edges) == 4
