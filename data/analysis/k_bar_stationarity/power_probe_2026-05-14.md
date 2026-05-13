# Sizing Decorator Power Probe (in-sample upper bound)

Generated: 2026-05-14  
Source: `events_2026-05-14.csv` (cycle-7 cache, 4,498 events)

## Per (regime × tertile) cells

| regime | tertile | mult | n | mean_r | std_r | Sharpe |
|--------|---------|-----:|--:|-------:|------:|-------:|
| bull | 1 | 0.5× | 318 | +2.59pp | 11.58pp | +0.223 |
| bull | 2 | 1.0× | 316 | +3.03pp | 10.29pp | +0.295 |
| bull | 3 | 1.5× | 299 | +2.69pp | 7.97pp | +0.338 |
| bull | 0 | 1.0× | 6 | -0.46pp | 6.43pp | -0.072 |
| choppy | 1 | 0.5× | 708 | +1.19pp | 12.44pp | +0.096 |
| choppy | 2 | 1.0× | 673 | +1.15pp | 9.87pp | +0.116 |
| choppy | 3 | 1.5× | 640 | +2.02pp | 7.54pp | +0.268 |
| choppy | 0 | 1.0× | 12 | +2.94pp | 8.50pp | +0.346 |
| bear | 1 | 1.0× | 552 | +4.32pp | 12.04pp | +0.359 |
| bear | 2 | 1.0× | 489 | +2.74pp | 9.42pp | +0.291 |
| bear | 3 | 1.0× | 470 | +3.77pp | 8.07pp | +0.468 |
| bear | 0 | 1.0× | 15 | +1.91pp | 8.44pp | +0.226 |

## In-sample ΔSharpe (upper bound — uses lifetime tertile labels)

- Baseline (flat 1.0×) per-trade Sharpe: **+0.2412**
- Decorated (schema) per-trade Sharpe: **+0.2579**
- **ΔSharpe (in-sample): +0.0167**  
- Bootstrap 95% CI: [+0.0059, +0.0269] (1000 resamples)

## Detection floor (MDE @ α=0.05, power=0.80, conservative)

- All events (n=4,498): MDE ΔSharpe ≈ **+0.059**
- Gated events only — bull/choppy × T1/T3 (n=1965): MDE ΔSharpe ≈ **+0.089**

## Power verdict

**REJECT full probe** — in-sample upper-bound ΔSharpe is below the +0.05 accept gate. Walk-forward implementation will be worse. The sizing schema cannot detectably lift Sharpe at this n.
