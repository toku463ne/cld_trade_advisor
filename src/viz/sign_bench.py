"""sign_bench — Sign Benchmark event viewer page component.

Embedded as a sub-tab of the Backtest page.

Dropdowns:  Sign → Stock Set → Run (SignBenchmarkRun) → Stock
Chart:      Daily-style 5-row chart covering the full run period, with each
            SignBenchmarkEvent overlaid as a vertical dashed line coloured by
            outcome: green (+1), red (−1), grey (None).
"""

from __future__ import annotations

import datetime
import math
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html
from loguru import logger
from plotly.subplots import make_subplots
from sqlalchemy import func, select
from ta.trend import ADXIndicator

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.indicators.ichimoku import calc_ichimoku
from src.indicators.moving_corr import compute_moving_corr
from src.indicators.zigzag import detect_peaks
from src.simulator.cache import DataCache
from src.viz.daily import SIGN_DESCRIPTIONS
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
_S_CARD: dict[str, Any] = {
    "background": CARD_BG, "border": f"1px solid {BORDER}",
    "borderRadius": "6px", "padding": "10px", "marginTop": "14px",
    "color": TEXT, "fontSize": "12px",
}
_S_STAT_ROW: dict[str, Any] = {
    "display": "flex", "justifyContent": "space-between",
    "borderBottom": f"1px solid {BORDER}", "padding": "3px 0",
}

# ── Layout ─────────────────────────────────────────────────────────────────────


def layout() -> html.Div:
    """Return the Sign Benchmark sub-tab content (sidebar + chart)."""
    return html.Div(
        style={
            "display": "flex",
            "height": "100%",
            "overflow": "hidden",
            "fontFamily": "'Segoe UI', Arial, sans-serif",
            "background": BG,
        },
        children=[
            dcc.Store(id="sb-init", data=True),

            # ── Sidebar ───────────────────────────────────────────────────────
            html.Div(style=_S_SIDEBAR, children=[
                html.H4("Sign Benchmark",
                        style={"color": ACCENT, "margin": "0 0 10px 0", "fontSize": "15px"}),

                html.Span("Sign", style=_S_LABEL),
                dcc.Dropdown(id="sb-sign-dd", options=[], value=None, clearable=False),
                dcc.Markdown(id="sb-sign-desc", style={
                    **_S_CARD,
                    "fontSize": "11px", "lineHeight": "1.5",
                    "marginTop": "8px",
                }),

                html.Span("Stock Set", style=_S_LABEL),
                dcc.Dropdown(id="sb-set-dd", options=[], value=None, clearable=False),

                html.Span("Run", style=_S_LABEL),
                dcc.Dropdown(id="sb-run-dd", options=[], value=None, clearable=False),

                html.Span("Stock", style=_S_LABEL),
                dcc.Dropdown(id="sb-stock-dd", options=[], value=None, clearable=False),

                html.Div(id="sb-stats", style=_S_CARD,
                         children=[html.Span("Select a run.", style={"color": MUTED})]),
            ]),

            # ── Chart ─────────────────────────────────────────────────────────
            html.Div(style=_S_MAIN, children=[
                dcc.Graph(
                    id="sb-chart",
                    style={"height": "100%"},
                    config={
                        "scrollZoom": True,
                        "displayModeBar": True,
                        "modeBarButtonsToRemove": ["autoScale2d", "lasso2d", "select2d"],
                    },
                ),
            ]),
        ],
    )


# ── Callbacks ──────────────────────────────────────────────────────────────────


def register_callbacks() -> None:
    """Register all Dash callbacks for the Sign Benchmark page."""

    @callback(
        Output("sb-sign-dd", "options"),
        Output("sb-sign-dd", "value"),
        Input("sb-init", "data"),
    )
    def _load_signs(_: Any) -> tuple[list[dict], Any]:
        with get_session() as s:
            signs: list[str] = s.execute(
                select(SignBenchmarkRun.sign_type)
                .distinct()
                .order_by(SignBenchmarkRun.sign_type)
            ).scalars().all()
        if not signs:
            return [], None
        opts = [{"label": sg, "value": sg} for sg in signs]
        return opts, opts[0]["value"]

    @callback(
        Output("sb-sign-desc", "children"),
        Input("sb-sign-dd", "value"),
    )
    def _update_sign_desc(sign: str | None) -> str:
        if not sign:
            return ""
        return SIGN_DESCRIPTIONS.get(sign, f"*No description available for `{sign}`.*")

    @callback(
        Output("sb-set-dd", "options"),
        Output("sb-set-dd", "value"),
        Input("sb-sign-dd", "value"),
    )
    def _load_sets(sign: str | None) -> tuple[list[dict], Any]:
        if not sign:
            return [], None
        with get_session() as s:
            sets: list[str] = s.execute(
                select(SignBenchmarkRun.stock_set)
                .where(SignBenchmarkRun.sign_type == sign)
                .distinct()
                .order_by(SignBenchmarkRun.stock_set)
            ).scalars().all()
        opts = [{"label": ss, "value": ss} for ss in sets]
        return opts, (opts[-1]["value"] if opts else None)   # default: latest set

    @callback(
        Output("sb-run-dd", "options"),
        Output("sb-run-dd", "value"),
        Input("sb-sign-dd", "value"),
        Input("sb-set-dd",  "value"),
    )
    def _load_runs(sign: str | None, stock_set: str | None) -> tuple[list[dict], Any]:
        if not sign or not stock_set:
            return [], None
        with get_session() as s:
            runs = s.execute(
                select(SignBenchmarkRun)
                .where(SignBenchmarkRun.sign_type  == sign)
                .where(SignBenchmarkRun.stock_set  == stock_set)
                .order_by(SignBenchmarkRun.start_dt)
            ).scalars().all()
            opts = []
            for r in runs:
                start = r.start_dt.strftime("%Y-%m-%d")
                end   = r.end_dt.strftime("%Y-%m-%d")
                dr    = f"{r.direction_rate * 100:.1f}%" if r.direction_rate is not None else "—"
                label = f"{start} – {end}  |  n={r.n_events}  |  DR={dr}"
                opts.append({"label": label, "value": r.id})
        return opts, (opts[0]["value"] if opts else None)

    @callback(
        Output("sb-stock-dd", "options"),
        Output("sb-stock-dd", "value"),
        Output("sb-stats",    "children"),
        Input("sb-run-dd",    "value"),
    )
    def _load_stocks_and_stats(run_id: int | None) -> tuple[list[dict], Any, Any]:
        if run_id is None:
            return [], None, html.Span("Select a run.", style={"color": MUTED})

        with get_session() as s:
            run = s.get(SignBenchmarkRun, run_id)
            if run is None:
                return [], None, html.Span("Run not found.", style={"color": RED})
            rows: list[tuple[str, int]] = s.execute(
                select(SignBenchmarkEvent.stock_code, func.count().label("n"))
                .where(SignBenchmarkEvent.run_id == run_id)
                .group_by(SignBenchmarkEvent.stock_code)
                .order_by(func.count().desc(), SignBenchmarkEvent.stock_code)
            ).all()
            dr          = run.direction_rate
            mean_bars   = run.mean_trend_bars
            mag_flw     = run.mag_follow
            mag_rev     = run.mag_reverse

        opts = [{"label": f"{code} ({n})", "value": code} for code, n in rows]
        stocks = [code for code, _ in rows]

        def _row(label: str, val: str) -> html.Div:
            return html.Div(style=_S_STAT_ROW, children=[
                html.Span(label, style={"color": MUTED}),
                html.Span(val,   style={"color": TEXT, "fontWeight": "500"}),
            ])

        dr_color = GREEN if dr is not None and dr >= 0.50 else RED
        stats = [
            html.Div("Run Stats",
                     style={"color": ACCENT, "fontWeight": "600",
                            "marginBottom": "6px", "fontSize": "11px",
                            "textTransform": "uppercase", "letterSpacing": "0.5px"}),
            _row("n events",   str(run.n_events)),
            _row("DR",         html.Span(f"{dr * 100:.1f}%" if dr is not None else "—",
                                         style={"color": dr_color, "fontWeight": "600"})),
            _row("mean bars",  f"{mean_bars:.1f}" if mean_bars is not None else "—"),
            _row("mag follow", f"{mag_flw:.3f}"   if mag_flw  is not None else "—"),
            _row("mag reverse",f"{mag_rev:.3f}"   if mag_rev  is not None else "—"),
            html.Div(
                style={**_S_STAT_ROW, "borderBottom": "none", "marginTop": "4px"},
                children=[
                    html.Span("stocks w/ events", style={"color": MUTED}),
                    html.Span(str(len(stocks)),   style={"color": TEXT, "fontWeight": "500"}),
                ],
            ),
        ]
        return opts, (opts[0]["value"] if opts else None), stats

    @callback(
        Output("sb-chart",    "figure"),
        Input("sb-run-dd",    "value"),
        Input("sb-stock-dd",  "value"),
    )
    def _update_chart(run_id: int | None, stock_code: str | None) -> go.Figure:
        if run_id is None or not stock_code:
            return _empty_chart("Select a run and stock.")

        with get_session() as s:
            run = s.get(SignBenchmarkRun, run_id)
            if run is None:
                return _empty_chart("Run not found.")
            events_db = s.execute(
                select(SignBenchmarkEvent)
                .where(SignBenchmarkEvent.run_id     == run_id)
                .where(SignBenchmarkEvent.stock_code == stock_code)
                .order_by(SignBenchmarkEvent.fired_at)
            ).scalars().all()
            events = [
                {
                    "fired_at":        e.fired_at.strftime("%Y-%m-%d"),
                    "trend_direction": e.trend_direction,
                    "sign_score":      e.sign_score,
                    "trend_bars":      e.trend_bars,
                    "trend_magnitude": e.trend_magnitude,
                }
                for e in events_db
            ]
            run_meta = {
                "start_dt":  run.start_dt,
                "end_dt":    run.end_dt,
                "gran":      run.gran,
                "sign_type": run.sign_type,
            }

        return _build_bench_chart(stock_code, run_meta, events)


# ── Chart builder ──────────────────────────────────────────────────────────────

_WARMUP_DAYS = 90      # extra days before run.start_dt for SMA / Ichimoku warmup
_CORR_WINDOW = 20


def _build_bench_chart(
    stock_code: str,
    run_meta:   dict,
    events:     list[dict],
) -> go.Figure:
    try:
        run_start: datetime.datetime = run_meta["start_dt"]
        run_end:   datetime.datetime = run_meta["end_dt"]
        gran:      str               = run_meta["gran"]
        sign_type: str               = run_meta["sign_type"]

        load_start = run_start - datetime.timedelta(days=_WARMUP_DAYS)

        with get_session() as session:
            n225_cache = DataCache("^N225", gran)
            n225_cache.load(session, load_start, run_end)
            n225_cache.add_sma(25).add_sma(75)

            gspc_cache = DataCache("^GSPC", gran)
            gspc_cache.load(session, load_start, run_end)

            stock_cache = DataCache(stock_code, gran)
            stock_cache.load(session, load_start, run_end)
            stock_cache.add_sma(25).add_sma(75)

        if not stock_cache.bars or not n225_cache.bars:
            return _empty_chart(f"No data for {stock_code}")

        run_start_date = run_start.date()
        run_end_date   = run_end.date()

        stock_all_bars = stock_cache.bars
        n225_all_bars  = n225_cache.bars

        stock_bars = [b for b in stock_all_bars if run_start_date <= b.dt.date() <= run_end_date]
        n225_bars  = [b for b in n225_all_bars  if run_start_date <= b.dt.date() <= run_end_date]

        if not stock_bars or not n225_bars:
            return _empty_chart(f"No data in run period for {stock_code}")

        # ── Union date axis ───────────────────────────────────────────────────
        dates = sorted(
            {b.dt.strftime("%Y-%m-%d") for b in n225_bars}
            | {b.dt.strftime("%Y-%m-%d") for b in stock_bars}
        )
        n     = len(dates)
        ticks = _monthly_ticks(dates)

        # ── Helpers ───────────────────────────────────────────────────────────

        def _ohlcv(bars_slice: list) -> tuple:
            m = {b.dt.strftime("%Y-%m-%d"): b for b in bars_slice}
            return (
                [m[d].open                               if d in m else None for d in dates],
                [m[d].high                               if d in m else None for d in dates],
                [m[d].low                                if d in m else None for d in dates],
                [m[d].close                              if d in m else None for d in dates],
                [m[d].volume                             if d in m else None for d in dates],
                [(m[d].indicators.get("SMA25") or None)  if d in m else None for d in dates],
                [(m[d].indicators.get("SMA75") or None)  if d in m else None for d in dates],
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

        # ── N225 indicators ───────────────────────────────────────────────────
        n225_ichi = _ichi_map(n225_all_bars)
        n225_ca   = [n225_ichi.get(d, (None,) * 4)[0] for d in dates]
        n225_cb   = [n225_ichi.get(d, (None,) * 4)[1] for d in dates]
        n225_tk   = [n225_ichi.get(d, (None,) * 4)[2] for d in dates]
        n225_kj   = [n225_ichi.get(d, (None,) * 4)[3] for d in dates]
        n225_o, n225_h, n225_l, n225_c, n225_v, n225_sma25, n225_sma75 = _ohlcv(n225_bars)
        n225_zz_hi, n225_zz_lo = _zz_maps(n225_bars)
        n225_conf_hi = [(d, n225_zz_hi[d]) for d in dates if d in n225_zz_hi]
        n225_conf_lo = [(d, n225_zz_lo[d]) for d in dates if d in n225_zz_lo]

        def _add_n225_price(fig: go.Figure, row: int, legend: bool) -> None:
            _add_cloud(fig, n225_ca, n225_cb, row)
            fig.add_trace(go.Scatter(x=dates, y=n225_tk, mode="lines", name="Tenkan",
                                     showlegend=legend,
                                     line=dict(color="#ef5350", width=1, dash="dot"),
                                     hovertemplate="Tenkan: %{y:,.0f}<extra></extra>"),
                          row=row, col=1)
            fig.add_trace(go.Scatter(x=dates, y=n225_kj, mode="lines", name="Kijun",
                                     showlegend=legend,
                                     line=dict(color="#42a5f5", width=1.2),
                                     hovertemplate="Kijun: %{y:,.0f}<extra></extra>"),
                          row=row, col=1)
            fig.add_trace(go.Candlestick(
                x=dates, open=n225_o, high=n225_h, low=n225_l, close=n225_c,
                name="^N225",
                increasing_fillcolor="#26a69a", increasing_line_color="#26a69a",
                decreasing_fillcolor="#ef5350", decreasing_line_color="#ef5350",
                showlegend=False,
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "O:%{open:,.0f} H:%{high:,.0f} L:%{low:,.0f} C:%{close:,.0f}"
                    "<extra></extra>"
                ),
            ), row=row, col=1)
            fig.add_trace(go.Scatter(x=dates, y=n225_sma25, mode="lines", name="SMA25",
                                     showlegend=legend, line=dict(color="#ff9800", width=1.2),
                                     hovertemplate="SMA25: %{y:,.0f}<extra></extra>"),
                          row=row, col=1)
            fig.add_trace(go.Scatter(x=dates, y=n225_sma75, mode="lines", name="SMA75",
                                     showlegend=legend, line=dict(color="#ab47bc", width=1.2),
                                     hovertemplate="SMA75: %{y:,.0f}<extra></extra>"),
                          row=row, col=1)
            if n225_conf_hi:
                hx, hy = zip(*n225_conf_hi)
                fig.add_trace(go.Scatter(
                    x=list(hx), y=list(hy), mode="markers", name="ZZ high",
                    showlegend=legend,
                    marker=dict(symbol="triangle-down", size=9, color="#ef5350",
                                line=dict(width=1, color="#fff")),
                    hovertemplate="ZZ high: %{y:,.0f}<extra></extra>",
                ), row=row, col=1)
            if n225_conf_lo:
                lx, ly = zip(*n225_conf_lo)
                fig.add_trace(go.Scatter(
                    x=list(lx), y=list(ly), mode="markers", name="ZZ low",
                    showlegend=legend,
                    marker=dict(symbol="triangle-up", size=9, color="#26a69a",
                                line=dict(width=1, color="#fff")),
                    hovertemplate="ZZ low: %{y:,.0f}<extra></extra>",
                ), row=row, col=1)

        # ── Stock indicators ──────────────────────────────────────────────────
        s_ichi = _ichi_map(stock_all_bars)
        s_ca   = [s_ichi.get(d, (None,) * 4)[0] for d in dates]
        s_cb   = [s_ichi.get(d, (None,) * 4)[1] for d in dates]
        s_tk   = [s_ichi.get(d, (None,) * 4)[2] for d in dates]
        s_kj   = [s_ichi.get(d, (None,) * 4)[3] for d in dates]
        s_o, s_h, s_l, s_c, s_v, s_sma25, s_sma75 = _ohlcv(stock_bars)
        s_zz_hi, s_zz_lo = _zz_maps(stock_bars)
        s_conf_hi = [(d, s_zz_hi[d]) for d in dates if d in s_zz_hi]
        s_conf_lo = [(d, s_zz_lo[d]) for d in dates if d in s_zz_lo]

        _sb_ds = [b.dt.strftime("%Y-%m-%d") for b in stock_bars]
        _adx   = ADXIndicator(
            high=pd.Series([b.high  for b in stock_bars], dtype=float),
            low=pd.Series([b.low   for b in stock_bars], dtype=float),
            close=pd.Series([b.close for b in stock_bars], dtype=float),
            window=14,
        )
        _adx_m = {d: (None if pd.isna(v) else float(v))
                  for d, v in zip(_sb_ds, _adx.adx())}
        _dip_m = {d: (None if pd.isna(v) else float(v))
                  for d, v in zip(_sb_ds, _adx.adx_pos())}
        _din_m = {d: (None if pd.isna(v) else float(v))
                  for d, v in zip(_sb_ds, _adx.adx_neg())}
        s_adx_v = [_adx_m.get(d) for d in dates]
        s_dip_v = [_dip_m.get(d) for d in dates]
        s_din_v = [_din_m.get(d) for d in dates]

        first_n = next((v for v in n225_c if v is not None), None)
        first_s = next((v for v in s_c    if v is not None), None)
        n225_norm = (
            [v * (first_s / first_n) if v is not None else None for v in n225_c]
            if first_n and first_s else [None] * n
        )

        # ── Rolling correlation ───────────────────────────────────────────────
        _s_ser   = pd.Series({b.dt.date(): b.close for b in stock_bars})
        _ind_map: dict[str, pd.Series] = {
            "^N225": pd.Series({b.dt.date(): b.close for b in n225_bars}),
        }
        gspc_bars = [b for b in gspc_cache.bars
                     if run_start_date <= b.dt.date() <= run_end_date]
        if gspc_bars:
            _ind_map["^GSPC"] = pd.Series({b.dt.date(): b.close for b in gspc_bars})
        _corr = compute_moving_corr(_s_ser, _ind_map, window=_CORR_WINDOW)

        def _corr_series(key: str) -> list[float | None]:
            ser = _corr.get(key)
            if ser is None:
                return [None] * n
            dm = ser.to_dict()
            return [
                (None if pd.isna(v := dm.get(datetime.date.fromisoformat(d))) else float(v))
                for d in dates
            ]

        n225_corr_v = _corr_series("^N225")
        gspc_corr_v = _corr_series("^GSPC")

        # ── Assemble figure ───────────────────────────────────────────────────
        fig = make_subplots(
            rows=5, cols=1, shared_xaxes=True,
            row_heights=[0.37, 0.10, 0.08, 0.31, 0.14],
            vertical_spacing=0.010,
        )

        # Row 1 — stock price
        _add_cloud(fig, s_ca, s_cb, row=1)
        fig.add_trace(go.Scatter(x=dates, y=s_tk, mode="lines", name="Tenkan",
                                 line=dict(color="#ef5350", width=1, dash="dot"),
                                 hovertemplate="Tenkan: %{y:,.0f}<extra></extra>"), row=1, col=1)
        fig.add_trace(go.Scatter(x=dates, y=s_kj, mode="lines", name="Kijun",
                                 line=dict(color="#42a5f5", width=1.2),
                                 hovertemplate="Kijun: %{y:,.0f}<extra></extra>"), row=1, col=1)
        fig.add_trace(go.Scatter(x=dates, y=n225_norm, mode="lines", name="^N225",
                                 line=dict(color="#78909c", width=1.2, dash="dash"), opacity=0.7,
                                 hovertemplate="^N225 (norm): %{y:,.0f}<extra></extra>"), row=1, col=1)
        fig.add_trace(go.Candlestick(
            x=dates, open=s_o, high=s_h, low=s_l, close=s_c,
            name=stock_code,
            increasing_fillcolor="#26a69a", increasing_line_color="#26a69a",
            decreasing_fillcolor="#ef5350", decreasing_line_color="#ef5350",
            showlegend=False,
            hovertemplate=(
                "<b>%{x}</b><br>"
                "O:%{open:,.0f} H:%{high:,.0f} L:%{low:,.0f} C:%{close:,.0f}"
                "<extra></extra>"
            ),
        ), row=1, col=1)
        fig.add_trace(go.Scatter(x=dates, y=s_sma25, mode="lines", name="SMA25",
                                 line=dict(color="#ff9800", width=1.2),
                                 hovertemplate="SMA25: %{y:,.0f}<extra></extra>"), row=1, col=1)
        fig.add_trace(go.Scatter(x=dates, y=s_sma75, mode="lines", name="SMA75",
                                 line=dict(color="#ab47bc", width=1.2),
                                 hovertemplate="SMA75: %{y:,.0f}<extra></extra>"), row=1, col=1)
        if s_conf_hi:
            hx, hy = zip(*s_conf_hi)
            fig.add_trace(go.Scatter(x=list(hx), y=list(hy), mode="markers", name="ZZ high",
                                     marker=dict(symbol="triangle-down", size=9, color="#ef5350",
                                                 line=dict(width=1, color="#fff")),
                                     hovertemplate="ZZ high: %{y:,.0f}<extra></extra>"), row=1, col=1)
        if s_conf_lo:
            lx, ly = zip(*s_conf_lo)
            fig.add_trace(go.Scatter(x=list(lx), y=list(ly), mode="markers", name="ZZ low",
                                     marker=dict(symbol="triangle-up", size=9, color="#26a69a",
                                                 line=dict(width=1, color="#fff")),
                                     hovertemplate="ZZ low: %{y:,.0f}<extra></extra>"), row=1, col=1)

        # Row 2 — ADX
        fig.add_trace(go.Scatter(x=dates, y=s_adx_v, mode="lines", name="ADX",
                                 line=dict(color="#eceff1", width=1.4),
                                 hovertemplate="ADX: %{y:.1f}<extra></extra>"), row=2, col=1)
        fig.add_trace(go.Scatter(x=dates, y=s_dip_v, mode="lines", name="+DI",
                                 line=dict(color=GREEN, width=1),
                                 hovertemplate="+DI: %{y:.1f}<extra></extra>"), row=2, col=1)
        fig.add_trace(go.Scatter(x=dates, y=s_din_v, mode="lines", name="−DI",
                                 line=dict(color=RED, width=1),
                                 hovertemplate="−DI: %{y:.1f}<extra></extra>"), row=2, col=1)
        fig.add_hline(y=20, line_dash="dot", line_color=MUTED, opacity=0.5, row=2, col=1)

        # Row 3 — volume
        vcol = ["#26a69a" if (c or 0) >= (o or 0) else "#ef5350" for c, o in zip(s_c, s_o)]
        fig.add_trace(go.Bar(x=dates, y=s_v, name="Volume", marker_color=vcol,
                             showlegend=False,
                             hovertemplate="<b>Vol</b>  %{x}<br>%{y:,.0f}<extra></extra>"),
                      row=3, col=1)

        # Row 4 — N225 price
        _add_n225_price(fig, row=4, legend=False)

        # Row 5 — rolling correlation
        fig.add_trace(go.Scatter(x=dates, y=n225_corr_v, mode="lines", name="ρ ^N225",
                                 line=dict(color="#ff9800", width=1.4),
                                 hovertemplate="ρ ^N225: %{y:.2f}<extra></extra>"), row=5, col=1)
        if gspc_bars:
            fig.add_trace(go.Scatter(x=dates, y=gspc_corr_v, mode="lines", name="ρ ^GSPC",
                                     line=dict(color="#29b6f6", width=1.4),
                                     hovertemplate="ρ ^GSPC: %{y:.2f}<extra></extra>"), row=5, col=1)
        for lvl, col, dash, lw in (
            ( 0.6, "rgba(38,166,154,0.4)",   "dot",   1.0),
            ( 0.0, "rgba(180,180,180,0.85)", "solid", 2.0),
            (-0.6, "rgba(239,83,80,0.4)",    "dot",   1.0),
        ):
            fig.add_hline(y=lvl, line_dash=dash, line_color=col, line_width=lw,
                          opacity=1.0, row=5, col=1)

        # ── Event markers ─────────────────────────────────────────────────────
        for ev in events:
            d = ev["fired_at"]
            if d not in dates:
                continue
            td    = ev["trend_direction"]
            score = ev["sign_score"]
            color = "#00e676" if td == 1 else ("#ef5350" if td == -1 else "#78909c")
            fig.add_shape(
                type="line", x0=d, x1=d, y0=0, y1=1,
                xref="x", yref="paper",
                line=dict(width=1.5, dash="dot", color=color),
                opacity=0.85,
            )
            fig.add_annotation(
                x=d, y=0.99, xref="x", yref="paper",
                text=f"{score:.2f}", showarrow=False,
                xanchor="left", yanchor="top",
                font=dict(size=8, color=color),
            )

        # ── Layout & axes ─────────────────────────────────────────────────────
        n_ev  = len(events)
        n_pos = sum(1 for e in events if e["trend_direction"] == 1)
        dr_s  = f"{n_pos / n_ev * 100:.0f}%" if n_ev else "—"
        title = (
            f"{stock_code}  ·  {sign_type}  ·  "
            f"{run_start.strftime('%Y-%m-%d')} – {run_end.strftime('%Y-%m-%d')}  ·  "
            f"n={n_ev}  DR={dr_s}  "
            f"(green=follow, red=reverse)"
        )

        fig.update_layout(
            template="plotly_dark", paper_bgcolor=BG, plot_bgcolor="#0d1117",
            margin=dict(l=60, r=20, t=36, b=10),
            title=dict(text=title, font=dict(size=12, color=MUTED), x=0.01),
            legend=dict(orientation="h", yanchor="bottom", y=1.01,
                        xanchor="right", x=1, font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
            dragmode="pan", hovermode="x unified",
            yaxis_title="Price",  yaxis2_title="ADX",
            yaxis3_title="Vol",   yaxis4_title="^N225",
            yaxis5_title=f"ρ({_CORR_WINDOW})",
            yaxis_tickformat=",.0f", yaxis4_tickformat=",.0f",
        )
        fig.update_xaxes(type="category")
        fig.update_xaxes(tickvals=ticks, ticktext=ticks, tickangle=-30, tickfont=dict(size=9))
        fig.update_xaxes(rangeslider_visible=False)
        fig.update_xaxes(
            rangeslider_visible=True,
            rangeslider=dict(thickness=0.03, bgcolor=CARD_BG, bordercolor=BORDER),
            row=5, col=1,
        )
        return fig

    except Exception:
        logger.exception("sign_bench chart error for %s", stock_code)
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
