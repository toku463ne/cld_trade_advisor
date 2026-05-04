"""Correlation analysis viewer — multi-page Dash app.

Routes:
  /          → correlation pair table
  /pair?a=X&b=Y → side-by-side price charts for stock pair X and Y

Launch:
    uv run --env-file devenv python -m src.analysis.corr_ui
    # open http://localhost:8051
"""

from __future__ import annotations

import datetime
import sys
from typing import Any
from urllib.parse import urlencode, parse_qs

import yfinance as yf
import dash
from dash import Input, Output, callback, dash_table, dcc, html
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.analysis.models import CorrRun, StockCorrPair
from src.data.db import get_session
from src.data.models import Stock
from src.simulator.cache import DataCache
from src.viz.charts import (
    BG, SIDEBAR_BG, CARD_BG, BORDER, TEXT, MUTED, ACCENT,
    build_pair_figure, empty_figure,
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


def _main_page() -> html.Div:
    return html.Div(style=_S_PAGE, children=[
        dcc.Store(id="_init", data=True),

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


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8051
    print(f"Starting Correlation UI at http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
