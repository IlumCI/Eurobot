import { useEffect, useRef, useState } from 'react';

/**
 * Client-side telemetry simulator for the DEMO dashboard — no wallet, no funds, no
 * backend. It emits the same shape as a real `pod_state` row so the exact same views
 * render it. The arc is scripted to be legible in ~a minute: warm up, start earning
 * fees, survive one market cascade (step back, dip, recover), keep earning.
 *
 * This is clearly labelled "DEMO / simulated" everywhere it renders. It is a
 * marketing + design artefact, never presented as real performance.
 */
function makeSim() {
  const START = 100;
  let t = 0; // seconds of sim time
  let price = 77.88;
  let anchor = price;
  let fees = 0;
  let inv = 0; // inventory P&L, mean-reverting noise
  let hawkes = 0.06;
  let ema = price;
  let cascadeAt = 34 + Math.random() * 10; // one scripted cascade
  let cascade = 0; // remaining cascade seconds

  const band = () => ({ lower: anchor * 0.986, upper: anchor * 1.006 }); // asymmetric: wide below

  return function step(dt) {
    t += dt;
    const rnd = () => Math.random() * 2 - 1;

    // trigger the scripted cascade once
    if (cascade <= 0 && t >= cascadeAt && t < cascadeAt + 0.1) cascade = 9;

    if (cascade > 0) {
      cascade -= dt;
      price *= 1 - 0.006 - Math.random() * 0.004; // sharp drop
      hawkes = Math.min(0.9, hawkes + 0.22);
      inv -= 0.06; // small protective loss as it exits
    } else {
      price *= 1 + rnd() * 0.0022; // gentle random walk
      hawkes = Math.max(0.04, hawkes * 0.86 + Math.random() * 0.02);
      inv += (0 - inv) * 0.08 + rnd() * 0.04; // mean-revert
    }
    ema += (price - ema) * 0.12;
    const { lower, upper } = band();

    // re-anchor the band when price wanders out of it (and market is calm)
    if (cascade <= 0 && (price < lower || price > upper)) anchor = price;

    // lifecycle state
    let runtime_state;
    if (t < 5) runtime_state = 'HATCHING';
    else if (cascade > 0) runtime_state = 'PERCHED';
    else runtime_state = 'FLYING';

    const { lower: lo, upper: hi } = band();
    const mid = (lo + hi) / 2;
    const inRange = price >= lo && price <= hi;

    let position;
    if (runtime_state === 'HATCHING') position = 'idle';
    else if (runtime_state === 'PERCHED') position = 'closing';
    else position = inRange ? (price < mid ? 'bid_in' : 'ask_in') : price < lo ? 'bid_out' : 'ask_out';

    // fees accrue only while actively quoting in range
    if (runtime_state === 'FLYING' && inRange) fees += 0.018 + Math.random() * 0.03;

    const equity = START + fees + inv;
    return {
      runtime_state,
      position,
      price,
      equity,
      pnl: equity - START,
      fees_earned: fees,
      band_lower: lo,
      band_upper: hi,
      trend: (price - ema) / ema * 100,
      hawkes_n: hawkes,
      lvr_daily: 0.04 + Math.random() * 0.006,
      updated_at: new Date().toISOString(),
      _t: t,
    };
  };
}

const CAP = 160;

/** Runs the simulator on an interval and returns { state, history, quote }. */
export function useDemoTelemetry(enabled = true) {
  const [state, setState] = useState(null);
  const [history, setHistory] = useState([]);
  const stepRef = useRef(null);

  useEffect(() => {
    if (!enabled) return undefined;
    stepRef.current = makeSim();
    // seed a few points so the charts aren't empty on first paint
    let hist = [];
    let s = null;
    for (let i = 0; i < 6; i++) s = stepRef.current(1.2);
    // (the loop above advances sim; capture points from here on)
    const push = snap => {
      hist = [...hist, { t: snap._t, price: snap.price, equity: snap.equity, fees: snap.fees_earned }].slice(-CAP);
      setHistory(hist);
      setState(snap);
    };
    push(s);
    const id = setInterval(() => push(stepRef.current(1.2)), 1200);
    return () => clearInterval(id);
  }, [enabled]);

  return { state, history, quote: 'USDT' };
}
