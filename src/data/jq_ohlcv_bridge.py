"""Bridge J-Quants adjusted OHLCV into the partitioned `ohlcv_1d` table.

Stage-1 universe expansion (`docs/analysis/universe_expansion_stage1_preregistration.md` §8.3):
the strategy reads `ohlcv_1d` via `DataCache`, but `ohlcv_1d` currently holds only the 225
yfinance Nikkei names. `jq_daily_quotes` holds the full ~4,495-stock universe with split/
dividend-ADJUSTED prices. This module copies the *adjusted* OHLCV of a given code list into
`ohlcv_1d` (and upserts a `stocks` master row from `jq_listed`), so the expanded tier becomes
visible to the existing cluster → sign_benchmark → confluence pipeline with NO change to that
pipeline.

- jq local code → yfinance code via `to_yf_code` (e.g. 13320 → 1332.T) — `ohlcv_1d.stock_code`
  is yfinance format, matching the 225 cohort.
- prices = jq `adj_*` (consistent with the auto-adjusted 225), volume = `adj_volume`.
- ADDITIVE + idempotent: `ensure_partitions` (CREATE IF NOT EXISTS) + `on_conflict_do_nothing`
  on (stock_code, ts). Never drops the 225 / ^N225 / ^GSPC. Safe to re-run.

Run (smoke slice — top 300 NEW liquid codes):
  PYTHONPATH=. uv run --env-file devenv python -m src.data.jq_ohlcv_bridge \
      --tier-file docs/analysis/universe_expansion_tier.txt --limit 300 --new-only
"""
from __future__ import annotations

import argparse
import datetime
import sys

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.data.db import get_session
from src.data.jquants_collector import to_yf_code
from src.data.jquants_models import JqDailyQuote, JqListed
from src.data.models import Ohlcv1d, Stock
from src.data.partitions import ensure_partitions

_UTC = datetime.timezone.utc


def _read_tier(path: str, limit: int | None, new_only: bool) -> list[str]:
    """Return jq local codes from the frozen tier file (col 0), highest qual_days first
    (file is pre-sorted). `new_only` drops codes already in the 225 (col 2 == '1')."""
    out = []
    with open(path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            code, in225 = parts[0], parts[2]
            if new_only and in225 == "1":
                continue
            out.append(code)
            if limit and len(out) >= limit:
                break
    return out


def bridge(jq_codes: list[str]) -> tuple[int, int]:
    """Copy adjusted OHLCV of `jq_codes` into ohlcv_1d (+ upsert stocks). Returns
    (codes_done, rows_inserted)."""
    with get_session() as s:
        listed = {c: (nm, s33, s17, sc, mkt) for c, nm, s33, s17, sc, mkt in s.execute(
            select(JqListed.code, JqListed.company_name, JqListed.sector33_name,
                   JqListed.sector17_name, JqListed.scale_category,
                   JqListed.market_code_name))}
        # global date span (one ensure_partitions covers all codes)
        dmin, dmax = s.execute(select(func.min(JqDailyQuote.date),
                                      func.max(JqDailyQuote.date))).one()
        ensure_partitions(s, "1d",
                          datetime.datetime.combine(dmin, datetime.time.min, tzinfo=_UTC),
                          datetime.datetime.combine(dmax, datetime.time.max, tzinfo=_UTC))

        total_rows = 0
        done = 0
        for jq in jq_codes:
            yf = to_yf_code(jq)
            nm, s33, s17, sc, mkt = listed.get(jq, (jq, None, None, None, None))
            # upsert stocks master row
            s.execute(pg_insert(Stock).values(
                code=yf, name=(nm or yf), market=mkt, sector33=s33, sector17=s17,
                scale=sc, is_active=True
            ).on_conflict_do_nothing(index_elements=["code"]))
            # adjusted OHLCV rows
            rows = []
            for d, ao, ah, al, ac, av in s.execute(
                    select(JqDailyQuote.date, JqDailyQuote.adj_open, JqDailyQuote.adj_high,
                           JqDailyQuote.adj_low, JqDailyQuote.adj_close, JqDailyQuote.adj_volume)
                    .where(JqDailyQuote.code == jq).order_by(JqDailyQuote.date)):
                if ac is None or ao is None or ah is None or al is None:
                    continue
                rows.append({
                    "stock_code": yf,
                    "ts": datetime.datetime.combine(d, datetime.time.min, tzinfo=_UTC),
                    "open_price": ao, "high_price": ah, "low_price": al,
                    "close_price": ac, "volume": int(av) if av is not None else 0,
                })
            if rows:
                stmt = pg_insert(Ohlcv1d).values(rows).on_conflict_do_nothing(
                    index_elements=["stock_code", "ts"])
                res = s.execute(stmt)
                total_rows += res.rowcount or 0
            done += 1
            if done % 50 == 0:
                s.commit()
                logger.info("  bridged {}/{} codes, {} rows so far", done, len(jq_codes), total_rows)
        s.commit()
    logger.info("bridge done: {} codes, {} rows inserted", done, total_rows)
    return done, total_rows


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Bridge jq adjusted OHLCV into ohlcv_1d")
    p.add_argument("--tier-file", required=True)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--new-only", action="store_true", help="skip codes already in the 225")
    args = p.parse_args(argv)
    codes = _read_tier(args.tier_file, args.limit, args.new_only)
    logger.info("bridging {} codes from {}", len(codes), args.tier_file)
    bridge(codes)


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    main()
