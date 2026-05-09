# Regime-Sign Strategy Backtest — FY2019–FY2024

Generated: 2026-05-09

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

- Proposals : 334  |  Trades: 41

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 41 | -1.64% | -0.0938% | -2.10 | 46.3% | 17.4 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 8 | -2.24% | -0.1672% | -2.02 | 50.0% | 13.4 |
| low | 33 | -1.49% | -0.0809% | -2.13 | 45.5% | 18.4 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_sma | 1 | -17.06% | -1.1373% | — | 0.0% | 15.0 |
| corr_shift | 4 | -3.73% | -0.1639% | -5.97 | 25.0% | 22.8 |
| div_gap | 4 | -17.77% | -2.7336% | -33.69 | 0.0% | 6.5 |
| rev_nhi | 1 | +26.23% | +1.1403% | — | 100.0% | 23.0 |
| str_hold | 20 | +2.17% | +0.1327% | 3.26 | 60.0% | 16.4 |
| str_lag | 11 | -3.06% | -0.1445% | -4.59 | 45.5% | 21.2 |

### Exit reasons

`end_of_data:4  sl:17  time:8  tp:12`

---

## FY2020  (2020-04-01 – 2021-03-31)

- Proposals : 380  |  Trades: 33

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 33 | +5.89% | +0.2307% | 8.45 | 72.7% | 25.5 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 9 | +5.15% | +0.2506% | 4.78 | 77.8% | 20.6 |
| low | 24 | +6.16% | +0.2251% | 11.86 | 70.8% | 27.4 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_sma | 17 | +7.08% | +0.2451% | 13.20 | 70.6% | 28.9 |
| corr_flip | 7 | +0.28% | +0.0114% | 0.27 | 71.4% | 24.3 |
| str_hold | 2 | +14.88% | +1.9837% | 12.04 | 100.0% | 7.5 |
| str_lag | 7 | +6.03% | +0.2545% | 11.35 | 71.4% | 23.7 |

### Exit reasons

`end_of_data:4  sl:2  time:13  tp:14`

---

## FY2021  (2021-04-01 – 2022-03-31)

- Proposals : 474  |  Trades: 31

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 31 | -0.67% | -0.0225% | -1.00 | 51.6% | 29.6 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 8 | -1.40% | -0.0472% | -2.13 | 50.0% | 29.6 |
| low | 23 | -0.41% | -0.0139% | -0.61 | 52.2% | 29.6 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_sma | 7 | -4.66% | -0.1547% | -10.34 | 28.6% | 30.1 |
| div_gap | 9 | +1.17% | +0.0366% | 2.18 | 55.6% | 32.0 |
| div_peer | 4 | +1.93% | +0.0685% | 2.51 | 75.0% | 28.2 |
| str_hold | 4 | +1.50% | +0.0531% | 1.18 | 50.0% | 28.2 |
| str_lag | 7 | -1.75% | -0.0636% | -2.83 | 57.1% | 27.6 |

### Exit reasons

`end_of_data:4  sl:8  time:12  tp:7`

---

## FY2022  (2022-04-01 – 2023-03-31)

- Proposals : 477  |  Trades: 33

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 33 | +1.26% | +0.0465% | 1.69 | 51.5% | 27.1 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 10 | +4.78% | +0.2152% | 5.42 | 70.0% | 22.2 |
| low | 23 | -0.27% | -0.0091% | -0.40 | 43.5% | 29.3 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_sma | 5 | +5.63% | +0.1769% | 5.27 | 60.0% | 31.8 |
| corr_flip | 1 | -8.88% | -0.5921% | — | 0.0% | 15.0 |
| corr_shift | 2 | +15.55% | +1.1960% | 37.26 | 100.0% | 13.0 |
| div_gap | 8 | +2.14% | +0.0672% | 5.44 | 50.0% | 31.9 |
| div_peer | 7 | +2.34% | +0.1094% | 2.68 | 71.4% | 21.4 |
| rev_lo | 1 | -21.03% | -1.1686% | — | 0.0% | 18.0 |
| str_hold | 9 | -2.36% | -0.0781% | -4.28 | 33.3% | 30.2 |

### Exit reasons

`end_of_data:3  sl:6  time:13  tp:11`

---

## FY2023  (2023-04-01 – 2024-03-31)

- Proposals : 434  |  Trades: 38

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 38 | +2.52% | +0.1084% | 4.58 | 65.8% | 23.2 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 8 | -1.04% | -0.0381% | -2.10 | 62.5% | 27.2 |
| low | 30 | +3.47% | +0.1565% | 6.24 | 66.7% | 22.2 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 1 | +2.90% | +0.0934% | — | 100.0% | 31.0 |
| brk_sma | 21 | +3.04% | +0.1494% | 4.80 | 61.9% | 20.3 |
| corr_flip | 3 | +2.48% | +0.0875% | 46.82 | 100.0% | 28.3 |
| corr_shift | 3 | +3.01% | +0.1412% | 14.37 | 66.7% | 21.3 |
| div_gap | 6 | +3.07% | +0.1190% | 6.32 | 66.7% | 25.8 |
| rev_lo | 1 | +8.93% | +0.4958% | — | 100.0% | 18.0 |
| str_hold | 1 | -3.29% | -0.0823% | — | 0.0% | 40.0 |
| str_lag | 2 | -5.75% | -0.1824% | -5.65 | 50.0% | 31.5 |

### Exit reasons

`end_of_data:4  sl:6  time:10  tp:18`

---

## FY2024  (2024-04-01 – 2025-03-31)

- Proposals : 488  |  Trades: 31

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 31 | +0.71% | +0.0240% | 1.00 | 51.6% | 29.5 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 9 | +2.56% | +0.1003% | 4.64 | 66.7% | 25.6 |
| low | 22 | -0.05% | -0.0016% | -0.06 | 45.5% | 31.2 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 2 | -5.33% | -0.4266% | -3.91 | 50.0% | 12.5 |
| brk_sma | 5 | +0.73% | +0.0221% | 1.03 | 60.0% | 33.2 |
| corr_shift | 8 | +3.10% | +0.1063% | 3.75 | 50.0% | 29.1 |
| div_gap | 8 | -2.45% | -0.0867% | -3.16 | 37.5% | 28.2 |
| div_peer | 5 | +5.86% | +0.1799% | 15.83 | 80.0% | 32.6 |
| str_hold | 3 | -1.85% | -0.0539% | -4.74 | 33.3% | 34.3 |

### Exit reasons

`end_of_data:4  sl:8  time:13  tp:6`

---

## FY2025  (2025-04-01 – 2026-03-31)

- Proposals : 477  |  Trades: 35

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| overall | 35 | +4.77% | +0.1921% | 7.48 | 74.3% | 24.8 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 11 | +8.43% | +0.4310% | 12.58 | 90.9% | 19.5 |
| low | 24 | +3.09% | +0.1135% | 5.10 | 66.7% | 27.2 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 1 | +7.38% | +0.9220% | — | 100.0% | 8.0 |
| brk_sma | 2 | +2.20% | +0.0564% | 2.69 | 50.0% | 39.0 |
| corr_shift | 2 | +3.57% | +0.0892% | 9.15 | 50.0% | 40.0 |
| div_gap | 3 | +2.50% | +0.0826% | 12.05 | 66.7% | 30.3 |
| div_peer | 8 | +13.44% | +0.6892% | 36.08 | 100.0% | 19.5 |
| rev_nhi | 1 | +0.29% | +0.0073% | — | 100.0% | 40.0 |
| str_hold | 3 | +4.54% | +0.1481% | 19.27 | 100.0% | 30.7 |
| str_lag | 15 | +1.27% | +0.0589% | 1.63 | 60.0% | 21.6 |

### Exit reasons

`end_of_data:4  sl:3  time:11  tp:17`

---

## Aggregate  (FY2019–FY2024)

- Total proposals : 3064  |  Total trades: 242

### Overall

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| aggregate | 242 | +1.79% | +0.0717% | 2.56 | 59.1% | 25.0 |

### By corr_mode

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| high | 63 | +2.74% | +0.1220% | 3.40 | 68.3% | 22.4 |
| low | 179 | +1.45% | +0.0563% | 2.21 | 55.9% | 25.8 |

### By sign_type

| label | n | mean_r | mean_r/bar | sharpe | win_rate | hold_bars |
|-------|--:|-------:|----------:|-------:|---------:|----------:|
| brk_bol | 4 | -0.10% | -0.0061% | -0.11 | 75.0% | 16.0 |
| brk_sma | 58 | +2.94% | +0.1103% | 4.38 | 58.6% | 26.7 |
| corr_flip | 11 | +0.04% | +0.0018% | 0.06 | 72.7% | 24.5 |
| corr_shift | 19 | +3.01% | +0.1156% | 4.42 | 52.6% | 26.0 |
| div_gap | 38 | -0.97% | -0.0356% | -1.52 | 47.4% | 27.4 |
| div_peer | 24 | +6.71% | +0.2766% | 10.05 | 83.3% | 24.2 |
| rev_lo | 2 | -6.05% | -0.3364% | -4.54 | 50.0% | 18.0 |
| rev_nhi | 2 | +13.26% | +0.4209% | 11.48 | 100.0% | 31.5 |
| str_hold | 42 | +1.49% | +0.0652% | 2.14 | 54.8% | 22.9 |
| str_lag | 42 | +0.09% | +0.0040% | 0.13 | 57.1% | 23.3 |

### Exit reasons (aggregate)

`end_of_data:27  sl:50  time:80  tp:85`
