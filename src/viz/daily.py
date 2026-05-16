"""daily — Daily trade proposals page.

Shows today's RegimeSign proposals: sign, stock, regime metrics.
Hover a sign name for a description. Click a row to view the stock chart.
"""

from __future__ import annotations

import datetime
import json
import math
import os
import threading
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Input, Output, State, callback, callback_context, dash_table, dcc, html, no_update
from loguru import logger
from plotly.subplots import make_subplots
from sqlalchemy import select
from ta.trend import ADXIndicator

from src.analysis.models import SignBenchmarkRun
from src.data.db import get_session
from src.data.models import Stock
from src.indicators.ichimoku import calc_ichimoku
from src.indicators.moving_corr import compute_moving_corr
from src.indicators.zigzag import detect_peaks
from src.portfolio.crud import (
    close_position,
    create_memo,
    delete_memo,
    get_entry_price_for_fire,
    get_memos_for_date,
    list_accounts,
    register_review,
    update_memo,
    compute_exit_levels,
    get_latest_price,
    get_open_positions,
    register_position,
)
from src.indicators.corr_regime import CorrRegime
from src.indicators.rev_n_regime import RevNRegime
from src.indicators.sma_regime import SMARegime
from src.portfolio.models import Position, ReviewedCandidate
from src.simulator.cache import DataCache
from src.strategy.proposal import SignalProposal
from src.strategy.regime_sign import _CERTIFIED_SECTOR_BONUS, RegimeSignStrategy
from src.viz.palette import ACCENT, BG, BORDER, CARD_BG, GREEN, MUTED, RED, SIDEBAR_BG, TEXT

# ── Config ────────────────────────────────────────────────────────────────────

_CURRENT_STOCK_SET = "classified2024"
_PRIOR_BENCH_SETS: list[str] = [
    "classified2019", "classified2020", "classified2021",
    "classified2022", "classified2023",
]
_MIN_DR        = 0.52
_LOOKBACK_DAYS = 230    # 200 warmup + 30 buffer
_GRAN          = "1d"
_CHART_BARS    = 160    # bars shown in chart (extra loaded for SMA warmup)

# ── Decision factors (Daily tab factor panel) ─────────────────────────────────
# Per evaluation_criteria.md §5.11: every factor shown must carry measured
# strength + sample size + provenance, and no A/B-negative factor is displayed.
#
# Sector decision factor — v1 display set. Certified by sign_sector_axis_probe
# (data/analysis/sign_sector_axis/probe_2026-05-14.md): per-cell shuffle p<0.05,
# OOS test n≥100 with positive ΔDR, passed dual-axis orthogonality gate.
# rev_nhi×銀行 was also probe-certified but is EXCLUDED here — the strategy A/B
# (ab_2026-05-14.md) showed it loses money live (−1.42% mean_r), and §5.11
# forbids showing an A/B-negative factor on the decision surface.
_SECTOR_FACTOR_DISPLAY: dict[tuple[str, str], dict[str, Any]] = {
    ("str_hold", "不動産"):     {"delta_ev": 0.0128, "oos_test_n": 109, "oos_ddr": "+4.8pp"},
    ("rev_nlo",  "電機・精密"): {"delta_ev": 0.0142, "oos_test_n": 140, "oos_ddr": "+2.8pp"},
}
_SECTOR_PROVENANCE  = "data/analysis/sign_sector_axis/probe_2026-05-14.md"
_REGIME_MIN_READ_N  = 100   # §5.1 — below this a cell DR is too noisy to read

# Kumo informativeness per sign — DR spread across (above/inside/below) kumo
# states, from benchmark.md § Regime-Split (Ichimoku Kumo table, FY2018–FY2024).
# Kumo is the lookup key for the regime cell already shown — this caption tells
# the reader whether that key actually differentiates outcomes for the sign, or
# is flat noise (e.g. brk_bol: 0.6pp spread). Not a standalone factor (§5.11) —
# it lives in the context block as a caption on the regime cell.
_KUMO_INFORMATIVENESS: dict[str, str] = {
    "str_lead":   "strong — 25pp DR spread across kumo states",
    "div_peer":   "strong — 20pp DR spread",
    "rev_nlo":    "strong — 20pp DR spread",
    "str_hold":   "strong — 10pp DR spread ('above' is ~a coin flip)",
    "rev_hi":     "strong — 8pp DR spread ('above' is noise)",
    "brk_sma":    "moderate — 9pp spread but n-thin",
    "div_gap":    "moderate — 8pp DR spread",
    "rev_lo":     "moderate — 6pp DR spread",
    "corr_flip":  "flat — all 3 states are noise (sign itself weak)",
    "str_lag":    "flat — 3pp spread, all states noise",
    "rev_nhi":    "flat — 3pp DR spread",
    "brk_bol":    "flat — 0.6pp spread, kumo says nothing here",
    "corr_shift": "flat — spread is noise-driven, 2/3 states not significant",
    "rev_nhold":  "unknown — n too thin to read",
}

_sector_map_cache: dict[str, str] = {}


def _load_sector_map() -> dict[str, str]:
    """{stock_code: sector17} — cached; populated from the stocks master table."""
    if not _sector_map_cache:
        with get_session() as session:
            for code, sector in session.execute(
                select(Stock.code, Stock.sector17)
            ).all():
                if sector:
                    _sector_map_cache[code] = sector
    return _sector_map_cache


_name_map_cache: dict[str, str] = {}


def _load_name_map() -> dict[str, str]:
    """{stock_code: company name} — cached; from the stocks master table."""
    if not _name_map_cache:
        with get_session() as session:
            for code, name in session.execute(
                select(Stock.code, Stock.name)
            ).all():
                if name:
                    _name_map_cache[code] = name
    return _name_map_cache


# ── Sign descriptions — loaded dynamically from each sign module ───────────────

def _load_sign_descriptions() -> dict[str, str]:
    import importlib
    import pkgutil
    import src.signs as _signs_pkg
    result: dict[str, str] = {}
    for mod_info in pkgutil.iter_modules(_signs_pkg.__path__):
        if mod_info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"src.signs.{mod_info.name}")
            desc = getattr(mod, "SIGN_DESCRIPTIONS", None)
            if isinstance(desc, dict):
                result.update(desc)
        except Exception:
            pass
    return result

SIGN_DESCRIPTIONS: dict[str, str] = _load_sign_descriptions()

# ── Module-level strategy cache (keyed by (stock_set, date_str)) ──────────────

_strategy_cache: dict[tuple[str, str], RegimeSignStrategy] = {}
_revn_regime_cache: dict[tuple[str, str], RevNRegime] = {}
_sma_regime_cache:  dict[tuple[str, str], SMARegime]   = {}
_corr_regime_cache: dict[tuple[str, str], CorrRegime]  = {}

# ── Daily update state ────────────────────────────────────────────────────────

_update_lock    = threading.Lock()
_update_msg: str  = ""
_update_running   = False

_S_UPDATE_RUN: dict = {
    "fontSize": "11px", "color": "#ff9800",
    "fontFamily": "monospace", "marginTop": "4px",
}
_S_UPDATE_DONE: dict = {
    "fontSize": "11px", "color": GREEN,
    "fontFamily": "monospace", "marginTop": "4px",
}
_S_UPDATE_HIDDEN: dict = {"display": "none"}


def _run_daily_update() -> None:
    global _update_running, _update_msg
    try:
        from src.analysis.sign_regime_analysis import phase_build
        from src.data.collect import OHLCVCollector
        from src.data.nikkei225 import load_or_fetch

        today    = datetime.date.today()
        end_dt   = datetime.datetime(today.year, today.month, today.day,
                                     tzinfo=datetime.timezone.utc)
        start_dt = datetime.datetime(today.year, 1, 1, tzinfo=datetime.timezone.utc)

        n225_codes = load_or_fetch()
        all_codes  = ["^N225", "^GSPC"] + sorted(set(n225_codes))
        total      = len(all_codes)

        with _update_lock:
            _update_msg = (
                f"Downloading {total} codes "
                f"({start_dt.date()} → {end_dt.date()}, gaps only) …"
            )

        total_new = 0
        for i, code in enumerate(all_codes, 1):
            try:
                with get_session() as session:
                    collector = OHLCVCollector(session)
                    n = collector.collect(code, _GRAN, start_dt, end_dt)
                total_new += n
                with _update_lock:
                    _update_msg = f"[{i}/{total}] {code}: +{n}  (total new bars: {total_new})"
            except Exception as exc:
                with _update_lock:
                    _update_msg = f"[{i}/{total}] {code}: ERROR — {exc}"

        with _update_lock:
            _update_msg = "Rebuilding N225 regime snapshots …"
        try:
            phase_build()
        except Exception as exc:
            with _update_lock:
                _update_msg = f"Regime build error: {exc}"
            return

        _strategy_cache.clear()
        now = datetime.datetime.now().strftime("%H:%M")
        with _update_lock:
            _update_msg = f"✓ Done — {total_new} new bars across {total} codes  ({now})"
    finally:
        with _update_lock:
            _update_running = False


def _get_run_ids(prior_sets: list[str]) -> list[int]:
    with get_session() as session:
        rows = session.execute(
            select(SignBenchmarkRun.id)
            .where(SignBenchmarkRun.stock_set.in_(prior_sets))
        ).scalars().all()
    return list(rows)


def _get_strategy(target_date: datetime.date) -> RegimeSignStrategy:
    key = (_CURRENT_STOCK_SET, target_date.isoformat())
    if key not in _strategy_cache:
        _strategy_cache.clear()
        tz       = datetime.timezone.utc
        end_dt   = datetime.datetime(
            target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=tz
        )
        start_dt = end_dt - datetime.timedelta(days=_LOOKBACK_DAYS)
        run_ids  = _get_run_ids(_PRIOR_BENCH_SETS)
        logger.info(
            "Building daily strategy for {} ({} run_ids, stock_set={})",
            target_date, len(run_ids), _CURRENT_STOCK_SET,
        )
        _strategy_cache[key] = RegimeSignStrategy.from_config(
            stock_set = _CURRENT_STOCK_SET,
            run_ids   = run_ids,
            start     = start_dt,
            end       = end_dt,
            mode      = "trade",
            min_dr    = _MIN_DR,
        )
    return _strategy_cache[key]


def _get_revn_regime(
    target_date: datetime.date,
    strategy:    RegimeSignStrategy,
) -> RevNRegime:
    """Cached per-target-date RevNRegime built from the strategy's loaded caches."""
    key = (_CURRENT_STOCK_SET, target_date.isoformat())
    if key not in _revn_regime_cache:
        _revn_regime_cache.clear()
        dates = sorted({b.dt.date() for b in strategy._n225_cache.bars})
        _revn_regime_cache[key] = RevNRegime.build(
            stock_caches = strategy._stock_caches,
            dates        = dates,
        )
    return _revn_regime_cache[key]


def _get_sma_regime(
    target_date: datetime.date,
    strategy:    RegimeSignStrategy,
) -> SMARegime:
    """Cached per-target-date SMARegime built from the strategy's loaded caches."""
    key = (_CURRENT_STOCK_SET, target_date.isoformat())
    if key not in _sma_regime_cache:
        _sma_regime_cache.clear()
        dates = sorted({b.dt.date() for b in strategy._n225_cache.bars})
        _sma_regime_cache[key] = SMARegime.build(
            stock_caches = strategy._stock_caches,
            dates        = dates,
        )
    return _sma_regime_cache[key]


def _get_corr_regime(
    target_date: datetime.date,
    strategy:    RegimeSignStrategy,
) -> CorrRegime:
    """Cached per-target-date CorrRegime built from MovingCorr DB rows."""
    key = (_CURRENT_STOCK_SET, target_date.isoformat())
    if key not in _corr_regime_cache:
        _corr_regime_cache.clear()
        stock_codes = list(strategy._stock_caches)
        tz       = datetime.timezone.utc
        end_dt   = datetime.datetime(
            target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=tz
        )
        start_dt = end_dt - datetime.timedelta(days=_LOOKBACK_DAYS)
        with get_session() as session:
            _corr_regime_cache[key] = CorrRegime.build(
                session, stock_codes, start_dt, end_dt,
            )
    return _corr_regime_cache[key]


# ── Data helpers ──────────────────────────────────────────────────────────────

def _kumo_text(state: int, corr_mode: str | None = None) -> str:
    """Kumo state text, annotated with *whose* kumo it is.

    regime_sign attaches the N225's kumo to high/mid-corr proposals (they are
    index proxies) and the stock's own kumo to low-corr proposals. Without the
    suffix, "▲ above" on a high-corr row reads as "this stock is above its
    cloud" — which is not what it means and won't match the stock's own chart.
    """
    base = {1: "▲ above", 0: "≈ inside", -1: "▼ below"}.get(state, "?")
    if corr_mode == "low":
        return f"{base} (own)"
    if corr_mode in ("high", "mid"):
        return f"{base} (N225)"
    return base


def _adx_state_str(adx: float, adx_pos: float, adx_neg: float) -> str:
    if math.isnan(adx) or adx < 20.0:
        return "choppy"
    return "bull" if adx_pos > adx_neg else "bear"


def _proposals_to_json(
    proposals: list[SignalProposal],
    ranking_evs: list[float] | None = None,
) -> str:
    sector_map = _load_sector_map()
    name_map   = _load_name_map()
    rows = [
        {
            "stock":     p.stock_code,
            "name":      name_map.get(p.stock_code),
            "sign":      p.sign_type,
            "corr":      p.corr_mode,
            "corr_n225": (None if p.corr_n225 is None or math.isnan(p.corr_n225)
                          else round(p.corr_n225, 3)),
            "kumo":      _kumo_text(p.kumo_state, p.corr_mode),
            "kumo_int":  p.kumo_state,
            "dr":        round(p.regime_dr, 4),
            "ev":        round(p.regime_ev, 5),
            "bench_flw": round(p.regime_bench_flw, 5),
            "regime_n":  p.regime_n,
            "adx":       round(p.adx, 1),
            "adx_state": _adx_state_str(p.adx, p.adx_pos, p.adx_neg),
            "score":     round(p.sign_score, 3),
            "sector":    sector_map.get(p.stock_code),
            "fired_at":  p.fired_at.strftime("%Y-%m-%d"),
        }
        for p in proposals
    ]
    # Order by the strategy's own recommendation composite, not raw EV alone.
    # regime_ev is a (sign, kumo)-cell aggregate, so a raw-EV sort leaves many
    # stocks tied (identical EV for every stock firing that sign in that
    # regime). The strategy's _sort_stock/_sort_n225 break those ties with
    # (EV + certified-sector tilt) then sign_score — this matches that key so
    # the table stops discarding the ranking the strategy already computed.
    sector_on = bool(os.environ.get("RS_SECTOR_FACTOR"))

    def _rec_key(r: dict[str, Any]) -> tuple[float, float, str]:
        bonus = (_CERTIFIED_SECTOR_BONUS.get((r["sign"], r["sector"]), 0.0)
                 if sector_on else 0.0)
        return (-(r["ev"] + bonus), -r["score"], r["stock"])

    rows.sort(key=_rec_key)

    # Recommendation tier chip (Daily factor-panel context block, §5.11).
    # Derived from regime_ev ALONE — never ev+bonus: the sector bonus includes
    # an A/B-negative cell (rev_nhi×銀行), and §5.11 bars an A/B-negative factor
    # from feeding the decision surface. Cutoff = 75th pct of the strategy
    # ranking's positive cell EVs — the exact population the live regime_ev
    # values are drawn from (not benchmark.md, which can drift). Cell-level,
    # NOT per-stock: every stock firing the same sign in the same regime shares
    # the tier — it is shown in the "context, not stock-specific" block only.
    pos_evs = [e for e in (ranking_evs or []) if e > 0]
    strong_cut = float(np.percentile(pos_evs, 75)) if pos_evs else None
    for r in rows:
        if strong_cut is None:
            r["tier"] = None
        elif r["ev"] >= strong_cut:
            r["tier"] = "strong"
        elif r["ev"] > 0:
            r["tier"] = "moderate"
        else:
            r["tier"] = "marginal"
    return json.dumps(rows)


def _regime_from_proposals(proposals: list[SignalProposal]) -> dict[str, Any] | None:
    if not proposals:
        return None
    p = proposals[0]
    return {
        "kumo_state": p.kumo_state,
        "kumo_text":  _kumo_text(p.kumo_state),
        "adx":        round(p.adx, 1),
        "adx_pos":    round(p.adx_pos, 1),
        "adx_neg":    round(p.adx_neg, 1),
        "adx_state":  _adx_state_str(p.adx, p.adx_pos, p.adx_neg),
    }


# ── Chart helpers ─────────────────────────────────────────────────────────────

def _empty_chart(msg: str) -> go.Figure:
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


def _div_peer_reference(
    stock_code: str,
    fired_str:  str,
    stock_set:  str = _CURRENT_STOCK_SET,
) -> tuple[float, int, int, datetime.date] | None:
    """Reconstruct div_peer trigger details for a given fire.

    Mirrors DivPeerDetector's calculation: looks up the stock's cluster
    in the latest StockClusterRun for ``stock_set``, fetches the previous
    and fire-day closes for the stock plus all cluster peers, and returns
    ``(stock_return, n_peers_down, n_peers_total, prev_date)``.  Returns
    None if the cluster / data lookup fails.
    """
    from src.analysis.models import StockClusterMember, StockClusterRun
    from src.data.models import OHLCV_MODEL_MAP
    from sqlalchemy import select, func, and_

    try:
        fired_date = datetime.date.fromisoformat(fired_str)
    except Exception:
        return None
    model = OHLCV_MODEL_MAP.get("1d")
    if model is None:
        return None

    PEER_DOWN_MIN = -0.003   # match DivPeerDetector constant

    try:
        with get_session() as session:
            run_id = session.execute(
                select(StockClusterRun.id)
                .where(StockClusterRun.fiscal_year == stock_set)
                .order_by(StockClusterRun.created_at.desc())
            ).scalars().first()
            if run_id is None:
                return None
            # All cluster peer codes (excluding the stock itself)
            cluster_id = session.execute(
                select(StockClusterMember.cluster_id)
                .where(StockClusterMember.run_id == run_id,
                       StockClusterMember.stock_code == stock_code)
            ).scalar_one_or_none()
            if cluster_id is None:
                return None
            peer_codes = list(session.execute(
                select(StockClusterMember.stock_code)
                .where(StockClusterMember.run_id == run_id,
                       StockClusterMember.cluster_id == cluster_id,
                       StockClusterMember.stock_code != stock_code)
            ).scalars().all())

            # Previous trading day = most-recent date in DB strictly before fired_date for the stock
            prev_date = session.execute(
                select(func.date(model.ts))
                .where(model.stock_code == stock_code,
                       func.date(model.ts) < fired_date)
                .order_by(model.ts.desc())
                .limit(1)
            ).scalar_one_or_none()
            if prev_date is None:
                return None

            # Bulk-fetch closes for stock + peers on (prev_date, fired_date)
            all_codes = [stock_code] + peer_codes
            rows = session.execute(
                select(model.stock_code, func.date(model.ts), model.close_price)
                .where(model.stock_code.in_(all_codes),
                       func.date(model.ts).in_([prev_date, fired_date]))
            ).all()
            by_code: dict[str, dict[datetime.date, float]] = {}
            for code, dt_, cp in rows:
                by_code.setdefault(code, {})[dt_] = float(cp)

        # Stock return
        sc = by_code.get(stock_code, {}).get(fired_date)
        ps = by_code.get(stock_code, {}).get(prev_date)
        if sc is None or ps is None or ps == 0:
            return None
        stock_ret = sc / ps - 1.0

        # Peer down count
        n_down = n_total = 0
        for peer in peer_codes:
            pd = by_code.get(peer, {})
            cc = pd.get(fired_date)
            pp = pd.get(prev_date)
            if cc is None or pp is None or pp == 0:
                continue
            n_total += 1
            if cc / pp - 1.0 < PEER_DOWN_MIN:
                n_down += 1
        if n_total == 0:
            return None
        return stock_ret, n_down, n_total, prev_date
    except Exception as exc:
        logger.warning("_div_peer_reference failed for {} {}: {}",
                       stock_code, fired_str, exc)
        return None


def _rev_peak_reference(
    stock_bars: list | None,
    sign_type:  str,
    fired_str:  str,
    n_peaks:        int   = 2,
    proximity_pct:  float = 0.005,
    zz_size:        int   = 5,
    zz_middle:      int   = 2,
) -> tuple[float, str] | None:
    """Compute (reference_price, reference_date) for rev_lo / rev_hi fires.

    Mirrors RevPeakDetector's peak-matching logic: among the last
    ``n_peaks`` confirmed same-side zigzag peaks observable before
    fired_date, find the most recent one within ``proximity_pct`` of
    bar.low (rev_lo) or bar.high (rev_hi).  Returns None for non-rev_peak
    signs or insufficient data.
    """
    if sign_type not in ("rev_lo", "rev_hi"):
        return None
    if not stock_bars:
        return None
    try:
        fired_date = datetime.date.fromisoformat(fired_str)
    except Exception:
        return None

    # Collapse to daily
    daily_hi: dict[datetime.date, float] = {}
    daily_lo: dict[datetime.date, float] = {}
    for b in stock_bars:
        d = b.dt.date()
        if d not in daily_hi or b.high > daily_hi[d]:
            daily_hi[d] = float(b.high)
        if d not in daily_lo or b.low < daily_lo[d]:
            daily_lo[d] = float(b.low)
    sorted_dates = sorted(daily_hi)
    if fired_date not in sorted_dates:
        return None
    fired_idx = sorted_dates.index(fired_date)
    highs_list = [daily_hi[d] for d in sorted_dates]
    lows_list  = [daily_lo[d] for d in sorted_dates]

    target_dir = -2 if sign_type == "rev_lo" else 2
    peaks = detect_peaks(highs_list, lows_list, size=zz_size, middle_size=zz_middle)

    # Peaks observable before fire (need zz_size bars of confirmation past peak)
    candidates: list[tuple[int, float]] = []
    for p in peaks:
        if p.direction != target_dir:
            continue
        obs_from = p.bar_index + zz_size
        if obs_from > fired_idx:
            continue
        candidates.append((p.bar_index, p.price))
    if not candidates:
        return None
    candidates.sort()
    recent = candidates[-n_peaks:]

    test_price = lows_list[fired_idx] if sign_type == "rev_lo" else highs_list[fired_idx]
    if not test_price:
        return None
    # Match: iterate most-recent first, take first within proximity (same as RevPeakDetector)
    for bar_idx, peak_price in reversed(recent):
        if not peak_price:
            continue
        proximity = abs(test_price - peak_price) / peak_price
        if proximity <= proximity_pct:
            return peak_price, sorted_dates[bar_idx].isoformat()
    return None


def _rev_nday_reference(
    stock_bars: list | None,
    sign_type:  str,
    fired_str:  str,
    n_days:     int = 20,
) -> tuple[float, str] | None:
    """Compute the (reference_price, reference_date) for rev_nhi / rev_nlo fires.

    Mirrors ``RevNDayDetector``'s reference-level calculation: the max/min
    of the prior N complete trading days before fired_str.  Returns the
    most recent date among ties so the annotation points at the proximal
    peak.  Returns None for non-rev_nday signs or insufficient history.
    """
    if sign_type not in ("rev_nhi", "rev_nlo"):
        return None
    if not stock_bars:
        return None
    try:
        fired_date = datetime.date.fromisoformat(fired_str)
    except Exception:
        return None
    # Build daily high/low (collapse intraday bars to one per date)
    daily_hi: dict[datetime.date, float] = {}
    daily_lo: dict[datetime.date, float] = {}
    for b in stock_bars:
        d = b.dt.date()
        if d not in daily_hi or b.high > daily_hi[d]:
            daily_hi[d] = float(b.high)
        if d not in daily_lo or b.low  < daily_lo[d]:
            daily_lo[d] = float(b.low)
    prior_dates = sorted(d for d in daily_hi if d < fired_date)
    if len(prior_dates) < n_days:
        return None
    window = prior_dates[-n_days:]
    if sign_type == "rev_nhi":
        ref_date = max(window, key=lambda d: daily_hi[d])
        return daily_hi[ref_date], ref_date.isoformat()
    else:
        ref_date = min(window, key=lambda d: daily_lo[d])
        return daily_lo[ref_date], ref_date.isoformat()


def _build_combined_chart(
    target_date: datetime.date,
    stock_row: dict | None = None,
    tp_price: float | None = None,
    sl_price: float | None = None,
) -> go.Figure:
    """Single figure with shared x-axis (same approach as corr_ui.py / build_pair_figure).

    No stock selected : N225 price + ADX + Vol (3 rows).
    Stock selected    : Stock price + ADX + Vol (rows 1-3) + N225 price (row 4).
    Both panels share the date-union x-axis — panning one pans the other automatically.
    """
    try:
        tz = datetime.timezone.utc
        end_dt = datetime.datetime(
            target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=tz,
        )
        start_dt = end_dt - datetime.timedelta(days=_CHART_BARS + 140)

        with get_session() as session:
            n225_cache = DataCache("^N225", _GRAN)
            n225_cache.load(session, start_dt, end_dt)
            n225_cache.add_sma(25).add_sma(75)
            gspc_cache = DataCache("^GSPC", _GRAN)
            gspc_cache.load(session, start_dt, end_dt)
            stock_cache = None
            if stock_row:
                stock_cache = DataCache(stock_row["stock"], _GRAN)
                stock_cache.load(session, start_dt, end_dt)
                stock_cache.add_sma(25).add_sma(75)

        if not n225_cache.bars:
            return _empty_chart("No ^N225 data — run Update to download OHLCV")

        n225_all_bars = n225_cache.bars
        n225_bars     = n225_all_bars[-_CHART_BARS:]

        stock_all_bars: list | None = None
        stock_bars: list | None     = None
        if stock_row and stock_cache and stock_cache.bars:
            stock_all_bars = stock_cache.bars
            stock_bars     = stock_all_bars[-_CHART_BARS:]
        elif stock_row:
            return _empty_chart(f"No data for {stock_row['stock']}")

        # ── Union date axis (same technique as build_pair_figure) ─────────────
        n225_date_set  = {b.dt.strftime("%Y-%m-%d") for b in n225_bars}
        stock_date_set = {b.dt.strftime("%Y-%m-%d") for b in stock_bars} if stock_bars else set()
        dates          = sorted(n225_date_set | stock_date_set)
        n              = len(dates)
        ticks          = dates[::max(1, n // 24)]

        # ── OHLCV map helper ──────────────────────────────────────────────────
        def _ohlcv(bars_slice: list) -> tuple:
            m = {b.dt.strftime("%Y-%m-%d"): b for b in bars_slice}
            return (
                [m[d].open   if d in m else None for d in dates],
                [m[d].high   if d in m else None for d in dates],
                [m[d].low    if d in m else None for d in dates],
                [m[d].close  if d in m else None for d in dates],
                [m[d].volume if d in m else None for d in dates],
                [(m[d].indicators.get("SMA25") or None) if d in m else None for d in dates],
                [(m[d].indicators.get("SMA75") or None) if d in m else None for d in dates],
            )

        n225_o, n225_h, n225_l, n225_c, n225_v, n225_sma25, n225_sma75 = _ohlcv(n225_bars)

        # ── Ichimoku helper (compute on all loaded bars, map to union dates) ──
        def _clean_v(v: float | None) -> float | None:
            return None if v is None or (isinstance(v, float) and math.isnan(v)) else v

        def _ichi_map(all_bars: list) -> dict[str, tuple]:
            hi  = [b.high  for b in all_bars]
            lo  = [b.low   for b in all_bars]
            cl  = [b.close for b in all_bars]
            raw = calc_ichimoku(hi, lo, cl)
            d   = raw["displacement"]
            out: dict[str, tuple] = {}
            ds  = [b.dt.strftime("%Y-%m-%d") for b in all_bars]
            for i, dt in enumerate(ds):
                ai = i - d
                out[dt] = (
                    _clean_v(raw["senkou_a"][ai] if ai >= 0 else None),
                    _clean_v(raw["senkou_b"][ai] if ai >= 0 else None),
                    _clean_v(raw["tenkan"][i]),
                    _clean_v(raw["kijun"][i]),
                )
            return out

        n225_ichi = _ichi_map(n225_all_bars)
        n225_ca   = [n225_ichi.get(d, (None,)*4)[0] for d in dates]
        n225_cb   = [n225_ichi.get(d, (None,)*4)[1] for d in dates]
        n225_tk   = [n225_ichi.get(d, (None,)*4)[2] for d in dates]
        n225_kj   = [n225_ichi.get(d, (None,)*4)[3] for d in dates]

        # ── Zigzag helper ─────────────────────────────────────────────────────
        def _zz_maps(bars_slice: list) -> tuple[dict, dict]:
            hi = [b.high for b in bars_slice]
            lo = [b.low  for b in bars_slice]
            ds = [b.dt.strftime("%Y-%m-%d") for b in bars_slice]
            pk = detect_peaks(hi, lo, size=5, middle_size=2)
            return (
                {ds[p.bar_index]: hi[p.bar_index] for p in pk if p.direction == 2},
                {ds[p.bar_index]: lo[p.bar_index] for p in pk if p.direction == -2},
            )

        n225_zz_hi, n225_zz_lo = _zz_maps(n225_bars)
        n225_conf_hi = [(d, n225_zz_hi[d]) for d in dates if d in n225_zz_hi]
        n225_conf_lo = [(d, n225_zz_lo[d]) for d in dates if d in n225_zz_lo]

        # ── Cloud fill helper ─────────────────────────────────────────────────
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

        # ── N225 price-panel helper ───────────────────────────────────────────
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
                x=dates, open=n225_o, high=n225_h, low=n225_l, close=n225_c, name="^N225",
                increasing_fillcolor="#26a69a", increasing_line_color="#26a69a",
                decreasing_fillcolor="#ef5350", decreasing_line_color="#ef5350",
                showlegend=False,
                hovertemplate="<b>%{x}</b><br>O:%{open:,.0f} H:%{high:,.0f} L:%{low:,.0f} C:%{close:,.0f}<extra></extra>",
            ), row=row, col=1)
            fig.add_trace(go.Scatter(x=dates, y=n225_sma25, mode="lines", name="SMA25",
                                     showlegend=legend,
                                     line=dict(color="#ff9800", width=1.2),
                                     hovertemplate="SMA25: %{y:,.0f}<extra></extra>"),
                          row=row, col=1)
            fig.add_trace(go.Scatter(x=dates, y=n225_sma75, mode="lines", name="SMA75",
                                     showlegend=legend,
                                     line=dict(color="#ab47bc", width=1.2),
                                     hovertemplate="SMA75: %{y:,.0f}<extra></extra>"),
                          row=row, col=1)
            if n225_conf_hi:
                hx, hy = zip(*n225_conf_hi)
                fig.add_trace(go.Scatter(x=list(hx), y=list(hy), mode="markers",
                                         name="ZZ high", showlegend=legend,
                                         marker=dict(symbol="triangle-down", size=9,
                                                     color="#ef5350", line=dict(width=1, color="#fff")),
                                         hovertemplate="ZZ high: %{y:,.0f}<extra></extra>"),
                              row=row, col=1)
            if n225_conf_lo:
                lx, ly = zip(*n225_conf_lo)
                fig.add_trace(go.Scatter(x=list(lx), y=list(ly), mode="markers",
                                         name="ZZ low", showlegend=legend,
                                         marker=dict(symbol="triangle-up", size=9,
                                                     color="#26a69a", line=dict(width=1, color="#fff")),
                                         hovertemplate="ZZ low: %{y:,.0f}<extra></extra>"),
                              row=row, col=1)

        _LEGEND = dict(orientation="h", yanchor="bottom", y=1.01,
                       xanchor="right", x=1, font=dict(size=10), bgcolor="rgba(0,0,0,0)")

        def _apply_axes(fig: go.Figure, bottom_row: int) -> None:
            fig.update_xaxes(type="category")
            fig.update_xaxes(tickvals=ticks, ticktext=ticks, tickangle=-30, tickfont=dict(size=9))
            fig.update_xaxes(rangeslider_visible=False)
            fig.update_xaxes(
                rangeslider_visible=True,
                rangeslider=dict(thickness=0.03, bgcolor=CARD_BG, bordercolor=BORDER),
                row=bottom_row, col=1,
            )

        # ══════════════════════════════════════════════════════════════════════
        if stock_bars:
            # ── 4-row combined: stock (price/ADX/vol) + N225 price ────────────
            s_ichi = _ichi_map(stock_all_bars)  # type: ignore[arg-type]
            s_ca   = [s_ichi.get(d, (None,)*4)[0] for d in dates]
            s_cb   = [s_ichi.get(d, (None,)*4)[1] for d in dates]
            s_tk   = [s_ichi.get(d, (None,)*4)[2] for d in dates]
            s_kj   = [s_ichi.get(d, (None,)*4)[3] for d in dates]

            s_o, s_h, s_l, s_c, s_v, s_sma25, s_sma75 = _ohlcv(stock_bars)

            s_zz_hi, s_zz_lo = _zz_maps(stock_bars)
            s_conf_hi = [(d, s_zz_hi[d]) for d in dates if d in s_zz_hi]
            s_conf_lo = [(d, s_zz_lo[d]) for d in dates if d in s_zz_lo]

            # ADX computed on actual stock bars → mapped to union dates
            _sb_ds = [b.dt.strftime("%Y-%m-%d") for b in stock_bars]
            _adx = ADXIndicator(
                high=pd.Series([b.high  for b in stock_bars], dtype=float),
                low=pd.Series([b.low   for b in stock_bars], dtype=float),
                close=pd.Series([b.close for b in stock_bars], dtype=float),
                window=14,
            )
            _adx_m = {d: (None if pd.isna(v) else float(v)) for d, v in zip(_sb_ds, _adx.adx())}
            _dip_m = {d: (None if pd.isna(v) else float(v)) for d, v in zip(_sb_ds, _adx.adx_pos())}
            _din_m = {d: (None if pd.isna(v) else float(v)) for d, v in zip(_sb_ds, _adx.adx_neg())}
            s_adx_v = [_adx_m.get(d) for d in dates]
            s_dip_v = [_dip_m.get(d) for d in dates]
            s_din_v = [_din_m.get(d) for d in dates]

            # N225 normalised to stock's first close
            first_n = next((v for v in n225_c if v is not None), None)
            first_s = next((v for v in s_c    if v is not None), None)
            n225_norm = (
                [v * (first_s / first_n) if v is not None else None for v in n225_c]
                if first_n and first_s else [None] * n
            )
            fired_str = stock_row["fired_at"]

            # ── Moving correlation (stock vs ^N225 and ^GSPC) ─────────────────
            _corr_window = 20
            _sb_date_objs = [b.dt.date() for b in stock_bars]
            _s_ser  = pd.Series({d: b.close for d, b in zip(_sb_date_objs, stock_bars)})
            _ind_map: dict[str, pd.Series] = {
                "^N225": pd.Series({b.dt.date(): b.close for b in n225_bars}),
            }
            if gspc_cache.bars:
                _ind_map["^GSPC"] = pd.Series(
                    {b.dt.date(): b.close for b in gspc_cache.bars[-_CHART_BARS:]}
                )
            _corr_result = compute_moving_corr(_s_ser, _ind_map, window=_corr_window)

            def _corr_series(key: str) -> list[float | None]:
                s = _corr_result.get(key)
                if s is None:
                    return [None] * n
                d_map = s.to_dict()
                return [
                    (None if pd.isna(v := d_map.get(datetime.date.fromisoformat(d))) else float(v))
                    for d in dates
                ]

            n225_corr_v = _corr_series("^N225")
            gspc_corr_v = _corr_series("^GSPC")

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
                                     line=dict(color="#78909c", width=1.2, dash="dash"),
                                     opacity=0.7,
                                     hovertemplate="^N225 (norm): %{y:,.0f}<extra></extra>"), row=1, col=1)
            fig.add_trace(go.Candlestick(
                x=dates, open=s_o, high=s_h, low=s_l, close=s_c,
                name=stock_row["stock"],
                increasing_fillcolor="#26a69a", increasing_line_color="#26a69a",
                decreasing_fillcolor="#ef5350", decreasing_line_color="#ef5350",
                showlegend=False,
                hovertemplate="<b>%{x}</b><br>O:%{open:,.0f} H:%{high:,.0f} L:%{low:,.0f} C:%{close:,.0f}<extra></extra>",
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

            # TP / SL overlay (proposal preview or actual position levels)
            if tp_price is not None and tp_price > 0:
                fig.add_hline(
                    y=tp_price, line_dash="dash", line_color=GREEN, line_width=1.2,
                    opacity=0.85,
                    annotation_text=f"TP {tp_price:,.0f}",
                    annotation_position="right",
                    annotation=dict(font=dict(color=GREEN, size=10),
                                    bgcolor="rgba(0,0,0,0.6)"),
                    row=1, col=1,
                )
            if sl_price is not None and sl_price > 0:
                fig.add_hline(
                    y=sl_price, line_dash="dash", line_color=RED, line_width=1.2,
                    opacity=0.85,
                    annotation_text=f"SL {sl_price:,.0f}",
                    annotation_position="right",
                    annotation=dict(font=dict(color=RED, size=10),
                                    bgcolor="rgba(0,0,0,0.6)"),
                    row=1, col=1,
                )

            # Row 2 — stock ADX
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

            # Row 3 — stock vol
            vcol = ["#26a69a" if (c or 0) >= (o or 0) else "#ef5350" for c, o in zip(s_c, s_o)]
            fig.add_trace(go.Bar(x=dates, y=s_v, name="Volume", marker_color=vcol,
                                 showlegend=False,
                                 hovertemplate="<b>Vol</b>  %{x}<br>%{y:,.0f}<extra></extra>"),
                          row=3, col=1)

            # Row 4 — N225 price only (no ADX / vol)
            _add_n225_price(fig, row=4, legend=False)

            # Row 5 — Moving correlation (stock vs ^N225, ^GSPC)
            fig.add_trace(go.Scatter(
                x=dates, y=n225_corr_v, mode="lines", name="ρ ^N225",
                line=dict(color="#ff9800", width=1.4),
                hovertemplate="ρ ^N225: %{y:.2f}<extra></extra>",
            ), row=5, col=1)
            if gspc_cache.bars:
                fig.add_trace(go.Scatter(
                    x=dates, y=gspc_corr_v, mode="lines", name="ρ ^GSPC",
                    line=dict(color="#29b6f6", width=1.4),
                    hovertemplate="ρ ^GSPC: %{y:.2f}<extra></extra>",
                ), row=5, col=1)
            for lvl, col, dash, width in (
                ( 0.6, "rgba(38,166,154,0.4)",  "dot",   1.0),
                ( 0.0, "rgba(180,180,180,0.85)", "solid", 2.0),
                (-0.6, "rgba(239,83,80,0.4)",    "dot",   1.0),
            ):
                fig.add_hline(y=lvl, line_dash=dash, line_color=col,
                              line_width=width, opacity=1.0, row=5, col=1)

            # Signal / entry annotation lines
            if stock_row.get("is_position"):
                entry_str = stock_row.get("entry_date", "")
                if entry_str and entry_str in dates:
                    fig.add_shape(type="line", x0=entry_str, x1=entry_str, y0=0, y1=1,
                                  xref="x", yref="paper",
                                  line=dict(width=1.5, dash="dot", color="#ff9800"))
                    dir_label = stock_row.get("direction", "").upper()
                    fig.add_annotation(x=entry_str, y=0.98, xref="x", yref="paper",
                                       text=f" Entry {dir_label}", showarrow=False,
                                       xanchor="left", font=dict(size=10, color="#ff9800"))
                ep = stock_row.get("entry_price")
                tp = stock_row.get("tp")
                sl = stock_row.get("sl")
                if ep:
                    fig.add_hline(y=ep, line_dash="dot", line_color="#ff9800", opacity=0.8,
                                  row=1, col=1,
                                  annotation_text=f" {ep:,.0f}",
                                  annotation_font_size=9, annotation_font_color="#ff9800",
                                  annotation_position="right")
                if tp:
                    fig.add_hline(y=tp, line_dash="dash", line_color=GREEN, opacity=0.6,
                                  row=1, col=1,
                                  annotation_text=f" TP {tp:,.0f}",
                                  annotation_font_size=9, annotation_font_color=GREEN,
                                  annotation_position="right")
                if sl:
                    fig.add_hline(y=sl, line_dash="dash", line_color=RED, opacity=0.6,
                                  row=1, col=1,
                                  annotation_text=f" SL {sl:,.0f}",
                                  annotation_font_size=9, annotation_font_color=RED,
                                  annotation_position="right")
                dir_label = stock_row.get("direction", "").upper()
                title_text = (
                    f"{stock_row['stock']}  —  {stock_row['sign']}  "
                    f"|  entry {stock_row.get('entry_date', '')}  [{dir_label}]"
                )
            else:
                if fired_str in dates:
                    fig.add_shape(type="line", x0=fired_str, x1=fired_str, y0=0, y1=1,
                                  xref="x", yref="paper",
                                  line=dict(width=1.5, dash="dot", color="#00e676"))
                    fig.add_annotation(x=fired_str, y=0.98, xref="x", yref="paper",
                                       text=f" {stock_row['sign']} fired", showarrow=False,
                                       xanchor="left", font=dict(size=10, color="#00e676"),
                                       bgcolor="rgba(0,0,0,0.6)")
                # "Today" marker — only render when it differs from fired_str so it
                # doesn't overlap; helps distinguish "where the sign originated" from
                # "the date currently being viewed / would be acted on".  Stacked at
                # y=0.92 so it doesn't collide with the fired-label at 0.98 when the
                # two dates render close together.
                target_str = target_date.isoformat()
                if target_str in dates and target_str != fired_str:
                    fig.add_shape(type="line", x0=target_str, x1=target_str, y0=0, y1=1,
                                  xref="x", yref="paper",
                                  line=dict(width=1.2, dash="dash", color="#29b6f6"))
                    fig.add_annotation(x=target_str, y=0.92, xref="x", yref="paper",
                                       text=" today", showarrow=False,
                                       xanchor="right", font=dict(size=10, color="#29b6f6"),
                                       bgcolor="rgba(0,0,0,0.6)")
                # Reference peak annotation for reversal signs — shows the prior
                # level the bar tested + the date that level was set.
                #   rev_nhi / rev_nlo → prior N-day reference high/low
                #   rev_lo  / rev_hi  → most-recent confirmed zigzag peak matched
                sign_name = stock_row["sign"]
                ref_info = (
                    _rev_nday_reference(stock_bars, sign_name, fired_str)
                    or _rev_peak_reference (stock_bars, sign_name, fired_str)
                )

                # div_peer trigger annotation — no price level to draw,
                # but render the stock_ret + peer_down_count as a stacked
                # text block near the fire marker.
                if sign_name == "div_peer":
                    dp = _div_peer_reference(stock_row["stock"], fired_str)
                    if dp is not None and fired_str in dates:
                        stock_ret, n_down, n_total, _prev = dp
                        peer_pct = 100.0 * n_down / n_total if n_total else 0.0
                        fig.add_annotation(
                            x=fired_str, y=0.84, xref="x", yref="paper",
                            text=(f" stock {stock_ret*100:+.2f}%"
                                  f"<br> peers {n_down}/{n_total} down "
                                  f"({peer_pct:.0f}%)"),
                            showarrow=False, xanchor="left", align="left",
                            font=dict(size=10, color="#29b6f6"),
                            bgcolor="rgba(0,0,0,0.6)",
                        )
                if ref_info is not None:
                    ref_price, ref_date_str = ref_info
                    is_hi = sign_name in ("rev_nhi", "rev_hi")
                    color = "#ef5350" if is_hi else "#26a69a"
                    label_side = "HIGH" if is_hi else "LOW"
                    fig.add_hline(
                        y=ref_price, line_dash="dot", line_color=color,
                        line_width=1.0, opacity=0.7,
                        annotation_text=f"ref {label_side} {ref_price:,.0f}<br>{ref_date_str}",
                        annotation_position="right",
                        annotation=dict(font=dict(color=color, size=10),
                                        bgcolor="rgba(0,0,0,0.6)",
                                        align="left"),
                        row=1, col=1,
                    )
                title_text = (
                    f"{stock_row['stock']}  —  {stock_row['sign']}  "
                    f"|  fired {fired_str}  ·  viewing {target_str}"
                )

            fig.update_layout(
                template="plotly_dark", paper_bgcolor=BG, plot_bgcolor="#0d1117",
                margin=dict(l=60, r=90, t=36, b=10),
                title=dict(
                    text=title_text,
                    font=dict(size=13, color=MUTED), x=0.01,
                ),
                dragmode="pan", hovermode="x unified",
                yaxis_title="Price",   yaxis2_title="ADX",
                yaxis3_title="Vol",    yaxis4_title="^N225",
                yaxis5_title=f"ρ({_corr_window})",
                yaxis_tickformat=",.0f", yaxis2_tickformat=".0f",
                yaxis3_tickformat=".2s", yaxis4_tickformat=",.0f",
                yaxis5=dict(range=[-1.05, 1.05], tickformat=".1f", fixedrange=True),
                legend=_LEGEND,
            )
            _apply_axes(fig, bottom_row=5)

        else:
            # ── N225-only 3-panel chart ───────────────────────────────────────
            _nb_ds = [b.dt.strftime("%Y-%m-%d") for b in n225_bars]
            _nadx  = ADXIndicator(
                high=pd.Series([b.high  for b in n225_bars], dtype=float),
                low=pd.Series([b.low   for b in n225_bars], dtype=float),
                close=pd.Series([b.close for b in n225_bars], dtype=float),
                window=14,
            )
            _adx_m = {d: (None if pd.isna(v) else float(v)) for d, v in zip(_nb_ds, _nadx.adx())}
            _dip_m = {d: (None if pd.isna(v) else float(v)) for d, v in zip(_nb_ds, _nadx.adx_pos())}
            _din_m = {d: (None if pd.isna(v) else float(v)) for d, v in zip(_nb_ds, _nadx.adx_neg())}
            n225_adx_v = [_adx_m.get(d) for d in dates]
            n225_dip_v = [_dip_m.get(d) for d in dates]
            n225_din_v = [_din_m.get(d) for d in dates]

            fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                                row_heights=[0.60, 0.20, 0.20], vertical_spacing=0.015)
            _add_n225_price(fig, row=1, legend=True)
            fig.add_trace(go.Scatter(x=dates, y=n225_adx_v, mode="lines", name="ADX",
                                     line=dict(color="#eceff1", width=1.4),
                                     hovertemplate="ADX: %{y:.1f}<extra></extra>"), row=2, col=1)
            fig.add_trace(go.Scatter(x=dates, y=n225_dip_v, mode="lines", name="+DI",
                                     line=dict(color=GREEN, width=1),
                                     hovertemplate="+DI: %{y:.1f}<extra></extra>"), row=2, col=1)
            fig.add_trace(go.Scatter(x=dates, y=n225_din_v, mode="lines", name="−DI",
                                     line=dict(color=RED, width=1),
                                     hovertemplate="−DI: %{y:.1f}<extra></extra>"), row=2, col=1)
            fig.add_hline(y=20, line_dash="dot", line_color=MUTED, opacity=0.5, row=2, col=1)

            vcol = ["#26a69a" if (c or 0) >= (o or 0) else "#ef5350" for c, o in zip(n225_c, n225_o)]
            fig.add_trace(go.Bar(x=dates, y=n225_v, name="Volume", marker_color=vcol,
                                 showlegend=False,
                                 hovertemplate="<b>Vol</b>  %{x}<br>%{y:,.0f}<extra></extra>"),
                          row=3, col=1)

            fig.update_layout(
                template="plotly_dark", paper_bgcolor=BG, plot_bgcolor="#0d1117",
                margin=dict(l=60, r=90, t=30, b=10),
                title=dict(text="^N225", font=dict(size=13, color=MUTED), x=0.01),
                dragmode="pan", hovermode="x unified",
                yaxis_title="Price", yaxis2_title="ADX", yaxis3_title="Vol",
                yaxis_tickformat=",.0f", yaxis2_tickformat=".0f", yaxis3_tickformat=".2s",
                legend=_LEGEND,
            )
            _apply_axes(fig, bottom_row=3)

        return fig

    except Exception as exc:
        logger.exception("Combined chart build error")
        return _empty_chart(f"Error: {exc}")


# ── Regime card ───────────────────────────────────────────────────────────────

def _breadth_row(label: str, snap: dict[str, float | bool] | None,
                 high_color: str = "#ff9800") -> html.Div | None:
    """Single breadth-indicator row: 'label: HIGH ▲   frac (cutoff, P)'."""
    if snap is None:
        return None
    frac = float(snap.get("frac", float("nan")))
    if math.isnan(frac):
        return None
    cutoff  = float(snap["cutoff"])
    is_high = bool(snap["is_high"])
    pct     = snap.get("percentile")
    state   = "HIGH ▲" if is_high else "normal"
    color   = high_color if is_high else MUTED
    pct_txt = f"P{float(pct):.0f}" if pct is not None and not math.isnan(float(pct)) else ""
    body    = f"breadth {frac*100:.1f}%  (cutoff {cutoff*100:.1f}%, {pct_txt})"
    return html.Div(
        style={"fontSize": "11px", "marginTop": "2px"},
        children=[
            html.Span(f"{label}: ", style={"color": MUTED}),
            html.Span(state, style={"color": color, "fontWeight": "600"}),
            html.Span(f"   {body}", style={"color": MUTED}),
        ],
    )


def _regime_risk_panel(
    revn_snap: dict[str, float | bool] | None,
    sma_snap:  dict[str, float | bool] | None,
    corr_snap: dict[str, float | bool] | None,
) -> html.Div | None:
    """Composite regime-risk panel.

    Two groupings:
      • Reversal Risk family (rev_nhi + SMA(50)) — compression-style signals.
        Highlight composite 'BOTH HIGH ▲▲' when the two AND-fire.
      • Diversification (CorrRegime) — lockstep / false-diversification gate;
        opposite-direction signal vs the reversal-risk family.
    """
    rows: list = []

    both_high = (
        revn_snap is not None
        and sma_snap is not None
        and bool(revn_snap.get("is_high"))
        and bool(sma_snap.get("is_high"))
    )
    if revn_snap is not None or sma_snap is not None:
        rows.append(html.Div(
            style={"color": MUTED, "fontSize": "10px",
                   "textTransform": "uppercase", "letterSpacing": "0.5px",
                   "marginTop": "8px"},
            children="Reversal Risk",
        ))
        if both_high:
            rows.append(html.Div(
                style={"fontSize": "11px", "marginTop": "2px"},
                children=[
                    html.Span("BOTH HIGH ▲▲", style={"color": RED, "fontWeight": "700"}),
                    html.Span("   (rev_nhi ∧ SMA50 — concentrated regime, fwd N225 ≈ 0%)",
                              style={"color": MUTED}),
                ],
            ))
        r1 = _breadth_row("rev_nhi", revn_snap)
        if r1 is not None: rows.append(r1)
        r2 = _breadth_row("SMA(50)", sma_snap)
        if r2 is not None: rows.append(r2)

    if corr_snap is not None:
        rows.append(html.Div(
            style={"color": MUTED, "fontSize": "10px",
                   "textTransform": "uppercase", "letterSpacing": "0.5px",
                   "marginTop": "8px"},
            children="Diversification",
        ))
        cr = _breadth_row("CorrRegime", corr_snap, high_color="#ba68c8")
        if cr is not None:
            rows.append(cr)
        if corr_snap.get("is_high"):
            rows.append(html.Div(
                "↳ high lockstep — multi-stock entries are false diversification",
                style={"color": MUTED, "fontSize": "10px", "marginLeft": "12px"},
            ))

    if not rows:
        return None
    return html.Div(style={"marginTop": "2px"}, children=rows)


def _regime_card(
    trade_date: datetime.date,
    regime: dict[str, Any] | None,
    n_proposals: int,
    revn_snap: dict[str, float | bool] | None = None,
    sma_snap:  dict[str, float | bool] | None = None,
    corr_snap: dict[str, float | bool] | None = None,
) -> list:
    if regime is None:
        return [
            html.Span(
                f"No proposals for {trade_date}  (weekend / holiday / no signals)",
                style={"color": MUTED, "fontSize": "13px"},
            )
        ]

    kumo_color = {1: GREEN, 0: "#ff9800", -1: RED}.get(regime["kumo_state"], TEXT)
    adx_color  = {"bear": "#ff9800", "bull": GREEN, "choppy": MUTED}.get(
        regime["adx_state"], MUTED
    )
    trend_line = (
        f"N225 {regime['kumo_text']}  ·  "
        f"ADX {regime['adx_state']} "
        f"(ADX={regime['adx']}, +DI={regime['adx_pos']}, −DI={regime['adx_neg']})"
    )

    children: list = [
        html.Div(
            style={"display": "flex", "justifyContent": "space-between",
                   "alignItems": "center", "marginBottom": "6px"},
            children=[
                html.Span(
                    f"N225 Regime — {trade_date}",
                    style={"color": MUTED, "fontSize": "11px",
                           "textTransform": "uppercase", "letterSpacing": "0.5px"},
                ),
                html.Span(
                    f"{n_proposals} proposal{'s' if n_proposals != 1 else ''}",
                    style={"color": ACCENT, "fontWeight": "600", "fontSize": "12px"},
                ),
            ],
        ),
        html.Div(
            style={"fontSize": "13px", "lineHeight": "1.7"},
            children=[
                html.Span("Kumo: ", style={"color": MUTED}),
                html.Span(regime["kumo_text"], style={"color": kumo_color, "fontWeight": "600"}),
                html.Span("   ADX: ", style={"color": MUTED}),
                html.Span(regime["adx_state"], style={"color": adx_color, "fontWeight": "600"}),
                html.Br(),
                html.Span(trend_line, style={"color": MUTED, "fontSize": "11px"}),
            ],
        ),
    ]
    panel = _regime_risk_panel(revn_snap, sma_snap, corr_snap)
    if panel is not None:
        children.append(panel)
    return children


# ── Decision factor panel ─────────────────────────────────────────────────────

def _factor_panel(row: dict[str, Any]) -> list:
    """Per-stock decision-factor panel for a selected proposal row.

    Implements docs/evaluation_criteria.md §5.11: every factor carries measured
    strength + sample size + provenance; cell-level aggregates live in a
    separate "context, not stock-specific" block; no A/B-negative factor shown.
    """
    sign   = row["sign"]
    stock  = row["stock"]
    sector = row.get("sector")

    def _header(text: str) -> html.Div:
        return html.Div(
            text,
            style={"color": MUTED, "fontSize": "10px", "textTransform": "uppercase",
                   "letterSpacing": "0.5px", "marginTop": "8px", "marginBottom": "4px"},
        )

    def _factor_row(label: str, body: str, *, tier: str, caption: str) -> html.Div:
        border = f"3px solid {GREEN}" if tier == "production" else f"3px dashed {MUTED}"
        return html.Div(
            style={"borderLeft": border, "padding": "3px 0 3px 8px", "marginBottom": "6px"},
            children=[
                html.Div([
                    html.Span(f"{label}: ", style={"color": MUTED, "fontSize": "12px"}),
                    html.Span(body, style={"color": TEXT, "fontSize": "12px",
                                           "fontWeight": "600"}),
                ]),
                html.Div(caption, style={"color": MUTED, "fontSize": "10px",
                                         "fontStyle": "italic"}),
            ],
        )

    children: list = [
        html.Div(
            row.get("name") or stock,
            style={"color": TEXT, "fontWeight": "700", "fontSize": "15px"},
        ),
        html.Div(
            f"Decision Factors — {stock} · {sign}",
            style={"color": ACCENT, "fontWeight": "600", "fontSize": "12px"},
        ),
        html.Div(
            "Nothing here is a guarantee — each factor shows its measured "
            "strength and evidence source.",
            style={"color": MUTED, "fontSize": "10px", "marginBottom": "4px"},
        ),
        _header("Per-stock factors"),
    ]

    # ── Corr mode (production) ──
    cm = row.get("corr") or "?"
    cn = row.get("corr_n225")
    cn_txt = f"ρ={cn:+.2f} vs N225" if cn is not None else "ρ unavailable"
    cm_interp = {
        "high": "index proxy — take one bet only (CLAUDE.md high-corr rule)",
        "low":  "independent alpha — genuine diversification",
        "mid":  "neither proxy nor clearly independent",
    }.get(cm, "")
    children.append(_factor_row(
        "Corr mode", f"{cm}  ({cn_txt})",
        tier="production",
        caption=f"{cm_interp}  ·  src: regime_sign 20-bar rolling corr",
    ))

    # ── Sector factor (experimental) ──
    cell = _SECTOR_FACTOR_DISPLAY.get((sign, sector)) if sector else None
    if cell:
        children.append(_factor_row(
            "Sector",
            f"{sector} — certified for {sign}: +{cell['delta_ev'] * 100:.2f}pp ΔEV",
            tier="experimental",
            caption=(f"OOS test n={cell['oos_test_n']} (≥100), OOS ΔDR {cell['oos_ddr']}  ·  "
                     f"src: {_SECTOR_PROVENANCE}"),
        ))
    else:
        children.append(_factor_row(
            "Sector", f"{sector or 'unknown'} — no certified factor for {sign} here",
            tier="experimental",
            caption=f"only 2 (sign, sector) cells are certified  ·  src: {_SECTOR_PROVENANCE}",
        ))

    # ── Context block — NOT stock-specific ──
    children.append(_header("Context — not stock-specific"))

    # Recommendation tier — derived from regime_ev alone; cell-level, shared by
    # every stock firing this sign in this regime (kept out of the per-stock
    # factor list per §5.11).
    tier = row.get("tier")
    if tier:
        tier_color = {"strong": GREEN, "moderate": "#ff9800",
                      "marginal": MUTED}.get(tier, MUTED)
        children.append(html.Div(
            style={"borderLeft": f"3px dotted {MUTED}", "padding": "3px 0 3px 8px",
                   "marginBottom": "4px"},
            children=[
                html.Div([
                    html.Span("Recommendation tier: ",
                              style={"color": MUTED, "fontSize": "12px"}),
                    html.Span(tier.upper(), style={"color": tier_color,
                              "fontSize": "12px", "fontWeight": "600"}),
                ]),
                html.Div(
                    "from regime_ev alone — top quartile of all regime cells = "
                    "strong  ·  cell-level, shared by every stock firing this "
                    "sign in this regime",
                    style={"color": MUTED, "fontSize": "10px", "fontStyle": "italic"},
                ),
            ],
        ))

    rn = row.get("regime_n") or 0
    dr = row.get("dr") or 0.0
    ev = row.get("ev") or 0.0
    cell_txt = (f"({sign}, kumo {row.get('kumo', '?')}) cell: "
                f"DR {dr:.1%}, EV {ev:+.4f}, n={rn}")
    if rn < _REGIME_MIN_READ_N:
        cell_caption = (f"n<{_REGIME_MIN_READ_N} — too small to read reliably (§5.1)  ·  "
                        "aggregate over every stock firing this sign in this regime")
        cell_color = MUTED
    else:
        cell_caption = ("aggregate over every stock firing this sign in this regime — "
                        "does NOT distinguish stocks  ·  src: benchmark.md § Regime-Split")
        cell_color = TEXT
    kumo_info = _KUMO_INFORMATIVENESS.get(sign, "unrated")
    children.append(html.Div(
        style={"borderLeft": f"3px dotted {MUTED}", "padding": "3px 0 3px 8px"},
        children=[
            html.Div(cell_txt, style={"color": cell_color, "fontSize": "12px"}),
            html.Div(cell_caption, style={"color": MUTED, "fontSize": "10px",
                                          "fontStyle": "italic"}),
            html.Div(
                f"Kumo as a factor for {sign}: {kumo_info}  ·  "
                "src: benchmark.md § Regime-Split",
                style={"color": MUTED, "fontSize": "10px", "fontStyle": "italic",
                       "marginTop": "2px"},
            ),
        ],
    ))
    return children


# ── Styles ────────────────────────────────────────────────────────────────────

_S_CARD: dict[str, Any] = {
    "background": CARD_BG, "border": f"1px solid {BORDER}",
    "borderRadius": "6px", "padding": "12px", "marginBottom": "12px",
    "color": TEXT,
}

# ── Layout ────────────────────────────────────────────────────────────────────

def layout() -> html.Div:
    today = datetime.date.today().isoformat()
    return html.Div(
        style={"display": "flex", "height": "calc(100vh - 44px)", "overflow": "hidden"},
        children=[
            # ── Left panel: controls + table ──────────────────────────────────
            html.Div(
                id="daily-sidebar",
                style={
                    "width": "460px", "minWidth": "460px",
                    "height": "100%", "overflowY": "auto",
                    "background": SIDEBAR_BG,
                    "borderRight": f"1px solid {BORDER}",
                    "padding": "16px", "boxSizing": "border-box",
                },
                children=[
                    dcc.Interval(
                        id="daily-update-interval",
                        interval=1500, n_intervals=0, disabled=True,
                    ),

                    # Account selector (synced via active-account-id Store)
                    html.Div(
                        style={"display": "flex", "alignItems": "center",
                               "gap": "8px", "marginBottom": "6px"},
                        children=[
                            html.Span(
                                "Account:",
                                style={"color": MUTED, "fontSize": "11px",
                                       "whiteSpace": "nowrap"},
                            ),
                            dcc.Dropdown(
                                id="daily-account-dropdown",
                                placeholder="(loading…)",
                                clearable=False,
                                style={"flex": "1", "fontSize": "12px",
                                       "color": "#000"},
                            ),
                        ],
                    ),

                    # Date picker + Refresh + Daily Update
                    html.Div(
                        style={"display": "flex", "alignItems": "center",
                               "gap": "8px", "marginBottom": "4px"},
                        children=[
                            dcc.DatePickerSingle(
                                id="daily-date",
                                date=today,
                                display_format="YYYY-MM-DD",
                                style={"fontSize": "13px"},
                            ),
                            html.Button(
                                "⟳ Refresh",
                                id="daily-refresh-btn",
                                n_clicks=0,
                                style={
                                    "background": ACCENT, "color": BG,
                                    "border": "none", "borderRadius": "4px",
                                    "padding": "7px 14px", "cursor": "pointer",
                                    "fontWeight": "600", "fontSize": "13px",
                                },
                            ),
                            html.Button(
                                "⬇ Update",
                                id="daily-update-btn",
                                n_clicks=0,
                                style={
                                    "background": "transparent", "color": MUTED,
                                    "border": f"1px solid {BORDER}", "borderRadius": "4px",
                                    "padding": "7px 12px", "cursor": "pointer",
                                    "fontWeight": "600", "fontSize": "12px",
                                },
                            ),
                        ],
                    ),
                    html.Div(
                        id="daily-update-status",
                        style=_S_UPDATE_HIDDEN,
                        children="",
                    ),

                    # Proposals progress bar (hidden until Refresh fires)
                    html.Div(
                        id="daily-progress-section",
                        style={"display": "none"},
                        children=[
                            html.Div(className="daily-progress-track"),
                            html.Div(
                                "Scanning proposals…",
                                style={
                                    "color": MUTED, "fontSize": "11px",
                                    "marginTop": "4px", "letterSpacing": "0.3px",
                                },
                            ),
                        ],
                    ),

                    # N225 regime card
                    html.Div(
                        id="daily-regime-card",
                        style=_S_CARD,
                        children=[
                            html.Span(
                                "Click Refresh to load today's proposals",
                                style={"color": MUTED, "fontSize": "13px"},
                            )
                        ],
                    ),

                    # Loading spinner wraps the table
                    dcc.Loading(
                        id="daily-loading",
                        type="circle",
                        color=ACCENT,
                        children=[
                            dash_table.DataTable(
                                id="daily-table",
                                columns=[
                                    {"name": "Stock",     "id": "stock"},
                                    {"name": "Sign",      "id": "sign"},
                                    {"name": "Corr",      "id": "corr"},
                                    {"name": "Kumo",      "id": "kumo"},
                                    {"name": "DR%",       "id": "dr_pct"},
                                    {"name": "EV",        "id": "ev"},
                                    {"name": "bench_flw", "id": "bench_flw"},
                                    {"name": "ADX",       "id": "adx"},
                                    {"name": "State",     "id": "adx_state"},
                                    {"name": "Fired",     "id": "fired_at"},
                                ],
                                data=[],
                                row_selectable="single",
                                selected_rows=[],
                                tooltip_delay=0,
                                tooltip_duration=None,
                                style_table={"overflowX": "auto", "minHeight": "40px"},
                                style_cell={
                                    "backgroundColor": CARD_BG, "color": TEXT,
                                    "fontSize": "12px", "padding": "5px 8px",
                                    "border": f"1px solid {BORDER}",
                                    "textAlign": "center", "whiteSpace": "normal",
                                },
                                style_header={
                                    "backgroundColor": SIDEBAR_BG, "color": MUTED,
                                    "fontWeight": "600", "border": f"1px solid {BORDER}",
                                    "fontSize": "11px", "textAlign": "center",
                                },
                                style_data_conditional=[
                                    {
                                        "if": {"state": "selected"},
                                        "backgroundColor": "#1f3a5f",
                                        "border": f"1px solid {ACCENT}",
                                    },
                                    {
                                        "if": {"filter_query": '{kumo} contains "above"'},
                                        "color": GREEN,
                                    },
                                    {
                                        "if": {"filter_query": '{kumo} contains "below"'},
                                        "color": RED,
                                    },
                                    {
                                        "if": {
                                            "filter_query": '{adx_state} = "bear"',
                                            "column_id": "adx_state",
                                        },
                                        "color": "#ff9800",
                                    },
                                    {
                                        "if": {
                                            "filter_query": '{adx_state} = "bull"',
                                            "column_id": "adx_state",
                                        },
                                        "color": GREEN,
                                    },
                                ],
                                sort_action="native",
                                sort_mode="single",
                                page_size=5,
                            ),
                        ],
                    ),

                    dcc.Store(id="daily-proposals-store"),
                    dcc.Store(id="daily-pos-selected-store"),
                    dcc.Store(id="daily-regime-snapshot"),

                    # Decision factors panel (shown when a row is selected)
                    html.Div(
                        id="daily-factor-panel",
                        style={"display": "none"},
                        children=[],
                    ),

                    # ── Register form (shown when a row is selected) ──────────
                    html.Div(
                        id="daily-register-panel",
                        style={
                            "display": "none",
                            "marginTop": "12px",
                            "background": CARD_BG,
                            "border": f"1px solid {BORDER}",
                            "borderRadius": "6px",
                            "padding": "12px",
                        },
                        children=[
                            html.Div(
                                style={"color": ACCENT, "fontWeight": "600",
                                       "fontSize": "12px", "marginBottom": "8px"},
                                children="Register Position",
                            ),
                            html.Div(id="daily-register-stock-label",
                                     style={"color": MUTED, "fontSize": "12px",
                                            "marginBottom": "8px"}),
                            html.Div(id="daily-existing-review-label",
                                     style={"display": "none"}),
                            html.Div(
                                style={"display": "flex", "gap": "8px",
                                       "alignItems": "center", "marginBottom": "8px",
                                       "flexWrap": "wrap"},
                                children=[
                                    html.Span("Direction:", style={"color": MUTED,
                                                                    "fontSize": "12px",
                                                                    "whiteSpace": "nowrap"}),
                                    dcc.Dropdown(
                                        id="daily-direction",
                                        options=[
                                            {"label": "Long  ↑", "value": "long"},
                                            {"label": "Short ↓", "value": "short"},
                                        ],
                                        value="long",
                                        clearable=False,
                                        style={
                                            "width": "110px", "fontSize": "12px",
                                            "color": "#000",
                                        },
                                    ),
                                    html.Span("Entry price:", style={"color": MUTED,
                                                                     "fontSize": "12px",
                                                                     "whiteSpace": "nowrap"}),
                                    dcc.Input(
                                        id="daily-entry-price",
                                        type="number",
                                        placeholder="price",
                                        debounce=True,
                                        style={
                                            "width": "100px", "background": BG,
                                            "color": TEXT, "border": f"1px solid {BORDER}",
                                            "borderRadius": "4px", "padding": "4px 8px",
                                            "fontSize": "12px",
                                        },
                                    ),
                                    html.Span("Units:", style={"color": MUTED,
                                                               "fontSize": "12px",
                                                               "whiteSpace": "nowrap"}),
                                    dcc.Input(
                                        id="daily-units",
                                        type="number",
                                        value=100,
                                        min=1,
                                        style={
                                            "width": "70px", "background": BG,
                                            "color": TEXT, "border": f"1px solid {BORDER}",
                                            "borderRadius": "4px", "padding": "4px 8px",
                                            "fontSize": "12px",
                                        },
                                    ),
                                ],
                            ),
                            html.Div(
                                id="daily-tp-sl-preview",
                                style={"color": MUTED, "fontSize": "11px", "marginBottom": "8px"},
                            ),
                            html.Div(
                                style={"display": "flex", "gap": "8px",
                                       "alignItems": "center", "flexWrap": "wrap"},
                                children=[
                                    html.Button(
                                        "Register",
                                        id="daily-register-btn",
                                        n_clicks=0,
                                        style={
                                            "background": GREEN, "color": BG,
                                            "border": "none", "borderRadius": "4px",
                                            "padding": "5px 14px", "cursor": "pointer",
                                            "fontWeight": "600", "fontSize": "12px",
                                        },
                                    ),
                                    html.Button(
                                        "Skip",
                                        id="daily-skip-btn",
                                        n_clicks=0,
                                        style={
                                            "background": "transparent", "color": MUTED,
                                            "border": f"1px solid {BORDER}", "borderRadius": "4px",
                                            "padding": "5px 14px", "cursor": "pointer",
                                            "fontWeight": "600", "fontSize": "12px",
                                        },
                                    ),
                                    dcc.Input(
                                        id="daily-decision-reason",
                                        type="text",
                                        placeholder="reason (optional)",
                                        debounce=True,
                                        style={
                                            "flex": "1", "minWidth": "120px",
                                            "background": BG, "color": TEXT,
                                            "border": f"1px solid {BORDER}",
                                            "borderRadius": "4px", "padding": "4px 8px",
                                            "fontSize": "12px",
                                        },
                                    ),
                                ],
                            ),
                            html.Span(id="daily-register-msg",
                                      style={"marginLeft": "10px", "fontSize": "12px"}),
                        ],
                    ),

                    # ── Open Positions panel ──────────────────────────────────
                    html.Div(
                        style={"marginTop": "16px"},
                        children=[
                            html.Div(
                                style={"display": "flex", "justifyContent": "space-between",
                                       "alignItems": "center", "marginBottom": "6px"},
                                children=[
                                    html.Span(
                                        "Open Positions",
                                        style={"color": MUTED, "fontSize": "11px",
                                               "textTransform": "uppercase",
                                               "letterSpacing": "0.5px"},
                                    ),
                                    html.Button(
                                        "⟳",
                                        id="daily-positions-refresh-btn",
                                        n_clicks=0,
                                        style={
                                            "background": "transparent",
                                            "color": MUTED,
                                            "border": f"1px solid {BORDER}",
                                            "borderRadius": "3px",
                                            "padding": "2px 8px",
                                            "cursor": "pointer",
                                            "fontSize": "12px",
                                        },
                                    ),
                                ],
                            ),
                            dcc.Loading(
                                type="circle", color=ACCENT,
                                children=[
                                    html.Div(id="daily-positions-panel",
                                             style={"fontSize": "12px"}),
                                ],
                            ),
                        ],
                    ),

                    # ── Memos panel ──────────────────────────────────────────
                    html.Div(
                        style={"marginTop": "16px"},
                        children=[
                            html.Div(
                                style={"display": "flex", "justifyContent": "space-between",
                                       "alignItems": "center", "marginBottom": "6px"},
                                children=[
                                    html.Span(
                                        "Memos",
                                        style={"color": MUTED, "fontSize": "11px",
                                               "textTransform": "uppercase",
                                               "letterSpacing": "0.5px"},
                                    ),
                                    html.Span(
                                        id="daily-memo-date-label",
                                        style={"color": MUTED, "fontSize": "10px"},
                                    ),
                                ],
                            ),
                            dcc.Textarea(
                                id="daily-memo-input",
                                placeholder="Write an idea for this day…",
                                style={
                                    "width": "100%", "minHeight": "60px",
                                    "background": BG, "color": TEXT,
                                    "border": f"1px solid {BORDER}",
                                    "borderRadius": "4px",
                                    "padding": "6px 8px", "fontSize": "12px",
                                    "resize": "vertical",
                                    "fontFamily": "'Segoe UI', Arial, sans-serif",
                                    "boxSizing": "border-box",
                                },
                            ),
                            html.Div(
                                style={"display": "flex", "alignItems": "center",
                                       "gap": "8px", "marginTop": "6px"},
                                children=[
                                    html.Button(
                                        "Save Memo",
                                        id="daily-memo-save-btn",
                                        n_clicks=0,
                                        style={
                                            "background": ACCENT, "color": BG,
                                            "border": "none", "borderRadius": "4px",
                                            "padding": "5px 12px", "cursor": "pointer",
                                            "fontWeight": "600", "fontSize": "12px",
                                        },
                                    ),
                                    html.Button(
                                        "Cancel Edit",
                                        id="daily-memo-cancel-btn",
                                        n_clicks=0,
                                        style={
                                            "display": "none",
                                            "background": "transparent",
                                            "color": MUTED,
                                            "border": f"1px solid {BORDER}",
                                            "borderRadius": "4px",
                                            "padding": "5px 12px", "cursor": "pointer",
                                            "fontSize": "11px",
                                        },
                                    ),
                                    html.Span(id="daily-memo-msg",
                                              style={"fontSize": "11px"}),
                                ],
                            ),
                            html.Div(
                                id="daily-memo-list",
                                style={"marginTop": "8px", "fontSize": "12px"},
                            ),
                            dcc.Store(id="daily-memo-editing-id"),
                        ],
                    ),
                ],
            ),

            # ── Right panel: single combined chart (shared x-axis) ───────────
            html.Div(
                style={"flex": "1", "height": "100%", "overflow": "hidden", "background": BG},
                children=[
                    dcc.Graph(
                        id="daily-chart",
                        style={"height": "100%"},
                        config={
                            "scrollZoom": True,
                            "displayModeBar": True,
                            "modeBarButtonsToRemove": ["autoScale2d", "lasso2d", "select2d"],
                            "toImageButtonOptions": {
                                "format": "png", "width": 1920, "height": 1080,
                                "filename": "daily_chart",
                            },
                        },
                        figure=_empty_chart("Loading ^N225 …"),
                    ),
                ],
            ),
        ],
    )


# ── Callbacks ─────────────────────────────────────────────────────────────────

def register_callbacks() -> None:

    # ── Account selector — populate options and two-way sync with Store ───
    @callback(
        Output("daily-account-dropdown", "options"),
        Output("daily-account-dropdown", "value"),
        Output("active-account-id",      "data"),
        Input("daily-account-dropdown",  "value"),
        Input("active-account-id",       "data"),
    )
    def sync_daily_account(dropdown_val, store_val):
        with get_session() as session:
            accts = list_accounts(session)
        options = [{"label": a.name, "value": a.id} for a in accts]
        trig = callback_context.triggered_id
        if trig == "daily-account-dropdown" and dropdown_val is not None:
            chosen = dropdown_val
        elif store_val is not None:
            chosen = store_val
        else:
            chosen = accts[0].id if accts else None
        return options, chosen, chosen


    @callback(
        Output("daily-proposals-store", "data"),
        Output("daily-regime-card", "children"),
        Output("daily-regime-snapshot", "data"),
        Input("daily-refresh-btn", "n_clicks"),
        Input("daily-date", "date"),
        running=[
            (Output("daily-progress-section", "style"),
             {"display": "block", "margin": "8px 0"},
             {"display": "none"}),
        ],
        prevent_initial_call=True,
    )
    def refresh_proposals(n_clicks: int, date_str: str | None) -> tuple:
        if not date_str:
            return no_update, no_update, no_update

        try:
            target    = datetime.date.fromisoformat(date_str[:10])
            _strategy_cache.clear()       # Refresh = "give me fresh data" — drop any pre-download cache
            _revn_regime_cache.clear()    # invalidate regime indicators alongside the strategy
            _sma_regime_cache.clear()
            _corr_regime_cache.clear()
            strategy  = _get_strategy(target)
            tz        = datetime.timezone.utc
            target_dt = datetime.datetime(
                target.year, target.month, target.day, 15, 0, 0, tzinfo=tz
            )
            proposals = strategy.propose(target_dt)
            logger.info("Daily proposals for {}: {} results", target, len(proposals))
        except ValueError as exc:
            msg = str(exc)
            if "StockClusterRun" in msg:
                hint = (
                    "Stock universe not configured for this environment. "
                    "Run cluster analysis to set up classified2024."
                )
            else:
                hint = f"Data error: {msg}. Use Maintenance → Download OHLCV to refresh market data."
            logger.warning("refresh_proposals ValueError: {}", msg)
            return None, [html.Span(hint, style={"color": "#ff9800", "fontSize": "13px"})], None
        except Exception as exc:
            logger.exception("refresh_proposals error")
            hint = (
                f"Not enough data for {date_str[:10]}. "
                "Use Maintenance → Download OHLCV to fetch latest market data, then Refresh."
            )
            return None, [
                html.Span(hint, style={"color": MUTED, "fontSize": "13px"}),
                html.Br(),
                html.Span(f"({type(exc).__name__}: {exc})",
                          style={"color": RED, "fontSize": "11px"}),
            ], None

        regime = _regime_from_proposals(proposals)
        def _snap_or_none(builder, name):
            try:
                ind = builder(target, strategy)
                return {
                    "frac":       ind.frac(target),
                    "is_high":    ind.is_high(target),
                    "cutoff":     ind.cutoff,
                    "percentile": ind.percentile(target) if hasattr(ind, "percentile") else float("nan"),
                }
            except Exception as exc:
                logger.warning("{} build failed: {}", name, exc)
                return None
        revn_snap = _snap_or_none(_get_revn_regime, "RevNRegime")
        sma_snap  = _snap_or_none(_get_sma_regime,  "SMARegime")
        corr_snap = _snap_or_none(_get_corr_regime, "CorrRegime")
        card   = _regime_card(target, regime, len(proposals),
                              revn_snap=revn_snap, sma_snap=sma_snap, corr_snap=corr_snap)
        ranking_evs = [e.ev for e in strategy._ranking.values()]

        def _frac_of(snap):
            if snap is None: return None
            v = snap.get("frac")
            try: v = float(v)
            except (TypeError, ValueError): return None
            return None if math.isnan(v) else v
        regime_snapshot = {
            "revn_frac": _frac_of(revn_snap),
            "sma_frac":  _frac_of(sma_snap),
            "corr_frac": _frac_of(corr_snap),
        }
        return _proposals_to_json(proposals, ranking_evs), card, regime_snapshot

    @callback(
        Output("daily-table", "data"),
        Output("daily-table", "tooltip_data"),
        Input("daily-proposals-store", "data"),
    )
    def update_table(store_data: str | None) -> tuple:
        if not store_data:
            return [], []
        rows = json.loads(store_data)
        table_rows = [
            {
                "stock":     r["stock"],
                "sign":      r["sign"],
                "corr":      r["corr"],
                "kumo":      r["kumo"],
                "dr_pct":    f"{r['dr']:.1%}",
                "ev":        f"{r['ev']:+.4f}",
                "bench_flw": f"{r['bench_flw']:.4f}",
                "adx":       f"{r['adx']:.1f}",
                "adx_state": r["adx_state"],
                "fired_at":  r["fired_at"],
            }
            for r in rows
        ]
        tooltip_data = [
            {
                "sign": {
                    "value": SIGN_DESCRIPTIONS.get(r["sign"], r["sign"]),
                    "type":  "markdown",
                }
            }
            for r in rows
        ]
        return table_rows, tooltip_data

    @callback(
        Output("daily-factor-panel", "children"),
        Output("daily-factor-panel", "style"),
        Input("daily-table", "selected_rows"),
        Input("daily-proposals-store", "data"),
    )
    def update_factor_panel(
        selected_rows: list[int], store_data: str | None
    ) -> tuple:
        hidden = {"display": "none"}
        visible = {
            "display": "block", "marginTop": "12px",
            "background": CARD_BG, "border": f"1px solid {BORDER}",
            "borderRadius": "6px", "padding": "12px",
        }
        if not selected_rows or not store_data:
            return [], hidden
        rows = json.loads(store_data)
        if selected_rows[0] >= len(rows):
            return [], hidden
        return _factor_panel(rows[selected_rows[0]]), visible

    @callback(
        Output("daily-chart", "figure"),
        Input("daily-date",                "date"),
        Input("daily-table",               "selected_rows"),
        Input("daily-proposals-store",     "data"),
        Input("daily-pos-selected-store",  "data"),
        Input("daily-entry-price",         "value"),
    )
    def update_charts(
        date_str: str | None,
        selected_rows: list[int],
        store_data: str | None,
        pos_data: dict | None,
        entry_price: float | None,
    ) -> go.Figure:
        """Single combined figure — stock + N225 share one x-axis (no sync needed)."""
        target = (
            datetime.date.fromisoformat(date_str[:10])
            if date_str else datetime.date.today()
        )
        triggered = callback_context.triggered_id
        # Position card click takes priority — use the stored TP/SL
        if triggered == "daily-pos-selected-store" and pos_data:
            return _build_combined_chart(
                target, stock_row=pos_data,
                tp_price=pos_data.get("tp"),
                sl_price=pos_data.get("sl"),
            )
        # Proposal table row — compute preview TP/SL from current entry-price input
        if selected_rows and store_data:
            rows = json.loads(store_data)
            if selected_rows[0] < len(rows):
                row = rows[selected_rows[0]]
                tp = sl = None
                if entry_price is not None:
                    try:
                        fired = datetime.date.fromisoformat(row["fired_at"])
                        tp, sl = compute_exit_levels(
                            row["stock"], float(entry_price), fired,
                        )
                    except Exception:
                        logger.exception("update_charts TP/SL preview failed")
                return _build_combined_chart(
                    target, stock_row=row, tp_price=tp, sl_price=sl,
                )
        return _build_combined_chart(target)

    # ── Portfolio: show register panel when row selected ─────────────────────

    @callback(
        Output("daily-register-panel", "style"),
        Output("daily-register-stock-label", "children"),
        Output("daily-entry-price", "value"),
        Input("daily-table", "selected_rows"),
        Input("daily-proposals-store", "data"),
    )
    def show_register_panel(
        selected_rows: list[int], store_data: str | None
    ) -> tuple:
        hidden = {"display": "none"}
        visible = {
            "display": "block", "marginTop": "12px",
            "background": CARD_BG, "border": f"1px solid {BORDER}",
            "borderRadius": "6px", "padding": "12px",
        }
        if not selected_rows or not store_data:
            return hidden, "", None
        rows = json.loads(store_data)
        if selected_rows[0] >= len(rows):
            return hidden, "", None
        row  = rows[selected_rows[0]]
        # Default entry price = next bar's open after fired_at (two-bar fill rule).
        # Falls back to close-on-fired then latest if no next bar exists.
        try:
            fired = datetime.date.fromisoformat(row["fired_at"])
            price = get_entry_price_for_fire(row["stock"], fired)
        except Exception:
            price = get_latest_price(row["stock"])
        label = (
            f"{row['stock']}  ·  {row['sign']}  ·  "
            f"corr={row['corr']}  kumo={row['kumo']}  "
            f"fired={row['fired_at']}"
        )
        return visible, label, price

    @callback(
        Output("daily-existing-review-label", "children"),
        Output("daily-existing-review-label", "style"),
        Input("daily-table",             "selected_rows"),
        Input("daily-proposals-store",   "data"),
        Input("daily-register-msg",      "children"),  # re-query after Skip / Register
    )
    def show_existing_review(
        selected_rows: list[int],
        store_data: str | None,
        _msg: object,
    ) -> tuple:
        hidden = {"display": "none"}
        if not selected_rows or not store_data:
            return "", hidden
        rows = json.loads(store_data)
        if selected_rows[0] >= len(rows):
            return "", hidden
        row = rows[selected_rows[0]]
        try:
            fired = datetime.date.fromisoformat(row["fired_at"])
            with get_session() as session:
                existing = session.execute(
                    select(ReviewedCandidate)
                    .where(
                        ReviewedCandidate.fired_at   == fired,
                        ReviewedCandidate.stock_code == row["stock"],
                        ReviewedCandidate.sign_type  == row["sign"],
                    )
                    .order_by(ReviewedCandidate.reviewed_at.desc())
                ).scalars().first()
        except Exception:
            logger.exception("show_existing_review query failed")
            return "", hidden
        if existing is None:
            return "", hidden

        ts = existing.reviewed_at.strftime("%H:%M") if existing.reviewed_at else ""
        base = {
            "fontSize": "11px", "marginBottom": "8px",
            "padding": "4px 8px", "borderRadius": "3px",
            "display": "block",
        }
        if existing.action == "skipped":
            reason_txt = existing.reason or "(no reason recorded)"
            children = [
                html.Span("⊘ Previously skipped",
                          style={"color": "#ffb74d", "fontWeight": "600"}),
                html.Span(f"  ({ts})  ", style={"color": MUTED}),
                html.Span(reason_txt, style={"color": TEXT}),
            ]
            style = {**base, "background": "#2a2520",
                     "border": "1px solid #503a25"}
        else:
            children = [
                html.Span("✓ Already registered",
                          style={"color": GREEN, "fontWeight": "600"}),
                html.Span(f"  (position id={existing.position_id}, {ts})",
                          style={"color": MUTED}),
            ]
            style = {**base, "background": "#1a2a1f",
                     "border": f"1px solid {GREEN}"}
        return children, style


    @callback(
        Output("daily-tp-sl-preview", "children"),
        Input("daily-entry-price", "value"),
        Input("daily-table", "selected_rows"),
        Input("daily-proposals-store", "data"),
    )
    def preview_tp_sl(
        entry_price: float | None,
        selected_rows: list[int],
        store_data: str | None,
    ) -> str:
        if entry_price is None or not selected_rows or not store_data:
            return ""
        rows = json.loads(store_data)
        if selected_rows[0] >= len(rows):
            return ""
        row    = rows[selected_rows[0]]
        fired  = datetime.date.fromisoformat(row["fired_at"])
        tp, sl = compute_exit_levels(row["stock"], float(entry_price), fired)
        if tp is None:
            return "TP/SL: could not compute (insufficient zigzag history)"
        return f"TP: {tp:,.0f}  ·  SL: {sl:,.0f}  (ZsTpSl 2.0/2.0/0.3)"

    @callback(
        Output("daily-register-msg", "children"),
        Output("daily-register-msg", "style"),
        Input("daily-register-btn", "n_clicks"),
        Input("daily-skip-btn",     "n_clicks"),
        State("daily-direction",         "value"),
        State("daily-entry-price",       "value"),
        State("daily-units",             "value"),
        State("daily-table",             "selected_rows"),
        State("daily-proposals-store",   "data"),
        State("daily-date",              "date"),
        State("daily-regime-snapshot",   "data"),
        State("daily-decision-reason",   "value"),
        State("active-account-id",       "data"),
        prevent_initial_call=True,
    )
    def decision_btn_click(
        n_register:    int,
        n_skip:        int,
        direction:     str | None,
        entry_price:   float | None,
        units:         int | None,
        selected_rows: list[int],
        store_data:    str | None,
        date_str:      str | None,
        regime_snap:   dict | None,
        reason:        str | None,
        account_id:    int | None,
    ) -> tuple:
        ok_style    = {"marginLeft": "10px", "fontSize": "12px", "color": GREEN}
        err_style   = {"marginLeft": "10px", "fontSize": "12px", "color": RED}
        skip_style  = {"marginLeft": "10px", "fontSize": "12px", "color": MUTED}

        trig = callback_context.triggered_id
        if trig not in ("daily-register-btn", "daily-skip-btn"):
            return no_update, no_update
        if not selected_rows or not store_data:
            return "Select a proposal row first.", err_style
        rows = json.loads(store_data)
        if selected_rows[0] >= len(rows):
            return "Invalid row.", err_style
        row = rows[selected_rows[0]]

        revn_f = sma_f = corr_f = None
        if isinstance(regime_snap, dict):
            revn_f = regime_snap.get("revn_frac")
            sma_f  = regime_snap.get("sma_frac")
            corr_f = regime_snap.get("corr_frac")
        sign_score = row.get("score")
        try:
            sign_score = float(sign_score) if sign_score is not None else None
        except (TypeError, ValueError):
            sign_score = None
        corr_n225_v = row.get("corr_val")
        try:
            corr_n225_v = float(corr_n225_v) if corr_n225_v is not None else None
        except (TypeError, ValueError):
            corr_n225_v = None
        clean_reason = (reason or "").strip() or None
        fired = datetime.date.fromisoformat(row["fired_at"])

        if trig == "daily-skip-btn":
            try:
                with get_session() as session:
                    rv = register_review(
                        session    = session,
                        fired_at   = fired,
                        stock_code = row["stock"],
                        sign_type  = row["sign"],
                        action     = "skipped",
                        sign_score = sign_score,
                        corr_mode  = row["corr"],
                        corr_n225  = corr_n225_v,
                        kumo_state = row["kumo_int"],
                        reason     = clean_reason,
                        revn_frac  = revn_f,
                        sma_frac   = sma_f,
                        corr_frac  = corr_f,
                        account_id = account_id,
                    )
                return f"Skipped {row['stock']} (review id={rv.id})", skip_style
            except Exception as exc:
                logger.exception("skip_btn error")
                return f"Skip error: {exc}", err_style

        if entry_price is None or units is None:
            return "Enter price and units first.", err_style
        try:
            today     = datetime.date.fromisoformat(date_str[:10]) if date_str else datetime.date.today()
            tp, sl    = compute_exit_levels(row["stock"], float(entry_price), fired)
            with get_session() as session:
                pos = register_position(
                    session     = session,
                    stock_code  = row["stock"],
                    sign_type   = row["sign"],
                    corr_mode   = row["corr"],
                    kumo_state  = row["kumo_int"],
                    fired_at    = fired,
                    entry_date  = today,
                    entry_price = float(entry_price),
                    direction   = direction or "long",
                    units       = int(units),
                    tp_price    = tp,
                    sl_price    = sl,
                    sign_score  = sign_score,
                    corr_n225   = corr_n225_v,
                    revn_frac   = revn_f,
                    sma_frac    = sma_f,
                    corr_frac   = corr_f,
                    reason      = clean_reason,
                    account_id  = account_id,
                )
            tp_s = f"{tp:,.0f}" if tp is not None else "—"
            sl_s = f"{sl:,.0f}" if sl is not None else "—"
            return f"Saved (id={pos.id})  TP={tp_s}  SL={sl_s}", ok_style
        except Exception as exc:
            logger.exception("register_btn error")
            return f"Error: {exc}", err_style

    # ── Portfolio: open positions panel ──────────────────────────────────────

    @callback(
        Output("daily-positions-panel", "children"),
        Input("daily-positions-refresh-btn", "n_clicks"),
        Input("daily-register-btn", "n_clicks"),
        Input("active-account-id", "data"),
    )
    def refresh_positions(_r: int, _reg: int, account_id: int | None) -> list:
        try:
            with get_session() as session:
                positions = get_open_positions(session, account_id=account_id)
                # detach — read all needed attrs inside session
                rows = [
                    {
                        "id":           p.id,
                        "stock":        p.stock_code,
                        "sign":         p.sign_type,
                        "direction":    getattr(p, "direction", "long"),
                        "entry_date":   str(p.entry_date),
                        "entry_price":  float(p.entry_price),
                        "units":        p.units,
                        "tp":           float(p.tp_price) if p.tp_price else None,
                        "sl":           float(p.sl_price) if p.sl_price else None,
                    }
                    for p in positions
                ]
        except Exception as exc:
            logger.exception("refresh_positions error")
            return [html.Span(f"Error: {exc}", style={"color": RED})]

        if not rows:
            return [html.Span("No open positions.", style={"color": MUTED})]

        # Enrich with current price
        items = []
        for r in rows:
            cur = get_latest_price(r["stock"])
            if cur and r["entry_price"]:
                pnl_pct = (cur / r["entry_price"] - 1.0) * 100.0
            else:
                pnl_pct = None

            if cur and r["tp"] and cur >= r["tp"]:
                status_text, status_color = "TP hit", GREEN
            elif cur and r["sl"] and cur <= r["sl"]:
                status_text, status_color = "SL hit", RED
            else:
                status_text, status_color = "Hold", ACCENT

            pnl_color = GREEN if pnl_pct and pnl_pct >= 0 else RED
            pnl_str   = f"{pnl_pct:+.1f}%" if pnl_pct is not None else "—"
            cur_str   = f"{cur:,.0f}" if cur else "—"
            tp_str    = f"{r['tp']:,.0f}" if r["tp"] else "—"
            sl_str    = f"{r['sl']:,.0f}" if r["sl"] else "—"

            _pos_idx = (
                f"{r['id']}|{r['stock']}|{r['sign']}|{r['entry_date']}"
                f"|{r['entry_price']}|{r.get('tp', '')}|{r.get('sl', '')}"
                f"|{r.get('direction', 'long')}"
            )
            items.append(
                html.Div(
                    style={
                        "background": BG, "border": f"1px solid {BORDER}",
                        "borderRadius": "4px", "padding": "8px",
                        "marginBottom": "6px",
                    },
                    children=[
                        # Clickable area — header + detail (close button kept separate
                        # to avoid event-bubbling triggering chart when closing)
                        html.Div(
                            id={"type": "pos-card", "index": _pos_idx},
                            n_clicks=0,
                            style={"cursor": "pointer"},
                            children=[
                                # Header row
                                html.Div(
                                    style={"display": "flex",
                                           "justifyContent": "space-between",
                                           "marginBottom": "4px"},
                                    children=[
                                        html.Span(
                                            f"{r['stock']}  ·  {r['sign']}  ·  {r['direction'].upper()}",
                                            style={"color": TEXT, "fontWeight": "600",
                                                   "fontSize": "12px"},
                                        ),
                                        html.Span(
                                            status_text,
                                            style={"color": status_color,
                                                   "fontWeight": "600",
                                                   "fontSize": "12px"},
                                        ),
                                    ],
                                ),
                                # Detail row
                                html.Div(
                                    style={"color": MUTED, "fontSize": "11px",
                                           "display": "flex", "gap": "12px",
                                           "flexWrap": "wrap"},
                                    children=[
                                        html.Span(f"Entry {r['entry_price']:,.0f} ({r['entry_date']})"),
                                        html.Span(f"Cur {cur_str}",
                                                  style={"color": pnl_color}),
                                        html.Span(f"P&L {pnl_str}",
                                                  style={"color": pnl_color}),
                                        html.Span(f"TP {tp_str}", style={"color": GREEN}),
                                        html.Span(f"SL {sl_str}", style={"color": RED}),
                                        html.Span(f"×{r['units']}"),
                                    ],
                                ),
                            ],
                        ),
                        # Close button + reason dropdown — separate div so clicks don't bubble to pos-card
                        html.Div(
                            style={"marginTop": "6px", "display": "flex",
                                   "gap": "6px", "alignItems": "center"},
                            children=[
                                html.Button(
                                    "Close Position",
                                    id={"type": "close-pos-btn", "index": r["id"]},
                                    n_clicks=0,
                                    style={
                                        "background": "transparent",
                                        "color": MUTED,
                                        "border": f"1px solid {BORDER}",
                                        "borderRadius": "3px",
                                        "padding": "2px 10px",
                                        "cursor": "pointer",
                                        "fontSize": "11px",
                                    },
                                ),
                                dcc.Dropdown(
                                    id={"type": "close-reason", "index": r["id"]},
                                    options=[
                                        {"label": "manual",    "value": "manual"},
                                        {"label": "tp_hit",    "value": "tp_hit"},
                                        {"label": "sl_hit",    "value": "sl_hit"},
                                        {"label": "time_stop", "value": "time_stop"},
                                    ],
                                    value="manual",
                                    clearable=False,
                                    style={"width": "120px", "fontSize": "11px",
                                           "color": "#000"},
                                ),
                            ],
                        ),
                    ],
                )
            )
        return items

    @callback(
        Output("daily-pos-selected-store", "data"),
        Input({"type": "pos-card", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def select_pos_card(n_clicks_list: list[int]) -> dict | None:
        if not any(n for n in (n_clicks_list or []) if n):
            return no_update  # type: ignore[return-value]
        triggered = callback_context.triggered_id
        if not triggered:
            return no_update  # type: ignore[return-value]
        parts = triggered["index"].split("|")
        if len(parts) < 8:
            return no_update  # type: ignore[return-value]
        _, stock, sign, entry_date, ep_str, tp_str, sl_str, direction = parts
        return {
            "stock":        stock,
            "sign":         sign,
            "fired_at":     entry_date,
            "entry_date":   entry_date,
            "entry_price":  float(ep_str) if ep_str else None,
            "tp":           float(tp_str) if tp_str else None,
            "sl":           float(sl_str) if sl_str else None,
            "direction":    direction,
            "is_position":  True,
        }

    @callback(
        Output("daily-positions-panel", "children", allow_duplicate=True),
        Input({"type": "close-pos-btn", "index": ALL}, "n_clicks"),
        State({"type": "close-reason", "index": ALL}, "value"),
        State({"type": "close-reason", "index": ALL}, "id"),
        State("active-account-id", "data"),
        prevent_initial_call=True,
    )
    def close_position_btn(n_clicks_list: list[int],
                            reasons: list[str | None],
                            reason_ids: list[dict],
                            account_id: int | None) -> list:
        triggered = callback_context.triggered
        if not triggered or not any(n for n in (n_clicks_list or []) if n):
            return no_update  # type: ignore[return-value]
        pos_id = callback_context.triggered_id["index"]
        # Match the per-position reason value by id
        chosen_reason: str | None = "manual"
        for rid, rv in zip(reason_ids, reasons):
            if rid.get("index") == pos_id:
                chosen_reason = rv or "manual"
                break
        try:
            with get_session() as session:
                pos = session.get(Position, pos_id)
                cur = get_latest_price(pos.stock_code) if pos else None
                close_position(session, pos_id, exit_price=cur or 0.0,
                                exit_reason=chosen_reason)
        except Exception as exc:
            logger.exception("close_position error for id={}", pos_id)
        return refresh_positions(0, 0, account_id)

    # ── Memos panel ──────────────────────────────────────────────────────────

    def _memo_card(memo) -> html.Div:
        ts_label = ""
        if memo.created_at:
            ts_label = memo.created_at.strftime("%H:%M")
            if memo.updated_at and memo.updated_at != memo.created_at:
                ts_label += f"  (edited {memo.updated_at.strftime('%H:%M')})"
        btn_style = {
            "background": "transparent",
            "color": MUTED,
            "border": f"1px solid {BORDER}",
            "borderRadius": "3px",
            "padding": "1px 8px",
            "cursor": "pointer",
            "fontSize": "10px",
            "marginLeft": "4px",
        }
        return html.Div(
            style={"padding": "8px", "background": CARD_BG,
                   "border": f"1px solid {BORDER}",
                   "borderRadius": "4px", "marginBottom": "6px"},
            children=[
                html.Div(
                    style={"display": "flex", "justifyContent": "space-between",
                           "alignItems": "center", "marginBottom": "4px"},
                    children=[
                        html.Span(ts_label, style={"color": MUTED, "fontSize": "10px"}),
                        html.Div([
                            html.Button("Edit",
                                        id={"type": "memo-edit", "index": memo.id},
                                        n_clicks=0, style=btn_style),
                            html.Button("Delete",
                                        id={"type": "memo-del", "index": memo.id},
                                        n_clicks=0,
                                        style={**btn_style, "color": RED, "borderColor": RED}),
                        ]),
                    ],
                ),
                html.Div(
                    memo.content,
                    style={"whiteSpace": "pre-wrap", "color": TEXT, "fontSize": "12px"},
                ),
            ],
        )

    def _render_memo_list(memo_date: datetime.date) -> list:
        with get_session() as session:
            memos = get_memos_for_date(session, memo_date)
        if not memos:
            return [html.Div(
                "No memos for this day yet.",
                style={"color": MUTED, "fontSize": "11px", "fontStyle": "italic"},
            )]
        return [_memo_card(m) for m in memos]

    @callback(
        Output("daily-memo-date-label",  "children"),
        Output("daily-memo-list",        "children"),
        Output("daily-memo-input",       "value"),
        Output("daily-memo-msg",         "children"),
        Output("daily-memo-msg",         "style"),
        Output("daily-memo-editing-id",  "data"),
        Output("daily-memo-cancel-btn",  "style"),
        Input("daily-date",              "date"),
        Input("daily-memo-save-btn",     "n_clicks"),
        Input("daily-memo-cancel-btn",   "n_clicks"),
        Input({"type": "memo-edit", "index": ALL}, "n_clicks"),
        Input({"type": "memo-del",  "index": ALL}, "n_clicks"),
        State("daily-memo-input",        "value"),
        State("daily-memo-editing-id",   "data"),
    )
    def memo_controller(
        date_str:   str | None,
        save_n:     int | None,
        cancel_n:   int | None,
        edit_clicks: list[int],
        del_clicks:  list[int],
        memo_text:  str | None,
        editing_id: int | None,
    ) -> tuple:
        cancel_visible = {
            "background": "transparent", "color": MUTED,
            "border": f"1px solid {BORDER}", "borderRadius": "4px",
            "padding": "5px 12px", "cursor": "pointer", "fontSize": "11px",
        }
        cancel_hidden = {**cancel_visible, "display": "none"}
        ok_msg  = {"color": GREEN, "fontSize": "11px"}
        err_msg = {"color": RED,   "fontSize": "11px"}
        muted_msg = {"color": MUTED, "fontSize": "11px"}

        if not date_str:
            return (no_update,) * 7
        target = datetime.date.fromisoformat(date_str[:10])
        date_label = f"{target}"

        trig = callback_context.triggered_id
        if trig is None:
            # initial load
            return (date_label, _render_memo_list(target), "", "", muted_msg, None, cancel_hidden)

        # Date picker change → refresh list, clear editing
        if trig == "daily-date":
            return (date_label, _render_memo_list(target), "", "", muted_msg, None, cancel_hidden)

        # Cancel edit
        if trig == "daily-memo-cancel-btn":
            return (date_label, _render_memo_list(target), "", "", muted_msg, None, cancel_hidden)

        # Save
        if trig == "daily-memo-save-btn":
            if not (memo_text and memo_text.strip()):
                return (date_label, no_update, no_update, "Empty memo.", err_msg, no_update, no_update)
            try:
                with get_session() as session:
                    if editing_id:
                        m = update_memo(session, int(editing_id), memo_text)
                        msg = f"Updated memo id={m.id}"
                    else:
                        m = create_memo(session, target, memo_text)
                        msg = f"Saved memo id={m.id}"
                return (date_label, _render_memo_list(target), "", msg, ok_msg, None, cancel_hidden)
            except Exception as exc:
                logger.exception("memo save error")
                return (date_label, no_update, no_update, f"Error: {exc}", err_msg, no_update, no_update)

        # Edit (pattern-matched id)
        if isinstance(trig, dict) and trig.get("type") == "memo-edit":
            mid = trig["index"]
            try:
                with get_session() as session:
                    from src.portfolio.models import Memo as _M
                    m = session.get(_M, mid)
                    if m is None:
                        return (date_label, _render_memo_list(target), no_update,
                                "Memo not found.", err_msg, None, cancel_hidden)
                    return (date_label, _render_memo_list(target), m.content,
                            f"Editing memo id={mid}", muted_msg, mid, cancel_visible)
            except Exception as exc:
                logger.exception("memo edit error")
                return (date_label, no_update, no_update, f"Error: {exc}", err_msg, no_update, no_update)

        # Delete (pattern-matched id)
        if isinstance(trig, dict) and trig.get("type") == "memo-del":
            mid = trig["index"]
            try:
                with get_session() as session:
                    delete_memo(session, int(mid))
                return (date_label, _render_memo_list(target), "",
                        f"Deleted memo id={mid}", muted_msg, None, cancel_hidden)
            except Exception as exc:
                logger.exception("memo delete error")
                return (date_label, no_update, no_update, f"Error: {exc}", err_msg, no_update, no_update)

        return (no_update,) * 7

    # ── Daily OHLCV update ────────────────────────────────────────────────────

    @callback(
        Output("daily-update-interval", "disabled"),
        Output("daily-update-status",   "children"),
        Output("daily-update-status",   "style"),
        Input("daily-update-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _start_daily_update(n: int | None) -> tuple:
        global _update_running, _update_msg
        with _update_lock:
            if _update_running:
                return False, _update_msg, _S_UPDATE_RUN
            _update_running = True
            _update_msg = "Starting …"
        threading.Thread(target=_run_daily_update, daemon=True).start()
        return False, "Starting …", _S_UPDATE_RUN

    @callback(
        Output("daily-update-status",   "children", allow_duplicate=True),
        Output("daily-update-status",   "style",    allow_duplicate=True),
        Output("daily-update-interval", "disabled", allow_duplicate=True),
        Input("daily-update-interval",  "n_intervals"),
        prevent_initial_call=True,
    )
    def _poll_daily_update(n: int) -> tuple:
        with _update_lock:
            msg     = _update_msg
            running = _update_running
        done  = msg.startswith("✓")
        style = _S_UPDATE_DONE if done else _S_UPDATE_RUN
        return msg, style, not running

