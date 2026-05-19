# N225 trend_score × stock corr regime — Stage 0

Probe run: 2026-05-19.  Stratified per-sign EV table testing whether N225's own trend regime matters more for high-corr stocks (index proxies) than low-corr stocks (independent alpha).

## TL;DR — H3 FAILS, but pattern is INVERTED of hypothesis

**Pre-registered H3 (≥1 sign passes monotone-up in high-corr ∧ flat in low-corr at n≥30) — FAIL.** No sign passes.

**However, the failure is not noise.** High-corr cohort shows systematic **INVERSE monotonicity** for ~12 of 22 signs: DR is HIGHER when N225 trend_score is LOW (bearish regime).  Clean examples (pooled):

| sign | high-corr lo → mid → hi DR | swing | replicates on FY2024+FY2025 holdout? |
|---|---|---:|:---:|
| str_hold      | 65% → 49% → 42% | 23pp ↓ | ✓ (69 → 53 → 51) — but semantic (sign def gated on N225 falling) |
| str_lag       | 52% → 56% → 44% |  8pp ↓ | ✓ (58 → 41 → 45)        — but semantic |
| brk_floor     | 62% → 60% → 45% | 17pp ↓ | ✓ (67 → 49 → 54)        — REAL inverse, non-semantic |
| brk_sma       | 59% → 61% → 48% | 13pp ↓ | partial (60 → 55 → 55)  — weak |
| brk_tenkan_hi | 60% → 53% → 48% | 12pp ↓ | broken (61 → 47 → 56)   — V-shape |
| div_peer      | 73% → 49% → 53% | 20pp ↓ | partial                 — low-n holdout |
| rev_hi        | 58% → 53% → 49% |  9pp ↓ | flat on holdout         — broke |
| str_lead      | 60% → 22% → —   | 38pp ↓ | n thin                  — broke |

**Direction of the inverse**: high-corr stock signs (index proxies) fire BEST when the index itself is in a bearish trend regime.  Two interpretations:
1. **Semantic (str_*)**: str_hold and str_lag are *defined* on N225 falling, so the inverse is baked in.
2. **Mechanical (brk_floor, brk_sma)**: in bullish N225 regime, breakouts in correlated stocks are momentum continuations into already-extended levels (mean-reversion risk).  In bearish N225 regime, the same breakout is a divergence — strength against a weak tide → real alpha.

**This CHALLENGES the trading philosophy in CLAUDE.md**: high-corr stocks are NOT just N225 proxies you ride along with — their sign EV is *anti-correlated* with N225 trend_score.

**H2 also fails**: low-corr cohort also shows N225 score dependence (max-min 15-25pp typical), not the flat response the philosophy predicts.

## Stage-1 verdict

**REJECT** per pre-registered gate.  brk_floor's 17pp swing replicates on holdout, but brk_floor is already REJECT'd ([[project-brk-wall-brk-floor]]) — re-litigating it via a regime-conditional gate just creates a thinner sub-cohort.  Same n-thin trap as the last 7 rejects.

**However, the INVERSE pattern itself is a finding worth keeping in memory** — it predicts the next "intuitive" gate someone proposes ("only enter high-corr longs when N225 is bullish") will FAIL, since the actual data shows the OPPOSITE.

---

## Setup

- Universe: classified2024 (219 stocks loaded)
- N225 trend_score: same 5-feature 250-bar pct-rank (`src.analysis._trend_score`) applied to ^N225 OHLC
- N225 score terciles (cross-section over 1068 dates): lo < 40.6 ≤ mid < 68.5 ≤ hi
- Corr regime: |20-bar rolling corr stock vs ^N225| → high (≥0.6) / mid / low (≤0.3)
- Events tagged: 97502 (skipped: no-corr or no-score)
- Min n per cell to count: 30

- FYs: pooled across all (FY2017–FY2025); holdout: FY2024, FY2025

## Per-sign 2×3 EV table — POOLED

#### brk_bol

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 114/54%/+1.3% | 239/54%/+0.8% | 290/51%/+1.2% | 643/53%/+1.1% |
| **mid** | 205/51%/+1.7% | 311/50%/+1.1% | 377/53%/+1.9% | 893/52%/+1.6% |
| **low** | 126/51%/+2.4% | 178/44%/+0.2% | 319/60%/+3.0% | 623/54%/+2.1% |
| **all** | 445/52%/+1.8% | 728/50%/+0.8% | 986/55%/+2.1% | 2159/52%/+1.6% |

#### brk_floor

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 575/62%/+3.7% | 233/60%/+2.3% | 106/45%/+0.3% | 914/60%/+3.0% |
| **mid** | 360/54%/+1.0% | 227/56%/+0.5% | 196/59%/+2.8% | 783/56%/+1.3% |
| **low** | 131/42%/-1.1% | 97/45%/-0.8% | 165/45%/+0.3% | 393/44%/-0.4% |
| **all** | 1066/57%/+2.2% | 557/56%/+1.0% | 467/51%/+1.4% | 2090/55%/+1.7% |

#### brk_kumo_hi

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 430/53%/+0.8% | 624/47%/+0.4% | 409/45%/-0.4% | 1463/48%/+0.3% |
| **mid** | 382/59%/+2.1% | 505/50%/+1.3% | 510/53%/+1.9% | 1397/54%/+1.7% |
| **low** | 172/51%/+0.9% | 230/43%/+0.7% | 326/55%/+1.4% | 728/50%/+1.1% |
| **all** | 984/55%/+1.3% | 1359/47%/+0.8% | 1245/51%/+1.0% | 3588/51%/+1.0% |

#### brk_kumo_lo

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 909/57%/+3.2% | 455/53%/+1.6% | 153/50%/+1.0% | 1517/55%/+2.5% |
| **mid** | 519/55%/+1.2% | 369/56%/+1.9% | 324/48%/+1.1% | 1212/54%/+1.4% |
| **low** | 168/59%/+1.4% | 195/52%/+1.0% | 242/52%/+0.6% | 605/54%/+1.0% |
| **all** | 1596/57%/+2.3% | 1019/54%/+1.6% | 719/50%/+0.9% | 3334/55%/+1.8% |

#### brk_sma

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 117/59%/+1.6% | 147/61%/+3.1% | 132/48%/+0.3% | 396/56%/+1.7% |
| **mid** | 144/56%/+2.3% | 152/47%/+0.3% | 147/53%/+1.7% | 443/52%/+1.5% |
| **low** | 57/51%/+2.3% | 86/38%/-0.9% | 116/53%/+1.5% | 259/48%/+0.9% |
| **all** | 318/56%/+2.1% | 385/51%/+1.1% | 395/52%/+1.2% | 1098/53%/+1.4% |

#### brk_tenkan_hi

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 2273/60%/+2.7% | 2416/53%/+1.0% | 1848/48%/+0.9% | 6537/54%/+1.6% |
| **mid** | 1518/56%/+1.9% | 2269/51%/+0.8% | 2742/52%/+1.6% | 6529/53%/+1.4% |
| **low** | 696/56%/+1.4% | 1171/48%/+0.1% | 1768/54%/+1.8% | 3635/52%/+1.2% |
| **all** | 4487/58%/+2.2% | 5856/51%/+0.8% | 6358/52%/+1.4% | 16701/53%/+1.4% |

#### brk_tenkan_lo

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 2732/56%/+2.8% | 1966/53%/+1.3% | 1209/55%/+2.4% | 5907/55%/+2.2% |
| **mid** | 2092/55%/+1.5% | 1875/55%/+1.1% | 2141/53%/+2.0% | 6108/55%/+1.5% |
| **low** | 855/54%/+1.5% | 1011/53%/+0.8% | 1544/53%/+1.8% | 3410/53%/+1.4% |
| **all** | 5679/56%/+2.1% | 4852/54%/+1.1% | 4894/54%/+2.0% | 15425/54%/+1.8% |

#### brk_wall

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 236/59%/+2.0% | 316/48%/-0.0% | 486/56%/+1.5% | 1038/54%/+1.1% |
| **mid** | 241/65%/+3.6% | 430/47%/+0.3% | 645/56%/+2.1% | 1316/55%/+1.8% |
| **low** | 151/56%/+1.2% | 231/46%/+0.2% | 465/58%/+2.1% | 847/55%/+1.4% |
| **all** | 628/61%/+2.4% | 977/47%/+0.2% | 1596/56%/+1.9% | 3201/55%/+1.5% |

#### chiko_hi

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 404/52%/+1.0% | 641/53%/+1.1% | 556/51%/+0.8% | 1601/52%/+1.0% |
| **mid** | 423/57%/+1.7% | 664/52%/+1.0% | 861/54%/+2.0% | 1948/54%/+1.6% |
| **low** | 249/55%/+1.0% | 407/54%/+0.1% | 532/54%/+1.9% | 1188/54%/+1.1% |
| **all** | 1076/55%/+1.3% | 1712/53%/+0.8% | 1949/53%/+1.6% | 4737/53%/+1.3% |

#### chiko_lo

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 863/58%/+3.2% | 598/54%/+1.3% | 180/52%/+2.5% | 1641/56%/+2.5% |
| **mid** | 557/51%/+1.1% | 471/61%/+2.3% | 401/48%/+1.0% | 1429/54%/+1.5% |
| **low** | 187/59%/+3.0% | 253/58%/+1.4% | 356/51%/+1.3% | 796/55%/+1.7% |
| **all** | 1607/56%/+2.5% | 1322/57%/+1.7% | 937/50%/+1.4% | 3866/55%/+1.9% |

#### corr_flip

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | *1/100%/+10.6%* | *2/50%/+4.0%* |   —   | 3/67%/+6.2% |
| **mid** | *6/67%/+6.2%* | *11/73%/+7.0%* | *21/38%/-0.9%* | 38/53%/+2.5% |
| **low** | 156/47%/+1.1% | 177/47%/+0.6% | 272/49%/-0.3% | 605/48%/+0.3% |
| **all** | 163/48%/+1.4% | 190/49%/+1.0% | 293/48%/-0.4% | 646/48%/+0.5% |

#### corr_shift

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | *2/0%/-12.8%* | *3/67%/+4.1%* | *2/50%/+12.8%* | 7/43%/+1.7% |
| **mid** | 55/60%/+1.2% | 69/48%/-1.2% | 87/47%/+2.8% | 211/51%/+1.1% |
| **low** | 153/50%/+0.4% | 233/48%/-1.1% | 321/52%/+1.2% | 707/50%/+0.2% |
| **all** | 210/52%/+0.5% | 305/48%/-1.1% | 410/51%/+1.6% | 925/50%/+0.4% |

#### div_gap

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 343/55%/+2.0% | 187/57%/+1.6% | 55/53%/+1.6% | 585/55%/+1.8% |
| **mid** | 511/60%/+2.4% | 378/53%/+1.3% | 299/57%/+2.5% | 1188/57%/+2.1% |
| **low** | 339/58%/+1.6% | 342/48%/+0.7% | 409/50%/+1.1% | 1090/52%/+1.1% |
| **all** | 1193/58%/+2.1% | 907/52%/+1.1% | 763/53%/+1.7% | 2863/55%/+1.7% |

#### div_peer

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 56/73%/+6.3% | 59/49%/+0.9% | 43/53%/+1.7% | 158/59%/+3.0% |
| **mid** | 62/65%/+4.5% | 65/51%/+0.9% | 63/62%/+3.3% | 190/59%/+2.9% |
| **low** | *28/36%/-1.3%* | 47/49%/-0.2% | 49/61%/+4.2% | 124/51%/+1.3% |
| **all** | 146/62%/+4.1% | 171/50%/+0.6% | 155/59%/+3.1% | 472/57%/+2.5% |

#### rev_hi

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 686/58%/+1.9% | 917/53%/+1.0% | 620/49%/+0.7% | 2223/53%/+1.2% |
| **mid** | 623/56%/+1.7% | 849/51%/+0.1% | 1043/51%/+1.3% | 2515/52%/+1.0% |
| **low** | 326/52%/+0.6% | 453/48%/-0.5% | 807/50%/+0.6% | 1586/50%/+0.3% |
| **all** | 1635/56%/+1.6% | 2219/51%/+0.3% | 2470/50%/+0.9% | 6324/52%/+0.9% |

#### rev_lo

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 996/60%/+2.8% | 838/52%/+1.5% | 381/54%/+1.2% | 2215/56%/+2.1% |
| **mid** | 737/56%/+1.6% | 732/54%/+0.9% | 731/51%/+1.7% | 2200/54%/+1.4% |
| **low** | 287/60%/+1.6% | 406/51%/+0.5% | 513/54%/+1.9% | 1206/55%/+1.4% |
| **all** | 2020/58%/+2.2% | 1976/53%/+1.1% | 1625/53%/+1.6% | 5621/55%/+1.6% |

#### rev_nhi

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 660/53%/+1.5% | 1177/54%/+1.1% | 1225/52%/+0.7% | 3062/53%/+1.1% |
| **mid** | 757/52%/+1.0% | 1328/49%/+0.3% | 2031/56%/+2.4% | 4116/53%/+1.5% |
| **low** | 413/52%/+0.8% | 700/48%/+0.2% | 1362/56%/+1.9% | 2475/53%/+1.3% |
| **all** | 1830/52%/+1.1% | 3205/50%/+0.6% | 4618/55%/+1.8% | 9653/53%/+1.3% |

#### rev_nhold

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | *14/64%/+1.6%* |   —   |   —   | 14/64%/+1.6% |
| **mid** | 47/77%/+4.5% |   —   |   —   | 47/77%/+4.5% |
| **low** | *27/41%/-4.0%* |   —   |   —   | 27/41%/-4.0% |
| **all** | 88/64%/+1.4% |   —   |   —   | 88/64%/+1.4% |

#### rev_nlo

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 1057/58%/+3.0% | 401/44%/-1.4% |   —   | 1458/54%/+1.8% |
| **mid** | 346/57%/+1.3% | 182/46%/-0.6% |   —   | 528/53%/+0.6% |
| **low** | 64/62%/+2.4% | 125/54%/+0.6% |   —   | 189/57%/+1.2% |
| **all** | 1467/58%/+2.6% | 708/46%/-0.8% |   —   | 2175/54%/+1.5% |

#### str_hold

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 1378/65%/+4.0% | 620/49%/-0.3% | 209/42%/+0.5% | 2207/58%/+2.5% |
| **mid** | 2259/59%/+2.8% | 1180/46%/-1.9% | 603/43%/+1.5% | 4042/53%/+1.2% |
| **low** | 1435/58%/+2.0% | 1216/49%/-1.0% | 651/48%/+2.2% | 3302/53%/+0.9% |
| **all** | 5072/60%/+2.9% | 3016/48%/-1.2% | 1463/45%/+1.6% | 9551/54%/+1.4% |

#### str_lag

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 149/52%/+0.8% | 324/56%/+1.9% | 138/44%/+1.8% | 611/53%/+1.6% |
| **mid** | 236/49%/-0.2% | 355/56%/+2.0% | 300/44%/+0.6% | 891/50%/+0.9% |
| **low** | 107/63%/+3.2% | 196/45%/+1.2% | 212/41%/-1.6% | 515/47%/+0.5% |
| **all** | 492/53%/+0.8% | 875/54%/+1.8% | 650/43%/+0.1% | 2017/50%/+1.0% |

#### str_lead

Format: n / DR / signed_mean.  *italic* = n < 30.

| corr \\ N225 score | lo | mid | hi | row total |
|---|:---:|:---:|:---:|:---:|
| **high** | 204/60%/+2.4% | 121/22%/-3.7% |   —   | 325/46%/+0.1% |
| **mid** | 290/66%/+3.0% | 142/38%/-0.6% |   —   | 432/57%/+1.8% |
| **low** | 97/58%/+1.0% | 114/47%/+0.0% |   —   | 211/52%/+0.5% |
| **all** | 591/62%/+2.5% | 377/36%/-1.4% |   —   | 968/52%/+0.9% |

---

## Hypothesis check (pre-registered)

**H1**: For HIGH-corr cohort, sign DR shows monotone response to N225 score tercile (≥5pp per step).

**H2**: For LOW-corr cohort, sign DR is flat across N225 terciles (max - min ≤ 3pp).

**H3**: ≥1 sign passes both at n ≥ 30 per cell.

| sign | high cohort: lo→mid→hi DR | H1 mono ≥5pp | low cohort range | H2 flat | n_cells ≥ 30 |
|---|---|:---:|---|:---:|:---:|
| brk_bol | 54% → 54% → 51% | · | max-min 15.5pp | · | 6/6   |
| brk_floor | 62% → 60% → 45% | · | max-min 3.5pp | · | 6/6   |
| brk_kumo_hi | 53% → 47% → 45% | · | max-min 12.3pp | · | 6/6   |
| brk_kumo_lo | 57% → 53% → 50% | · | max-min 6.6pp | · | 6/6   |
| brk_sma | 59% → 61% → 48% | · | max-min 15.1pp | · | 6/6   |
| brk_tenkan_hi | 60% → 53% → 48% | · | max-min 8.0pp | · | 6/6   |
| brk_tenkan_lo | 56% → 53% → 55% | · | max-min 1.0pp | ✓ | 6/6   |
| brk_wall | 59% → 48% → 56% | · | max-min 12.0pp | · | 6/6   |
| chiko_hi | 52% → 53% → 51% | · | max-min 1.4pp | ✓ | 6/6   |
| chiko_lo | 58% → 54% → 52% | · | max-min 7.4pp | · | 6/6   |
| corr_flip | 100% → 50% → — | — | max-min 1.7pp | ✓ | 3/6   |
| corr_shift | 0% → 67% → 50% | · | max-min 3.6pp | · | 3/6   |
| div_gap | 55% → 57% → 53% | · | max-min 9.6pp | · | 6/6   |
| div_peer | 73% → 49% → 53% | · | max-min 25.5pp | · | 5/6   |
| rev_hi | 58% → 53% → 49% | · | max-min 3.9pp | · | 6/6   |
| rev_lo | 60% → 52% → 54% | · | max-min 9.3pp | · | 6/6   |
| rev_nhi | 53% → 54% → 52% | · | max-min 7.9pp | · | 6/6   |
| rev_nhold | 64% → — → — | — | — | — | 0/6   |
| rev_nlo | 58% → 44% → — | — | max-min 8.1pp | · | 4/6   |
| str_hold | 65% → 49% → 42% | · | max-min 9.9pp | · | 6/6   |
| str_lag | 52% → 56% → 44% | · | max-min 21.6pp | · | 6/6   |
| str_lead | 60% → 22% → — | — | max-min 10.4pp | · | 4/6   |

**H3 result**: NO sign passes H1+H2+n≥30. Stage 1 NOT justified by Stage 0 finding.

---

## FY2024+FY2025 holdout replication

Repeats the per-sign hypothesis check on holdout-only events.

## Hypothesis check (pre-registered)

**H1**: For HIGH-corr cohort, sign DR shows monotone response to N225 score tercile (≥5pp per step).

**H2**: For LOW-corr cohort, sign DR is flat across N225 terciles (max - min ≤ 3pp).

**H3**: ≥1 sign passes both at n ≥ 30 per cell.

| sign | high cohort: lo→mid→hi DR | H1 mono ≥5pp | low cohort range | H2 flat | n_cells ≥ 30 |
|---|---|:---:|---|:---:|:---:|
| brk_bol | 45% → 43% → 53% | · | max-min 24.7pp | · | 6/6   |
| brk_floor | 67% → 49% → 54% | · | max-min 4.9pp | · | 6/6   |
| brk_kumo_hi | 52% → 38% → 57% | · | max-min 22.4pp | · | 6/6   |
| brk_kumo_lo | 66% → 53% → 58% | · | max-min 15.1pp | · | 6/6   |
| brk_sma | 60% → 55% → 55% | · | max-min 23.3pp | · | 6/6   |
| brk_tenkan_hi | 61% → 47% → 56% | · | max-min 10.9pp | · | 6/6   |
| brk_tenkan_lo | 58% → 53% → 60% | · | max-min 4.7pp | · | 6/6   |
| brk_wall | 64% → 47% → 64% | · | max-min 15.9pp | · | 6/6   |
| chiko_hi | 52% → 56% → 62% | · | max-min 4.1pp | · | 6/6   |
| chiko_lo | 69% → 45% → 52% | · | max-min 3.4pp | · | 6/6   |
| corr_flip | — → 0% → — | — | max-min 13.1pp | · | 3/6   |
| corr_shift | 0% → 100% → 0% | · | max-min 7.7pp | · | 3/6   |
| div_gap | 57% → 46% → 73% | · | max-min 25.1pp | · | 5/6   |
| div_peer | 72% → 40% → 100% | · | max-min 41.7pp | · | 0/6   |
| rev_hi | 51% → 52% → 53% | · | max-min 10.9pp | · | 6/6   |
| rev_lo | 63% → 52% → 64% | · | max-min 15.1pp | · | 6/6   |
| rev_nhi | 57% → 46% → 57% | · | max-min 8.0pp | · | 6/6   |
| rev_nhold | 67% → — → — | — | — | — | 0/6   |
| rev_nlo | 67% → 31% → — | — | max-min 6.7pp | · | 3/6   |
| str_hold | 69% → 53% → 51% | · | max-min 18.3pp | · | 6/6   |
| str_lag | 58% → 41% → 45% | · | max-min 21.3pp | · | 6/6   |
| str_lead | 80% → 27% → — | — | max-min 16.7pp | · | 3/6   |

**H3 result**: NO sign passes H1+H2+n≥30. Stage 1 NOT justified by Stage 0 finding.
