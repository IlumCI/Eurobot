/**
 * Cookie / storage consent — single source of truth.
 *
 * The site currently sets NO non-essential cookies: theme + this consent record live in
 * localStorage (strictly necessary), and traffic analytics is Cloudflare's cookieless kind.
 * This module exists so that the MOMENT you add anything non-essential (e.g. a cookie-based
 * analytics tag), it is gated behind opt-in consent and GDPR-correct by construction.
 *
 * Stored shape: { necessary: true, analytics: boolean, ts: ISO string, v: 1 }
 */
export const CONSENT_KEY = 'vg-consent';
export const CONSENT_EVENT = 'vg-consent-change';
export const OPEN_EVENT = 'vg-open-consent';

export function getConsent() {
  try {
    const raw = localStorage.getItem(CONSENT_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function setConsent({ analytics }) {
  const record = { necessary: true, analytics: !!analytics, v: 1, ts: new Date().toISOString() };
  try {
    localStorage.setItem(CONSENT_KEY, JSON.stringify(record));
  } catch {
    /* storage blocked — respect the choice for this session only */
  }
  applyConsent(record);
  try {
    window.dispatchEvent(new CustomEvent(CONSENT_EVENT, { detail: record }));
  } catch {
    /* no-op */
  }
  return record;
}

/** Ask the banner to reopen (wired to the footer "Cookie preferences" control). */
export function openConsent() {
  try {
    window.dispatchEvent(new CustomEvent(OPEN_EVENT));
  } catch {
    /* no-op */
  }
}

/**
 * Load / unload non-essential integrations according to the consent record.
 * Nothing to do today (no cookie-based tools). When you add one, load it here ONLY when
 * record.analytics === true — do not load it anywhere else.
 */
export function applyConsent(record) {
  if (!record) return;
  // if (record.analytics) { /* inject your cookie-based analytics tag here */ }
}
