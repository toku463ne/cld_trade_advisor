# Confluence Strategy — multi-sign agreement beats single-sign ranking (2026-05-17)

**Verdict: SHIP** — `ConfluenceSignStrategy` deployed in Daily-tab
shadow mode alongside `RegimeSignStrategy`.

Operator hypothesis: "Individual signs decay quickly, but state-change
signs and multi-sign agreement could last longer.  N bullish signs
agreeing on direction should be a stronger bet than any single sign."

The data confirmed it — once measured properly.  Live ZsTpSl backtest
at N≥3 confluence: **Sharpe +3.80 vs `RegimeSign` baseline +1.33
(~3×), 6 of 7 FYs positive**, FY2025 OOS at +8.37 Sharpe.  First
positive backtest result in the May 2026 sign-review run.

## The framework

For each stock × trading day, count the number of bullish signs
currently "valid" using each sign's own `valid_bars` (matches
detector defaults):

| Sign | valid_bars |
|---|---:|
| str_hold | 3 |
| brk_bol | 3 |
| str_lead, str_lag, brk_sma, rev_lo, rev_nlo | 5 |

A sign that fired on Monday counts toward confluence on Mon–Wed
(str_hold/brk_bol) or Mon–Fri (others).  Fire if count ≥ N.

**brk_wall is EXCLUDED** from the bullish set — its inclusion diluted
the v2 probe uplift by −0.83pp (see [brk_wall / brk_floor](brk_wall_brk_floor.md)).

## Three iterations to the right framing

### v1 — same-day confluence (REJECT)

`src/analysis/bullish_confluence_probe.py` measured confluence on the
calendar day signs co-fire.  Only **13 stock-dates in 7 FYs had ≥3
bullish signs firing the same day**.  Signs detect at different
timescales — str_hold (5-day window), brk_sma (20-bar), rev_lo
(zigzag-confirmed) — and almost never trigger on the same calendar
day.  Confluence-as-defined was structurally rare.

### v2 — validity-windowed confluence (operator's key insight)

Operator: *"each sign has 'valid days' — why don't we use that to
widen the time?"*

`src/analysis/bullish_confluence_v2_probe.py` credits each fire for
its full validity window.  Result per-fire (using benchmark.md
`trend_direction` convention):

| Bucket | n_train | DR (train) | EV (train) | EV (FY2025) | mean signs/day |
|---|---:|---:|---:|---:|---:|
| 1 sign | 84,449 | 55.1% | +0.89% | +2.59% | 1.00 |
| 2 signs | 16,260 | 60.1% | +1.79% | +4.07% | 2.00 |
| **≥3 signs** | **1,746** | **64.3%** | **+3.20%** | **+5.02%** | 3.08 |

- All 7 FYs positive uplift at ≥3 confluence
- Pooled training EV[≥3] − EV[1] = **+2.31pp**
- FY2025 OOS uplift = **+2.44pp**

PASS at per-fire level.  But this uses the trend_direction proxy,
which the same session demonstrated can overstate live performance
(see [Probe vs Canonical](probe_vs_canonical_lesson.md)).  The real
test is live ZsTpSl backtest.

### v3 — live ZsTpSl strategy backtest (BIG WIN)

`src/analysis/confluence_strategy_backtest.py` runs the confluence
gate as an actual entry rule with all of: ZsTpSl(2.0, 2.0, 0.3) exit,
two-bar fill, portfolio cap (≤1 high-corr + ≤3 low/mid), 10-bar
cooldown between re-entries on same stock.

| Strategy | trades | avg Sharpe | avg mean_r | win% |
|---|---:|---:|---:|---:|
| `regime_sign` baseline (currently shipped) | 171 | **+1.33** | +0.77% | varies |
| N ≥ 1 (any bullish sign valid) | 249 | +1.36 | +0.77% | 52% |
| N ≥ 2 (≥2 signs agree) | 222 | **+3.53** | +1.92% | 58% |
| **N ≥ 3 (≥3 signs agree)** | **165** | **+3.80** | **+1.97%** | **59%** |

### Per-FY at N ≥ 3 (the recommended gate)

| FY | trades | Sharpe | mean_r | win% |
|---|---:|---:|---:|---:|
| FY2019 | 12 | **−5.26** | −2.75% | 42% |
| FY2020 | 24 | **+7.95** | +4.47% | 67% |
| FY2021 | 29 | +1.55 | +1.06% | 48% |
| FY2022 | 22 | +3.35 | +1.66% | 64% |
| FY2023 | 23 | **+9.10** | +3.80% | 70% |
| FY2024 | 30 | +1.55 | +0.94% | 57% |
| **FY2025 OOS** | **25** | **+8.37** | **+4.61%** | **68%** |

- 6 of 7 FYs positive Sharpe
- FY2025 OOS is the cleanest win — +8.37 Sharpe on 25 trades with 68% win rate
- Three FYs above +7 Sharpe: FY2020, FY2023, FY2025
- Lone loss: FY2019 at −5.26 on n=12 (small-sample concern, deferred)

### Why N=3 over N=2

| Gate | Pooled Sharpe | Per-FY consistency |
|---|---|---|
| N≥2 | +3.53 | 4 of 7 FYs positive |
| N≥3 | +3.80 | **6 of 7 FYs positive** |

N≥3 has fewer trades but materially better per-FY consistency.  The
extra robustness matters more than the trade-count gain.

## What shipped

### `src/strategy/confluence_sign.py`

`ConfluenceSignStrategy` class mirroring the `RegimeSignStrategy`
public interface (`propose`, `propose_range`, `from_config`).

```python
ConfluenceSignStrategy.from_config(
    stock_set = "classified2024",
    start     = ...,
    end       = ...,
    n_gate    = 3,             # default per backtest finding
)
```

Each proposal's `sign_type` is the label
`conf{N}:{sorted_constituent_signs}`, e.g.
`conf3:brk_sma,rev_lo,str_hold`.  This distinguishes confluence
proposals from regime proposals in the Daily table.

### `src/viz/daily.py` — shadow mode

Both strategies run on Refresh.  Proposals concatenated: regime first,
then confluence.  Existing UI renders both naturally; confluence rows
visible by their `conf{N}:...` label.

`_CONFLUENCE_N_GATE = 3` constant at the top of `daily.py` for easy
tuning.  Confluence cache invalidates alongside regime cache.

## Caveats and known unknowns

1. **FY2019 outlier** — Sharpe −5.26 on n=12.  Too small to dismiss,
   too small to weight heavily.  Worth investigating if the universe
   ever expands and n grows to 30+; until then, the 6/7-FY positive
   pattern stands.

2. **Bootstrap CI on ΔSharpe not done.**  The +2.47 Sharpe gap vs
   baseline could be sample-noise inflated.  Pre-registered CI test
   was on the natural-next-steps list but explicitly deferred.

3. **Hold time ~22 days** at N≥3.  Much longer than typical regime_sign
   holds.  Different capital efficiency profile; per-trade Sharpe
   may not translate to annualized Sharpe one-for-one.

4. **Sparse fire rate.**  N≥3 produces ~24 trades/year (1-2 per week
   typically; sometimes zero).  Does NOT replace regime_sign — they
   COMPLEMENT each other on the Daily tab.

5. **Shadow mode only.**  No live decision is forced; operator picks
   per-row.  Decision to flip confluence to primary (or drop regime)
   should wait until accumulated discretionary data + bootstrap CI.

## What the data lesson is

- Single-sign feature additions (str_hold candle / gap probe, brk_wall
  / brk_floor, long-term high continuation) repeatedly failed live
  benchmark gates this session.
- Multi-sign **agreement** with the right window definition produced
  the breakthrough.
- The operator's intuition about "state-change signs" was on the right
  track; the implementation needed the validity-windowed framework
  before the signal became visible.
- This is the only positive ship-decision from the May 2026 cycle
  (which generated 10+ REJECTs of single-sign tweaks).

## Files

| Path | Role |
|---|---|
| `src/strategy/confluence_sign.py` | Production strategy class |
| `src/viz/daily.py` | Daily-tab shadow-mode wiring |
| `src/analysis/bullish_confluence_probe.py` | v1 (same-day, REJECT) |
| `src/analysis/bullish_confluence_v2_probe.py` | v2 (validity-windowed, PASS at per-fire level) |
| `src/analysis/confluence_strategy_backtest.py` | v3 live ZsTpSl backtest |
| `src/analysis/benchmark.md` § Confluence Strategy A/B | Canonical numbers |
| `docs/analysis/probe_vs_canonical_lesson.md` | Methodology safeguard learned this cycle |

## Commit trail

- `8a10ee4` — both confluence probes + brk_wall probe
- `bc758d0` — confluence strategy live backtest report
- `ce5f0b9` — ConfluenceSignStrategy + Daily shadow mode
