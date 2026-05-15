"""Maintenance page — Dash layout + callbacks.

Panels
------
1. Stock Sets       — existing StockClusterRun rows
2. OHLCV Download   — background thread to fill 6 years of OHLCV + rebuild regime snapshots
3. Sign Benchmark   — sign × FY coverage grid + "Run Missing" button
4. Task Log         — scrolling output from background workers
"""
from __future__ import annotations

import datetime
import threading
from pathlib import Path
from typing import Any

import dash
from dash import Input, Output, State, callback, dcc, html
from sqlalchemy import func, select

from src.analysis.cluster import FISCAL_YEARS
from src.analysis.models import SignBenchmarkRun, StockClusterMember, StockClusterRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS
from src.analysis.sign_benchmark import compute_sign_code_hash
from src.data.db import get_session
from src.data.models import Ohlcv1d
from src.maintenance.registry import all_valid_sign_names

_OHLCV_MARKER = Path(__file__).resolve().parent.parent.parent / "data" / ".ohlcv_last_download"
from src.viz.palette import (
    ACCENT as _ACCENT, BG as _BG, BORDER as _BORDER, CARD_BG as _CARD_BG,
    GREEN as _GREEN, MUTED as _MUTED, RED as _RED, SIDEBAR_BG as _SIDEBAR_BG,
    TEXT as _TEXT,
)

_N225 = "^N225"
_GSPC = "^GSPC"
_GRAN = "1d"

# ── Shared task state ──────────────────────────────────────────────────────────

_task_lock    = threading.Lock()
_task_log: list[str] = []
_task_running = False


def _log(msg: str) -> None:
    with _task_lock:
        _task_log.append(msg)


def _read_log() -> str:
    with _task_lock:
        return "\n".join(_task_log[-300:])


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _load_stock_sets() -> list[dict]:
    with get_session() as session:
        runs = session.execute(
            select(StockClusterRun).order_by(StockClusterRun.fiscal_year)
        ).scalars().all()
        return [
            {
                "fiscal_year": r.fiscal_year,
                "start":       r.start_dt.date().isoformat() if r.start_dt else "—",
                "end":         r.end_dt.date().isoformat()   if r.end_dt   else "—",
                "n_stocks":    r.n_stocks,
            }
            for r in runs
        ]


def _load_coverage() -> dict[tuple[str, str], tuple[datetime.date, bool]]:
    """Return {(sign_type, stock_set): (latest_date, is_stale)} for existing runs.

    `is_stale` is True when the most recent run's stored `code_hash` differs
    from the current SHA256 of the sign's source module (or when the stored
    hash is NULL — legacy rows count as stale until rerun).
    """
    with get_session() as session:
        # Pick the latest row per (sign, set) by created_at, then read its hash.
        # SQL: SELECT sign_type, stock_set, MAX(created_at), latest-hash
        # but we can't pull the hash off the MAX directly in pure GROUP BY;
        # take a two-step: collect candidates, then for each (sign, set) keep
        # the row with max created_at.
        rows = session.execute(
            select(
                SignBenchmarkRun.sign_type,
                SignBenchmarkRun.stock_set,
                SignBenchmarkRun.created_at,
                SignBenchmarkRun.code_hash,
            )
        ).all()
    latest: dict[tuple[str, str], tuple[datetime.datetime, str | None]] = {}
    for sign_type, stock_set, created_at, code_hash in rows:
        if created_at is None:
            continue
        key = (sign_type, stock_set)
        prev = latest.get(key)
        if prev is None or created_at > prev[0]:
            latest[key] = (created_at, code_hash)

    out: dict[tuple[str, str], tuple[datetime.date, bool]] = {}
    hash_cache: dict[str, str | None] = {}
    for key, (created_at, code_hash) in latest.items():
        sign_type = key[0]
        if sign_type not in hash_cache:
            hash_cache[sign_type] = compute_sign_code_hash(sign_type)
        current_hash = hash_cache[sign_type]
        is_stale = (code_hash is None) or (current_hash is not None and code_hash != current_hash)
        out[key] = (created_at.date(), is_stale)
    return out


# fiscal years that map to RS_FY_CONFIGS stock sets (classified20XX)
_CLUSTER_FY_YEARS: list[str] = sorted(
    {cfg.stock_set.replace("classified", "") for cfg in RS_FY_CONFIGS}
)


def _load_ohlcv_status() -> dict[str, Any]:
    """Return summary of OHLCV bars in the DB + last-download timestamp."""
    with get_session() as session:
        row = session.execute(
            select(func.min(Ohlcv1d.ts), func.max(Ohlcv1d.ts),
                   func.count(func.distinct(Ohlcv1d.stock_code)))
        ).one()
    min_ts, max_ts, n_codes = row
    last_dl: datetime.datetime | None = None
    if _OHLCV_MARKER.exists():
        try:
            txt = _OHLCV_MARKER.read_text(encoding="utf-8").strip()
            last_dl = datetime.datetime.fromisoformat(txt)
        except Exception:
            last_dl = None
    return {
        "period_start": min_ts.date() if min_ts else None,
        "period_end":   max_ts.date() if max_ts else None,
        "n_codes":      int(n_codes or 0),
        "last_download": last_dl,
    }


def _ohlcv_status_block(status: dict[str, Any]) -> html.Div:
    """Two-line label: bars period + last-download timestamp."""
    rows = []
    if status["period_start"] and status["period_end"]:
        rows.append((
            "Bars in DB",
            f"{status['period_start']} → {status['period_end']}  "
            f"({status['n_codes']} codes)",
        ))
    else:
        rows.append(("Bars in DB", "no bars yet"))
    if status["last_download"]:
        dl = status["last_download"]
        dl_local = dl.astimezone() if dl.tzinfo else dl
        rows.append(("Last download", dl_local.strftime("%Y-%m-%d %H:%M %Z").strip()))
    else:
        rows.append(("Last download", "no record (run the button once to start tracking)"))
    return html.Div(style={"fontSize": "12px", "marginBottom": "10px",
                           "display": "grid",
                           "gridTemplateColumns": "auto 1fr",
                           "columnGap": "10px", "rowGap": "2px"},
                    children=[
        c for label, value in rows for c in (
            html.Span(label + ":", style={"color": _MUTED}),
            html.Span(value, style={"fontFamily": "monospace", "color": _TEXT}),
        )
    ])


def _load_cluster_status() -> dict[str, int]:
    """Return {fiscal_year_label: n_stocks} for existing StockClusterRun rows.

    fiscal_year_label is like "classified2023".
    """
    with get_session() as session:
        runs = session.execute(select(StockClusterRun)).scalars().all()
    return {r.fiscal_year: r.n_stocks for r in runs}


# ── Styles ────────────────────────────────────────────────────────────────────

_S_PAGE: dict[str, Any] = {
    "background": _BG,
    "minHeight": "100vh",
    "padding": "16px 20px",
    "fontFamily": "'Segoe UI', Arial, sans-serif",
    "color": _TEXT,
    "boxSizing": "border-box",
}
_S_CARD: dict[str, Any] = {
    "background": _CARD_BG,
    "border": f"1px solid {_BORDER}",
    "borderRadius": "6px",
    "padding": "14px",
    "marginBottom": "14px",
}
_S_H2: dict[str, Any] = {
    "color": _ACCENT,
    "fontSize": "14px",
    "fontWeight": "600",
    "margin": "0 0 10px 0",
}
_S_BTN: dict[str, Any] = {
    "background": _ACCENT,
    "color": "#000",
    "border": "none",
    "borderRadius": "4px",
    "padding": "6px 14px",
    "fontSize": "12px",
    "cursor": "pointer",
    "fontWeight": "600",
    "marginRight": "8px",
}
_S_TH: dict[str, Any] = {
    "padding": "4px 10px",
    "border": f"1px solid {_BORDER}",
    "color": _MUTED,
    "fontSize": "11px",
    "textAlign": "left",
    "background": _SIDEBAR_BG,
}
_S_TD: dict[str, Any] = {
    "padding": "4px 10px",
    "border": f"1px solid {_BORDER}",
    "fontSize": "12px",
}
_S_LOG: dict[str, Any] = {
    "background": "#0d1117",
    "color": "#a9d2a9",
    "fontFamily": "monospace",
    "fontSize": "11px",
    "height": "200px",
    "overflowY": "auto",
    "padding": "8px",
    "border": f"1px solid {_BORDER}",
    "borderRadius": "4px",
    "whiteSpace": "pre-wrap",
}


# ── Table helpers ─────────────────────────────────────────────────────────────

def _sets_table(sets: list[dict]) -> html.Table:
    rows = [
        html.Tr([
            html.Td(s["fiscal_year"], style={**_S_TD, "fontFamily": "monospace"}),
            html.Td(s["start"],       style=_S_TD),
            html.Td(s["end"],         style=_S_TD),
            html.Td(str(s["n_stocks"]), style={**_S_TD, "textAlign": "right"}),
        ])
        for s in sets
    ]
    if not rows:
        rows = [html.Tr([html.Td("No stock sets found.", colSpan=4,
                                  style={**_S_TD, "color": _MUTED})])]
    return html.Table(
        [
            html.Thead(html.Tr([
                html.Th("Set (fiscal_year)", style=_S_TH),
                html.Th("Period Start",      style=_S_TH),
                html.Th("Period End",        style=_S_TH),
                html.Th("# Stocks",          style={**_S_TH, "textAlign": "right"}),
            ])),
            html.Tbody(rows),
        ],
        style={"borderCollapse": "collapse", "fontSize": "12px"},
    )


def _cluster_table(status: dict[str, int]) -> html.Table:
    """Table of cluster FY years with present/missing indicator."""
    rows = []
    for fy in _CLUSTER_FY_YEARS:
        label = f"classified{fy}"
        n = status.get(label)
        ok_cell = html.Td(
            f"✓  {n} stocks" if n else "—",
            style={**_S_TD,
                   "color": _GREEN if n else _MUTED,
                   "background": "#1a3a1a" if n else "#1a1a1a",
                   "textAlign": "center"},
        )
        rows.append(html.Tr([
            html.Td(label, style={**_S_TD, "fontFamily": "monospace"}),
            html.Td(f"FY{int(fy)+1}", style={**_S_TD, "color": _MUTED}),
            ok_cell,
        ]))
    return html.Table(
        [
            html.Thead(html.Tr([
                html.Th("Stock Set",  style=_S_TH),
                html.Th("FY Label",  style=_S_TH),
                html.Th("Status",    style={**_S_TH, "textAlign": "center"}),
            ])),
            html.Tbody(rows),
        ],
        style={"borderCollapse": "collapse", "fontSize": "12px"},
    )


# ── Grid helpers ───────────────────────────────────────────────────────────────

def _grid_cell(value: tuple[datetime.date, bool] | None) -> html.Td:
    if value is None:
        bg, fg, txt = "#3a1a1a", _RED, "✗"
    else:
        date, is_stale = value
        if is_stale:
            bg, fg = "#3a2e0d", "#f0c050"  # amber: code changed since this run
        else:
            bg, fg = "#1a3a1a", _GREEN
        txt = date.isoformat()
    return html.Td(txt, style={**_S_TD, "textAlign": "center", "background": bg, "color": fg,
                                "fontFamily": "monospace"})


def _make_grid(
    sign_names: list[str],
    coverage: dict[tuple[str, str], tuple[datetime.date, bool]],
) -> html.Table:
    fy_labels = [c.label    for c in RS_FY_CONFIGS]
    fy_sets   = [c.stock_set for c in RS_FY_CONFIGS]

    header = html.Tr([
        html.Th("Sign", style={**_S_TH, "minWidth": "100px"}),
        *[html.Th(lbl, style={**_S_TH, "textAlign": "center"}) for lbl in fy_labels],
    ])
    body_rows: list[html.Tr] = []
    for sign in sign_names:
        cells = [html.Td(sign, style={**_S_TD, "fontFamily": "monospace"})]
        for stock_set in fy_sets:
            cells.append(_grid_cell(coverage.get((sign, stock_set))))
        body_rows.append(html.Tr(cells))

    return html.Table(
        [html.Thead(header), html.Tbody(body_rows)],
        style={"borderCollapse": "collapse", "width": "100%", "overflowX": "auto"},
    )


# ── Layout ─────────────────────────────────────────────────────────────────────

def layout() -> html.Div:
    sign_names     = all_valid_sign_names()
    coverage       = _load_coverage()
    sets           = _load_stock_sets()
    cluster_status = _load_cluster_status()

    return html.Div(
        style={"height": "calc(100vh - 44px)", "overflowY": "auto", "background": _BG},
        children=[html.Div(style=_S_PAGE, children=[
        dcc.Interval(id="maint-interval", interval=2000, n_intervals=0, disabled=True),
        dcc.Interval(id="maint-load",     interval=200,  n_intervals=0, max_intervals=1),
        dcc.Store(id="maint-task", data=None),

        html.H3("Maintenance",
                style={"color": _ACCENT, "marginTop": "0", "marginBottom": "16px",
                       "fontSize": "18px"}),

        # ── Stock Sets ──────────────────────────────────────────────────────
        html.Div(style=_S_CARD, children=[
            html.H2("Stock Sets", style=_S_H2),
            html.Div(id="maint-sets", children=_sets_table(sets)),
        ]),

        # ── OHLCV Download ──────────────────────────────────────────────────
        html.Div(style=_S_CARD, children=[
            html.H2("OHLCV Download", style=_S_H2),
            html.P(
                "Fetches 6 years of daily bars for ^N225, ^GSPC, and all Nikkei 225 stocks. "
                "After download, rebuilds N225 regime snapshots.",
                style={"fontSize": "12px", "color": _MUTED, "margin": "0 0 10px 0"},
            ),
            html.Div(id="maint-ohlcv-status",
                     children=_ohlcv_status_block(_load_ohlcv_status())),
            html.Button("⬇  Download OHLCV", id="maint-dl-btn", style=_S_BTN),
        ]),

        # ── Cluster Analysis ────────────────────────────────────────────────
        html.Div(style=_S_CARD, children=[
            html.H2("Cluster Analysis", style=_S_H2),
            html.P(
                "Runs pair-correlation and agglomerative clustering for selected fiscal years. "
                "Populates stock_cluster_runs + stock_cluster_members. "
                "OHLCV must be downloaded first.",
                style={"fontSize": "12px", "color": _MUTED, "margin": "0 0 10px 0"},
            ),
            html.Div(id="maint-cluster", children=_cluster_table(cluster_status)),
            html.Div(style={"marginTop": "12px", "display": "flex", "alignItems": "flex-start",
                            "gap": "12px", "flexWrap": "wrap"}, children=[
                html.Div(children=[
                    html.Div("Select fiscal years to (re-)run:", style={"fontSize": "11px",
                             "color": _MUTED, "marginBottom": "6px"}),
                    dcc.Checklist(
                        id="maint-cluster-fy-check",
                        options=[
                            {"label": f"classified{fy}  (FY{int(fy)+1})", "value": fy}
                            for fy in _CLUSTER_FY_YEARS
                        ],
                        value=[],
                        style={"fontSize": "12px"},
                        inputStyle={"marginRight": "6px", "accentColor": _ACCENT},
                        labelStyle={"display": "flex", "alignItems": "center",
                                    "marginBottom": "4px", "color": _TEXT,
                                    "fontFamily": "monospace", "cursor": "pointer"},
                    ),
                ]),
                html.Div(style={"marginTop": "24px"}, children=[
                    html.Button("⚙  Run Clustering", id="maint-cluster-btn", style=_S_BTN),
                ]),
            ]),
        ]),

        # ── Benchmark Grid ──────────────────────────────────────────────────
        html.Div(style=_S_CARD, children=[
            html.Div(style={"display": "flex", "alignItems": "center", "marginBottom": "10px"}, children=[
                html.H2("Sign Benchmark Coverage  (date = fresh; amber = stale; ✗ = missing)",
                        style={**_S_H2, "marginBottom": "0", "flex": "1"}),
                html.Button("⟳ Refresh", id="maint-grid-refresh-btn",
                            style={**_S_BTN, "background": "transparent",
                                   "color": _MUTED, "border": f"1px solid {_BORDER}",
                                   "marginRight": "0"}),
            ]),
            html.Div(id="maint-grid", style={"overflowX": "auto"},
                     children=_make_grid(sign_names, coverage)),
            html.Div(style={"marginTop": "10px"}, children=[
                html.Button("▶  Run Missing & Stale Benchmarks", id="maint-bench-btn", style=_S_BTN),
            ]),
        ]),

        # ── Log ─────────────────────────────────────────────────────────────
        html.Div(style=_S_CARD, children=[
            html.H2("Task Log", style=_S_H2),
            html.Pre(id="maint-log", style=_S_LOG, children=""),
        ]),
    ])])


# ── Background workers ─────────────────────────────────────────────────────────

def _run_ohlcv_download() -> None:
    global _task_running
    try:
        from src.analysis.sign_regime_analysis import phase_build
        from src.data.collect import OHLCVCollector

        today    = datetime.date.today()
        start_dt = datetime.datetime(today.year - 6, today.month, today.day,
                                     tzinfo=datetime.timezone.utc)
        end_dt   = datetime.datetime(today.year, today.month, today.day,
                                     tzinfo=datetime.timezone.utc)

        _log(f"[OHLCV] Window: {start_dt.date()} → {end_dt.date()}")

        from src.data.nikkei225 import load_or_fetch
        n225_codes = load_or_fetch()
        all_codes = [_N225, _GSPC] + sorted(set(n225_codes))
        _log(f"[OHLCV] {len(all_codes)} codes (N225 constituents + indices)")

        for code in all_codes:
            try:
                with get_session() as session:
                    collector = OHLCVCollector(session)
                    n = collector.collect(code, _GRAN, start_dt, end_dt)
                _log(f"[OHLCV] {code}: +{n} rows")
            except Exception as exc:
                _log(f"[OHLCV] {code}: ERROR — {exc}")

        _log("[OHLCV] Rebuilding N225 regime snapshots …")
        try:
            phase_build()
            _log("[OHLCV] Regime snapshots updated.")
        except Exception as exc:
            _log(f"[OHLCV] Regime build error: {exc}")

        try:
            _OHLCV_MARKER.parent.mkdir(parents=True, exist_ok=True)
            _OHLCV_MARKER.write_text(
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                encoding="utf-8",
            )
        except Exception as exc:
            _log(f"[OHLCV] marker write failed: {exc}")

        _log("[OHLCV] Done.")
    finally:
        with _task_lock:
            _task_running = False


def _run_benchmarks() -> None:
    global _task_running
    try:
        from src.analysis.sign_benchmark import run_benchmark

        sign_names = all_valid_sign_names()
        coverage   = _load_coverage()

        targets: list[tuple[str, Any, str]] = []
        for cfg in RS_FY_CONFIGS:
            for sign in sign_names:
                key = (sign, cfg.stock_set)
                if key not in coverage:
                    targets.append((sign, cfg, "missing"))
                elif coverage[key][1]:  # is_stale
                    targets.append((sign, cfg, "stale"))
        n_missing = sum(1 for _, _, why in targets if why == "missing")
        n_stale   = sum(1 for _, _, why in targets if why == "stale")
        _log(f"[BENCH] {len(targets)} (sign, FY) pairs to run: "
             f"{n_missing} missing + {n_stale} stale.")

        for sign, cfg, why in targets:
            _log(f"[BENCH] {sign} × {cfg.stock_set} ({cfg.label})  [{why}] …")
            try:
                with get_session() as session:
                    codes: list[str] = session.execute(
                        select(StockClusterMember.stock_code)
                        .join(StockClusterRun)
                        .where(StockClusterRun.fiscal_year == cfg.stock_set)
                    ).scalars().all()

                if not codes:
                    _log(f"[BENCH] {cfg.stock_set}: no members — skip")
                    continue

                # Stale rerun: delete the old run rows (events cascade) so the
                # downstream ranking doesn't pool stale + fresh events.
                if why == "stale":
                    with get_session() as session:
                        old_runs = session.execute(
                            select(SignBenchmarkRun)
                            .where(SignBenchmarkRun.sign_type == sign,
                                   SignBenchmarkRun.stock_set == cfg.stock_set)
                        ).scalars().all()
                        for r in old_runs:
                            session.delete(r)
                        session.commit()
                    _log(f"[BENCH] {sign} × {cfg.stock_set}: deleted {len(old_runs)} old run(s)")

                start_dt = datetime.datetime(
                    cfg.start.year, cfg.start.month, cfg.start.day,
                    tzinfo=datetime.timezone.utc,
                )
                end_dt = datetime.datetime(
                    cfg.end.year, cfg.end.month, cfg.end.day,
                    tzinfo=datetime.timezone.utc,
                )
                with get_session() as session:
                    run_id = run_benchmark(
                        session, sign, list(codes), cfg.stock_set, start_dt, end_dt,
                    )
                _log(f"[BENCH] {sign} × {cfg.stock_set}: run_id={run_id} ✓")
            except Exception as exc:
                _log(f"[BENCH] {sign} × {cfg.stock_set}: ERROR — {exc}")

        _log("[BENCH] Done.")
    finally:
        with _task_lock:
            _task_running = False


def _run_cluster_analysis(fiscal_years: list[str]) -> None:
    global _task_running
    try:
        from src.analysis.cluster import run_pipeline
        if not fiscal_years:
            _log("[CLUSTER] No fiscal years selected.")
            return
        _log(f"[CLUSTER] Running for: {', '.join(fiscal_years)}")
        for fy in fiscal_years:
            _log(f"[CLUSTER] ── fiscal year {fy} (collect + corr + cluster) ──")
            try:
                run_pipeline(fiscal_year=fy, collect=True, run_corr=True)
                _log(f"[CLUSTER] {fy}: done ✓")
            except Exception as exc:
                _log(f"[CLUSTER] {fy}: ERROR — {exc}")
        _log("[CLUSTER] Done.")
    finally:
        with _task_lock:
            _task_running = False


# ── Callbacks ──────────────────────────────────────────────────────────────────

def register_callbacks() -> None:

    @callback(
        Output("maint-interval", "disabled"),
        Output("maint-task",     "data"),
        Input("maint-dl-btn",    "n_clicks"),
        prevent_initial_call=True,
    )
    def _start_ohlcv(n: int | None) -> tuple:
        global _task_running
        with _task_lock:
            if _task_running:
                return True, dash.no_update
            _task_running = True
            _task_log.clear()
        threading.Thread(target=_run_ohlcv_download, daemon=True).start()
        return False, "ohlcv"

    @callback(
        Output("maint-interval", "disabled",  allow_duplicate=True),
        Output("maint-task",     "data",      allow_duplicate=True),
        Input("maint-bench-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _start_benchmarks(n: int | None) -> tuple:
        global _task_running
        with _task_lock:
            if _task_running:
                return True, dash.no_update
            _task_running = True
            _task_log.clear()
        threading.Thread(target=_run_benchmarks, daemon=True).start()
        return False, "benchmarks"

    @callback(
        Output("maint-interval", "disabled",  allow_duplicate=True),
        Output("maint-task",     "data",      allow_duplicate=True),
        Input("maint-cluster-btn", "n_clicks"),
        State("maint-cluster-fy-check", "value"),
        prevent_initial_call=True,
    )
    def _start_cluster(n: int | None, fy_list: list[str] | None) -> tuple:
        global _task_running
        with _task_lock:
            if _task_running:
                return True, dash.no_update
            _task_running = True
            _task_log.clear()
        years = fy_list or []
        threading.Thread(target=_run_cluster_analysis, args=(years,), daemon=True).start()
        return False, "cluster"

    @callback(
        Output("maint-log",      "children"),
        Output("maint-grid",     "children"),
        Output("maint-sets",     "children"),
        Output("maint-cluster",  "children"),
        Output("maint-ohlcv-status", "children"),
        Output("maint-interval", "disabled",  allow_duplicate=True),
        Input("maint-interval",  "n_intervals"),
        prevent_initial_call=True,
    )
    def _poll(n: int) -> tuple:
        log = _read_log()
        with _task_lock:
            still_running = _task_running
        sign_names = all_valid_sign_names()
        coverage   = _load_coverage()
        grid    = _make_grid(sign_names, coverage)
        sets    = _sets_table(_load_stock_sets())
        cluster = _cluster_table(_load_cluster_status())
        ohlcv   = _ohlcv_status_block(_load_ohlcv_status())
        return log, grid, sets, cluster, ohlcv, not still_running

    def _refresh_all() -> tuple:
        sign_names = all_valid_sign_names()
        coverage   = _load_coverage()
        return (
            _make_grid(sign_names, coverage),
            _sets_table(_load_stock_sets()),
            _cluster_table(_load_cluster_status()),
            _ohlcv_status_block(_load_ohlcv_status()),
        )

    @callback(
        Output("maint-grid",    "children"),
        Output("maint-sets",    "children"),
        Output("maint-cluster", "children"),
        Output("maint-ohlcv-status", "children"),
        Input("maint-load",     "n_intervals"),
    )
    def _on_page_load(n: int) -> tuple:
        return _refresh_all()

    @callback(
        Output("maint-grid",    "children", allow_duplicate=True),
        Output("maint-sets",    "children", allow_duplicate=True),
        Output("maint-cluster", "children", allow_duplicate=True),
        Output("maint-ohlcv-status", "children", allow_duplicate=True),
        Input("maint-grid-refresh-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _on_refresh_btn(n: int | None) -> tuple:
        return _refresh_all()
