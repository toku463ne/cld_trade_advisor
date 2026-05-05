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
from src.indicators.moving_corr import compute_moving_corr
from src.indicators.zigzag import detect_peaks
from src.data.db import get_session
from src.data.models import Stock
from src.simulator.cache import DataCache
from src.viz.charts import (
    BG, SIDEBAR_BG, CARD_BG, BORDER, TEXT, MUTED, ACCENT,
    ZigzagPoint, build_moving_corr_figure, build_pair_figure, empty_figure,
)

# ── App ───────────────────────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    title="Correlation Analysis",
    suppress_callback_exceptions=True,
)

# ── Style helpers (re-use palette from charts.py) ─────────────────────────────

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

_MAJOR_SET: set[str] = set(MAJOR_INDICATORS)


def _peaks_for_cache(cache: DataCache) -> list[ZigzagPoint]:
    """Return confirmed zigzag points (|dir|==2) as (date_str, price, direction) tuples.

    Early peaks (|dir|==1) are excluded: they can appear between two confirmed
    highs of the same sign, creating a visual artifact where ▲ sits at the
    trough of the zigzag line.
    """
    if not cache.bars:
        return []
    bars  = cache.bars
    intraday = len(bars) >= 2 and bars[0].dt.date() == bars[1].dt.date()
    ts_fmt   = "%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d"
    highs = [b.high for b in bars]
    lows  = [b.low  for b in bars]
    peaks = detect_peaks(highs, lows, size=5, middle_size=2)
    return [
        (bars[p.bar_index].dt.strftime(ts_fmt), p.price, p.direction)
        for p in peaks
        if abs(p.direction) == 2 and p.bar_index < len(bars)
    ]


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


_INPUT_STYLE: dict = {
    "background": CARD_BG, "color": TEXT,
    "border": f"1px solid {BORDER}", "borderRadius": "4px",
    "padding": "8px", "width": "100%", "fontSize": "13px",
    "boxSizing": "border-box",
}

# Height: price + vol + indicator panel + fixed pixels per corr panel
_MC_CHART_HEIGHT = 620 + 110 * len(MAJOR_INDICATORS)


_STOCK_CODES_INI = "configs/stock_codes.ini"


def _all_stock_group_options() -> list[dict]:
    """Return dropdown options for every available stock group.

    Values are prefixed with their source:
      "db:<fiscal_year>"   — cluster run from DB
      "ini:<section>"      — section in stock_codes.ini
    """
    opts: list[dict] = []

    # DB cluster runs (most recent first)
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

    # INI sections
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
    """Return stock dropdown options for a given group key (e.g. 'db:classified2023')."""
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


def _moving_corr_page() -> html.Div:
    return html.Div(style=_S_PAGE, children=[
        _nav("/moving-corr"),
        html.H3("Moving Correlation vs Major Indices",
                style={"color": ACCENT, "margin": "0 0 16px 0"}),

        # ── Controls ──────────────────────────────────────────────────────────
        html.Div(
            style={"display": "flex", "gap": "16px", "alignItems": "flex-end",
                   "flexWrap": "wrap"},
            children=[
                # Stock group selector
                html.Div(style={"flex": "0 0 240px"}, children=[
                    html.Span("Stock group", style=_S_LABEL),
                    dcc.Dropdown(
                        id="mc-group-dd",
                        options=_all_stock_group_options(),
                        value=None, clearable=True, searchable=False,
                        placeholder="Select group…",
                    ),
                ]),
                # Stock dropdown (populated by callback)
                html.Div(style={"flex": "0 0 220px"}, children=[
                    html.Span("Stock", style=_S_LABEL),
                    dcc.Dropdown(
                        id="mc-stock-dd",
                        options=[], value=None,
                        clearable=True, searchable=True,
                        placeholder="Select…",
                    ),
                ]),
                # Free-text code entry
                html.Div(style={"flex": "0 0 160px"}, children=[
                    html.Span("or type code", style=_S_LABEL),
                    dcc.Input(
                        id="mc-stock-input", type="text",
                        debounce=True, placeholder="e.g. 9101.T",
                        style=_INPUT_STYLE,
                    ),
                ]),
                # Window
                html.Div(style={"flex": "0 0 120px"}, children=[
                    html.Span("Window (bars)", style=_S_LABEL),
                    dcc.Input(
                        id="mc-window", type="number",
                        value=20, min=5, max=500, step=1,
                        debounce=True,
                        style=_INPUT_STYLE,
                    ),
                ]),
                # Granularity
                html.Div(style={"flex": "0 0 110px"}, children=[
                    html.Span("Granularity", style=_S_LABEL),
                    dcc.Dropdown(
                        id="mc-gran",
                        options=[{"label": "Daily", "value": "1d"},
                                 {"label": "Hourly", "value": "1h"}],
                        value="1d", clearable=False,
                    ),
                ]),
                # Start date
                html.Div(style={"flex": "0 0 140px"}, children=[
                    html.Span("Start", style=_S_LABEL),
                    dcc.Input(
                        id="mc-start", type="text",
                        value="2022-01-01", debounce=True,
                        placeholder="YYYY-MM-DD",
                        style=_INPUT_STYLE,
                    ),
                ]),
                # End date
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
        ),

        # ── Status card ───────────────────────────────────────────────────────
        html.Div(id="mc-status", style={**_S_CARD, "marginTop": "12px",
                                        "marginBottom": "8px"}),

        # ── Chart ─────────────────────────────────────────────────────────────
        dcc.Graph(
            id="mc-chart",
            figure=empty_figure("Select a stock to view moving correlation"),
            style={"height": f"{_MC_CHART_HEIGHT}px"},
            config={"scrollZoom": True, "displayModeBar": True,
                    "modeBarButtonsToRemove": ["lasso2d", "select2d"]},
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

    zz_a = _peaks_for_cache(cache_a) if stock_a in _MAJOR_SET else None
    zz_b = _peaks_for_cache(cache_b) if stock_b in _MAJOR_SET else None

    fig = build_pair_figure(cache_a, cache_b, title_a=title_a, title_b=title_b,
                            zigzag_a=zz_a, zigzag_b=zz_b)

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
    Output("mc-chart",  "figure"),
    Output("mc-status", "children"),
    Input("mc-stock-dd",    "value"),
    Input("mc-stock-input", "value"),
    Input("mc-window",      "value"),
    Input("mc-gran",        "value"),
    Input("mc-start",       "value"),
    Input("mc-end",         "value"),
)
def _update_mc_chart(
    stock_dd: str | None,
    stock_input: str | None,
    window: int | None,
    gran: str | None,
    start_str: str | None,
    end_str: str | None,
) -> tuple:
    # Text input takes priority over dropdown when non-empty
    stock = (stock_input or "").strip() or stock_dd
    if not stock:
        return empty_figure("Select a stock to view moving correlation"), "No stock selected."

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
            return empty_figure(msg), msg

        # For hourly gran: only ^N225 (same trading hours as JP stocks → true hourly corr).
        # For daily gran: all major indicators using daily closes.
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
        return empty_figure(msg), msg

    if gran == "1h":
        stock_series = pd.Series({b.dt: b.close for b in cache.bars})
        corr_map = compute_moving_corr(stock_series, indicator_map, window=window)
    else:
        stock_series = pd.Series({b.dt.date(): b.close for b in cache.bars})
        corr_map = compute_moving_corr(stock_series, indicator_map, window=window)

    names = _lookup_names({stock})
    name  = names.get(stock, "")
    title = (
        f"{stock}" + (f" — {name}" if name else "")
        + f"  |  ρ vs major indices  |  window={window} bars"
    )

    ind_zigzag = _peaks_for_cache(first_ind_cache) if first_ind_cache else None
    fig = build_moving_corr_figure(
        cache, corr_map, title=title,
        indicator_cache=first_ind_cache,
        indicator_zigzag=ind_zigzag,
        indicator_label=first_ind_code,
    )

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
    return fig, status


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
