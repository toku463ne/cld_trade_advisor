"""Moving Correlation tab — integrated into the main viz app.

Computes rolling return-correlation on-the-fly from OHLCV in the DB
(no pre-computed moving_corr table required — just needs fresh OHLCV).
"""

from __future__ import annotations

import datetime
from typing import Any

import pandas as pd
from dash import Input, Output, callback, dcc, html
from loguru import logger

from src.analysis.peak_corr import MAJOR_INDICATORS
from src.data.db import get_session
from src.indicators import (
    calc_atr, calc_bb, calc_ema, calc_ichimoku, calc_macd, calc_rsi, calc_sma,
    compute_moving_corr, detect_peaks,
)
from src.simulator.cache import DataCache
from src.viz.charts import BG, CARD_BG, BORDER, MUTED, ACCENT, TEXT, SIDEBAR_BG
from src.viz.charts import build_moving_corr_figure, empty_figure

# ── Style constants ───────────────────────────────────────────────────────────

_S_LABEL: dict[str, Any] = {
    "color": MUTED, "fontSize": "11px",
    "textTransform": "uppercase", "letterSpacing": "0.5px",
    "marginTop": "14px", "marginBottom": "4px", "display": "block",
}
_S_IND_BLOCK: dict[str, Any] = {
    "padding": "8px 4px",
    "borderBottom": f"1px solid {BORDER}",
}
_S_IND_PARAM_WRAP: dict[str, Any] = {
    "paddingLeft": "16px",
    "display": "flex", "flexDirection": "column", "gap": "4px",
    "marginTop": "4px",
}
_S_PARAM_ROW: dict[str, Any] = {
    "display": "flex", "justifyContent": "space-between", "alignItems": "center",
}
_S_PARAM_LABEL: dict[str, Any] = {"color": MUTED, "fontSize": "10px"}
_S_PARAM_INPUT: dict[str, Any] = {
    "background": CARD_BG, "color": TEXT,
    "border": f"1px solid {BORDER}", "borderRadius": "3px",
    "padding": "3px 5px", "width": "56px", "fontSize": "11px",
    "textAlign": "right",
}
_S_CARD: dict[str, Any] = {
    "background": CARD_BG, "border": f"1px solid {BORDER}",
    "borderRadius": "6px", "padding": "12px", "marginTop": "12px",
    "color": TEXT, "fontSize": "13px",
}
_INPUT_STYLE: dict[str, Any] = {
    "background": CARD_BG, "color": TEXT,
    "border": f"1px solid {BORDER}", "borderRadius": "4px",
    "padding": "8px", "width": "100%", "fontSize": "13px",
    "boxSizing": "border-box",
}

_STOCK_CODES_INI = "configs/stock_codes.ini"
_MC_CHART_HEIGHT_BASE = 620 + 110 * len(MAJOR_INDICATORS)

# ── Overlay builders (same as corr_ui.py) ────────────────────────────────────

def _sma_overlays(closes: list[float], period: int) -> list[dict]:
    return [{"label": f"SMA{period}", "y": calc_sma(closes, period),
             "color": "#F0C040", "dash": "solid", "width": 1.2}]

def _ema_overlays(closes: list[float], period: int) -> list[dict]:
    return [{"label": f"EMA{period}", "y": calc_ema(closes, period),
             "color": "#29B6F6", "dash": "solid", "width": 1.2}]

def _bb_overlays(closes: list[float], period: int, nstd: float) -> list[dict]:
    lower, mid, upper = calc_bb(closes, period, nstd)
    cb = "rgba(100,100,200,0.35)"
    return [
        {"label": f"BB{period} lo",  "y": lower, "color": cb, "dash": "dot", "width": 1.0},
        {"label": f"BB{period} hi",  "y": upper, "color": cb, "dash": "dot", "width": 1.0,
         "fill": "tonexty", "fillcolor": "rgba(100,100,200,0.07)"},
        {"label": f"BB{period} mid", "y": mid,   "color": "rgba(100,100,200,0.7)",
         "dash": "dash", "width": 1.0},
    ]

def _ichi_overlays(
    highs: list[float], lows: list[float], closes: list[float],
    tenkan: int, kijun: int, senkou_b: int, displacement: int,
) -> list[dict]:
    import math
    raw = calc_ichimoku(highs, lows, closes, tenkan, kijun, senkou_b, displacement)
    n   = len(closes)
    d   = displacement

    def _fwd(src: list[float]) -> list[float | None]:
        out: list[float | None] = [None] * n
        for i in range(n):
            j = i + d
            if j < n:
                v = src[i]
                out[j] = None if math.isnan(v) else v
        return out

    def _back(src: list[float]) -> list[float | None]:
        out: list[float | None] = [None] * n
        for i in range(d, n):
            v = src[i]
            out[i - d] = None if math.isnan(v) else v
        return out

    sa = _fwd(raw["senkou_a"])   # type: ignore[arg-type]
    sb = _fwd(raw["senkou_b"])   # type: ignore[arg-type]
    ch = _back(raw["chikou"])    # type: ignore[arg-type]
    return [
        {"label": f"Tenkan({tenkan})", "y": raw["tenkan"],
         "color": "#EF5350", "dash": "solid", "width": 1.0},
        {"label": f"Kijun({kijun})",   "y": raw["kijun"],
         "color": "#1E88E5", "dash": "solid", "width": 1.5},
        {"label": "Senkou A", "y": sa,
         "color": "rgba(0,200,83,0.5)", "dash": "solid", "width": 0.8},
        {"label": "Senkou B", "y": sb,
         "color": "rgba(229,57,53,0.5)", "dash": "solid", "width": 0.8,
         "fill": "tonexty", "fillcolor": "rgba(120,120,120,0.12)"},
        {"label": "Chikou",   "y": ch,
         "color": "rgba(180,180,180,0.6)", "dash": "dot", "width": 1.0},
    ]

# ── Sidebar widget ────────────────────────────────────────────────────────────

def _ind_item(
    check_id: str,
    label: str,
    params: list[tuple[str, str, int | float, int | float, int | float]],
) -> html.Div:
    param_rows = [
        html.Div([
            html.Span(plabel, style=_S_PARAM_LABEL),
            dcc.Input(
                id=pid, type="number",
                value=default, min=pmin, max=pmax,
                step=1 if isinstance(default, int) else 0.5,
                debounce=True, style=_S_PARAM_INPUT,
            ),
        ], style=_S_PARAM_ROW)
        for pid, plabel, default, pmin, pmax in params
    ]
    return html.Div([
        dcc.Checklist(
            id=check_id,
            options=[{"label": f"  {label}", "value": "on"}],
            value=[],
            style={"fontSize": "12px", "cursor": "pointer"},
            labelStyle={"color": "#e6edf3"},
            inputStyle={"marginRight": "4px"},
        ),
        html.Div(param_rows, style=_S_IND_PARAM_WRAP),
    ], style=_S_IND_BLOCK)

# ── Stock group helpers ───────────────────────────────────────────────────────

_MAJOR_SET: set[str] = set(MAJOR_INDICATORS)


def _all_stock_group_options() -> list[dict]:
    from sqlalchemy import select
    from src.analysis.models import StockClusterRun
    opts: list[dict] = []
    try:
        with get_session() as s:
            runs = s.execute(
                select(StockClusterRun.fiscal_year, StockClusterRun.n_clusters)
                .order_by(StockClusterRun.created_at.desc())
            ).all()
        for r in runs:
            opts.append({
                "label": f"{r.fiscal_year}  ({r.n_clusters} clusters, DB)",
                "value": f"db:{r.fiscal_year}",
            })
    except Exception:
        pass
    try:
        import configparser
        cfg = configparser.ConfigParser(allow_no_value=True)
        cfg.read(_STOCK_CODES_INI)
        for section in cfg.sections():
            n = len([k for k in cfg[section]])
            opts.append({"label": f"{section}  ({n} stocks, INI)", "value": f"ini:{section}"})
    except Exception:
        pass
    return opts


def _stock_options_for_group(group_key: str | None) -> list[dict]:
    if not group_key:
        return []
    try:
        source, name = group_key.split(":", 1)
    except ValueError:
        return []
    if source == "db":
        try:
            from sqlalchemy import select
            from src.analysis.models import StockClusterMember, StockClusterRun
            from src.data.models import Stock
            with get_session() as s:
                run = s.execute(
                    select(StockClusterRun).where(StockClusterRun.fiscal_year == name)
                ).scalar_one_or_none()
                if run is None:
                    return []
                members = s.execute(
                    select(StockClusterMember.stock_code)
                    .where(StockClusterMember.run_id == run.id,
                           StockClusterMember.is_representative.is_(True))
                    .order_by(StockClusterMember.stock_code)
                ).scalars().all()
                codes = [c for c in members if c not in _MAJOR_SET]
                name_rows = s.execute(
                    select(Stock.code, Stock.name).where(Stock.code.in_(codes))
                ).all()
                stock_names = {r.code: r.name for r in name_rows}
            return [{"label": f"{c}  {stock_names.get(c, '')}", "value": c} for c in codes]
        except Exception:
            return []
    if source == "ini":
        try:
            from src.config import load_stock_codes
            codes = [c for c in load_stock_codes(_STOCK_CODES_INI, name) if c not in _MAJOR_SET]
            return [{"label": c, "value": c} for c in codes]
        except Exception:
            return []
    return []

# ── Layout ────────────────────────────────────────────────────────────────────

def layout() -> html.Div:
    _sidebar = html.Div(
        style={
            "flex": "0 0 180px",
            "background": CARD_BG,
            "border": f"1px solid {BORDER}",
            "borderRadius": "6px",
            "padding": "8px 10px",
            "alignSelf": "flex-start",
            "marginTop": "12px",
        },
        children=[
            html.Span("Indicators", style={**_S_LABEL, "marginTop": "4px"}),
            _ind_item("mc-ind-sma-check", "SMA",   [("mc-ind-sma-period", "Period", 20, 2, 500)]),
            _ind_item("mc-ind-ema-check", "EMA",   [("mc-ind-ema-period", "Period", 20, 2, 500)]),
            _ind_item("mc-ind-bb-check",  "Bollinger", [
                ("mc-ind-bb-period", "Period", 20,  2, 500),
                ("mc-ind-bb-std",    "Std Dev",  2.0, 0.5, 5.0),
            ]),
            _ind_item("mc-ind-ichi-check", "Ichimoku", [
                ("mc-ind-ichi-tenkan",   "Tenkan",    9, 1, 100),
                ("mc-ind-ichi-kijun",    "Kijun",    26, 1, 200),
                ("mc-ind-ichi-senkou-b", "Senkou B", 52, 1, 300),
                ("mc-ind-ichi-displace", "Displace", 26, 1, 100),
            ]),
            _ind_item("mc-ind-rsi-check",  "RSI",  [("mc-ind-rsi-period",  "Period", 14, 2, 100)]),
            _ind_item("mc-ind-macd-check", "MACD", [
                ("mc-ind-macd-fast", "Fast",   12, 2, 200),
                ("mc-ind-macd-slow", "Slow",   26, 2, 500),
                ("mc-ind-macd-sig",  "Signal",  9, 2, 100),
            ]),
            _ind_item("mc-ind-atr-check", "ATR",    [("mc-ind-atr-period", "Period", 14, 2, 200)]),
            _ind_item("mc-ind-zz-check",  "Zigzag", [
                ("mc-ind-zz-size", "Size", 5, 1, 20),
                ("mc-ind-zz-mid",  "Mid",  2, 0, 10),
            ]),
        ],
    )

    _controls = html.Div(
        style={"display": "flex", "gap": "16px", "alignItems": "flex-end", "flexWrap": "wrap"},
        children=[
            html.Div(style={"flex": "0 0 240px"}, children=[
                html.Span("Stock group", style=_S_LABEL),
                dcc.Dropdown(
                    id="mc-group-dd", options=_all_stock_group_options(),
                    value=None, clearable=True, searchable=False,
                    placeholder="Select group…",
                ),
            ]),
            html.Div(style={"flex": "0 0 220px"}, children=[
                html.Span("Stock", style=_S_LABEL),
                dcc.Dropdown(
                    id="mc-stock-dd", options=[], value=None,
                    clearable=True, searchable=True, placeholder="Select…",
                ),
            ]),
            html.Div(style={"flex": "0 0 160px"}, children=[
                html.Span("or type code", style=_S_LABEL),
                dcc.Input(
                    id="mc-stock-input", type="text",
                    debounce=True, placeholder="e.g. 9101.T",
                    style=_INPUT_STYLE,
                ),
            ]),
            html.Div(style={"flex": "0 0 120px"}, children=[
                html.Span("Window (bars)", style=_S_LABEL),
                dcc.Input(
                    id="mc-window", type="number",
                    value=20, min=5, max=500, step=1,
                    debounce=True, style=_INPUT_STYLE,
                ),
            ]),
            html.Div(style={"flex": "0 0 110px"}, children=[
                html.Span("Granularity", style=_S_LABEL),
                dcc.Dropdown(
                    id="mc-gran",
                    options=[{"label": "Daily", "value": "1d"},
                             {"label": "Hourly", "value": "1h"}],
                    value="1d", clearable=False,
                ),
            ]),
            html.Div(style={"flex": "0 0 140px"}, children=[
                html.Span("Start", style=_S_LABEL),
                dcc.Input(
                    id="mc-start", type="text",
                    value="2022-01-01", debounce=True,
                    placeholder="YYYY-MM-DD", style=_INPUT_STYLE,
                ),
            ]),
            html.Div(style={"flex": "0 0 140px"}, children=[
                html.Span("End (blank = today)", style=_S_LABEL),
                dcc.Input(
                    id="mc-end", type="text",
                    value="", debounce=True,
                    placeholder="YYYY-MM-DD", style=_INPUT_STYLE,
                ),
            ]),
        ],
    )

    _chart_area = html.Div(
        style={"flex": "1", "minWidth": "0"},
        children=[
            html.Div(id="mc-status", style={**_S_CARD, "marginTop": "12px",
                                            "marginBottom": "8px"}),
            dcc.Graph(
                id="mc-chart",
                figure=empty_figure("Select a stock to view moving correlation"),
                style={"height": f"{_MC_CHART_HEIGHT_BASE}px"},
                config={"scrollZoom": True, "displayModeBar": True,
                        "modeBarButtonsToRemove": ["lasso2d", "select2d"]},
            ),
        ],
    )

    return html.Div(
        style={
            "fontFamily": "'Segoe UI', Arial, sans-serif",
            "background": BG, "height": "calc(100vh - 44px)",
            "overflowY": "auto", "padding": "16px 24px",
        },
        children=[
            html.H3("Moving Correlation vs Major Indices",
                    style={"color": ACCENT, "margin": "0 0 12px 0"}),
            _controls,
            html.Div(
                style={"display": "flex", "gap": "16px", "alignItems": "flex-start"},
                children=[_sidebar, _chart_area],
            ),
        ],
    )

# ── Callbacks ─────────────────────────────────────────────────────────────────

def _parse_date(s: str | None, fallback: datetime.datetime) -> datetime.datetime:
    if not s or not s.strip():
        return fallback
    try:
        dt = datetime.datetime.fromisoformat(s.strip())
        return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return fallback


def register_callbacks() -> None:

    @callback(
        Output("mc-stock-dd", "options"),
        Output("mc-stock-dd", "value"),
        Input("mc-group-dd",  "value"),
    )
    def _update_stock_list(group_key: str | None) -> tuple:
        return _stock_options_for_group(group_key), None

    @callback(
        Output("mc-chart",  "figure"),
        Output("mc-chart",  "style"),
        Output("mc-status", "children"),
        Input("mc-stock-dd",    "value"),
        Input("mc-stock-input", "value"),
        Input("mc-window",      "value"),
        Input("mc-gran",        "value"),
        Input("mc-start",       "value"),
        Input("mc-end",         "value"),
        # indicator checkboxes + params
        Input("mc-ind-sma-check",  "value"),
        Input("mc-ind-sma-period", "value"),
        Input("mc-ind-ema-check",  "value"),
        Input("mc-ind-ema-period", "value"),
        Input("mc-ind-bb-check",      "value"),
        Input("mc-ind-bb-period",     "value"),
        Input("mc-ind-bb-std",        "value"),
        Input("mc-ind-ichi-check",    "value"),
        Input("mc-ind-ichi-tenkan",   "value"),
        Input("mc-ind-ichi-kijun",    "value"),
        Input("mc-ind-ichi-senkou-b", "value"),
        Input("mc-ind-ichi-displace", "value"),
        Input("mc-ind-rsi-check",  "value"),
        Input("mc-ind-rsi-period", "value"),
        Input("mc-ind-macd-check", "value"),
        Input("mc-ind-macd-fast",  "value"),
        Input("mc-ind-macd-slow",  "value"),
        Input("mc-ind-macd-sig",   "value"),
        Input("mc-ind-atr-check",  "value"),
        Input("mc-ind-atr-period", "value"),
        Input("mc-ind-zz-check",   "value"),
        Input("mc-ind-zz-size",    "value"),
        Input("mc-ind-zz-mid",     "value"),
    )
    def _update_chart(
        stock_dd: str | None, stock_input: str | None,
        window: int | None, gran: str | None,
        start_str: str | None, end_str: str | None,
        sma_check: list, sma_period: int | None,
        ema_check: list, ema_period: int | None,
        bb_check: list, bb_period: int | None, bb_std: float | None,
        ichi_check: list, ichi_tenkan: int | None, ichi_kijun: int | None,
        ichi_senkou_b: int | None, ichi_displace: int | None,
        rsi_check: list, rsi_period: int | None,
        macd_check: list, macd_fast: int | None, macd_slow: int | None, macd_sig: int | None,
        atr_check: list, atr_period: int | None,
        zz_check: list, zz_size: int | None, zz_mid: int | None,
    ) -> tuple:
        default_style = {"height": f"{_MC_CHART_HEIGHT_BASE}px"}
        stock = (stock_input or "").strip() or stock_dd
        if not stock:
            return empty_figure("Select a stock to view moving correlation"), default_style, "No stock selected."

        window = int(window) if window else 20
        gran   = gran or "1d"
        now    = datetime.datetime.now(datetime.timezone.utc)
        start  = _parse_date(start_str, datetime.datetime(2022, 1, 1, tzinfo=datetime.timezone.utc))
        end    = _parse_date(end_str, now)

        try:
            with get_session() as session:
                cache = DataCache(stock, gran)
                cache.load(session, start, end)

                if not cache.bars:
                    msg = f"No OHLCV data found for {stock} ({gran}) in DB."
                    return empty_figure(msg), default_style, msg

                inds_to_load   = ["^N225"] if gran == "1h" else list(MAJOR_INDICATORS)
                indicator_map: dict[str, pd.Series] = {}
                first_ind_cache: DataCache | None = None
                first_ind_code:  str = ""
                for ind_code in inds_to_load:
                    cache_ind = DataCache(ind_code, gran)
                    cache_ind.load(session, start, end)
                    if not cache_ind.bars:
                        continue
                    if first_ind_cache is None:
                        first_ind_cache = cache_ind
                        first_ind_code  = ind_code
                    if gran == "1h":
                        indicator_map[ind_code] = pd.Series({b.dt: b.close for b in cache_ind.bars})
                    else:
                        indicator_map[ind_code] = pd.Series({b.dt.date(): b.close for b in cache_ind.bars})
        except Exception as exc:
            logger.exception("mc chart load error for {}", stock)
            msg = f"Error loading data: {exc}"
            return empty_figure(msg), default_style, msg

        if not indicator_map:
            msg = "No indicator data in DB. Run the data collector first."
            return empty_figure(msg), default_style, msg

        if gran == "1h":
            stock_series = pd.Series({b.dt: b.close for b in cache.bars})
        else:
            stock_series = pd.Series({b.dt.date(): b.close for b in cache.bars})
        corr_map = compute_moving_corr(stock_series, indicator_map, window=window)

        bars     = cache.bars
        s_closes = [b.close for b in bars]
        s_highs  = [b.high  for b in bars]
        s_lows   = [b.low   for b in bars]

        ind_bars = first_ind_cache.bars if first_ind_cache else []
        i_closes = [b.close for b in ind_bars]
        i_highs  = [b.high  for b in ind_bars]
        i_lows   = [b.low   for b in ind_bars]

        sma_p   = int(sma_period    or 20)
        ema_p   = int(ema_period    or 20)
        bb_p    = int(bb_period     or 20)
        bb_s    = float(bb_std      or 2.0)
        ichi_t  = int(ichi_tenkan   or 9)
        ichi_k  = int(ichi_kijun    or 26)
        ichi_sb = int(ichi_senkou_b or 52)
        ichi_d  = int(ichi_displace or 26)
        rsi_p   = int(rsi_period    or 14)
        mf      = int(macd_fast     or 12)
        ms_     = int(macd_slow     or 26)
        msig    = int(macd_sig      or 9)
        atr_p   = int(atr_period    or 14)
        zz_s    = int(zz_size       or 5)
        zz_m    = int(zz_mid        or 2)

        stock_overlays: list[dict] = []
        ind_overlays:   list[dict] = []
        sub_panels:     list[dict] = []

        if sma_check:
            stock_overlays += _sma_overlays(s_closes, sma_p)
            if i_closes:
                ind_overlays += _sma_overlays(i_closes, sma_p)
        if ema_check:
            stock_overlays += _ema_overlays(s_closes, ema_p)
            if i_closes:
                ind_overlays += _ema_overlays(i_closes, ema_p)
        if bb_check:
            stock_overlays += _bb_overlays(s_closes, bb_p, bb_s)
            if i_closes:
                ind_overlays += _bb_overlays(i_closes, bb_p, bb_s)
        if ichi_check:
            stock_overlays += _ichi_overlays(s_highs, s_lows, s_closes, ichi_t, ichi_k, ichi_sb, ichi_d)
            if i_closes:
                ind_overlays += _ichi_overlays(i_highs, i_lows, i_closes, ichi_t, ichi_k, ichi_sb, ichi_d)
        if rsi_check:
            sub_panels.append({
                "label": f"RSI({rsi_p})", "kind": "line",
                "y": calc_rsi(s_closes, rsi_p),
                "color": "#AB47BC", "hlines": [30, 70],
            })
        if macd_check:
            m = calc_macd(s_closes, mf, ms_, msig)
            sub_panels.append({"label": f"MACD({mf},{ms_},{msig})", "kind": "macd", **m})
        if atr_check:
            sub_panels.append({
                "label": f"ATR({atr_p})", "kind": "line",
                "y": calc_atr(s_highs, s_lows, s_closes, atr_p),
                "color": "#FF7043",
            })
        if zz_check:
            intraday = len(bars) >= 2 and bars[0].dt.date() == bars[1].dt.date()
            ts_fmt   = "%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d"
            peaks = detect_peaks(s_highs, s_lows, size=zz_s, middle_size=zz_m)
            pts = [
                (bars[p.bar_index].dt.strftime(ts_fmt), p.price, p.direction)
                for p in peaks if abs(p.direction) == 2 and p.bar_index < len(bars)
            ]
            if pts:
                stock_overlays.append({"kind": "zigzag", "points": pts})
            if i_closes and ind_bars:
                i_intraday = len(ind_bars) >= 2 and ind_bars[0].dt.date() == ind_bars[1].dt.date()
                i_ts_fmt   = "%Y-%m-%d %H:%M" if i_intraday else "%Y-%m-%d"
                i_highs_zz = [b.high for b in ind_bars]
                i_lows_zz  = [b.low  for b in ind_bars]
                i_peaks = detect_peaks(i_highs_zz, i_lows_zz, size=zz_s, middle_size=zz_m)
                i_pts = [
                    (ind_bars[p.bar_index].dt.strftime(i_ts_fmt), p.price, p.direction)
                    for p in i_peaks if abs(p.direction) == 2 and p.bar_index < len(ind_bars)
                ]
                if i_pts:
                    ind_overlays.append({"kind": "zigzag", "points": i_pts})

        title = f"{stock}  |  ρ vs major indices  |  window={window} bars"
        fig = build_moving_corr_figure(
            cache, corr_map, title=title,
            indicator_cache=first_ind_cache,
            indicator_label=first_ind_code,
            stock_overlays=stock_overlays or None,
            ind_overlays=ind_overlays or None,
            sub_panels=sub_panels or None,
        )

        chart_h    = 600 + 120 * len(corr_map) + 120 * len(sub_panels)
        chart_style = {"height": f"{chart_h}px"}

        bar0, barN = cache.bars[0].dt.date(), cache.bars[-1].dt.date()
        status = [
            html.Span("Stock: ",     style={"color": MUTED}),
            html.Span(f"{stock}  ",  style={"color": TEXT, "fontWeight": "500", "marginRight": "20px"}),
            html.Span("Bars: ",      style={"color": MUTED}),
            html.Span(f"{len(cache.bars)}  ", style={"color": TEXT, "marginRight": "20px"}),
            html.Span("Period: ",    style={"color": MUTED}),
            html.Span(f"{bar0} → {barN}  ", style={"color": TEXT, "marginRight": "20px"}),
            html.Span("Indicators: ", style={"color": MUTED}),
            html.Span(f"{len(indicator_map)}  ", style={"color": TEXT, "marginRight": "20px"}),
            html.Span("Window: ",    style={"color": MUTED}),
            html.Span(f"{window} bars", style={"color": TEXT}),
        ]
        return fig, chart_style, status
