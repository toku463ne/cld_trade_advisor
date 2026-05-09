"""Shared pytest fixtures for DB-touching tests."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.data.models import Base
import src.analysis.models   # noqa: F401 — register tables with Base.metadata
import src.portfolio.models  # noqa: F401
import src.simulator.models  # noqa: F401
import src.backtest.train_models  # noqa: F401


@pytest.fixture(scope="session")
def db_engine() -> Engine:
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://stockdevuser:stockdevpass@localhost:5432/stock_trader_test",
    )
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(db_engine: Engine) -> Session:
    """Yield a Session that is always rolled back after the test."""
    factory = sessionmaker(bind=db_engine)
    sess = factory()
    sess.begin_nested()  # savepoint
    yield sess
    sess.rollback()
    sess.close()
