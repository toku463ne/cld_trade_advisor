"""CRUD tests for the Order/Entry/Cancel position workflow (DB, rolled back).

Run WITHOUT --env-file (conftest defaults to stock_trader_test and guards against
dropping non-test DBs).
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select

from src.portfolio.crud import (
    cancel_order, create_account, enter_position, get_active_positions,
    get_open_positions, order_position, register_position, set_account_budget,
)
from src.portfolio.models import Position, ReviewedCandidate

_FIRED = datetime.date(2025, 1, 6)
_ORDER = datetime.date(2025, 1, 7)
_ENTRY = datetime.date(2025, 1, 8)


def _order(session, **over):
    kw = dict(
        stock_code="1301.T", sign_type="conf3:a,b,c", corr_mode="low",
        kumo_state=1, fired_at=_FIRED, order_date=_ORDER, order_price=1500.0,
    )
    kw.update(over)
    return order_position(session, **kw)


def test_order_creates_ordered_with_null_entry(session):
    pos = _order(session)
    assert pos.status == "ordered"
    assert pos.order_date == _ORDER and float(pos.order_price) == 1500.0
    assert pos.entry_date is None and pos.entry_price is None
    # paired review recorded as taken, linked to the position
    rv = session.execute(select(ReviewedCandidate)
                         .where(ReviewedCandidate.position_id == pos.id)).scalars().one()
    assert rv.action == "taken"


def test_enter_transitions_to_open(session):
    pos = _order(session)
    entered = enter_position(session, pos.id, _ENTRY, 1530.0)
    assert entered.status == "open"
    assert entered.entry_date == _ENTRY and float(entered.entry_price) == 1530.0
    # order price preserved for slippage inspection
    assert float(entered.order_price) == 1500.0


def test_enter_requires_ordered(session):
    pos = _order(session)
    enter_position(session, pos.id, _ENTRY, 1530.0)
    with pytest.raises(ValueError, match="not ordered"):
        enter_position(session, pos.id, _ENTRY, 1540.0)


def test_cancel_removes_position_and_audits_review(session):
    pos = _order(session, reason="auction test")
    pid = pos.id
    cancel_order(session, pid)
    assert session.get(Position, pid) is None              # not a trade
    rv = session.execute(select(ReviewedCandidate)
                         .where(ReviewedCandidate.stock_code == "1301.T")).scalars().one()
    assert rv.action == "skipped"
    assert rv.reason.startswith("cancelled")               # audit trail kept
    assert rv.position_id is None


def test_cancel_only_valid_while_ordered(session):
    pos = _order(session)
    enter_position(session, pos.id, _ENTRY, 1530.0)
    with pytest.raises(ValueError, match="not ordered"):
        cancel_order(session, pos.id)


def test_active_positions_counts_ordered_and_open(session):
    acct = create_account(session, name="acct-active-test")
    _order(session, account_id=acct.id)                    # ordered
    register_position(                                     # open (one-step legacy path)
        session, stock_code="6758.T", sign_type="conf3:x,y,z", corr_mode="low",
        kumo_state=1, fired_at=_FIRED, entry_date=_ENTRY, entry_price=2000.0,
        account_id=acct.id,
    )
    active = get_active_positions(session, account_id=acct.id)
    open_only = get_open_positions(session, account_id=acct.id)
    assert len(active) == 2 and len(open_only) == 1


def test_set_account_budget(session):
    acct = create_account(session, name="acct-budget-test")
    assert acct.budget is None
    set_account_budget(session, acct.id, 2_000_000)
    assert float(session.get(type(acct), acct.id).budget) == 2_000_000.0
