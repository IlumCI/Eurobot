import { useEffect, useState } from 'react';
import { supabase, phantom, walletAddress, configError } from './supabase.js';
import PodConfig from './PodConfig.jsx';
import PodTelemetry from './PodTelemetry.jsx';

// SIWS requires an ASCII-only statement — the non-ASCII "ä" makes Phantom reject the
// message as "invalid formatting", so the wallet display name here is plain ASCII.
const STATEMENT =
  'Sign in to Valtgeist. This proves you control this wallet. It authorizes no transaction and moves no funds.';

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

  // The profiles row is created server-side from the verified identity (DB trigger),
  // so the client never asserts the wallet address — it only reads the session.
  useEffect(() => {
    if (!supabase) return;
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setReady(true);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => setSession(s));
    return () => sub.subscription.unsubscribe();
  }, []);

  // Prefer the verified wallet from the session (survives reload); fall back to the live wallet.
  const address = session?.user?.user_metadata?.custom_claims?.address || walletAddress();

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

  if (configError) {
    return (
      <div className="app">
        <p className="error mono">{configError}</p>
      </div>
    );
  }

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
        <main className="panel signed">
          <div className="idrow">
            <div>
              <p className="kicker mono">YOUR POD</p>
              <h1 className="addr mono">{short(address) || 'wallet connected'}</h1>
            </div>
            <button className="btn ghost" onClick={signOut}>
              SIGN OUT
            </button>
          </div>
          <PodTelemetry />
          <PodConfig />
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
