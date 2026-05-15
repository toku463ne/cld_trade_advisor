# rev_nday — Price Reaches N-Day High/Low (Reversal)
Fires on the first hourly bar of a session where the bar's extreme price
reaches or exceeds the reference N-day high (side='hi') or falls to/below
the N-day low (side='lo'), computed from the *prior* N complete trading
days — no look-ahead.

side='hi'  → bar.high >= N-day reference high → sign_type = "rev_nhi"
Expect DOWN reversal (exhaustion at multi-day high)
side='lo'  → bar.low  <= N-day reference low  → sign_type = "rev_nlo"
Expect UP  bounce   (exhaustion at multi-day low)

Directional filter: for rev_nhi the bar must close below its open (bearish
body confirms rejection); for rev_nlo the bar must close above its open
(bullish body confirms rejection of the low).

Score = 1.0 (uniform — the level touch is the signal; strength is captured
by the n_days parameter choice).

Valid for up to ``valid_bars`` bars after firing (time-bounded only).

## Benchmark notes

```
── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
rev_nhi: run_id=30  n=3579  direction_rate=54.0%  p<0.001
  bench_flw=0.047  bench_rev=0.033  mean_bars=12.6
  Regime split: bear DR=51.2% (p=0.47)  bull DR=54.4% (p<0.001)
  → 2-year result was RECOMMEND but driven by bull market (FY2023+FY2024).

── 7-year cross-validation (FY2018–FY2024) ──
rev_nhi: pooled DR=48.9%  p≈0.024  perm_pass=2/7
→ PROVISIONAL (bull-only): no edge in bear regime across all FYs. Only use when
  N225 last confirmed zigzag peak is a LOW. In bear/neutral regimes treat as SKIP.
rev_nlo (side='lo') is handled by RevNloDetector in rev_nlo.py — see that file.
```
