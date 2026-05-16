"""Ideas sub-tab — chronological list of memos with deep-link to Daily tab.

Lists every memo (`src.portfolio.models.Memo`) newest-first.  Each row
has an Open button that switches the main tab to ``daily`` and sets
the Daily-tab date picker to the memo's date.  Because the
``daily-date`` picker is already an Input of ``refresh_proposals``,
changing it auto-triggers the proposal/regime refresh with no extra
glue.
"""

from __future__ import annotations

from dash import ALL, Input, Output, callback, callback_context, html, no_update
from loguru import logger

from src.data.db import get_session
from src.portfolio.crud import list_memos
from src.viz.palette import (
    ACCENT, BG, BORDER, CARD_BG, MUTED, TEXT,
)


def _memo_row(memo) -> html.Div:
    snippet = memo.content if len(memo.content) <= 240 else memo.content[:240] + "…"
    ts = memo.created_at.strftime("%Y-%m-%d %H:%M") if memo.created_at else ""
    return html.Div(
        style={
            "background": CARD_BG,
            "border": f"1px solid {BORDER}",
            "borderRadius": "6px",
            "padding": "10px 12px",
            "marginBottom": "8px",
        },
        children=[
            html.Div(
                style={"display": "flex", "justifyContent": "space-between",
                       "alignItems": "center", "marginBottom": "6px"},
                children=[
                    html.Div([
                        html.Span(
                            f"{memo.memo_date}",
                            style={"color": ACCENT, "fontWeight": "600",
                                   "fontSize": "13px"},
                        ),
                        html.Span(
                            f"   created {ts}",
                            style={"color": MUTED, "fontSize": "10px",
                                   "marginLeft": "6px"},
                        ),
                    ]),
                    html.Button(
                        "Open in Daily →",
                        id={"type": "ideas-open-btn", "index": memo.id},
                        n_clicks=0,
                        style={
                            "background": ACCENT, "color": BG,
                            "border": "none", "borderRadius": "4px",
                            "padding": "4px 12px", "cursor": "pointer",
                            "fontWeight": "600", "fontSize": "11px",
                        },
                    ),
                ],
            ),
            html.Div(
                snippet,
                style={"whiteSpace": "pre-wrap", "color": TEXT, "fontSize": "12px"},
            ),
        ],
    )


def layout() -> html.Div:
    return html.Div(
        style={
            "height": "100%", "overflow": "auto", "background": BG,
            "padding": "16px", "boxSizing": "border-box",
        },
        children=[
            html.Div(
                style={"display": "flex", "justifyContent": "space-between",
                       "alignItems": "center", "marginBottom": "12px"},
                children=[
                    html.Span(
                        "Ideas — All Memos",
                        style={"color": TEXT, "fontSize": "14px", "fontWeight": "600"},
                    ),
                    html.Button(
                        "⟳ Refresh",
                        id="ideas-refresh-btn",
                        n_clicks=0,
                        style={
                            "background": "transparent", "color": MUTED,
                            "border": f"1px solid {BORDER}", "borderRadius": "4px",
                            "padding": "4px 10px", "cursor": "pointer",
                            "fontSize": "11px",
                        },
                    ),
                ],
            ),
            html.Div(id="ideas-list"),
        ],
    )


def register_callbacks() -> None:

    @callback(
        Output("ideas-list", "children"),
        Input("ideas-refresh-btn", "n_clicks"),
        Input("analysis-sub-tabs", "value"),
    )
    def refresh_ideas(_n: int, sub_tab: str | None) -> list:
        if sub_tab != "ideas":
            return no_update  # type: ignore[return-value]
        with get_session() as session:
            memos = list_memos(session)
        if not memos:
            return [html.Div(
                "No memos yet. Write one from the Daily tab.",
                style={"color": MUTED, "fontStyle": "italic", "fontSize": "12px"},
            )]
        return [_memo_row(m) for m in memos]

    # Cross-tab navigation: click an Ideas "Open" button → switch main tab to
    # Daily and set the date picker to the memo's date.
    @callback(
        Output("main-tabs", "value"),
        Output("daily-date", "date"),
        Input({"type": "ideas-open-btn", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def open_in_daily(n_clicks_list: list[int]) -> tuple:
        triggered = callback_context.triggered_id
        if not triggered or not any(n for n in (n_clicks_list or []) if n):
            return no_update, no_update
        memo_id = triggered["index"]
        from src.portfolio.models import Memo
        try:
            with get_session() as session:
                m = session.get(Memo, memo_id)
                if m is None:
                    return no_update, no_update
                date_iso = m.memo_date.isoformat()
        except Exception:
            logger.exception("open_in_daily lookup failed for memo id={}", memo_id)
            return no_update, no_update
        return "daily", date_iso
