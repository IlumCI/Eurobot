//! Vältgeist Vault — non-custodial "trade, never withdraw" delegation for Solana CLMMs.
//!
//! WHY THIS EXISTS
//! Meteora/Orca/Raydium CLMM positions can only be managed by their owner's signature,
//! and SPL token `approve` delegation is withdraw-capable (a delegate can transfer to any
//! address — see research/delegation_spike/spl_delegate_proof.mjs). So there is no native
//! way to let a bot rebalance a user's liquidity without also letting it steal the funds.
//! This program interposes a user-owned vault that fixes that.
//!
//! THE INVARIANT (what an auditor must verify):
//!   The `operator` (pod) key can successfully sign exactly ONE instruction, `pod_rebalance`,
//!   and no operator-authorized code path moves tokens to a destination the vault does not
//!   own. Every fund-exit path (`withdraw`, `close_vault`) and every authority change
//!   (`set_operator`) requires the `owner` (the user's Phantom key) to sign. Therefore the
//!   pod can trade the vault's liquidity but can never remove value from it, and the user
//!   can withdraw or revoke at any instant.
//!
//! STATUS: reference design for the delegation spike. NOT deployed, NOT audited. Requires
//! the Anchor toolchain to build and a security audit before touching real funds. The CLMM
//! CPI bodies are sketched — each venue's exact accounts/instruction data go where marked.

use anchor_lang::prelude::*;
use anchor_spl::token::{self, Token, TokenAccount, Transfer};

declare_id!("Vau1tvgSTgeist1111111111111111111111111111");

/// CLMM programs the operator is allowed to reach by CPI. LP add/remove/collect on these
/// return value to the position owner (this vault) — they cannot pay out to a third party.
/// (Fill with the exact mainnet program IDs before deploy.)
mod allowed {
    use anchor_lang::prelude::Pubkey;
    // Meteora DLMM, Orca Whirlpools, Raydium CLMM — placeholders, replace with real IDs.
    pub const CLMM_PROGRAMS: [&str; 3] = [
        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", // meteora dlmm (verify)
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc", // orca whirlpools (verify)
        "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK", // raydium clmm (verify)
    ];
}

#[program]
pub mod valtgeist_vault {
    use super::*;

    /// The user creates their own vault and names the pod operator key. Signer = owner.
    pub fn initialize_vault(ctx: Context<InitializeVault>, operator: Pubkey) -> Result<()> {
        let v = &mut ctx.accounts.vault;
        v.owner = ctx.accounts.owner.key();
        v.operator = operator;
        v.bump = ctx.bumps.vault;
        emit!(VaultEvent { vault: v.key(), kind: 0, actor: v.owner });
        Ok(())
    }

    /// Rotate or revoke the operator. Owner only. Passing the system program / default key
    /// as `new_operator` effectively disables the pod until re-set.
    pub fn set_operator(ctx: Context<OwnerOnly>, new_operator: Pubkey) -> Result<()> {
        ctx.accounts.vault.operator = new_operator;
        emit!(VaultEvent { vault: ctx.accounts.vault.key(), kind: 1, actor: ctx.accounts.owner.key() });
        Ok(())
    }

    /// The pod rebalances the vault's liquidity. Signer MUST be the operator. The only
    /// programs it may CPI into are the whitelisted CLMMs, whose LP instructions return
    /// funds to the vault. There is no branch here that transfers to an arbitrary address.
    pub fn pod_rebalance(ctx: Context<PodRebalance>, target_program: Pubkey /* , clmm args */) -> Result<()> {
        let v = &ctx.accounts.vault;
        require_keys_eq!(ctx.accounts.operator.key(), v.operator, VaultError::NotOperator);
        require!(is_allowed_program(&target_program), VaultError::ProgramNotWhitelisted);
        // --- CLMM CPI goes here, signed by the vault PDA seeds ---
        // The vault PDA is the position owner; add/remove/collect land back in vault-owned
        // token accounts. NEVER build a Transfer/CloseAccount to a non-vault destination in
        // this instruction — that is the single line that would break the invariant.
        emit!(VaultEvent { vault: v.key(), kind: 2, actor: ctx.accounts.operator.key() });
        Ok(())
    }

    /// Withdraw funds from the vault back to the owner. Owner only. This is the ONLY path
    /// that moves tokens out of the vault, and the pod can never reach it.
    pub fn withdraw(ctx: Context<Withdraw>, amount: u64) -> Result<()> {
        let v = &ctx.accounts.vault;
        let seeds: &[&[u8]] = &[b"vault", v.owner.as_ref(), &[v.bump]];
        token::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.vault_token_account.to_account_info(),
                    to: ctx.accounts.owner_token_account.to_account_info(),
                    authority: ctx.accounts.vault.to_account_info(),
                },
                &[seeds],
            ),
            amount,
        )?;
        emit!(VaultEvent { vault: v.key(), kind: 3, actor: ctx.accounts.owner.key() });
        Ok(())
    }
}

fn is_allowed_program(p: &Pubkey) -> bool {
    allowed::CLMM_PROGRAMS.iter().any(|s| s.parse::<Pubkey>().map(|k| &k == p).unwrap_or(false))
}

#[account]
pub struct Vault {
    pub owner: Pubkey,     // the user's Phantom key — sole withdraw authority
    pub operator: Pubkey,  // the pod's key — trade authority only
    pub bump: u8,
}

#[derive(Accounts)]
pub struct InitializeVault<'info> {
    #[account(init, payer = owner, space = 8 + 32 + 32 + 1,
              seeds = [b"vault", owner.key().as_ref()], bump)]
    pub vault: Account<'info, Vault>,
    #[account(mut)]
    pub owner: Signer<'info>,
    pub system_program: Program<'info, System>,
}

/// Owner-gated: `has_one = owner` binds the signer to the stored owner. The pod key fails here.
#[derive(Accounts)]
pub struct OwnerOnly<'info> {
    #[account(mut, has_one = owner @ VaultError::NotOwner)]
    pub vault: Account<'info, Vault>,
    pub owner: Signer<'info>,
}

#[derive(Accounts)]
pub struct PodRebalance<'info> {
    #[account(mut)]
    pub vault: Account<'info, Vault>,
    pub operator: Signer<'info>, // checked against vault.operator in the handler
    // + CLMM accounts (pool, position, vault-owned token accounts, ...) as remaining_accounts
}

#[derive(Accounts)]
pub struct Withdraw<'info> {
    #[account(mut, has_one = owner @ VaultError::NotOwner)]
    pub vault: Account<'info, Vault>,
    pub owner: Signer<'info>,
    #[account(mut)]
    pub vault_token_account: Account<'info, TokenAccount>,
    #[account(mut)]
    pub owner_token_account: Account<'info, TokenAccount>,
    pub token_program: Program<'info, Token>,
}

#[event]
pub struct VaultEvent { pub vault: Pubkey, pub kind: u8, pub actor: Pubkey } // 0=init 1=setop 2=rebalance 3=withdraw

#[error_code]
pub enum VaultError {
    #[msg("signer is not the vault owner")]
    NotOwner,
    #[msg("signer is not the vault operator")]
    NotOperator,
    #[msg("target program is not a whitelisted CLMM")]
    ProgramNotWhitelisted,
}
