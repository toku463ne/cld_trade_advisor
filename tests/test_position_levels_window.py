"""Regression tests: raising TP/SL must not retroactively fire a hit.

Bug (2026-07-21, live book): ``evaluate_position_as_of`` re-scanned every bar
from ``entry_date`` against the *current* SL, so raising a stop made old bars
that traded below the new level report ``sl_hit``.  7951.T showed
"SL hit (2026-06-30)" — its own entry date — because the raised stop (1,163)
sat above the entry price (1,151), so the entry bar's low tripped it.

Run WITHOUT --env-file (conftest defaults to stock_trader_test).
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import delete

from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP
from src.data.partitions import ensure_partitions
from src.portfolio.crud import evaluate_position_as_of, update_position_levels
from src.portfolio.models import Position

_STOCK = "7951.T"
_ENTRY = datetime.date(2026, 6, 30)
_AS_OF = datetime.date(2026, 7, 3)

# Mirrors the real 7951.T tape that exposed the bug: the entry bar dips to 1118,
# well under the later-raised 1163 stop.
_BARS = [
    (datetime.date(2026, 6, 30), 1156.5, 1118.0, 1142.5),
    (datetime.date(2026, 7, 1),  1147.0, 1132.0, 1137.0),
    (datetime.date(2026, 7, 2),  1175.0, 1140.0, 1155.0),
    (datetime.date(2026, 7, 3),  1177.0, 1157.0, 1163.5),
]


@pytest.fixture
def bars(db_engine, monkeypatch):
    """Insert + COMMIT bars — evaluate_position_as_of opens its own session.

    That inner session resolves DATABASE_URL itself, which conftest does not set
    (it falls back to a literal default); point it at the same test engine.
    """
    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    model = OHLCV_MODEL_MAP["1d"]
    with get_session() as s:
        # ohlcv_1d is PARTITION BY RANGE (ts); create_all does not make partitions.
        ensure_partitions(s, "1d",
                          datetime.datetime(2026, 1, 1), datetime.datetime(2026, 12, 31))
        s.execute(delete(model).where(model.stock_code == _STOCK))
        for d, hi, lo, close in _BARS:
            s.add(model(
                stock_code=_STOCK, ts=datetime.datetime.combine(d, datetime.time()),
                open_price=lo, high_price=hi, low_price=lo, close_price=close,
                volume=1_000,
            ))
        s.commit()
    yield
    with get_session() as s:
        s.execute(delete(model).where(model.stock_code == _STOCK))
        s.commit()


def _evaluate(sl: float, levels_updated_at: datetime.date | None):
    return evaluate_position_as_of(
        stock_code=_STOCK, entry_date=_ENTRY, as_of=_AS_OF,
        tp_price=1321.0, sl_price=sl, direction="long",
        levels_updated_at=levels_updated_at,
    )


# SL 1130 sits above the entry bar's 1118 low but below every later low, so a
# retroactive scan hits on the entry bar while a correct one never fires at all.
_RETRO_SL = 1130.0


def test_raised_sl_does_not_fire_on_pre_update_bars(bars):
    """The bug: a stop raised on 06-30 must not hit on the 06-30 entry bar."""
    _, status, hit = _evaluate(sl=_RETRO_SL, levels_updated_at=_ENTRY)
    assert status == "hold"
    assert hit is None


def test_raised_sl_still_fires_on_later_bars(bars):
    """Guard against over-suppressing: a genuine post-update breach must fire."""
    # Stop raised on 06-30; the 07-01 low of 1132 is a real breach of a 1140 stop.
    _, status, hit = _evaluate(sl=1140.0, levels_updated_at=datetime.date(2026, 6, 30))
    assert status == "sl_hit"
    assert hit == datetime.date(2026, 7, 1)


def test_null_levels_updated_at_preserves_legacy_scan(bars):
    """Never-edited positions (NULL column) keep scanning from entry_date.

    Same SL as the retro test above — only the window differs, which isolates
    the fix to the new parameter.
    """
    _, status, hit = _evaluate(sl=_RETRO_SL, levels_updated_at=None)
    assert status == "sl_hit"
    assert hit == _ENTRY


def test_close_at_as_of_ignores_the_window(bars):
    """Cur price must still resolve even when the whole window is suppressed."""
    close, status, _ = _evaluate(sl=1163.0, levels_updated_at=_AS_OF)
    assert close == pytest.approx(1163.5)   # 07-03 close, not None
    assert status == "hold"


def test_update_position_levels_stamps_today(session):
    pos = Position(
        stock_code=_STOCK, sign_type="conf4:a,b,c,d", corr_mode="low", kumo_state=1,
        direction="long", fired_at=_ENTRY, entry_date=_ENTRY, entry_price=1151.0,
        units=200, tp_price=1321.0, sl_price=1080.0, status="open",
    )
    session.add(pos)
    session.flush()
    assert pos.levels_updated_at is None

    update_position_levels(session, pos.id, sl_price=1163.0)
    assert pos.levels_updated_at == datetime.date.today()
    assert float(pos.sl_price) == 1163.0


def test_tp_only_update_also_resets_window(session):
    """Operator re-places the whole bracket, so either leg resets both."""
    pos = Position(
        stock_code=_STOCK, sign_type="conf4:a,b,c,d", corr_mode="low", kumo_state=1,
        direction="long", fired_at=_ENTRY, entry_date=_ENTRY, entry_price=1151.0,
        units=200, tp_price=1321.0, sl_price=1080.0, status="open",
    )
    session.add(pos)
    session.flush()

    update_position_levels(session, pos.id, tp_price=1400.0)
    assert pos.levels_updated_at == datetime.date.today()
