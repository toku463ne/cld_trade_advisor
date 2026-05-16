"""Portfolio CRUD — register, query, and close positions."""

from __future__ import annotations

import datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP
from src.exit.zs_tp_sl import ZsTpSl
from src.indicators.zigzag import detect_peaks
from src.portfolio.models import Account, Memo, Position, ReviewedCandidate
from src.simulator.cache import DataCache

_EXIT_RULE = ZsTpSl(tp_mult=2.0, sl_mult=2.0, alpha=0.3)
_ZZ_SIZE   = 5
_ZZ_MIDDLE = 2
_ZS_LOOKBACK = 16


def _build_zs_legs(
    stock_code: str,
    as_of: datetime.date,
    n225_code: str = "^N225",
    gran: str = "1d",
    lookback_days: int = 400,
) -> tuple[float, ...]:
    """Return recent zigzag leg sizes for *stock_code* up to *as_of*."""
    tz       = datetime.timezone.utc
    end_dt   = datetime.datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=tz)
    start_dt = end_dt - datetime.timedelta(days=lookback_days)

    with get_session() as session:
        cache = DataCache(stock_code, gran)
        cache.load(session, start_dt, end_dt)
        n225 = DataCache(n225_code, gran)
        n225.load(session, start_dt, end_dt)

    if not cache.bars or not n225.bars:
        return ()

    n225_dates = {b.dt.date() for b in n225.bars}
    bars_by_date: dict[datetime.date, list] = {}
    for b in cache.bars:
        bars_by_date.setdefault(b.dt.date(), []).append(b)

    days = sorted((d, g) for d, g in bars_by_date.items() if d in n225_dates)
    if not days:
        return ()

    highs = [max(b.high for b in g) for _, g in days]
    lows  = [min(b.low  for b in g) for _, g in days]

    peaks = detect_peaks(highs, lows, size=_ZZ_SIZE, middle_size=_ZZ_MIDDLE)
    peaks_sorted = sorted(peaks, key=lambda p: p.bar_index)

    leg_sizes: list[float] = []
    prev_price: float | None = None
    for p in peaks_sorted:
        if prev_price is not None:
            leg_sizes.append(abs(p.price - prev_price))
        prev_price = p.price

    return tuple(leg_sizes[-_ZS_LOOKBACK:])


def compute_exit_levels(
    stock_code: str,
    entry_price: float,
    fired_at: datetime.date,
    direction: str = "long",
) -> tuple[float | None, float | None]:
    """Return (tp_price, sl_price) using ZsTpSl(2.0, 2.0, 0.3).

    For ``direction="long"`` (default): TP is above entry, SL is below.
    For ``direction="short"``: TP is below entry, SL is above — the
    profit-direction inversion applied to the symmetric band ZsTpSl
    returns from preview_levels.

    Returns (None, None) if zigzag history cannot be built.
    """
    try:
        legs = _build_zs_legs(stock_code, fired_at)
        tp_long, sl_long = _EXIT_RULE.preview_levels(entry_price, legs)
        if direction == "short":
            # Swap relative to entry: short TP is below entry by the same band,
            # short SL is above entry by the same band.
            band_tp = tp_long - entry_price  # >0 (long TP distance above)
            band_sl = entry_price - sl_long  # >0 (long SL distance below)
            tp = entry_price - band_tp       # short TP below entry
            sl = entry_price + band_sl       # short SL above entry
            return round(tp, 0), round(sl, 0)
        return round(tp_long, 0), round(sl_long, 0)
    except Exception as exc:
        logger.warning("compute_exit_levels failed for {}: {}", stock_code, exc)
        return None, None


def _upsert_review(
    session: Session,
    *,
    account_id:  int | None,
    fired_at:    datetime.date,
    trade_date:  datetime.date,
    stock_code:  str,
    sign_type:   str,
    action:      str,
    sign_score:  float | None,
    corr_mode:   str | None,
    corr_n225:   float | None,
    kumo_state:  int | None,
    position_id: int | None,
    reason:      str | None,
    revn_frac:   float | None,
    sma_frac:    float | None,
    corr_frac:   float | None,
    tags:        str | None,
) -> ReviewedCandidate:
    """Upsert one reviewed_candidates row keyed by the decision tuple.

    Key = (account_id, stock_code, fired_at, trade_date, sign_type) —
    matches the uq_reviewed_candidates_decision unique constraint.

    On hit: updates all mutable fields (including action and
    position_id) and bumps reviewed_at.  On miss: inserts a new row.

    Note on position_id: a Skip after a Register would normally clear
    position_id and leave an orphan Position row.  The Daily UI shows
    an "Already registered" banner to discourage that flow; we keep
    the upsert mechanically simple and trust the operator to honor
    the banner.
    """
    existing = session.execute(
        select(ReviewedCandidate).where(
            ReviewedCandidate.account_id == account_id,
            ReviewedCandidate.stock_code == stock_code,
            ReviewedCandidate.fired_at   == fired_at,
            ReviewedCandidate.trade_date == trade_date,
            ReviewedCandidate.sign_type  == sign_type,
        )
    ).scalars().first()

    if existing is not None:
        existing.sign_score  = sign_score
        existing.corr_mode   = corr_mode
        existing.corr_n225   = corr_n225
        existing.kumo_state  = kumo_state
        existing.action      = action
        existing.position_id = position_id
        existing.reason      = reason
        existing.revn_frac   = revn_frac
        existing.sma_frac    = sma_frac
        existing.corr_frac   = corr_frac
        existing.tags        = tags
        existing.reviewed_at = datetime.datetime.now(datetime.timezone.utc)
        session.flush()
        return existing

    review = ReviewedCandidate(
        account_id  = account_id,
        fired_at    = fired_at,
        trade_date  = trade_date,
        stock_code  = stock_code,
        sign_type   = sign_type,
        sign_score  = sign_score,
        corr_mode   = corr_mode,
        corr_n225   = corr_n225,
        kumo_state  = kumo_state,
        action      = action,
        position_id = position_id,
        reason      = reason,
        revn_frac   = revn_frac,
        sma_frac    = sma_frac,
        corr_frac   = corr_frac,
        tags        = tags,
    )
    session.add(review)
    session.flush()
    return review


def register_position(
    session: Session,
    stock_code:  str,
    sign_type:   str,
    corr_mode:   str,
    kumo_state:  int,
    fired_at:    datetime.date,
    entry_date:  datetime.date,
    entry_price: float,
    direction:   str = "long",
    units:       int = 100,
    tp_price:    float | None = None,
    sl_price:    float | None = None,
    notes:       str | None = None,
    sign_score:  float | None = None,
    corr_n225:   float | None = None,
    revn_frac:   float | None = None,
    sma_frac:    float | None = None,
    corr_frac:   float | None = None,
    reason:      str | None = None,
    account_id:  int | None = None,
    tags:        str | None = None,
    trade_date:  datetime.date | None = None,
) -> Position:
    """Create and persist a new open position.

    Also upserts the paired `ReviewedCandidate(action='taken')` row,
    keyed by (account_id, stock_code, fired_at, trade_date, sign_type).
    If the operator previously skipped this candidate on the same
    trade_date, the existing review row flips to 'taken' rather than
    creating a duplicate.

    ``trade_date`` defaults to ``entry_date`` for backward
    compatibility — call sites built before the column existed treat
    "the day we considered the fire" as the same day we opened the
    position, which matches the implicit pre-column behavior.
    """
    if trade_date is None:
        trade_date = entry_date

    pos = Position(
        stock_code  = stock_code,
        sign_type   = sign_type,
        corr_mode   = corr_mode,
        kumo_state  = kumo_state,
        direction   = direction,
        fired_at    = fired_at,
        entry_date  = entry_date,
        entry_price = entry_price,
        units       = units,
        tp_price    = tp_price,
        sl_price    = sl_price,
        notes       = notes,
        status      = "open",
        sign_score  = sign_score,
        revn_frac   = revn_frac,
        sma_frac    = sma_frac,
        corr_frac   = corr_frac,
        account_id  = account_id,
    )
    session.add(pos)
    session.flush()

    review = _upsert_review(
        session,
        account_id  = account_id,
        fired_at    = fired_at,
        trade_date  = trade_date,
        stock_code  = stock_code,
        sign_type   = sign_type,
        action      = "taken",
        sign_score  = sign_score,
        corr_mode   = corr_mode,
        corr_n225   = corr_n225,
        kumo_state  = kumo_state,
        position_id = pos.id,
        reason      = reason,
        revn_frac   = revn_frac,
        sma_frac    = sma_frac,
        corr_frac   = corr_frac,
        tags        = tags,
    )

    logger.info("Registered position id={} {} @ {} (review id={})",
                pos.id, stock_code, entry_price, review.id)
    return pos


def register_review(
    session: Session,
    fired_at:    datetime.date,
    stock_code:  str,
    sign_type:   str,
    action:      str,
    trade_date:  datetime.date | None = None,
    sign_score:  float | None = None,
    corr_mode:   str | None = None,
    corr_n225:   float | None = None,
    kumo_state:  int | None = None,
    position_id: int | None = None,
    reason:      str | None = None,
    revn_frac:   float | None = None,
    sma_frac:    float | None = None,
    corr_frac:   float | None = None,
    account_id:  int | None = None,
    tags:        str | None = None,
) -> ReviewedCandidate:
    """Persist a reviewed-candidate row (upsert).

    Used directly for 'skipped' clicks; `register_position` calls the
    same upsert internally for 'taken' actions.  Key =
    (account_id, stock_code, fired_at, trade_date, sign_type).

    ``trade_date`` defaults to ``fired_at`` for backward
    compatibility — call sites built before the column existed treat
    "the day we considered the fire" as the fire day itself.
    """
    if action not in ("taken", "skipped"):
        raise ValueError(f"action must be 'taken' or 'skipped', got {action!r}")
    if trade_date is None:
        trade_date = fired_at

    review = _upsert_review(
        session,
        account_id  = account_id,
        fired_at    = fired_at,
        trade_date  = trade_date,
        stock_code  = stock_code,
        sign_type   = sign_type,
        action      = action,
        sign_score  = sign_score,
        corr_mode   = corr_mode,
        corr_n225   = corr_n225,
        kumo_state  = kumo_state,
        position_id = position_id,
        reason      = reason,
        revn_frac   = revn_frac,
        sma_frac    = sma_frac,
        corr_frac   = corr_frac,
        tags        = tags,
    )
    logger.info("Upserted review id={} {} action={}", review.id, stock_code, action)
    return review


def get_distinct_tags(session: Session) -> list[str]:
    """Return all distinct tags ever used, sorted by frequency (desc) then alphabetically.

    Tags are stored comma-separated; this helper splits and aggregates.
    """
    from collections import Counter
    rows = session.execute(
        select(ReviewedCandidate.tags).where(ReviewedCandidate.tags.is_not(None))
    ).scalars().all()
    counter: Counter[str] = Counter()
    for raw in rows:
        for tag in (raw or "").split(","):
            t = tag.strip()
            if t:
                counter[t] += 1
    return [t for t, _ in counter.most_common()]


def get_reviews_for_date(
    session: Session,
    fired_at: datetime.date,
) -> list[ReviewedCandidate]:
    """All reviewed candidates with `fired_at == fired_at`, newest first."""
    return list(
        session.execute(
            select(ReviewedCandidate)
            .where(ReviewedCandidate.fired_at == fired_at)
            .order_by(ReviewedCandidate.reviewed_at.desc())
        ).scalars().all()
    )


def get_open_positions(
    session: Session,
    account_id: int | None = None,
) -> list[Position]:
    """Return open positions ordered by entry_date descending.

    If ``account_id`` is provided, only positions in that account are
    returned.  ``None`` returns all open positions across accounts (legacy
    behaviour).
    """
    stmt = select(Position).where(Position.status == "open")
    if account_id is not None:
        stmt = stmt.where(Position.account_id == account_id)
    stmt = stmt.order_by(Position.entry_date.desc())
    return list(session.execute(stmt).scalars().all())


# ── Account CRUD ──────────────────────────────────────────────────────────────

def create_account(
    session: Session,
    name: str,
    description: str | None = None,
    initial_cash: float | None = None,
) -> Account:
    """Create a new virtual account.  Name must be unique."""
    name = (name or "").strip()
    if not name:
        raise ValueError("account name cannot be empty")
    acct = Account(
        name=name,
        description=(description or None),
        initial_cash=initial_cash,
    )
    session.add(acct)
    session.flush()
    logger.info("Created account id={} name={!r}", acct.id, name)
    return acct


def list_accounts(
    session: Session,
    include_archived: bool = False,
) -> list[Account]:
    """Return all accounts ordered by id (creation order)."""
    stmt = select(Account)
    if not include_archived:
        stmt = stmt.where(Account.archived.is_(False))
    stmt = stmt.order_by(Account.id.asc())
    return list(session.execute(stmt).scalars().all())


def get_account(session: Session, account_id: int) -> Account | None:
    return session.get(Account, account_id)


def archive_account(session: Session, account_id: int) -> None:
    """Soft-archive (sets archived=True; positions/reviews remain queryable)."""
    acct = session.get(Account, account_id)
    if acct is None:
        return
    acct.archived = True
    session.flush()
    logger.info("Archived account id={}", account_id)


def get_default_account_id(session: Session) -> int | None:
    """Return the id of the auto-created 'default' account, or None if missing."""
    return session.execute(
        select(Account.id).where(Account.name == "default")
    ).scalar_one_or_none()


def close_position(
    session: Session,
    position_id: int,
    exit_price: float,
    exit_date: datetime.date | None = None,
    exit_reason: str | None = None,
) -> Position:
    """Mark an open position as closed.

    `exit_reason` is one of: ``tp_hit``, ``sl_hit``, ``time_stop``,
    ``manual``, or None.  Stored verbatim; analysis code is responsible
    for grouping.
    """
    pos = session.get(Position, position_id)
    if pos is None:
        raise ValueError(f"Position {position_id} not found")
    if pos.status != "open":
        raise ValueError(f"Position {position_id} is already {pos.status}")
    pos.status      = "closed"
    pos.exit_date   = exit_date or datetime.date.today()
    pos.exit_price  = exit_price
    pos.exit_reason = exit_reason
    session.flush()
    logger.info("Closed position id={} {} @ {} reason={}",
                pos.id, pos.stock_code, exit_price, exit_reason)
    return pos


def create_memo(
    session: Session,
    memo_date: datetime.date,
    content: str,
) -> Memo:
    """Persist a new memo for *memo_date*."""
    if not content or not content.strip():
        raise ValueError("memo content cannot be empty")
    memo = Memo(memo_date=memo_date, content=content.strip())
    session.add(memo)
    session.flush()
    logger.info("Created memo id={} date={} ({} chars)",
                memo.id, memo_date, len(memo.content))
    return memo


def update_memo(
    session: Session,
    memo_id: int,
    content: str,
) -> Memo:
    """Replace an existing memo's content; bumps updated_at."""
    memo = session.get(Memo, memo_id)
    if memo is None:
        raise ValueError(f"Memo {memo_id} not found")
    if not content or not content.strip():
        raise ValueError("memo content cannot be empty")
    memo.content    = content.strip()
    memo.updated_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()
    logger.info("Updated memo id={}", memo_id)
    return memo


def delete_memo(session: Session, memo_id: int) -> None:
    """Hard-delete a memo (no soft-delete semantics)."""
    memo = session.get(Memo, memo_id)
    if memo is None:
        return
    session.delete(memo)
    session.flush()
    logger.info("Deleted memo id={}", memo_id)


def get_memos_for_date(
    session: Session,
    memo_date: datetime.date,
) -> list[Memo]:
    """Memos for *memo_date*, newest-first by created_at."""
    return list(
        session.execute(
            select(Memo)
            .where(Memo.memo_date == memo_date)
            .order_by(Memo.created_at.desc())
        ).scalars().all()
    )


def list_memos(
    session: Session,
    limit: int | None = None,
) -> list[Memo]:
    """All memos, newest-first by memo_date then created_at."""
    stmt = (
        select(Memo)
        .order_by(Memo.memo_date.desc(), Memo.created_at.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars().all())


def get_entry_price_for_fire(
    stock_code: str,
    fired_at:   datetime.date,
    gran: str = "1d",
) -> float | None:
    """Default entry price for a proposal fired on *fired_at*.

    Follows the two-bar fill rule: prefer the **open** of the next
    trading day after fired_at.  Falls back to the close on fired_at
    if no subsequent bar exists yet (live-trading case where the
    proposal fired today and tomorrow hasn't traded yet), then to the
    overall latest close as the last resort.
    """
    model = OHLCV_MODEL_MAP.get(gran)
    if model is None:
        return None
    try:
        with get_session() as session:
            next_bar = session.execute(
                select(model.open_price)
                .where(model.stock_code == stock_code)
                .where(func.date(model.ts) > fired_at)
                .order_by(model.ts.asc())
                .limit(1)
            ).scalar_one_or_none()
            if next_bar is not None:
                return float(next_bar)
            same_day = session.execute(
                select(model.close_price)
                .where(model.stock_code == stock_code)
                .where(func.date(model.ts) == fired_at)
                .limit(1)
            ).scalar_one_or_none()
            if same_day is not None:
                return float(same_day)
            latest = session.execute(
                select(model.close_price)
                .where(model.stock_code == stock_code)
                .order_by(model.ts.desc())
                .limit(1)
            ).scalar_one_or_none()
            return float(latest) if latest is not None else None
    except Exception as exc:
        logger.warning("get_entry_price_for_fire failed for {} {}: {}",
                       stock_code, fired_at, exc)
        return None


def get_latest_price(stock_code: str, gran: str = "1d") -> float | None:
    """Return the latest available close price for *stock_code*."""
    model = OHLCV_MODEL_MAP.get(gran)
    if model is None:
        return None
    try:
        with get_session() as session:
            row = session.execute(
                select(model.close_price)
                .where(model.stock_code == stock_code)
                .order_by(model.ts.desc())
                .limit(1)
            ).scalar_one_or_none()
        return float(row) if row is not None else None
    except Exception as exc:
        logger.warning("get_latest_price failed for {}: {}", stock_code, exc)
        return None
