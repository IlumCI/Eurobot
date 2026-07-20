import { useEffect, useState } from 'react';
import { supabase } from './supabase.js';

const STATE_NOTE = {
  HATCHING: 'gathering market data before it quotes',
  FLYING: 'quoting and compounding',
  PERCHED: 'cascade detected — pulled out, waiting for calm',
  ASHES: 'equity floor breached — halted for good',
  VETOED: 'venue failed the guards — refused at start',
};

// The runtime emits a small position vocabulary; render it in plain language.
const POSITION = {
  idle: 'idle — waiting to quote',
  opening: 'opening a position…',
  closing: 'rebalancing…',
  bid_in: 'bid · in range — earning fees',
  bid_out: 'bid · out of range',
  ask_in: 'ask · in range — earning fees',
  ask_out: 'ask · out of range',
};

// Format a price/amount to ~5 significant figures so it reads well at any magnitude
// (a 0.00002 memecoin and a 150 SOL price both look right).
function sig(v, digits = 5) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  if (n === 0) return '0';
  return Number(n.toPrecision(digits)).toString();
}

function money(v, d = 4) {
  return v === null || v === undefined ? '—' : Number(v).toFixed(d);
}

// Presentational — pure, so it can be previewed with any state.
export function PodTelemetryView({ state, quote = 'USDC' }) {
  if (!state) {
    return (
      <div className="telem empty mono">
        <span className="blink">○</span> Pod not running. It goes live once you subscribe and your vault
        is funded — telemetry streams here in real time.
      </div>
    );
  }
  const s = state;
  const pnl = s.pnl == null ? null : Number(s.pnl);
  const initial = pnl == null || s.equity == null ? null : Number(s.equity) - pnl;
  const pnlPct = initial && initial > 0 ? (pnl / initial) * 100 : null;
  const pnlClass = pnl == null ? '' : pnl > 0 ? 'gain' : pnl < 0 ? 'loss' : '';
  const inRange = s.position === 'bid_in' || s.position === 'ask_in';

  return (
    <div className="telem mono">
      <div className="telem-bar">
        <span>POD TELEMETRY</span>
        <span className="blink live">● LIVE</span>
      </div>

      <div className="telem-body">
        {/* what it's doing */}
        <Row k="state" v={s.runtime_state || '—'} note={STATE_NOTE[s.runtime_state]} />
        <Row
          k="position"
          v={<span className={inRange ? 'gain' : ''}>{POSITION[s.position] || '—'}</span>}
        />
        <Row k="price" v={`${sig(s.price)} ${quote}`} />
        <Row k="band" v={`[${sig(s.band_lower, 6)} , ${sig(s.band_upper, 6)}]`} />

        <div className="telem-sep" />

        {/* the money */}
        <Row k="equity" v={`${money(s.equity)} ${quote}`} />
        <Row
          k="p&l"
          v={
            <span className={pnlClass}>
              {pnl == null ? '—' : `${pnl >= 0 ? '+' : ''}${money(pnl)} ${quote}`}
              {pnlPct != null && `  (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%)`}
            </span>
          }
        />
        <Row k="fees" v={`${money(s.fees_earned, 5)} ${quote}`} note="LP fees earned" />

        <div className="telem-sep" />

        {/* the gears (signals) */}
        <Row
          k="signals"
          v={
            <span className="telem-note">
              trend {s.trend >= 0 ? `+${money(s.trend, 2)}` : money(s.trend, 2)} · cascade{' '}
              {money(s.hawkes_n, 2)}/0.70{s.lvr_daily != null && ` · lvr ${money(s.lvr_daily, 3)}%/day`}
            </span>
          }
        />
      </div>

      <div className="telem-foot">
        updated {s.updated_at ? new Date(s.updated_at).toLocaleTimeString() : '—'}
      </div>
    </div>
  );
}

function Row({ k, v, note }) {
  return (
    <div className="telem-row">
      <span className="telem-k">{k}</span>
      <span className="telem-v">{v}</span>
      {note && <span className="telem-note">{note}</span>}
    </div>
  );
}

// Container — fetches this user's pod_state (RLS-scoped) and polls for live updates.
// Also reads the pod's pair once, to label amounts in the right quote token.
export default function PodTelemetry() {
  const [state, setState] = useState(null);
  const [quote, setQuote] = useState('USDC');

  useEffect(() => {
    let alive = true;
    supabase
      .from('pods')
      .select('trading_pair')
      .maybeSingle()
      .then(({ data }) => {
        if (alive && data?.trading_pair?.includes('-')) setQuote(data.trading_pair.split('-')[1]);
      });
    const load = async () => {
      const { data } = await supabase.from('pod_state').select('*').maybeSingle();
      if (alive) setState(data || null);
    };
    load();
    const id = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return <PodTelemetryView state={state} quote={quote} />;
}
