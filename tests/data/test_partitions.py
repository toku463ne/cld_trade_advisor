"""Unit tests for partition DDL generation."""

from __future__ import annotations

import datetime

from src.data.partitions import _monthly_partition_ddl, _yearly_partition_ddl


UTC = datetime.timezone.utc


class TestYearlyPartitionDDL:
    def test_basic_year(self) -> None:
        ddl = _yearly_partition_ddl("ohlcv_1d", 2024)
        assert "ohlcv_1d_y2024" in ddl
        assert "2024-01-01" in ddl
        assert "2025-01-01" in ddl

    def test_partition_of_clause(self) -> None:
        ddl = _yearly_partition_ddl("ohlcv_1wk", 2020)
        assert "PARTITION OF ohlcv_1wk" in ddl
        assert "CREATE TABLE IF NOT EXISTS" in ddl


class TestMonthlyPartitionDDL:
    def test_mid_year(self) -> None:
        ddl = _monthly_partition_ddl("ohlcv_1h", 2024, 6)
        assert "ohlcv_1h_y2024m06" in ddl
        assert "2024-06-01" in ddl
        assert "2024-07-01" in ddl

    def test_december_rolls_over(self) -> None:
        ddl = _monthly_partition_ddl("ohlcv_5m", 2024, 12)
        assert "ohlcv_5m_y2024m12" in ddl
        assert "2025-01-01" in ddl

    def test_january(self) -> None:
        ddl = _monthly_partition_ddl("ohlcv_1m", 2025, 1)
        assert "ohlcv_1m_y2025m01" in ddl
        assert "2025-02-01" in ddl
