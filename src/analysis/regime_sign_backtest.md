# Regime-Sign Strategy Backtest — FY2019–FY2024

Generated: 2026-05-29

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

- Proposals : 259  |  Trades: 58

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 58 | -1.73% | -0.0927% | -2.61 | 39.7% | 18.6 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 8 | +0.83% | +0.0648% | 1.02 | 50.0% | 12.8 |
| low | 50 | -2.14% | -0.1091% | -3.33 | 38.0% | 19.6 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| corr_shift | 10 | -5.87% | -0.2781% | -14.46 | 20.0% | 21.1 |
| div_gap | 2 | -7.26% | -0.2847% | -25.05 | 0.0% | 25.5 |
| rev_hi | 5 | +1.61% | +0.0401% | 3.70 | 80.0% | 40.0 |
| rev_lo | 11 | +1.06% | +0.0680% | 2.12 | 45.5% | 15.5 |
| str_hold | 28 | -1.85% | -0.1164% | -2.24 | 39.3% | 15.9 |
| str_lead | 2 | +2.61% | +2.6115% | 8.49 | 50.0% | 1.0 |

### Exit reasons

`end_of_data:6  sl:24  time:15  tp:13`

---

## FY2020  (2020-04-01 – 2021-03-31)

- Proposals : 227  |  Trades: 46

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 46 | +5.34% | +0.2007% | 6.72 | 58.7% | 26.6 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 7 | +3.73% | +0.1347% | 5.56 | 42.9% | 27.7 |
| low | 39 | +5.63% | +0.2131% | 6.86 | 61.5% | 26.4 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 4 | +9.70% | +0.3290% | 14.56 | 75.0% | 29.5 |
| brk_sma | 3 | +1.59% | +0.0917% | 2.13 | 66.7% | 17.3 |
| corr_flip | 16 | +4.95% | +0.1658% | 6.12 | 68.8% | 29.9 |
| corr_shift | 6 | +2.55% | +0.1205% | 4.17 | 50.0% | 21.2 |
| str_hold | 15 | +5.80% | +0.2291% | 6.13 | 46.7% | 25.3 |
| str_lead | 2 | +10.30% | +0.2944% | 10.92 | 50.0% | 35.0 |

### Exit reasons

`end_of_data:6  sl:6  time:17  tp:17`

---

## FY2021  (2021-04-01 – 2022-03-31)

- Proposals : 412  |  Trades: 50

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 50 | +1.35% | +0.0505% | 2.10 | 52.0% | 26.7 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 8 | -2.91% | -0.1022% | -4.14 | 37.5% | 28.5 |
| low | 42 | +2.16% | +0.0820% | 3.44 | 54.8% | 26.3 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 10 | +2.91% | +0.0944% | 4.28 | 60.0% | 30.8 |
| brk_sma | 1 | -0.55% | -0.0137% | — | 0.0% | 40.0 |
| corr_flip | 8 | -1.19% | -0.0487% | -1.61 | 37.5% | 24.5 |
| div_gap | 8 | +2.58% | +0.1127% | 5.33 | 62.5% | 22.9 |
| div_peer | 6 | +1.01% | +0.0344% | 0.88 | 66.7% | 29.3 |
| str_hold | 10 | +3.42% | +0.1379% | 6.74 | 60.0% | 24.8 |
| str_lag | 6 | -1.34% | -0.0467% | -3.19 | 33.3% | 28.7 |
| str_lead | 1 | -4.48% | -0.4483% | — | 0.0% | 10.0 |

### Exit reasons

`end_of_data:6  sl:9  time:20  tp:15`

---

## FY2022  (2022-04-01 – 2023-03-31)

- Proposals : 437  |  Trades: 46

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 46 | +1.20% | +0.0412% | 2.12 | 54.3% | 29.1 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 7 | +2.50% | +0.0761% | 5.72 | 71.4% | 32.9 |
| low | 39 | +0.97% | +0.0340% | 1.64 | 51.3% | 28.4 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 9 | -1.21% | -0.0354% | -4.51 | 44.4% | 34.1 |
| brk_sma | 1 | +3.11% | +0.2393% | — | 100.0% | 13.0 |
| corr_flip | 9 | -0.24% | -0.0070% | -0.47 | 55.6% | 34.7 |
| div_gap | 5 | -3.25% | -0.0838% | -13.80 | 0.0% | 38.8 |
| div_peer | 2 | -11.48% | -0.4501% | -190.59 | 0.0% | 25.5 |
| rev_hi | 3 | +10.02% | +0.8354% | 30.96 | 100.0% | 12.0 |
| str_hold | 16 | +3.85% | +0.1588% | 5.46 | 68.8% | 24.2 |
| str_lead | 1 | +12.67% | +0.3333% | — | 100.0% | 38.0 |

### Exit reasons

`end_of_data:6  sl:9  time:17  tp:14`

---

## FY2023  (2023-04-01 – 2024-03-31)

- Proposals : 390  |  Trades: 50

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 50 | +4.35% | +0.1655% | 7.69 | 72.0% | 26.3 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 9 | +1.05% | +0.0429% | 1.96 | 66.7% | 24.6 |
| low | 41 | +5.08% | +0.1903% | 8.94 | 73.2% | 26.7 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 6 | -1.48% | -0.0442% | -1.65 | 66.7% | 33.5 |
| brk_sma | 2 | +9.31% | +0.2864% | 41.93 | 100.0% | 32.5 |
| corr_flip | 3 | +3.23% | +0.0807% | 7.69 | 66.7% | 40.0 |
| corr_shift | 2 | +0.52% | +0.0205% | 1.20 | 50.0% | 25.5 |
| div_gap | 10 | +4.21% | +0.1685% | 8.01 | 70.0% | 25.0 |
| div_peer | 7 | +2.20% | +0.1159% | 3.59 | 57.1% | 19.0 |
| rev_hi | 5 | +4.89% | +0.1293% | 13.54 | 80.0% | 37.8 |
| rev_nhi | 4 | +5.97% | +0.2878% | 13.10 | 75.0% | 20.8 |
| rev_nlo | 1 | +16.56% | +0.4246% | — | 100.0% | 39.0 |
| str_hold | 10 | +7.47% | +0.4062% | 13.16 | 80.0% | 18.4 |

### Exit reasons

`end_of_data:6  sl:6  time:15  tp:23`

---

## FY2024  (2024-04-01 – 2025-03-31)

- Proposals : 471  |  Trades: 51

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 51 | +1.07% | +0.0412% | 1.52 | 52.9% | 26.0 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 8 | -2.63% | -0.0945% | -6.27 | 37.5% | 27.9 |
| low | 43 | +1.76% | +0.0687% | 2.37 | 55.8% | 25.6 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 4 | -0.19% | -0.0069% | -0.50 | 50.0% | 27.2 |
| brk_sma | 5 | +9.42% | +0.4323% | 17.10 | 80.0% | 21.8 |
| corr_flip | 5 | +5.13% | +0.2515% | 5.13 | 60.0% | 20.4 |
| div_gap | 3 | -3.56% | -0.1302% | -5.95 | 33.3% | 27.3 |
| div_peer | 4 | -6.75% | -0.2213% | -12.57 | 25.0% | 30.5 |
| rev_hi | 2 | +15.96% | +0.5149% | 16.56 | 100.0% | 31.0 |
| rev_lo | 1 | +5.17% | +0.1293% | — | 100.0% | 40.0 |
| rev_nhi | 3 | -11.68% | -0.5309% | -55.98 | 0.0% | 22.0 |
| rev_nlo | 1 | -0.76% | -0.1082% | — | 0.0% | 7.0 |
| str_hold | 13 | +2.19% | +0.0826% | 4.08 | 61.5% | 26.5 |
| str_lag | 8 | -3.07% | -0.1223% | -3.47 | 37.5% | 25.1 |
| str_lead | 2 | +7.53% | +0.1882% | 46.10 | 100.0% | 40.0 |

### Exit reasons

`end_of_data:6  sl:13  time:15  tp:17`

---

## FY2025  (2025-04-01 – 2026-03-31)

- Proposals : 399  |  Trades: 56

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 56 | +3.43% | +0.1480% | 5.62 | 67.9% | 23.2 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 12 | +5.33% | +0.2882% | 7.74 | 75.0% | 18.5 |
| low | 44 | +2.92% | +0.1191% | 4.92 | 65.9% | 24.5 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 8 | +9.54% | +0.4149% | 17.84 | 87.5% | 23.0 |
| brk_sma | 7 | +8.05% | +0.3611% | 14.02 | 85.7% | 22.3 |
| corr_flip | 1 | +8.68% | +0.2481% | — | 100.0% | 35.0 |
| div_gap | 13 | +0.96% | +0.0427% | 1.47 | 69.2% | 22.5 |
| rev_hi | 7 | +5.58% | +0.2340% | 11.45 | 85.7% | 23.9 |
| rev_lo | 4 | +3.38% | +0.2761% | 7.84 | 50.0% | 12.2 |
| rev_nhi | 2 | -4.25% | -0.1063% | -8.20 | 50.0% | 40.0 |
| str_hold | 14 | -0.41% | -0.0169% | -0.62 | 42.9% | 24.0 |

### Exit reasons

`end_of_data:6  sl:8  time:15  tp:27`

---

## Aggregate  (FY2019–FY2024)

- Total proposals : 2595  |  Total trades: 357

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| aggregate | 357 | +2.05% | +0.0822% | 3.10 | 56.6% | 25.0 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 59 | +1.34% | +0.0559% | 2.15 | 55.9% | 24.1 |
| low | 298 | +2.19% | +0.0871% | 3.27 | 56.7% | 25.2 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 41 | +3.02% | +0.1008% | 4.80 | 63.4% | 29.9 |
| brk_sma | 19 | +6.81% | +0.2975% | 12.70 | 78.9% | 22.9 |
| corr_flip | 42 | +2.66% | +0.0897% | 3.67 | 59.5% | 29.6 |
| corr_shift | 18 | -2.35% | -0.1088% | -4.49 | 33.3% | 21.6 |
| div_gap | 41 | +0.82% | +0.0321% | 1.51 | 53.7% | 25.7 |
| div_peer | 19 | -1.50% | -0.0591% | -1.88 | 47.4% | 25.4 |
| rev_hi | 22 | +6.07% | +0.2042% | 12.16 | 86.4% | 29.7 |
| rev_lo | 16 | +1.90% | +0.1167% | 4.14 | 50.0% | 16.2 |
| rev_nhi | 9 | -2.18% | -0.0858% | -3.48 | 44.4% | 25.4 |
| rev_nlo | 2 | +7.90% | +0.3435% | 10.24 | 50.0% | 23.0 |
| str_hold | 106 | +2.16% | +0.0982% | 2.93 | 53.8% | 22.0 |
| str_lag | 14 | -2.33% | -0.0875% | -3.32 | 35.7% | 26.6 |
| str_lead | 8 | +6.13% | +0.2454% | 11.83 | 62.5% | 25.0 |

### Exit reasons (aggregate)

`end_of_data:42  sl:75  time:114  tp:126`
