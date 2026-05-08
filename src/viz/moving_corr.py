"""Stacked price + rolling-correlation chart for a single stock vs multiple indices."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.simulator.cache import DataCache
from src.viz.palette import BG, MUTED, GREEN, RED
from src.viz.price import build_price_figure, empty_figure


def _add_overlay_trace(
    fig: go.Figure,
    dates: list[str],
    ov: dict,
    row: int,
) -> None:
    """Add an overlay trace to *row*.

    Supports kind="zigzag" (lines+markers at peak points) or a plain
    Scatter line (default, identified by having a ``y`` key).
    """
    if ov.get("kind") == "zigzag":
        pts = ov.get("points", [])
        if not pts:
            return
        xs      = [p[0] for p in pts]
        ys      = [p[1] for p in pts]
        symbols = ["triangle-up" if p[2] > 0 else "triangle-down" for p in pts]
        fig.add_trace(
            go.Scatter(
                x=xs, y=ys,
                mode="lines+markers",
                line=dict(color="#f0c040", width=1.5, dash="dot"),
                marker=dict(color="#f0c040", symbol=symbols, size=10,
                            line=dict(color="#0d1117", width=1)),
                name="Zigzag", showlegend=False,
                hovertemplate="ZZ <b>%{x}</b>  %{y:,.2f}<extra></extra>",
            ),
            row=row, col=1,
        )
        return
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=ov["y"],
            mode="lines",
            name=ov.get("label", ""),
            line=dict(
                color=ov.get("color", "#aaaaaa"),
                width=ov.get("width", 1.2),
                dash=ov.get("dash", "solid"),
            ),
            fill=ov.get("fill"),
            fillcolor=ov.get("fillcolor"),
            showlegend=False,
            hovertemplate=f"{ov.get('label', '')} %{{x}}: %{{y:,.4f}}<extra></extra>",
        ),
        row=row, col=1,
    )


def _add_sub_panel(
    fig: go.Figure,
    row: int,
    dates: list[str],
    panel: dict,
) -> None:
    """Add traces for a sub-panel (line or MACD) to *row*."""
    kind = panel.get("kind", "line")
    label = panel.get("label", "")
    if kind == "macd":
        hist = panel.get("hist", [])
        hist_colors = [GREEN if (v is not None and not (v != v) and v >= 0) else RED for v in hist]
        fig.add_trace(go.Bar(
            x=dates, y=hist,
            marker_color=hist_colors, showlegend=False,
            hovertemplate=f"MACD hist %{{x}}: %{{y:.4f}}<extra></extra>",
        ), row=row, col=1)
        fig.add_trace(go.Scatter(
            x=dates, y=panel.get("macd", []), mode="lines",
            line=dict(color="#2196F3", width=1.2), showlegend=False,
            hovertemplate=f"MACD %{{x}}: %{{y:.4f}}<extra></extra>",
        ), row=row, col=1)
        fig.add_trace(go.Scatter(
            x=dates, y=panel.get("signal", []), mode="lines",
            line=dict(color="#FF9800", width=1.2), showlegend=False,
            hovertemplate=f"Signal %{{x}}: %{{y:.4f}}<extra></extra>",
        ), row=row, col=1)
    else:
        fig.add_trace(go.Scatter(
            x=dates, y=panel.get("y", []), mode="lines",
            line=dict(color=panel.get("color", "#aaaaaa"), width=1.2),
            showlegend=False,
            hovertemplate=f"{label} %{{x}}: %{{y:.4f}}<extra></extra>",
        ), row=row, col=1)
        for hl in (panel.get("hlines") or []):
            fig.add_hline(y=hl, line_dash="dot", line_color=MUTED, line_width=0.8,
                          row=row, col=1)
    fig.update_yaxes(
        title_text=label,
        title_font=dict(size=9, color=MUTED),
        row=row, col=1,
    )


def build_moving_corr_figure(
    cache: DataCache,
    corr_map: dict[str, pd.Series],
    title: str = "",
    indicator_cache: DataCache | None = None,
    indicator_label: str = "",
    stock_overlays: list[dict] | None = None,
    ind_overlays: list[dict] | None = None,
    sub_panels: list[dict] | None = None,
) -> go.Figure:
    """Vertically stacked chart:

      Row 1         — stock candlestick + stock_overlays
      Row 2         — stock volume
      Row 3         — indicator (N225) candlestick + ind_overlays  (when given)
      Rows 4..      — sub-panels (RSI, MACD, ATR …)
      Rows last..   — one corr bar panel per indicator

    Parameters
    ----------
    stock_overlays:
        List of overlay dicts for the stock price row.
        Keys: label, y (aligned with stock bars), color, dash, width,
              fill (e.g. "tonexty"), fillcolor.
    ind_overlays:
        Same format; overlaid on the indicator (N225) row.
    sub_panels:
        List of sub-panel dicts for indicators below both price charts.
        kind="line": keys label, y, color, hlines.
        kind="macd": keys label, hist, macd, signal.
    """
    bars = cache.bars
    if not bars:
        return empty_figure("No price data")
    if not corr_map:
        return build_price_figure(cache, title=title)

    has_ind = indicator_cache is not None and bool(indicator_cache.bars)

    intraday = len(bars) >= 2 and bars[0].dt.date() == bars[1].dt.date()
    ts_fmt   = "%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d"

    stock_dates = [b.dt.strftime(ts_fmt) for b in bars]
    opens  = [b.open   for b in bars]
    highs  = [b.high   for b in bars]
    lows   = [b.low    for b in bars]
    closes = [b.close  for b in bars]
    vols   = [b.volume for b in bars]

    if has_ind:
        ind_bars_list = indicator_cache.bars  # type: ignore[union-attr]
        ind_date_strs = [b.dt.strftime(ts_fmt) for b in ind_bars_list]
        dates = sorted(set(stock_dates) | set(ind_date_strs))
    else:
        ind_date_strs = []
        dates = stock_dates

    n_sub  = len(sub_panels or [])
    n_inds = len(corr_map)

    price_h  = 0.25
    vol_h    = 0.03
    ind_h    = 0.20 if has_ind else 0.0
    sub_h    = 0.09
    spacing  = 0.008
    n_rows   = 2 + n_sub + (1 if has_ind else 0) + n_inds
    total_fixed = price_h + vol_h + sub_h * n_sub + ind_h + spacing * (n_rows - 1)
    corr_h   = max(0.04, (1.0 - total_fixed) / n_inds) if n_inds else 0.04

    # Row order: stock price | stock vol | sub-panels… | N225 | corr panels…
    row_heights = [price_h, vol_h]
    row_heights += [sub_h] * n_sub
    if has_ind:
        row_heights.append(ind_h)
    row_heights += [corr_h] * n_inds

    sub_row0  = 3                               # first sub-panel row (1-indexed)
    ind_row   = sub_row0 + n_sub                # N225 row
    corr_row0 = ind_row + (1 if has_ind else 0)

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

    for ov in (stock_overlays or []):
        _add_overlay_trace(fig, stock_dates, ov, row=1)

    # ── Row 2: stock volume ───────────────────────────────────────────────────

    vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(closes, opens)]
    fig.add_trace(
        go.Bar(
            x=stock_dates, y=vols, marker_color=vol_colors, showlegend=False,
            hovertemplate="Vol %{x}: %{y:,.0f}<extra></extra>",
        ),
        row=2, col=1,
    )

    # ── Row 3: indicator (N225) candlestick ───────────────────────────────────

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
            row=ind_row, col=1,
        )

        for ov in (ind_overlays or []):
            _add_overlay_trace(fig, ind_date_strs, ov, row=ind_row)

        fig.update_yaxes(
            title_text=indicator_label or "Indicator",
            title_font=dict(size=9, color=MUTED),
            tickformat=",.2f",
            row=ind_row, col=1,
        )

    # ── Sub-panels (between stock vol and N225) ───────────────────────────────

    for j, panel in enumerate(sub_panels or []):
        _add_sub_panel(fig, row=sub_row0 + j, dates=stock_dates, panel=panel)

    # ── Corr rows ─────────────────────────────────────────────────────────────

    for i, (code, series) in enumerate(corr_map.items()):
        row = corr_row0 + i

        corr_by_date: dict[str, float | None] = {
            ts.strftime(ts_fmt): (float(v) if pd.notna(v) else None)
            for ts, v in series.items()
        }
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
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=dates)
    fig.update_xaxes(tickvals=ticks, ticktext=ticks, tickangle=-30, tickfont=dict(size=10))
    fig.update_xaxes(rangeslider_visible=False)
    return fig
