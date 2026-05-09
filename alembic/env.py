import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Ensure project root is on sys.path so src.data.models is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.models import Base  # noqa: E402
import src.portfolio.models  # noqa: F401 — registers Position with Base.metadata
import src.analysis.models   # noqa: F401 — registers analysis tables with Base.metadata
import src.simulator.models  # noqa: F401 — registers simulator tables with Base.metadata
import src.backtest.train_models  # noqa: F401 — registers backtest tables with Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    raise RuntimeError("DATABASE_URL environment variable is not set")


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
