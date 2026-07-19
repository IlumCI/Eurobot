import CountUp from './reactbits/CountUp.jsx';
import DecryptedText from './reactbits/DecryptedText.jsx';
import GradientText from './reactbits/GradientText.jsx';
import Particles from './reactbits/Particles.jsx';
import ShinyText from './reactbits/ShinyText.jsx';
import SplitText from './reactbits/SplitText.jsx';
import SpotlightCard from './reactbits/SpotlightCard.jsx';

const EMBER = ['#ff6a00', '#ffb347', '#ff3c00'];
const SWARM = ['#22d3ee', '#818cf8', '#22d3ee'];

const MECHANISMS = [
  {
    name: 'Venue guards',
    detail:
      'Refuses pools whose fee tier cannot beat adverse selection. Vetoes pump.fun mints on-chain. Shows the LVR breakeven race live.',
  },
  {
    name: 'Asymmetric bands',
    detail:
      'A protective zone below the position so dumps never crystallize impermanent loss; a tight trip above to re-anchor fast. Hard minimum rebalance interval — over-trading is the documented killer.',
  },
  {
    name: 'Avellaneda–Stoikov geometry',
    detail:
      'The classic market-making optimum, ported to liquidity bins: inventory-skewed reservation price sets the band center, the optimal spread sets its width.',
  },
  {
    name: 'Cascade detection',
    detail:
      'A Hawkes-process detector on the swap stream senses when sell flow starts feeding on itself — and pulls the position before volatility metrics react.',
  },
  {
    name: 'Panic flatten',
    detail:
      'When the alarm fires, the pod does not just close the position. It exits the token entirely, back to your stablecoin.',
  },
  {
    name: 'Sub-Kelly compounding',
    detail:
      'Positions sized as a conservative fraction of live equity — wins compound the next quote, and a hard equity floor halts the machine for good if breached.',
  },
];

const FAQS = [
  {
    q: 'Is this a fund? Do you pool money?',
    a: 'No. There is no pool. Your capital sits in your own wallet, trades from your own wallet, and never touches ours. We sell software and hosting — a pod — for a flat fee.',
  },
  {
    q: 'Can Euroswarms withdraw my funds?',
    a: 'No, and not as a policy promise — as a construction. Your pod trades through a scoped delegation that can open and close positions on whitelisted programs only. Transfers to any other address are structurally impossible, and you can revoke the delegation in your wallet at any moment.',
  },
  {
    q: 'What returns should I expect?',
    a: 'We make no return promises, and you should distrust anyone who does. This is experimental market making on volatile assets. You can lose money — every defense in the machine exists precisely because losses are real. Fund a pod only with capital whose loss would not matter to you.',
  },
  {
    q: 'What do other users get from my pod?',
    a: 'Intelligence, never money. Your pod contributes anonymized signals — cascade alarms, rug blacklist entries, pool crowding data — to the swarm, and receives the same from every other pod. Profits and losses stay strictly yours.',
  },
  {
    q: 'How do I stop?',
    a: 'Revoke the delegation in your wallet — the pod loses all authority instantly, no support ticket required. Cancel the subscription whenever you like.',
  },
  {
    q: 'Is the strategy public?',
    a: 'The research behind it is — the verified strategy catalog and the simulation harness are in the open repository. The pod runtime and swarm layer are proprietary.',
  },
];

function Section({ id, kicker, title, children }) {
  return (
    <section id={id} className="section">
      <p className="kicker mono">{kicker}</p>
      <h2 className="section-title">{title}</h2>
      {children}
    </section>
  );
}

export default function App() {
  return (
    <div className="page">
      <div className="particles-wrap">
        <Particles
          particleColors={['#ff6a00', '#22d3ee', '#ffffff']}
          particleCount={220}
          particleSpread={11}
          speed={0.06}
          particleBaseSize={90}
          moveParticlesOnHover={false}
          alphaParticles
          disableRotation={false}
        />
      </div>

      <nav className="nav">
        <span className="logo mono">
          EURO<span className="logo-accent">SWARMS</span>
        </span>
        <div className="nav-links">
          <a href="#how">How it works</a>
          <a href="#machine">The machine</a>
          <a href="#swarm">The swarm</a>
          <a href="#faq">FAQ</a>
          <a className="cta-small" href="mailto:Europa@Euroswarms.eu?subject=Pod%20waitlist">
            Join the waitlist
          </a>
        </div>
      </nav>

      <header className="hero">
        <SplitText
          text="Your machine. Your wallet. The swarm's brain."
          className="hero-title"
          tag="h1"
          splitType="words"
          delay={90}
          duration={0.9}
          from={{ opacity: 0, y: 46 }}
          to={{ opacity: 1, y: 0 }}
        />
        <div className="hero-sub">
          <DecryptedText
            text="Autonomous market-making pods for Solana, powered by Phoenix."
            animateOn="view"
            sequential
            speed={28}
            className="decrypted"
            encryptedClassName="encrypted"
          />
        </div>
        <p className="hero-line">
          You keep the keys. You keep the capital. You keep the profits.
          <br />
          We run the machine — for a flat fee, never a cut.
        </p>
        <div className="hero-ctas">
          <a className="cta" href="mailto:Europa@Euroswarms.eu?subject=Pod%20waitlist">
            <ShinyText text="Join the waitlist →" speed={2.5} color="#0a0a0a" shineColor="#fff7ed" />
          </a>
          <a className="cta ghost" href="https://github.com/IlumCI/Eurobot" target="_blank" rel="noreferrer">
            Read the research
          </a>
        </div>
        <div className="stats">
          <div className="stat">
            <span className="stat-num mono">
              <CountUp to={6} duration={1.5} />
              /6
            </span>
            <span className="stat-label">simulation suites passing</span>
          </div>
          <div className="stat">
            <span className="stat-num mono">
              <CountUp to={5} duration={1.5} />
              /5
            </span>
            <span className="stat-label">simulated rugs caught early</span>
          </div>
          <div className="stat">
            <span className="stat-num mono">
              $<CountUp to={10} duration={1.5} />
            </span>
            <span className="stat-label">reference bankroll — start small</span>
          </div>
          <div className="stat">
            <span className="stat-num mono">
              <CountUp to={0} duration={1.5} />
            </span>
            <span className="stat-label">times we can touch your money</span>
          </div>
        </div>
      </header>

      <Section id="how" kicker="01 / custody" title="Non-custodial by construction, not by promise">
        <div className="cards">
          <SpotlightCard className="card" spotlightColor="rgba(255, 106, 0, 0.18)">
            <span className="card-step mono">1</span>
            <h3>Connect your wallet</h3>
            <p>
              Phantom connect, nothing else. Your keys are generated by you, held by you, and never leave your
              device. Euroswarms never sees them.
            </p>
          </SpotlightCard>
          <SpotlightCard className="card" spotlightColor="rgba(34, 211, 238, 0.16)">
            <span className="card-step mono">2</span>
            <h3>Grant a scoped delegation</h3>
            <p>
              Your pod receives an agent key that can open and close positions on whitelisted programs —
              and can do <em>nothing else</em>. Withdrawals to any other address are structurally impossible.
              Revoke it any time, instantly.
            </p>
          </SpotlightCard>
          <SpotlightCard className="card" spotlightColor="rgba(255, 106, 0, 0.18)">
            <span className="card-step mono">3</span>
            <h3>Your pod flies</h3>
            <p>
              A dedicated instance of Phoenix runs for you — quoting, defending, compounding — from your
              wallet, on pools it selects under strict venue rules. Flat subscription. Your P&amp;L is yours alone.
            </p>
          </SpotlightCard>
        </div>
      </Section>

      <Section id="machine" kicker="02 / the machine" title="Phoenix: engineered around the ways small accounts die">
        <p className="section-lede">
          Fees, adverse selection, volatility cascades, over-trading. Every mechanism in Phoenix exists
          because one of these kills bots like it. Each one was researched against primary literature and
          adversarially verified before a line of it was written.
        </p>
        <div className="mech-grid">
          {MECHANISMS.map(m => (
            <div className="mech" key={m.name}>
              <GradientText colors={EMBER} animationSpeed={6} className="mech-name">
                {m.name}
              </GradientText>
              <p>{m.detail}</p>
            </div>
          ))}
        </div>
        <p className="mech-states mono">
          HATCHING → FLYING → PERCHED → ASHES — the pod's state machine is visible to you at all times.
        </p>
      </Section>

      <Section id="swarm" kicker="03 / the swarm" title="Communal intelligence. Never communal capital.">
        <div className="swarm-split">
          <div className="swarm-col">
            <GradientText colors={SWARM} animationSpeed={7} className="swarm-head">
              What pods share
            </GradientText>
            <ul>
              <li>Cascade alarms — one pod smells a rug, every pod hears it</li>
              <li>A fleet-wide token and mint blacklist</li>
              <li>Pool crowding data, so pods spread across venues instead of eating each other's edge</li>
              <li>Anonymized market telemetry that sharpens every detector</li>
            </ul>
          </div>
          <div className="swarm-col">
            <GradientText colors={EMBER} animationSpeed={7} className="swarm-head">
              What pods never share
            </GradientText>
            <ul>
              <li>Your capital — it never pools, never commingles</li>
              <li>Your profits and losses — strictly, structurally yours</li>
              <li>Your keys or delegation — one pod, one wallet, one owner</li>
            </ul>
          </div>
        </div>
        <p className="swarm-line">
          Every pod that joins makes every other pod safer and better-placed. The network effect is the
          brain — not the bankroll.
        </p>
      </Section>

      <Section id="pricing" kicker="04 / pricing" title="A flat fee. Never a cut.">
        <div className="price-card">
          <h3 className="mono">POD — EARLY ACCESS</h3>
          <p className="price">
            <span className="mono">€19</span>/month
          </p>
          <ul>
            <li>One dedicated Phoenix pod, hosted and maintained</li>
            <li>Full swarm intelligence membership</li>
            <li>Live state, equity and defense telemetry</li>
            <li>Cancel or revoke at any moment</li>
          </ul>
          <p className="price-note">
            We deliberately charge no percentage of profits — your trading results are none of our business,
            in the most literal sense.
          </p>
          <a className="cta" href="mailto:Europa@Euroswarms.eu?subject=Pod%20waitlist">
            <ShinyText text="Request early access →" speed={2.5} color="#0a0a0a" shineColor="#fff7ed" />
          </a>
        </div>
      </Section>

      <Section id="faq" kicker="05 / faq" title="The questions that matter">
        <div className="faq">
          {FAQS.map(f => (
            <details key={f.q}>
              <summary>{f.q}</summary>
              <p>{f.a}</p>
            </details>
          ))}
        </div>
      </Section>

      <footer className="footer">
        <p className="mono footer-logo">EUROSWARMS</p>
        <p className="footer-legal">
          Euroswarms provides software and hosting. It does not provide investment advice, portfolio
          management, or custody of client assets. You deploy, configure and control your own pod, and you
          can revoke its authority at any time. Automated trading of volatile crypto-assets carries a risk
          of total loss — use only capital whose loss would not matter to you. Nothing on this page is a
          promise of returns.
        </p>
        <p className="footer-contact mono">
          <a href="mailto:Europa@Euroswarms.eu">Europa@Euroswarms.eu</a> ·{' '}
          <a href="https://github.com/IlumCI/Eurobot" target="_blank" rel="noreferrer">
            research repository
          </a>
        </p>
      </footer>
    </div>
  );
}
