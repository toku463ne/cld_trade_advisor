# Peak Correlation Analysis

Measures how strongly each stock co-moves with major market indices around
zigzag peak events.  Two metrics are computed per stock × indicator pair:

| Metric | Window | Interpretation |
|--------|--------|----------------|
| **A** | 20-bar return correlation ending **at** the peak day | Did the stock move with the index leading into the peak? |
| **B** | 5-bar return correlation starting 3 bars **after** the peak | Does the stock follow the index after the peak? |

Averages are taken over all *confirmed* peaks (zigzag direction = ±2) found in
the indicator's OHLCV series.

---

## Key files

| File | Role |
|------|------|
| `src/indicators/zigzag.py` | Peak/trough detection algorithm (shared with backtest strategies) |
| `src/analysis/peak_corr.py` | Core computation, DB persistence, CLI entry point |
| `src/analysis/models.py` | `PeakCorrRun` / `PeakCorrResult` ORM models |
| `alembic/versions/0007_peak_corr_tables.py` | DB migration for the two tables |
| `src/analysis/corr_ui.py` | Dash UI — `/peak-corr` route |

---

## Zigzag parameters

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `zz_size` | 5 | Bars required on **each side** of a candidate bar for a confirmed peak |
| `zz_middle_size` | 2 | Bars on the right side needed for an early peak (direction ±1) |

Only confirmed peaks (`direction = ±2`) are used in the correlation
calculation.  Early peaks are detected but excluded.

---

## Major indicators

```
^N225   Nikkei 225
^DJI    Dow Jones Industrial Average
^GSPC   S&P 500
^IXIC   NASDAQ Composite
^HSI    Hang Seng Index
^GDAXI  DAX (Germany)
^FTSE   FTSE 100 (UK)
^VIX    CBOE Volatility Index
```

These are automatically excluded from the "stocks" list even if they appear
in `configs/stock_codes.ini`.

---

## Running a computation

```bash
# Named stock set
uv run --env-file devenv python -m src.analysis.peak_corr \
    --stock-set medium --start 2022-01-01 --end 2025-12-31

# Explicit codes
uv run --env-file devenv python -m src.analysis.peak_corr \
    --code 7203.T 6758.T 9984.T --start 2022-01-01 --end 2025-12-31

# Custom zigzag parameters
uv run --env-file devenv python -m src.analysis.peak_corr \
    --stock-set medium --start 2022-01-01 --end 2025-12-31 \
    --zz-size 7 --zz-middle-size 3
```

Results are saved to `peak_corr_runs` / `peak_corr_results` and a run ID is
printed.

---

## DB schema

**`peak_corr_runs`**

| Column | Type | Notes |
|--------|------|-------|
| `id` | integer PK | |
| `created_at` | timestamptz | |
| `start_dt` / `end_dt` | timestamptz | Analysis period |
| `granularity` | varchar(10) | e.g. `1d` |
| `zz_size` / `zz_middle_size` | integer | Zigzag parameters |
| `stock_set` | varchar(100) | `null` when `--code` used |
| `n_indicators` | integer | |
| `n_stocks` | integer | |

**`peak_corr_results`**

| Column | Type | Notes |
|--------|------|-------|
| `id` | integer PK | |
| `run_id` | integer FK → `peak_corr_runs.id` | CASCADE delete |
| `stock` | varchar(30) | |
| `indicator` | varchar(30) | One of the major indices |
| `mean_corr_a` | float | `null` if no valid peaks |
| `mean_corr_b` | float | `null` if no valid peaks |
| `n_peaks` | integer | Number of A-window samples used |

---

## Viewing results

Launch the Dash UI and navigate to the **Peak Correlation** tab:

```bash
uv run --env-file devenv python -m src.analysis.corr_ui
# open http://localhost:8051/peak-corr
```

- Filter by indicator using the dropdown to isolate one index.
- The footer shows **corr(A, B)** — the Pearson correlation between the A
  values and B values across all displayed rows.  A positive value means
  stocks that tracked the index into the peak also tended to track it after.
- Click **📊** on any row to open the pair price chart.

---

## Interpreting the metrics

- **High A, high B** — stock closely follows this index both before and after
  peaks; useful as a leading/confirmation signal.
- **High A, low B** — stock tracks the index into peaks but diverges
  afterward; may reverse independently.
- **Low A, high B** — stock lags the index; peaks in the index may predict
  subsequent stock movement.
- **Both near zero** — stock is largely uncorrelated with this index around
  peaks; consider other indicators.
