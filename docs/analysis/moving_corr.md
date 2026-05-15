# Moving Correlation Analysis

Per-bar rolling Pearson correlation of *returns* between a stock and each
major market index.  The result is a time-series (one value per bar) that
answers: **"how tightly is this stock co-moving with index X right now?"**

Combined with zigzag peak detection on the index, it provides a daily risk
signal: if an index just hit a confirmed peak and the stock's rolling
correlation with that index is high, the stock is at elevated risk of
following the index down.

---

## Design evaluation (rationale)

> This section preserves the architectural evaluation done before
> implementation.

**Simple Moving Correlation**

The idea is sound.  A rolling-N correlation between a stock and each major
index gives a time-series that, when combined with zigzag peaks on the index,
answers: *"right now, is this stock tightly coupled to an index that just
peaked?"*  That is a meaningful risk signal and complements the static
`peak_corr` A / B metrics well.


**DB caching for backtests**

Pre-computing and caching to DB is the right call for GA optimisation.
Rolling correlation over N stocks × M indicators × D bars is cheap once, but
500 GA generations × that cost becomes the bottleneck.  The
*compute-once / read-many* pattern avoids that.

Scale check: 3-year period, 8 indicators, 60 stocks ≈ 60 × 8 × 750 ≈ 360 K
rows — completely manageable in PostgreSQL.

**Backtest cycle**

The three-phase cycle (download → heavy indicators → simulate) is a standard
ETL pipeline pattern and is the right architecture.  One gap to watch:
**cache invalidation**.  If new OHLCV data arrives for a stock, the stored
correlations become stale.  The current implementation handles this with an
incremental *covered-end* check: it computes only from the last stored date
forward, so running the cycle again after a new data download automatically
fills the gap.

**Summary verdict**

The key architectural decision was to keep indicator data loading *outside*
the pure function — the function receives pre-loaded price series from the
cycle layer, not from the DB.  This lets it be called efficiently inside a GA
loop without opening new DB sessions.  DB persistence stays in the cycle
layer.

---

## Key files

| File | Role |
|------|------|
| `src/indicators/moving_corr.py` | Pure computation — no DB dependency |
| `src/analysis/moving_corr.py` | DB persistence + standalone CLI |
| `src/analysis/models.py` | `MovingCorr` ORM model |
| `alembic/versions/0008_moving_corr.py` | Create `moving_corr` table |
| `alembic/versions/0009_moving_corr_add_gran.py` | Add `granularity`, rename `window_bars` |
| `src/backtest/cycle.py` | Phase-2 orchestration (download → indicators → simulate) |

---

## Pure indicator function

```python
from src.indicators.moving_corr import compute_moving_corr

corr_map = compute_moving_corr(
    stock_series,      # pd.Series — close prices, DatetimeIndex
    indicator_map,     # dict[str, pd.Series] — code → close prices
    window=20,         # rolling window in bars
)
# corr_map["^N225"] → pd.Series of daily correlation values in [-1, 1]
```

- Uses **return** (pct_change) correlation to avoid spurious level-correlation.
- Both series are aligned on their shared date index before rolling.
- `min_periods = max(5, window // 2)` — avoids all-NaN at the start of short series.
- No DB access, no side effects — safe to call inside GA hot loops.

---

## DB schema

**`moving_corr`**

| Column | Type | Notes |
|--------|------|-------|
| `id` | integer PK | |
| `stock_code` | varchar(30) | |
| `indicator` | varchar(30) | One of the major indices |
| `granularity` | varchar(10) | e.g. `1d`, `1h` |
| `window_bars` | integer | Rolling window length in bars |
| `ts` | timestamptz | Bar timestamp |
| `corr_value` | float | `null` if insufficient data |

**Unique constraint**: `(stock_code, indicator, granularity, window_bars, ts)`  
**Indexes**: lookup by `(stock_code, indicator, granularity, window_bars)`, by `ts`

---

## CLI usage

```bash
# Named stock set, default window (20 bars, 1d granularity):
uv run --env-file devenv python -m src.analysis.moving_corr \
    --stock-set medium --start 2022-01-01 --end 2025-12-31

# Custom window and granularity:
uv run --env-file devenv python -m src.analysis.moving_corr \
    --stock-set medium --start 2022-01-01 --end 2025-12-31 \
    --granularity 1h --window 40

# Force recompute (overwrite existing values):
uv run --env-file devenv python -m src.analysis.moving_corr \
    --stock-set medium --start 2022-01-01 --end 2025-12-31 --force
```

The CLI is **idempotent by default**: it checks the latest stored date per
`(stock, indicator, granularity, window_bars)` and only computes the
uncovered tail.  Rerunning after a data update automatically fills gaps.

---

## Backtest cycle

```bash
# Data + indicators only (no simulation):
uv run --env-file devenv python -m src.backtest.cycle \
    --stock-set medium --start 2022-01-01 --end 2025-12-31 --no-sim

# Full cycle — download, indicators, then GA optimisation:
uv run --env-file devenv python -m src.backtest.cycle \
    --stock-set medium --start 2022-01-01 --end 2025-12-31 \
    --strategy sma_breakout --trainer ga --ga-pop 60 --ga-gen 40
```

The cycle runs three phases in order:

| Phase | Module | Skip condition |
|-------|--------|----------------|
| 1 — Download OHLCV | `src.data.collect` | Date range already in DB |
| 2 — Compute moving_corr | `src.analysis.moving_corr` | Covered end ≥ requested end |
| 3 — Run trainer | `src.backtest.train_models` | Skipped with `--no-sim` |

---

## Using cached values inside a strategy

Load from DB during strategy setup, then inject into `DataCache` as a custom
indicator so the GA loop can access it bar-by-bar with zero DB overhead:

```python
from src.analysis.moving_corr import load_moving_corr

with get_session() as session:
    corr_series = load_moving_corr(
        session,
        stock_code="7203.T",
        indicator="^N225",
        gran="1d",
        window_bars=20,
        start=start,
        end=end,
    )

# Inject into DataCache as a named indicator
cache.add_indicator("CORR_N225_20", lambda closes: corr_series.reindex(...).values)
```

---

## Interpreting the values

| Value range | Meaning |
|-------------|---------|
| > +0.7 | Strong positive co-movement — stock closely follows the index |
| +0.3 to +0.7 | Moderate positive correlation |
| −0.3 to +0.3 | Weak or no consistent relationship |
| < −0.3 | Stock tends to move against the index |

**Risk signal**: high positive correlation (`> 0.6`) with an index that has
just hit a confirmed zigzag peak (`peak_corr` B metric also high) suggests
the stock is likely to decline in the near term.

**Low or negative correlation** with all major indices suggests the stock
moves on its own fundamentals — less exposed to macro index risk at peaks,
but also less predictable from index signals.
