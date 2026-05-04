"""Persist simulation results to the database."""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from src.simulator.models import SimOrder, SimPosition, SimRun, SimTrade
from src.simulator.simulator import SimResult, TradeSimulator


def save_run(
    session: Session,
    sim: TradeSimulator,
    stock_code: str,
    gran: str,
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
) -> SimRun:
    """Persist a completed simulation run and return the SimRun record."""
    result = sim.result()

    run = SimRun(
        stock_code=stock_code,
        gran=gran,
        start_dt=start_dt,
        end_dt=end_dt,
        initial_capital=result.initial_capital,
        final_equity=result.final_equity,
        realized_pnl=result.realized_pnl,
        total_trades=result.total_trades,
    )
    session.add(run)
    session.flush()  # populate run.id

    for order in result.orders:
        session.add(
            SimOrder(
                run_id=run.id,
                order_id=order.id,
                side=int(order.side),
                order_type=int(order.type),
                quantity=order.quantity,
                limit_price=order.limit_price,
                stop_price=order.stop_price,
                status=int(order.status),
                filled_price=order.filled_price,
                created_at=order.created_at,
                filled_at=order.filled_at,
                realized_pnl=order.realized_pnl,
            )
        )

    for trade in result.trade_history:
        session.add(
            SimTrade(
                run_id=run.id,
                order_id=trade.order_id,
                side=int(trade.side),
                quantity=trade.quantity,
                price=trade.price,
                dt=trade.dt,
                realized_pnl=trade.realized_pnl,
            )
        )

    pos = result.position
    session.add(
        SimPosition(
            run_id=run.id,
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            entry_dt=pos.entry_dt,
            unrealized_pnl=result.unrealized_pnl,
        )
    )

    return run
