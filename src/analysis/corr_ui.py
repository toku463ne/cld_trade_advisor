"""Correlation analysis viewer — multi-page Dash app.

Routes:
  /              → correlation pair table
  /pair?a=X&b=Y  → side-by-side price charts for stock pair X and Y
  /peak-corr     → zigzag peak correlation table (A / B metrics)
  /moving-corr   → per-bar rolling correlation chart for a selected stock

Launch:
    uv run --env-file devenv python -m src.analysis.corr_ui
    # open http://localhost:8051
"""

from __future__ import annotations

import datetime
import sys
from typing import Any
from urllib.parse import urlencode, parse_qs

import numpy as np
import pandas as pd
import yfinance as yf
import dash
from dash import Input, Output, callback, dash_table, dcc, html
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.analysis.models import CorrRun, StockCorrPair, PeakCorrRun, PeakCorrResult, StockClusterMember, StockClusterRun
from src.analysis.peak_corr import MAJOR_INDICATORS
from src.config import load_stock_codes
from src.indicators import (
    calc_atr, calc_bb, calc_ema, calc_ichimoku, calc_macd, calc_rsi, calc_sma,
    compute_moving_corr, detect_peaks,
)
from src.data.db import get_session
from src.data.models import Stock
from src.simulator.cache import DataCache
from src.viz.charts import (
    BG, SIDEBAR_BG, CARD_BG, BORDER, TEXT, MUTED, ACCENT,
    build_moving_corr_figure, build_pair_figure, empty_figure,
)

# ── App ───────────────────────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    title="Correlation Analysis",
    suppress_callback_exceptions=True,
)

# ── Style helpers ─────────────────────────────────────────────────────────────

_S_LABEL: dict[str, Any] = {
    "color": MUTED, "fontSize": "11px",
    "textTransform": "uppercase", "letterSpacing": "0.5px",
    "marginTop": "14px", "marginBottom": "4px", "display": "block",
}
_S_CARD: dict[str, Any] = {
    "background": CARD_BG, "border": f"1px solid {BORDER}",
    "borderRadius": "6px", "padding": "12px", "marginTop": "12px",
    "color": TEXT, "fontSize": "13px",
}
_S_PAGE: dict[str, Any] = {
    "fontFamily": "'Segoe UI', Arial, sans-serif",
    "background": BG, "minHeight": "100vh", "padding": "24px",
}
_S_IND_BLOCK: dict[str, Any] = {
    "padding": "8px 4px",
    "borderBottom": f"1px solid {BORDER}",
}
_S_IND_PARAM_WRAP: dict[str, Any] = {
    "paddingLeft": "16px",
    "display": "flex", "flexDirection": "column", "gap": "4px",
    "marginTop": "4px",
}
_S_PARAM_ROW: dict[str, Any] = {
    "display": "flex", "justifyContent": "space-between", "alignItems": "center",
}
_S_PARAM_LABEL: dict[str, Any] = {
    "color": MUTED, "fontSize": "10px",
}
_S_PARAM_INPUT: dict[str, Any] = {
    "background": CARD_BG, "color": TEXT,
    "border": f"1px solid {BORDER}", "borderRadius": "3px",
    "padding": "3px 5px", "width": "56px", "fontSize": "11px",
    "textAlign": "right",
}

_MAJOR_SET: set[str] = set(MAJOR_INDICATORS)

_INPUT_STYLE: dict = {
    "background": CARD_BG, "color": TEXT,
    "border": f"1px solid {BORDER}", "borderRadius": "4px",
    "padding": "8px", "width": "100%", "fontSize": "13px",
    "boxSizing": "border-box",
}

_STOCK_CODES_INI = "configs/stock_codes.ini"


# ── Overlay builders ──────────────────────────────────────────────────────────


def _sma_overlays(closes: list[float], period: int) -> list[dict]:
    return [{"label": f"SMA{period}", "y": calc_sma(closes, period),
             "color": "#F0C040", "dash": "solid", "width": 1.2}]


def _ema_overlays(closes: list[float], period: int) -> list[dict]:
    return [{"label": f"EMA{period}", "y": calc_ema(closes, period),
             "color": "#29B6F6", "dash": "solid", "width": 1.2}]


def _bb_overlays(closes: list[float], period: int, nstd: float) -> list[dict]:
    lower, mid, upper = calc_bb(closes, period, nstd)
    cb = "rgba(100,100,200,0.35)"
    return [
        {"label": f"BB{period} lo",  "y": lower, "color": cb, "dash": "dot", "width": 1.0},
        {"label": f"BB{period} hi",  "y": upper, "color": cb, "dash": "dot", "width": 1.0,
         "fill": "tonexty", "fillcolor": "rgba(100,100,200,0.07)"},
        {"label": f"BB{period} mid", "y": mid,   "color": "rgba(100,100,200,0.7)", "dash": "dash", "width": 1.0},
    ]


def _ichi_overlays(
    highs: list[float], lows: list[float], closes: list[float],
    tenkan: int, kijun: int, senkou_b: int, displacement: int,
) -> list[dict]:
    """Build overlay dicts for all five Ichimoku lines.

    Senkou A/B are shifted forward by *displacement* within the existing
    date range so they align with the chart x-axis.  Chikou is shifted
    backward.  Values that fall outside the range are dropped (None).
    """
    import math
    raw = calc_ichimoku(highs, lows, closes, tenkan, kijun, senkou_b, displacement)
    n   = len(closes)
    d   = displacement

    def _shift_forward(src: list[float]) -> list[float | None]:
        out: list[float | None] = [None] * n
        for i in range(n):
            j = i + d
            if j < n:
                v = src[i]
                out[j] = None if math.isnan(v) else v
        return out

    def _shift_back(src: list[float]) -> list[float | None]:
        out: list[float | None] = [None] * n
        for i in range(d, n):
            v = src[i]
            out[i - d] = None if math.isnan(v) else v
        return out

    sa = _shift_forward(raw["senkou_a"])  # type: ignore[arg-type]
    sb = _shift_forward(raw["senkou_b"])  # type: ignore[arg-type]
    ch = _shift_back(raw["chikou"])       # type: ignore[arg-type]

    return [
        # Tenkan-sen (red)
        {"label": f"Tenkan({tenkan})", "y": raw["tenkan"],
         "color": "#EF5350", "dash": "solid", "width": 1.0},
        # Kijun-sen (blue)
        {"label": f"Kijun({kijun})",  "y": raw["kijun"],
         "color": "#1E88E5", "dash": "solid", "width": 1.5},
        # Senkou A (bottom of fill pair — must come first)
        {"label": f"Senkou A", "y": sa,
         "color": "rgba(0,200,83,0.5)", "dash": "solid", "width": 0.8},
        # Senkou B (top / bottom — fill tonexty fills back to Senkou A)
        {"label": f"Senkou B", "y": sb,
         "color": "rgba(229,57,53,0.5)", "dash": "solid", "width": 0.8,
         "fill": "tonexty", "fillcolor": "rgba(120,120,120,0.12)"},
        # Chikou Span (lagging, gray dashed)
        {"label": "Chikou",   "y": ch,
         "color": "rgba(180,180,180,0.6)", "dash": "dot", "width": 1.0},
    ]


# ── Sidebar widget ────────────────────────────────────────────────────────────


def _ind_item(
    check_id: str,
    label: str,
    params: list[tuple[str, str, int | float, int | float, int | float]],
) -> html.Div:
    """Indicator row: checkbox + parameter inputs.

    params: list of (input_id, display_label, default, min, max).
    """
    param_rows = [
        html.Div([
            html.Span(plabel, style=_S_PARAM_LABEL),
            dcc.Input(
                id=pid, type="number",
                value=default, min=pmin, max=pmax,
                step=1 if isinstance(default, int) else 0.5,
                debounce=True,
                style=_S_PARAM_INPUT,
            ),
        ], style=_S_PARAM_ROW)
        for pid, plabel, default, pmin, pmax in params
    ]
    return html.Div([
        dcc.Checklist(
            id=check_id,
            options=[{"label": f"  {label}", "value": "on"}],
            value=[],
            style={"fontSize": "12px", "cursor": "pointer"},
            labelStyle={"color": "#e6edf3"},
            inputStyle={"marginRight": "4px"},
        ),
        html.Div(param_rows, style=_S_IND_PARAM_WRAP),
    ], style=_S_IND_BLOCK)


# ── Stock name cache ──────────────────────────────────────────────────────────

_name_cache: dict[str, str] = {}


def _lookup_names(codes: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    missing: set[str] = set()

    for code in codes:
        if code in _name_cache:
            result[code] = _name_cache[code]
        else:
            missing.add(code)

    if missing:
        with get_session() as session:
            rows = session.execute(
                select(Stock.code, Stock.name).where(Stock.code.in_(missing))
            ).all()
        for r in rows:
            result[r.code] = r.name
            _name_cache[r.code] = r.name
            missing.discard(r.code)

    if missing:
        now = datetime.datetime.now(datetime.timezone.utc)
        fetched: list[dict] = []
        for code in missing:
            try:
                info = yf.Ticker(code).info
                name = info.get("shortName") or info.get("longName") or ""
            except Exception:
                name = ""
            result[code] = name
            _name_cache[code] = name
            if name:
                fetched.append({"code": code, "name": name, "updated_at": now})

        if fetched:
            with get_session() as session:
                stmt = pg_insert(Stock).values(fetched)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["code"],
                    set_={"name": stmt.excluded.name, "updated_at": stmt.excluded.updated_at},
                )
                session.execute(stmt)

    return result


# ── Page layouts ──────────────────────────────────────────────────────────────


def _nav(active: str) -> html.Div:
    links = [
        ("Correlation Table",  "/"),
        ("Peak Correlation",   "/peak-corr"),
        ("Moving Correlation", "/moving-corr"),
    ]
    items = []
    for label, href in links:
        is_active = href == active
        items.append(html.A(
            label, href=href,
            style={
                "color": ACCENT if is_active else MUTED,
                "fontSize": "13px", "textDecoration": "none",
                "fontWeight": "600" if is_active else "400",
                "paddingBottom": "4px",
                "borderBottom": f"2px solid {ACCENT}" if is_active else "2px solid transparent",
            },
        ))
    return html.Div(items, style={"display": "flex", "gap": "24px", "marginBottom": "20px"})


def _main_page() -> html.Div:
    return html.Div(style=_S_PAGE, children=[
        dcc.Store(id="_init", data=True),

        _nav("/"),
        html.H3("Stock Return Correlation Analysis",
                style={"color": ACCENT, "margin": "0 0 16px 0"}),

        html.Div(style={"display": "flex", "gap": "24px", "alignItems": "flex-end"}, children=[
            html.Div(style={"flex": "0 0 420px"}, children=[
                html.Span("Correlation Run", style=_S_LABEL),
                dcc.Dropdown(id="run-dd", options=[], value=None, clearable=False),
            ]),
            html.Div(style={"flex": "0 0 200px"}, children=[
                html.Span("Filter by stock (optional)", style=_S_LABEL),
                dcc.Input(
                    id="stock-filter", type="text", placeholder="e.g. 7203.T",
                    debounce=False,
                    style={
                        "background": CARD_BG, "color": TEXT,
                        "border": f"1px solid {BORDER}", "borderRadius": "4px",
                        "padding": "8px", "width": "100%", "fontSize": "13px",
                    },
                ),
            ]),
        ]),

        html.Div(id="run-meta", style={**_S_CARD, "marginBottom": "16px"}),

        html.Span("All Pairs", style=_S_LABEL),
        dash_table.DataTable(
            id="pair-tbl",
            columns=[
                {"name": "",          "id": "chart_link",  "presentation": "markdown"},
                {"name": "#",         "id": "rank"},
                {"name": "Stock A",   "id": "stock_a"},
                {"name": "Name A",    "id": "name_a"},
                {"name": "Stock B",   "id": "stock_b"},
                {"name": "Name B",    "id": "name_b"},
                {"name": "Mean ρ",    "id": "mean_corr"},
                {"name": "Std ρ",     "id": "std_corr"},
                {"name": "|μ| round", "id": "abs_rounded"},
                {"name": "Windows",   "id": "n_windows"},
            ],
            data=[],
            sort_action="native",
            filter_action="native",
            page_size=30,
            style_table={"overflowX": "auto"},
            style_cell={
                "backgroundColor": CARD_BG, "color": TEXT,
                "fontSize": "12px", "padding": "5px 8px",
                "border": f"1px solid {BORDER}",
                "textAlign": "left",
                "maxWidth": "220px",
                "overflow": "hidden",
                "textOverflow": "ellipsis",
            },
            style_cell_conditional=[
                {"if": {"column_id": c}, "textAlign": "center"}
                for c in ("chart_link", "rank", "mean_corr", "std_corr", "abs_rounded", "n_windows")
            ],
            style_header={
                "backgroundColor": SIDEBAR_BG, "color": MUTED,
                "fontWeight": "600", "border": f"1px solid {BORDER}",
                "fontSize": "11px", "textAlign": "center",
            },
            style_data_conditional=[
                {"if": {"filter_query": "{mean_corr} > 0.6"},
                 "color": "#3fb950", "fontWeight": "600"},
                {"if": {"filter_query": "{mean_corr} < -0.6"},
                 "color": "#f85149", "fontWeight": "600"},
            ],
            markdown_options={"link_target": "_self"},
        ),
    ])


def _pair_page(stock_a: str, stock_b: str) -> html.Div:
    return html.Div(style=_S_PAGE, children=[
        dcc.Store(id="pair-data", data={"stock_a": stock_a, "stock_b": stock_b}),

        html.A(
            "← Back to Correlation Table", href="/",
            style={"color": MUTED, "fontSize": "12px",
                   "textDecoration": "none", "display": "inline-block", "marginBottom": "16px"},
        ),

        html.Div(id="pair-header",
                 style={"color": ACCENT, "fontSize": "18px", "fontWeight": "600",
                        "marginBottom": "4px"}),
        html.Div(id="pair-subheader",
                 style={"color": MUTED, "fontSize": "13px", "marginBottom": "16px"}),

        dcc.Graph(id="pair-chart", style={"height": "88vh"},
                  config={"scrollZoom": True, "displayModeBar": True,
                          "modeBarButtonsToRemove": ["lasso2d", "select2d"]}),
    ])


def _peak_corr_page() -> html.Div:
    return html.Div(style=_S_PAGE, children=[
        dcc.Store(id="_pc_init", data=True),

        _nav("/peak-corr"),
        html.H3("Zigzag Peak Correlation Analysis",
                style={"color": ACCENT, "margin": "0 0 16px 0"}),

        html.Div(style={"display": "flex", "gap": "24px", "alignItems": "flex-end"}, children=[
            html.Div(style={"flex": "0 0 500px"}, children=[
                html.Span("Peak Corr Run", style=_S_LABEL),
                dcc.Dropdown(id="pc-run-dd", options=[], value=None, clearable=False),
            ]),
            html.Div(style={"flex": "0 0 200px"}, children=[
                html.Span("Indicator (optional)", style=_S_LABEL),
                dcc.Dropdown(id="pc-ind-dd", options=[], value=None, clearable=True,
                             placeholder="All indicators"),
            ]),
        ]),

        html.Div(id="pc-run-meta", style={**_S_CARD, "marginBottom": "16px"}),

        html.Span("Peak Correlation Results", style=_S_LABEL),
        dash_table.DataTable(
            id="pc-tbl",
            columns=[
                {"name": "",             "id": "chart_link",  "presentation": "markdown"},
                {"name": "Stock",        "id": "stock"},
                {"name": "Name",         "id": "stock_name"},
                {"name": "Indicator",    "id": "indicator"},
                {"name": "A  20-bar ρ",  "id": "mean_corr_a"},
                {"name": "B  5-bar ρ",   "id": "mean_corr_b"},
                {"name": "N Peaks",      "id": "n_peaks"},
            ],
            data=[],
            sort_action="native",
            filter_action="native",
            page_size=40,
            style_table={"overflowX": "auto"},
            style_cell={
                "backgroundColor": CARD_BG, "color": TEXT,
                "fontSize": "12px", "padding": "5px 8px",
                "border": f"1px solid {BORDER}",
                "textAlign": "left",
                "maxWidth": "220px", "overflow": "hidden",
                "textOverflow": "ellipsis",
            },
            style_cell_conditional=[
                {"if": {"column_id": c}, "textAlign": "center"}
                for c in ("chart_link", "mean_corr_a", "mean_corr_b", "n_peaks")
            ],
            style_header={
                "backgroundColor": SIDEBAR_BG, "color": MUTED,
                "fontWeight": "600", "border": f"1px solid {BORDER}",
                "fontSize": "11px", "textAlign": "center",
            },
            style_data_conditional=[
                {"if": {"filter_query": "{mean_corr_a} > 0.6", "column_id": "mean_corr_a"},
                 "color": "#3fb950", "fontWeight": "600"},
                {"if": {"filter_query": "{mean_corr_a} < -0.6", "column_id": "mean_corr_a"},
                 "color": "#f85149", "fontWeight": "600"},
                {"if": {"filter_query": "{mean_corr_b} > 0.6", "column_id": "mean_corr_b"},
                 "color": "#3fb950", "fontWeight": "600"},
                {"if": {"filter_query": "{mean_corr_b} < -0.6", "column_id": "mean_corr_b"},
                 "color": "#f85149", "fontWeight": "600"},
            ],
            markdown_options={"link_target": "_self"},
        ),

        html.Div(id="pc-corr-summary", style={**_S_CARD, "marginTop": "16px"}),
    ])


def _all_stock_group_options() -> list[dict]:
    opts: list[dict] = []
    try:
        with get_session() as s:
            runs = s.execute(
                select(StockClusterRun.fiscal_year, StockClusterRun.n_clusters)
                .order_by(StockClusterRun.created_at.desc())
            ).all()
        for r in runs:
            opts.append({
                "label": f"{r.fiscal_year}  ({r.n_clusters} clusters, DB)",
                "value": f"db:{r.fiscal_year}",
            })
    except Exception:
        pass

    try:
        import configparser
        cfg = configparser.ConfigParser(allow_no_value=True)
        cfg.read(_STOCK_CODES_INI)
        for section in cfg.sections():
            n = len([k for k in cfg[section]])
            opts.append({
                "label": f"{section}  ({n} stocks, INI)",
                "value": f"ini:{section}",
            })
    except Exception:
        pass

    return opts


def _stock_options_for_group(group_key: str | None) -> list[dict]:
    if not group_key:
        return []
    try:
        source, name = group_key.split(":", 1)
    except ValueError:
        return []

    if source == "db":
        try:
            with get_session() as s:
                run = s.execute(
                    select(StockClusterRun)
                    .where(StockClusterRun.fiscal_year == name)
                ).scalar_one_or_none()
                if run is None:
                    return []
                members = s.execute(
                    select(StockClusterMember.stock_code)
                    .where(StockClusterMember.run_id == run.id,
                           StockClusterMember.is_representative.is_(True))
                    .order_by(StockClusterMember.stock_code)
                ).scalars().all()
                codes = [c for c in members if c not in _MAJOR_SET]
                name_rows = s.execute(
                    select(Stock.code, Stock.name).where(Stock.code.in_(codes))
                ).all()
                stock_names = {r.code: r.name for r in name_rows}
            return [
                {"label": f"{c}  {stock_names.get(c, '')}", "value": c}
                for c in codes
            ]
        except Exception:
            return []

    if source == "ini":
        try:
            codes = load_stock_codes(_STOCK_CODES_INI, name)
            codes = [c for c in codes if c not in _MAJOR_SET]
            return [{"label": c, "value": c} for c in codes]
        except Exception:
            return []

    return []


_MC_CHART_HEIGHT_BASE = 620 + 110 * len(MAJOR_INDICATORS)


def _moving_corr_page() -> html.Div:
    _sidebar = html.Div(
        style={
            "flex": "0 0 180px",
            "background": CARD_BG,
            "border": f"1px solid {BORDER}",
            "borderRadius": "6px",
            "padding": "8px 10px",
            "alignSelf": "flex-start",
            "marginTop": "12px",
        },
        children=[
            html.Span("Indicators", style={**_S_LABEL, "marginTop": "4px"}),
            _ind_item("ind-sma-check", "SMA",
                      [("ind-sma-period", "Period", 20, 2, 500)]),
            _ind_item("ind-ema-check", "EMA",
                      [("ind-ema-period", "Period", 20, 2, 500)]),
            _ind_item("ind-bb-check", "Bollinger Bands", [
                ("ind-bb-period", "Period", 20, 2, 500),
                ("ind-bb-std",    "Std Dev", 2.0, 0.5, 5.0),
            ]),
            _ind_item("ind-ichi-check", "Ichimoku", [
                ("ind-ichi-tenkan",   "Tenkan",   9,  1, 100),
                ("ind-ichi-kijun",    "Kijun",   26,  1, 200),
                ("ind-ichi-senkou-b", "Senkou B", 52,  1, 300),
                ("ind-ichi-displace", "Displace", 26,  1, 100),
            ]),
            _ind_item("ind-rsi-check", "RSI",
                      [("ind-rsi-period", "Period", 14, 2, 100)]),
            _ind_item("ind-macd-check", "MACD", [
                ("ind-macd-fast", "Fast",   12, 2, 200),
                ("ind-macd-slow", "Slow",   26, 2, 500),
                ("ind-macd-sig",  "Signal",  9, 2, 100),
            ]),
            _ind_item("ind-atr-check", "ATR",
                      [("ind-atr-period", "Period", 14, 2, 200)]),
            _ind_item("ind-zz-check", "Zigzag", [
                ("ind-zz-size", "Size",    5, 1, 20),
                ("ind-zz-mid",  "Mid",     2, 0, 10),
            ]),
        ],
    )

    _controls = html.Div(
        style={"display": "flex", "gap": "16px", "alignItems": "flex-end", "flexWrap": "wrap"},
        children=[
            html.Div(style={"flex": "0 0 240px"}, children=[
                html.Span("Stock group", style=_S_LABEL),
                dcc.Dropdown(
                    id="mc-group-dd",
                    options=_all_stock_group_options(),
                    value=None, clearable=True, searchable=False,
                    placeholder="Select group…",
                ),
            ]),
            html.Div(style={"flex": "0 0 220px"}, children=[
                html.Span("Stock", style=_S_LABEL),
                dcc.Dropdown(
                    id="mc-stock-dd",
                    options=[], value=None,
                    clearable=True, searchable=True,
                    placeholder="Select…",
                ),
            ]),
            html.Div(style={"flex": "0 0 160px"}, children=[
                html.Span("or type code", style=_S_LABEL),
                dcc.Input(
                    id="mc-stock-input", type="text",
                    debounce=True, placeholder="e.g. 9101.T",
                    style=_INPUT_STYLE,
                ),
            ]),
            html.Div(style={"flex": "0 0 120px"}, children=[
                html.Span("Window (bars)", style=_S_LABEL),
                dcc.Input(
                    id="mc-window", type="number",
                    value=20, min=5, max=500, step=1,
                    debounce=True,
                    style=_INPUT_STYLE,
                ),
            ]),
            html.Div(style={"flex": "0 0 110px"}, children=[
                html.Span("Granularity", style=_S_LABEL),
                dcc.Dropdown(
                    id="mc-gran",
                    options=[{"label": "Daily", "value": "1d"},
                             {"label": "Hourly", "value": "1h"}],
                    value="1d", clearable=False,
                ),
            ]),
            html.Div(style={"flex": "0 0 140px"}, children=[
                html.Span("Start", style=_S_LABEL),
                dcc.Input(
                    id="mc-start", type="text",
                    value="2022-01-01", debounce=True,
                    placeholder="YYYY-MM-DD",
                    style=_INPUT_STYLE,
                ),
            ]),
            html.Div(style={"flex": "0 0 140px"}, children=[
                html.Span("End (blank = today)", style=_S_LABEL),
                dcc.Input(
                    id="mc-end", type="text",
                    value="", debounce=True,
                    placeholder="YYYY-MM-DD",
                    style=_INPUT_STYLE,
                ),
            ]),
        ],
    )

    _chart_area = html.Div(
        style={"flex": "1", "minWidth": "0"},
        children=[
            html.Div(id="mc-status", style={**_S_CARD, "marginTop": "12px", "marginBottom": "8px"}),
            dcc.Graph(
                id="mc-chart",
                figure=empty_figure("Select a stock to view moving correlation"),
                style={"height": f"{_MC_CHART_HEIGHT_BASE}px"},
                config={"scrollZoom": True, "displayModeBar": True,
                        "modeBarButtonsToRemove": ["lasso2d", "select2d"]},
            ),
        ],
    )

    return html.Div(style=_S_PAGE, children=[
        _nav("/moving-corr"),
        html.H3("Moving Correlation vs Major Indices",
                style={"color": ACCENT, "margin": "0 0 16px 0"}),
        _controls,
        html.Div(
            style={"display": "flex", "gap": "16px", "alignItems": "flex-start"},
            children=[_sidebar, _chart_area],
        ),
    ])


# ── Top-level layout with routing ─────────────────────────────────────────────

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    html.Div(id="page-content"),
])


@callback(
    Output("page-content", "children"),
    Input("url", "pathname"),
    Input("url", "search"),
)
def _route(pathname: str | None, search: str | None) -> html.Div:
    if pathname == "/pair" and search:
        params = parse_qs(search.lstrip("?"))
        stock_a = (params.get("a") or [None])[0]
        stock_b = (params.get("b") or [None])[0]
        if stock_a and stock_b:
            return _pair_page(stock_a, stock_b)
    if pathname == "/peak-corr":
        return _peak_corr_page()
    if pathname == "/moving-corr":
        return _moving_corr_page()
    return _main_page()


# ── Main page callbacks ───────────────────────────────────────────────────────


@callback(
    Output("run-dd", "options"),
    Output("run-dd", "value"),
    Input("_init", "data"),
)
def _load_runs(_: Any) -> tuple[list[dict], Any]:
    with get_session() as session:
        runs = session.execute(
            select(CorrRun).order_by(CorrRun.created_at.desc())
        ).scalars().all()
        options = [
            {
                "label": (
                    f"{r.created_at.strftime('%Y-%m-%d %H:%M')}  |  "
                    f"{r.start_dt.date()} → {r.end_dt.date()}  |  "
                    f"win={r.window_days} step={r.step_days}  |  "
                    f"{r.n_stocks} stocks / {r.n_windows} windows"
                ),
                "value": r.id,
            }
            for r in runs
        ]
    return options, (options[0]["value"] if options else None)


@callback(
    Output("run-meta", "children"),
    Output("pair-tbl", "data"),
    Input("run-dd", "value"),
    Input("stock-filter", "value"),
)
def _update(run_id: int | None, stock_filter: str | None) -> tuple:
    if run_id is None:
        return "No run selected.", []

    with get_session() as session:
        run = session.get(CorrRun, run_id)
        if run is None:
            return "Run not found.", []

        run_meta_args = (
            run.start_dt, run.end_dt, run.granularity,
            run.window_days, run.step_days, run.n_stocks,
            run.n_windows, run.created_at,
        )

        stmt = (
            select(StockCorrPair)
            .where(StockCorrPair.corr_run_id == run_id)
            .order_by(StockCorrPair.mean_corr.desc())
        )
        if stock_filter and stock_filter.strip():
            sf = stock_filter.strip()
            from sqlalchemy import or_
            stmt = stmt.where(
                or_(StockCorrPair.stock_a == sf, StockCorrPair.stock_b == sf)
            )
        pair_tuples = [
            (p.stock_a, p.stock_b, p.mean_corr, p.std_corr, p.n_windows)
            for p in session.execute(stmt).scalars()
        ]

    all_codes = {a for a, *_ in pair_tuples} | {b for _, b, *_ in pair_tuples}
    names = _lookup_names(all_codes)

    meta = _meta_card_from_args(*run_meta_args)
    rows = [
        {
            "chart_link":  f"[📊](/pair?{urlencode({'a': stock_a, 'b': stock_b})})",
            "rank":        i + 1,
            "stock_a":     stock_a,
            "name_a":      names.get(stock_a, ""),
            "stock_b":     stock_b,
            "name_b":      names.get(stock_b, ""),
            "mean_corr":   f"{mean_corr:+.4f}",
            "std_corr":    f"{std_corr:.4f}",
            "abs_rounded": f"{abs(round(mean_corr, 2)):.2f}",
            "n_windows":   n_windows,
        }
        for i, (stock_a, stock_b, mean_corr, std_corr, n_windows) in enumerate(pair_tuples)
    ]
    return meta, rows


# ── Pair chart page callbacks ─────────────────────────────────────────────────


@callback(
    Output("pair-header",    "children"),
    Output("pair-subheader", "children"),
    Output("pair-chart",     "figure"),
    Input("pair-data", "data"),
)
def _update_pair_charts(data: dict | None) -> tuple:
    if not data:
        return "", "", empty_figure("No price data")

    stock_a: str = data["stock_a"]
    stock_b: str = data["stock_b"]

    names  = _lookup_names({stock_a, stock_b})
    name_a = names.get(stock_a, stock_a)
    name_b = names.get(stock_b, stock_b)

    end   = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(days=3 * 365)

    with get_session() as session:
        cache_a = DataCache(stock_a, "1d")
        cache_a.load(session, start, end)
        cache_b = DataCache(stock_b, "1d")
        cache_b.load(session, start, end)

    title_a = f"{stock_a}  —  {name_a}" if name_a != stock_a else stock_a
    title_b = f"{stock_b}  —  {name_b}" if name_b != stock_b else stock_b

    fig = build_pair_figure(cache_a, cache_b, title_a=title_a, title_b=title_b)

    header    = f"{stock_a} × {stock_b}"
    subheader = f"{name_a}  ×  {name_b}" if (name_a and name_b) else ""
    return header, subheader, fig


# ── Peak-corr page callbacks ──────────────────────────────────────────────────


@callback(
    Output("pc-run-dd", "options"),
    Output("pc-run-dd", "value"),
    Input("_pc_init", "data"),
)
def _load_pc_runs(_: Any) -> tuple[list[dict], Any]:
    with get_session() as session:
        runs = session.execute(
            select(PeakCorrRun).order_by(PeakCorrRun.created_at.desc())
        ).scalars().all()
        options = [
            {
                "label": (
                    f"{r.created_at.strftime('%Y-%m-%d %H:%M')}  |  "
                    f"{r.start_dt.date()} → {r.end_dt.date()}  |  "
                    f"zz={r.zz_size}/{r.zz_middle_size}  |  "
                    f"{r.n_indicators} indicators / {r.n_stocks} stocks"
                    + (f"  |  set={r.stock_set}" if r.stock_set else "")
                ),
                "value": r.id,
            }
            for r in runs
        ]
    return options, (options[0]["value"] if options else None)


@callback(
    Output("pc-ind-dd", "options"),
    Output("pc-ind-dd", "value"),
    Input("pc-run-dd", "value"),
)
def _load_pc_indicators(run_id: int | None) -> tuple[list[dict], Any]:
    if run_id is None:
        return [], None
    with get_session() as session:
        inds = session.execute(
            select(PeakCorrResult.indicator)
            .where(PeakCorrResult.run_id == run_id)
            .distinct()
            .order_by(PeakCorrResult.indicator)
        ).scalars().all()
    return [{"label": ind, "value": ind} for ind in inds], None


@callback(
    Output("pc-run-meta",      "children"),
    Output("pc-tbl",           "data"),
    Output("pc-corr-summary",  "children"),
    Input("pc-run-dd",  "value"),
    Input("pc-ind-dd",  "value"),
)
def _update_pc_table(run_id: int | None, indicator: str | None) -> tuple:
    if run_id is None:
        return "No run selected.", [], ""

    with get_session() as session:
        run = session.get(PeakCorrRun, run_id)
        if run is None:
            return "Run not found.", [], ""

        run_meta_args = (
            run.created_at, run.start_dt, run.end_dt, run.granularity,
            run.zz_size, run.zz_middle_size, run.stock_set,
            run.n_indicators, run.n_stocks,
        )

        stmt = (
            select(PeakCorrResult)
            .where(PeakCorrResult.run_id == run_id)
            .order_by(PeakCorrResult.indicator, PeakCorrResult.stock)
        )
        if indicator:
            stmt = stmt.where(PeakCorrResult.indicator == indicator)

        result_tuples = [
            (r.stock, r.indicator, r.mean_corr_a, r.mean_corr_b, r.n_peaks)
            for r in session.execute(stmt).scalars()
        ]

    all_codes = {stock for stock, *_ in result_tuples} | {ind for _, ind, *_ in result_tuples}
    names = _lookup_names(all_codes)

    rows = [
        {
            "chart_link":  f"[📊](/pair?{urlencode({'a': stock, 'b': ind})})",
            "stock":       stock,
            "stock_name":  names.get(stock, ""),
            "indicator":   ind,
            "mean_corr_a": f"{corr_a:+.4f}" if corr_a is not None else "—",
            "mean_corr_b": f"{corr_b:+.4f}" if corr_b is not None else "—",
            "n_peaks":     n_peaks,
        }
        for stock, ind, corr_a, corr_b, n_peaks in result_tuples
    ]

    paired = [(r[2], r[3]) for r in result_tuples if r[2] is not None and r[3] is not None]
    if len(paired) >= 2:
        a_arr = np.array([p[0] for p in paired])
        b_arr = np.array([p[1] for p in paired])
        corr_ab = float(np.corrcoef(a_arr, b_arr)[0, 1])
        scope = f"for {indicator}" if indicator else "across all indicators"
        summary_text = (
            f"corr(A, B) = {corr_ab:+.4f}  "
            f"({len(paired)} stock-indicator pairs {scope})"
        )
    else:
        summary_text = "Not enough data to compute corr(A, B)"

    summary = [
        html.Span("A / B correlation: ", style={"color": MUTED}),
        html.Span(summary_text, style={"color": TEXT, "fontWeight": "500"}),
    ]

    return _pc_meta_card(*run_meta_args), rows, summary


# ── Moving-corr page callbacks ────────────────────────────────────────────────


@callback(
    Output("mc-stock-dd", "options"),
    Output("mc-stock-dd", "value"),
    Input("mc-group-dd",  "value"),
)
def _update_mc_stock_list(group_key: str | None) -> tuple:
    return _stock_options_for_group(group_key), None


def _parse_date(s: str | None, fallback: datetime.datetime) -> datetime.datetime:
    if not s or not s.strip():
        return fallback
    try:
        dt = datetime.datetime.fromisoformat(s.strip())
        return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return fallback


@callback(
    Output("mc-chart",   "figure"),
    Output("mc-chart",   "style"),
    Output("mc-status",  "children"),
    Input("mc-stock-dd",    "value"),
    Input("mc-stock-input", "value"),
    Input("mc-window",      "value"),
    Input("mc-gran",        "value"),
    Input("mc-start",       "value"),
    Input("mc-end",         "value"),
    # indicator checkboxes + params
    Input("ind-sma-check",  "value"),
    Input("ind-sma-period", "value"),
    Input("ind-ema-check",  "value"),
    Input("ind-ema-period", "value"),
    Input("ind-bb-check",      "value"),
    Input("ind-bb-period",     "value"),
    Input("ind-bb-std",        "value"),
    Input("ind-ichi-check",    "value"),
    Input("ind-ichi-tenkan",   "value"),
    Input("ind-ichi-kijun",    "value"),
    Input("ind-ichi-senkou-b", "value"),
    Input("ind-ichi-displace", "value"),
    Input("ind-rsi-check",  "value"),
    Input("ind-rsi-period", "value"),
    Input("ind-macd-check", "value"),
    Input("ind-macd-fast",  "value"),
    Input("ind-macd-slow",  "value"),
    Input("ind-macd-sig",   "value"),
    Input("ind-atr-check",  "value"),
    Input("ind-atr-period", "value"),
    Input("ind-zz-check",   "value"),
    Input("ind-zz-size",    "value"),
    Input("ind-zz-mid",     "value"),
)
def _update_mc_chart(
    stock_dd: str | None,
    stock_input: str | None,
    window: int | None,
    gran: str | None,
    start_str: str | None,
    end_str: str | None,
    sma_check:  list, sma_period:  int | None,
    ema_check:  list, ema_period:  int | None,
    bb_check:   list, bb_period:   int | None, bb_std: float | None,
    ichi_check: list, ichi_tenkan: int | None, ichi_kijun: int | None,
    ichi_senkou_b: int | None, ichi_displace: int | None,
    rsi_check:  list, rsi_period:  int | None,
    macd_check: list, macd_fast:   int | None, macd_slow: int | None, macd_sig: int | None,
    atr_check:  list, atr_period:  int | None,
    zz_check:   list, zz_size:     int | None, zz_mid: int | None,
) -> tuple:
    stock = (stock_input or "").strip() or stock_dd
    if not stock:
        default_style = {"height": f"{_MC_CHART_HEIGHT_BASE}px"}
        return empty_figure("Select a stock to view moving correlation"), default_style, "No stock selected."

    window = int(window) if window else 20
    gran   = gran or "1d"

    now   = datetime.datetime.now(datetime.timezone.utc)
    start = _parse_date(start_str, datetime.datetime(2022, 1, 1, tzinfo=datetime.timezone.utc))
    end   = _parse_date(end_str, now)

    with get_session() as session:
        cache = DataCache(stock, gran)
        cache.load(session, start, end)

        if not cache.bars:
            msg = f"No OHLCV data found for {stock} ({gran}) in DB."
            return empty_figure(msg), {"height": f"{_MC_CHART_HEIGHT_BASE}px"}, msg

        inds_to_load   = ["^N225"] if gran == "1h" else list(MAJOR_INDICATORS)
        indicator_map: dict[str, pd.Series] = {}
        first_ind_cache: DataCache | None = None
        first_ind_code:  str = ""
        for ind_code in inds_to_load:
            cache_ind = DataCache(ind_code, gran)
            cache_ind.load(session, start, end)
            if not cache_ind.bars:
                continue
            if first_ind_cache is None:
                first_ind_cache = cache_ind
                first_ind_code  = ind_code
            if gran == "1h":
                indicator_map[ind_code] = pd.Series({b.dt: b.close for b in cache_ind.bars})
            else:
                indicator_map[ind_code] = pd.Series({b.dt.date(): b.close for b in cache_ind.bars})

    if not indicator_map:
        msg = "No indicator data found in DB. Run the data collector first."
        return empty_figure(msg), {"height": f"{_MC_CHART_HEIGHT_BASE}px"}, msg

    if gran == "1h":
        stock_series = pd.Series({b.dt: b.close for b in cache.bars})
        corr_map = compute_moving_corr(stock_series, indicator_map, window=window)
    else:
        stock_series = pd.Series({b.dt.date(): b.close for b in cache.bars})
        corr_map = compute_moving_corr(stock_series, indicator_map, window=window)

    # ── Compute overlays and sub-panels ───────────────────────────────────────

    bars      = cache.bars
    s_closes  = [b.close  for b in bars]
    s_highs   = [b.high   for b in bars]
    s_lows    = [b.low    for b in bars]

    ind_bars   = first_ind_cache.bars if first_ind_cache else []
    i_closes   = [b.close for b in ind_bars]
    i_highs    = [b.high  for b in ind_bars]
    i_lows     = [b.low   for b in ind_bars]

    sma_on  = bool(sma_check)
    ema_on  = bool(ema_check)
    bb_on   = bool(bb_check)
    ichi_on = bool(ichi_check)
    rsi_on  = bool(rsi_check)
    macd_on = bool(macd_check)
    atr_on  = bool(atr_check)
    zz_on   = bool(zz_check)

    sma_p    = int(sma_period    or 20)
    ema_p    = int(ema_period    or 20)
    bb_p     = int(bb_period     or 20)
    bb_s     = float(bb_std      or 2.0)
    ichi_t   = int(ichi_tenkan   or 9)
    ichi_k   = int(ichi_kijun    or 26)
    ichi_sb  = int(ichi_senkou_b or 52)
    ichi_d   = int(ichi_displace or 26)
    rsi_p    = int(rsi_period    or 14)
    mf     = int(macd_fast   or 12)
    ms_    = int(macd_slow   or 26)
    msig   = int(macd_sig    or 9)
    atr_p  = int(atr_period  or 14)
    zz_s   = int(zz_size     or 5)
    zz_m   = int(zz_mid      or 2)

    stock_overlays: list[dict] = []
    ind_overlays:   list[dict] = []

    if sma_on:
        stock_overlays += _sma_overlays(s_closes, sma_p)
        if i_closes:
            ind_overlays += _sma_overlays(i_closes, sma_p)

    if ema_on:
        stock_overlays += _ema_overlays(s_closes, ema_p)
        if i_closes:
            ind_overlays += _ema_overlays(i_closes, ema_p)

    if bb_on:
        stock_overlays += _bb_overlays(s_closes, bb_p, bb_s)
        if i_closes:
            ind_overlays += _bb_overlays(i_closes, bb_p, bb_s)

    if ichi_on:
        stock_overlays += _ichi_overlays(s_highs, s_lows, s_closes, ichi_t, ichi_k, ichi_sb, ichi_d)
        if i_closes:
            ind_overlays += _ichi_overlays(i_highs, i_lows, i_closes, ichi_t, ichi_k, ichi_sb, ichi_d)

    sub_panels: list[dict] = []

    if rsi_on:
        sub_panels.append({
            "label": f"RSI({rsi_p})", "kind": "line",
            "y": calc_rsi(s_closes, rsi_p),
            "color": "#AB47BC", "hlines": [30, 70],
        })

    if macd_on:
        m = calc_macd(s_closes, mf, ms_, msig)
        sub_panels.append({
            "label": f"MACD({mf},{ms_},{msig})", "kind": "macd", **m,
        })

    if atr_on:
        sub_panels.append({
            "label": f"ATR({atr_p})", "kind": "line",
            "y": calc_atr(s_highs, s_lows, s_closes, atr_p),
            "color": "#FF7043",
        })

    if zz_on:
        intraday = len(bars) >= 2 and bars[0].dt.date() == bars[1].dt.date()
        ts_fmt   = "%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d"
        peaks = detect_peaks(s_highs, s_lows, size=zz_s, middle_size=zz_m)
        pts = [
            (bars[p.bar_index].dt.strftime(ts_fmt), p.price, p.direction)
            for p in peaks if abs(p.direction) == 2 and p.bar_index < len(bars)
        ]
        if pts:
            stock_overlays.append({"kind": "zigzag", "points": pts})
        if i_closes and ind_bars:
            i_intraday = len(ind_bars) >= 2 and ind_bars[0].dt.date() == ind_bars[1].dt.date()
            i_ts_fmt   = "%Y-%m-%d %H:%M" if i_intraday else "%Y-%m-%d"
            i_highs_zz = [b.high for b in ind_bars]
            i_lows_zz  = [b.low  for b in ind_bars]
            i_peaks = detect_peaks(i_highs_zz, i_lows_zz, size=zz_s, middle_size=zz_m)
            i_pts = [
                (ind_bars[p.bar_index].dt.strftime(i_ts_fmt), p.price, p.direction)
                for p in i_peaks if abs(p.direction) == 2 and p.bar_index < len(ind_bars)
            ]
            if i_pts:
                ind_overlays.append({"kind": "zigzag", "points": i_pts})

    # ── Build figure ──────────────────────────────────────────────────────────

    names = _lookup_names({stock})
    name  = names.get(stock, "")
    title = (
        f"{stock}" + (f" — {name}" if name else "")
        + f"  |  ρ vs major indices  |  window={window} bars"
    )

    fig = build_moving_corr_figure(
        cache, corr_map, title=title,
        indicator_cache=first_ind_cache,
        indicator_label=first_ind_code,
        stock_overlays=stock_overlays or None,
        ind_overlays=ind_overlays or None,
        sub_panels=sub_panels or None,
    )

    # Dynamic chart height
    n_corr = len(corr_map)
    n_sub  = len(sub_panels)
    chart_h = 600 + 120 * n_corr + 120 * n_sub
    chart_style = {"height": f"{chart_h}px"}

    bar0, barN = cache.bars[0].dt.date(), cache.bars[-1].dt.date()
    status = [
        html.Span("Stock: ",    style={"color": MUTED}),
        html.Span(f"{stock}  ", style={"color": TEXT, "fontWeight": "500", "marginRight": "20px"}),
        html.Span("Bars: ",     style={"color": MUTED}),
        html.Span(f"{len(cache.bars)}  ", style={"color": TEXT, "marginRight": "20px"}),
        html.Span("Period: ",   style={"color": MUTED}),
        html.Span(f"{bar0} → {barN}  ", style={"color": TEXT, "marginRight": "20px"}),
        html.Span("Indicators: ", style={"color": MUTED}),
        html.Span(f"{len(indicator_map)}  ", style={"color": TEXT, "marginRight": "20px"}),
        html.Span("Window: ",   style={"color": MUTED}),
        html.Span(f"{window} bars", style={"color": TEXT}),
    ]
    return fig, chart_style, status


# ── Helpers ───────────────────────────────────────────────────────────────────


def _meta_card_from_args(
    start_dt: Any, end_dt: Any, granularity: str,
    window_days: int, step_days: int, n_stocks: int,
    n_windows: int, created_at: Any,
) -> list:
    items = [
        ("Period",      f"{start_dt.date()} → {end_dt.date()}"),
        ("Granularity", granularity),
        ("Window",      f"{window_days} bars"),
        ("Step",        f"{step_days} bars"),
        ("Stocks",      str(n_stocks)),
        ("Windows",     str(n_windows)),
        ("Pairs",       f"{n_stocks * (n_stocks - 1) // 2:,}"),
        ("Run date",    created_at.strftime("%Y-%m-%d %H:%M UTC")),
    ]
    return [
        html.Span(
            [html.Span(k + ": ", style={"color": MUTED}),
             html.Span(v, style={"color": TEXT, "fontWeight": "500", "marginRight": "24px"})],
        )
        for k, v in items
    ]


def _pc_meta_card(
    created_at: Any, start_dt: Any, end_dt: Any, granularity: str,
    zz_size: int, zz_middle_size: int, stock_set: str | None,
    n_indicators: int, n_stocks: int,
) -> list:
    items = [
        ("Period",      f"{start_dt.date()} → {end_dt.date()}"),
        ("Granularity", granularity),
        ("ZZ size",     f"{zz_size} / {zz_middle_size}"),
        ("Indicators",  str(n_indicators)),
        ("Stocks",      str(n_stocks)),
        ("Stock set",   stock_set or "—"),
        ("Run date",    created_at.strftime("%Y-%m-%d %H:%M UTC")),
    ]
    return [
        html.Span(
            [html.Span(k + ": ", style={"color": MUTED}),
             html.Span(v, style={"color": TEXT, "fontWeight": "500", "marginRight": "24px"})],
        )
        for k, v in items
    ]


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8051
    print(f"Starting Correlation UI at http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
