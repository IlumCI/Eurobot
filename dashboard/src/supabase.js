import { createClient } from '@supabase/supabase-js';

const url = import.meta.env.VITE_SUPABASE_URL;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

export const supabase = createClient(url, key, {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
});

/** The Phantom Solana provider, if the extension is present. */
export function phantom() {
  return window.phantom?.solana ?? (window.solana?.isPhantom ? window.solana : null);
}

/** Best-effort read of the connected wallet's public key as a base58 string. */
export function walletAddress() {
  const p = phantom();
  return p?.publicKey ? p.publicKey.toString() : null;
}
