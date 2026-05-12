"""chart_view — stand-alone stock chart viewer (Analysis › Chart sub-tab).

Sidebar : stock selector + date range + indicator toggles.
Chart   : 4- or 5-row layout (stock / [ADX] / volume / N225 / ρ(20)) with
          per-indicator visibility controlled by the Indicators checklist.
"""

from __future__ import annotations

import datetime
import math
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html
from loguru import logger
from plotly.subplots import make_subplots
from sqlalchemy import select
from ta.trend import ADXIndicator

from src.data.db import get_session
from src.data.models import Stock
from src.indicators.ichimoku import calc_ichimoku
from src.indicators.moving_corr import compute_moving_corr
from src.indicators.zigzag import detect_peaks
from src.simulator.cache import DataCache
from src.viz.palette import ACCENT, BG, BORDER, CARD_BG, GREEN, MUTED, RED, SIDEBAR_BG, TEXT

# ── Style constants ────────────────────────────────────────────────────────────

_S_SIDEBAR: dict[str, Any] = {
    "width": "280px", "minWidth": "280px",
    "height": "100%", "overflowY": "auto",
    "background": SIDEBAR_BG,
    "borderRight": f"1px solid {BORDER}",
    "padding": "16px", "boxSizing": "border-box",
}
_S_MAIN: dict[str, Any] = {
    "flex": "1", "height": "100%", "overflow": "hidden", "background": BG,
}
_S_LABEL: dict[str, Any] = {
    "color": MUTED, "fontSize": "11px",
    "textTransform": "uppercase", "letterSpacing": "0.5px",
    "marginTop": "14px", "marginBottom": "4px", "display": "block",
}
_S_BUTTON: dict[str, Any] = {
    "background": ACCENT, "color": BG,
    "border": "none", "borderRadius": "4px",
    "padding": "7px 14px", "marginTop": "16px",
    "fontSize": "13px", "fontWeight": "600",
    "cursor": "pointer", "width": "100%",
}

# ── Indicator option keys (used in the Checklist values) ───────────────────────

_OPT_SMA25     = "sma25"
_OPT_SMA75     = "sma75"
_OPT_SMA200    = "sma200"
_OPT_ICHIMOKU  = "ichimoku"
_OPT_ZIGZAG    = "zigzag"
_OPT_ADX_PANEL = "adx_panel"

_DEFAULT_OPTS: list[str] = []   # all indicators off until the user enables them

_GRAN          = "1d"
_WARMUP_DAYS   = 250    # enough for SMA200 + Ichimoku + ZigZag warmup
_CORR_WINDOW   = 20

# ── Layout ─────────────────────────────────────────────────────────────────────


def layout() -> html.Div:
    """Return the Chart sub-tab content (sidebar + chart area)."""
    today = datetime.date.today()
    default_start = today - datetime.timedelta(days=365)

    return html.Div(
        style={
            "display": "flex",
            "height": "100%",
            "overflow": "hidden",
            "fontFamily": "'Segoe UI', Arial, sans-serif",
            "background": BG,
        },
        children=[
            dcc.Store(id="cv-init", data=True),

            # ── Sidebar ───────────────────────────────────────────────────────
            html.Div(style=_S_SIDEBAR, children=[
                html.H4("Chart Viewer",
                        style={"color": ACCENT, "margin": "0 0 10px 0", "fontSize": "15px"}),

                html.Span("Stock", style=_S_LABEL),
                dcc.Dropdown(
                    id="cv-stock-dd",
                    options=[],
                    value=None,
                    placeholder="Search stock code or name…",
                    clearable=False,
                    optionHeight=42,
                ),

                html.Span("Date Range", style=_S_LABEL),
                dcc.DatePickerRange(
                    id="cv-date-range",
                    start_date=default_start,
                    end_date=today,
                    display_format="YYYY-MM-DD",
                    style={"fontSize": "12px"},
                ),

                html.Span("Indicators", style=_S_LABEL),
                dcc.Checklist(
                    id="cv-indicators",
                    options=[
                        {"label": " SMA 25",   "value": _OPT_SMA25},
                        {"label": " SMA 75",   "value": _OPT_SMA75},
                        {"label": " SMA 200",  "value": _OPT_SMA200},
                        {"label": " Ichimoku", "value": _OPT_ICHIMOKU},
                        {"label": " ZigZag",   "value": _OPT_ZIGZAG},
                        {"label": " ADX panel","value": _OPT_ADX_PANEL},
                    ],
                    value=_DEFAULT_OPTS,
                    style={"color": "#ffffff", "fontSize": "13px", "lineHeight": "1.9",
                           "fontWeight": "500"},
                    labelStyle={"color": "#ffffff", "display": "block"},
                    inputStyle={"marginRight": "6px"},
                ),

                html.Button("Show", id="cv-show-btn", n_clicks=0, style=_S_BUTTON),
            ]),

            # ── Chart ─────────────────────────────────────────────────────────
            html.Div(style=_S_MAIN, children=[
                dcc.Loading(
                    id="cv-loading", type="circle", color=ACCENT,
                    parent_style={"height": "100%"},
                    style={"height": "100%"},
                    children=[
                        dcc.Graph(
                            id="cv-chart",
                            figure=_empty_chart("Pick a stock and click Show."),
                            style={"height": "100%"},
                            config={
                                "scrollZoom": True,
                                "displayModeBar": True,
                                "modeBarButtonsToRemove":
                                    ["autoScale2d", "lasso2d", "select2d"],
                            },
                        ),
                    ],
                ),
            ]),
        ],
    )


# ── Callbacks ──────────────────────────────────────────────────────────────────


def register_callbacks() -> None:
    """Register Dash callbacks for the Chart Viewer sub-tab."""

    @callback(
        Output("cv-stock-dd", "options"),
        Output("cv-stock-dd", "value"),
        Input("cv-init", "data"),
    )
    def _load_stocks(_: Any) -> tuple[list[dict], Any]:
        with get_session() as s:
            rows = s.execute(
                select(Stock.code, Stock.name)
                .where(Stock.is_active.is_(True))
                .order_by(Stock.code)
            ).all()
        opts = [{"label": f"{c}  {n}", "value": c} for c, n in rows]
        # Always include common indices for convenience.
        for code in ("^N225", "^GSPC"):
            if not any(o["value"] == code for o in opts):
                opts.insert(0, {"label": code, "value": code})
        return opts, (opts[0]["value"] if opts else None)

    @callback(
        Output("cv-chart", "figure"),
        Input("cv-show-btn", "n_clicks"),
        Input("cv-indicators", "value"),
        State("cv-stock-dd",  "value"),
        State("cv-date-range", "start_date"),
        State("cv-date-range", "end_date"),
        prevent_initial_call=True,
    )
    def _show(n_clicks: int, indicators: list[str] | None,
              stock: str | None,
              start_date: str | None, end_date: str | None) -> go.Figure:
        if not stock or not start_date or not end_date:
            return _empty_chart("Pick a stock and date range, then click Show.")
        opts = set(indicators or [])
        try:
            start = datetime.datetime.fromisoformat(start_date[:10])
            end   = datetime.datetime.fromisoformat(end_date[:10])
        except ValueError:
            return _empty_chart("Invalid date range.")
        if end < start:
            return _empty_chart("End date is before start date.")
        return _build_chart(stock, start, end, opts)


# ── Chart builder ──────────────────────────────────────────────────────────────


def _build_chart(
    stock_code: str,
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    opts: set[str],
) -> go.Figure:
    try:
        load_start = start_dt - datetime.timedelta(days=_WARMUP_DAYS)

        with get_session() as session:
            stock_cache = DataCache(stock_code, _GRAN)
            stock_cache.load(session, load_start, end_dt)
            n225_cache  = DataCache("^N225", _GRAN)
            n225_cache.load(session, load_start, end_dt)
            gspc_cache  = DataCache("^GSPC", _GRAN)
            gspc_cache.load(session, load_start, end_dt)

            for period in (25, 75, 200):
                stock_cache.add_sma(period)
                n225_cache.add_sma(period)

        if not stock_cache.bars:
            return _empty_chart(f"No data for {stock_code} in the selected range.")
        if not n225_cache.bars:
            return _empty_chart("No ^N225 data — run Update on the Daily tab.")

        d0 = start_dt.date()
        d1 = end_dt.date()

        stock_all_bars = stock_cache.bars
        n225_all_bars  = n225_cache.bars
        stock_bars     = [b for b in stock_all_bars if d0 <= b.dt.date() <= d1]
        n225_bars      = [b for b in n225_all_bars  if d0 <= b.dt.date() <= d1]
        if not stock_bars:
            return _empty_chart(f"No bars in selected range for {stock_code}.")

        # Union date axis across stock + N225
        dates = sorted(
            {b.dt.strftime("%Y-%m-%d") for b in stock_bars}
            | {b.dt.strftime("%Y-%m-%d") for b in n225_bars}
        )
        n     = len(dates)
        ticks = _monthly_ticks(dates)

        # ── Helpers ───────────────────────────────────────────────────────────

        def _ohlcv(bars_slice: list) -> tuple:
            m = {b.dt.strftime("%Y-%m-%d"): b for b in bars_slice}
            def col(getter):
                return [getter(m[d]) if d in m else None for d in dates]
            return (
                col(lambda b: b.open),
                col(lambda b: b.high),
                col(lambda b: b.low),
                col(lambda b: b.close),
                col(lambda b: b.volume),
                col(lambda b: b.indicators.get("SMA25")  or None),
                col(lambda b: b.indicators.get("SMA75")  or None),
                col(lambda b: b.indicators.get("SMA200") or None),
            )

        def _clean(v: float | None) -> float | None:
            return None if v is None or (isinstance(v, float) and math.isnan(v)) else v

        def _ichi_map(all_bars: list) -> dict[str, tuple]:
            hi  = [b.high  for b in all_bars]
            lo  = [b.low   for b in all_bars]
            cl  = [b.close for b in all_bars]
            raw = calc_ichimoku(hi, lo, cl)
            d   = raw["displacement"]
            out: dict[str, tuple] = {}
            for i, dt_str in enumerate(b.dt.strftime("%Y-%m-%d") for b in all_bars):
                ai = i - d
                out[dt_str] = (
                    _clean(raw["senkou_a"][ai] if ai >= 0 else None),
                    _clean(raw["senkou_b"][ai] if ai >= 0 else None),
                    _clean(raw["tenkan"][i]),
                    _clean(raw["kijun"][i]),
                )
            return out

        def _zz_maps(bars_slice: list) -> tuple[dict, dict]:
            hi = [b.high for b in bars_slice]
            lo = [b.low  for b in bars_slice]
            ds = [b.dt.strftime("%Y-%m-%d") for b in bars_slice]
            pk = detect_peaks(hi, lo, size=5, middle_size=2)
            return (
                {ds[p.bar_index]: hi[p.bar_index] for p in pk if p.direction  ==  2},
                {ds[p.bar_index]: lo[p.bar_index] for p in pk if p.direction  == -2},
            )

        def _add_cloud(fig: go.Figure, ca: list, cb: list, row: int) -> None:
            upper = [max(a, b) if a is not None and b is not None else None for a, b in zip(ca, cb)]
            lower = [min(a, b) if a is not None and b is not None else None for a, b in zip(ca, cb)]
            bull  = sum(1 for a, b in zip(ca, cb) if a is not None and b is not None and a > b)
            fc    = "rgba(38,166,154,0.12)" if bull >= n // 2 else "rgba(239,83,80,0.12)"
            fig.add_trace(go.Scatter(x=dates, y=upper, mode="lines", line=dict(width=0),
                                     showlegend=False, hoverinfo="skip"), row=row, col=1)
            fig.add_trace(go.Scatter(x=dates, y=lower, mode="lines", line=dict(width=0),
                                     fill="tonexty", fillcolor=fc, name="Kumo",
                                     showlegend=(row == 1), hoverinfo="skip"), row=row, col=1)

        # ── Stock series ──────────────────────────────────────────────────────
        s_o, s_h, s_l, s_c, s_v, s_sma25, s_sma75, s_sma200 = _ohlcv(stock_bars)

        if _OPT_ZIGZAG in opts:
            s_zz_hi, s_zz_lo = _zz_maps(stock_bars)
            s_conf_hi = [(d, s_zz_hi[d]) for d in dates if d in s_zz_hi]
            s_conf_lo = [(d, s_zz_lo[d]) for d in dates if d in s_zz_lo]
        else:
            s_conf_hi = s_conf_lo = []

        if _OPT_ICHIMOKU in opts:
            s_ichi = _ichi_map(stock_all_bars)
            s_ca   = [s_ichi.get(d, (None,) * 4)[0] for d in dates]
            s_cb   = [s_ichi.get(d, (None,) * 4)[1] for d in dates]
            s_tk   = [s_ichi.get(d, (None,) * 4)[2] for d in dates]
            s_kj   = [s_ichi.get(d, (None,) * 4)[3] for d in dates]
        else:
            s_ca = s_cb = s_tk = s_kj = None  # type: ignore[assignment]

        # ── ADX (only computed when panel is enabled) ─────────────────────────
        if _OPT_ADX_PANEL in opts:
            ds_stock = [b.dt.strftime("%Y-%m-%d") for b in stock_bars]
            adx     = ADXIndicator(
                high=pd.Series([b.high  for b in stock_bars], dtype=float),
                low=pd.Series([b.low   for b in stock_bars], dtype=float),
                close=pd.Series([b.close for b in stock_bars], dtype=float),
                window=14,
            )
            adx_m = {d: (None if pd.isna(v) else float(v)) for d, v in zip(ds_stock, adx.adx())}
            dip_m = {d: (None if pd.isna(v) else float(v)) for d, v in zip(ds_stock, adx.adx_pos())}
            din_m = {d: (None if pd.isna(v) else float(v)) for d, v in zip(ds_stock, adx.adx_neg())}
            s_adx_v = [adx_m.get(d) for d in dates]
            s_dip_v = [dip_m.get(d) for d in dates]
            s_din_v = [din_m.get(d) for d in dates]
        else:
            s_adx_v = s_dip_v = s_din_v = None  # type: ignore[assignment]

        # ── N225 series (price always shown; indicators follow checkboxes) ────
        n225_o, n225_h, n225_l, n225_c, n225_v, n225_sma25, n225_sma75, n225_sma200 \
            = _ohlcv(n225_bars)

        if _OPT_ZIGZAG in opts:
            n225_zz_hi, n225_zz_lo = _zz_maps(n225_bars)
            n225_conf_hi = [(d, n225_zz_hi[d]) for d in dates if d in n225_zz_hi]
            n225_conf_lo = [(d, n225_zz_lo[d]) for d in dates if d in n225_zz_lo]
        else:
            n225_conf_hi = n225_conf_lo = []

        if _OPT_ICHIMOKU in opts:
            n225_ichi = _ichi_map(n225_all_bars)
            n225_ca   = [n225_ichi.get(d, (None,) * 4)[0] for d in dates]
            n225_cb   = [n225_ichi.get(d, (None,) * 4)[1] for d in dates]
            n225_tk   = [n225_ichi.get(d, (None,) * 4)[2] for d in dates]
            n225_kj   = [n225_ichi.get(d, (None,) * 4)[3] for d in dates]
        else:
            n225_ca = n225_cb = n225_tk = n225_kj = None  # type: ignore[assignment]

        first_n = next((v for v in n225_c if v is not None), None)
        first_s = next((v for v in s_c    if v is not None), None)
        n225_norm = (
            [v * (first_s / first_n) if v is not None else None for v in n225_c]
            if first_n and first_s else [None] * n
        )

        # ── Rolling correlation (always shown) ────────────────────────────────
        s_ser   = pd.Series({b.dt.date(): b.close for b in stock_bars})
        ind_map: dict[str, pd.Series] = {
            "^N225": pd.Series({b.dt.date(): b.close for b in n225_bars}),
        }
        gspc_bars = [b for b in gspc_cache.bars if d0 <= b.dt.date() <= d1]
        if gspc_bars:
            ind_map["^GSPC"] = pd.Series({b.dt.date(): b.close for b in gspc_bars})
        corr = compute_moving_corr(s_ser, ind_map, window=_CORR_WINDOW)

        def _corr_series(key: str) -> list[float | None]:
            ser = corr.get(key)
            if ser is None:
                return [None] * n
            dm = ser.to_dict()
            return [
                (None if pd.isna(v := dm.get(datetime.date.fromisoformat(d))) else float(v))
                for d in dates
            ]

        n225_corr_v = _corr_series("^N225")
        gspc_corr_v = _corr_series("^GSPC")

        # ── Decide row layout ─────────────────────────────────────────────────
        # Order: stock / [adx] / volume / n225 / rho
        rows_layout: list[str] = ["price"]
        if _OPT_ADX_PANEL in opts:
            rows_layout.append("adx")
        rows_layout += ["vol", "n225", "rho"]

        row_heights_5 = [0.37, 0.10, 0.08, 0.31, 0.14]
        row_heights_4 = [0.42, 0.10, 0.34, 0.14]
        row_heights = row_heights_5 if "adx" in rows_layout else row_heights_4
        row_index = {label: i + 1 for i, label in enumerate(rows_layout)}

        fig = make_subplots(
            rows=len(rows_layout), cols=1, shared_xaxes=True,
            row_heights=row_heights, vertical_spacing=0.010,
        )

        def _add_price_row(
            row: int, name: str, *,
            o: list, h: list, l: list, c: list,
            sma25: list, sma75: list, sma200: list,
            ca: list | None, cb: list | None,
            tk: list | None, kj: list | None,
            conf_hi: list, conf_lo: list,
            overlay_norm: list | None = None,
            show_legend: bool = True,
        ) -> None:
            if _OPT_ICHIMOKU in opts:
                _add_cloud(fig, ca, cb, row=row)
                fig.add_trace(go.Scatter(x=dates, y=tk, mode="lines",
                                         name=f"{name} Tenkan", showlegend=show_legend,
                                         line=dict(color="#ef5350", width=1, dash="dot"),
                                         hovertemplate="Tenkan: %{y:,.0f}<extra></extra>"),
                              row=row, col=1)
                fig.add_trace(go.Scatter(x=dates, y=kj, mode="lines",
                                         name=f"{name} Kijun", showlegend=show_legend,
                                         line=dict(color="#42a5f5", width=1.2),
                                         hovertemplate="Kijun: %{y:,.0f}<extra></extra>"),
                              row=row, col=1)
            if overlay_norm is not None:
                fig.add_trace(go.Scatter(x=dates, y=overlay_norm, mode="lines",
                                         name="^N225 (norm)", showlegend=show_legend,
                                         line=dict(color="#78909c", width=1.2, dash="dash"),
                                         opacity=0.7,
                                         hovertemplate="^N225 (norm): %{y:,.0f}<extra></extra>"),
                              row=row, col=1)
            fig.add_trace(go.Candlestick(
                x=dates, open=o, high=h, low=l, close=c,
                name=name,
                increasing_fillcolor="#26a69a", increasing_line_color="#26a69a",
                decreasing_fillcolor="#ef5350", decreasing_line_color="#ef5350",
                showlegend=False,
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "O:%{open:,.0f} H:%{high:,.0f} L:%{low:,.0f} C:%{close:,.0f}"
                    "<extra></extra>"
                ),
            ), row=row, col=1)
            if _OPT_SMA25 in opts:
                fig.add_trace(go.Scatter(x=dates, y=sma25, mode="lines",
                                         name=f"{name} SMA25", showlegend=show_legend,
                                         line=dict(color="#ff9800", width=1.2),
                                         hovertemplate="SMA25: %{y:,.0f}<extra></extra>"),
                              row=row, col=1)
            if _OPT_SMA75 in opts:
                fig.add_trace(go.Scatter(x=dates, y=sma75, mode="lines",
                                         name=f"{name} SMA75", showlegend=show_legend,
                                         line=dict(color="#ab47bc", width=1.2),
                                         hovertemplate="SMA75: %{y:,.0f}<extra></extra>"),
                              row=row, col=1)
            if _OPT_SMA200 in opts:
                fig.add_trace(go.Scatter(x=dates, y=sma200, mode="lines",
                                         name=f"{name} SMA200", showlegend=show_legend,
                                         line=dict(color="#26c6da", width=1.2),
                                         hovertemplate="SMA200: %{y:,.0f}<extra></extra>"),
                              row=row, col=1)
            if conf_hi:
                hx, hy = zip(*conf_hi)
                fig.add_trace(go.Scatter(x=list(hx), y=list(hy), mode="markers",
                                         name=f"{name} ZZ high", showlegend=show_legend,
                                         marker=dict(symbol="triangle-down", size=9,
                                                     color="#ef5350",
                                                     line=dict(width=1, color="#fff")),
                                         hovertemplate="ZZ high: %{y:,.0f}<extra></extra>"),
                              row=row, col=1)
            if conf_lo:
                lx, ly = zip(*conf_lo)
                fig.add_trace(go.Scatter(x=list(lx), y=list(ly), mode="markers",
                                         name=f"{name} ZZ low", showlegend=show_legend,
                                         marker=dict(symbol="triangle-up", size=9,
                                                     color="#26a69a",
                                                     line=dict(width=1, color="#fff")),
                                         hovertemplate="ZZ low: %{y:,.0f}<extra></extra>"),
                              row=row, col=1)

        # ── Row: stock price ──────────────────────────────────────────────────
        r_price = row_index["price"]
        _add_price_row(
            r_price, stock_code,
            o=s_o, h=s_h, l=s_l, c=s_c,
            sma25=s_sma25, sma75=s_sma75, sma200=s_sma200,
            ca=s_ca, cb=s_cb, tk=s_tk, kj=s_kj,
            conf_hi=s_conf_hi, conf_lo=s_conf_lo,
            overlay_norm=n225_norm,
            show_legend=True,
        )

        # ── Row: ADX ──────────────────────────────────────────────────────────
        if "adx" in rows_layout:
            r_adx = row_index["adx"]
            fig.add_trace(go.Scatter(x=dates, y=s_adx_v, mode="lines", name="ADX",
                                     line=dict(color="#eceff1", width=1.4),
                                     hovertemplate="ADX: %{y:.1f}<extra></extra>"),
                          row=r_adx, col=1)
            fig.add_trace(go.Scatter(x=dates, y=s_dip_v, mode="lines", name="+DI",
                                     line=dict(color=GREEN, width=1),
                                     hovertemplate="+DI: %{y:.1f}<extra></extra>"),
                          row=r_adx, col=1)
            fig.add_trace(go.Scatter(x=dates, y=s_din_v, mode="lines", name="−DI",
                                     line=dict(color=RED, width=1),
                                     hovertemplate="−DI: %{y:.1f}<extra></extra>"),
                          row=r_adx, col=1)
            fig.add_hline(y=20, line_dash="dot", line_color=MUTED, opacity=0.5,
                          row=r_adx, col=1)

        # ── Row: volume ───────────────────────────────────────────────────────
        r_vol = row_index["vol"]
        vcol = ["#26a69a" if (c or 0) >= (o or 0) else "#ef5350" for c, o in zip(s_c, s_o)]
        fig.add_trace(go.Bar(x=dates, y=s_v, name="Volume", marker_color=vcol,
                             showlegend=False,
                             hovertemplate="<b>Vol</b>  %{x}<br>%{y:,.0f}<extra></extra>"),
                      row=r_vol, col=1)

        # ── Row: N225 ─────────────────────────────────────────────────────────
        r_n225 = row_index["n225"]
        _add_price_row(
            r_n225, "^N225",
            o=n225_o, h=n225_h, l=n225_l, c=n225_c,
            sma25=n225_sma25, sma75=n225_sma75, sma200=n225_sma200,
            ca=n225_ca, cb=n225_cb, tk=n225_tk, kj=n225_kj,
            conf_hi=n225_conf_hi, conf_lo=n225_conf_lo,
            show_legend=False,
        )

        # ── Row: rolling correlation ─────────────────────────────────────────
        r_rho = row_index["rho"]
        fig.add_trace(go.Scatter(x=dates, y=n225_corr_v, mode="lines", name="ρ ^N225",
                                 line=dict(color="#ff9800", width=1.4),
                                 hovertemplate="ρ ^N225: %{y:.2f}<extra></extra>"),
                      row=r_rho, col=1)
        if gspc_bars:
            fig.add_trace(go.Scatter(x=dates, y=gspc_corr_v, mode="lines", name="ρ ^GSPC",
                                     line=dict(color="#29b6f6", width=1.4),
                                     hovertemplate="ρ ^GSPC: %{y:.2f}<extra></extra>"),
                          row=r_rho, col=1)
        for lvl, col, dash, lw in (
            ( 0.6, "rgba(38,166,154,0.4)",   "dot",   1.0),
            ( 0.0, "rgba(180,180,180,0.85)", "solid", 2.0),
            (-0.6, "rgba(239,83,80,0.4)",    "dot",   1.0),
        ):
            fig.add_hline(y=lvl, line_dash=dash, line_color=col, line_width=lw,
                          opacity=1.0, row=r_rho, col=1)

        # ── Layout & axes ─────────────────────────────────────────────────────
        title = (
            f"{stock_code}  ·  {start_dt.strftime('%Y-%m-%d')} – "
            f"{end_dt.strftime('%Y-%m-%d')}  ({len(stock_bars)} bars)"
        )
        # Per-row y-axis titles, generated dynamically from rows_layout
        axis_titles = {
            "price": "Price",
            "adx":   "ADX",
            "vol":   "Vol",
            "n225":  "^N225",
            "rho":   f"ρ({_CORR_WINDOW})",
        }
        layout_kwargs: dict[str, Any] = dict(
            template="plotly_dark", paper_bgcolor=BG, plot_bgcolor="#0d1117",
            margin=dict(l=60, r=20, t=36, b=10),
            title=dict(text=title, font=dict(size=12, color=MUTED), x=0.01),
            legend=dict(orientation="h", yanchor="bottom", y=1.01,
                        xanchor="right", x=1, font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
            dragmode="pan", hovermode="x unified",
        )
        for label, idx in row_index.items():
            key = "yaxis_title" if idx == 1 else f"yaxis{idx}_title"
            layout_kwargs[key] = axis_titles[label]
            if label in ("price", "n225"):
                tick_key = "yaxis_tickformat" if idx == 1 else f"yaxis{idx}_tickformat"
                layout_kwargs[tick_key] = ",.0f"

        fig.update_layout(**layout_kwargs)
        fig.update_xaxes(type="category")
        fig.update_xaxes(tickvals=ticks, ticktext=ticks, tickangle=-30, tickfont=dict(size=9))
        fig.update_xaxes(rangeslider_visible=False)
        fig.update_xaxes(
            rangeslider_visible=True,
            rangeslider=dict(thickness=0.03, bgcolor=CARD_BG, bordercolor=BORDER),
            row=len(rows_layout), col=1,
        )
        return fig

    except Exception:
        logger.exception("chart_view error for %s", stock_code)
        return _empty_chart("Chart error — check logs")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _monthly_ticks(dates: list[str]) -> list[str]:
    seen: set[str] = set()
    ticks: list[str] = []
    for d in dates:
        ym = d[:7]
        if ym not in seen:
            seen.add(ym)
            ticks.append(d)
    return ticks


def _empty_chart(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=BG, plot_bgcolor="#0d1117",
        xaxis_visible=False, yaxis_visible=False,
        annotations=[dict(
            text=msg, x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=14, color=MUTED),
        )],
    )
    return fig
