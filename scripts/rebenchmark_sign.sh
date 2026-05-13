#!/usr/bin/env bash
# rebenchmark_sign.sh — Delete, re-run, and report benchmarks for one sign.
#
# Usage:
#   scripts/rebenchmark_sign.sh <sign_type>
#
# Steps performed:
#   1. Delete existing SignBenchmarkRun rows (and cascaded events) for the sign.
#   2. Truncate auto-generated sections of benchmark.md (from "## Multi-Year Benchmark").
#   3. Run benchmark → validate → report phases for the sign.
#   4. Run sign_regime_analysis (build + analyze + report) for the sign.
#   5. Run sign_score_calibration (Spearman ρ + score-quartile EV table).
#   6. Run FY2025 out-of-sample backtest phase.
#
# All commands use the devenv environment file.
# Run from the project root directory.

set -euo pipefail

SIGN="${1:-}"
if [[ -z "$SIGN" ]]; then
  echo "Usage: $0 <sign_type>" >&2
  exit 1
fi

BENCH_MD="src/analysis/benchmark.md"
TRUNCATE_MARKER="## Multi-Year Benchmark"

echo "=== Rebenchmark: $SIGN ==="

# ── Step 1: Delete existing DB runs ──────────────────────────────────────────
echo "[1/5] Deleting existing DB runs for $SIGN ..."
uv run --env-file devenv python - <<EOF
from src.data.db import get_session
from src.analysis.models import SignBenchmarkRun, SignBenchmarkEvent
from sqlalchemy import select, delete

sign = "$SIGN"
with get_session() as s:
    runs = s.execute(
        select(SignBenchmarkRun).where(SignBenchmarkRun.sign_type == sign)
    ).scalars().all()
    ids = [r.id for r in runs]
    if ids:
        s.execute(delete(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(ids)))
        for r in runs:
            s.delete(r)
        s.commit()
        print(f"  Deleted {len(ids)} runs (ids {ids}) and their events for '{sign}'.")
    else:
        print(f"  No existing runs found for '{sign}'.")
EOF

# ── Step 2: Truncate auto-generated benchmark.md sections ────────────────────
echo "[2/5] Truncating $BENCH_MD at '${TRUNCATE_MARKER}' ..."
python3 - "$BENCH_MD" "$TRUNCATE_MARKER" <<'PYEOF'
import sys
from pathlib import Path

md = Path(sys.argv[1])
marker = sys.argv[2]
lines = md.read_text().splitlines(keepends=True)
for i, line in enumerate(lines):
    if line.startswith(marker):
        # Include the --- separator before the marker if present
        start = i
        if i >= 2 and lines[i-1].strip() == '' and lines[i-2].strip() == '---':
            start = i - 2
        md.write_text("".join(lines[:start]).rstrip() + "\n")
        print(f"  Truncated at line {start} (was {len(lines)} lines).")
        sys.exit(0)
print(f"  WARNING: marker '{marker}' not found — benchmark.md not truncated.", file=sys.stderr)
PYEOF

# ── Step 3: Run benchmark → validate → report ─────────────────────────────────
echo "[3/6] Running benchmark → validate → report for $SIGN ..."
uv run --env-file devenv python -m src.analysis.sign_benchmark_multiyear \
  --phase benchmark validate report \
  --sign "$SIGN"

# ── Step 4: Regime split analysis ─────────────────────────────────────────────
echo "[4/6] Running sign_regime_analysis (build + analyze + report) ..."
uv run --env-file devenv python -m src.analysis.sign_regime_analysis

# ── Step 5: Score calibration (sign_score → outcome correlation) ──────────────
echo "[5/6] Running sign_score_calibration (Spearman ρ + quartile EV + by-regime) ..."
uv run --env-file devenv python -m src.analysis.sign_score_calibration --by-regime

# ── Step 6: FY2025 out-of-sample backtest ─────────────────────────────────────
echo "[6/6] Running FY2025 out-of-sample backtest for $SIGN ..."
uv run --env-file devenv python -m src.analysis.sign_benchmark_multiyear \
  --phase backtest \
  --sign "$SIGN"

echo ""
echo "=== Done: $SIGN benchmark updated. ==="
echo "    Review: $BENCH_MD"
echo "    Update the sign's header comment with new DR / perm_pass numbers."
