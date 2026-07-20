import { useEffect, useState } from 'react';

import CountUp from './reactbits/CountUp.jsx';
import DecryptedText from './reactbits/DecryptedText.jsx';
import SplitText from './reactbits/SplitText.jsx';
import Logo from './Logo.jsx';
import Waitlist from './Waitlist.jsx';

function useTheme() {
  const [theme, setTheme] = useState(() => {
    const stored = localStorage.getItem('es-theme');
    if (stored === 'light' || stored === 'dark') return stored;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('es-theme', theme);
  }, [theme]);
  return [theme, setTheme];
}

const MECHANISMS = [
  {
    id: 'M-01',
    name: 'Venue guards',
    kills: 'Adverse selection',
    detail:
      'Refuses pools whose fee tier cannot beat the σ²/8 arbitrage bleed. Vetoes pump.fun mints by their on-chain suffix. The LVR breakeven race is displayed live.',
  },
  {
    id: 'M-02',
    name: 'Asymmetric bands',
    kills: 'Impermanent loss',
    detail:
      'A wide protective zone below the position — dumps never crystallize IL at the bottom of a wick. A tight trip above re-anchors fast. Hard minimum interval between rebalances.',
  },
  {
    id: 'M-03',
    name: 'Avellaneda–Stoikov geometry',
    kills: 'Naive placement',
    detail:
      'Inventory-skewed reservation price sets the band center; the optimal spread sets its width. One side quoted at a time, flipping with hysteresis as fills convert inventory.',
  },
  {
    id: 'M-04',
    name: 'Hawkes cascade detector',
    kills: 'Rugs',
    detail:
      'An exponential-kernel self-excitation fit on the swap stream. When sell flow starts feeding on itself, the pod knows before any volatility metric does.',
  },
  {
    id: 'M-05',
    name: 'Panic flatten',
    kills: 'Bag-holding',
    detail:
      'On alarm, the pod does not merely close the position — it exits the token entirely, back to your stablecoin, via the swap provider.',
  },
  {
    id: 'M-06',
    name: 'Sub-Kelly compounding',
    kills: 'Ruin',
    detail:
      'Deployment sized as a conservative fraction of live equity. Wins compound the next quote. A hard floor halts the machine permanently if breached.',
  },
];

const FAQS = [
  {
    q: 'Is this a fund? Do you pool money?',
    a: 'No. There is no pool — your capital is never commingled with anyone else’s, or with ours. It sits in a vault that only you can withdraw from; Vältgeist never takes custody of it. We sell software and hosting — a pod — for a flat fee.',
  },
  {
    q: 'Can Vältgeist withdraw my funds?',
    a: 'No — and not as a policy promise, as a construction. Your funds sit in a vault only you can withdraw from. The pod holds an operator key authorized for exactly one action — rebalancing your liquidity on whitelisted programs — and structurally unable to transfer funds to any other address. Only you can withdraw. Revoke the pod’s key any time with a signature from your wallet.',
  },
  {
    q: 'What returns should I expect?',
    a: 'We make no return promises, and you should distrust anyone who does. This is experimental market making on volatile assets. You can lose money — every defense in the machine exists precisely because losses are real. Fund a pod only with capital whose loss would not matter to you.',
  },
  {
    q: 'What do other users get from my pod?',
    a: 'Intelligence, never money. Your pod contributes anonymized signals — cascade alarms, blacklist entries, pool crowding data — to the swarm, and receives the same from every other pod. Profits and losses stay strictly yours.',
  },
  {
    q: 'How do I stop?',
    a: 'Revoke the delegation in your wallet: the pod loses all authority instantly, no support ticket required. Cancel the subscription whenever you like.',
  },
  {
    q: 'Is the strategy public?',
    a: 'The research behind it is — the verified strategy catalog and the simulation harness live in the open repository. The pod runtime and the swarm layer are proprietary.',
  },
];

function RuleLabel({ num, children }) {
  return (
    <div className="rule-label">
      <span className="rule-num">§{num}</span>
      <span className="rule-text">{children}</span>
      <span className="rule-line" />
    </div>
  );
}

export default function App() {
  const [theme, setTheme] = useTheme();
  return (
    <div className="doc">
      {/* ---- masthead ---- */}
      <header className="masthead">
        <div className="masthead-left">
          <Logo height={42} className="site-logo" />
          <span className="doc-meta">AUTONOMOUS MARKET-MAKING PODS</span>
        </div>
        <nav className="masthead-nav">
          <a href="#custody">Custody</a>
          <a href="#machine">Machine</a>
          <a href="#swarm">Swarm</a>
          <a href="#terms">Terms</a>
          <a href="#faq">FAQ</a>
        </nav>
        <div className="masthead-right">
          <span className="doc-meta">DOC. VG-001 / REV B</span>
          <button
            className="mode-toggle"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            aria-label="Toggle color theme"
          >
            <span className={theme === 'light' ? 'active' : ''}>PRINT</span>
            {' / '}
            <span className={theme === 'dark' ? 'active' : ''}>TERM</span>
          </button>
        </div>
      </header>

      {/* ---- hero ---- */}
      <section className="hero">
        <div className="hero-copy">
          <SplitText
            text="Everything a hedge fund has. Except the fund."
            className="hero-title"
            tag="h1"
            splitType="words"
            delay={70}
            duration={0.8}
            textAlign="left"
            from={{ opacity: 0, y: 24 }}
            to={{ opacity: 1, y: 0 }}
          />
          <div className="hero-sub">
            <DecryptedText
              text="The strategy research. The execution engine. The risk machinery. The live telemetry. Missing by design: the pool, the manager, the lockup, the 2-and-20. A prop desk of one, on Solana — trading your own capital from a vault only you can withdraw from, under a delegation that rebalances your liquidity but can never move funds out."
              animateOn="view"
              sequential
              speed={10}
              className="sub-plain"
              encryptedClassName="sub-encrypted"
            />
          </div>
          <div className="hero-actions">
            <a className="btn btn-solid" href="#waitlist">
              REQUEST A POD →
            </a>
            <a className="btn" href="https://github.com/IlumCI/Eurobot" target="_blank" rel="noreferrer">
              READ THE RESEARCH
            </a>
          </div>
          <dl className="figures">
            <div>
              <dt>Simulation suites</dt>
              <dd>
                <CountUp to={6} duration={1.2} />
                <span className="dim">/6</span>
              </dd>
            </div>
            <div>
              <dt>Simulated rugs caught early</dt>
              <dd>
                <CountUp to={5} duration={1.2} />
                <span className="dim">/5</span>
              </dd>
            </div>
            <div>
              <dt>Reference bankroll</dt>
              <dd>
                <span className="dim">$</span>
                <CountUp to={10} duration={1.2} />
              </dd>
            </div>
            <div>
              <dt>Access to your funds</dt>
              <dd>
                <CountUp to={0} duration={1.2} />
                <span className="dim"> ever</span>
              </dd>
            </div>
          </dl>
        </div>

        {/* the one dark object on the page */}
        <aside className="telemetry" aria-label="Example pod telemetry">
          <div className="telemetry-bar">
            <span>POD 0x3F · MEME-USDC</span>
            <span className="blink">●</span>
          </div>
          <pre className="telemetry-body">{`state          FLYING
equity         10.4172 USDC   (+4.17%)
deploy         4.1668 USDC    (0.4 × eq)
band           [0.0012211 , 0.0012443]
   protective  −8.0%  below
   re-anchor   +0.5%  above
trend          +0.31
hawkes n       0.12   (panic ≥ 0.70)
lvr gauge      0.041%/d   vs fee 0.60%
swarm          41 pods · 0 alarms · 3 vetoes
delegation     scoped · revocable · yours`}</pre>
          <div className="telemetry-foot">ILLUSTRATIVE DISPLAY — NOT LIVE DATA, NOT A PROJECTION</div>
        </aside>
      </section>

      {/* ---- custody ---- */}
      <section id="custody" className="block">
        <RuleLabel num="01">Custody — by construction, not by promise</RuleLabel>
        <div className="procedure">
          <div className="step">
            <span className="step-no">1</span>
            <div>
              <h3>Connect your wallet</h3>
              <p>
                Phantom connect, nothing more. Your keys are generated by you, held by you, and never leave
                your device. Vältgeist never sees them.
              </p>
            </div>
          </div>
          <div className="step">
            <span className="step-no">2</span>
            <div>
              <h3>Fund a vault only you can withdraw from</h3>
              <p>
                Your capital moves into a vault program whose sole withdraw key is yours. Not your Phantom
                wallet, but no less yours — Vältgeist never takes custody and cannot move it out. You can pull
                it back whenever you like.
              </p>
            </div>
          </div>
          <div className="step">
            <span className="step-no">3</span>
            <div>
              <h3>The pod flies</h3>
              <p>
                You authorize the pod’s operator key — scoped to one action, rebalancing on whitelisted
                programs, structurally unable to move funds out. A dedicated Phoenix instance then quotes,
                defends and compounds inside your vault. Flat subscription; the P&amp;L is yours alone; revoke
                any time.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ---- machine ---- */}
      <section id="machine" className="block">
        <RuleLabel num="02">The machine — engineered around the ways small accounts die</RuleLabel>
        <p className="lede">
          Fees, adverse selection, volatility cascades, over-trading. Each mechanism exists because one of
          these kills bots like it — and each was verified against the primary literature before a line of it
          was written.
        </p>
        <table className="spec">
          <thead>
            <tr>
              <th>ID</th>
              <th>Mechanism</th>
              <th>Defends against</th>
              <th>Implementation</th>
            </tr>
          </thead>
          <tbody>
            {MECHANISMS.map(m => (
              <tr key={m.id}>
                <td className="spec-id">{m.id}</td>
                <td className="spec-name">{m.name}</td>
                <td className="spec-kills">{m.kills}</td>
                <td className="spec-detail">{m.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="statechart">
          <span className="statechart-label">POD STATES</span>
          <pre>{`HATCHING ──▶ FLYING ◀──────┐
                │            │  calm
                │ cascade    │
                ▼            │
             PERCHED ────────┘
                │ equity floor breached
                ▼
              ASHES ▪ permanent      VETOED ▪ bad venue, refused at start`}</pre>
        </div>
      </section>

      {/* ---- swarm ---- */}
      <section id="swarm" className="block">
        <RuleLabel num="03">The swarm — communal intelligence, never communal capital</RuleLabel>
        <p className="lede">The machine is yours. The brain is shared.</p>
        <table className="ledger">
          <tbody>
            <tr>
              <td className="ledger-mark yes">SHARED</td>
              <td>Cascade alarms — one pod smells a rug, every pod hears it</td>
            </tr>
            <tr>
              <td className="ledger-mark yes">SHARED</td>
              <td>A fleet-wide token and mint blacklist</td>
            </tr>
            <tr>
              <td className="ledger-mark yes">SHARED</td>
              <td>Pool crowding data — pods spread across venues instead of eating each other's edge</td>
            </tr>
            <tr>
              <td className="ledger-mark no">NEVER</td>
              <td>Your capital — it never pools, never commingles, never leaves your control</td>
            </tr>
            <tr>
              <td className="ledger-mark no">NEVER</td>
              <td>Your profits and losses — strictly, structurally yours</td>
            </tr>
            <tr>
              <td className="ledger-mark no">NEVER</td>
              <td>Your keys or your delegation — one pod, one wallet, one owner</td>
            </tr>
          </tbody>
        </table>
        <p className="aside-note">
          Every pod that joins makes every other pod safer and better-placed. The network effect is the brain
          — not the bankroll.
        </p>
      </section>

      {/* ---- terms ---- */}
      <section id="terms" className="block">
        <RuleLabel num="04">Terms — two-and-twenty, struck</RuleLabel>
        <div className="terms">
          <div className="terms-price">
            <span className="price-strike mono">2 &amp; 20</span>
            <span className="price-figure">€19</span>
            <span className="price-per">per month, per pod — that is the entire fee schedule</span>
          </div>
          <div className="terms-body">
            <p>
              A hedge fund charges two percent of your assets plus twenty percent of your gains — for the
              privilege of holding your money. A pod costs nineteen euros: one dedicated Phoenix instance,
              hosted and maintained, full swarm membership, live state, equity and defense telemetry. Cancel
              or revoke at any moment.
            </p>
            <p className="terms-note">
              We take no percentage of profits — your trading results are none of our business, in the most
              literal sense. A fee tied to your returns would make us your manager. We are your machinist.
            </p>
            <a className="btn btn-solid" href="#waitlist">
              REQUEST EARLY ACCESS →
            </a>
          </div>
        </div>
      </section>

      {/* ---- waitlist ---- */}
      <section id="waitlist" className="block">
        <RuleLabel num="05">Pre-registration — form VG-W1</RuleLabel>
        <p className="lede">
          Pods open in small batches — the strategy's edge lives in thin pools, so the fleet grows at the
          pace the venues can carry. The waitlist is first-come: one address, no payment, no obligation.
        </p>
        <Waitlist />
      </section>

      {/* ---- faq ---- */}
      <section id="faq" className="block">
        <RuleLabel num="06">Appendix — the questions that matter</RuleLabel>
        <div className="faq">
          {FAQS.map((f, i) => (
            <details key={f.q}>
              <summary>
                <span className="faq-no">A.{i + 1}</span>
                {f.q}
              </summary>
              <p>{f.a}</p>
            </details>
          ))}
        </div>
      </section>

      {/* ---- colophon ---- */}
      <footer className="colophon">
        <div className="colophon-grid">
          <div>
            <Logo height={34} className="site-logo" />
            <p className="colophon-meta">
              A venture of the Euroswarms research institute. Built on the open Phoenix research —{' '}
              <a href="https://github.com/IlumCI/Eurobot" target="_blank" rel="noreferrer">
                repository
              </a>
              {' '}·{' '}
              <a href="mailto:valtgeist@euroswarms.eu">valtgeist@euroswarms.eu</a>
            </p>
          </div>
          <p className="colophon-legal">
            Vältgeist provides software and hosting. It does not provide investment advice, portfolio
            management, or custody of client assets. It is not a hedge fund, and it does not operate one for
            you: there is no pooled vehicle, no manager, and no client money — you deploy, configure and
            control your own pod, trading your own capital, and you can revoke its authority at any time.
            Automated trading of volatile crypto-assets carries a risk of total loss — use only capital whose
            loss would not matter to you. Nothing on this page is a promise of returns.
          </p>
        </div>
      </footer>
    </div>
  );
}
