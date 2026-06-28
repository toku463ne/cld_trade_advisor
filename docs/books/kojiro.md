# Trend-Following Rule Construction — 小次郎講師 (Kojiro Koushi), *真・トレーダーズバイブル*

Source: 『小次郎講師流 目標利益を安定的に狙い澄まして獲る 真・トレーダーズバイブル ——Vトレーダーになるためのルール作り』 (小次郎講師). A Japanese trading-rules manual built on two pillars: **(1) the Turtles' money/risk-management chassis** (ATR-volatility unit sizing, the 2N stop, pyramiding, Donchian breakout entries) and **(2) the author's own trend engine — 移動平均線大循環分析 (moving-average "great-cycle" analysis) and 大循環MACD.** The book's thesis: *entry rules only locate the エッジ (edge); survival and the year's P&L are decided by money management, risk management, and 増し玉 (pyramiding)* — so the entry signal is "one element of a trade rule," not the rule.

The book is **timeframe- and instrument-agnostic** and is taught on **daily bars** with the standard MA params **5 / 20 / 40**. Because this repo trades **Japanese stocks on daily OHLCV**, the mechanical content (MA-stack stages, Donchian channels, ATR units, MACD pairs) transfers directly. The final section (`Daily-Bar Adaptation Notes`) is the reader's own mapping to this repo's signs/exits and is **NOT from the book** — and it is heavily qualified by repo memory, because **the book's core claim (momentum-continuation breakouts + volume/trend confirmation) is precisely the family that has repeatedly *inverted* on JP daily bars** (see `dekidaka.md`, `project_lowprice_volspike_stage0_reject`, `project_vol_breakout_confirm_stage0_reject`). Everything below the book sections is a **hypothesis to Stage-0 test, not a validated edge.**

---

## Part 0 — Foundations (Edge, Expected Value, Probability)

### Edge (エッジ) — trade only where one side is favored (p.2, p.37, p.84-86)

Price up/down is normally **50/50 (フィフティフィフティ)**. An "edge" exists only in the rare moments when buy *or* sell is clearly favored — the canonical case being **a trend in progress** (uptrend → only "buy" has an edge; never short an uptrend). **Rule:** read where the edge is, take positions only in the edge direction, otherwise do not trade (休む). Explicitly forbidden: shorting an uptrend because an oscillator says "overbought (買われ過ぎ)" or because "it's about due for a top." A buy signal means *"buy-side has an edge here (probability)"* — **not** *"price will go up (prediction)."* Realistic attainable win-rate ≈ 60%, at most 70%.

- **Codeable as:** `edge_long = trend_state == UP` (大循環 stage bullish, or price > rising long-MA). Gate mean-reversion/oversold buys on the trend filter being up; veto a short generated purely from `RSI>70` while trend is up.

### No prediction / state-reactive design (p.3)

"Turtles do not predict and do not pretend to read the future" (quoting 『タートル流投資の魔術』 and 一目山人's 一目均衡表原著). Voicing a forecast anchors you → you cherry-pick confirming news, refuse to loss-cut, and **average down into losers** (the biggest blow-up mode).

- **Design constraint:** rules condition on bar-T realized state, never a predicted future path. **Forbid add-to-loser logic** (see ナンピン below).

### Trade Edge / Expected Value (TE) — the "勝利の方程式" (p.4-6, p.86-88, p.96-97)

```
TE (期待値) = win_rate × avg_profit − loss_rate × avg_loss      [avg_loss as positive magnitude]
            = L·I − M·J        (L=win_rate, M=1−L, I=avg_win, J=avg_loss)
```

TE > 0 → wins over many trades (law of large numbers, 大数の法則); TE < 0 → loses. **Win-rate alone is meaningless:** an 80%-win rule can lose (8×10 − 2×50 → TE −2万); a 30%-win rule can win. Annual P&L decomposes as **`annual_PnL = TE × annual_trade_count`** ⇒ to raise annual profit at low TE, **increase the number of names traded** (raises count without forcing no-edge trades on one name). Turtle baseline: win 35-40%, RR ≈ 3.

- **Codeable as:** `te = win_rate*mean_win - (1-win_rate)*mean_loss`; this is the per-sign mean per-trade return — already the repo's primary backtest metric. Accept iff `TE>0` AND trade count adequate.

### Risk-Reward (RR) ratio & break-even table (p.7-9, p.88, p.97)

```
RR (PR) = avg_profit / avg_loss          win condition: RR > (1 − win_rate) / win_rate
```

Break-even RR by win-rate (RR above → winner, below → loser):

| win% | 10 | 20 | 30 | 40 | 50 | 60 | 70 | 80 | 90 |
|---|---|---|---|---|---|---|---|---|---|
| break-even RR | 9.00 | 4.00 | 2.33 | 1.50 | 1.00 | 0.67 | 0.43 | 0.25 | 0.11 |

RR > 1 = **損小利大** (cut losses small, let profits run); the typical losing JP retail trader has win% ~60% but RR ~0.33 (損大利小). Win-rate obsession (勝率至上主義) causes premature profit-taking + held losers.

- **Codeable as:** acceptance gate `rr > (1−win_rate)/win_rate`; ZsTpSl exit design target = realized RR clears the win-rate-implied threshold (typically aim RR ≥ 2-3).

### Cognitive-bias catalogue (priors for sign design) (p.44-48)

①損を出したくない病 (disposition effect — hold losers, cut winners) ②サンクコスト病 (sunk-cost) ③結果にこだわりすぎ病 ④**値ごろ病** (anchoring to "cheap/expensive" vs *past* price — explicitly: a price level relative to the past is meaningless; only the future matters) ⑤バンドワゴン病 (herding) ⑥**小数の法則信仰病** (law-of-small-numbers — distrust "N-year cycle" claims; a pattern needs ≥300 random samples to validate). Items ④ and ⑥ directly back this repo's small-sample / fill-order-null discipline.

---

## Part 1 — Money & Risk Management (the Turtle chassis)

> The book's central claim: this section, not the entry, is what makes a "Vトレーダー."

### True Range & ATR (the "N") (p.28-33)

```
TR  = max(high − prev_close,  prev_close − low,  high − low)     # accounts for gaps
ATR = average of TR, default N = 20 (≈ 1 trading month) = the book's "N"
```

Three averaging modes, all selectable:
- **SMA:** `ATR = Σ(TR, 20)/20`
- **MMA (Wilder, Turtles' early):** `ATR_t = (ATR_{t-1}×19 + TR_t) / 20`
- **EMA (Turtles' later):** `ATR_t = (ATR_{t-1}×19 + TR_t×2) / 21` — front-loads recency.

ATR measures the instrument's current average daily move; recompute regularly (Turtles weekly; author intra-week on big moves). Repo already has ATR in `src/indicators/`.

### Position sizing — 1 Unit = 1 ATR = 1% of capital/day (p.17-21, p.33, p.60-61, p.90-94)

The single most important formula in the book:

```
1 Unit (1ユニット) = floor_to_lot( (capital × 0.01) / (ATR(N) × lot_size) )
```

i.e. size each position so a **1 ATR adverse move = 1% of capital**. Worked: capital ¥10M, Sony ATR ¥52, lot 100 → 10M×0.01 / (52×100) = 19.23 → 1900 shares. This **volatility-normalizes risk across instruments** (ATR-80 name carries 2× the per-lot risk of an ATR-40 name). Target annual return 10-30% (40%+ high risk, 100%+ unrealistic).

- **Portfolio risk meter:** `portfolio_risk_pct = Σ open units` (each unit = 1%). A trader must always be able to answer "what % am I risking today?"

### Risk of Ruin (破産の確率) — fixed-fractional sizing (p.11-16)

P(ruin) (drawdown ≥ ~90%, unrecoverable) depends on 5 inputs: capital, win%, avg_win, avg_loss, and **per-trade risk % (the lever)**. Key findings: even a +edge ruins if per-trade risk is too large; ruin% rises with per-trade risk; **beyond a threshold ruin% rises abruptly (非線形 knee)**. Standard target: **P(ruin) ≤ 1%**. Adjust *position size*, not stop location.

- **Codeable as:** `risk_per_trade = capital × f` (f ≈ 1-2%); `units = risk_per_trade / (entry − SL)`; keep `f` below the non-linear ruin knee; Monte-Carlo validate to `P(ruin) ≤ 1%`.

### Stop-loss = 2N (2 ATR), outside the noise band (p.34-36, p.58-61)

Price = **trend + noise**. Place the stop **just outside the noise band** so ordinary pullbacks don't trigger it but a true reversal exits fast. Turtles measured noise ≤ 2 ATR ⇒

```
SL_long  = entry − 2·ATR(N)        SL_short = entry + 2·ATR(N)
```

With 1-unit sizing, a 2N stop = exactly **2% capital loss per unit** (the modern top-trader standard). The book argues the tunable range is **2.0N-3.0N** (default ~2.5N); under 2N is too tight. Always place the stop as a resting 逆指値 at entry; never override discretionarily ("マーケットが常に正しい"). Beware stop-overshoot from gaps / weekend / holiday holds / illiquidity → flatten before long holidays. Repo `atr_trail` / `ZsTpSl` are the analogues (fixed 2-ATR initial stop = `atr_mult=2.0`).

### Trailing stop (p.62-64, p.94)

Raise the stop as price advances. Variants: (a) raise by the full rise; (b) raise by **half** the rise (`Δstop = 0.5·Δhigh`) — author's preference, keeps the stop further from price so ordinary pullbacks don't flush you. Turtle ratchet form:

```
N = ATR(N)
while price ≥ last_raise + 0.5N and stop < avg_entry:   stop += 0.5N; last_raise = price   # rush to break-even
after stop > avg_entry:  if price ≥ last_raise + 1.0N:  stop += 0.5N; last_raise = price     # then trail at half-pace
```

Advanced 大循環 profit-exit: place a 逆指値 sell at **yesterday's mid MA value** (`exit if close < SMA20[t-1]`).

### Pyramiding (増し玉) — add to winners only (p.25-27, p.51-52, p.65-67, p.94)

After a winning entry, **add 1 unit each time price moves a fixed ATR increment in your favor**, up to the per-name cap. Two add-spacings:
- **0.5N (base):** add every ½ ATR; max risk at 4 units = 0.5N+1N+1.5N+2N = **5N (5%)**; better average price.
- **1.0N (low-risk):** add every 1 ATR; max risk at 4 units = **2N (2%)**; worse average price.

On **each add, move the single aggregate stop to 2N below the latest fill** and apply it to all units. Anchor stops/adds to **actual** fill prices on slippage. Max 3 adds (4-unit cap).

### Exposure caps by correlation (p.25-27, p.65, p.94)

The diversification rule **always overrides** the add rule:

| Scope | Cap |
|---|---|
| Same name (同一銘柄) | **4 units** |
| Highly-correlated group (相関高い, \|ρ\|≳0.7) | **6 units** |
| Loosely-correlated group (相関ある) | **10 units** |
| One direction, all names (買い or 売り) | **12 units** |
| Concurrent distinct low-corr names | ~3 names |

Rationale: 4 units in one name share one adverse condition; correlated names give **no diversification** ("高い相関関係にある銘柄を複数取引しても分散効果は得られない"). Correlation is **non-stationary** — re-measure each period (worked gold-complex ρ table: NY金 0.92, 東京ゴム 0.83 … ドル/円 −0.23). **This is exactly CLAUDE.md's "count high-corr positions as one logical bet" / CorrRegime gating** — reuse `src/indicators/moving_corr` + `corr_regime`.

- **Codeable as:** before any add, require `units_name+1 ≤ 4 AND units_corr_cluster+1 ≤ 6 AND units_loose+1 ≤ 10 AND units_side+1 ≤ 12`. Closing a unit decrements all group counters.

### ナンピン (averaging-down) — PROHIBITED (p.68-72); 両建て/ツナギ (p.72-77)

**Never add to a losing position.** Trend-followers add up (1000→1100→1200, avg in profit); ナンピン adds down (1000→900→800, avg in loss) — gambling on a reversal that may never come ("上がりっぱなし" markets exist). **両建て (equal long+short)** freezes PnL = event insurance only, never a substitute for cutting losses (Gann: 禁止). **利乗せの両建て / ツナギ売り** = against a held long, repeatedly open a short at swing-highs and cover at swing-lows to harvest counter-swings while the core runs (overlay short at zigzag HIGH, cover at zigzag LOW).

- **Strategy guard:** never add to a position with negative unrealized PnL; only pyramid in the profitable direction.

### Verification / change-control hygiene (p.95-97)

Count trades **in units** (1-unit entry closed by 1 exit = 1 trade) to keep win%/RR well-defined under scaling. Keep a rule **unchanged ≥ 6 months** before revising; **flatten all positions before changing a rule**; when verifying mid-flight, **mark open positions to market** as if closed (don't let "take profits early / defer losses" inflate a period). V-trader rate `P = expected_annual_pnl / annual_target` (≥1 = rule meets target). Matches the repo's anti-overfitting / honest-evaluation methodology.

---

## Part 2 — Turtle Breakout Entries / Exits

### Donchian / Turtle breakout entries (p.39-40, p.49-53, p.64)

New-high/new-low breakouts carry an edge because trapped sellers' limit orders at the old high get exhausted, removing overhead supply ("スルスル"). Two systems:

```
Entry Rule 1 (mid trend):  buy if high > max(high[t-20 .. t-1])   sell if low < min(low[t-20 .. t-1])
Entry Rule 2 (long trend): buy if high > 55-day high              sell if low < 55-day low
```

On fill, set SL = entry ∓ 2·ATR. **Donchian-system variant** adds a long-term trend filter: only buy when `SMA(50) > SMA(300)`, only sell when `SMA(50) < SMA(300)`.

- **PL filter (Rule 1 only):** if the name's **previous trade was a winner, skip the next Rule-1 signal** (two big trends rarely occur back-to-back; cut ~30% of trades with no profit loss). Rule 2 has **no** PL filter — it is the failsafe that catches a huge trend the PL filter made you skip.
- **Weakness:** breakout signals arrive **late** (trend already underway) → for small trends you can enter the top. This motivates the author's own 大循環 / MACD entries below.

### Donchian breakout exits (p.64)

Exit channel = half the entry period, opposite extreme: a 20-day-high long exits on the **10-day low**; a 55-day-high long exits on the **20-day low** (mirror for shorts).

### Exit on trend-end, not a fixed target (p.54-57)

Never pre-set a fixed take-profit price — "頭と尻尾は市場に返す" (give the head & tail back, capture the middle). The year's P&L is decided by the few large trends; a fixed target caps exactly those. Exit on a **trend-termination signal** (MA cross / stage change), not a `tp_price`.

### Universe filters (p.78, p.93, p.125)

Trade only names that are: **liquid** (your order doesn't move price), **volatile** (ATR/price above floor — "volatility is the trend-follower's lifeline"), **shortable**, **unrestricted**, P&L-nettable, and **clean/trendy** (few gaps 窓, short wicks ヒゲ, not whippy 乱高下, persistent trends). Watch ~**5 names** so at least one has a big yearly move. Down-moves are faster/sharper than up-moves ("壊れ vs 積み上げ") — shorts profit quicker but suffer more stop-overshoot on crashes.

---

## Part 3 — 移動平均線大循環分析 (Moving-Average Great-Cycle)

The author's primary trend engine. Plot **3 SMAs: 短期=5, 中期=20, 長期=40** (daily) and read **order (並び順) → stage**, **slope (傾き) → strength**, **spacing (間隔) → continuity**. (Other common params 25/50/75/100/150/200; weekly 13/26/52.)

### The 6 ステージ (stages) by MA ordering (p.108, p.113, p.131)

Top-to-bottom ordering of (short, mid, long) has exactly 6 permutations:

| Stage | Ordering (top→bottom) | Meaning |
|---|---|---|
| **1** | 短 > 中 > 長 | stable **uptrend** (買い本仕掛け zone) |
| **2** | 中 > 短 > 長 | uptrend ending |
| **3** | 中 > 長 > 短 | entering downtrend |
| **4** | 長 > 中 > 短 | stable **downtrend** (売り本仕掛け zone) |
| **5** | 長 > 短 > 中 | downtrend ending |
| **6** | 短 > 長 > 中 | entering uptrend |

```
s=SMA(5); m=SMA(20); l=SMA(40)   # EMA(5,20,40) for 大循環MACD
rank the three values → ordering tuple → stage int 1..6
rising(x) = x[t] > x[t-1]
```

### 大循環の法則 — the stage cycle (p.109-112)

Stages transition **one step at a time** (never skip, bar a rare 3-line single-point cross). ~**70% 順行 (forward)** 1→2→3→4→5→6→1…; ~**30% 逆行 (reverse)** 1→6→5→4→3→2→1…. Knowing only 2 possible next states is the edge. If a market follows 順行 cleanly = high-EV ("獲りやすい"); if it won't obey the law, drop that market.

### Stage transitions = 3 GC + 3 DC (p.129-130)

Each step is a specific 2-MA cross among (5,20,40):
- 1→2: SMA5 **DC** SMA20 · 2→3: SMA5 **DC** SMA40 · 3→4: SMA20 **DC** SMA40 (**帯 turns 陰転**)
- 4→5: SMA5 **GC** SMA20 · 5→6: SMA5 **GC** SMA40 · 6→1: SMA20 **GC** SMA40 (**帯 turns 陽転**)

**Fakeout suppression:** a lone 2-MA cross gives many ダマシ in ranges; require **all 3 MA slopes aligned** before trusting a transition. Cost = slightly later entry (solved by 早仕掛け).

### クロスされる側の傾き — does the cross "take"? (p.114-116)

Whether two MAs actually cross is read from the **slope of the slower (longer-period) line being crossed**: ①steep-up → cross almost certainly fails/reverts; ②mild-up → likely fails; ③flat-to-down → likely succeeds; ④down → succeeds and won't revert. Gate: only treat a transition as valid if `slope(crossed_MA) ≤ ~flat`.

### 帯 (band) = the SMA20–SMA40 gap (p.119-124, p.127)

```
band_dir   = sign(SMA20 − SMA40)     # +1 上昇帯(陽) / −1 下降帯(陰)
band_width = |SMA20 − SMA40|         # trend strength; widening=continuation, narrowing→もみ合い
陽転/陰転  = cross(SMA20, SMA40)      # 帯のねじれ = 大転換 (major reversal)
```

The 帯 shows the 大局 (big-picture) trend. **A thick, stable band acts as dynamic S/R:** in a thick 上昇帯, dips into the band = **押し目買い** (buy-the-dip, price repelled up); in a thick 下降帯, rallies into it = **戻り売り**. Valid **only while** the band is ◎stable ◎slope intact ◎wide:

```
if band_dir>0 and band_width>thresh and slope(SMA40)>0:
    buy when low ≤ SMA20 (price touches top of band) and price holds   # mirror for shorts
```

### Stage-by-stage strategy & entry tiers (p.118-119, p.124-134)

| Stage | Primary action |
|---|---|
| 1 | **BUY 本仕掛け** (only after all 3 slopes up); widening gaps → add |
| 2 | exit longs (手仕舞い); 売り試し玉 timing — unless band still thick |
| 3 | stand aside (様子見); optional 早仕掛け short |
| 4 | **SELL 本仕掛け** (all 3 slopes down) |
| 5 | exit shorts; if band thick hold short; buy 試し玉 |
| 6 | stand aside; **buy 早仕掛け** (one step before Stage 1) |

**Canonical entries (p.125):** BUY = `stage==1 AND rising(s,m,l)`; SELL = `stage==4 AND falling(s,m,l)`. **Exit long** = transition 1→2 (`cross_down(SMA5,SMA20)`). Mirror for short.

**Three commitment tiers** (trade-off: earliness vs ダマシ):
- **本仕掛け** (full) — Stage 1, all 3 rising. *Weakness: late → small/zero gain on small trends.*
- **早仕掛け** (full size, 1 step early) — Stage 5/6 with all 3 slopes already turned up (or long-MA flattening from down). Higher reward on small trends, higher fakeout risk.
- **試し玉** (probe, **⅓-⅕ size**) — Stage 5/6/1 with short+mid rising and long-MA clearly easing from down; reconnaissance position, looser conditions allowed because size keeps the loss non-fatal. Pairs with 本仕掛け (試し玉 + 本仕掛け = 1 set).

**仕掛けポイント早見表 (buy quick-reference, p.134)** — `(stage, 3-slope clarity) → {full, early, probe(⅓-⅕), none}`; stronger up-alignment + later stage → bigger commitment (sell = mirror).

**もみ合い放れ (p.127-128):** a range breaks UP only via Stage 1, DOWN only via Stage 4 (stages 2/3/5/6 never produce a valid break). Watch SMA5: accelerating away from band center → real breakout; turning back → range continues.

**獲りやすい vs 獲りにくい regime filter (p.128):** easy = stages 1/4 long-lasting + wide band; hard = stages 1/4 end fast, transition stages dominate, narrow/clustered band → don't trade.

**Pitfall — 急騰急落 (p.125-126):** a single big candle can jump Stage 4→1 then immediately revert to Stage 2. On a spike-driven stage flip (`|ret| > k·ATR` in 1 bar), **wait one bar (ワンテンポ)** and confirm before entering.

---

## Part 4 — 大循環MACD (the early-entry overlay)

The author's solution to 大循環分析's only flaw (late signals): overlay MACD, which "gives buy/sell signals earlier than MAs."

### EMA, MACD definitions (p.136-141)

```
EMA(N): ema_t = (ema_{t-1}·(N−1) + price·2)/(N+1)      # α = 2/(N+1) — standard EMA; validates repo EMA
MACD1   = EMA(12) − EMA(26)
SIGNAL  = EMA(MACD1, 9)
HIST    = MACD1 − SIGNAL
```

EMA's turns are closer in time to price turns than SMA (SMA lags + flips on the *dropped* value); EMA is smoother → fewer ダマシ.

### Three signals in chronological order (p.142-146)

For a bottom→up turn, signals appear in this order (earliest → latest):
1. **HIST bottom-out** (max-negative then turns up) — earliest, but noisy = "劇場のベル," only good for a 試し玉.
2. **MACD1 × SIGNAL G-cross** — the **best / most reliable** buy signal → 本仕掛け.
3. **EMA12 × EMA26 G-cross** — latest; this is the normal (late) 大循環 entry → use as the **増し玉** trigger.

(Sell mirror: HIST peak-out → MACD1×SIGNAL D-cross → EMA12×EMA26 D-cross.)

- **増し玉 STOP conditions** (do NOT add when any appear, chronologically): HIST rise eases → HIST falls → MACD1 rise eases → MACD1 goes flat.
- **仕切り (exit):** trim **half** at HIST peak-out→down; exit the rest at **MACD1×SIGNAL D-cross**.
- **ロスカット:** prior swing low (直近の底). After a G-cross buy, if price quickly breaks the prior bottom → not a true bottom-out → exit.
- **ダマシ filters:** prefer G-crosses **below** the zero line + **large** HIST swings (above-zero / near-zero crosses & small HIST swings are unreliable). Post-entry: if MACD1 or SIGNAL stalls (not both right-shoulder-up), or **MACD1 never reaches the zero line** (so the EMA cross can't happen) → it was a ダマシ → exit immediately.

### 大循環MACD construction — 3 MACDs (p.146-150)

大循環分析 EMAs (5,20,40) on top + **three MACDs**, each = a pairwise EMA gap (signal 9), each pre-reading one stage transition:

```
MACD(上)  = EMA5  − EMA20   (signal 9)   zero-up-cross ⇒ SMA5×SMA20 GC ⇒ → Stage 5
MACD(中)  = EMA5  − EMA40   (signal 9)   zero-up-cross ⇒ SMA5×SMA40 GC ⇒ → Stage 6
MACD(下)  = EMA20 − EMA40   (signal 9)   zero-up-cross ⇒ SMA20×SMA40 GC ⇒ → Stage 1
```

MACD(上) is fast (leads price); MACD(下) is slow (confirms big move). **Require all 3 MACDs right-shoulder-up** so you aren't fooled by the fast line alone.

**Buy entries** (mirror for sells: 3 MACDs falling, MACD(下) D-cross, stages {3,2,1}):
- **本仕掛け:** `stage==6 AND crossed_up(MACD(下), signal) AND rising(all 3 MACDs)`
- **早仕掛け:** `stage==5 AND …`
- **試し玉 (⅓-⅕):** `stage==4 AND …` (band still spread above → may bounce, caution)

**Exit (手仕舞い):** watch the fastest **MACD(上)**; its roll-over pre-tells the coming stage change → exit a step early (`not rising(MACD(上))`), or trail a 逆指値 at the prior-day **EMA20**.

---

## Daily-Bar Adaptation Notes (for this repo)

> **This section is the reader's mapping — NOT from the book, and it is heavily gated by repo memory.** Kojiro's system is a **momentum-continuation, trend-follow, breakout-and-pyramid** framework. The repo has *already* found that the JP-daily momentum/volume-confirmation family **inverts or washes out** (`project_lowprice_volspike_stage0_reject`, `project_vol_breakout_confirm_stage0_reject`, `project_brk_prev2peaks_stage0_reject`, the whole `dekidaka.md` arc), and that **no selection/ordering rule beats the paired fill-order null at ~36 trades/yr** (CLAUDE.md Methodology). So most of this book lands as **sizing/risk primitives** (likely useful) and **entry signs** (must clear the full Stage-0 → fill-order-null gauntlet, with a *low prior* given the momentum-inversion history). Everything below is a hypothesis to test.

### Where the book CONFIRMS existing repo doctrine (adopt as priors, not new tests)

| Book concept | Repo equivalent / status |
|---|---|
| Correlation exposure caps; "correlated names = no diversification" (p.25-27, p.65) | **Already repo doctrine** — CLAUDE.md "count high-corr positions as one logical bet," CorrRegime gating, `project_daily_diversification_order` (diversify by correlation, SHIPPED). The 4/6/10/12-unit caps are a concrete parameterization. |
| ATR-volatility unit sizing, 2N stop (p.17-21, p.36, p.60) | Matches `atr_trail`, `ZsTpSl`; 2-ATR initial stop = `atr_mult=2.0`. `src/indicators` has ATR. |
| EV/TE as the metric, count in units, mark-to-market, ≥6-month rule freeze (p.86-97) | Matches the repo's per-sign mean-return metric + anti-overfitting methodology. |
| 値ごろ病 (anchoring to past price) + 小数の法則 (≥300 samples) (p.44-48) | Backs the repo's small-sample / fill-order-null discipline; `feedback_score_calibration_insufficient`, `project_confluence_strength_probe`. |
| Selection-rule skepticism is implicit; **the book is bullish on selection (PL filter, EV-ranking)** | **Repo says the opposite** — the PL filter and EV-ranked candidate selection are exactly the "which-of-6-slots" rules that died at the fill-order null. Treat book selection claims as **theory, not adoptable.** |

### Mapping book signs to existing repo signs

| Book sign | Closest repo sign | Notes |
|---|---|---|
| Golden/Dead cross, single MA (p.37-39) | `brk_sma` | `close` crosses `SMA(N)`. |
| Donchian 20/55 N-day high/low breakout (p.49-53) | **net-new** (`brk_*` are SMA/Bollinger, not channel) | No N-day high/low channel sign yet. |
| 大循環 Stage-1/4 perfect-order regime (p.105-134) | conceptually near `corr_regime` / RegimeSign gating | A 3-MA-stack stage classifier is **net-new** as a sign. |
| 帯 押し目買い / 戻り売り (p.122-124) | adjacent to `str_hold` (hold-while-pullback) | Pullback-into-rising-band buy; pullback-continuation family the repo has *repeatedly rejected* (`project_brk_kumo_pullback_gate_reject`, no_supply rejects). |
| MACD1×Signal / HIST signals (p.139-146) | **net-new** (no MACD sign module) | MACD is in the stack but not a sign. |
| Trailing stop (half-rise; prior-day MA 逆指値) (p.62-64, p.94) | `atr_trail`, `adx_trail`, `next_peak` | Half-rise trail & "stop at SMA20[t-1]" are exit variants to A/B. |
| New-high breakout edge / S波動 role-flip (p.39-41) | `brk_sma`, `brk_bol`; VAP node S/R in `dekidaka.md` | Role-flip S/R already tested negative on JP daily (VAP node REJECT). |

### Net-new candidates (each = a Stage-0 hypothesis, LOW prior given momentum-inversion history)

1. **`brk_donchian`** — N-day high/low channel breakout (Turtle Rule 1 = 20, Rule 2 = 55), with the `SMA(50)>SMA(300)` trend filter and 2-ATR stop. Cleanest net-new sign vs the existing `brk_*` family. *Prior:* breakout-momentum on JP daily has inverted before (`vol_breakout_confirm`, `lowprice_volspike`) — test the **un-volume-gated** channel first; do NOT add a volume-confirm gate (it inverted).
2. **`ma_stage`** — 大循環 3-MA-stack stage (5/20/40) as a sign/regime feature: fire long when `stage==1 AND rising(SMA5,SMA20,SMA40)`. Test both as a standalone entry and as a **regime gate** for confluence (does conditioning existing signs on Stage 1 vs 4 add EV?). **→ Stage-0 REJECT (2026-06-28):** the 6-stage axis carries NO beta-stripped forward-return structure on JP daily bars. Raw h10 return by stage is flat (~baseline +0.36% drift); the book's Stage1>Stage4 claim is mildly INVERTED raw (S1 +0.31 vs S4 +0.50, spread −0.19pp) and exactly zero once market-neutralized (S1−S4 MN +0.01pp; every stage MN ≈0). Stage-1 onset sits BELOW baseline (exc_mn −0.09); the パーフェクトオーダー slope-confirmation adds nothing (onset+PO MN −0.06). Per-FY is textbook beta — positive in bull FYs (2020 +1.8, 2025 +1.3), negative in bear FYs (2018 −1.0, 2024 −0.7), all vanishing under MN. Well-powered (4.4M bars, 2775 stocks, FY2016-2026), so it is a real null, not thinness. This also closes the regime-gate angle: Stage = a per-stock trend-regime proxy = the same family as the already-rejected N225-trend-score gate (CLAUDE.md High-corr caveat). See `src/analysis/ma_stage_stage0.py` and memory `project_ma_stage_stage0_reject.md`.
3. **`ma_band`** — 帯 features (`band_dir`, `band_width = SMA20−SMA40`, 陽転/陰転 cross) as a trend-strength/continuity filter on existing signs. Cheap to compute; test as an EV-conditioning axis like the N225-trend-score work.
4. **`macd_stage`** — 大循環MACD: MACD1×Signal G-cross gated on `stage∈{5,6}` and all-3-MACD-rising, with the below-zero + large-HIST ダマシ filters. The book's "enter 1-2 stages early" claim is directly testable as fresh-vs-co-fired DR (cf. the orthogonality test in `project_accum_volume_stage0_reject`).
5. **`stage_pullback`** (帯 押し目買い) — buy a dip to SMA20 inside a thick rising band. **Lowest prior** — this is the pullback-continuation pattern the repo has rejected repeatedly (`brk_kumo_pullback_gate`, no_supply, `confluence_no_supply_entry` adverse-selection). Test only to confirm the inversion replicates, not expecting an edge.

### Exit / sizing experiments (more promising than the entry signs)

- **2.5N initial stop & half-rise trail** — A/B the book's 2.0N-3.0N stop band and the "raise stop by ½ the rise" trail against the current `ZsTpSl` / `adx_trail_d8.0` baseline (note `project_confluence_exit_ab_reject` — exit A/Bs have washed out at the portfolio null before).
- **Prior-day SMA20 逆指値 trailing exit** — `exit if close < SMA20[t-1]` as a trend-end exit; compare to `next_peak` / zigzag exits.
- **0.5N vs 1.0N pyramiding** within the 4-unit cap — but **pyramiding/sizing has repeatedly died at the premise** in this repo (`project_pead_sizing_reject`, `project_confluence_deployed_capital_reject`): the 6-slot book has no cohort spread for sizing to harvest. Treat 増し玉 as the *lowest-priority* item.

### Hard constraints carried from the book that ALREADY match repo rules

- **Never average down (ナンピン)** — only ever add in the profitable direction; forbid add-to-loser (matches the "register only via crud, no auto-execution, manual trading" discipline + the no-prediction constraint).
- **Risk of ruin ≤ 1%, per-trade risk 1-2%, volatility-normalized units** — a clean sizing target to layer onto `src/portfolio/`.
- **Diversify the 6 live slots by correlation, not by predicted return** — the book's EV-ranking theory is explicitly *not* adoptable here (fill-order null); this is already what `project_daily_diversification_order` ships.

For each entry candidate: Stage-0 the per-fire DR / forward-return distribution vs the relevant baseline (and, for `ma_stage`/`ma_band`, test as a regime-conditioning gate rather than a selection rule); only if a per-fire edge survives, proceed to the paired fill-order null on the 6-slot book before claiming any selection value. Given the momentum-inversion track record, **enter each with a low prior and check the relevant `dekidaka.md` / breakout reject memory first.**
