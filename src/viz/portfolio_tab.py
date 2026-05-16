"""Portfolio sub-tab — per-account stats + cross-account comparison.

Shows realized / unrealized P&L for the currently selected account,
plus a one-row summary across all accounts.  Provides a small form
for creating a new account so the operator can spin up separate test
scenarios without leaving the UI.
"""

from __future__ import annotations

from dash import Input, Output, State, callback, callback_context, dcc, html, no_update
from loguru import logger

from src.data.db import get_session
from src.portfolio.crud import create_account, get_account, list_accounts
from src.portfolio.stats import (
    AccountStats,
    compute_account_stats,
    summarize_all_accounts,
)
from src.viz.palette import (
    ACCENT, BG, BORDER, CARD_BG, GREEN, MUTED, RED, TEXT,
)


def _fmt_pct(v: float) -> str:
    return f"{v * 100:+.2f}%"


def _fmt_yen(v: float) -> str:
    sign = "+" if v >= 0 else "−"
    return f"{sign}¥{abs(v):,.0f}"


def _pnl_color(v: float) -> str:
    if v > 0: return GREEN
    if v < 0: return RED
    return TEXT


def _kv_row(label: str, value, *, color: str | None = None) -> html.Div:
    return html.Div(
        style={"display": "flex", "justifyContent": "space-between",
               "fontSize": "12px", "padding": "3px 0",
               "borderBottom": f"1px solid {BORDER}"},
        children=[
            html.Span(label, style={"color": MUTED}),
            html.Span(value, style={"color": color or TEXT, "fontWeight": "600"}),
        ],
    )


def _stats_panel(s: AccountStats) -> html.Div:
    if s.n_realized == 0 and s.n_open == 0 and s.n_taken == 0 and s.n_skipped == 0:
        return html.Div(
            "No positions or reviews recorded for this account yet.",
            style={"color": MUTED, "fontStyle": "italic", "fontSize": "12px",
                   "padding": "12px"},
        )
    realized_color = _pnl_color(s.realized_sum_abs)
    open_color     = _pnl_color(s.open_sum_abs)
    total_color    = _pnl_color(s.total_sum_abs)

    return html.Div(
        style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr",
               "gap": "12px"},
        children=[
            html.Div(
                style={"background": CARD_BG, "border": f"1px solid {BORDER}",
                       "borderRadius": "6px", "padding": "12px"},
                children=[
                    html.Div("Realized (closed)",
                             style={"color": ACCENT, "fontSize": "12px",
                                    "fontWeight": "600", "marginBottom": "6px"}),
                    _kv_row("n closed",       s.n_realized),
                    _kv_row("mean per-trade", _fmt_pct(s.realized_mean_pct)),
                    _kv_row("sum (yen)",      _fmt_yen(s.realized_sum_abs),
                            color=realized_color),
                    _kv_row("win rate",       _fmt_pct(s.win_rate)),
                    _kv_row("avg win",        _fmt_pct(s.avg_win_pct), color=GREEN),
                    _kv_row("avg loss",       _fmt_pct(s.avg_loss_pct), color=RED),
                    _kv_row("max win",        _fmt_pct(s.max_win_pct),  color=GREEN),
                    _kv_row("max loss",       _fmt_pct(s.max_loss_pct), color=RED),
                ],
            ),
            html.Div(
                style={"background": CARD_BG, "border": f"1px solid {BORDER}",
                       "borderRadius": "6px", "padding": "12px"},
                children=[
                    html.Div("Open (mark-to-market)",
                             style={"color": ACCENT, "fontSize": "12px",
                                    "fontWeight": "600", "marginBottom": "6px"}),
                    _kv_row("n open",         s.n_open),
                    _kv_row("mean per-trade", _fmt_pct(s.open_mean_pct)),
                    _kv_row("sum (yen)",      _fmt_yen(s.open_sum_abs),
                            color=open_color),
                    _kv_row("missing price",  s.open_missing_price),
                ],
            ),
            html.Div(
                style={"background": CARD_BG, "border": f"1px solid {BORDER}",
                       "borderRadius": "6px", "padding": "12px"},
                children=[
                    html.Div("Total + Reviews",
                             style={"color": ACCENT, "fontSize": "12px",
                                    "fontWeight": "600", "marginBottom": "6px"}),
                    _kv_row("total P&L (yen)", _fmt_yen(s.total_sum_abs),
                            color=total_color),
                    _kv_row("n taken (reviews)",   s.n_taken),
                    _kv_row("n skipped (reviews)", s.n_skipped),
                ],
            ),
        ],
    )


def _breakdown_table(title: str, by_group: dict) -> html.Div:
    if not by_group:
        return html.Div()
    rows = [
        html.Tr([
            html.Th("group",   style={"textAlign": "left",  "padding": "4px 8px"}),
            html.Th("n",       style={"textAlign": "right", "padding": "4px 8px"}),
            html.Th("mean %",  style={"textAlign": "right", "padding": "4px 8px"}),
            html.Th("sum yen", style={"textAlign": "right", "padding": "4px 8px"}),
        ]),
    ]
    for key in sorted(by_group, key=lambda k: -by_group[k].sum_abs):
        gs = by_group[key]
        rows.append(html.Tr([
            html.Td(key, style={"padding": "4px 8px"}),
            html.Td(str(gs.n), style={"padding": "4px 8px", "textAlign": "right"}),
            html.Td(_fmt_pct(gs.mean_pct),
                    style={"padding": "4px 8px", "textAlign": "right",
                           "color": _pnl_color(gs.mean_pct)}),
            html.Td(_fmt_yen(gs.sum_abs),
                    style={"padding": "4px 8px", "textAlign": "right",
                           "color": _pnl_color(gs.sum_abs)}),
        ]))
    return html.Div(
        style={"background": CARD_BG, "border": f"1px solid {BORDER}",
               "borderRadius": "6px", "padding": "12px", "marginTop": "12px"},
        children=[
            html.Div(title, style={"color": ACCENT, "fontSize": "12px",
                                   "fontWeight": "600", "marginBottom": "6px"}),
            html.Table(rows, style={"width": "100%", "fontSize": "12px"}),
        ],
    )


def _cross_account_table(rows: list[AccountStats]) -> html.Div:
    if not rows:
        return html.Div()
    header = html.Tr([
        html.Th(c, style={"padding": "4px 8px",
                          "textAlign": ("left" if c == "account" else "right")})
        for c in ("account", "n_closed", "mean %", "win%",
                  "n_open", "open yen", "total yen",
                  "taken", "skipped")
    ])
    body = []
    for s in rows:
        body.append(html.Tr([
            html.Td(s.account_name, style={"padding": "4px 8px"}),
            html.Td(str(s.n_realized),       style={"padding": "4px 8px", "textAlign": "right"}),
            html.Td(_fmt_pct(s.realized_mean_pct),
                    style={"padding": "4px 8px", "textAlign": "right",
                           "color": _pnl_color(s.realized_mean_pct)}),
            html.Td(_fmt_pct(s.win_rate),    style={"padding": "4px 8px", "textAlign": "right"}),
            html.Td(str(s.n_open),           style={"padding": "4px 8px", "textAlign": "right"}),
            html.Td(_fmt_yen(s.open_sum_abs),
                    style={"padding": "4px 8px", "textAlign": "right",
                           "color": _pnl_color(s.open_sum_abs)}),
            html.Td(_fmt_yen(s.total_sum_abs),
                    style={"padding": "4px 8px", "textAlign": "right",
                           "color": _pnl_color(s.total_sum_abs), "fontWeight": "600"}),
            html.Td(str(s.n_taken),          style={"padding": "4px 8px", "textAlign": "right"}),
            html.Td(str(s.n_skipped),        style={"padding": "4px 8px", "textAlign": "right"}),
        ]))
    return html.Div(
        style={"background": CARD_BG, "border": f"1px solid {BORDER}",
               "borderRadius": "6px", "padding": "12px", "marginTop": "16px"},
        children=[
            html.Div("All accounts — summary",
                     style={"color": ACCENT, "fontSize": "12px",
                            "fontWeight": "600", "marginBottom": "6px"}),
            html.Table([header] + body, style={"width": "100%", "fontSize": "12px"}),
        ],
    )


def layout() -> html.Div:
    return html.Div(
        style={"height": "100%", "overflow": "auto", "background": BG,
               "padding": "16px", "boxSizing": "border-box"},
        children=[
            # Header: account picker + create form
            html.Div(
                style={"display": "flex", "alignItems": "center",
                       "gap": "12px", "flexWrap": "wrap",
                       "marginBottom": "16px"},
                children=[
                    html.Span("Account:", style={"color": MUTED, "fontSize": "12px"}),
                    dcc.Dropdown(
                        id="portfolio-account-dropdown",
                        placeholder="(loading…)",
                        clearable=False,
                        style={"width": "240px", "fontSize": "12px",
                               "color": "#000"},
                    ),
                    html.Button(
                        "⟳ Refresh",
                        id="portfolio-refresh-btn",
                        n_clicks=0,
                        style={"background": "transparent", "color": MUTED,
                               "border": f"1px solid {BORDER}",
                               "borderRadius": "4px", "padding": "4px 10px",
                               "cursor": "pointer", "fontSize": "11px"},
                    ),
                    html.Div(style={"flex": "1"}),
                    dcc.Input(
                        id="portfolio-new-name",
                        type="text",
                        placeholder="new account name",
                        style={"width": "180px", "fontSize": "12px",
                               "background": BG, "color": TEXT,
                               "border": f"1px solid {BORDER}",
                               "borderRadius": "4px", "padding": "4px 8px"},
                    ),
                    dcc.Input(
                        id="portfolio-new-desc",
                        type="text",
                        placeholder="description (optional)",
                        style={"width": "220px", "fontSize": "12px",
                               "background": BG, "color": TEXT,
                               "border": f"1px solid {BORDER}",
                               "borderRadius": "4px", "padding": "4px 8px"},
                    ),
                    html.Button(
                        "+ Create Account",
                        id="portfolio-create-btn",
                        n_clicks=0,
                        style={"background": ACCENT, "color": BG,
                               "border": "none", "borderRadius": "4px",
                               "padding": "5px 12px", "cursor": "pointer",
                               "fontWeight": "600", "fontSize": "12px"},
                    ),
                    html.Span(id="portfolio-create-msg",
                              style={"fontSize": "11px", "marginLeft": "6px"}),
                ],
            ),
            html.Div(id="portfolio-stats"),
            html.Div(id="portfolio-cross-table"),
        ],
    )


def register_callbacks() -> None:

    # Account dropdown sync (mirrors the Daily one via active-account-id Store)
    @callback(
        Output("portfolio-account-dropdown", "options"),
        Output("portfolio-account-dropdown", "value"),
        Output("active-account-id",          "data", allow_duplicate=True),
        Input("portfolio-account-dropdown",  "value"),
        Input("active-account-id",           "data"),
        Input("portfolio-refresh-btn",       "n_clicks"),
        Input("portfolio-create-btn",        "n_clicks"),
        prevent_initial_call=True,
    )
    def sync_portfolio_account(dropdown_val, store_val, _refresh_n, _create_n):
        with get_session() as session:
            accts = list_accounts(session)
        options = [{"label": a.name, "value": a.id} for a in accts]
        trig = callback_context.triggered_id
        if trig == "portfolio-account-dropdown" and dropdown_val is not None:
            chosen = dropdown_val
        elif store_val is not None and any(o["value"] == store_val for o in options):
            chosen = store_val
        else:
            chosen = accts[0].id if accts else None
        return options, chosen, chosen

    # Create-account button
    @callback(
        Output("portfolio-create-msg",  "children"),
        Output("portfolio-create-msg",  "style"),
        Output("portfolio-new-name",    "value"),
        Output("portfolio-new-desc",    "value"),
        Output("portfolio-refresh-btn", "n_clicks", allow_duplicate=True),
        Input("portfolio-create-btn",   "n_clicks"),
        State("portfolio-new-name",     "value"),
        State("portfolio-new-desc",     "value"),
        State("portfolio-refresh-btn",  "n_clicks"),
        prevent_initial_call=True,
    )
    def create_account_btn(n_clicks, name, desc, refresh_n):
        ok_style  = {"fontSize": "11px", "color": GREEN, "marginLeft": "6px"}
        err_style = {"fontSize": "11px", "color": RED,   "marginLeft": "6px"}
        if not n_clicks:
            return no_update, no_update, no_update, no_update, no_update
        if not (name and name.strip()):
            return "Name required.", err_style, no_update, no_update, no_update
        try:
            with get_session() as session:
                a = create_account(session, name=name.strip(),
                                   description=(desc.strip() if desc else None))
            return f"Created account id={a.id} ({a.name})", ok_style, "", "", (refresh_n or 0) + 1
        except Exception as exc:
            logger.exception("create_account_btn error")
            return f"Error: {exc}", err_style, no_update, no_update, no_update

    # Stats panel + cross-account table
    @callback(
        Output("portfolio-stats",       "children"),
        Output("portfolio-cross-table", "children"),
        Input("portfolio-account-dropdown", "value"),
        Input("portfolio-refresh-btn",      "n_clicks"),
        Input("analysis-sub-tabs",          "value"),
    )
    def refresh_stats(account_id, _n, sub_tab):
        if sub_tab != "portfolio":
            return no_update, no_update
        with get_session() as session:
            if account_id is None:
                return html.Div("No account selected.",
                                style={"color": MUTED, "fontSize": "12px"}), html.Div()
            stats = compute_account_stats(session, int(account_id))
            cross = summarize_all_accounts(session)
        panels = [_stats_panel(stats)]
        if stats.by_sign:
            panels.append(_breakdown_table("Per sign", stats.by_sign))
        if stats.by_corr_mode:
            panels.append(_breakdown_table("Per corr_mode", stats.by_corr_mode))
        return panels, _cross_account_table(cross)
