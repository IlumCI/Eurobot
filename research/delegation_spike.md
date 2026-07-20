# Delegation Spike — can a pod trade a user's funds but never withdraw them?

**Question the whole business rests on.** The site promises "a delegation that can trade but
never withdraw." This spike establishes whether that is true on Solana, by what mechanism, and
what the marketing copy must say to stay honest. Primary-source research + two runnable proofs.

## TL;DR

- **The naive version is false.** Neither Phantom "connect" nor SPL token `approve` gives a
  bot trade-authority-without-withdraw. A token delegate can transfer to any address (proven
  live-constructible in `delegation_spike/spl_delegate_proof.mjs`), and Phantom's autonomous
  signing (auto-confirm) is trusted-apps-only and time-boxed to 2 hours — useless for a 24/7 pod.
- **The property is real and has a Solana precedent.** Drift Protocol's *delegated accounts*
  let a delegate "deposit, swap, place/cancel orders" but **not withdraw — withdrawals are
  owner-only.** So trade-not-withdraw is an audited, live pattern on Solana.
- **Our venue doesn't provide it.** Meteora/Orca/Raydium CLMM positions can only be managed by
  the owner's signature; there is no operator role. So on CLMMs we must interpose our own
  **user-owned vault program** to get what Drift gets for free.
- **The vault makes the claim true by construction**, and its access-control invariant is
  encoded and tested (`delegation_spike/vault_guard_test.mjs`, 14/14) against the reference
  program (`vault/programs/valtgeist-vault/src/lib.rs`).
- **Copy correction required:** funds do *not* "stay in your Phantom wallet" — they move to a
  vault only the user can withdraw from. Non-custodial in the meaningful sense, but the current
  wording is literally inaccurate and must change before launch (see §5).

## 1. What the wallet layer can and cannot do

| Mechanism | Gives the pod trade authority? | Can the pod withdraw? | Fit for an autonomous pod |
|---|---|---|---|
| Phantom `connect` | No — authorizes reading the pubkey only; every tx still needs a signature | n/a | No |
| Phantom auto-confirm | Only with a human present | — | No — trusted-apps allowlist (Magic Eden, Jupiter…), ~10 tx/min, **2-hour** window, wallet must be unlocked |
| SPL token `approve` (delegate) | Yes | **Yes — to any address, up to the cap** | No — withdraw-capable, i.e. the pod could rug the user |
| Holding the user's private key | Yes | Yes | No — that is literal custody; the thing we must never do |

The first finding that reshapes everything: **a bot cannot autonomously act "from the user's
Phantom wallet."** Autonomy without custody forces the funds into a *program-controlled account*
the user owns. They leave the Phantom wallet — the honest question is only *what they move into*.

## 2. Three ways to get trade-not-withdraw, ranked by build cost

1. **Protocol-native (Drift model).** If Vältgeist ever runs a perps/Drift strategy, Drift's
   delegated accounts deliver trade-not-withdraw with zero custom code. Not applicable to CLMM
   LP, but it is the proof that the property is real and the design target to match.
2. **Squads v4 smart account.** Formally verified, ~$10B secured; strong at withdraw-gating,
   destination-scoped spending limits, roles, and owner revocation. **Open question the spike
   could not close from docs alone:** whether a member can *autonomously* (no per-tx multisig
   approval) execute program-scoped CPIs like "add liquidity on Meteora." Spending limits look
   transfer/destination-oriented, not "call this DEX unattended." Needs a hands-on devnet eval
   before betting the MVP on it. If it works, it beats a custom program (less code to audit).
3. **Custom user-owned vault program (the documented MVP path).** A PDA vault the user solely
   owns; the pod is named as `operator` and can reach exactly one instruction, `pod_rebalance`,
   which may only CPI into whitelisted CLMM programs. `withdraw` / `set_operator` (revoke) /
   `close_vault` are owner-only. This makes the promise literally true and is fully within our
   control to build and audit. Reference implementation: `vault/programs/valtgeist-vault/`.

## 3. The proofs in this spike

**Negative — `delegation_spike/spl_delegate_proof.mjs`** (runs offline; lands live if you set
`SPIKE_DEVNET_KEY` to a funded devnet key). Builds the exact SPL instructions with the canonical
`@solana/spl-token` library and shows: the *delegate alone* signs a transfer whose *destination
is an attacker address*; the owner never signs. Output confirms `owner signs the drain? false /
delegate signs alone? true`. This is why token approval cannot back the pitch.

**Positive — `delegation_spike/vault_guard_test.mjs`** (runs offline). Encodes the vault's
authorization matrix — the same guards as the Rust program — and tests it exhaustively (14/14):
the pod can reach exactly one instruction (`pod_rebalance`, whitelisted CLMMs only); only the
owner can withdraw, rotate the operator, or close; a revoked pod key is powerless.

The vault's one auditable invariant, stated for whoever reviews the Rust: *no operator-authorized
code path moves tokens to a destination the vault does not own.* Keep that true and the pod can
never remove value, only rearrange it inside positions the user can pull back at will.

## 4. Devnet note

Devnet's public faucet returned 429 (rate-limited) during this spike, so nothing was landed
on-chain from here. Both proofs run offline and are correct on the instruction/logic level; the
negative proof will also land live given a funded key, and the vault program needs the Anchor
toolchain (absent here) plus an audit before any real deployment. Deploying and driving the
vault against a Meteora devnet pool is the next spike.

## 5. Consequence for the live site (action required before launch)

The mechanism is sound but the current copy overstates it. Fixes:

| Location | Now (inaccurate) | Should say |
|---|---|---|
| Hero sub | "…from your own wallet, under a delegation that can never withdraw." | "…from a vault only you can withdraw from, under a delegation that can trade but never move funds out." |
| FAQ "Can Vältgeist withdraw my funds?" | "…trades from your own wallet, and never touches ours." | "Your funds sit in a vault program that only you can withdraw from. The pod is authorized for one thing — rebalancing your liquidity — and is structurally unable to transfer funds anywhere. Revoke it any time." |
| "money never leaves your wallet" framing anywhere | literal, false | "your funds never leave your control" / "only you can withdraw" |

This is non-custodial in the sense that matters — the user is the sole withdraw authority and can
exit instantly — but "stays in your Phantom wallet" is not true and a diligent user (or regulator)
will catch it. Correct it before driving traffic.

## Sources
- Phantom auto-confirm: help.phantom.com/hc/en-us/articles/19385761078547 ; docs.phantom.com/phantom-portal/contracts
- SPL approve/delegate semantics: solana.com/docs/tokens/basics/approve-delegate
- Drift delegated accounts: docs.drift.trade/getting-started/delegated-accounts
- Squads v4: github.com/Squads-Protocol/v4 ; docs.squads.so
- Session keys (require target-program integration; don't retrofit onto third-party DEXs):
  docs.magicblock.gg/pages/tools/session-keys/introduction ; github.com/magicblock-labs/session-keys
- Meteora DLMM (owner-signed position ops, no operator role): docs.meteora.ag/developer-guide/guides/dlmm/overview
