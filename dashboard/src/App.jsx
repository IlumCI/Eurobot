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

// Standalone demo view — no wallet, no Supabase, no funds. Reachable at ?demo=1 so it can
// be linked/screenshotted; it renders the exact dashboard against a simulated stream.
function DemoView() {
  return (
    <div className="app app--wide">
      <header className="bar">
        <span className="wordmark">VÄLTGEIST</span>
        <span className="mono dim doc">DASHBOARD · DEMO</span>
      </header>
      <main className="panel signed">
        <div className="demo-banner">
          <strong>This is a live demo.</strong> Simulated data — no wallet, no funds, nothing at risk. It
          shows how your dashboard looks while a pod runs. <a href="https://valtgeist.trade">Join the waitlist →</a>
        </div>
        <div className="idrow">
          <div>
            <p className="kicker mono">DEMO POD</p>
            <h1 className="addr mono">SOL-USDT</h1>
          </div>
          <a className="btn ghost" href="?">EXIT DEMO</a>
        </div>
        <PodTelemetry demo />
      </main>
    </div>
  );
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

  // Demo bypasses auth + Supabase entirely, so it works with no wallet and no env.
  if (typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('demo')) {
    return <DemoView />;
  }

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
    <div className={`app${session ? ' app--wide' : ''}`}>
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
          <div className="cta-row">
            <button className="btn solid" onClick={connect} disabled={busy}>
              {busy ? 'CHECK YOUR WALLET…' : 'CONNECT WALLET →'}
            </button>
            <a className="btn ghost" href="?demo=1">▶ WATCH A LIVE DEMO</a>
          </div>
          {error && <p className="error mono">{error}</p>}
          <p className="dim small">
            No account, no password. New here? <a href="https://valtgeist.trade">valtgeist.trade</a>
          </p>
        </main>
      )}
    </div>
  );
}
