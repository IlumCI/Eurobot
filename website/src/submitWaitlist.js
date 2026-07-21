/**
 * Shared waitlist submission — used by the on-page form (Waitlist.jsx) and the
 * WebMCP agent tool (webmcp.js) so there is one code path and one endpoint.
 */
export const WAITLIST_ENDPOINT = import.meta.env.VITE_WAITLIST_ENDPOINT || '';
export const WAITLIST_CONTACT = 'valtgeist@euroswarms.eu';
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

/**
 * POST an email to the waitlist endpoint.
 * @returns {Promise<{ok: boolean, reason: string|null}>}
 *   reason: null | 'invalid-email' | 'no-endpoint' | 'endpoint-error' | 'network-error'
 */
export async function postWaitlist(email, { source = 'valtgeist-site' } = {}) {
  if (!EMAIL_RE.test(email || '')) return { ok: false, reason: 'invalid-email' };
  if (!WAITLIST_ENDPOINT) return { ok: false, reason: 'no-endpoint' };
  try {
    const res = await fetch(WAITLIST_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ email, source, consent: true }),
    });
    return { ok: res.ok, reason: res.ok ? null : 'endpoint-error' };
  } catch {
    return { ok: false, reason: 'network-error' };
  }
}
