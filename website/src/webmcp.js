/**
 * WebMCP — expose Vältgeist's site actions to in-browser AI agents.
 *
 * Progressive enhancement: if the visiting agent's browser implements the WebMCP
 * proposal (navigator.modelContext), we register a small set of tools so the agent
 * can read structured facts and take the one real action the site offers today —
 * joining the waitlist. No-ops everywhere else (normal browsers, prerender, SSR).
 *
 * Spec (unstable): https://webmachinelearning.github.io/webmcp/
 */
import { postWaitlist, EMAIL_RE, WAITLIST_CONTACT } from './submitWaitlist.js';

const FACTS = {
  name: 'Vältgeist',
  tagline: 'Everything a hedge fund has. Except the fund.',
  what: 'Non-custodial, autonomous market-making "pods" for Solana — a prop desk of one. You deploy and control a pod that provides concentrated liquidity from a vault only you can withdraw from.',
  custody:
    'Non-custodial by construction. Your funds stay in a vault only you can withdraw from. The pod holds a scoped, revocable delegation that can rebalance on whitelisted programs and can never move funds out.',
  not: 'Not a fund. Not pooled. Not custodial. No investment advice, no promise of returns.',
  status: 'Pre-launch. Waitlist open. Real funds go live only after the vault is independently audited.',
  risk: 'Experimental market making on volatile crypto-assets can lose money, including total loss. No returns are promised.',
  parent: 'A venture of the Euroswarms research institute.',
  site: 'https://valtgeist.trade',
  contact: WAITLIST_CONTACT,
};

const PRICING = {
  model: 'Flat subscription per pod. No performance fee, no share of profits.',
  price: '€19 / month per pod',
  compare: 'Contrast with a hedge fund: no minimum, no accreditation, no 2-and-20, no lockup.',
  note: 'Indicative pre-launch pricing; final terms are shown at sign-up when the Service is live.',
};

const text = t => ({ content: [{ type: 'text', text: typeof t === 'string' ? t : JSON.stringify(t, null, 2) }] });
const noArgs = { type: 'object', properties: {}, additionalProperties: false };

const TOOLS = [
  {
    name: 'valtgeist_get_facts',
    description:
      'Get structured facts about Vältgeist: what it is, its non-custodial custody model, status, risk, and contact. Use this to answer questions about the product.',
    inputSchema: noArgs,
    async execute() {
      return text(FACTS);
    },
  },
  {
    name: 'valtgeist_get_pricing',
    description: 'Get Vältgeist pricing: the flat per-pod subscription and how it compares to a hedge fund.',
    inputSchema: noArgs,
    async execute() {
      return text(PRICING);
    },
  },
  {
    name: 'valtgeist_get_status',
    description: 'Get the current launch status of Vältgeist (whether pods are live yet, and what comes next).',
    inputSchema: noArgs,
    async execute() {
      return text(FACTS.status);
    },
  },
  {
    name: 'valtgeist_join_waitlist',
    description:
      "Add an email address to the Vältgeist launch waitlist. Only call this when the user has explicitly asked to join. By joining, the user agrees to be contacted about pod availability per Vältgeist's Privacy Policy (https://valtgeist.trade/privacy); no marketing lists, no sharing.",
    inputSchema: {
      type: 'object',
      properties: {
        email: { type: 'string', format: 'email', description: "The user's email address to register." },
      },
      required: ['email'],
      additionalProperties: false,
    },
    async execute({ email }) {
      if (!EMAIL_RE.test(email || '')) return text(`"${email}" is not a valid email address; nothing was submitted.`);
      const { ok, reason } = await postWaitlist(email, { source: 'webmcp' });
      if (ok) return text(`Registered ${email} on the Vältgeist waitlist. They will be contacted when pods open.`);
      if (reason === 'no-endpoint')
        return text(`The waitlist endpoint is not configured; ask the user to email ${WAITLIST_CONTACT} to register.`);
      return text(`Could not register ${email} (${reason}). Please try again shortly, or email ${WAITLIST_CONTACT}.`);
    },
  },
];

export function initWebMCP() {
  try {
    const mc = typeof navigator !== 'undefined' ? navigator.modelContext : null;
    if (!mc) return; // browser doesn't support WebMCP — silently do nothing
    if (typeof mc.provideContext === 'function') {
      mc.provideContext({ tools: TOOLS });
    } else if (typeof mc.registerTool === 'function') {
      TOOLS.forEach(t => mc.registerTool(t));
    }
  } catch {
    /* experimental API; never let it affect the page */
  }
}
