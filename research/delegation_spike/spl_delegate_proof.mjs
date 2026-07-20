/**
 * NEGATIVE PROOF — why the naive "connect Phantom, approve the pod, it can't withdraw"
 * design is FALSE on Solana.
 *
 * SPL token `approve` grants a delegate authority to transfer up to N tokens. The
 * on-chain Token program accepts a Transfer whose *authority* is the delegate and whose
 * *destination is arbitrary* — the account owner does not sign. So a delegate can move a
 * user's tokens to an attacker address. Delegation == withdraw-capable. Full stop.
 *
 * This script proves it two ways:
 *   (1) OFFLINE (always runs): build the exact instructions with the canonical
 *       @solana/spl-token library and inspect signers/destination.
 *   (2) ON-CHAIN (runs if SPIKE_DEVNET_KEY is set to a funded devnet secret key, JSON
 *       array or base58): actually land a delegated transfer to a fresh "attacker"
 *       address and confirm the balance moved without the owner signing.
 *
 * Run:  node spl_delegate_proof.mjs
 *       SPIKE_DEVNET_KEY='[12,34,...]' node spl_delegate_proof.mjs   # to land it live
 */
import {
  Connection, Keypair, PublicKey, Transaction, sendAndConfirmTransaction, clusterApiUrl,
} from '@solana/web3.js';
import {
  TOKEN_PROGRAM_ID, createApproveInstruction, createTransferInstruction,
  createMint, getOrCreateAssociatedTokenAccount, mintTo, getAccount, approve, transfer,
} from '@solana/spl-token';

const line = '─'.repeat(72);

function offlineProof() {
  console.log(line);
  console.log('OFFLINE PROOF — the instruction a delegate signs to drain a wallet');
  console.log(line);

  const owner = Keypair.generate().publicKey;      // the user's Phantom pubkey
  const delegate = Keypair.generate().publicKey;   // the pod's "trade-only" key (the claim)
  const attacker = Keypair.generate().publicKey;   // any address the pod chooses
  const ownerAta = Keypair.generate().publicKey;   // user's token account
  const attackerAta = Keypair.generate().publicKey;

  // Step 1: the user approves the delegate for some amount (the "let it trade" step)
  const approveIx = createApproveInstruction(ownerAta, delegate, owner, 1_000_000n);

  // Step 2: the delegate ALONE signs a transfer of those tokens to the attacker.
  // Note the authority passed is the DELEGATE, not the owner.
  const drainIx = createTransferInstruction(ownerAta, attackerAta, delegate, 1_000_000n);

  const signers = drainIx.keys.filter(k => k.isSigner).map(k => k.pubkey.toBase58());
  const ownerSigns = signers.includes(owner.toBase58());
  const delegateSigns = signers.includes(delegate.toBase58());
  const dest = drainIx.keys[1].pubkey.toBase58();

  console.log('approve ix authority (who must sign approve):',
    approveIx.keys.find(k => k.isSigner)?.pubkey.toBase58(), '= the owner (one time)');
  console.log('drain  ix signer(s):', signers);
  console.log('drain  ix destination:', dest, '= attacker, fully arbitrary');
  console.log();
  console.log('owner signs the drain? ', ownerSigns, '  <-- FALSE: owner is not involved');
  console.log('delegate signs alone?  ', delegateSigns, '  <-- TRUE: the pod key is enough');
  console.log();
  console.log('VERDICT: an SPL "trade" delegate can transfer to any address. Delegation is');
  console.log('withdraw-capable. The naive pitch cannot be built on token approval.');

  if (ownerSigns || !delegateSigns) throw new Error('offline proof invariant broke');
}

async function onchainProof(secret) {
  console.log('\n' + line);
  console.log('ON-CHAIN PROOF (devnet) — landing the drain for real');
  console.log(line);
  const c = new Connection(clusterApiUrl('devnet'), 'confirmed');
  const payer = Keypair.fromSecretKey(secret);
  const delegate = Keypair.generate();
  const attacker = Keypair.generate();
  console.log('payer/owner:', payer.publicKey.toBase58());

  const mint = await createMint(c, payer, payer.publicKey, null, 6);
  const ownerAta = await getOrCreateAssociatedTokenAccount(c, payer, mint, payer.publicKey);
  const attackerAta = await getOrCreateAssociatedTokenAccount(c, payer, mint, attacker.publicKey);
  await mintTo(c, payer, mint, ownerAta.address, payer, 1_000_000n);

  // owner approves the "trade-only" delegate
  await approve(c, payer, ownerAta.address, delegate.publicKey, payer, 1_000_000n);
  console.log('approved delegate for 1.0 token');

  // fund the delegate with a little SOL for fees, then it drains — owner never signs
  const airdrop = await c.requestAirdrop(delegate.publicKey, 1e7);
  await c.confirmTransaction(airdrop, 'confirmed');

  await transfer(c, delegate, ownerAta.address, attackerAta.address, delegate, 1_000_000n);

  const drained = (await getAccount(c, attackerAta.address)).amount;
  console.log('attacker balance after delegate-signed transfer:', drained.toString());
  console.log(drained === 1_000_000n
    ? 'CONFIRMED: the delegate moved the owner\'s tokens with no owner signature.'
    : 'unexpected balance');
}

async function main() {
  offlineProof();
  const key = process.env.SPIKE_DEVNET_KEY;
  if (!key) {
    console.log('\n(set SPIKE_DEVNET_KEY to a funded devnet secret key to also land it live)');
    return;
  }
  const secret = key.trim().startsWith('[')
    ? Uint8Array.from(JSON.parse(key))
    : (await import('bs58')).default.decode(key);
  await onchainProof(secret);
}

main().catch(e => { console.error(e); process.exit(1); });
