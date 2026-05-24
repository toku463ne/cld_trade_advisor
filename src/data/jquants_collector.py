"""J-Quants API **v2** collector — API-key auth, Free-plan compatible, resumable.

All external J-Quants calls live here (per CLAUDE.md: external API code only in
src/data/).  Writes the parallel ``jq_*`` tables defined in
:mod:`src.data.jquants_models`.

v2 vs v1 (accounts registered on/after 2025-12-22 are v2-only):
  * Auth is a dashboard-issued **API key** sent as the ``x-api-key`` header — no
    refresh-token / idToken flow, no expiry.  Set ``JQUANTS_API_KEY`` in the env.
  * Base URL is ``https://api.jquants.com/v2``.
  * Every response wraps its array in a top-level ``"data"`` key + ``pagination_key``.
  * Field names are shortened (Open->O, Close->C, Volume->Vo, OperatingProfit->OP…).
  * Endpoints were renamed:
      /equities/master         (was /listed/info)
      /equities/bars/daily     (was /prices/daily_quotes)
      /fins/summary            (was /fins/statements)
      /indices/bars/daily/topix(was /indices/topix)
      /markets/calendar        (was /markets/trading_calendar)

Free plan: only a ~12-week window (lagged ~2 years) is available.  Per-date fetches
for dates outside the subscription return 4xx / empty; the collector logs and
continues rather than aborting.  Resume: ``jq_fetch_cursor`` stores the last date
fetched per endpoint; a re-run with no ``--from`` resumes at ``last_date + 1``.

CLI:
  uv run --env-file devenv python -m src.data.jquants_collector --endpoint statements \\
      --from 2024-01-01 --to 2024-03-31
  uv run --env-file devenv python -m src.data.jquants_collector --endpoint all --weeks 12
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator

import requests
from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.data.db import get_session
from src.data.jquants_models import (
    JqDailyQuote, JqFetchCursor, JqListed, JqStatement, JqTopix, JqTradingCalendar,
)

_DEFAULT_BASE = "https://api.jquants.com/v2"
_DATA_KEY = "data"
_TIMEOUT = 30
_MAX_RETRY = 3              # for network / 5xx
_MAX_429_RETRY = 6         # rate-limit is transient — be patient
_RETRY_BACKOFF = 2.0
_BACKOFF_CAP = 60.0
_MIN_INTERVAL_DEFAULT = 0.5  # seconds between requests (throttle); tune via env


# ── parsing helpers ──────────────────────────────────────────────────────────
def _dec(v: Any) -> Decimal | None:
    if v is None or v == "" or v == "-" or v == "－":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _int(v: Any) -> int | None:
    d = _dec(v)
    return int(d) if d is not None else None


def _date(v: Any) -> datetime.date | None:
    if not v:
        return None
    s = str(v)[:10].replace("/", "-")
    if "-" not in s and len(s) == 8:        # YYYYMMDD
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return datetime.date.fromisoformat(s)


def _time(v: Any) -> datetime.time | None:
    if not v:
        return None
    parts = str(v).split(":")
    try:
        return datetime.time(int(parts[0]), int(parts[1]),
                             int(parts[2]) if len(parts) > 2 else 0)
    except (ValueError, IndexError):
        return None


def _qd(d: datetime.date) -> str:
    """J-Quants v2 date request param format (YYYYMMDD, per the v2 quickstart)."""
    return d.strftime("%Y%m%d")


def _pick(r: dict, *keys: str) -> Any:
    """First present (non-None) value among candidate keys — tolerates v2/v1 names."""
    for k in keys:
        if r.get(k) is not None:
            return r[k]
    return None


def to_yf_code(local_code: str) -> str:
    """Map a J-Quants 5-digit code (``"13010"``) to existing ``"1301.T"`` form."""
    c = str(local_code)
    if len(c) == 5 and c.endswith("0"):
        c = c[:4]
    return f"{c}.T"


# ── row parsers (module-level for unit testing) — v2 short field names ───────
def parse_listed(r: dict) -> dict:
    return {
        "code": r["Code"], "date": _date(r.get("Date")),
        "company_name": _pick(r, "CoName", "CompanyName"),
        "company_name_en": _pick(r, "CoNameEn", "CompanyNameEnglish"),
        "sector17_code": _pick(r, "S17", "Sector17Code"),
        "sector17_name": _pick(r, "S17Nm", "Sector17CodeName"),
        "sector33_code": _pick(r, "S33", "Sector33Code"),
        "sector33_name": _pick(r, "S33Nm", "Sector33CodeName"),
        "scale_category": _pick(r, "ScaleCat", "ScaleCategory"),
        "market_code": _pick(r, "Mkt", "MarketCode"),
        "market_code_name": _pick(r, "MktNm", "MarketCodeName"),
    }


def parse_quote(r: dict) -> dict:
    return {
        "code": r["Code"], "date": _date(r["Date"]),
        "open": _dec(_pick(r, "O", "Open")), "high": _dec(_pick(r, "H", "High")),
        "low": _dec(_pick(r, "L", "Low")), "close": _dec(_pick(r, "C", "Close")),
        "volume": _int(_pick(r, "Vo", "Volume")),
        "turnover_value": _dec(_pick(r, "Va", "TurnoverValue")),
        "adjustment_factor": _dec(_pick(r, "AdjFactor", "AdjustmentFactor")),
        "adj_open": _dec(_pick(r, "AdjO", "AdjustmentOpen")),
        "adj_high": _dec(_pick(r, "AdjH", "AdjustmentHigh")),
        "adj_low": _dec(_pick(r, "AdjL", "AdjustmentLow")),
        "adj_close": _dec(_pick(r, "AdjC", "AdjustmentClose")),
        "adj_volume": _int(_pick(r, "AdjVo", "AdjustmentVolume")),
    }


def parse_statement(r: dict) -> dict:
    disclosed = _date(_pick(r, "DiscDate", "DisclosedDate"))
    return {
        "disclosure_number": _pick(r, "DiscNo", "DisclosureNumber"),
        "local_code": _pick(r, "Code", "LocalCode"),
        "disclosed_date": disclosed, "disclosed_time": _time(_pick(r, "DiscTime", "DisclosedTime")),
        "announcement_date": disclosed,        # PEAD event anchor == disclosed date
        "type_of_document": _pick(r, "DocType", "TypeOfDocument"),
        "type_of_current_period": _pick(r, "CurPerType", "TypeOfCurrentPeriod"),
        "current_period_start_date": _date(_pick(r, "CurPerSt", "CurrentPeriodStartDate")),
        "current_period_end_date": _date(_pick(r, "CurPerEn", "CurrentPeriodEndDate")),
        "current_fiscal_year_start_date": _date(_pick(r, "CurFYSt", "CurrentFiscalYearStartDate")),
        "current_fiscal_year_end_date": _date(_pick(r, "CurFYEn", "CurrentFiscalYearEndDate")),
        "net_sales": _dec(_pick(r, "Sales", "NetSales")),
        "operating_profit": _dec(_pick(r, "OP", "OperatingProfit")),
        "ordinary_profit": _dec(_pick(r, "OdP", "OrdinaryProfit")),
        "profit": _dec(_pick(r, "NP", "Profit")),
        "earnings_per_share": _dec(_pick(r, "EPS", "EarningsPerShare")),
        "forecast_operating_profit": _dec(_pick(r, "FOP", "ForecastOperatingProfit")),
        "forecast_ordinary_profit": _dec(_pick(r, "FOdP", "ForecastOrdinaryProfit")),
        "forecast_profit": _dec(_pick(r, "FNP", "ForecastProfit")),
        "forecast_earnings_per_share": _dec(_pick(r, "FEPS", "ForecastEarningsPerShare")),
        "total_assets": _dec(_pick(r, "TA", "TotalAssets")),
        "equity": _dec(_pick(r, "Eq", "Equity")),
        "equity_to_asset_ratio": _dec(_pick(r, "EqAR", "EquityToAssetRatio")),
        "book_value_per_share": _dec(_pick(r, "BPS", "BookValuePerShare")),
        "shares_outstanding_fy": _int(_pick(
            r, "ShOutFY",
            "NumberOfIssuedAndOutstandingSharesAtTheEndOfFiscalYearIncludingTreasuryStock")),
        "treasury_shares_fy": _int(_pick(
            r, "TrShFY", "NumberOfTreasuryStockAtTheEndOfFiscalYear")),
        "average_shares": _dec(_pick(r, "AvgSh", "AverageNumberOfShares")),
    }


def parse_topix(r: dict) -> dict:
    return {"date": _date(r["Date"]), "open": _dec(_pick(r, "O", "Open")),
            "high": _dec(_pick(r, "H", "High")), "low": _dec(_pick(r, "L", "Low")),
            "close": _dec(_pick(r, "C", "Close"))}


def parse_calendar(r: dict) -> dict:
    return {"date": _date(r["Date"]),
            "holiday_division": _pick(r, "HolidayDiv", "HolidayDivision", "Div", "HD")}


# ── HTTP client (v2 / x-api-key) ─────────────────────────────────────────────
class JQuantsClient:
    """Authenticated v2 client. API key in ``x-api-key``; handles pagination + retry."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 min_interval: float | None = None) -> None:
        self._key = api_key or os.environ.get("JQUANTS_API_KEY")
        if not self._key:
            raise RuntimeError(
                "JQUANTS_API_KEY is not set. Issue an API key from the J-Quants "
                "dashboard (v2) and export JQUANTS_API_KEY=..."
            )
        self._base = (base_url or os.environ.get("JQUANTS_BASE_URL") or _DEFAULT_BASE).rstrip("/")
        self._min_interval = (min_interval if min_interval is not None
                              else float(os.environ.get("JQUANTS_MIN_INTERVAL_SEC",
                                                         _MIN_INTERVAL_DEFAULT)))
        self._last_req = 0.0   # monotonic timestamp of last request (throttle)

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        wait = self._min_interval - (time.monotonic() - self._last_req)
        if wait > 0:
            time.sleep(wait)

    @staticmethod
    def _retry_after(resp: "requests.Response", attempt: int) -> float:
        """Seconds to wait on a 429: honor Retry-After header, else exponential backoff."""
        hdr = resp.headers.get("Retry-After")
        if hdr:
            try:
                return min(float(hdr), _BACKOFF_CAP)
            except ValueError:
                pass
        return min(_RETRY_BACKOFF * (2 ** attempt), _BACKOFF_CAP)

    def get_pages(self, path: str, params: dict[str, Any]) -> Iterator[list[dict]]:
        """GET ``path`` following ``pagination_key``; yield each page's ``data`` list.

        Raises on auth failure (401) or persistent 5xx; returns no pages on a
        non-auth 4xx (out-of-subscription / not-found) after logging.
        """
        pagination_key: str | None = None
        while True:
            q = dict(params)
            if pagination_key:
                q["pagination_key"] = pagination_key
            body = self._request(path, q)
            if body is None:
                return
            yield body.get(_DATA_KEY, []) or []
            pagination_key = body.get("pagination_key")
            if not pagination_key:
                return

    def _request(self, path: str, params: dict[str, Any]) -> dict | None:
        url = f"{self._base}{path}"
        last_exc: Exception | None = None
        net_attempt = 0          # network / 5xx retries
        rl_attempt = 0           # 429 rate-limit retries (separate, more patient)
        while True:
            self._throttle()
            try:
                resp = requests.get(url, params=params,
                                    headers={"x-api-key": self._key}, timeout=_TIMEOUT)
            except requests.RequestException as e:
                last_exc = e
                net_attempt += 1
                if net_attempt >= _MAX_RETRY:
                    raise RuntimeError(f"GET {path} failed after {net_attempt} attempts: {e}")
                time.sleep(_RETRY_BACKOFF * net_attempt)
                continue
            finally:
                self._last_req = time.monotonic()

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 401:
                raise RuntimeError(
                    f"401 Unauthorized from {path}: {resp.text[:160]}. Check "
                    "JQUANTS_API_KEY (issued from the v2 dashboard)."
                )
            if resp.status_code == 429:        # rate-limited — back off and retry (don't skip)
                if rl_attempt >= _MAX_429_RETRY:
                    logger.warning("{} still 429 after {} retries — skipping", path, rl_attempt)
                    return None
                wait = self._retry_after(resp, rl_attempt)
                logger.info("429 rate-limited on {} — backing off {:.1f}s (retry {}/{})",
                            path, wait, rl_attempt + 1, _MAX_429_RETRY)
                time.sleep(wait)
                rl_attempt += 1
                continue
            if 400 <= resp.status_code < 500:  # subscription / out-of-range / not-found
                logger.warning("{} {} -> {} {}", path, params, resp.status_code, resp.text[:160])
                return None                    # genuinely not ret-riable — tolerate/skip
            # 5xx
            last_exc = RuntimeError(f"{resp.status_code}: {resp.text[:160]}")
            net_attempt += 1
            if net_attempt >= _MAX_RETRY:
                logger.warning("GET {} failed after {} attempts: {}", path, net_attempt, last_exc)
                return None
            time.sleep(_RETRY_BACKOFF * net_attempt)


# ── collector ────────────────────────────────────────────────────────────────
def _upsert(session: Session, model: Any, rows: list[dict]) -> int:
    if not rows:
        return 0
    pk = {c.name for c in model.__table__.primary_key.columns}
    stmt = pg_insert(model).values(rows)
    update_cols = {c.name: getattr(stmt.excluded, c.name)
                   for c in model.__table__.columns if c.name not in pk}
    stmt = stmt.on_conflict_do_update(index_elements=list(pk), set_=update_cols)
    session.execute(stmt)
    return len(rows)


def _daterange(start: datetime.date, end: datetime.date) -> Iterator[datetime.date]:
    d = start
    while d <= end:
        yield d
        d += datetime.timedelta(days=1)


class JQuantsCollector:
    """Fetch J-Quants v2 endpoints into the jq_* tables, idempotently and resumably."""

    def __init__(self, client: JQuantsClient | None = None) -> None:
        self._client = client or JQuantsClient()

    def _cursor(self, session: Session, endpoint: str) -> datetime.date | None:
        row = session.get(JqFetchCursor, endpoint)
        return row.last_date if row else None

    def _advance(self, session: Session, endpoint: str, d: datetime.date) -> None:
        _upsert(session, JqFetchCursor, [{
            "endpoint": endpoint, "last_date": d,
            "updated_at": datetime.datetime.now(datetime.timezone.utc),
        }])

    def _resolve_from(self, session: Session, endpoint: str,
                      explicit: datetime.date | None) -> datetime.date | None:
        if explicit is not None:
            return explicit
        cur = self._cursor(session, endpoint)
        return cur + datetime.timedelta(days=1) if cur else None

    def collect_listed(self, date: datetime.date | None = None) -> int:
        rows: list[dict] = []
        for page in self._client.get_pages(
                "/equities/master", {"date": _qd(date)} if date else {}):
            rows.extend(parse_listed(r) for r in page)
        with get_session() as s:
            n = _upsert(s, JqListed, rows)
        logger.info("jq_listed: upserted {} rows", n)
        return n

    def _collect_per_date(self, endpoint: str, path: str, parse: Any, model: Any,
                          from_: datetime.date | None, to: datetime.date) -> int:
        total = 0
        with get_session() as s:
            start = self._resolve_from(s, endpoint, from_)
        if start is None:
            raise ValueError(f"{endpoint}: no --from and no cursor — provide a start date.")
        for d in _daterange(start, to):
            rows: list[dict] = []
            for page in self._client.get_pages(path, {"date": _qd(d)}):
                rows.extend(parse(r) for r in page)
            with get_session() as s:
                total += _upsert(s, model, rows)
                self._advance(s, endpoint, d)
            if rows:
                logger.info("{} {}: {} rows", endpoint, d, len(rows))
        logger.info("{}: upserted {} rows total ({}..{})", endpoint, total, start, to)
        return total

    def collect_daily_quotes(self, from_: datetime.date | None, to: datetime.date) -> int:
        return self._collect_per_date("daily_quotes", "/equities/bars/daily",
                                      parse_quote, JqDailyQuote, from_, to)

    def collect_statements(self, from_: datetime.date | None, to: datetime.date) -> int:
        return self._collect_per_date("statements", "/fins/summary",
                                      parse_statement, JqStatement, from_, to)

    def _collect_ranged(self, endpoint: str, path: str, parse: Any, model: Any,
                        from_: datetime.date | None, to: datetime.date) -> int:
        with get_session() as s:
            start = self._resolve_from(s, endpoint, from_)
        params: dict[str, Any] = {"to": _qd(to)}
        if start:
            params["from"] = _qd(start)
        rows: list[dict] = []
        max_d: datetime.date | None = None
        for page in self._client.get_pages(path, params):
            for r in page:
                row = parse(r)
                rows.append(row)
                d = row["date"]
                if d and (max_d is None or d > max_d):
                    max_d = d
        with get_session() as s:
            n = _upsert(s, model, rows)
            if max_d:
                self._advance(s, endpoint, max_d)
        logger.info("{}: upserted {} rows", endpoint, n)
        return n

    def collect_topix(self, from_: datetime.date | None, to: datetime.date) -> int:
        return self._collect_ranged("topix", "/indices/bars/daily/topix",
                                    parse_topix, JqTopix, from_, to)

    def collect_trading_calendar(self, from_: datetime.date | None, to: datetime.date) -> int:
        return self._collect_ranged("trading_calendar", "/markets/calendar",
                                    parse_calendar, JqTradingCalendar, from_, to)


def main() -> None:
    p = argparse.ArgumentParser(description="J-Quants v2 collector (API-key auth, Free-plan).")
    p.add_argument("--endpoint", required=True,
                   choices=["listed", "daily_quotes", "statements", "topix",
                            "trading_calendar", "all"])
    p.add_argument("--from", dest="from_", type=datetime.date.fromisoformat, default=None)
    p.add_argument("--to", type=datetime.date.fromisoformat, default=None)
    p.add_argument("--weeks", type=int, default=None,
                   help="If set and --from omitted, fetch the last N weeks ending --to/today.")
    args = p.parse_args()

    to = args.to or datetime.date.today()
    from_ = args.from_
    if from_ is None and args.weeks is not None:
        from_ = to - datetime.timedelta(weeks=args.weeks)

    c = JQuantsCollector()
    if args.endpoint in ("listed", "all"):
        c.collect_listed()
    if args.endpoint in ("trading_calendar", "all"):
        c.collect_trading_calendar(from_, to)
    if args.endpoint in ("topix", "all"):
        c.collect_topix(from_, to)
    if args.endpoint in ("daily_quotes", "all"):
        c.collect_daily_quotes(from_, to)
    if args.endpoint in ("statements", "all"):
        c.collect_statements(from_, to)


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    main()
