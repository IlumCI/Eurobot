import { useEffect, useRef, useState } from 'react';
import { supabase } from './supabase.js';
import { useDemoTelemetry } from './telemetryDemo.js';
import { BalanceChart, PriceZone } from './charts.jsx';

// Plain-language status, computed from the runtime state + current position. Every tone
// ships with a word and a dot — never colour alone.
function statusOf(s) {
  const st = s?.runtime_state;
  const inRange = s?.position === 'bid_in' || s?.position === 'ask_in';
  if (!st) return { label: 'Offline', sub: 'not running yet', tone: 'idle' };
  if (st === 'HATCHING') return { label: 'Warming up', sub: 'getting ready to trade', tone: 'wait' };
  if (st === 'PERCHED') return { label: 'Playing it safe', sub: 'the market dropped — it stepped back', tone: 'warn' };
  if (st === 'ASHES') return { label: 'Stopped', sub: 'it hit its safety limit to protect you', tone: 'stop' };
  if (st === 'VETOED') return { label: 'Didn’t start', sub: 'this pool wasn’t safe enough', tone: 'stop' };
  // FLYING
  return inRange
    ? { label: 'Working', sub: 'earning fees right now', tone: 'go' }
    : { label: 'Working', sub: 'waiting for the price to come back', tone: 'wait' };
}

function money(v, d = 2) {
  return v == null || !Number.isFinite(Number(v))
    ? '—'
    : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}
function sig(v, d = 5) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return Number(n.toPrecision(d)).toString();
}
function ago(iso) {
  if (!iso) return '—';
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 2 ? 'just now' : `${s}s ago`;
}

/** Presentational — pure, renders from a state snapshot + accumulated history. */
export function PodTelemetryView({ state, history = [], quote = 'USDT', demo = false }) {
  if (!state) {
    return (
      <div className="tel-empty">
        <span className="blink">○</span> Your pod isn’t running yet. Once you subscribe and fund your
        vault, everything it does shows up here live.
      </div>
    );
  }

  const status = statusOf(state);
  const equity = Number(state.equity);
  const pnl = Number(state.pnl);
  const start = Number.isFinite(equity) && Number.isFinite(pnl) ? equity - pnl : null;
  const pct = start && start > 0 ? (pnl / start) * 100 : null;
  const up = pnl >= 0;
  const inRange = state.position === 'bid_in' || state.position === 'ask_in';

  return (
    <div className="tel">
      {/* 1 — is it working? one glance. */}
      <div className={`tel-status tone-${status.tone}`}>
        <span className="tel-dot" />
        <div className="tel-status-txt">
          <strong>{status.label}</strong>
          <span>{status.sub}</span>
        </div>
      </div>

      {/* 2 — how much money, up or down. the headline. */}
      <div className="tel-hero">
        <div className="tel-hero-main">
          <span className="tel-hero-label">What your pod is worth</span>
          <span className="tel-hero-value">
            {money(equity)} <em>{quote}</em>
          </span>
          <span className={`tel-delta ${up ? 'is-up' : 'is-down'}`}>
            {up ? '▲' : '▼'} {up ? 'Up' : 'Down'} {money(Math.abs(pnl))} {quote}
            {pct != null && <> ({up ? '+' : '−'}{Math.abs(pct).toFixed(1)}%)</>}
            <span className="tel-delta-since">since it started</span>
          </span>
        </div>
        <div className="tel-hero-tiles">
          <div className="tel-tile">
            <span className="tel-tile-k">Fees earned</span>
            <span className="tel-tile-v is-up">{money(state.fees_earned, 3)} {quote}</span>
          </div>
          <div className="tel-tile">
            <span className="tel-tile-k">Started with</span>
            <span className="tel-tile-v">{money(start)} {quote}</span>
          </div>
          <div className="tel-tile">
            <span className="tel-tile-k">Price now</span>
            <span className="tel-tile-v">{sig(state.price)} {quote}</span>
          </div>
        </div>
      </div>

      {/* 3 & 4 — charts, side by side on desktop */}
      <div className="tel-charts">
        <figure className="tel-chart">
          <figcaption>
            <span>Your balance over time</span>
            <span className="tel-chart-now">{money(equity)} {quote}</span>
          </figcaption>
          <BalanceChart points={history} start={start ?? equity} quote={quote} />
        </figure>

        <figure className="tel-chart">
          <figcaption>
            <span>Where your pod is trading</span>
            <span className={inRange ? 'tel-earning' : 'tel-idle'}>
              {inRange ? '● earning now' : '○ waiting'}
            </span>
          </figcaption>
          <PriceZone
            points={history}
            lower={Number(state.band_lower)}
            upper={Number(state.band_upper)}
            price={Number(state.price)}
            inRange={inRange}
            quote={quote}
          />
          <p className="tel-chart-note">
            Your pod places buy &amp; sell orders across this zone and collects a small fee each time the
            price trades inside it. When it drifts out, the pod re-centres the zone.
          </p>
        </figure>
      </div>

      {/* 5 — for the curious (hidden by default) */}
      <details className="tel-adv">
        <summary>Technical signals</summary>
        <div className="tel-adv-grid">
          <div><span>Trend</span><b>{state.trend >= 0 ? '+' : ''}{money(state.trend, 2)}</b></div>
          <div><span>Cascade risk</span><b>{money(state.hawkes_n, 2)} / 0.70</b></div>
          <div><span>LVR / day</span><b>{money(state.lvr_daily, 3)}%</b></div>
          <div><span>Position</span><b>{state.position || '—'}</b></div>
        </div>
      </details>

      <div className="tel-foot">
        {demo ? 'demo stream · simulated data, no funds' : `updated ${ago(state.updated_at)}`}
      </div>
    </div>
  );
}

const CAP = 160;

/** Live source: poll pod_state (RLS-scoped), accumulate history, derive the quote token. */
function useLiveTelemetry(enabled) {
  const [state, setState] = useState(null);
  const [history, setHistory] = useState([]);
  const [quote, setQuote] = useState('USDT');
  const lastTs = useRef(null);

  useEffect(() => {
    if (!enabled || !supabase) return undefined;
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
      if (!alive || !data) return;
      setState(data);
      if (data.updated_at !== lastTs.current && data.price != null) {
        lastTs.current = data.updated_at;
        setHistory(h =>
          [...h, { t: h.length, price: Number(data.price), equity: Number(data.equity), fees: Number(data.fees_earned) }].slice(-CAP),
        );
      }
    };
    load();
    const id = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [enabled]);

  return { state, history, quote };
}

/** Container. `demo` swaps the live Supabase source for the simulator; the view is identical. */
export default function PodTelemetry({ demo = false }) {
  const live = useLiveTelemetry(!demo);
  const sim = useDemoTelemetry(demo);
  const src = demo ? sim : live;
  return <PodTelemetryView state={src.state} history={src.history} quote={src.quote} demo={demo} />;
}
