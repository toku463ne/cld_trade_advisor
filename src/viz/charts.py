"""Shared chart-building primitives used across visualization modules."""

from __future__ import annotations

import datetime

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.simulator.cache import DataCache

# ── Shared palette (single source of truth) ───────────────────────────────────

BG         = "#0d1117"
SIDEBAR_BG = "#161b22"
CARD_BG    = "#1c2128"
BORDER     = "#30363d"
TEXT       = "#c9d1d9"
MUTED      = "#8b949e"
ACCENT     = "#58a6ff"
GREEN      = "#3fb950"
RED        = "#f85149"


# ── Helpers ───────────────────────────────────────────────────────────────────


def empty_figure(msg: str = "No data") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=BG, plot_bgcolor=BG,
        margin=dict(l=20, r=20, t=20, b=20),
        annotations=[{
            "text": msg, "xref": "paper", "yref": "paper",
            "x": 0.5, "y": 0.5, "showarrow": False,
            "font": {"size": 14, "color": MUTED},
        }],
    )
    return fig


def build_price_figure(cache: DataCache, title: str = "") -> go.Figure:
    """Candlestick + volume figure for a single stock. No strategy overlays."""
    bars = cache.bars
    if not bars:
        return empty_figure("No price data")

    dates  = [b.dt.strftime("%Y-%m-%d") for b in bars]
    opens  = [b.open   for b in bars]
    highs  = [b.high   for b in bars]
    lows   = [b.low    for b in bars]
    closes = [b.close  for b in bars]
    vols   = [b.volume for b in bars]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.82, 0.18],
        vertical_spacing=0.02,
    )

    fig.add_trace(
        go.Candlestick(
            x=dates, open=opens, high=highs, low=lows, close=closes,
            name=title or "OHLCV",
            increasing_fillcolor="#26a69a", increasing_line_color="#26a69a",
            decreasing_fillcolor="#ef5350", decreasing_line_color="#ef5350",
            showlegend=False,
            hovertemplate=(
                "<b>%{x}</b><br>"
                "O: %{open:,.2f}  H: %{high:,.2f}<br>"
                "L: %{low:,.2f}  C: %{close:,.2f}<extra></extra>"
            ),
        ),
        row=1, col=1,
    )

    vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(closes, opens)]
    fig.add_trace(
        go.Bar(
            x=dates, y=vols, name="Volume", marker_color=vol_colors,
            showlegend=False,
            hovertemplate="<b>Vol</b>  %{x}<br>%{y:,.0f}<extra></extra>",
        ),
        row=2, col=1,
    )

    n     = len(dates)
    ticks = dates[::max(1, n // 24)]

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=BG, plot_bgcolor=BG,
        margin=dict(l=60, r=20, t=36, b=10),
        title=dict(text=title, font=dict(size=13, color=MUTED), x=0.01),
        dragmode="pan",
        hovermode="x unified",
        yaxis_title="Price", yaxis2_title="Vol",
        yaxis_tickformat=",.2f", yaxis2_tickformat=".2s",
    )
    fig.update_xaxes(type="category")
    fig.update_xaxes(tickvals=ticks, ticktext=ticks, tickangle=-30, tickfont=dict(size=10))
    fig.update_xaxes(rangeslider_visible=False)
    fig.update_xaxes(
        rangeslider_visible=True,
        rangeslider=dict(thickness=0.05, bgcolor=CARD_BG, bordercolor=BORDER),
        row=2, col=1,
    )
    return fig


# ZigzagPoint = (date_str, price, direction)   direction: 2=conf high, -2=conf low, ±1=early
ZigzagPoint = tuple[str, float, int]


def _zigzag_trace(pts: list[ZigzagPoint], name: str = "Zigzag") -> go.Scatter:
    """Line + marker trace connecting confirmed zigzag peaks/troughs."""
    xs      = [p[0] for p in pts]
    ys      = [p[1] for p in pts]
    symbols = ["triangle-up" if p[2] > 0 else "triangle-down" for p in pts]
    return go.Scatter(
        x=xs, y=ys,
        mode="lines+markers",
        line=dict(color="#f0c040", width=1.5, dash="dot"),
        marker=dict(color="#f0c040", symbol=symbols, size=10,
                    line=dict(color=BG, width=1)),
        name=name,
        showlegend=False,
        hovertemplate="ZZ  <b>%{x}</b>  %{y:,.2f}<extra></extra>",
    )


def build_pair_figure(
    cache_a: DataCache, cache_b: DataCache,
    title_a: str = "", title_b: str = "",
    zigzag_a: list[ZigzagPoint] | None = None,
    zigzag_b: list[ZigzagPoint] | None = None,
) -> go.Figure:
    """4-row subplot with shared x-axis: A price / A vol / B price / B vol.

    Dates are unioned across both stocks so the x-axis is perfectly aligned.
    Missing bars for a stock on a given date are rendered as gaps.
    Pass *zigzag_a* / *zigzag_b* to overlay a zigzag line on the price rows.
    """
    bars_a = {b.dt: b for b in cache_a.bars}
    bars_b = {b.dt: b for b in cache_b.bars}

    if not bars_a and not bars_b:
        return empty_figure("No price data")

    all_dts = sorted(bars_a.keys() | bars_b.keys())
    dates   = [d.strftime("%Y-%m-%d") for d in all_dts]

    def _field(bars: dict[datetime.datetime, object], attr: str) -> list:
        return [getattr(bars[d], attr) if d in bars else None for d in all_dts]

    def _vol_colors(closes: list, opens: list) -> list[str]:
        return [
            "#26a69a" if (c is not None and o is not None and c >= o) else "#ef5350"
            for c, o in zip(closes, opens)
        ]

    o_a, h_a = _field(bars_a, "open"), _field(bars_a, "high")
    l_a, c_a = _field(bars_a, "low"),  _field(bars_a, "close")
    v_a      = _field(bars_a, "volume")

    o_b, h_b = _field(bars_b, "open"), _field(bars_b, "high")
    l_b, c_b = _field(bars_b, "low"),  _field(bars_b, "close")
    v_b      = _field(bars_b, "volume")

    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True,
        row_heights=[0.45, 0.05, 0.45, 0.05],
        vertical_spacing=0.01,
        subplot_titles=["", "", "", ""],
    )

    _cs_kw = dict(
        increasing_fillcolor="#26a69a", increasing_line_color="#26a69a",
        decreasing_fillcolor="#ef5350", decreasing_line_color="#ef5350",
        showlegend=False,
    )
    fig.add_trace(
        go.Candlestick(x=dates, open=o_a, high=h_a, low=l_a, close=c_a,
                       name=title_a or "Stock A",
                       hovertemplate="<b>%{x}</b><br>O:%{open:,.2f} H:%{high:,.2f} L:%{low:,.2f} C:%{close:,.2f}<extra></extra>",
                       **_cs_kw),
        row=1, col=1,
    )
    fig.add_trace(
        go.Bar(x=dates, y=v_a, marker_color=_vol_colors(c_a, o_a), showlegend=False,
               hovertemplate="Vol %{x}: %{y:,.0f}<extra></extra>"),
        row=2, col=1,
    )
    fig.add_trace(
        go.Candlestick(x=dates, open=o_b, high=h_b, low=l_b, close=c_b,
                       name=title_b or "Stock B",
                       hovertemplate="<b>%{x}</b><br>O:%{open:,.2f} H:%{high:,.2f} L:%{low:,.2f} C:%{close:,.2f}<extra></extra>",
                       **_cs_kw),
        row=3, col=1,
    )
    fig.add_trace(
        go.Bar(x=dates, y=v_b, marker_color=_vol_colors(c_b, o_b), showlegend=False,
               hovertemplate="Vol %{x}: %{y:,.0f}<extra></extra>"),
        row=4, col=1,
    )

    if zigzag_a:
        fig.add_trace(_zigzag_trace(zigzag_a), row=1, col=1)
    if zigzag_b:
        fig.add_trace(_zigzag_trace(zigzag_b), row=3, col=1)

    n     = len(dates)
    ticks = dates[::max(1, n // 24)]

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=BG, plot_bgcolor=BG,
        margin=dict(l=60, r=20, t=8, b=10),
        dragmode="pan",
        hovermode="x unified",
        yaxis_title=title_a or "A",  yaxis_tickformat=",.2f",
        yaxis2_title="Vol",          yaxis2_tickformat=".2s",
        yaxis3_title=title_b or "B", yaxis3_tickformat=",.2f",
        yaxis4_title="Vol",          yaxis4_tickformat=".2s",
    )
    fig.update_xaxes(type="category")
    fig.update_xaxes(tickvals=ticks, ticktext=ticks, tickangle=-30, tickfont=dict(size=10))
    fig.update_xaxes(rangeslider_visible=False)
    fig.update_xaxes(
        rangeslider_visible=True,
        rangeslider=dict(thickness=0.04, bgcolor=CARD_BG, bordercolor=BORDER),
        row=4, col=1,
    )
    return fig
