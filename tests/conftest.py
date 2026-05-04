"""Shared pytest fixtures for DB-touching tests."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.data.models import Base


@pytest.fixture(scope="session")
def db_engine() -> Engine:
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://stockdevuser:stockdevpass@localhost:5432/stock_trader_test",
    )
    engine = create_engine(url)
    # Create all parent tables (partitioned tables need raw DDL for the PARTITION BY clause)
    # Use Alembic for real migrations; here we create a minimal schema for tests.
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        conn.commit()
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
