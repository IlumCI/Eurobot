# Eurobot — Phoenix

**Experimental micro-bankroll market making, built to test in production first.**

Eurobot is a standalone algorithmic trading codebase by [Euroswarms](mailto:Europa@Euroswarms.eu),
born as a fork of [Hummingbot](https://github.com/hummingbot/hummingbot) and since cut loose:
~300 KLoC of exchange connectors, legacy strategies, and their tests were removed, leaving a lean
core (~80 KLoC) shaped around one question — *can theoretically-grounded but never-deployed trading
mechanisms survive contact with a real market, starting from a bankroll of ten dollars?*

The flagship is **Phoenix**, a family of two market-making bots that implement mechanisms from the
"everyone says it can't work, nobody ever risked proving it" tier of quantitative finance:

| Bot | Venue | File |
|---|---|---|
| **Phoenix LP** | Solana CLMM pools (Meteora / Orca / Raydium, via Gateway) | `controllers/generic/phoenix_lp.py` |
| **PMM Phoenix** | Binance perpetuals (order-book market making) | `controllers/market_making/pmm_phoenix.py` |

Both are designed around the failure modes that kill small accounts: fees, adverse selection,
volatility cascades, and over-trading. Neither pretends a $10 bankroll is anything but an
experiment — the point is that the experiment is *survivable, instrumented, and honest*.

---

## The theory, and how each piece is implemented

Every mechanism below was researched and adversarially verified against primary sources before a
line of it was written — the full ranked catalog, with citations, verification votes, and the
archive directory it was mined from, lives in [`research/esoteric_strategies.md`](research/esoteric_strategies.md).

### 1. Loss-versus-rebalancing as the null hypothesis (venue selection is the alpha)
An AMM liquidity provider bleeds exactly **σ²/8** of position value per unit time to arbitrageurs
(Milionis et al.). Most LP strategies die here before their signal ever matters. Phoenix LP treats
this as a *venue filter*, not a fate: it refuses pools below a minimum fee tier
(`min_pool_fee_pct`), refuses pump.fun-launched tokens by checking the on-chain mint suffix
(`banned_ca_suffixes`), and displays a live LVR gauge (realized σ² / 8 vs. pool fee) so the
operator can see the breakeven race in `status`. The empirical record says long-tail, high-fee
pools are where fees beat the bleed — so that is the only regime the bot consents to trade in.

### 2. Asymmetric τ-reset bands (the anti-impermanent-loss geometry)
The published evidence on concentrated liquidity is brutal: tight always-rebalancing bands
produced ≈ −100% annualized in backtests, while the same strategy with *asymmetric downside
protection* beat buy-and-hold with lower drawdown. Phoenix LP implements this directly in the
position's auto-close limits: a tight trip **above** the band (`upper_rebalance_pct`, re-anchor
fast when price exits upward) and a wide protective zone **below** (`protective_zone_pct`) so that
dumps do *not* trigger relocation — impermanent loss is never crystallized at the bottom of a
wick. A hard `min_rebalance_interval` enforces the other half of the finding: over-rebalancing,
not bad placement, is the documented LP killer.

### 3. Avellaneda–Stoikov, ported to band geometry (previously theory-only)
The classic market-making optimum — reservation price shifted against inventory, spread set by
risk aversion × volatility × fill intensity — had been mapped to AMMs only on paper, never
deployed. Phoenix LP uses the A-S reservation price as the **band center** (inventory read from
the token ratio it actually holds) and the A-S optimal spread as the **band width**
(`as_gamma`, `as_kappa`, floored and capped). The bot quotes one side at a time — a bid band of
quote below price, an ask band of base above — flipping with hysteresis as fills convert
inventory, which is bid/ask market making expressed in liquidity bins.

### 4. Hawkes-process cascade detection (the rug alarm)
Sell flow on a dying token is *self-exciting*: each dump triggers more. Phoenix LP fits an
exponential-kernel Hawkes process by expectation-maximization on down-move events from the
sampled pool price stream, with two twists found the hard way in simulation: **marked events**
(a move worth k× the detection threshold counts k times — otherwise a violent cascade saturates
into a regular event stream that the EM correctly, and uselessly, attributes to background rate)
and a **composite score** (the EM branching ratio reads clustering pattern and blocks re-entry
after a storm; a burst-intensity term against a robust-MAD adaptive baseline catches the storm
*live*). Above the panic threshold the bot pulls its position, and — via **panic flatten** —
market-sells any held base through the swap provider: it exits the *token*, not just the
position. In simulated rugs across five seeds it perched at −3.5% to −9.9% of dumps that
finished at −37% to −48%.

### 5. Ergodicity economics, minus the metaphysics (sizing)
The one result from the ergodicity literature that survives adversarial scrutiny: a single
compounding bankroll must maximize time-average growth, which has an interior optimum in position
size — and full Kelly is far too aggressive for fat-tailed venues. Phoenix deploys a
**sub-Kelly fraction of live equity** (`kelly_fraction`, default 0.4×), where equity is the
bankroll plus realized PnL from settled positions, capped at `max_compound_factor`. Wins compound
the next quote; the refuted parts of the theory were left in the literature.

### 6. The ashes floor (ruin is absorbing)
If equity breaches `ashes_floor_pct` of the starting bankroll the bot halts *permanently* —
state `ASHES` — and flattens what remains. A second, independent kill switch
(`max_global_drawdown_quote` in the runner config) sits underneath it. Volatility harvesting
mathematics only works if you are still alive to rebalance.

### How this differs from the usual bot

| The usual | Phoenix |
|---|---|
| Symmetric spreads / bands around mid | Asymmetric: protective below, aggressive above, trend-leaned center |
| Rebalance on a timer | Event-driven only, with a hard minimum interval |
| Volatility circuit breaker (reactive) | Self-excitation detector (anticipatory) + volatility fallback |
| Position sizing = fixed amount | Sub-Kelly fraction of live equity, compounding, capped |
| "Stop loss" closes the position | Panic flatten exits the token entirely |
| Backtest-first, prod maybe | Test-in-prod-first: sim harness with latency/staleness models, prod is the test generator |
| Any pool with volume | Venue guards: fee-tier floor, pump.fun mint veto, live LVR gauge |

---

## How to use it

### Prerequisites
- Docker (or a source install: `./install`, then `conda activate hummingbot && ./compile`)
- For Phoenix LP: a running [Gateway](https://github.com/hummingbot/gateway) instance with a
  Solana wallet holding your quote stable (USDC) **plus ~0.08 SOL** for transaction fees and
  refundable position rent
- For PMM Phoenix: Binance API keys connected via `connect binance_perpetual`

### Phoenix LP (Solana CLMM)
1. Pick a pool per the research: long-tail token, fee tier ≥ 0.25% (Meteora dynamic-fee pools
   are ideal), real volume, and *not* a pump.fun mint — the bot re-checks the mint on-chain and
   vetoes itself if you get this wrong.
2. Edit `conf/controllers/phoenix_lp.yml`: set `trading_pair` and `pool_address`
   (every other parameter ships with researched defaults for a $10 bankroll).
3. Start the client and run:
   ```
   start --script v2_with_controllers.py --conf v2_phoenix_lp.yml
   ```
4. Watch `status`: it shows the state machine (`HATCHING → FLYING`, `PERCHED` on cascade panic,
   `ASHES`/`VETOED` as terminal states), live equity, trend score, Hawkes cascade score, and the
   LVR breakeven gauge.

### PMM Phoenix (Binance perpetuals)
```
start --script v2_with_controllers.py --conf v2_phoenix.yml
```
Preset in `conf/controllers/phoenix_micro.yml`: $10, DOGE-USDT, 5× leverage, fee-floor spreads,
momentum lean, volatility circuit breaker, compounding with the same ashes floor.

### Run the simulation suite (no exchange, no funds, no install)
The whole-controller integration harness — mocked framework, simulated pool, RPC latency, stale
data, slow executor lifecycle, and chop/trend/rug regimes — runs on bare Python:
```
python research/phoenix_lp_sim.py
```
Six scenario suites assert warm-up, band geometry, side-flipping, rug defense, venue vetoes,
ashes permanence, latency tolerance, and compounding. This harness found four real bugs before
any money did; extend it before you extend the bot.

---

## What this fork keeps from Hummingbot

Eurobot stands on Hummingbot's shoulders and keeps its load-bearing layers intact:

- **Strategy V2 framework** (`hummingbot/strategy_v2/`) — the controller/executor architecture:
  Phoenix controllers emit executor actions; `lp_executor`, `order_executor`, and
  `position_executor` handle order lifecycle, retries, and accounting.
- **Gateway client** (`hummingbot/connector/gateway/`, `hummingbot/core/gateway/`) — the entire
  Solana path: pool info, add/remove liquidity, swaps on Meteora/Orca/Raydium via Jupiter.
- **Binance spot + perpetual connectors** and their candle feeds — for the CEX variant
  (plus the Hyperliquid perpetual connector, reserved for future perp-hedged LP work).
- **The client CLI** (`hummingbot/client/`) — config system, `start`/`status` commands.
- **Core plumbing** (`hummingbot/core/`) — clock, events, order books, a trimmed rate oracle.
- **SQLite persistence** (`hummingbot/model/`) — every executor and fill is recorded, which is
  the forensic record the test-in-prod ethos depends on.

Removed relative to upstream: 43 exchange/derivative connectors, the entire V1 strategy engine,
25 candle feeds, ~180 KLoC of tests for deleted code, and all demo strategies and scripts. What
remains is documented above; what was cut is one `git log` away.

Hummingbot is licensed under Apache 2.0; this fork retains that license and its attribution
(see `LICENSE`). If you want a general-purpose trading framework with 140+ venue connectors and
a large community, use [upstream Hummingbot](https://hummingbot.org) — it is excellent. Use
Eurobot if you want a small, sharp, experimental codebase with opinions.

## Repository map

```
controllers/
  generic/phoenix_lp.py          # Phoenix LP: the Solana CLMM bot (flagship)
  market_making/pmm_phoenix.py   # PMM Phoenix: the Binance perpetuals variant
  generic/lp_rebalancer/         # reference CLMM controller (upstream)
  generic/{stat_arb,xemm_multiple_levels,arbitrage_controller}.py   # kept as references
conf/controllers/                # deployable presets (phoenix_lp.yml, phoenix_micro.yml)
conf/scripts/                    # runner configs (v2_phoenix_lp.yml, v2_phoenix.yml)
scripts/v2_with_controllers.py   # the only runner script
research/
  esoteric_strategies.md         # the verified strategy catalog + source archive directory
  phoenix_lp_sim.py              # whole-controller integration simulation
hummingbot/                      # the retained framework core (see above)
test/hummingbot/strategy_v2/     # regression tests for the layer Phoenix stands on
```

## Disclaimer

This is experimental software for experimental capital. It trades volatile on-chain assets with
mechanisms that are, by deliberate design, largely untested in production — that is the research
program, not an oversight. Nothing here is financial advice. Do not fund it with money whose loss
would matter: the reference deployment is **ten dollars**, and every defense in the codebase
(vetoes, floors, flatten, kill switches) exists because ten dollars can still be lost.
