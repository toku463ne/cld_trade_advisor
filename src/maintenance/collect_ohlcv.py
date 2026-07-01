"""Headless OHLCV collection for the N225 universe + indices.

Mirrors the Maintenance tab's OHLCV-download worker
(``src.viz.maintenance._run_ohlcv_download``) without the Dash/UI glue, so it
can be driven from cron or a systemd timer. Incrementally collects a 6-year
window for every N225 constituent plus ^N225/^GSPC, rebuilds the N225 regime
snapshots, and writes the ``.ohlcv_last_download`` marker the UI reads.

The DB is chosen by the ``--env-file`` passed to ``uv run`` — point it at
``prodenv`` to refresh the live UI's database. Run:

    uv run --env-file prodenv python -m src.maintenance.collect_ohlcv

Exit code is 0 when the run completes (0 new rows is a normal no-op — e.g. the
vendor has not posted today's bar yet), and non-zero only when *every* symbol
fetch errored (a systemic failure worth alerting on).
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

from loguru import logger

_N225 = "^N225"
_GSPC = "^GSPC"
_GRAN = "1d"
_OHLCV_MARKER = Path(__file__).resolve().parent.parent.parent / "data" / ".ohlcv_last_download"


def collect_universe() -> int:
    """Collect the N225 universe + indices and rebuild regime snapshots.

    Returns:
        Process exit code: 0 on completion (including a 0-new-rows no-op),
        1 if every symbol fetch raised (systemic failure).
    """
    from src.analysis.sign_regime_analysis import phase_build
    from src.data.collect import OHLCVCollector
    from src.data.db import get_session
    from src.data.nikkei225 import load_or_fetch

    today = datetime.date.today()
    start_dt = datetime.datetime(today.year - 6, today.month, today.day,
                                 tzinfo=datetime.timezone.utc)
    end_dt = datetime.datetime(today.year, today.month, today.day,
                               tzinfo=datetime.timezone.utc)
    logger.info("[OHLCV] Window: {} -> {}", start_dt.date(), end_dt.date())

    all_codes = [_N225, _GSPC] + sorted(set(load_or_fetch()))
    logger.info("[OHLCV] {} codes (N225 constituents + indices)", len(all_codes))

    total_new = 0
    errors = 0
    for code in all_codes:
        try:
            with get_session() as session:
                n = OHLCVCollector(session).collect(code, _GRAN, start_dt, end_dt)
            total_new += n
            if n:
                logger.info("[OHLCV] {}: +{} rows", code, n)
        except Exception as exc:
            errors += 1
            logger.error("[OHLCV] {}: ERROR - {}", code, exc)

    if errors == len(all_codes):
        logger.error("[OHLCV] All {} fetches failed - aborting before regime "
                     "rebuild / marker write", errors)
        return 1

    logger.info("[OHLCV] Rebuilding N225 regime snapshots ...")
    try:
        phase_build()
        logger.info("[OHLCV] Regime snapshots updated.")
    except Exception as exc:
        logger.error("[OHLCV] Regime build error: {}", exc)

    try:
        _OHLCV_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _OHLCV_MARKER.write_text(
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.error("[OHLCV] marker write failed: {}", exc)

    logger.info("[OHLCV] Done. new_rows={} errors={}", total_new, errors)
    return 0


if __name__ == "__main__":
    sys.exit(collect_universe())
