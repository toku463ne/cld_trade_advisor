# Confluence vs Nikkei ETF — net of transaction costs (2026-05-22)

**Verdict: net WIN holds.** Confluence beats the only buyable passive
alternative (a Nikkei-225 ETF) on Sharpe at every realistic cost level.
Break-even round-trip cost is **34 bps** — above typical liquid large-cap
Japanese round-trip (~10–20 bps). The win is modest and is a Sharpe edge,
not a drawdown edge.

This closes the open thread left by the gross buy-and-hold comparison
([confluence_buyhold.py](../../src/analysis/confluence_buyhold.py); memory
`project_confluence_buyhold_win.md`): confluence beat the *frictionless index*
gross, but the edge was thin and turnover was unmodeled. The worry was that
transaction costs would erase it.

## The question

"Better to just hold an ETF than manually execute ConfluenceSignStrategy?"
The frictionless `^N225` index is not buyable; the realistic passive is a
cap-weighted Nikkei-225 ETF (1321-class) carrying an expense ratio. There is
no buyable equal-weight-225 ETF, so the equal-weight universe is a *reference
ceiling*, not an alternative the operator can actually hold.

## Method (`src/analysis/confluence_buyhold_costs.py`)

Reuses the capital-aware 4-slot daily-marked equity curve from
`confluence_buyhold.py` over the same trading days (FY2017 + FY2018 +
FY2019–2025, 9 FYs, 2187 days ≈ 8.7y), and adds frictions:

- **Confluence:** round-trip cost of *C* bps deducted once per trade on its
  exit day. Each position is 1/4 of capital, so the book-level hit on exit
  day *d* is `(C/10000)/SLOTS` per position exiting that day. The deduction
  is linear in *C*, so one gross pass sweeps *C* ∈ {0, 10, 15, 20} bps.
- **Nikkei ETF:** `^N225` buy-and-hold minus a **0.15%/yr** expense ratio
  (1321-class), accrued daily. The single entry/exit cost is negligible over
  9 FYs and ignored.
- **Universe equal-weight BH:** gross reference only (uninvestable).

## Result

```
9 FYs, 2187 trading days (~8.7y), 326 confluence trades (~38/yr)

series                                total  Sharpe   maxDD ann.cost
------------------------------------------------------------------------------------
^N225 index (frictionless)          +180.6%   +0.68  -32.8%        —
Nikkei ETF (0.15%/yr expense)       +176.9%   +0.67  -32.9%    0.15%
univ equal-wt BH (uninvestable)     +253.2%   +0.86  -36.0%        —
------------------------------------------------------------------------------------
confluence net @0bps (gross)        +256.9%   +0.84  -29.9%    0.00%
confluence net @10bps               +229.0%   +0.79  -31.0%    0.94%
confluence net @15bps               +215.9%   +0.77  -31.5%    1.40%
confluence net @20bps               +203.3%   +0.74  -32.0%    1.87%
------------------------------------------------------------------------------------

Net Sharpe edge over the buyable Nikkei ETF (+ = confluence wins):
  @ 0bps round-trip:  confluence +0.84  vs ETF +0.67  => +0.16  WIN
  @10bps round-trip:  confluence +0.79  vs ETF +0.67  => +0.11  WIN
  @15bps round-trip:  confluence +0.77  vs ETF +0.67  => +0.09  WIN
  @20bps round-trip:  confluence +0.74  vs ETF +0.67  => +0.07  WIN

Break-even round-trip cost (confluence Sharpe == ETF Sharpe): 34 bps
```

## What changed the conclusion

**Turnover is ~38 trades/yr, not ~140** as the open-thread note had guessed
(326 trades over 8.7y; 4 slots × ~26-bar holds simply don't churn much). Cost
drag is therefore only ~0.94%/yr @10bps and ~1.87%/yr @20bps — small enough
that the net Sharpe edge survives all the way to a 34-bps break-even. The
earlier "~140 trades/yr turnover favors the ETF" worry was wrong by ~3.7×.

## What the net win does NOT fix

1. **The drawdown edge erodes with cost.** −29.9% gross → −32.0% @20bps,
   essentially tied with the ETF's −32.9%. Net of realistic costs this is a
   Sharpe edge with no meaningful drawdown edge.
2. **Still a wash vs the equal-weight universe** (+0.86), which remains
   uninvestable. The ETF is the floor confluence clears; the equal-weight
   universe is the ceiling it does not reach.
3. **Only explicit bps are modeled.** Manual-execution slippage and the
   documented absence of discretionary edge
   ([manual cohort n=10 closed](../../) — account_id=2 lost −255k JPY) eat
   into the 34-bps cushion in practice.

## Verdict / how to apply

Net of costs, confluence is a real but modest improvement over the only
buyable passive (Nikkei ETF): +0.07 to +0.16 Sharpe with a 34-bps break-even
cushion, but no drawdown edge once costs are paid. It does not beat the
(uninvestable) equal-weight universe. Treat it as a lower-beta concentrated
long book worth running over a Nikkei ETF if execution discipline is good and
round-trip costs stay under ~30 bps — not as a bear hedge, and not as a
clear win over broad equal-weight exposure.

**Related:** [confluence_strategy.md](confluence_strategy.md) (the strategy),
memory `project_confluence_buyhold_win.md` (gross buy-and-hold + drawdown
cut), `project_confluence_market_neutral.md` (β=0.73 attribution).
