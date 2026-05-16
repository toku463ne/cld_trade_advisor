"""Portfolio statistics — compute realized / unrealized / aggregate P&L per account.

Pure-function compute layer.  No UI, no side effects beyond DB reads.
Used by the Portfolio sub-tab (`src/viz/portfolio_tab.py`) and any
analysis script that wants per-account performance.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.portfolio.crud import get_latest_price
from src.portfolio.models import Position, ReviewedCandidate


def _pnl_pct(direction: str, entry: float, exit_: float) -> float:
    """Return P&L percent for a single trade (direction-aware)."""
    if entry <= 0:
        return 0.0
    raw = (exit_ - entry) / entry
    return raw if direction != "short" else -raw


def _pnl_abs(direction: str, entry: float, exit_: float, units: int) -> float:
    """Return absolute P&L in yen (entry × units × pct)."""
    return entry * units * _pnl_pct(direction, entry, exit_)


@dataclass
class GroupStat:
    """Aggregate stats for a sub-group (e.g. sign_type 'rev_nhi')."""
    n:        int   = 0
    mean_pct: float = 0.0
    sum_abs:  float = 0.0


@dataclass
class AccountStats:
    account_id:   int | None = None
    account_name: str        = ""

    # Realized (closed positions)
    n_realized:   int   = 0
    realized_mean_pct:    float = 0.0   # mean per-trade return %
    realized_sum_abs:     float = 0.0   # sum of yen P&L
    win_rate:             float = 0.0   # fraction of closed trades with positive return
    avg_win_pct:          float = 0.0
    avg_loss_pct:         float = 0.0
    max_win_pct:          float = 0.0
    max_loss_pct:         float = 0.0

    # Unrealized (open positions, marked-to-market)
    n_open:               int   = 0
    open_mean_pct:        float = 0.0
    open_sum_abs:         float = 0.0
    open_missing_price:   int   = 0   # open positions where current price could not be fetched

    # Total
    total_sum_abs:        float = 0.0

    # Review counts
    n_taken:              int   = 0
    n_skipped:            int   = 0

    # Breakdowns
    by_sign:      dict[str, GroupStat] = field(default_factory=dict)
    by_corr_mode: dict[str, GroupStat] = field(default_factory=dict)


def _agg_group(stat: GroupStat, pct: float, abs_yen: float) -> None:
    """Mutate stat to incorporate one trade (running mean trick)."""
    stat.n += 1
    stat.mean_pct = stat.mean_pct + (pct - stat.mean_pct) / stat.n
    stat.sum_abs += abs_yen


def compute_account_stats(
    session: Session,
    account_id: int | None,
) -> AccountStats:
    """Build an AccountStats snapshot from current DB state.

    ``account_id=None`` aggregates across all accounts (legacy / global view).
    """
    out = AccountStats(account_id=account_id)
    if account_id is not None:
        from src.portfolio.models import Account
        acct = session.get(Account, account_id)
        out.account_name = acct.name if acct else f"id={account_id}"
    else:
        out.account_name = "<all accounts>"

    pos_stmt = select(Position)
    rev_stmt = select(ReviewedCandidate)
    if account_id is not None:
        pos_stmt = pos_stmt.where(Position.account_id == account_id)
        rev_stmt = rev_stmt.where(ReviewedCandidate.account_id == account_id)

    positions: list[Position] = list(session.execute(pos_stmt).scalars().all())

    # Realized
    closed_pcts: list[float] = []
    closed_abs:  list[float] = []
    for p in positions:
        if p.status != "closed" or p.exit_price is None or p.entry_price is None:
            continue
        entry, exit_ = float(p.entry_price), float(p.exit_price)
        pct = _pnl_pct(p.direction, entry, exit_)
        abs_ = _pnl_abs(p.direction, entry, exit_, p.units or 0)
        closed_pcts.append(pct)
        closed_abs.append(abs_)
        out.n_realized += 1
        _agg_group(out.by_sign.setdefault(p.sign_type, GroupStat()), pct, abs_)
        _agg_group(out.by_corr_mode.setdefault(p.corr_mode, GroupStat()), pct, abs_)

    if closed_pcts:
        out.realized_mean_pct = statistics.mean(closed_pcts)
        out.realized_sum_abs  = sum(closed_abs)
        wins   = [r for r in closed_pcts if r > 0]
        losses = [r for r in closed_pcts if r <= 0]
        out.win_rate     = len(wins) / len(closed_pcts)
        out.avg_win_pct  = statistics.mean(wins)   if wins   else 0.0
        out.avg_loss_pct = statistics.mean(losses) if losses else 0.0
        out.max_win_pct  = max(closed_pcts)
        out.max_loss_pct = min(closed_pcts)

    # Unrealized (mark-to-market via latest close)
    open_pcts: list[float] = []
    open_abs:  list[float] = []
    for p in positions:
        if p.status != "open" or p.entry_price is None:
            continue
        out.n_open += 1
        cur = get_latest_price(p.stock_code)
        if cur is None:
            out.open_missing_price += 1
            continue
        entry = float(p.entry_price)
        pct = _pnl_pct(p.direction, entry, float(cur))
        abs_ = _pnl_abs(p.direction, entry, float(cur), p.units or 0)
        open_pcts.append(pct)
        open_abs.append(abs_)

    if open_pcts:
        out.open_mean_pct = statistics.mean(open_pcts)
        out.open_sum_abs  = sum(open_abs)

    out.total_sum_abs = out.realized_sum_abs + out.open_sum_abs

    # Review counts
    rev_counts = session.execute(rev_stmt).scalars().all()
    for r in rev_counts:
        if r.action == "taken":
            out.n_taken += 1
        elif r.action == "skipped":
            out.n_skipped += 1

    return out


def summarize_all_accounts(session: Session) -> list[AccountStats]:
    """Compute AccountStats for every non-archived account.  For cross-account view."""
    from src.portfolio.models import Account
    accounts = list(session.execute(
        select(Account).where(Account.archived.is_(False)).order_by(Account.id.asc())
    ).scalars().all())
    return [compute_account_stats(session, a.id) for a in accounts]
