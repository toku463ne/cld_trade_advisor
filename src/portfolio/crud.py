"""Portfolio CRUD — register, query, and close positions."""

from __future__ import annotations

import datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP
from src.exit.zs_tp_sl import ZsTpSl
from src.indicators.zigzag import detect_peaks
from src.portfolio.models import Position
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
) -> tuple[float | None, float | None]:
    """Return (tp_price, sl_price) using ZsTpSl(2.0, 2.0, 0.3).

    Returns (None, None) if zigzag history cannot be built.
    """
    try:
        legs = _build_zs_legs(stock_code, fired_at)
        tp, sl = _EXIT_RULE.preview_levels(entry_price, legs)
        return round(tp, 0), round(sl, 0)
    except Exception as exc:
        logger.warning("compute_exit_levels failed for {}: {}", stock_code, exc)
        return None, None


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
) -> Position:
    """Create and persist a new open position."""
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
    )
    session.add(pos)
    session.flush()
    logger.info("Registered position id={} {} @ {}", pos.id, stock_code, entry_price)
    return pos


def get_open_positions(session: Session) -> list[Position]:
    """Return all open positions ordered by entry_date descending."""
    return list(
        session.execute(
            select(Position)
            .where(Position.status == "open")
            .order_by(Position.entry_date.desc())
        ).scalars().all()
    )


def close_position(
    session: Session,
    position_id: int,
    exit_price: float,
    exit_date: datetime.date | None = None,
) -> Position:
    """Mark an open position as closed."""
    pos = session.get(Position, position_id)
    if pos is None:
        raise ValueError(f"Position {position_id} not found")
    if pos.status != "open":
        raise ValueError(f"Position {position_id} is already {pos.status}")
    pos.status     = "closed"
    pos.exit_date  = exit_date or datetime.date.today()
    pos.exit_price = exit_price
    session.flush()
    logger.info("Closed position id={} {} @ {}", pos.id, pos.stock_code, exit_price)
    return pos


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
