"""Stacked price + rolling-correlation chart for a single stock vs multiple indices."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.simulator.cache import DataCache
from src.viz.palette import BG, MUTED, GREEN, RED
from src.viz.price import build_price_figure, empty_figure
from src.viz.pair import ZigzagPoint, _zigzag_trace


def build_moving_corr_figure(
    cache: DataCache,
    corr_map: dict[str, pd.Series],
    title: str = "",
    indicator_cache: DataCache | None = None,
    indicator_zigzag: list[ZigzagPoint] | None = None,
    indicator_label: str = "",
) -> go.Figure:
    """Vertically stacked chart:
      Row 1   — stock candlestick
      Row 2   — stock volume
      Row 3   — first indicator candlestick + zigzag  (when indicator_cache given)
      Rows …  — one corr bar panel per indicator

    All rows share the x-axis for synchronized zoom/pan.
    Reference lines at ρ = 0, ±0.7 are drawn on each correlation panel.
    """
    bars = cache.bars
    if not bars:
        return empty_figure("No price data")
    if not corr_map:
        return build_price_figure(cache, title=title)

    has_ind = indicator_cache is not None and bool(indicator_cache.bars)

    # Use full datetime label when bars run at sub-daily frequency
    intraday = len(bars) >= 2 and bars[0].dt.date() == bars[1].dt.date()
    ts_fmt   = "%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d"

    # Stock bar x-labels (daytime for JP stocks)
    stock_dates = [b.dt.strftime(ts_fmt) for b in bars]
    opens  = [b.open   for b in bars]
    highs  = [b.high   for b in bars]
    lows   = [b.low    for b in bars]
    closes = [b.close  for b in bars]
    vols   = [b.volume for b in bars]

    # Union x-axis: merge stock timestamps with indicator timestamps so that
    # JP daytime bars and US/EU nighttime bars appear in the same timeline.
    if has_ind:
        ind_bars_list = indicator_cache.bars  # type: ignore[union-attr]
        ind_date_strs = [b.dt.strftime(ts_fmt) for b in ind_bars_list]
        dates = sorted(set(stock_dates) | set(ind_date_strs))
    else:
        ind_date_strs = []
        dates = stock_dates

    n_inds   = len(corr_map)
    price_h  = 0.28
    vol_h    = 0.03
    ind_h    = 0.20 if has_ind else 0.0
    spacing  = 0.008
    n_rows   = 2 + (1 if has_ind else 0) + n_inds
    available = 1.0 - price_h - vol_h - ind_h - spacing * (n_rows - 1)
    corr_h   = available / n_inds

    row_heights = [price_h, vol_h] + ([ind_h] if has_ind else []) + [corr_h] * n_inds
    corr_row0   = 4 if has_ind else 3   # first correlation row number

    fig = make_subplots(
        rows=n_rows, cols=1, shared_xaxes=True,
        row_heights=row_heights,
        vertical_spacing=spacing,
    )

    # ── Row 1: stock candlestick ──────────────────────────────────────────────

    fig.add_trace(
        go.Candlestick(
            x=stock_dates, open=opens, high=highs, low=lows, close=closes,
            name=title or "Price",
            increasing_fillcolor="#26a69a", increasing_line_color="#26a69a",
            decreasing_fillcolor="#ef5350", decreasing_line_color="#ef5350",
            showlegend=False,
            hovertemplate=(
                "<b>%{x}</b><br>"
                "O:%{open:,.2f} H:%{high:,.2f} L:%{low:,.2f} C:%{close:,.2f}"
                "<extra></extra>"
            ),
        ),
        row=1, col=1,
    )

    # ── Row 2: stock volume ───────────────────────────────────────────────────

    vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(closes, opens)]
    fig.add_trace(
        go.Bar(
            x=stock_dates, y=vols, marker_color=vol_colors, showlegend=False,
            hovertemplate="Vol %{x}: %{y:,.0f}<extra></extra>",
        ),
        row=2, col=1,
    )

    # ── Row 3: first indicator candlestick + zigzag ───────────────────────────

    if has_ind:
        ind_bars   = indicator_cache.bars  # type: ignore[union-attr]
        ind_opens  = [b.open   for b in ind_bars]
        ind_highs  = [b.high   for b in ind_bars]
        ind_lows   = [b.low    for b in ind_bars]
        ind_closes = [b.close  for b in ind_bars]

        fig.add_trace(
            go.Candlestick(
                x=ind_date_strs,
                open=ind_opens, high=ind_highs, low=ind_lows, close=ind_closes,
                name=indicator_label or "Indicator",
                increasing_fillcolor="#26a69a", increasing_line_color="#26a69a",
                decreasing_fillcolor="#ef5350", decreasing_line_color="#ef5350",
                showlegend=False,
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "O:%{open:,.2f} H:%{high:,.2f} L:%{low:,.2f} C:%{close:,.2f}"
                    "<extra></extra>"
                ),
            ),
            row=3, col=1,
        )

        if indicator_zigzag:
            fig.add_trace(_zigzag_trace(indicator_zigzag), row=3, col=1)

        fig.update_yaxes(
            title_text=indicator_label or "Indicator",
            title_font=dict(size=9, color=MUTED),
            tickformat=",.2f",
            row=3, col=1,
        )

    # ── Corr rows ─────────────────────────────────────────────────────────────

    for i, (code, series) in enumerate(corr_map.items()):
        row = corr_row0 + i

        corr_by_date: dict[str, float | None] = {
            ts.strftime(ts_fmt): (float(v) if pd.notna(v) else None)
            for ts, v in series.items()
        }
        # Correlation bars only at stock (daytime) positions; nighttime slots are gaps
        corr_vals = [corr_by_date.get(d) for d in stock_dates]

        bar_colors = [
            GREEN if (v is not None and v >= 0) else RED
            for v in corr_vals
        ]

        fig.add_trace(
            go.Bar(
                x=stock_dates, y=corr_vals, name=code,
                marker_color=bar_colors, showlegend=False,
                hovertemplate=f"<b>%{{x}}</b>  ρ={code} %{{y:.3f}}<extra></extra>",
            ),
            row=row, col=1,
        )
        fig.add_hline(y=0,    line_dash="dot", line_color=MUTED,  line_width=0.8, row=row, col=1)
        fig.add_hline(y=0.7,  line_dash="dot", line_color=GREEN,  line_width=0.8, row=row, col=1)
        fig.add_hline(y=-0.7, line_dash="dot", line_color=RED,    line_width=0.8, row=row, col=1)
        fig.update_yaxes(
            range=[-1.1, 1.1],
            title_text=code,
            title_font=dict(size=9, color=MUTED),
            row=row, col=1,
        )

    # ── Shared layout ─────────────────────────────────────────────────────────

    n     = len(dates)
    ticks = dates[::max(1, n // 24)]

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=BG, plot_bgcolor=BG,
        margin=dict(l=80, r=20, t=36, b=10),
        title=dict(text=title, font=dict(size=13, color=MUTED), x=0.01),
        dragmode="pan",
        hovermode="x unified",
        yaxis_title="Price",
        yaxis_tickformat=",.2f",
        yaxis2_title="Vol",
        yaxis2_tickformat=".2s",
    )
    # categoryarray pins the chronological order of the union x-axis so that
    # JP daytime bars and US/EU nighttime bars appear in sequence, not shuffled.
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=dates)
    fig.update_xaxes(tickvals=ticks, ticktext=ticks, tickangle=-30, tickfont=dict(size=10))
    fig.update_xaxes(rangeslider_visible=False)
    return fig
