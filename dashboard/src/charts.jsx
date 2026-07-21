import { useState } from 'react';

// Shared geometry. viewBox is fixed; the svg scales to its container width (height
// auto keeps the aspect ratio so percentage-positioned tooltips line up exactly).
const W = 600;
const H = 176;
const PAD = { t: 16, r: 16, b: 22, l: 16 };

const linePath = pts => pts.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');

// Map a pointer position anywhere over the chart to the nearest data index.
function useHover(n) {
  const [idx, setIdx] = useState(null);
  const left = PAD.l / W;
  const right = (W - PAD.r) / W;
  const onMove = e => {
    const r = e.currentTarget.getBoundingClientRect();
    const xr = (e.clientX - r.left) / r.width;
    const f = (xr - left) / (right - left);
    setIdx(Math.round(Math.max(0, Math.min(1, f)) * (n - 1)));
  };
  return { idx, onMove, onLeave: () => setIdx(null) };
}

function fmt(v, d = 2) {
  return Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}
function sig(v, d = 5) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return Number(n.toPrecision(d)).toString();
}

/**
 * Balance over time. Single series (portfolio value) → no legend; the title names it.
 * Line + soft area, coloured green when above the starting value, red when below, with
 * a dashed "start" baseline so up/down is readable at a glance.
 */
export function BalanceChart({ points, start, quote }) {
  const n = points.length;
  const { idx, onMove, onLeave } = useHover(n);
  if (n < 2) return <div className="chart-wait">gathering data…</div>;

  const vals = points.map(p => p.equity);
  let lo = Math.min(...vals, start);
  let hi = Math.max(...vals, start);
  const padv = (hi - lo) * 0.14 || 1;
  lo -= padv;
  hi += padv;

  const x = i => PAD.l + (i / (n - 1)) * (W - PAD.l - PAD.r);
  const y = v => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
  const pts = points.map((p, i) => ({ x: x(i), y: y(p.equity) }));
  const last = points[n - 1];
  const up = last.equity >= start;
  const col = up ? 'var(--up)' : 'var(--down)';
  const y0 = H - PAD.b;
  const area = `${linePath(pts)} L${pts[n - 1].x.toFixed(1)},${y0} L${pts[0].x.toFixed(1)},${y0} Z`;
  const yStart = y(start);
  const hp = idx != null && idx < n ? pts[idx] : null;
  const hv = idx != null && idx < n ? points[idx] : null;

  return (
    <div className="chart" onPointerMove={onMove} onPointerLeave={onLeave}>
      <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Your balance over time">
        <defs>
          <linearGradient id="balFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={col} stopOpacity="0.22" />
            <stop offset="100%" stopColor={col} stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* start baseline */}
        <line className="chart-base" x1={PAD.l} x2={W - PAD.r} y1={yStart} y2={yStart} />
        <text className="chart-baselabel" x={W - PAD.r} y={yStart - 5} textAnchor="end">
          start {fmt(start)}
        </text>
        <path d={area} fill="url(#balFill)" />
        <path d={linePath(pts)} fill="none" stroke={col} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {/* current point */}
        <circle cx={pts[n - 1].x} cy={pts[n - 1].y} r="3.5" fill={col} />
        {/* hover crosshair */}
        {hp && <line className="chart-cross" x1={hp.x} x2={hp.x} y1={PAD.t} y2={y0} />}
        {hp && <circle cx={hp.x} cy={hp.y} r="4" fill={col} stroke="var(--paper)" strokeWidth="1.5" />}
      </svg>
      {hp && hv && (
        <div className="chart-tip" style={{ left: `${(hp.x / W) * 100}%`, top: `${(hp.y / H) * 100}%` }}>
          <b>{fmt(hv.equity)} {quote}</b>
        </div>
      )}
    </div>
  );
}

/**
 * Where the pod is trading: the live price line threading through the current trading
 * zone (the band). The zone is neutral (not a status colour); the price dot turns green
 * only while price sits inside it — i.e. while the pod is actually earning.
 */
export function PriceZone({ points, lower, upper, price, inRange, quote }) {
  const n = points.length;
  const { idx, onMove, onLeave } = useHover(n);
  if (n < 2) return <div className="chart-wait">gathering data…</div>;

  const vals = points.map(p => p.price);
  const pmin = Math.min(...vals, lower);
  const pmax = Math.max(...vals, upper);
  // Keep ~1.1 band-heights of clear space above and below the zone so it reads as a band
  // with room around it, not a block filling the whole plot.
  const bandH = Math.max(upper - lower, pmax - pmin, upper * 0.004);
  const lo = pmin - bandH * 1.1;
  const hi = pmax + bandH * 1.1;

  const x = i => PAD.l + (i / (n - 1)) * (W - PAD.l - PAD.r);
  const y = v => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
  const pts = points.map((p, i) => ({ x: x(i), y: y(p.price) }));
  const yUp = y(upper);
  const yLo = y(lower);
  const dotCol = inRange ? 'var(--up)' : 'var(--dim)';
  const hp = idx != null && idx < n ? pts[idx] : null;
  const hv = idx != null && idx < n ? points[idx] : null;

  return (
    <div className="chart" onPointerMove={onMove} onPointerLeave={onLeave}>
      <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Price and trading zone">
        {/* trading zone */}
        <rect className="zone" x={PAD.l} y={yUp} width={W - PAD.l - PAD.r} height={Math.max(2, yLo - yUp)} />
        <line className="zone-edge" x1={PAD.l} x2={W - PAD.r} y1={yUp} y2={yUp} />
        <line className="zone-edge" x1={PAD.l} x2={W - PAD.r} y1={yLo} y2={yLo} />
        <text className="zone-label" x={PAD.l + 2} y={yUp - 4}>trading zone</text>
        {/* price line */}
        <path d={linePath(pts)} fill="none" stroke="var(--ink)" strokeWidth="1.75" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={pts[n - 1].x} cy={pts[n - 1].y} r="4" fill={dotCol} stroke="var(--paper)" strokeWidth="1.5" />
        {hp && <line className="chart-cross" x1={hp.x} x2={hp.x} y1={PAD.t} y2={H - PAD.b} />}
        {hp && <circle cx={hp.x} cy={hp.y} r="4" fill="var(--ink)" stroke="var(--paper)" strokeWidth="1.5" />}
      </svg>
      {hp && hv && (
        <div className="chart-tip" style={{ left: `${(hp.x / W) * 100}%`, top: `${(hp.y / H) * 100}%` }}>
          <b>{sig(hv.price)} {quote}</b>
        </div>
      )}
    </div>
  );
}
