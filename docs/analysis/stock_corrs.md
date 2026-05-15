# Stock Return Correlation Analysis

## Idea & Analysis

### Is this worth having?

**Yes — as a foundation, with important caveats.**

The module computes Pearson correlation of *daily returns* between every pair of stocks
over a sliding window and summarises each pair as mean ± std across all windows.

Immediately useful for:

| Use case | What to look for |
|----------|-----------------|
| **Pairs trading candidates** | High `\|mean\|`, low `std` → stable co-movement → mean-reversion opportunity when pair diverges |
| **Portfolio diversification** | Low `\|mean\|` → genuinely uncorrelated stocks → diversification benefit |
| **Regime analysis** | High `std` → correlation is unstable → relationship breaks down during stress |
| **Index exposure** | High correlation with `^N225` → stock mostly tracks the index |

### Why returns, not prices?

Price correlation is almost always spurious.  Two stocks that both trend upward over a
year will show ρ ≈ 0.9 even if their day-to-day moves are completely unrelated.
Return correlation (daily % change) removes the trend and captures actual co-movement.

### Does high correlation mean A can predict B?

**Not directly.**  Contemporaneous return correlation tells you they move *together*,
not that one *leads* the other.  To find leading indicators you need **lagged**
correlation: correlate today's return for stock A with tomorrow's return for stock B.

The current module lays the groundwork.  Lagged correlation (1–5 day lag) is the
planned next step.

### Observed results (medium set, 2022-2025, window=20, step=10)

Top stable pairs from the first run:

| Pair | Mean ρ | Std ρ | Interpretation |
|------|--------|-------|----------------|
| 8306.T × 8308.T | +0.777 | 0.133 | MUFG × Resona — both major banks |
| 8001.T × 8002.T | +0.723 | 0.181 | Itochu × Marubeni — both trading houses |
| ^N225 × 6479.T  | +0.713 | 0.160 | Minebea tracks Nikkei closely |
| 9007.T × 9005.T | +0.682 | 0.144 | Odakyu × Tokyu — both private railways |

These make intuitive sense — same sector, similar macro exposure.
The low std (0.13–0.18) confirms the relationship is stable across regimes.

No strongly negative correlations exist in this universe (min ≈ −0.02), which
is expected: the medium set is all Japanese equities with common market exposure.

---

## Usage

### 1. Compute correlations

```bash
# Medium stock set, default window=20 step=10
uv run --env-file devenv python -m src.analysis.stock_corrs \
    --stock-set medium \
    --start 2022-01-01 --end 2025-12-31

# Wider window for more stable estimates (recommended for production)
uv run --env-file devenv python -m src.analysis.stock_corrs \
    --stock-set medium \
    --start 2022-01-01 --end 2025-12-31 \
    --window 60 --step 20

# Explicit codes
uv run --env-file devenv python -m src.analysis.stock_corrs \
    --code 7203.T 8306.T ^N225 \
    --start 2022-01-01 --end 2025-12-31
```

Each run creates a new row in `corr_runs` and N*(N-1)/2 rows in `stock_corr_pairs`.
Multiple runs with different parameters can coexist in the DB.

### 2. View results in the UI

```bash
uv run --env-file devenv python -m src.analysis.corr_ui
# open http://localhost:8051
```

The UI shows:
- **Run selector** — choose from all computed runs
- **Stock filter** — type a ticker to see only pairs involving that stock
- **Pair table** — all pairs sorted by `|round(mean,2)|` desc, `std` asc; filterable and sortable; Name A / Name B columns show company names

---

## Algorithm

```
1. Load daily close prices from DB for all requested stocks.
2. Compute daily returns:  r_t = (close_t / close_{t-1}) - 1
3. Slide a window of `window_days` bars, advancing `step_days` bars each time.
4. In each window, compute the Pearson correlation matrix of returns.
   Windows with < 80% of expected bars are skipped.
5. For each pair (A, B) accumulate the per-window correlation values.
6. Compute:
     mean_corr = mean of window correlations
     std_corr  = std dev of window correlations  (ddof=1)
7. Sort: abs(round(mean_corr, 2)) DESC, std_corr ASC
8. Persist CorrRun + StockCorrPair rows to the DB.
```

### Complexity

| Parameter | Value |
|-----------|-------|
| N stocks  | 64 (medium set) |
| Pairs     | 64 × 63 / 2 = 2,016 |
| Windows (window=20, step=10, 978 bars) | 96 |
| Total correlations computed | ~194k |
| Run time  | < 5 s |

For all Nikkei 225 (~225 stocks): 25,200 pairs, same window count → still < 30 s.

---

## DB Schema

### `corr_runs`

| Column | Type | Description |
|--------|------|-------------|
| id | int PK | Auto-increment |
| start_dt | timestamptz | Analysis start date |
| end_dt | timestamptz | Analysis end date |
| granularity | varchar(10) | Bar granularity (e.g. `1d`) |
| window_days | int | Bars per window |
| step_days | int | Step between windows |
| n_stocks | int | Number of stocks with sufficient data |
| n_windows | int | Windows actually processed |
| created_at | timestamptz | When this run was created |

### `stock_corr_pairs`

| Column | Type | Description |
|--------|------|-------------|
| id | int PK | Auto-increment |
| corr_run_id | int FK | → corr_runs.id (CASCADE) |
| stock_a | varchar(30) | First stock code |
| stock_b | varchar(30) | Second stock code (always stock_a < stock_b alphabetically) |
| mean_corr | float | Mean Pearson ρ across windows |
| std_corr | float | Std dev of ρ across windows |
| n_windows | int | Windows where both stocks had data |

---

## Planned Enhancements

1. **Lagged correlation** — correlate A's return today vs B's return N days later
   (N = 1..5) to find leading/lagging relationships with predictive value.
2. **Nikkei 225 full universe** — requires collecting data for all ~225 constituents.
3. **Time-series of correlation** — plot how a specific pair's correlation evolves
   over time (currently only mean/std are stored).
4. **Sector grouping** — colour heatmap cells by sector to visualise intra vs.
   inter-sector correlations.
