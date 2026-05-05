"""Single-stock price charts: empty placeholder and candlestick + volume."""

from __future__ import annotations

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.simulator.cache import DataCache
from src.viz.palette import BG, CARD_BG, BORDER, MUTED


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
    """Candlestick + volume figure for a single stock."""
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
