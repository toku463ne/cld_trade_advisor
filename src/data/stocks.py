"""JPX stock list management — download, parse, and upsert to the stocks table."""

from __future__ import annotations

import datetime
import io
from typing import Any

import pandas as pd
import requests
from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.data.models import Stock

# JPX publishes a daily snapshot of all listed companies.
# The English version uses the same Excel format.
JPX_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/misc/"
    "tvdivq0000001vg2-att/data_j.xls"
)

_JPX_COLUMN_MAP = {
    "コード": "code_raw",
    "銘柄名": "name",
    "市場・商品区分": "market",
    "33業種区分": "sector33",
    "17業種区分": "sector17",
    "規模区分": "scale",
}

_REQUEST_TIMEOUT = 30


class StockManager:
    """Manages the stocks master table."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def download_and_update(self) -> int:
        """Download the JPX stock list and upsert into the stocks table.

        Returns the number of rows upserted.
        """
        logger.info("Downloading JPX stock list from {}", JPX_URL)
        raw_df = self._download_excel()
        records = self._parse_jpx_df(raw_df)
        if not records:
            logger.warning("Parsed zero records from JPX file — aborting update")
            return 0

        downloaded_codes = {r["code"] for r in records}

        # Mark stocks no longer in the JPX list as inactive
        (
            self._session.query(Stock)
            .filter(Stock.code.not_in(downloaded_codes))
            .update({Stock.is_active: False, Stock.updated_at: _now()}, synchronize_session=False)
        )

        stmt = pg_insert(Stock).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["code"],
            set_={
                "name": stmt.excluded.name,
                "market": stmt.excluded.market,
                "sector33": stmt.excluded.sector33,
                "sector17": stmt.excluded.sector17,
                "scale": stmt.excluded.scale,
                "is_active": True,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        result = self._session.execute(stmt)
        logger.info("Upserted {} stock records", result.rowcount)
        return result.rowcount

    def list_stocks(self, active_only: bool = True) -> list[Stock]:
        """Return stocks from the DB, optionally filtered to active only."""
        q = self._session.query(Stock)
        if active_only:
            q = q.filter(Stock.is_active.is_(True))
        return q.order_by(Stock.code).all()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _download_excel(self) -> pd.DataFrame:
        resp = requests.get(JPX_URL, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        content = io.BytesIO(resp.content)
        try:
            df: pd.DataFrame = pd.read_excel(content, engine="xlrd", dtype=str)
        except Exception:
            # Fallback for newer xlsx format
            content.seek(0)
            df = pd.read_excel(content, engine="openpyxl", dtype=str)
        return df

    def _parse_jpx_df(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        # Keep only columns we care about (ignore any extras)
        available = {c: v for c, v in _JPX_COLUMN_MAP.items() if c in df.columns}
        if "コード" not in available:
            logger.error("Expected column 'コード' not found in JPX file")
            return []

        df = df[list(available.keys())].rename(columns=available)
        df = df.dropna(subset=["code_raw", "name"])

        now = _now()
        records: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            raw_code = str(row["code_raw"]).strip().split(".")[0]
            if not raw_code.isdigit():
                continue
            code = raw_code.zfill(4) + ".T"
            records.append(
                {
                    "code": code,
                    "name": str(row["name"]).strip(),
                    "market": str(row.get("market", "")).strip() or None,
                    "sector33": str(row.get("sector33", "")).strip() or None,
                    "sector17": str(row.get("sector17", "")).strip() or None,
                    "scale": str(row.get("scale", "")).strip() or None,
                    "is_active": True,
                    "updated_at": now,
                }
            )
        return records


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)
