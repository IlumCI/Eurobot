import { useState } from 'react';
import { postWaitlist, WAITLIST_ENDPOINT as ENDPOINT, WAITLIST_CONTACT as CONTACT } from './submitWaitlist.js';

/**
 * Pre-registration form. POSTs JSON to a Formspree-style endpoint set via
 * VITE_WAITLIST_ENDPOINT (e.g. https://formspree.io/f/xxxxxxx). With no
 * endpoint configured it falls back to a prefilled mail compose, so the
 * form never dead-ends. Honeypot field for bots; explicit GDPR consent.
 * The network POST is shared with the WebMCP agent tool via submitWaitlist.js.
 */

export default function Waitlist() {
  const [email, setEmail] = useState('');
  const [consent, setConsent] = useState(false);
  const [state, setState] = useState('idle'); // idle | sending | done | error

  const valid = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email) && consent;

  async function submit(e) {
    e.preventDefault();
    if (!valid || state === 'sending') return;
    // Read the honeypot straight off the DOM, not React state: bots set input
    // values directly and never fire onChange, so a state-backed check misses them.
    if (e.currentTarget.elements._gotcha?.value) return; // bot; drop silently
    if (!ENDPOINT) {
      window.location.href =
        `mailto:${CONTACT}?subject=${encodeURIComponent('Pod waitlist pre-registration')}` +
        `&body=${encodeURIComponent(`Please add ${email} to the Vältgeist pod waitlist.`)}`;
      setState('done');
      return;
    }
    setState('sending');
    const { ok } = await postWaitlist(email, { source: 'valtgeist-site' });
    setState(ok ? 'done' : 'error');
  }

  if (state === 'done') {
    return (
      <div className="waitlist-done mono" role="status">
        REGISTERED ✓ — you will hear from us when pods open. Nothing else will be sent to this address.
      </div>
    );
  }

  return (
    <form className="waitlist" onSubmit={submit} noValidate>
      <div className="waitlist-row">
        <input
          className="waitlist-input mono"
          type="email"
          name="email"
          placeholder="you@example.com"
          value={email}
          onChange={e => setEmail(e.target.value)}
          aria-label="Email address"
          autoComplete="email"
          required
        />
        <button className="btn btn-solid" type="submit" disabled={!valid || state === 'sending'}>
          {state === 'sending' ? 'REGISTERING…' : 'JOIN THE WAITLIST →'}
        </button>
      </div>
      {/* honeypot — humans never see it; read from the DOM at submit */}
      <input
        className="waitlist-gotcha"
        type="text"
        name="_gotcha"
        tabIndex="-1"
        autoComplete="off"
        defaultValue=""
        aria-hidden="true"
      />
      <label className="waitlist-consent">
        <input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />
        <span>
          I consent to Vältgeist storing this address for the sole purpose of contacting me about pod
          availability. No other use, no sharing, no marketing lists — withdraw any time by replying
          "remove" or writing to {CONTACT}.
        </span>
      </label>
      {state === 'error' && (
        <p className="waitlist-error mono" role="alert">
          SUBMISSION FAILED — try again, or write to{' '}
          <a href={`mailto:${CONTACT}?subject=Pod%20waitlist%20pre-registration`}>{CONTACT}</a>
        </p>
      )}
    </form>
  );
}
