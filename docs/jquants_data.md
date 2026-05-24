# J-Quants Data — Download, Backup & Restore

How to populate and preserve the `jq_*` (J-Quants) and `ohlcv_*`/`stocks` (yfinance)
market data. The download is expensive (10 yr of per-date API calls); the
backup/restore path lets a fresh environment be seeded without re-downloading.

All external J-Quants calls live in `src/data/jquants_collector.py` (per CLAUDE.md,
API code only under `src/data/`). It writes the parallel `jq_*` tables defined in
`src/data/jquants_models.py`.

---

## 1. Prerequisites

- **API key** — issued from the J-Quants dashboard. Accounts registered on/after
  2025-12-22 are **v2-only**: auth is the dashboard API key sent as the `x-api-key`
  header (no refresh/idToken flow). Inject it into the shell — **never** write it
  into the git-tracked `devenv`:
  ```bash
  export JQUANTS_API_KEY='your-key-here'
  ```
  Optional: `export JQUANTS_BASE_URL=...` (defaults to `https://api.jquants.com/v2`),
  `export JQUANTS_MIN_INTERVAL_SEC=0.25` (throttle floor between requests).
- **Migration applied** — the `jq_*` tables come from migration `c3f1a8b6d2e9`.
  Apply it per environment before downloading:
  ```bash
  alembic upgrade head        # dev DB already has it; btenv/prod do not
  ```

## 2. Subscription windows (Free vs Standard)

| Plan     | Daily quotes / statements | TOPIX | Notes |
|----------|---------------------------|-------|-------|
| Free     | ~12-week window, lagged ~2 yr (`2024-03-01 ~ 2026-03-01`) | 403 (unavailable) | enough to smoke-test the pipeline, **too short** to form PEAD revision pairs + a 60-bar forward window |
| Standard | `2016-05-24 ~` (10 yr, open-ended end) | available | full PEAD backfill; TOPIX enables the β-strip |

> **Critical gotcha — J-Quants does NOT clamp.** A *ranged* request (`topix`,
> `trading_calendar`) whose `--from` precedes your subscription start is rejected
> wholesale with HTTP 400 (`"Your subscription covers the following dates: 2016-05-24 ~"`)
> — nothing loads. *Per-date* requests (`daily_quotes`, `statements`) skip
> out-of-window dates individually and continue. **Always set `--from` to your exact
> entitlement start.** The start is revealed in that 400 message itself, so if you
> don't know it, run one ranged probe with a too-early `--from` and read it off the error.

## 3. Download (the collector)

```bash
# endpoints: listed | daily_quotes | statements | topix | trading_calendar | all
uv run --env-file devenv python -m src.data.jquants_collector \
    --endpoint <ep> [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--weeks N]
```
- `--weeks N` (with `--from` omitted) fetches the last N weeks ending `--to`/today —
  handy for Free-plan smoke tests (`--endpoint all --weeks 12`).
- **Resume**: `jq_fetch_cursor` stores the last date fetched per endpoint. A re-run
  with no `--from` resumes at `last_date + 1`, so an interrupted backfill is
  re-runnable without re-fetching.
- **Rate limits**: the client throttles (`JQUANTS_MIN_INTERVAL_SEC`) and retries 429s
  with backoff (honoring `Retry-After`); 400/403 are logged and skipped, not fatal.

### Standard 10-yr backfill (recommended sequence)

```bash
export JQUANTS_API_KEY='...'
WIN=2016-05-24          # your exact entitlement start (see the 400 gotcha above)
TODAY=$(date +%F)

# 1. confirm the ranged endpoints load with the right --from
uv run --env-file devenv python -m src.data.jquants_collector --endpoint trading_calendar --from $WIN --to $TODAY
uv run --env-file devenv python -m src.data.jquants_collector --endpoint topix            --from $WIN --to $TODAY
#    topix rows > 0  ==>  β-strip is viable; still 403  ==>  use a proxy

# 2. full backfill, detached (per-date quotes+statements over 10 yr is long)
JQUANTS_MIN_INTERVAL_SEC=0.25 nohup uv run --env-file devenv python -m \
    src.data.jquants_collector --endpoint all --from $WIN --to $TODAY \
    > jq_backfill.log 2>&1 &
tail -f jq_backfill.log
```

### Verify

```bash
uv run --env-file devenv python -c "
from src.data.db import get_session
from src.data.jquants_models import (JqListed, JqDailyQuote, JqStatement, JqTopix, JqTradingCalendar)
from sqlalchemy import select, func
with get_session() as s:
    for m in (JqListed, JqDailyQuote, JqStatement, JqTopix, JqTradingCalendar):
        n = s.execute(select(func.count()).select_from(m)).scalar()
        print(f'{m.__tablename__:24} {n}')
"
```
Spot-check that `announcement_date` is stored (it equals `disclosed_date`):
```bash
uv run --env-file devenv python -c "
from src.data.db import get_session; from src.data.jquants_models import JqStatement
from sqlalchemy import select
with get_session() as s:
    r = s.execute(select(JqStatement.disclosure_number, JqStatement.disclosed_date,
                         JqStatement.announcement_date).limit(3)).all()
    print(*r, sep='\n')
"
```

---

## 4. Backup & Restore (`scripts/market_data_dump.sh`)

Seeds a new environment without re-downloading. Dumps **only** the
expensive-to-rebuild tables — `stocks`, `ohlcv_*` (incl. year partitions),
`jq_*` — to a single custom-format file under `~/db_backups/` (outside git).
This is narrower than `scripts/backup_dev_db.sh`, which dumps the *entire* dev DB.

> ⚠️ **The `.dump` files are data — never commit them.** They live in
> `~/db_backups/`, outside the repo. For real safety copy the single file off-box
> (`scp`/cloud); `jq_daily_quotes` over 10 yr is hundreds of MB to GB.

### Dump — only after a backfill has FINISHED
A mid-write dump captures partial `jq_daily_quotes`/`jq_statements`. Confirm the
collector has exited (`tail jq_backfill.log`) first.
```bash
scripts/market_data_dump.sh dump [envfile]      # default envfile: devenv
# -> ~/db_backups/market_data_YYYYMMDD_HHMM.dump   (keeps newest 5, prunes older)
```

### Restore into a new env
```bash
# in the new env: create the DB, set DATABASE_URL in its env file, then:
alembic upgrade head                              # build all app tables (recommended)
scripts/market_data_dump.sh restore ~/db_backups/market_data_XXXX.dump [envfile]
```
Restore uses `pg_restore --clean --if-exists`, so it drops & recreates **only**
`stocks`/`ohlcv_*`/`jq_*` (carrying the OHLCV partition DDL + the `jq_fetch_cursor`
resume state) and leaves every other table and `alembic_version` untouched. It
works whether or not the target tables already exist.

---

## 5. After data loads — PEAD analysis

The 10-yr Standard backfill (with TOPIX) unblocks the management-forecast-revision
PEAD study: `src/analysis/pead_forecast_revision.py` against the pre-registration in
`docs/analysis/pead_forecast_revision_preregistration.md`.
