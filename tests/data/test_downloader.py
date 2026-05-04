"""Unit tests for YFinanceDownloader (yfinance patched out)."""

from __future__ import annotations

import datetime

import pandas as pd
import pytest

from src.data.downloader import YFinanceDownloader

UTC = datetime.timezone.utc
JST = datetime.timezone(datetime.timedelta(hours=9))


def _make_df(dates: list[str]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(
        [pd.Timestamp(d, tz="Asia/Tokyo") for d in dates]
    )
    return pd.DataFrame(
        {
            "Open": [100.0] * len(dates),
            "High": [110.0] * len(dates),
            "Low": [90.0] * len(dates),
            "Close": [105.0] * len(dates),
            "Volume": [1000] * len(dates),
        },
        index=idx,
    )


class TestYFinanceDownloader:
    def test_fetch_returns_rows(self, mocker: pytest.FixtureRequest) -> None:
        mock_history = mocker.patch("yfinance.Ticker.history", return_value=_make_df(["2024-01-04"]))
        dl = YFinanceDownloader()
        rows = dl.fetch("7203.T", "1d", datetime.datetime(2024, 1, 1, tzinfo=UTC), datetime.datetime(2024, 1, 5, tzinfo=UTC))

        assert len(rows) == 1
        row = rows[0]
        assert row["stock_code"] == "7203.T"
        assert row["ts"].tzinfo is not None
        assert row["ts"].utcoffset() == datetime.timedelta(0)
        assert row["open_price"] == pytest.approx(100.0)
        assert row["volume"] == 1000

    def test_fetch_empty_returns_empty_list(self, mocker: pytest.FixtureRequest) -> None:
        mocker.patch("yfinance.Ticker.history", return_value=pd.DataFrame())
        dl = YFinanceDownloader()
        rows = dl.fetch("9999.T", "1d", datetime.datetime(2024, 1, 1, tzinfo=UTC), datetime.datetime(2024, 1, 5, tzinfo=UTC))
        assert rows == []

    def test_1m_chunked_into_multiple_requests(self, mocker: pytest.FixtureRequest) -> None:
        mock_history = mocker.patch("yfinance.Ticker.history", return_value=pd.DataFrame())
        dl = YFinanceDownloader()
        # 20-day range should produce ceil(20/7) = 3 chunk requests
        dl.fetch(
            "7203.T",
            "1m",
            datetime.datetime(2024, 1, 1, tzinfo=UTC),
            datetime.datetime(2024, 1, 21, tzinfo=UTC),
        )
        assert mock_history.call_count == 3
