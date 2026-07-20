import { useEffect, useState } from 'react';
import { supabase } from './supabase.js';

const VENUES = [
  { id: 'meteora/clmm', label: 'Meteora DLMM' },
  { id: 'orca/clmm', label: 'Orca Whirlpools' },
  { id: 'raydium/clmm', label: 'Raydium CLMM' },
];

// A user picks a risk appetite; each maps to a vetted (kelly_fraction, ashes_floor)
// bundle. Everything else in the strategy stays on researched defaults.
const RISK = {
  conservative: { kelly_fraction: 0.25, ashes_floor_pct: 0.7, label: 'Conservative', note: 'Deploys less of your equity, halts sooner (at −30%).' },
  balanced: { kelly_fraction: 0.4, ashes_floor_pct: 0.6, label: 'Balanced', note: 'The researched default. Halts at −40%.' },
  aggressive: { kelly_fraction: 0.6, ashes_floor_pct: 0.5, label: 'Aggressive', note: 'Deploys more, halts later (at −50%).' },
};

const SOLANA_ADDR = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/; // base58, excludes 0 O I l
const PAIR = /^[A-Za-z0-9]{2,12}-(USDC|USDT|SOL)$/i;

export default function PodConfig() {
  const [pod, setPod] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');

  const [pair, setPair] = useState('');
  const [poolAddress, setPoolAddress] = useState('');
  const [venue, setVenue] = useState('meteora/clmm');
  const [amount, setAmount] = useState(10);
  const [risk, setRisk] = useState('balanced');
  const [flatten, setFlatten] = useState(true);

  useEffect(() => {
    let alive = true;
    supabase
      .from('pods')
      .select('*')
      .maybeSingle()
      .then(({ data }) => {
        if (!alive) return;
        if (data) {
          setPod(data);
          setPair(data.trading_pair || '');
          setPoolAddress(data.pool_address || '');
          setVenue(data.lp_provider || 'meteora/clmm');
          const c = data.config || {};
          setAmount(c.total_amount_quote ?? 10);
          setRisk(c.risk_level || 'balanced');
          setFlatten(c.panic_flatten ?? true);
        }
        setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const pairOk = PAIR.test(pair.trim());
  const poolOk = SOLANA_ADDR.test(poolAddress.trim());
  const amountOk = Number(amount) >= 10;
  const zeroRisk = risk === 'none';
  const valid = pairOk && poolOk && amountOk && !zeroRisk;

  async function save(e) {
    e.preventDefault();
    if (!valid || saving) return;
    setSaving(true);
    setMsg('');
    const r = RISK[risk];
    const fields = {
      trading_pair: pair.trim().toUpperCase(),
      pool_address: poolAddress.trim(),
      lp_provider: venue,
      config: {
        total_amount_quote: Number(amount),
        risk_level: risk,
        kelly_fraction: r.kelly_fraction,
        ashes_floor_pct: r.ashes_floor_pct,
        panic_flatten: flatten,
      },
    };
    let error;
    if (pod) {
      ({ error } = await supabase.from('pods').update(fields).eq('id', pod.id));
    } else {
      const {
        data: { user },
      } = await supabase.auth.getUser();
      ({ error } = await supabase.from('pods').insert({ user_id: user.id, ...fields }));
    }
    setSaving(false);
    setMsg(error ? `Save failed: ${error.message}` : 'Saved. Your pod is a draft until you subscribe.');
    if (!error) setPod(p => ({ ...(p || { provisioning_status: 'draft' }), ...fields }));
  }

  if (loading) return <p className="mono dim">loading your pod…</p>;

  return (
    <form className={`podform${zeroRisk ? ' dimmed' : ''}`} onSubmit={save}>
      <div className="field">
        <label>Trading pair</label>
        <input
          className="mono"
          placeholder="e.g. BONK-USDC"
          value={pair}
          onChange={e => setPair(e.target.value)}
          spellCheck="false"
        />
        {pair && !pairOk && <span className="hint bad">Format: TOKEN-USDC (or USDT / SOL)</span>}
      </div>

      <div className="field">
        <label>Pool address</label>
        <input
          className="mono"
          placeholder="Solana CLMM pool address"
          value={poolAddress}
          onChange={e => setPoolAddress(e.target.value)}
          spellCheck="false"
        />
        {poolAddress && !poolOk && <span className="hint bad">Not a valid Solana address.</span>}
      </div>

      <div className="field">
        <label>Venue</label>
        <select className="mono" value={venue} onChange={e => setVenue(e.target.value)}>
          {VENUES.map(v => (
            <option key={v.id} value={v.id}>
              {v.label}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label>Capital to deploy (USDC)</label>
        <input
          className="mono"
          type="number"
          min="10"
          step="1"
          value={amount}
          onChange={e => setAmount(e.target.value)}
        />
        {!amountOk && <span className="hint bad">Minimum 10 USDC.</span>}
      </div>

      <div className="field risk-field">
        <label>Risk appetite</label>
        <div className="risk-row">
          {Object.entries(RISK).map(([k, v]) => (
            <button
              type="button"
              key={k}
              className={`risk ${risk === k ? 'on' : ''}`}
              onClick={() => setRisk(k)}
            >
              {v.label}
            </button>
          ))}
          <button
            type="button"
            className={`risk ${zeroRisk ? 'on' : ''}`}
            onClick={() => setRisk('none')}
          >
            None
          </button>
        </div>
        <span className={`hint ${zeroRisk ? 'bad' : ''}`}>
          {zeroRisk
            ? "To avoid all risk, it's best not to participate — you can't lose if you don't play."
            : RISK[risk].note}
        </span>
      </div>

      <label className="toggle">
        <input type="checkbox" checked={flatten} onChange={e => setFlatten(e.target.checked)} />
        <span>Panic flatten — on a cascade alarm, exit the token entirely, not just the position.</span>
      </label>

      <p className="guards mono dim">
        Guards always on: pods refuse pools below 0.25% fee and any pump.fun mint. Your funds stay in a
        vault only you can withdraw from.
      </p>

      <div className="podform-foot">
        <button className="btn solid" type="submit" disabled={!valid || saving}>
          {saving ? 'SAVING…' : pod ? 'SAVE CHANGES' : 'CREATE POD (DRAFT)'}
        </button>
        <span className="tag mono">
          status: {pod?.provisioning_status === 'active' ? 'active' : 'draft — subscribe to launch'}
        </span>
      </div>
      {msg && <p className={`hint ${msg.startsWith('Save failed') ? 'bad' : 'ok'}`}>{msg}</p>}
    </form>
  );
}
