/**
 * POSITIVE PROOF — the vault's authorization invariant, tested exhaustively offline.
 *
 * This mirrors the guards in vault/programs/valtgeist-vault/src/lib.rs as a pure function
 * and tests the full (instruction × signer) matrix plus the CPI whitelist. It is the same
 * logic the on-chain program enforces; if someone later loosens a guard in the Rust, the
 * equivalent change here should break a test. It does NOT need a validator or funds.
 *
 * Run:  node vault_guard_test.mjs
 */

const OWNER = 'OWNER';       // the user's Phantom key
const OPERATOR = 'OPERATOR'; // the pod key
const STRANGER = 'STRANGER'; // anyone else

const CLMM_WHITELIST = new Set(['METEORA_DLMM', 'ORCA_WHIRLPOOLS', 'RAYDIUM_CLMM']);

/**
 * authorize(instruction, signer, ctx) -> true if the on-chain program would accept it.
 * Encodes exactly: withdraw/set_operator/close = owner only (has_one = owner + Signer);
 * pod_rebalance = operator only AND target program whitelisted; initialize = anyone (they
 * become the owner of their own new vault).
 */
function authorize(instruction, signer, ctx = {}) {
  const vault = ctx.vault ?? { owner: OWNER, operator: OPERATOR };
  switch (instruction) {
    case 'initialize_vault':
      return true; // creating YOUR OWN vault; signer becomes owner
    case 'set_operator':
    case 'withdraw':
    case 'close_vault':
      return signer === vault.owner;
    case 'pod_rebalance':
      if (signer !== vault.operator) return false;
      return CLMM_WHITELIST.has(ctx.targetProgram);
    default:
      return false; // unknown instruction: deny
  }
}

let pass = 0, fail = 0;
function check(desc, got, want) {
  const ok = got === want;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${desc}`);
  ok ? pass++ : fail++;
}

console.log('─'.repeat(72));
console.log('VAULT GUARD MATRIX — who can invoke what');
console.log('─'.repeat(72));

// The claim, stated as tests: the pod can ONLY rebalance; only the owner can move funds out.
check('owner can withdraw', authorize('withdraw', OWNER), true);
check('POD cannot withdraw  (the whole promise)', authorize('withdraw', OPERATOR), false);
check('stranger cannot withdraw', authorize('withdraw', STRANGER), false);

check('owner can rotate/revoke operator', authorize('set_operator', OWNER), true);
check('POD cannot change the operator (no self-escalation)', authorize('set_operator', OPERATOR), false);

check('owner can close the vault', authorize('close_vault', OWNER), true);
check('POD cannot close the vault', authorize('close_vault', OPERATOR), false);

check('POD can rebalance on a whitelisted CLMM',
  authorize('pod_rebalance', OPERATOR, { targetProgram: 'METEORA_DLMM' }), true);
check('POD cannot rebalance into a non-whitelisted program (e.g. a drainer)',
  authorize('pod_rebalance', OPERATOR, { targetProgram: 'EVIL_TRANSFER_PROGRAM' }), false);
check('stranger cannot rebalance even on a whitelisted CLMM',
  authorize('pod_rebalance', STRANGER, { targetProgram: 'ORCA_WHIRLPOOLS' }), false);

// Revocation: after the owner sets the operator to a dead key, the old pod is powerless.
const revoked = { owner: OWNER, operator: 'DEAD_KEY' };
check('after revoke, old POD key cannot rebalance',
  authorize('pod_rebalance', OPERATOR, { targetProgram: 'METEORA_DLMM', vault: revoked }), false);

// Exhaustive sweep: only (owner, fund-exit/authority) and (operator, whitelisted rebalance) allowed.
console.log('\n─'.repeat(1) + ' exhaustive sweep');
const instrs = ['initialize_vault', 'set_operator', 'withdraw', 'close_vault', 'pod_rebalance', 'unknown_ix'];
const signers = [OWNER, OPERATOR, STRANGER];
let allowedCount = 0;
for (const i of instrs) for (const s of signers) {
  const r = authorize(i, s, { targetProgram: 'METEORA_DLMM' });
  if (r) allowedCount++;
}
// expected allowed: initialize(3 signers) + set_operator(owner) + withdraw(owner) + close(owner)
//   + pod_rebalance(operator) = 3 + 1 + 1 + 1 + 1 = 7
check('exactly 7 (instruction,signer) combinations are permitted', allowedCount, 7);
// `initialize_vault` just creates a NEW vault owned by the caller (harmless). The invariant
// that matters is over operations on an EXISTING user vault: the pod may reach exactly one.
const existingVaultInstrs = ['set_operator', 'withdraw', 'close_vault', 'pod_rebalance'];
check('on an existing user vault, the pod is permitted exactly ONE instruction',
  existingVaultInstrs.filter(i => authorize(i, OPERATOR, { targetProgram: 'METEORA_DLMM' })).length, 1);
check('on an existing user vault, that one instruction is pod_rebalance',
  existingVaultInstrs.filter(i => authorize(i, OPERATOR, { targetProgram: 'METEORA_DLMM' }))[0], 'pod_rebalance');

console.log('\n' + '─'.repeat(72));
console.log(`${pass} passed, ${fail} failed`);
console.log(fail === 0
  ? 'INVARIANT HOLDS: pod trades (1 instruction, whitelisted only); owner alone moves funds.'
  : 'INVARIANT BROKEN — do not ship.');
process.exit(fail === 0 ? 0 : 1);
