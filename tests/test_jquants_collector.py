"""Unit tests for the J-Quants v2 collector (no network — HTTP is mocked)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.data import jquants_collector as jc
from src.data.jquants_collector import (
    JQuantsClient, _dec, _date, _time, _qd, parse_listed, parse_quote,
    parse_statement, to_yf_code,
)
from src.data.jquants_models import JqStatement


# ── parsing helpers ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("123.45", Decimal("123.45")), ("0", Decimal("0")), (1000, Decimal("1000")),
    ("", None), ("-", None), ("－", None), (None, None), ("abc", None),
])
def test_dec(raw, expected):
    assert _dec(raw) == expected


def test_date_handles_both_formats():
    assert _date("2024-03-15") == datetime.date(2024, 3, 15)
    assert _date("20240315") == datetime.date(2024, 3, 15)          # v2 YYYYMMDD
    assert _date("2024-03-15T00:00:00") == datetime.date(2024, 3, 15)
    assert _date("") is None and _date(None) is None


def test_qd_request_format():
    assert _qd(datetime.date(2024, 3, 15)) == "20240315"


def test_time():
    assert _time("15:30:00") == datetime.time(15, 30, 0)
    assert _time("09:00") == datetime.time(9, 0)
    assert _time("") is None


@pytest.mark.parametrize("local,yf", [
    ("13010", "1301.T"), ("1301", "1301.T"), ("86970", "8697.T"), ("285A0", "285A.T"),
])
def test_to_yf_code(local, yf):
    assert to_yf_code(local) == yf


# ── row parsers (v2 short field names) ───────────────────────────────────────
def test_parse_statement_v2_announcement_date():
    row = {
        "DiscNo": "20240315000001", "Code": "86970",
        "DiscDate": "2024-03-15", "DiscTime": "15:00:00",
        "DocType": "3QFinancialStatements_Consolidated_JP",
        "CurPerType": "3Q", "CurPerEn": "2023-12-31",
        "OP": "123456789", "NP": "-50000", "EPS": "12.34", "FOP": "",
        "Eq": "5000000000", "BPS": "1234.5", "EqAR": "0.523",
        "ShOutFY": "10000000", "AvgSh": "9876543.2",
    }
    out = parse_statement(row)
    assert out["announcement_date"] == datetime.date(2024, 3, 15)
    assert out["announcement_date"] == out["disclosed_date"]      # step-4 invariant
    assert out["disclosed_time"] == datetime.time(15, 0, 0)
    assert out["disclosure_number"] == "20240315000001"
    assert out["local_code"] == "86970"
    assert out["operating_profit"] == Decimal("123456789")
    assert out["profit"] == Decimal("-50000")                     # NP -> profit
    assert out["earnings_per_share"] == Decimal("12.34")
    assert out["forecast_operating_profit"] is None               # "" -> None
    # balance-sheet fields for PBR / ROE
    assert out["equity"] == Decimal("5000000000")
    assert out["book_value_per_share"] == Decimal("1234.5")
    assert out["equity_to_asset_ratio"] == Decimal("0.523")
    assert out["shares_outstanding_fy"] == 10000000
    assert out["average_shares"] == Decimal("9876543.2")


def test_parse_statement_v1_fallback():
    # _pick tolerates the old long names too, so a v1 payload still parses.
    out = parse_statement({"DisclosureNumber": "D1", "LocalCode": "1301",
                           "DisclosedDate": "2024-03-15", "OperatingProfit": "100"})
    assert out["local_code"] == "1301" and out["operating_profit"] == Decimal("100")


def test_parse_quote_and_listed_v2():
    q = parse_quote({"Code": "13010", "Date": "2024-03-15", "O": "1000",
                     "C": "1010.5", "Vo": "12345", "AdjC": "1010.5", "AdjFactor": "1.0"})
    assert q["code"] == "13010" and q["close"] == Decimal("1010.5") and q["volume"] == 12345
    L = parse_listed({"Code": "13010", "Date": "2024-03-15", "CoName": "極洋",
                      "S33Nm": "水産・農林業", "Mkt": "0111"})
    assert L["company_name"] == "極洋" and L["sector33_name"] == "水産・農林業"
    assert L["market_code"] == "0111"


# ── HTTP client (mocked) ─────────────────────────────────────────────────────
class _Resp:
    def __init__(self, status, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("JQUANTS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JQUANTS_API_KEY is not set"):
        JQuantsClient()


def test_sends_x_api_key_header(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen["headers"] = headers
        return _Resp(200, {"data": [{"Code": "1301"}]})

    monkeypatch.setattr(jc.requests, "get", fake_get)
    client = JQuantsClient(api_key="KEY-123", min_interval=0)
    pages = list(client.get_pages("/equities/master", {}))
    assert seen["headers"] == {"x-api-key": "KEY-123"}
    assert pages == [[{"Code": "1301"}]]


def test_get_pages_follows_pagination(monkeypatch):
    client = JQuantsClient(api_key="K", min_interval=0)
    pages = [
        {"data": [{"a": 1}], "pagination_key": "k2"},
        {"data": [{"a": 2}]},                          # no key -> last page
    ]
    seq = iter(pages)
    monkeypatch.setattr(client, "_request", lambda path, params: next(seq))
    got = [row for page in client.get_pages("/x", {}) for row in page]
    assert got == [{"a": 1}, {"a": 2}]


def test_401_raises_clear_error(monkeypatch):
    client = JQuantsClient(api_key="BAD", min_interval=0)
    monkeypatch.setattr(jc.requests, "get",
                        lambda *a, **k: _Resp(401, text="forbidden"))
    with pytest.raises(RuntimeError, match="JQUANTS_API_KEY"):
        list(client.get_pages("/x", {}))


def test_non_auth_4xx_tolerated(monkeypatch):
    client = JQuantsClient(api_key="K", min_interval=0)
    monkeypatch.setattr(jc.requests, "get",
                        lambda *a, **k: _Resp(403, text="out of subscription"))
    assert list(client.get_pages("/x", {})) == []   # tolerate -> no pages


def test_429_backs_off_then_succeeds(monkeypatch):
    client = JQuantsClient(api_key="K", min_interval=0)
    seq = iter([_Resp(429, text="rate limit", headers={"Retry-After": "0"}),
                _Resp(429, text="rate limit"),
                _Resp(200, {"data": [{"a": 1}]})])
    monkeypatch.setattr(jc.requests, "get", lambda *a, **k: next(seq))
    monkeypatch.setattr(jc.time, "sleep", lambda *_: None)        # don't actually wait
    pages = list(client.get_pages("/x", {}))
    assert pages == [[{"a": 1}]]                                 # retried past the 429s


def test_429_gives_up_returns_none(monkeypatch):
    client = JQuantsClient(api_key="K", min_interval=0)
    monkeypatch.setattr(jc.requests, "get", lambda *a, **k: _Resp(429, text="rate limit"))
    monkeypatch.setattr(jc.time, "sleep", lambda *_: None)
    assert list(client.get_pages("/x", {})) == []                # skip after max retries, no crash


# ── DB upsert idempotency (test DB, rolled back) ─────────────────────────────
def test_upsert_is_idempotent(session):
    base = {
        "disclosure_number": "D1", "local_code": "86970",
        "disclosed_date": datetime.date(2024, 3, 15),
        "announcement_date": datetime.date(2024, 3, 15),
        "operating_profit": Decimal("100"),
    }
    jc._upsert(session, JqStatement, [base])
    jc._upsert(session, JqStatement, [{**base, "operating_profit": Decimal("200")}])
    rows = session.execute(select(JqStatement)).scalars().all()
    assert len(rows) == 1                       # conflict updated, not duplicated
    assert rows[0].operating_profit == Decimal("200")
