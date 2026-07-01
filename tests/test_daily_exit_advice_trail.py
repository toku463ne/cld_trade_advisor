"""Daily-tab trailing-SL suggestion — the in-profit gate on ``_exit_advice``.

Locks the operator rule: a trailing stop locks in *gains*, so the Daily tab may only
prompt an active "raise SL" (long) / "lower SL" (short) when the position is actually in
profit vs the real fill price the operator typed in. With price at or under entry the
entry-bar high can still push the chandelier candidate above the stored stop, but there is
no gain to protect — the level must be surfaced as a greyed "hold" (``sl_tightens=False``),
never as a call. Regression guard for src/viz/daily.py::_exit_advice.

The DB-touching dependencies (get_session/DataCache/compute_exit_levels) are mocked so the
test isolates the gate logic and runs without a database. The trail *candidate* is fixed by
the compute_exit_levels stub (long 2720->2744, short 3100->3080); only ``sl_tightens``
varies, which is exactly the flag the renderer keys off (daily.py: "raise/lower" vs "hold").
"""
from __future__ import annotations

import contextlib
import datetime

import pytest

_ED = datetime.date(2026, 6, 29)   # entry
_AO = datetime.date(2026, 7, 1)    # as-of


class _Bar:
    __slots__ = ("dt", "high", "low", "close")

    def __init__(self, dt: datetime.datetime, high: float, low: float, close: float):
        self.dt = dt
        self.high = high
        self.low = low
        self.close = close


def _make_bars(start: datetime.date, end: datetime.date) -> list[_Bar]:
    """One synthetic bar per calendar day in [start, end] (mild uptrend)."""
    bars: list[_Bar] = []
    d, i = start, 0
    while d <= end:
        close = 2900.0 + i
        bars.append(_Bar(datetime.datetime(d.year, d.month, d.day, 15, 0),
                         close + 15.0, close - 15.0, close))
        d += datetime.timedelta(days=1)
        i += 1
    return bars


@pytest.fixture
def daily(monkeypatch):
    """src.viz.daily with DB deps stubbed and a fixed trail candidate."""
    from src.viz import daily as _daily

    bars = _make_bars(datetime.date(2026, 6, 1), _AO)   # entry & as-of both present

    class _FakeCache:
        def __init__(self, *a, **k):
            pass

        def load(self, *a, **k):
            pass

        @property
        def bars(self):
            return bars

    @contextlib.contextmanager
    def _fake_session():
        yield None

    def _fake_compute_exit_levels(code, price, asof, direction="long"):
        # (tp, sl); only sl is used. Long candidate tightens 2720->2744;
        # short candidate tightens 3100->3080.
        return (None, 2744.0) if direction != "short" else (None, 3080.0)

    monkeypatch.setattr(_daily, "DataCache", _FakeCache)
    monkeypatch.setattr(_daily, "get_session", _fake_session)
    monkeypatch.setattr(_daily, "compute_exit_levels", _fake_compute_exit_levels)
    return _daily


def _advice(daily, *, cur, entry_price, direction="long", sl=2720.0):
    return daily._exit_advice("TEST.T", _ED, _AO, cur, 3160.0, sl,
                              direction, entry_price=entry_price)


# ── LONG ────────────────────────────────────────────────────────────────────

def test_long_underwater_shows_level_but_no_raise(daily):
    """Below fill: candidate reported, but NOT flagged as a raise (the bug fix)."""
    a = _advice(daily, cur=2924.0, entry_price=2940.0)
    assert a["sl_trail"] == 2744.0          # level still surfaced (greyed "hold")
    assert a["sl_tightens"] is False


def test_long_exactly_at_entry_no_raise(daily):
    """Break-even is not profit: strict '>' means no raise at cur == entry."""
    a = _advice(daily, cur=2940.0, entry_price=2940.0)
    assert a["sl_tightens"] is False


def test_long_in_profit_raises(daily):
    a = _advice(daily, cur=2955.0, entry_price=2940.0)
    assert a["sl_trail"] == 2744.0
    assert a["sl_tightens"] is True


def test_long_unknown_entry_falls_back_to_ungated(daily):
    """Unknown fill → prior behaviour (raise still shown when it tightens safely)."""
    a = _advice(daily, cur=2924.0, entry_price=None)
    assert a["sl_tightens"] is True


def test_long_unknown_price_no_raise(daily):
    """Can't confirm profit without a current price → conservative, no raise."""
    a = _advice(daily, cur=None, entry_price=2940.0)
    assert a["sl_tightens"] is False


# ── SHORT (symmetry) ──────────────────────────────────────────────────────────

def test_short_winning_lowers(daily):
    """Short in profit (price below fill) → active 'lower SL' call."""
    a = _advice(daily, cur=2900.0, entry_price=2940.0, direction="short", sl=3100.0)
    assert a["sl_trail"] == 3080.0
    assert a["sl_tightens"] is True


def test_short_losing_no_lower(daily):
    """Short underwater (price above fill) → no call."""
    a = _advice(daily, cur=2955.0, entry_price=2940.0, direction="short", sl=3100.0)
    assert a["sl_tightens"] is False
