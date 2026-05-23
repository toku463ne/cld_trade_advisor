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
    # SAFETY GUARD: this fixture DROPS ALL TABLES.  Running pytest with
    # `--env-file devenv` (or btenv/prodenv) sets DATABASE_URL to a real DB and
    # would wipe it — this happened twice on 2026-05-23.  Refuse to run unless the
    # target DB name clearly marks it as a test DB.  Run pytest with NO env-file
    # (defaults to stock_trader_test) or an explicit test DATABASE_URL.
    db_name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "test" not in db_name.lower():
        raise RuntimeError(
            f"Refusing to drop_all on non-test database {db_name!r}. "
            "Run pytest WITHOUT --env-file (defaults to stock_trader_test), "
            "or set DATABASE_URL to a *test* database. "
            "(tests/conftest.py drops every table — see the 2026-05-23 incident.)"
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
