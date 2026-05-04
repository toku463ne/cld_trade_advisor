"""Unit tests for ORM model definitions."""

from src.data.models import (
    GRANULARITIES,
    OHLCV_MODEL_MAP,
    Ohlcv1d,
    Ohlcv1h,
    Ohlcv1m,
    Ohlcv1wk,
    Ohlcv5m,
    Ohlcv15m,
    Ohlcv30m,
    Stock,
)


def test_stock_tablename() -> None:
    assert Stock.__tablename__ == "stocks"


def test_ohlcv_tablenames() -> None:
    assert Ohlcv1m.__tablename__ == "ohlcv_1m"
    assert Ohlcv5m.__tablename__ == "ohlcv_5m"
    assert Ohlcv15m.__tablename__ == "ohlcv_15m"
    assert Ohlcv30m.__tablename__ == "ohlcv_30m"
    assert Ohlcv1h.__tablename__ == "ohlcv_1h"
    assert Ohlcv1d.__tablename__ == "ohlcv_1d"
    assert Ohlcv1wk.__tablename__ == "ohlcv_1wk"


def test_ohlcv_model_map_has_all_granularities() -> None:
    assert set(OHLCV_MODEL_MAP.keys()) == set(GRANULARITIES)


def test_ohlcv_tables_have_partition_by() -> None:
    for gran, model in OHLCV_MODEL_MAP.items():
        args = model.__table_args__
        opts = next(a for a in args if isinstance(a, dict))
        assert "postgresql_partition_by" in opts, f"Missing partition_by for {gran}"
        assert "RANGE" in opts["postgresql_partition_by"]
