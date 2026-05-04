# Operation Cycle Guide

Interactive guide through each step of the trading cycle.

## Steps

1. **Data Collection** — `python -m src.data.collect`
   Fetch latest price and volume data for all watchlist tickers.

2. **Strategy Review** — Check if parameter tuning is needed
   Compare current backtest results against the previous cycle.

3. **Backtest** — `/backtest <strategy>`
   Re-run if any strategy parameters were changed.

4. **Report** — `/report`
   Review trade candidates and capital allocation before acting.

5. **Execute Trades** — Human decision and manual execution only.
   The program does not place orders. This step is intentionally skipped.

6. **Register Positions** — `python -m src.portfolio.register`
   Log executed trades into the DB through the portfolio module.

## Important
Step 5 is always performed by the human. The program never places orders.