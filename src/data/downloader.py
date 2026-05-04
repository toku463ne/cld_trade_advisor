"""yfinance wrapper for downloading OHLCV data."""

from __future__ import annotations

import datetime
from typing import Any

import pandas as pd
import yfinance as yf
from loguru import logger

# yfinance caps 1m data at 8 days per request; use 7 for a safety margin.
_CHUNK_DAYS: dict[str, int | None] = {
    "1m": 7,
    "5m": None,
    "15m": None,
    "30m": None,
    "1h": None,
    "1d": None,
    "1wk": None,
}


class YFinanceDownloader:
    """Downloads OHLCV data from yfinance and returns rows ready for DB insert."""

    def fetch(
        self,
        code: str,
        gran: str,
        start: datetime.datetime,
        end: datetime.datetime,
    ) -> list[dict[str, Any]]:
        """Download OHLCV for *code* between *start* and *end*.

        Returns a list of dicts with keys:
            stock_code, ts (UTC-aware), open_price, high_price,
            low_price, close_price, volume
        """
        chunk_days = _CHUNK_DAYS.get(gran)
        if chunk_days is not None:
            return self._fetch_chunked(code, gran, start, end, chunk_days)

        df = self._fetch_one(code, gran, start, end)
        return self._df_to_rows(df, code)

    def _fetch_chunked(
        self,
        code: str,
        gran: str,
        start: datetime.datetime,
        end: datetime.datetime,
        chunk_days: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(
                chunk_start + datetime.timedelta(days=chunk_days),
                end,
            )
            df = self._fetch_one(code, gran, chunk_start, chunk_end)
            rows.extend(self._df_to_rows(df, code))
            chunk_start = chunk_end
        return rows

    def _fetch_one(
        self,
        code: str,
        gran: str,
        start: datetime.datetime,
        end: datetime.datetime,
    ) -> pd.DataFrame:
        logger.debug(f"Downloading {code} {gran} {start.date()} to {end.date()}")
        ticker = yf.Ticker(code)
        df: pd.DataFrame = ticker.history(
            interval=gran,
            start=start,
            end=end,
            auto_adjust=True,
            actions=False,
        )
        if df.empty:
            logger.warning(f"No data returned for {code} {gran} {start.date()} – {end.date()}")
        return df

    def _df_to_rows(self, df: pd.DataFrame, code: str) -> list[dict[str, Any]]:
        if df.empty:
            return []

        # Normalise column names (yfinance may capitalise differently across versions)
        df = df.rename(
            columns={
                "Open": "open_price",
                "High": "high_price",
                "Low": "low_price",
                "Close": "close_price",
                "Volume": "volume",
            }
        )
        required = {"open_price", "high_price", "low_price", "close_price", "volume"}
        missing = required - set(df.columns)
        if missing:
            logger.error(f"Unexpected columns from yfinance for {code}: {df.columns.tolist()}")
            return []

        df = df[list(required)].copy()
        df = df.dropna(subset=["close_price"])

        # Ensure UTC-aware timestamps
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("Asia/Tokyo")
        df.index = df.index.tz_convert(datetime.timezone.utc)

        rows: list[dict[str, Any]] = []
        for ts, row in df.iterrows():
            rows.append(
                {
                    "stock_code": code,
                    "ts": ts.to_pydatetime(),
                    "open_price": float(row["open_price"]),
                    "high_price": float(row["high_price"]),
                    "low_price": float(row["low_price"]),
                    "close_price": float(row["close_price"]),
                    "volume": int(row["volume"]),
                }
            )
        return rows
