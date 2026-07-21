import { useEffect, useState } from 'react';
import { getConsent, setConsent, applyConsent, OPEN_EVENT } from './consent.js';

/**
 * GDPR-style consent banner. Shows on first visit (no stored choice) and whenever the user
 * reopens it via the footer "Cookie preferences" control. Non-essential storage is opt-IN:
 * nothing beyond strictly-necessary runs until the visitor chooses "Accept all".
 */
export default function CookieConsent() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const existing = getConsent();
    if (existing) applyConsent(existing);
    else setOpen(true);
    const reopen = () => setOpen(true);
    window.addEventListener(OPEN_EVENT, reopen);
    // support deep-links from the Cookie Policy page: /#cookie-preferences
    if (window.location.hash === '#cookie-preferences') setOpen(true);
    return () => window.removeEventListener(OPEN_EVENT, reopen);
  }, []);

  if (!open) return null;

  const choose = analytics => {
    setConsent({ analytics });
    setOpen(false);
  };

  return (
    <div className="cc-banner" role="dialog" aria-modal="false" aria-label="Cookie consent">
      <div className="cc-inner">
        <p className="cc-text">
          <span className="cc-label mono">COOKIES</span>
          This site uses only strictly-necessary local storage to work (your theme and this
          choice), plus cookieless analytics. We set no tracking cookies without your consent.{' '}
          <a href="/cookies">Cookie Policy</a>.
        </p>
        <div className="cc-actions">
          <button type="button" className="cc-btn cc-ghost" onClick={() => choose(false)}>
            Necessary only
          </button>
          <button type="button" className="cc-btn cc-solid" onClick={() => choose(true)}>
            Accept all
          </button>
        </div>
      </div>
    </div>
  );
}
