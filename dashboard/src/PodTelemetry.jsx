import { useEffect, useState } from 'react';
import { supabase } from './supabase.js';

const STATE_NOTE = {
  HATCHING: 'gathering market data before it quotes',
  FLYING: 'quoting and compounding',
  PERCHED: 'cascade detected — pulled out, waiting for calm',
  ASHES: 'equity floor breached — halted for good',
  VETOED: 'venue failed the guards — refused at start',
};

function fmt(v, d = 4) {
  return v === null || v === undefined ? '—' : Number(v).toFixed(d);
}

// Presentational — pure, so it can be previewed with any state.
export function PodTelemetryView({ state }) {
  if (!state) {
    return (
      <div className="telem empty mono">
        <span className="blink">○</span> Pod not running. It goes live once you subscribe and your vault
        is funded — telemetry streams here in real time.
      </div>
    );
  }
  const s = state;
  return (
    <div className="telem mono">
      <div className="telem-bar">
        <span>POD TELEMETRY</span>
        <span className="blink live">● LIVE</span>
      </div>
      <div className="telem-body">
        <Row k="state" v={`${s.runtime_state || '—'}`} note={STATE_NOTE[s.runtime_state]} />
        <Row k="equity" v={`${fmt(s.equity)} USDC`} />
        <Row k="deploy" v={`${fmt(s.deploy)} USDC`} />
        <Row k="trend" v={s.trend >= 0 ? `+${fmt(s.trend, 2)}` : fmt(s.trend, 2)} />
        <Row k="hawkes n" v={`${fmt(s.hawkes_n, 2)} (panic ≥ 0.70)`} />
        <Row k="band" v={`[${fmt(s.band_lower, 7)} , ${fmt(s.band_upper, 7)}]`} />
        {s.lvr_daily != null && <Row k="lvr gauge" v={`${fmt(s.lvr_daily, 3)}%/day`} />}
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
export default function PodTelemetry() {
  const [state, setState] = useState(null);

  useEffect(() => {
    let alive = true;
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

  return <PodTelemetryView state={state} />;
}
