"""Interactive trade advisor: daily proposals + backtest chart viewer.

Launch:
    uv run --env-file devenv python -m src.viz.app
    # then open http://localhost:8050

Tabs
----
- **Daily**   : today's RegimeSign proposals with regime status and stock charts.
- **Backtest**: OHLCV + strategy chart viewer for training runs.

Design principles
-----------------
- No strategy names are hardcoded in this file.
- Strategy list is loaded from the DB on page load.
- Params-table columns are generated from params_json keys of the selected run.
- Extra indicators / chart traces are registered in _EXTRA_INDICATOR_LOADERS
  and _EXTRA_TRACES, keyed by the *param name* that triggers them — so adding
  a new strategy never requires touching this file.
- For multi-stock runs a stock dropdown appears; the backtest is re-run live
  for the selected stock so the chart always shows one stock at a time.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Callable

import dash
from dash import Input, Output, State, callback, dash_table, dcc, html
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from sqlalchemy import select

from src.backtest.metrics import BacktestMetrics, compute_metrics
from src.backtest.runner import run_backtest
from src.backtest.train_models import TrainBestResult, TrainRun
from src.data.db import get_session
from src.simulator.cache import DataCache
from src.simulator.bar import BarData
from src.simulator.simulator import TradeSimulator
from src.viz import daily as _daily
from src.viz import maintenance as _maintenance
from src.viz.charts import (
    BG as _BG, SIDEBAR_BG as _SIDEBAR_BG, CARD_BG as _CARD_BG,
    BORDER as _BORDER, TEXT as _TEXT, MUTED as _MUTED, ACCENT as _ACCENT,
    GREEN as _GREEN, RED as _RED,
    empty_figure as _base_empty_figure,
)

# ── App ───────────────────────────────────────────────────────────────────────

_ASSETS = Path(__file__).parent / "assets"
app = dash.Dash(
    __name__,
    title="Trade Advisor",
    assets_folder=str(_ASSETS),
    suppress_callback_exceptions=True,
)

_TAB_STYLE: dict[str, Any] = {
    "backgroundColor": _SIDEBAR_BG,
    "color": _MUTED,
    "border": f"1px solid {_BORDER}",
    "borderBottom": "none",
    "padding": "8px 24px",
    "fontSize": "13px",
    "fontFamily": "'Segoe UI', Arial, sans-serif",
}
_TAB_SELECTED_STYLE: dict[str, Any] = {
    **_TAB_STYLE,
    "backgroundColor": _BG,
    "color": _ACCENT,
    "fontWeight": "600",
    "borderTop": f"2px solid {_ACCENT}",
}

# ── Param display config (keyed by param name, NOT strategy name) ─────────────

_PARAM_DISPLAY: dict[str, str] = {
    "sma_period":  "SMA",
    "sigma_mult":  "σ",
    "n_days":      "N",
    "m_days":      "M",
    "tp":          "TP%",
    "sl":          "SL%",
    "take_profit": "TP%",
    "stop_loss":   "SL%",
}

_SKIP_PARAMS: frozenset[str] = frozenset({"units"})

_PARAM_ORDER: list[str] = [
    "sma_period", "sigma_mult", "n_days", "m_days",
    "tp", "take_profit", "sl", "stop_loss",
]

_PCT_PARAMS: frozenset[str] = frozenset({"tp", "take_profit", "sl", "stop_loss"})


# ── Extra indicator loaders (keyed by triggering param name) ──────────────────

def _load_rolling_std(cache: DataCache, sma_period: int) -> None:
    cache.add_rolling_std(sma_period)

_EXTRA_INDICATOR_LOADERS: dict[str, Callable[[DataCache, int], None]] = {
    "sigma_mult": _load_rolling_std,
}


# ── Extra chart traces (keyed by triggering param name) ───────────────────────

def _bollinger_upper_band(
    params_json: dict, dates: list[str], bars: list[BarData],
) -> go.Scatter:
    period = params_json["sma_period"]
    sigma  = params_json["sigma_mult"]
    sma_v  = [b.indicators.get(f"SMA{period}") for b in bars]
    std_v  = [b.indicators.get(f"RSTD{period}") for b in bars]
    upper  = [
        s + sigma * d if (s is not None and d is not None and d > 0) else None
        for s, d in zip(sma_v, std_v)
    ]
    return go.Scatter(
        x=dates, y=upper,
        mode="lines",
        name=f"BB upper ({sigma}σ)",
        line=dict(color="#ab47bc", width=1.2, dash="dot"),
        hovertemplate="BB upper: %{y:,.0f}<extra></extra>",
    )

_EXTRA_TRACES: dict[str, Callable] = {
    "sigma_mult": _bollinger_upper_band,
}


# ── Column generation from params_json ────────────────────────────────────────

def _columns_from_params(params_json: dict) -> list[dict]:
    keys = set(params_json.keys()) - _SKIP_PARAMS
    ordered   = [k for k in _PARAM_ORDER if k in keys]
    remaining = sorted(keys - set(ordered))
    cols = [{"name": "#", "id": "rank"}]
    for k in ordered + remaining:
        cols.append({"name": _PARAM_DISPLAY.get(k, k), "id": k})
    cols.append({"name": "Score", "id": "score"})
    return cols


def _result_to_row(r: TrainBestResult) -> dict[str, Any]:
    row: dict[str, Any] = {"rank": r.rank, "score": f"{r.score:.3f}", "result_id": r.id}
    for key, val in r.params_json.items():
        if key in _SKIP_PARAMS:
            continue
        row[key] = f"{val:.0%}" if key in _PCT_PARAMS else val
    return row


_EMPTY_COLS = [{"name": "#", "id": "rank"}, {"name": "Score", "id": "score"}]

# ── Layout helpers ────────────────────────────────────────────────────────────

_S_SIDEBAR: dict[str, Any] = {
    "width": "320px", "minWidth": "320px",
    "height": "100vh", "overflowY": "auto",
    "background": _SIDEBAR_BG,
    "borderRight": f"1px solid {_BORDER}",
    "padding": "16px", "boxSizing": "border-box",
}
_S_MAIN: dict[str, Any] = {
    "flex": "1", "height": "100vh", "overflow": "hidden", "background": _BG,
}
_S_LABEL: dict[str, Any] = {
    "color": _MUTED, "fontSize": "11px",
    "textTransform": "uppercase", "letterSpacing": "0.5px",
    "marginTop": "14px", "marginBottom": "4px", "display": "block",
}
_S_LABEL_HIDDEN: dict[str, Any] = {**_S_LABEL, "display": "none"}
_S_CARD: dict[str, Any] = {
    "background": _CARD_BG, "border": f"1px solid {_BORDER}",
    "borderRadius": "6px", "padding": "12px", "marginTop": "12px",
    "color": _TEXT, "fontSize": "13px", "lineHeight": "1.8",
}
_S_METRIC_ROW: dict[str, Any] = {
    "display": "flex", "justifyContent": "space-between",
    "borderBottom": f"1px solid {_BORDER}", "padding": "2px 0",
}

# ── Layout ────────────────────────────────────────────────────────────────────

def _backtest_layout() -> html.Div:
    """Return the Backtest tab content (original sidebar + chart layout)."""
    return html.Div(
        style={
            "display": "flex",
            "fontFamily": "'Segoe UI', Arial, sans-serif",
            "background": _BG,
            "height": "calc(100vh - 44px)",
            "overflow": "hidden",
        },
        children=[
            dcc.Store(id="_init", data=True),

            # ── Sidebar ───────────────────────────────────────────────────────
            html.Div(style=_S_SIDEBAR, children=[
                html.H4("Backtest Review",
                        style={"color": _ACCENT, "margin": "0 0 10px 0", "fontSize": "16px"}),

                html.Span("Strategy", style=_S_LABEL),
                dcc.Dropdown(id="strategy-dd", options=[], value=None, clearable=False),

                html.Span("Training Run", style=_S_LABEL),
                dcc.Dropdown(id="run-dd", placeholder="Select run…", clearable=False),

                # Stock selector — visible only when the run covers multiple stocks
                html.Span("Stock", id="stock-label", style=_S_LABEL_HIDDEN),
                dcc.Dropdown(id="stock-dd", options=[], value=None, clearable=False,
                             style={"display": "none"}),

                html.Span("Parameter Set", style=_S_LABEL),
                dash_table.DataTable(
                    id="params-tbl",
                    columns=_EMPTY_COLS,
                    data=[],
                    row_selectable="single",
                    selected_rows=[0],
                    style_table={"overflowX": "auto", "marginTop": "4px"},
                    style_cell={
                        "backgroundColor": _CARD_BG, "color": _TEXT,
                        "fontSize": "12px", "padding": "4px 6px",
                        "border": f"1px solid {_BORDER}",
                        "textAlign": "center", "minWidth": "28px",
                    },
                    style_header={
                        "backgroundColor": _SIDEBAR_BG, "color": _MUTED,
                        "fontWeight": "600", "border": f"1px solid {_BORDER}",
                        "fontSize": "11px", "textAlign": "center",
                    },
                    style_data_conditional=[{
                        "if": {"state": "selected"},
                        "backgroundColor": "#1f3a5f",
                        "border": f"1px solid {_ACCENT}",
                    }],
                    page_size=30,
                ),

                html.Div(id="metrics-panel", style=_S_CARD),
            ]),

            # ── Main chart ────────────────────────────────────────────────────
            html.Div(style={**_S_MAIN, "height": "100%"}, children=[
                dcc.Graph(
                    id="main-chart",
                    style={"height": "100%"},
                    config={
                        "scrollZoom": True,
                        "displayModeBar": True,
                        "modeBarButtonsToRemove": ["autoScale2d", "lasso2d", "select2d"],
                        "toImageButtonOptions": {
                            "format": "png", "width": 1920, "height": 1080,
                            "filename": "trade_advisor_chart",
                        },
                    },
                ),
            ]),

            dcc.Store(id="run-store"),
        ],
    )


app.layout = html.Div(
    style={
        "fontFamily": "'Segoe UI', Arial, sans-serif",
        "background": _BG,
        "height": "100vh",
        "overflow": "hidden",
    },
    children=[
        dcc.Tabs(
            id="main-tabs",
            value="daily",
            style={
                "background": _SIDEBAR_BG,
                "borderBottom": f"1px solid {_BORDER}",
                "height": "44px",
            },
            children=[
                dcc.Tab(
                    label="Daily",
                    value="daily",
                    style=_TAB_STYLE,
                    selected_style=_TAB_SELECTED_STYLE,
                    children=_daily.layout(),
                ),
                dcc.Tab(
                    label="Backtest",
                    value="backtest",
                    style=_TAB_STYLE,
                    selected_style=_TAB_SELECTED_STYLE,
                    children=_backtest_layout(),
                ),
                dcc.Tab(
                    label="Maintenance",
                    value="maintenance",
                    style=_TAB_STYLE,
                    selected_style=_TAB_SELECTED_STYLE,
                    children=_maintenance.layout(),
                ),
            ],
        ),
    ],
)

# ── Callbacks ─────────────────────────────────────────────────────────────────


@callback(
    Output("strategy-dd", "options"),
    Output("strategy-dd", "value"),
    Input("_init", "data"),
)
def _init_strategies(_: Any) -> tuple[list[dict], Any]:
    with get_session() as session:
        names: list[str] = session.execute(
            select(TrainRun.strategy_name).distinct().order_by(TrainRun.strategy_name)
        ).scalars().all()
    if not names:
        return [], None
    options = [{"label": n, "value": n} for n in names]
    return options, options[0]["value"]


@callback(
    Output("run-dd", "options"),
    Output("run-dd", "value"),
    Input("strategy-dd", "value"),
)
def _load_runs(strategy: str | None) -> tuple[list[dict], Any]:
    if not strategy:
        return [], None
    with get_session() as session:
        stmt = (
            select(TrainRun)
            .where(TrainRun.strategy_name == strategy)
            .order_by(TrainRun.created_at.desc())
        )
        runs = session.execute(stmt).scalars().all()
        options = [
            {
                "label": (
                    f"{r.created_at.strftime('%Y-%m-%d %H:%M')}  |  "
                    f"{r.stock_code[:30]}{'…' if len(r.stock_code) > 30 else ''} "
                    f"{r.granularity}  |  {r.total_combinations} combos"
                ),
                "value": r.id,
            }
            for r in runs
        ]
    return options, (options[0]["value"] if options else None)


@callback(
    Output("params-tbl", "data"),
    Output("params-tbl", "columns"),
    Output("params-tbl", "selected_rows"),
    Output("run-store", "data"),
    Output("stock-dd", "options"),
    Output("stock-dd", "value"),
    Output("stock-dd", "style"),
    Output("stock-label", "style"),
    Input("run-dd", "value"),
)
def _load_params(run_id: int | None) -> tuple:
    _hidden = ([], _EMPTY_COLS, [0], None, [], None, {"display": "none"}, _S_LABEL_HIDDEN)
    if run_id is None:
        return _hidden

    with get_session() as session:
        run = session.get(TrainRun, run_id)
        if run is None:
            return _hidden

        stmt = (
            select(TrainBestResult)
            .where(TrainBestResult.train_run_id == run_id)
            .order_by(TrainBestResult.rank)
        )
        results_db = session.execute(stmt).scalars().all()
        if not results_db:
            return _hidden

        columns = _columns_from_params(results_db[0].params_json)
        rows    = [_result_to_row(r) for r in results_db]

        stocks = [s.strip() for s in run.stock_code.split(",") if s.strip()]
        multi  = len(stocks) > 1
        stock_options  = [{"label": s, "value": s} for s in stocks]
        stock_dd_style = {} if multi else {"display": "none"}
        stock_lbl_style = _S_LABEL if multi else _S_LABEL_HIDDEN

        run_meta: dict[str, Any] = {
            "run_id":          run_id,
            "strategy_name":   run.strategy_name,
            "stock_code":      run.stock_code,
            "granularity":     run.granularity,
            "start_dt":        run.start_dt.isoformat(),
            "end_dt":          run.end_dt.isoformat(),
            "initial_capital": run.initial_capital,
        }

    return (
        rows, columns, [0], run_meta,
        stock_options, stocks[0],
        stock_dd_style, stock_lbl_style,
    )


@callback(
    Output("main-chart", "figure"),
    Output("metrics-panel", "children"),
    Input("params-tbl", "selected_rows"),
    Input("stock-dd", "value"),
    State("params-tbl", "data"),
    State("run-store", "data"),
)
def _update_chart(
    selected_rows: list[int],
    selected_stock: str | None,
    table_data: list[dict],
    run_data: dict | None,
) -> tuple[go.Figure, list]:
    if not selected_rows or not table_data or run_data is None:
        return _empty_figure(), _no_data_panel()

    result_id = table_data[selected_rows[0]].get("result_id")
    if result_id is None:
        return _empty_figure(), _no_data_panel()

    # Resolve which single stock to chart
    stocks = [s.strip() for s in run_data["stock_code"].split(",") if s.strip()]
    stock  = selected_stock if selected_stock in stocks else stocks[0]

    with get_session() as session:
        result = session.get(TrainBestResult, result_id)
        if result is None:
            return _empty_figure(), _no_data_panel()

        start = datetime.datetime.fromisoformat(run_data["start_dt"])
        end   = datetime.datetime.fromisoformat(run_data["end_dt"])

        cache = DataCache(stock, run_data["granularity"])
        cache.load(session, start, end)
        if not cache.bars:
            return _empty_figure(), _no_data_panel()

        # Reconstruct typed params and re-run backtest for this stock
        from src.strategy.registry import get_by_name
        plugin = get_by_name(run_data["strategy_name"])
        units  = result.params_json.get("units", 100)
        params_cls = type(plugin.make_grid(units)[0])
        params = params_cls(**result.params_json)

        plugin.setup_cache_for_params(cache, params)
        strategy = plugin.make_strategy(params)
        sim = TradeSimulator(cache, run_data["initial_capital"])
        bt  = run_backtest(strategy, sim, cache)
        metrics = compute_metrics(bt, cache.gran)

        trades_json = [
            {
                "order_id":    t.order_id,
                "side":        int(t.side),
                "price":       t.price,
                "dt":          t.dt.isoformat(),
                "realized_pnl": t.realized_pnl,
            }
            for t in bt.trades
        ]

    fig   = _build_figure(
        result.params_json, cache, trades_json,
        bt.equity_curve, bt.bar_dts,
        run_data["initial_capital"], stock, metrics.score,
    )
    panel = _metrics_panel(metrics)
    return fig, panel


# ── Figure builder ────────────────────────────────────────────────────────────


def _build_figure(
    params_json: dict,
    cache: DataCache,
    trades_json: list[dict],
    equity_curve: list[float],
    bar_dts: list[Any],
    initial_capital: float,
    stock_code: str,
    score: float,
) -> go.Figure:
    bars   = cache.bars
    dates  = [b.dt.strftime("%Y-%m-%d") for b in bars]
    opens  = [b.open   for b in bars]
    highs  = [b.high   for b in bars]
    lows   = [b.low    for b in bars]
    closes = [b.close  for b in bars]
    vols   = [b.volume for b in bars]

    sma_period = params_json.get("sma_period", 20)
    sma_key    = f"SMA{sma_period}"
    sma_vals   = [b.indicators.get(sma_key) for b in bars]

    buys  = [t for t in trades_json if t["side"] == 1]
    sells = [t for t in trades_json if t["side"] == -1]

    def _d(iso: str) -> str:
        return datetime.datetime.fromisoformat(iso).strftime("%Y-%m-%d")

    buy_dates   = [_d(t["dt"]) for t in buys]
    buy_prices  = [t["price"]  for t in buys]
    sell_dates  = [_d(t["dt"]) for t in sells]
    sell_prices = [t["price"]  for t in sells]
    sell_pnls   = [t["realized_pnl"] for t in sells]

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.60, 0.22, 0.18],
        vertical_spacing=0.02,
    )

    # ── Candlestick ───────────────────────────────────────────────────────────
    fig.add_trace(
        go.Candlestick(
            x=dates, open=opens, high=highs, low=lows, close=closes,
            name="OHLCV",
            increasing_fillcolor="#26a69a", increasing_line_color="#26a69a",
            decreasing_fillcolor="#ef5350", decreasing_line_color="#ef5350",
            showlegend=False,
            hovertemplate=(
                "<b>%{x}</b><br>"
                "O: %{open:,.0f}  H: %{high:,.0f}<br>"
                "L: %{low:,.0f}  C: %{close:,.0f}<extra></extra>"
            ),
        ),
        row=1, col=1,
    )

    # ── SMA ───────────────────────────────────────────────────────────────────
    fig.add_trace(
        go.Scatter(
            x=dates, y=sma_vals, mode="lines", name=sma_key,
            line=dict(color="#ff9800", width=1.5),
            hovertemplate=f"{sma_key}: %{{y:,.0f}}<extra></extra>",
        ),
        row=1, col=1,
    )

    # ── Extra traces (Bollinger upper band, etc.) ─────────────────────────────
    for param_key, trace_fn in _EXTRA_TRACES.items():
        if param_key in params_json:
            fig.add_trace(trace_fn(params_json, dates, bars), row=1, col=1)

    # ── Position highlighting ─────────────────────────────────────────────────
    buys_by_id  = {t["order_id"]: t for t in buys}
    sells_by_id = {t["order_id"]: t for t in sells}
    for oid, buy_t in buys_by_id.items():
        sell_t = sells_by_id.get(oid)
        if sell_t:
            color = (
                "rgba(38,166,154,0.12)"
                if sell_t["realized_pnl"] > 0
                else "rgba(239,83,80,0.12)"
            )
            fig.add_vrect(
                x0=_d(buy_t["dt"]), x1=_d(sell_t["dt"]),
                fillcolor=color, layer="below", line_width=0,
                row=1, col=1,
            )

    # ── Buy / Sell markers ────────────────────────────────────────────────────
    if buy_dates:
        fig.add_trace(
            go.Scatter(
                x=buy_dates, y=buy_prices, mode="markers", name="Buy",
                marker=dict(symbol="triangle-up", size=14,
                            color="#00e676", line=dict(width=1, color="#fff")),
                hovertemplate="<b>Buy</b>  %{x}<br>Fill: %{y:,.0f}<extra></extra>",
            ),
            row=1, col=1,
        )
    if sell_dates:
        fig.add_trace(
            go.Scatter(
                x=sell_dates, y=sell_prices, mode="markers+text", name="Sell",
                marker=dict(symbol="triangle-down", size=14,
                            color="#ff5252", line=dict(width=1, color="#fff")),
                text=[f"{pnl:+,.0f}" for pnl in sell_pnls],
                textposition="top center",
                textfont=dict(size=10, color="#ffd700"),
                customdata=sell_pnls,
                hovertemplate=(
                    "<b>Sell</b>  %{x}<br>"
                    "Fill: %{y:,.0f}<br>"
                    "PnL: %{customdata:+,.0f}<extra></extra>"
                ),
            ),
            row=1, col=1,
        )

    # ── Equity curve ──────────────────────────────────────────────────────────
    eq_dates = [
        (d.strftime("%Y-%m-%d") if isinstance(d, datetime.datetime)
         else datetime.datetime.fromisoformat(d).strftime("%Y-%m-%d"))
        for d in bar_dts
    ]
    fig.add_trace(
        go.Scatter(
            x=eq_dates, y=equity_curve,
            mode="lines", name="Equity",
            line=dict(color="#58a6ff", width=1.5),
            fill="tozeroy", fillcolor="rgba(88,166,255,0.07)",
            hovertemplate="<b>Equity</b>  %{x}<br>%{y:,.0f}<extra></extra>",
        ),
        row=2, col=1,
    )
    fig.add_hline(y=initial_capital, line_dash="dot", line_color=_MUTED,
                  opacity=0.5, row=2, col=1)

    # ── Volume ────────────────────────────────────────────────────────────────
    vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(closes, opens)]
    fig.add_trace(
        go.Bar(x=dates, y=vols, name="Volume", marker_color=vol_colors,
               showlegend=False,
               hovertemplate="<b>Vol</b>  %{x}<br>%{y:,.0f}<extra></extra>"),
        row=3, col=1,
    )

    title = _params_title(params_json, stock_code, score)

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_BG, plot_bgcolor="#0d1117",
        margin=dict(l=60, r=20, t=40, b=20),
        title=dict(text=title, font=dict(size=13, color=_MUTED), x=0.01),
        legend=dict(orientation="h", yanchor="bottom", y=1.01,
                    xanchor="right", x=1, font=dict(size=11),
                    bgcolor="rgba(0,0,0,0)"),
        dragmode="pan",
        hovermode="x unified",
        yaxis_title="Price", yaxis2_title="Equity", yaxis3_title="Vol",
        yaxis_tickformat=",.0f", yaxis2_tickformat=",.0f", yaxis3_tickformat=".2s",
    )
    fig.update_xaxes(type="category")
    n = len(dates)
    ticks = dates[:: max(1, n // 24)]
    fig.update_xaxes(tickvals=ticks, ticktext=ticks, tickangle=-30, tickfont=dict(size=10))
    fig.update_xaxes(rangeslider_visible=False)
    fig.update_xaxes(
        rangeslider_visible=True,
        rangeslider=dict(thickness=0.05, bgcolor=_CARD_BG, bordercolor=_BORDER),
        row=3, col=1,
    )
    return fig


def _params_title(params_json: dict, stock_code: str, score: float) -> str:
    parts: list[str] = []
    for key in _PARAM_ORDER:
        if key not in params_json:
            continue
        val = params_json[key]
        label = _PARAM_DISPLAY.get(key, key)
        parts.append(f"{label}={val:.0%}" if key in _PCT_PARAMS else f"{label}={val}")
    return f"{stock_code}  —  " + "  ".join(parts) + f"   ·   Score {score:.3f}"


# ── UI helpers ────────────────────────────────────────────────────────────────


def _empty_figure() -> go.Figure:
    return _base_empty_figure("Select a training run and parameter set")


def _no_data_panel() -> list:
    return [html.Span("No parameter set selected.",
                      style={"color": _MUTED, "fontSize": "12px"})]


def _metrics_panel(metrics: BacktestMetrics) -> list:
    pf = "∞" if metrics.profit_factor == float("inf") else f"{metrics.profit_factor:.2f}"
    ret_color = _GREEN if metrics.total_return_pct >= 0 else _RED

    def _row(label: str, value: str, color: str | None = None) -> html.Div:
        return html.Div(style=_S_METRIC_ROW, children=[
            html.Span(label, style={"color": _MUTED}),
            html.Span(value, style={"color": color or _TEXT, "fontWeight": "500"}),
        ])

    return [
        html.Div("Performance",
                 style={"color": _ACCENT, "fontWeight": "600",
                        "marginBottom": "6px", "fontSize": "12px"}),
        _row("Total Return",  f"{metrics.total_return_pct:+.2f}%",  ret_color),
        _row("CAGR",          f"{metrics.annualized_return_pct:+.2f}%"),
        _row("Sharpe Ratio",  f"{metrics.sharpe_ratio:.3f}"),
        _row("Max Drawdown",  f"{metrics.max_drawdown_pct:.2f}%",
             _RED if metrics.max_drawdown_pct < -5 else None),
        _row("Win Rate",      f"{metrics.win_rate_pct:.1f}%",
             _GREEN if metrics.win_rate_pct >= 50 else _RED),
        _row("Profit Factor", pf),
        _row("Total Trades",  str(metrics.total_trades)),
        _row("Avg Holding",   f"{metrics.avg_holding_days:.1f} days"),
        html.Div(
            style={**_S_METRIC_ROW, "borderBottom": "none", "marginTop": "6px"},
            children=[
                html.Span("Score", style={"color": _ACCENT, "fontWeight": "600"}),
                html.Span(f"{metrics.score:.3f}",
                          style={"color": _ACCENT, "fontWeight": "700", "fontSize": "15px"}),
            ],
        ),
    ]


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    import sys
    _daily.register_callbacks()
    _maintenance.register_callbacks()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8050
    print(f"Starting Trade Advisor at http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
