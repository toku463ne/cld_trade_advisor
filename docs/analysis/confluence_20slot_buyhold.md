# Confluence at ¥10M / 20 slots vs N225 ETF buy-and-hold

**Created:** 2026-05-30 · **Status:** curiosity benchmark (single deterministic fill-order draw, NOT a
pre-registered null) · Script: `src/analysis/confluence_20slot_buyhold.py`

## Question

Operator: with a ¥10M budget and **20 slots**, does ConfluenceSignStrategy beat just holding the N225 ETF?

## Method

Capital-aware fractional book, same model as `confluence_buyhold.py`: each day a position's price-path
return is divided by `n_slots`; empty slots sit in **cash**. ¥10M / 20 = ¥500k per slot ≫ a 100-share JP
lot for nearly every name, so integer-lot rounding is negligible and the fractional model is a fair proxy.
1 high-corr slot + 19 low/mid (`_MAX_LOW_CORR = 19`). Deterministic entry-date fill order. Stitched
FY2017–FY2025, same trading calendar for every series. Context arms: the 6-slot production book and an
equal-weight buy-and-hold of the whole universe.

## Result

| book | total ret | Sharpe | maxDD | mean held/day | % invested |
|---|---|---|---|---|---|
| confluence-6 (prod) | +149.3% | +0.72 | −26.6% | 6.0 / 6 | 96% |
| **confluence-20** | **+149.4%** | **+0.84** | **−20.3%** | 19.0 / 20 | 92% |
| **N225 ETF (BH)** | +148.2% | +0.66 | −32.8% | — | 100% |
| universe EW (BH) | +194.7% | +0.82 | −34.3% | — | 100% |

(Sharpe = daily ×√252 on the stitched curve; maxDD on stitched curve; mean held = avg concurrent open
names on days with ≥1 position; % invested = avg fraction of slots filled.)

## Findings

1. **Yes — confluence-20 beats the N225 ETF, but on RISK, not return.** Near-identical total return
   (+149% vs +148%) with a materially better Sharpe (**+0.84 vs +0.66**) and a much shallower drawdown
   (**−20% vs −33%**). Spreading ¥10M across ~19 names instead of buying the index is a clean
   risk-adjusted win.

2. **The "20 slots starves on breadth" prior is REFUTED.** Expectation was ~8 names/day + heavy cash drag;
   instead the 20-slot book holds **~19 concurrent names, 92% invested**. The "~8 low-corr/day" figure in
   the backlog/memory is *new fires per day*; with ~26-bar holds those stack up to ~19 open at once. The
   20-slot cap barely binds. (This is the concurrent-held metric; the 8-slot null's 7.92/day was the same
   metric at its own cap.)

3. **It does NOT beat a naive equal-weight basket of the whole universe.** Universe EW returns **more**
   (+195% vs +149%) at a Sharpe tie (+0.82 vs +0.84), losing only on drawdown. So the 20-slot book is
   really a *diversified, drawdown-controlled equity basket*: its edge over the N225 ETF is the
   **diversification / drawdown cut, not stock-picking alpha**. Going 6→20 leaves return flat
   (+149.3→+149.4) and only lowers vol — textbook diversification, consistent with the repo-wide finding
   that confluence is a ~60%-beta book with thin alpha.

## Caveats

- **One deterministic fill-order draw**, not the paired fill-order null. Absolute returns are hugely
  fill-order-sensitive (`project_confluence_fill_order_null`: +141%…+624% band), so +149% is *a* draw, not
  *the* number. The **relative risk picture (Sharpe / maxDD ordering) is the robust part**, and the 20-slot
  book is *less* order-sensitive than the 6-slot because its cap rarely binds.
- ¥10M makes lot granularity negligible; the fractional model is fair here.
- Holding ~19 names is a heavy manual かぶミニ burden — the live plan stays at 6 slots (`project_live_trading_plan`).
  This is a "what-if at scale" benchmark, not a deployment recommendation.
- For a real CI on the conf-20-vs-N225 gap, run it through the paired fill-order null (not done here).
