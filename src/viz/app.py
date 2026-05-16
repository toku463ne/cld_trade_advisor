"""Interactive trade advisor: daily proposals + analysis tools.

Launch:
    uv run --env-file devenv python -m src.viz.app
    # then open http://localhost:8050

Tabs
----
- **Daily**       : today's RegimeSign proposals with regime status and stock charts.
- **Analysis**    : Chart viewer (search a stock + date range) and Sign Benchmark events.
- **Maintenance** : background workers (OHLCV download, sign benchmark coverage).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import dash
from dash import dcc, html

from src.viz import chart_view as _chart_view
from src.viz import ideas as _ideas
from src.viz import portfolio_tab as _portfolio_tab
from src.viz import daily as _daily
from src.viz import maintenance as _maintenance
from src.viz import sign_bench as _sign_bench
from src.viz.charts import (
    BG as _BG, SIDEBAR_BG as _SIDEBAR_BG,
    BORDER as _BORDER, MUTED as _MUTED, ACCENT as _ACCENT,
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

# ── Sub-tab styling ───────────────────────────────────────────────────────────

_SUB_TAB_STYLE: dict[str, Any] = {
    "backgroundColor": _SIDEBAR_BG, "color": _MUTED,
    "border": f"1px solid {_BORDER}", "borderBottom": "none",
    "padding": "5px 18px", "fontSize": "12px",
    "fontFamily": "'Segoe UI', Arial, sans-serif",
}
_SUB_TAB_SELECTED: dict[str, Any] = {
    **_SUB_TAB_STYLE,
    "backgroundColor": _BG, "color": _ACCENT,
    "fontWeight": "600", "borderTop": f"2px solid {_ACCENT}",
}
# Inner height for sub-tab content: subtract main tab bar (44px) + sub-tab bar (~40px)
_INNER_H = "calc(100vh - 84px)"


# ── Layout ────────────────────────────────────────────────────────────────────


def _analysis_layout() -> html.Div:
    """Return the Analysis tab content: Chart + Sign Bench sub-tabs."""
    return html.Div(
        style={
            "fontFamily": "'Segoe UI', Arial, sans-serif",
            "background": _BG,
            "height": "calc(100vh - 44px)",
            "overflow": "hidden",
            "display": "flex",
            "flexDirection": "column",
        },
        children=[
            dcc.Tabs(
                id="analysis-sub-tabs",
                value="chart",
                style={
                    "background": _SIDEBAR_BG,
                    "borderBottom": f"1px solid {_BORDER}",
                    "flexShrink": "0",
                },
                children=[
                    dcc.Tab(
                        label="Chart",
                        value="chart",
                        style=_SUB_TAB_STYLE,
                        selected_style=_SUB_TAB_SELECTED,
                        children=html.Div(
                            style={"height": _INNER_H, "overflow": "hidden"},
                            children=[_chart_view.layout()],
                        ),
                    ),
                    dcc.Tab(
                        label="Sign Bench",
                        value="sign-bench",
                        style=_SUB_TAB_STYLE,
                        selected_style=_SUB_TAB_SELECTED,
                        children=html.Div(
                            style={"height": _INNER_H, "overflow": "hidden"},
                            children=[_sign_bench.layout()],
                        ),
                    ),
                    dcc.Tab(
                        label="Ideas",
                        value="ideas",
                        style=_SUB_TAB_STYLE,
                        selected_style=_SUB_TAB_SELECTED,
                        children=html.Div(
                            style={"height": _INNER_H, "overflow": "hidden"},
                            children=[_ideas.layout()],
                        ),
                    ),
                    dcc.Tab(
                        label="Portfolio",
                        value="portfolio",
                        style=_SUB_TAB_STYLE,
                        selected_style=_SUB_TAB_SELECTED,
                        children=html.Div(
                            style={"height": _INNER_H, "overflow": "hidden"},
                            children=[_portfolio_tab.layout()],
                        ),
                    ),
                ],
            ),
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
        # Global active-account selector — persisted across tab switches.
        # Initialized from the "default" account by app load; updated by
        # the Daily and Portfolio dropdowns.
        dcc.Store(id="active-account-id", storage_type="local"),
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
                    label="Analysis",
                    value="analysis",
                    style=_TAB_STYLE,
                    selected_style=_TAB_SELECTED_STYLE,
                    children=_analysis_layout(),
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


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    import sys
    _daily.register_callbacks()
    _maintenance.register_callbacks()
    _sign_bench.register_callbacks()
    _chart_view.register_callbacks()
    _ideas.register_callbacks()
    _portfolio_tab.register_callbacks()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8050
    print(f"Starting Trade Advisor at http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
