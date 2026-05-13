# Regime-Sign Strategy Backtest — FY2019–FY2024

Generated: 2026-05-12

## Configuration

- Exit rule : `zs_tp2.0_sl2.0_a0.3`
- Entry     : `RegimeSignStrategy` (Kumo gate + ADX veto, backtest mode)
- Fill      : two-bar rule (signal on T, fill at T+1 open)
- Portfolio : ≤ 1 high-corr, ≤ 3 low/mid-corr simultaneous positions
- min_dr    : 0.52  (sign/kumo cells with DR ≤ this are excluded from ranking)

## Prior benchmark window per FY

| FY | stock_set | prior sets | yrs |
|----|-----------|-----------|----:|
| FY2019 | `classified2018` | classified2017 | 1 |
| FY2020 | `classified2019` | classified2017, classified2018 | 2 |
| FY2021 | `classified2020` | classified2017, classified2018, classified2019 | 3 |
| FY2022 | `classified2021` | classified2017, classified2018, classified2019, classified2020 | 4 |
| FY2023 | `classified2022` | classified2017, classified2018, classified2019, classified2020, classified2021 | 5 |
| FY2024 | `classified2023` | classified2018, classified2019, classified2020, classified2021, classified2022 | 5 |
| FY2025 | `classified2024` | classified2019, classified2020, classified2021, classified2022, classified2023 | 5 |

---

## FY2019  (2019-04-01 – 2020-03-31)

- Proposals : 0  |  Trades: 0

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | — | — | — | — | — | — |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|

### Exit reasons

``

---

## FY2020  (2020-04-01 – 2021-03-31)

- Proposals : 0  |  Trades: 0

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | — | — | — | — | — | — |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|

### Exit reasons

``

---

## FY2021  (2021-04-01 – 2022-03-31)

- Proposals : 303  |  Trades: 32

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 32 | +1.53% | +0.0591% | 2.07 | 59.4% | 25.8 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 6 | +0.53% | +0.0205% | 0.53 | 50.0% | 25.7 |
| low | 26 | +1.76% | +0.0680% | 2.53 | 61.5% | 25.9 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 1 | -1.33% | -1.3281% | — | 0.0% | 1.0 |
| brk_sma | 2 | +8.27% | +0.3061% | 41.58 | 100.0% | 27.0 |
| corr_shift | 3 | -8.13% | -0.2515% | -12.79 | 33.3% | 32.3 |
| div_gap | 7 | +7.12% | +0.3609% | 10.31 | 71.4% | 19.7 |
| div_peer | 3 | -6.13% | -0.2190% | -7.06 | 33.3% | 28.0 |
| rev_lo | 1 | +1.72% | +0.0430% | — | 100.0% | 40.0 |
| rev_nhi | 4 | -10.93% | -0.3192% | -39.06 | 0.0% | 34.2 |
| str_hold | 8 | +11.57% | +0.5260% | 29.53 | 100.0% | 22.0 |
| str_lag | 3 | -7.96% | -0.2388% | -14.10 | 33.3% | 33.3 |

### Exit reasons

`end_of_data:3  sl:6  time:12  tp:11`

---

## FY2022  (2022-04-01 – 2023-03-31)

- Proposals : 464  |  Trades: 31

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 31 | +5.02% | +0.1730% | 6.61 | 64.5% | 29.0 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 9 | +4.47% | +0.1774% | 6.67 | 66.7% | 25.2 |
| low | 22 | +5.25% | +0.1715% | 6.49 | 63.6% | 30.6 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 1 | +10.12% | +0.4598% | — | 100.0% | 22.0 |
| brk_sma | 2 | +10.22% | +0.2554% | 42.77 | 100.0% | 40.0 |
| corr_shift | 5 | -2.39% | -0.0715% | -2.88 | 40.0% | 33.4 |
| div_gap | 6 | +9.81% | +0.4634% | 24.00 | 100.0% | 21.2 |
| rev_lo | 3 | -2.42% | -0.0932% | -3.14 | 33.3% | 26.0 |
| rev_nhi | 1 | -0.99% | -0.0247% | — | 0.0% | 40.0 |
| str_hold | 9 | +7.23% | +0.2512% | 9.85 | 66.7% | 28.8 |
| str_lag | 4 | +5.35% | +0.1685% | 4.04 | 50.0% | 31.8 |

### Exit reasons

`end_of_data:4  sl:4  time:12  tp:11`

---

## FY2023  (2023-04-01 – 2024-03-31)

- Proposals : 455  |  Trades: 36

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 36 | +3.21% | +0.1290% | 6.57 | 72.2% | 24.9 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 8 | +1.25% | +0.0434% | 2.65 | 62.5% | 28.8 |
| low | 28 | +3.78% | +0.1585% | 7.60 | 75.0% | 23.8 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 2 | +11.47% | +0.4680% | 29.27 | 100.0% | 24.5 |
| brk_sma | 7 | +2.85% | +0.0810% | 4.88 | 57.1% | 35.1 |
| corr_shift | 9 | +0.06% | +0.0023% | 0.11 | 55.6% | 24.6 |
| div_gap | 9 | +2.75% | +0.1323% | 7.67 | 88.9% | 20.8 |
| div_peer | 2 | +7.63% | +1.6945% | 16.21 | 100.0% | 4.5 |
| rev_nhi | 1 | +2.00% | +0.0500% | — | 100.0% | 40.0 |
| str_hold | 6 | +5.06% | +0.2095% | 8.84 | 66.7% | 24.2 |

### Exit reasons

`end_of_data:4  sl:5  time:13  tp:14`

---

## FY2024  (2024-04-01 – 2025-03-31)

- Proposals : 478  |  Trades: 36

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 36 | +0.60% | +0.0240% | 0.83 | 52.8% | 25.2 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 9 | +4.10% | +0.1653% | 5.38 | 66.7% | 24.8 |
| low | 27 | -0.56% | -0.0221% | -0.79 | 48.1% | 25.4 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 5 | -3.08% | -0.0871% | -6.32 | 60.0% | 35.4 |
| brk_sma | 3 | +1.49% | +0.0699% | 2.82 | 66.7% | 21.3 |
| div_gap | 11 | +2.37% | +0.0972% | 3.09 | 63.6% | 24.4 |
| div_peer | 6 | -2.02% | -0.0717% | -2.63 | 16.7% | 28.2 |
| rev_hi | 1 | -8.44% | -0.2110% | — | 0.0% | 40.0 |
| rev_lo | 1 | +13.38% | +0.6370% | — | 100.0% | 21.0 |
| str_hold | 5 | +4.30% | +0.2472% | 7.52 | 60.0% | 17.4 |
| str_lag | 4 | -1.92% | -0.0935% | -1.51 | 50.0% | 20.5 |

### Exit reasons

`end_of_data:4  sl:8  time:13  tp:11`

---

## FY2025  (2025-04-01 – 2026-03-31)

- Proposals : 464  |  Trades: 41

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 41 | +1.09% | +0.0518% | 1.67 | 58.5% | 21.0 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 12 | +0.47% | +0.0251% | 0.79 | 58.3% | 18.7 |
| low | 29 | +1.34% | +0.0613% | 1.96 | 58.6% | 21.9 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_sma | 9 | -4.94% | -0.2527% | -7.28 | 44.4% | 19.6 |
| div_gap | 7 | +0.17% | +0.0078% | 0.22 | 57.1% | 21.6 |
| rev_lo | 9 | +6.48% | +0.3862% | 13.41 | 77.8% | 16.8 |
| rev_nhi | 5 | +0.15% | +0.0049% | 0.24 | 40.0% | 31.4 |
| str_hold | 11 | +2.62% | +0.1280% | 4.30 | 63.6% | 20.5 |

### Exit reasons

`end_of_data:4  sl:10  time:9  tp:18`

---

## Aggregate  (FY2019–FY2024)

- Total proposals : 2164  |  Total trades: 176

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| aggregate | 176 | +2.20% | +0.0880% | 3.25 | 61.4% | 25.0 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 44 | +2.18% | +0.0906% | 3.27 | 61.4% | 24.0 |
| low | 132 | +2.20% | +0.0872% | 3.23 | 61.4% | 25.3 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 9 | +1.81% | +0.0655% | 3.16 | 66.7% | 27.7 |
| brk_sma | 23 | +0.73% | +0.0272% | 1.16 | 60.9% | 27.0 |
| corr_shift | 17 | -2.11% | -0.0739% | -3.36 | 47.1% | 28.5 |
| div_gap | 40 | +4.02% | +0.1844% | 6.30 | 75.0% | 21.8 |
| div_peer | 11 | -1.39% | -0.0583% | -1.86 | 36.4% | 23.8 |
| rev_hi | 1 | -8.44% | -0.2110% | — | 0.0% | 40.0 |
| rev_lo | 14 | +4.72% | +0.2281% | 8.41 | 71.4% | 20.7 |
| rev_nhi | 11 | -3.81% | -0.1121% | -6.75 | 27.3% | 34.0 |
| str_hold | 39 | +6.11% | +0.2672% | 10.19 | 71.8% | 22.9 |
| str_lag | 11 | -0.92% | -0.0328% | -0.84 | 45.5% | 28.1 |

### Exit reasons (aggregate)

`end_of_data:19  sl:33  time:59  tp:65`
