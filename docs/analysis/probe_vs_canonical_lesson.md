# Probe vs Canonical — methodology lesson (2026-05-17)

**Rule**: One-shot probes that use globally-detected zigzag (`detect_peaks`
on the full bar series) overstate or even **invert** canonical-pipeline
results (per-fire windowed detection used by `sign_benchmark.py`).
Use probes to green-light builds; trust the canonical pipeline for
direction decisions.

## The mechanism

| Approach | Where peaks are detected | What this sees |
|---|---|---|
| Probe (global) | `detect_peaks(highs, lows)` on the FULL bar series, then look up "next peak with bar_index > entry_idx" | Peaks settled by post-fact data — confirmed swing points only |
| Canonical (windowed) | `_first_zigzag_peak(...)` slices `bars[entry_idx : entry_idx + 35]` and runs `detect_peaks` on that 35-bar window | What live trading sees at decision time — may reject as noise something later confirmed as a real peak, or call near-term reversals where global eventually settles into a continuation |

The two views disagree systematically because zigzag confirmation
requires data after the candidate peak.  Global detection sees the
full continuation/rejection sequence; windowed detection only sees
35 bars and ends up calling more peaks (since smaller wiggles aren't
yet "rejected" by subsequent data).

## Two demonstrated cases (2026-05-17)

### Case 1 — brk_wall (magnitude inflation, direction preserved)

Same data, same sample, two measurements:

| | Pooled DR | Pooled EV | Per-FY positive |
|---|---|---|---|
| Probe | 72.6% | +2.88% | 7 of 7 FYs |
| Canonical | 53.0% | +0.51% | 4 of 7 FYs (perm_pass 2/7) |

The probe overstated DR by ~20pp.  Direction of the finding survived
(brk_wall is still mildly +EV for long entry), but the magnitude
collapsed.

**Consequence**: We almost shipped brk_wall thinking it was the
strongest sign in the catalogue (72.6% DR would dwarf str_hold's
54.5%).  Canonical revealed it's actually same tier as brk_sma /
brk_bol — and subsequent strategy A/B showed **zero impact** on the
live portfolio.

### Case 2 — brk_floor (DIRECTION FLIPPED)

| | Pooled DR | Implication for SHORT entry |
|---|---|---|
| Probe | 33.2% (breakdowns persist) | Short DR = 67% → strong short signal |
| Canonical | 51.7% (breakdowns mean-revert) | Short DR = 48% → marginally negative |

The operator's intent was SHORT entry: "price broke through the floor,
expect continued downside."  The probe supported it (67% short-win
rate).  The canonical contradicted (48% short-win rate, short EV
≈ −0.44%).

**Consequence**: brk_floor was shipped on the operator's intuition but
immediately demoted to `_HIDDEN_PROPOSAL_SIGNS` — kept in catalogue
for event preservation (score calibration ρ=+0.17 in high-corr is the
strongest in the catalogue) but does NOT generate proposal rows.

If we'd shipped on the probe's reading without canonical verification,
operator might have started taking SHORT positions on this sign and
lost money.

## The rule

### Probes ARE useful for

- **Green-lighting a detector build**: "Is this even worth implementing?
  Does the event have reasonable fire-rate and any directional bias?"
- **Surfacing candidate features**: "Of these 4 features, which one
  has the largest pooled signal?  Investigate that one further."
- **Sample-size feasibility**: "Will the resulting sign have enough
  per-FY events to be measurable?"
- **Cross-tab exploration**: "Which (regime, sector) cells diverge?"

### Probes are NOT sufficient to

- **Decide trade direction** (especially LONG vs SHORT)
- **Authorize a sign for production deployment**
- **Claim a strategy edge worth taking trades on**
- **Replace `scripts/rebenchmark_sign.sh`**

### Before any sign ships

1. Build detector
2. Wire into `sign_benchmark.py` dispatch + `sign_benchmark_multiyear.py` SIGNS list
3. Run `scripts/rebenchmark_sign.sh <sign>` — this writes the canonical
   per-FY benchmark + regime split + score calibration to
   `src/analysis/benchmark.md`
4. **Decide ship based on those numbers, not the probe's**
5. If probe and canonical disagree, document the gap in the doc page
   and update memory ([[feedback-probe-vs-canonical]])

### Before any strategy claim

The same rule applies one layer up: per-sign canonical numbers do NOT
predict strategy-level impact.  See [brk_wall A/B
result](brk_wall_brk_floor.md) — canonical-passing brk_wall has
literally zero impact on live `regime_sign` portfolio.  For
**strategy** claims, run the live ZsTpSl backtest
(`regime_sign_backtest.py`, `regime_sign_brk_wall_ab.py`,
`confluence_strategy_backtest.py` pattern) — and that's the source of
truth, not the per-fire benchmark.

## The confluence-strategy contrast

The [Confluence Strategy](confluence_strategy.md) cycle followed this
discipline:

1. **v1 probe (same-day)** — REJECT, easy to see (n=13)
2. **v2 probe (validity-windowed, trend_direction proxy)** — PASS
   at per-fire level, but used the same proxy that overstates
3. **v3 live ZsTpSl backtest** — PASS at strategy level (Sharpe +3.80
   vs baseline +1.33), 6/7 FYs positive

The v3 step is what made the confluence finding credible.  The two
prior probe-only positive findings this session (brk_wall standalone,
brk_floor short interpretation) had both failed live verification.
The v3 walk is the standard the next probe-positive finding will
need to clear too.

## Related memory + docs

- Memory: `feedback_probe_vs_canonical.md`
- Sign case studies: [brk_wall + brk_floor](brk_wall_brk_floor.md)
- Strategy validation pattern: [Confluence Strategy](confluence_strategy.md)
- Rubric: `docs/evaluation_criteria.md` (the materiality + sample-size
  thresholds that apply once canonical numbers are in)
