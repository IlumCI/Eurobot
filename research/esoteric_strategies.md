# Esoteric Strategy Research — Ranked Catalog for a $10 Solana CLMM Bot

Deep-research sweep: 104 agents, 5 search angles, ~30 primary sources fetched, 68 falsifiable
claims extracted, 25 adversarially verified (3 independent verifiers per claim, 2/3 refutes to
kill). Verification tags used below: **[CONFIRMED 3-0]**, **[CONFIRMED 2-1]**, **[REFUTED 0-3]**,
**[UNVERIFIED]** (extraction done, adversarial pass rate-limited — treat as primary-source-backed
but not independently checked).

---

## 0. The null hypothesis every strategy must beat: LVR

Before ranking anything esoteric, the mainstream case against on-chain market making is one
closed-form number — **loss-versus-rebalancing** (Milionis, Moallemi, Roughgarden, Zhang,
arXiv:2208.06046):

- For a constant-product AMM, adverse-selection bleed is exactly **σ²/8 per unit time** as a
  fraction of pool value. 5% daily vol → 3.125 bp/day (~11%/yr) paid to arbitrageurs, no matter
  what fees do. [UNVERIFIED extraction, but this is the canonical published result]
- LVR scales **quadratically with volatility** and **linearly with marginal liquidity** — so
  tight CLMM bands raise fee capture and LVR *proportionally together*. Concentration is not a
  free lunch.
- Empirically (arXiv:2404.05803): in most large Uniswap pools **fees do not cover arb losses**
  (flagship WETH-USDC 5bp pool recovers only ~80% of LVR).

**Why this doesn't kill our project — four structural outs, all evidence-backed:**

1. **Long-tail pools invert the result.** Fees in less-traded token pools *exceed* arb losses,
   sometimes by ~50% (MATIC-ETH, LINK-ETH). Blue-chip pools are where LPs die; obscure
   volatile pairs are where they eat. [UNVERIFIED extraction of arXiv:2404.05803]
2. **Fast blocks cut LVR.** Moving from 12s to 100ms blocks cuts arb losses 20–70%. Solana's
   ~400ms slots put us near the good end of that curve.
3. **High fee tiers recapture LVR.** Simulation: a 0.3%-fee pool recaptures 80–90% of LVR as
   fees when arb transactions are cheap. Memecoin pools on Meteora/Orca run 0.25–2% (Meteora
   DLMM dynamic fees spike higher during vol) — far above the tiers studied.
4. **The LVR model assumes an infinitely deep external CEX reference market.** For tokens with
   no CEX listing, the pool *is* price discovery; classical arbitrage LVR partially collapses
   and is replaced by informed sniper flow. The paper's authors explicitly assume this regime
   away — meaning memecoin-pool LP economics are **genuinely untested, not debunked**.
   [UNVERIFIED extraction, direct quote from source]

Also structurally important: ~95%+ of *unhedged* LP PnL variance is raw directional exposure,
not the fees-vs-LVR bet. Any honest evaluation of our bot must decompose PnL into market beta
vs. fees-minus-adverse-selection, or the backtest is measuring noise.

---

## 1. Ranked catalog

### Tier 1 — Port these now (evidence exists, mechanism maps directly to CLMM)

#### 1.1 Asymmetric τ-reset bands with downside "protective buckets" ★ top pick
**Source:** arXiv:2505.15338 (Urusov et al., "Dynamic Liquidity Provision in Decentralized
Markets", v2 2026) — the only paper found doing strategy optimization in *exactly* our venue
mechanics. Status: [CONFIRMED 2-1] for its swap-log liquidity reconstruction method (~2% fee
error, i.e., you can backtest CLMM strategies without liquidity snapshots); headline strategy
results [UNVERIFIED — verifiers rate-limited].

- **Mechanism:** allocate liquidity to 2τ+1 buckets around price; rebalance only when price
  exits the band (event-driven, not timer-driven). The esoteric twist: add η_down *empty*
  buckets on the downside only, so sharp dumps do **not** trigger relocation — you stop
  crystallizing IL at the bottom of every wick.
- **Reported numbers (USDC/ETH 0.05%, Sep 2024 out-of-time):** τ=5, η_down=20 → **+88.9%
  annualized vs +51.9% buy-and-hold, with lower max drawdown (11.8% vs 16.2%)** — versus
  **−92.0%** for the same τ=5 *without* the asymmetric modification.
- **The negative result matters more than the positive:** τ=0–2 (ultra-tight, always-rebalance)
  produced **≈ −100% compound annual returns** (6,646 reallocations); τ=40 with *one*
  reallocation returned +181.9%. Over-rebalancing under tight concentration is the single
  fastest documented way to vaporize LP capital — even when placement is "optimal."
- **Why dismissed:** everyone chases fee APR; the paper shows fee-maximization is the wrong
  objective past a "profitability frontier" where IL + downside moves + costs erode principal
  faster than fees accrue.
- **$10 feasibility: high.** Purely rules-based, event-driven, needs only pool state. Caveats:
  results are gross of gas (Solana ≈ negligible vs Ethereum) and from one month/one pool —
  fragile, but directionally consistent with the Gauntlet ALM autopsy (rebalance costs +
  adverse selection are what killed production auto-rebalancers).

#### 1.2 Time-average (ergodicity/Kelly) sizing — with the hype stripped off
**Sources:** Peters, "Optimal leverage from non-ergodicity" (arXiv:0902.2965, published in
Quantitative Finance). Core claims [CONFIRMED 3-0 ×2]. Adversarial layer: Dickens essay +
arXiv:2306.03275 ("'Ergodicity Economics' is Pseudoscience"); the claim that ergodicity theory
*grounds* Kelly beyond log-utility was [REFUTED 0-3] as overreach.

- **What survives verification:** (a) ensemble-average and time-average growth genuinely diverge
  for a single compounding bankroll — expectation-maximizing sizing is the wrong objective for
  one $10 account; (b) time-average growth has an **interior optimum in position fraction** —
  a falsifiable prediction: sweep the LP fraction in backtest and realized growth should peak,
  not increase monotonically.
- **What's refuted:** that this is *more* than Kelly. In practice it reduces to expected-log
  maximization; Samuelson (1971) already showed geometric-mean maximization isn't optimal at
  any finite horizon; cited risk-aversion work suggests most people should run 2–3x *below*
  full Kelly.
- **$10 feasibility: high, as a sizing module.** Implement fractional Kelly (~0.3–0.5×) on the
  fraction of bankroll deployed into the band, with the fraction estimated from the realized
  time-average growth sweep — not from expected returns. This is the mathematically honest
  version of Phoenix's compounding rule.

#### 1.3 Volatility harvesting (Shannon's demon) — viable only trend-gated
**Sources:** Witte, arXiv:1508.05241 [three core claims CONFIRMED 3-0]; the debunking side:
Cuthbertson et al. SSRN 2311240 (published IJFE 2016) [UNVERIFIED extraction but published,
peer-reviewed].

- **Confirmed mechanics:** rebalancing premium = **¼σ²(1−ρ)** in growth-rate terms; real but
  small (beat buy-and-hold in 30/36 G10 FX crosses); explicitly *not* arbitrage — it depends
  on continuity/stationarity, and its stated principal risk is a trending collapse. The paper
  itself concedes the premium is the same order as transaction costs in normal markets — it's
  only viable where **volatility is extreme relative to costs**. Memecoin pools on Solana are
  one of the few venues on earth that satisfy that condition (σ_daily 20–50%+, near-zero fees).
- **The debunk that survives:** absent mean reversion, the "premium" is mostly a
  diversification effect buy-and-hold also earns; **rebalancing is profitable in mean-reverting
  markets and loss-making in momentum markets.** Memecoins trend violently — a naive harvester
  gets run over.
- **A CLMM position mechanically *is* a continuous rebalancer** — so this literature is really
  about when LPing itself is +EV: high σ, low fees, *and* a chop/mean-reversion regime.
- **$10 feasibility: high, as a regime gate.** Don't harvest always; harvest when a
  trend/chop classifier says mean-reversion. In-trend → pull to the asymmetric/one-sided band.
  This is the theoretical justification for keeping Phoenix's momentum lean in the LP port.

### Tier 2 — The genuinely-untested risk-taking tier (theory solid, nobody has deployed on-chain)

#### 2.1 Hawkes self-excitation on the swap stream → rug/cascade early-warning
**Sources:** "Deep Hawkes Process for HF Market Making" (arXiv:2109.15110) [2 claims CONFIRMED
3-0]; neural-Hawkes LOB simulation (arXiv:2502.17417) [2 claims CONFIRMED 3-0, "first-ever"
priority claim REFUTED 0-3 as overstated].

- **What's confirmed:** Hawkes order-flow models are operational as MM strategy components (not
  just descriptive fits) and beat baseline MMs *in simulation*; simulation-only — no live
  deployment anywhere. Next-event prediction accuracy on real LOB data is modest (0.31–0.50),
  and simulators mis-produce Hurst exponents — so treat Hawkes as a **risk gauge, not an alpha
  signal**.
- **The port nobody has done:** LOB queue mechanics don't exist on AMMs, but *self-excitation
  of swap events does*. Fit a minimal exponential Hawkes on the pool's swap tape (sells on the
  pair) in a rolling window; the **branching ratio n ≈ (triggered events)/(all events)** is a
  live panic gauge. n → 1 means sell flow is feeding on itself: a liquidity cascade / rug in
  progress. Pull the position *before* the NATR spike that a candle-based circuit breaker
  waits for. An EM fit of (μ, α, β) on a few hundred timestamped swaps is trivially cheap.
- **Why dismissed:** Hawkes MM research is CLOB/HFT-centric; on-chain microstructure people
  don't read it, and quant-finance people don't watch memecoin pools. Nobody has published a
  Hawkes cascade detector for AMM rugs. **Genuinely untested, mechanism confirmed adjacent.**
- **$10 feasibility: high** (it risks compute, not capital — it's a defensive overlay that
  upgrades the Phoenix circuit breaker from reactive to anticipatory).

#### 2.2 Path-signature trading as the lean/positioning brain
**Source:** Futter, Horvath, Wiese, "Signature Trading" (arXiv:2308.15135) [UNVERIFIED —
verifiers rate-limited; claims extracted from primary PDF].

- **Mechanism:** represent the strategy as a linear functional on the truncated path signature
  of (time, price, volume); dynamic mean-variance optimum is **closed-form** — a linear solve
  against the empirical expected signature and its covariance. No training, no gradients.
  Drawdown control is claimed to be embedded (path-dependent variance). Order-3 signatures
  reproduce a nonlinear MACD strategy at ~90% R² — i.e., standard TA is a special case, and
  low truncation orders suffice.
- **Evidence honesty:** no live deployment, no transaction costs, illustrative ETF examples
  only. The authors themselves suggest volume-augmentation instead of time-augmentation at high
  frequency — which maps beautifully to swap-tape data where volume-time is the natural clock.
- **Why dismissed:** rough-path theory reads as impenetrable math-finance exotica; practitioners
  assume it needs deep-learning infrastructure (it doesn't — that's the closed-form point).
- **$10 feasibility: medium.** ~20 lines of numpy for order-3 signatures over a rolling swap
  window. Use it to *replace the EMA+RSI trend score* with a path-aware lean signal. Untested
  in fee-heavy venues — that's the experiment.

#### 2.3 Avellaneda-Stoikov → CLMM band placement
**Source:** Roceanu 2026 write-up (only explicit A-S→AMM mapping found) [CONFIRMED 3-0 that the
mapping is presented theory-only: zero backtests, zero deployments].

- **Mechanism:** A-S reservation price (mid shifted against inventory) → band *center* offset;
  A-S optimal spread (risk aversion × vol × fill-intensity) → band *width*. Fill-intensity
  decay with distance from mid carries over to AMMs [CONFIRMED]. Atomic on-chain execution
  removes stale-quote risk [CONFIRMED].
- **Status: genuinely untested — confirmed.** This is the cleanest "everyone cites it, nobody
  ported it" candidate: closed-form γ/κ formulas repurposed as band geometry.
- **$10 feasibility: high** — it's a formula swap inside the band-placement logic we already
  planned.

### Tier 3 — Deployed elsewhere, exotic here (needs adaptation, or more than $10)

#### 3.1 Stochastic Portfolio Theory / functionally generated portfolios
**Sources:** Fernholz & Karatzas survey [diversity-weighted strong-arbitrage theorem CONFIRMED
3-0 — but note: the theorem needs T ≥ (2/pεδ)log n, which for realistic parameters is *years*];
two broader claims (intrinsic-volatility⇒arbitrage; universal positivity of excess growth)
[REFUTED 0-3 — both overreach the source's actual conditions]; Campbell & Wong convex
implementation + CRSP backtests *with* transaction costs [CONFIRMED 3-0 ×2].

- SPT ran real money for a decade+ (INTECH) — this family is **deployed-at-scale in equities,
  never on-chain**. The honest port: a diversity-weighted basket across N meme pools
  (overweight small, underweight big) — rebalancing premium harvested at the *portfolio* level
  with no parameter estimation at all.
- **$10 feasibility: low today** (needs multiple positions; rent per position on Solana makes
  N>2 impractical at $10) — file under "when the bankroll compounds to $100."

### Tier 4 — Graded out by the evidence (debunked *for this setting*)

| Strategy | Verdict | Evidence |
|---|---|---|
| **LPPLS / Sornette bubble timing** | Marginal edge, fatal tails | Best public backtest: Sharpe 0.503 vs 0.4 buy-and-hold but **51.7% max drawdown**; authors concede critical-time short signals are noise-dominated; literature says confidence indicators fail on fast large fluctuations — which *is* the memecoin regime. A 50% drawdown on $10 ≈ ashes floor. |
| **Naive symmetric tight bands** | Actively destructive | τ=0–2 → ≈ −100% CAR (above); Gauntlet's production ALM autopsy: rebalance costs + adverse selection ate fee yield. |
| **Ergodicity economics as *more* than Kelly** | Refuted 0-3 | Reduces to expected-log; use fractional Kelly and drop the metaphysics. |
| **Deep-Hawkes LOB microstructure (queues, cancellations)** | No AMM analog | Confirmed the papers' results are conditional on queue priority/latency that CLMMs don't have. Only the excitation core ports (→ 2.1). |
| **Unhedged "volatility pumping" as standalone alpha** | Mostly misattribution | SSRN 2311240: premium ≈ diversification effect; loses in momentum regimes. Survives only trend-gated (→ 1.3). |
| **Blue-chip-pool LPing** | Structurally net-negative | Fees ≈ 80% of arb losses in the deepest pools. Long-tail or nothing. |

---

## 2. The archive directory (beyond arXiv)

**Forum graveyards & communities**
- **Nuclear Phynance** — dead since ~2020; live scraped mirror at `phynance1.rssing.com`
  (paginated `all_pN` channel pages let you walk the whole archive); also Wayback on
  `nuclearphynance.com`. The consensus "closest thing to a real practitioner forum" of its era.
- **Wilmott forums** — still up; the pre-2015 threads are the esoterica-dense stratum.
- **quant.stackexchange.com** — best for "has anyone actually tried X" negative results.
- **Quantocracy** (`quantocracy.com`) — blog aggregator, average quality, occasional gems;
  useful as an *index* of the living blogosphere.
- **QuantNet blog-directory thread** (`quantnet.com/threads/quant-blogs.16788/`) and **Patrick
  Burns' 2013 list** (`portfolioprobe.com/2013/10/22/quant-finance-blogs/`) — meta-directories
  of the defunct-blog era; feed the names into the Wayback Machine.
- **HN thread 22783653** — practitioners mapping where each dead community's people went.

**Defunct/esoteric blogs (Wayback targets)**
- `quantivity.wordpress.com` — regime detection, esoteric portfolio math.
- `epchan.blogspot.com` — E.P. Chan's blog (still live; the old posts are the good ones).
- `breakingthemarket.com` — geometric-return/ergodicity rebalancing obsessive; the "Great Age
  of Rebalancing" essays are the practitioner edge of the Shannon's-demon literature.
- `mdickens.me/2025/05/29/ergodicity/` — the careful *anti*-ergodicity essay; pair with
  arXiv:2306.03275 ("'Ergodicity Economics' is Pseudoscience") before believing anything from
  the LML orbit.
- The Whole Street (`thewholestreet.com`, defunct) — era aggregator, Wayback its feed for a
  census of what existed.

**DeFi-native research (the real literature for our venue)**
- **Paradigm**: the LVR paper's home; `paradigm.xyz/2024/11/pm-amm` shows LVR used as a design
  principle. Crawl the whole research tab.
- **a16z crypto**: LVR explainer series (why IL is the wrong metric).
- **Gauntlet**: `gauntlet.xyz/resources/uniswap-alm-analysis` — the autopsy of every deployed
  auto-rebalancing vault; required reading before building ours.
- **Atis E's Medium LVR series** (`atise.medium.com`) — the closest practitioner analog to our
  exact problem: band width vs. hedging cost vs. fee recapture, with simulations.
- **arXiv:2404.05803** (arb losses vs fees, empirically, per pool type) and **arXiv:2305.14604**
  (fee-paying arbitrageurs → LPs recapture much of LVR).
- **Flashbots forum/writings** — MEV/ordering-flow side of the same adverse-selection coin.

**Academic non-arXiv**
- **SSRN** — where the debunking papers live (e.g. 2311240, the volatility-pumping takedown).
  Search terms: "rebalancing premium", "diversification return", "volatility pumping".
- **RePEc/IDEAS, EconPapers** — mirrors much of SSRN with better search.
- **Santa Fe Institute working papers** — Farmer's market-ecology ABM lineage (not yet swept —
  next expedition).
- **OSF/HAL preprints** — untouched this sweep; HAL is where French microstructure school
  (Bouchaud orbit — Hawkes calibration, order-book fractality) posts outside arXiv.
- **viXra** — read only for amusement; nothing there survived even relevance filtering.

**GitHub graveyard (cautionary exhibits)**
- `joaquinbejar/CLMM-Liquidity-Provider` — Rust, targets Orca/Raydium/Meteora exactly;
  self-described "production-ready" but: alpha tag, 4 days of commit history, 11 stars, zero
  published backtests, no-liability disclaimer. The archetype of "theoretically grounded,
  deployment-unverified" — mine it for data plumbing, trust nothing else.

**Not yet swept** (the rate limit ate the second expedition): SFI ABM corpus, HAL/Bouchaud
order-book fractality, transfer-entropy lead-lag literature, Kleinberg burst detection ports,
permutation-entropy signals, quantum-inspired portfolio methods. Of these, transfer entropy
(SOL→memecoin lead-lag) and burst detection (Kleinberg's algorithm on swap arrival times — a
discrete cousin of the Hawkes detector) are the most promising unexplored leads.

---

## 3. Synthesis: what the $10 bot should actually become

The evidence converges on a design — **Phoenix LP** — that is *more* risk-taking than anything
published (three of its five mechanisms have never been deployed on-chain) while every
mechanism is individually grounded:

1. **Venue selection is the alpha**: long-tail, high-fee (≥0.25%, ideally Meteora dynamic-fee),
   no-CEX-listing pools — the regime where fees beat adverse selection and where classical LVR
   theory admits it doesn't apply. Filter: mint not ending in `pump`.
2. **Band geometry**: asymmetric τ-reset — liquidity above/at price, η_down empty protective
   buckets below; rebalance on band exit only, never on a timer, with a hard minimum interval
   (the −100% CAR result is an over-rebalancing death, not a placement death).
3. **Band center/width**: Avellaneda-Stoikov reservation-price and spread formulas, with
   inventory read from the position's token ratio (untested port — our experiment #1).
4. **Lean signal**: order-3 path signature over the volume-clocked swap tape (untested port —
   experiment #2), degrading gracefully to EMA+RSI if the window is thin.
5. **Panic layer**: Hawkes branching ratio on the sell-swap stream as a pre-emptive cascade
   detector in front of the NATR circuit breaker (untested port — experiment #3).
6. **Sizing**: fractional Kelly (≈0.4×) on live equity with the ashes floor — the verified core
   of ergodicity sizing, none of the refuted metaphysics.
7. **Evaluation**: decompose PnL into directional beta vs fees-minus-adverse-selection
   (the 95%-variance result makes raw PnL a useless success metric), using the confirmed
   swap-log liquidity-reconstruction method (~2% fee error) for backtesting.

**Honest bottom line**: the confirmed evidence says naive LPing loses, tight bands without
asymmetry lose catastrophically, and every "free lunch" in the esoterica has a documented
failure mode. What's *left* after adversarial verification is a narrow, genuinely unexplored
corridor — long-tail high-fee pools, downside-asymmetric event-driven bands, self-excitation
panic detection, sub-Kelly compounding — where the mainstream dismissal rests on assumptions
(deep CEX reference markets, Ethereum gas, symmetric bands, timer rebalancing) that simply
don't hold on Solana. That corridor is where the $10 goes.
