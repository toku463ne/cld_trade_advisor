# rev_nhold — Resilient stock at confirmed N225 trough
Inverse of `rev_nlo`'s capitulation thesis. Where rev_nlo bets on stocks that
fell *hard* alongside a falling N225 (and proved empirically broken — see
rev_nlo header for the SKIP verdict), rev_nhold bets on stocks that *resisted*
the decline. The thesis: when N225 confirms a bottom, the stocks that barely
fell during the index decline are the strongest names and tend to lead the
rebound.

Conditions:
- N225 zigzag confirms a LOW (direction = −2) at bar T
- |N225 drawdown from prior confirmed HIGH to T| ≥ N225_DD_MIN  (default 10 %)
- stock close-to-close drawdown over the same window ≥ STOCK_DD_MAX_NEG
(default −3 %; stock barely fell)
- stock's daily LOW on the trough date is *above* the minimum daily LOW of
the prior LOOKBACK_DAYS (default 20) trading days — i.e. the stock did
not make a fresh 20-day low while N225 was bottoming.

Score = 0.6 × resilience_norm + 0.4 × n225_depth_bonus
resilience_norm  = clip((stk_dd − STOCK_DD_MAX_NEG) / -STOCK_DD_MAX_NEG, 0, 1)
[1.0 when stk_dd ≥ 0 (stock unscathed); 0.0 at threshold]
n225_depth_bonus = min(|n225_dd| / 0.20, 1.0)
[deeper N225 declines are more meaningful contexts]

Fires at the *confirmation* bar (low_bar + ZZ_SIZE N225 trading days), giving
a tradeable signal with no look-ahead.
