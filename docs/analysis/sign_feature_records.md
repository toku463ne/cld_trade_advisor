# `sign_feature_records` — per-fire feature/label table

A durable, queryable snapshot of the **context and forward outcome at every sign
fire** (daily, FY2010–FY2025). One row = one sign firing on one stock on one day.

**Pure-data table.** It holds only *measured observations* — no interpretive
labels (e.g. bullish/bearish). The intent is to support evidence-grounded
statements *derived from it*, such as "according to `sign_feature_records`
FY2010–FY2016 data, `str_lag` is bearish in situation X." Any directionality is a
conclusion drawn from the stored outcomes, never an input baked into the table.

Produced by `src/analysis/sign_features.py` (`--to-db`); schema in Alembic
migrations `d3f9a1c7e2b8` (create) and `e5a2c9b14f37` (drop a-priori directional
counts → pure data). It exists so the discover/validate/holdout characterization
(`sign_characteristics.py`, sibling report `sign_characteristics.md`) runs on
stored features rather than an ephemeral pickle.

## Relationship to other tables

- **Source of fires:** `sign_benchmark_events` (joined to `sign_benchmark_runs`
  for `sign_type`). The fire dates, own scores, and the `out_*` outcomes are
  copied from there; `sign_feature_records` *enriches* each fire with the
  cross-sectional context that `sign_benchmark_events` does not store.
- **Join key:** `(stock_code, fired_on, sign_type)` ↔ `sign_benchmark_events`
  `(stock_code, fired_at::date, run.sign_type)`.
- Sign scores are durable in **both** tables; the indicator/correlation/co-fire
  context is durable **only here**.

## `sign_feature_runs` (parent)

One row per collector execution. Records are FK'd to it (`run_id`,
`ON DELETE CASCADE`).

| column | type | meaning |
|---|---|---|
| `id` | int PK | run id |
| `label` | str | human label, e.g. `fy2010_2025_h20` |
| `fwd_h` | int | fixed-horizon bars used for `fwd_ret_h` (20) |
| `valid_bars` | int | co-fire validity window (5) |
| `corr_window` | int | rolling correlation window in bars (20) |
| `n_records` | int | number of records written |
| `created_at` | datetimetz | run timestamp |

## `sign_feature_records` — columns

### Identity / own signal

| column | type | meaning |
|---|---|---|
| `id` | int PK | record id |
| `run_id` | int FK | → `sign_feature_runs.id` |
| `stock_code` | str | e.g. `1332.T` |
| `fired_on` | date | the fire date |
| `fy` | str | Japanese fiscal year, e.g. `FY2021` (Apr–Mar) |
| `sign_type` | str | the firing sign, e.g. `brk_bol` |
| `sign_score` | float | that sign's own score at the fire |

### Indicator distances (own stock, at the fire bar)

All look-ahead-safe (computed from data up to and including the fire bar). Each
is a signed fractional distance; the math mirrors `_trend_score`.

| column | meaning |
|---|---|
| `sma_dist` | `(close − SMA50) / SMA50` |
| `kumo_dist` | `(close − kumo_midline) / kumo_midline`, midline = (Senkou A + Senkou B)/2, displaced 26 |
| `chiko_dist` | `(close − close[−26]) / close[−26]` (Chikou-span distance) |
| `tenkan_dist` | `(close − Tenkan) / Tenkan`, Tenkan = 9-bar (high+low)/2 |
| `zz_momentum` | signed % change of the last **confirmed** zigzag leg: `(last_peak − prev_peak)/prev_peak`. Positive = last leg up |

### Correlations (rolling daily-return Pearson, `corr_window`=20 bars)

| column | meaning |
|---|---|
| `corr_n225` | corr of daily returns vs `^N225` |
| `corr_gspc` | corr vs `^GSPC` (S&P 500) |
| `corr_hsi` | corr vs `^HSI` (Hang Seng) |

> Per the trading philosophy, the analyzer buckets these by **|corr|**: ≥ 0.6 =
> "high" (index proxy), ≤ 0.3 = "low" (idiosyncratic alpha).

### Co-fire context (other signs valid on this stock at the fire bar)

A sign that fires on bar *f* is "valid" on bars *f…f+`valid_bars`−1*. These
capture which other signals were live alongside this fire (the confluence
context).

| column | meaning |
|---|---|
| `valid_n` | count of signs valid on this bar (**includes the firing sign itself**), **direction-agnostic** |
| `cofire_scores` | JSONB map `{sign: score}` for every valid sign — the full raw detail |

> **Pure-data principle:** the table stores **no bullish/bearish grouping.**
> Whether a co-firing sign is bullish or bearish (and in which situation) is a
> *conclusion to be derived from this table's measured outcomes*, not an input
> baked in — and the discover data shows ~8 of the a-priori labels disagree with
> measured forward returns. Directional co-fire counts are computed in the
> analysis layer (`sign_characteristics.py`) from `cofire_scores`, where the
> grouping is an explicit, documented interpretive choice.

> **Self-inflation caveat:** `cofire_scores` / `valid_n` include the firing sign,
> so the count is never zero. Constant offset per sign → within-sign bucket
> contrasts are unaffected, but raw `valid_n` *levels* are not comparable across
> signs.

### ^N225 context (the index's own signal that day)

Only **self-contained** signs (those needing the price series alone) are
meaningful on the index; relative signs (`str_*`, `div_*`, `corr_*`,
`rev_nlo/nhold`) are excluded.

| column | meaning |
|---|---|
| `n225_valid_n` | count of self-contained signs valid on `^N225` that day, **direction-agnostic** |
| `n225_scores` | JSONB map `{sign: score}` of self-contained signs valid on `^N225` — the full raw detail |

(As with co-fire context, no bullish/bearish split is stored; derive it in the
analysis layer from `n225_scores`.)

### Outcomes (LABELS — forward-looking, never use as features)

Two complementary measures of "what happened after the fire." Entry is the open
of the bar **after** the fire (two-bar fill).

| column | meaning |
|---|---|
| `out_direction` | first confirmed zigzag swing after entry: `+1` = swing HIGH first (up), `−1` = swing LOW first (down) |
| `out_bars` | bars from entry to that confirmed peak (timing) |
| `out_magnitude` | `|peak_price − entry| / entry` — size of the move, **unsigned** (combine with `out_direction` for the signed picture) |
| `fwd_ret_h` | fixed-horizon return `(close[entry+H] − open[entry]) / open[entry]`, H = `fwd_h` (20) |

- `out_*` is **event-based** (next confirmed swing, variable horizon,
  magnitude-aware) and "confirmed" means zigzag-lagged (`size=5, middle_size=2`),
  capped ~30 bars. **~7% of rows have NULL `out_*`** — no confirmed peak formed
  in the window (near end of data, or sideways drift).
- `fwd_ret_h` is a **fixed 20-bar signed return** (tradeable, comparable across
  signs); ~100% populated.
- They can disagree (a HIGH-first fire can still have a flat/negative 20-bar
  return if it peaked early then fell back) — which is why both are kept.
- `out_direction` drives the benchmark's **direction-rate**, DR =
  P(`out_direction == +1`), the secondary "bullishness" metric.

## Conventions / gotchas

- **Two-bar fill**: signal on bar T, entry at the open of T+1 (matches the
  simulator). All outcome math anchors on the T+1 open.
- **Look-ahead legality**: identity, indicator, correlation, and co-fire/N225
  columns are fire-time legal. The `out_*` and `fwd_ret_h` columns are
  forward-looking labels — never feed them back as inputs.
- **Universe-beta warning** (see `sign_characteristics.md`): the cross-sign
  `corr_hsi`-low → bullish pattern is mostly a regime-unstable universe tilt,
  not a sign edge. Treat `corr_*` characterizations with the universe baseline
  in mind.

## Querying

ORM:
```python
from src.analysis.models import SignFeatureRecord, SignFeatureRun
from sqlalchemy import select
# latest run
run = session.execute(select(SignFeatureRun).order_by(SignFeatureRun.id.desc())).scalars().first()
# str_hold fires in low-N225-corr, price below kumo
q = (select(SignFeatureRecord)
     .where(SignFeatureRecord.run_id == run.id,
            SignFeatureRecord.sign_type == "str_hold",
            SignFeatureRecord.kumo_dist < 0))
```

JSONB (sparse per-sign maps):
```sql
-- fires where ^N225 had brk_sma valid that day
SELECT count(*) FROM sign_feature_records
WHERE run_id = 3 AND n225_scores ? 'brk_sma';

-- fires where rev_nhi was a co-firing sign, with its score
SELECT stock_code, fired_on, (cofire_scores->>'rev_nhi')::float AS rev_nhi_score
FROM sign_feature_records
WHERE run_id = 3 AND cofire_scores ? 'rev_nhi';
```

## Provenance / regeneration

```bash
PYTHONPATH=. uv run --env-file devenv python -m src.analysis.sign_features \
    --to-db --out /tmp/sign_features.pkl --label fy2010_2025_h20_puredata
```

Deterministic given `ohlcv_1d` (backfilled to 2008) + `sign_benchmark_events`
(FY2010–FY2025). Each invocation writes a new `sign_feature_runs` row; downstream
analysis reads the latest. Current populated run: `run_id=3`,
`label=fy2010_2025_h20_puredata`, 352,513 records.
