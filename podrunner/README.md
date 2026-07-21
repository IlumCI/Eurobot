# Valtgeist pod runtime

The process that runs **one user's pod**. It runs the real Phoenix LP controller
(`controllers/generic/phoenix_lp.py`) and streams telemetry to the control plane,
so the user's dashboard shows their pod live.

Two build modes share the *exact same controller and telemetry path* — only where
the orders go differs:

| Mode | Market data | Orders / funds | Use |
|------|-------------|----------------|-----|
| **paper** (this file) | real pool, live price (DexScreener) | simulated — no funds move | demos, dry-runs, staging |
| live (later) | real pool via Gateway | Gateway CLMM orders behind the user's vault | production, after the vault audit |

Paper mode is genuinely useful, not a toy: it drives the real controller against a
real pool's live price, so you see real warm-up, real band placement, and a real
cascade pull-out if the market rugs — with zero risk. The live build swaps the
paper executors for real ones and keeps everything else identical.

## Run it (paper demo)

```bash
# streams live telemetry to a real pod row
POD_ID=<uuid> \
POD_TOKEN=<per-pod runtime token> \
TELEMETRY_URL=https://<ref>.supabase.co/functions/v1/pod-telemetry \
TELEMETRY_APIKEY=<supabase publishable key> \
POOL_ADDRESS=4RX3HeVhvDT1N2Qnn9wMVtHfSGE3NqU3GYnuhaCoKDUD \
python3 run.py

# or watch it locally with no control plane:
DRY_RUN=1 POLL_SECONDS=2 python3 run.py
```

Defaults to a liquid BONK-USDC pool on Raydium. `POLL_SECONDS` also sets the demo
warm-up pace (quotes after ~12 samples); live-money pods use the production
5-minute warm-up.

## Security model

Auth to the control plane is a **per-pod token**: the pod holds the plaintext, the
DB stores only its SHA-256 hash (`pods.runtime_token_hash`), and the ingest
function compares in constant time. A compromised pod can only ever write *its own*
`pod_state` — never another pod's, never anything else. The token is never written
to a file or committed; the orchestrator injects it via the environment.

The runtime never holds a user private key. In the live build it holds only an
**operator** key that can rebalance within whitelisted CLMMs behind the vault —
withdrawal stays owner-only.

## Fleet + soak (multi-token)

`fleet.py` runs one Phoenix controller per token concurrently, sharing a swarm bus,
against live Jupiter prices with the realistic latency model. Tokens are chosen by a
tunable **selection filter** (the hypothesis) and the **soak report** is the judge.

```bash
# selection filter — "good" = graduated, liquid, high-turnover, moderate vol, seasoned
#   SEL_LIQ_MIN (80000)  SEL_TURN_MIN (3.0, 24h vol/liq)  SEL_VOL_MIN/MAX (3/40 % h1)
#   SEL_CHOP_MAX (35 % h6)  SEL_AGE_MIN_H (3)  SEL_H24_MIN (-60)
# candidates come from GeckoTerminal top/trending pools.

# run a soak: auto-select N tokens, log every cycle to CSV
DRY_RUN=1 FLEET_SIZE=8 POLL_SECONDS=5 RISK=balanced SOAK_CSV=soak.csv python3 -u fleet.py
#   ...let it run for hours, Ctrl+C to stop...

# judge it: net PnL per token + per selection bucket (turnover / vol / age)
python3 soak_report.py soak.csv
```

The bucket tables tell you which thresholds actually paid — tighten `SEL_*` toward the
winning buckets and re-soak. Override selection with `POOLS=addr1,addr2` to pin tokens.
