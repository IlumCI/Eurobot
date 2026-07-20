import { useCallback, useEffect, useState } from 'react';
import { supabase, phantom, walletAddress } from './supabase.js';

const STATEMENT =
  'Sign in to Vältgeist. This proves you control this wallet. It authorizes no transaction and moves no funds.';

// Try the Wallet-Standard auto-detect first (works for any standard Solana wallet),
// then fall back to the explicit Phantom provider object.
async function signInWithSolana() {
  let res = await supabase.auth.signInWithWeb3({ chain: 'solana', statement: STATEMENT });
  if (res.error && window.phantom) {
    res = await supabase.auth.signInWithWeb3({ chain: 'solana', statement: STATEMENT, wallet: window.phantom });
  }
  return res;
}

function short(addr) {
  return addr ? `${addr.slice(0, 4)}…${addr.slice(-4)}` : '';
}

export default function App() {
  const [session, setSession] = useState(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // Create/refresh the user's profile row with their verified wallet address.
  const ensureProfile = useCallback(async user => {
    const addr = walletAddress();
    if (!user || !addr) return;
    const { error: e } = await supabase.from('profiles').upsert(
      { id: user.id, wallet_address: addr },
      { onConflict: 'id' },
    );
    if (e) console.warn('profile upsert failed:', e.message);
  }, []);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setReady(true);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => {
      setSession(s);
      if (s?.user) ensureProfile(s.user);
    });
    return () => sub.subscription.unsubscribe();
  }, [ensureProfile]);

  const connect = async () => {
    setError('');
    if (!phantom()) {
      setError('No Phantom wallet detected. Install Phantom, then reload.');
      return;
    }
    setBusy(true);
    try {
      const { error: e } = await signInWithSolana();
      if (e) setError(e.message);
    } catch (e) {
      setError(e.message || 'Sign-in failed.');
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    await supabase.auth.signOut();
  };

  if (!ready) {
    return (
      <div className="app">
        <p className="mono dim">loading…</p>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="bar">
        <span className="wordmark">VÄLTGEIST</span>
        <span className="mono dim doc">DASHBOARD · VG-D1</span>
      </header>

      {session ? (
        <main className="panel">
          <p className="kicker mono">SIGNED IN</p>
          <h1 className="addr mono">{short(walletAddress()) || 'wallet connected'}</h1>
          <p className="dim">
            You're authenticated by wallet signature — no password, and Vältgeist never held a key to
            get here. Your pod, config, and telemetry will live in this dashboard next.
          </p>
          <div className="row">
            <span className="tag mono">status: no active subscription</span>
          </div>
          <button className="btn ghost" onClick={signOut}>
            SIGN OUT
          </button>
        </main>
      ) : (
        <main className="panel">
          <p className="kicker mono">GUEST</p>
          <h1 className="headline">Connect your wallet to open your pod.</h1>
          <p className="dim">
            Sign in with Solana — you'll sign a message proving you control the wallet. It authorizes no
            transaction and moves no funds. That signature is your entire login.
          </p>
          <button className="btn solid" onClick={connect} disabled={busy}>
            {busy ? 'CHECK YOUR WALLET…' : 'CONNECT WALLET →'}
          </button>
          {error && <p className="error mono">{error}</p>}
          <p className="dim small">
            No account, no password. New here? <a href="https://valtgeist.trade">valtgeist.trade</a>
          </p>
        </main>
      )}
    </div>
  );
}
