/**
 * Generates website/index.html with the full SEO head: metadata, Open Graph,
 * Twitter cards, icons, and schema.org JSON-LD (Organization, WebSite,
 * SoftwareApplication + Offer, FAQPage). The FAQ is read from src/App.jsx so
 * the structured data can never drift from the visible page.
 *
 * Run:  node scripts/gen-index.mjs   (also runs automatically via `prebuild`)
 *
 * IMPORTANT: schema types are deliberately software/commerce, never a financial
 * product — the site's whole legal posture is "software, not a fund". Keep it that way.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');

const SITE = 'https://valtgeist.trade';
const TITLE = 'Vältgeist — Everything a hedge fund has. Except the fund.';
const DESC =
  'Vältgeist runs autonomous market-making pods on Solana from a vault only you can withdraw from — a prop desk of one. Everything a hedge fund has, except the fund: no pool, no manager, no lockup, no 2-and-20. A flat €19/month.';
const OG_DESC =
  'Autonomous market-making pods for Solana. Your capital in a vault only you can withdraw from, under a delegation that can trade but never move funds out. A prop desk of one — flat €19/month, no cut of profits.';
const KEYWORDS = [
  'Solana market making', 'CLMM liquidity bot', 'non-custodial trading bot', 'prop desk',
  'Meteora DLMM', 'automated liquidity provision', 'Solana DeFi', 'Vältgeist', 'Phoenix pod',
  'delegated trading', 'flat-fee trading software',
].join(', ');
// TODO(handle): set to the real X/Twitter @handle once the account exists.
const TWITTER_HANDLE = '@valtgeist';

// --- pull the FAQ out of App.jsx so JSON-LD mirrors the page exactly ---
function extractFaq() {
  const src = readFileSync(resolve(root, 'src/App.jsx'), 'utf8');
  const block = src.match(/const FAQS = \[([\s\S]*?)\n\];/)[1];
  const items = [...block.matchAll(/\{\s*q:\s*('(?:[^'\\]|\\.)*'),\s*a:\s*('(?:[^'\\]|\\.)*'),?\s*\}/g)];
  // eslint-disable-next-line no-eval
  return items.map(m => ({ q: eval(m[1]), a: eval(m[2]) }));
}

const faq = extractFaq();

const jsonld = [
  {
    '@context': 'https://schema.org',
    '@type': 'Organization',
    '@id': `${SITE}/#org`,
    name: 'Vältgeist',
    url: SITE,
    logo: `${SITE}/favicon.png`,
    description: 'Autonomous market-making pod software for Solana. A venture of the Euroswarms research institute.',
    email: 'valtgeist@euroswarms.eu',
    parentOrganization: { '@type': 'Organization', name: 'Euroswarms', url: 'https://euroswarms.eu' },
    sameAs: ['https://github.com/IlumCI/Eurobot'],
  },
  {
    '@context': 'https://schema.org',
    '@type': 'WebSite',
    '@id': `${SITE}/#website`,
    url: SITE,
    name: 'Vältgeist',
    description: DESC,
    publisher: { '@id': `${SITE}/#org` },
    inLanguage: 'en',
  },
  {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    name: 'Vältgeist Pod',
    applicationCategory: 'FinanceApplication',
    operatingSystem: 'Web, Solana',
    url: SITE,
    description:
      'A hosted instance of the Phoenix market-making engine that provides concentrated liquidity on Solana pools from a vault only you can withdraw from, under a scoped delegation that can trade but never withdraw.',
    publisher: { '@id': `${SITE}/#org` },
    offers: {
      '@type': 'Offer',
      price: '19',
      priceCurrency: 'EUR',
      description: 'One dedicated pod, hosted and maintained. Flat monthly fee, no share of profits.',
      url: `${SITE}/#waitlist`,
    },
  },
  {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: faq.map(f => ({
      '@type': 'Question',
      name: f.q,
      acceptedAnswer: { '@type': 'Answer', text: f.a },
    })),
  },
];

const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />

    <title>${TITLE}</title>
    <meta name="description" content="${DESC}" />
    <meta name="keywords" content="${KEYWORDS}" />
    <meta name="author" content="Vältgeist" />
    <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1" />
    <meta name="color-scheme" content="dark light" />
    <meta name="theme-color" content="#121310" media="(prefers-color-scheme: dark)" />
    <meta name="theme-color" content="#f4f1ea" media="(prefers-color-scheme: light)" />
    <link rel="canonical" href="${SITE}/" />

    <!-- Icons -->
    <link rel="icon" type="image/png" href="/favicon.png" />
    <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
    <link rel="mask-icon" href="/favicon.png" color="#e8490f" />

    <!-- Open Graph -->
    <meta property="og:type" content="website" />
    <meta property="og:site_name" content="Vältgeist" />
    <meta property="og:locale" content="en_US" />
    <meta property="og:url" content="${SITE}/" />
    <meta property="og:title" content="${TITLE}" />
    <meta property="og:description" content="${OG_DESC}" />
    <meta property="og:image" content="${SITE}/og.png" />
    <meta property="og:image:secure_url" content="${SITE}/og.png" />
    <meta property="og:image:type" content="image/png" />
    <meta property="og:image:width" content="1200" />
    <meta property="og:image:height" content="630" />
    <meta property="og:image:alt" content="Vältgeist — Everything a hedge fund has. Except the fund. Autonomous market-making pods for Solana." />

    <!-- Twitter / X -->
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:site" content="${TWITTER_HANDLE}" />
    <meta name="twitter:creator" content="${TWITTER_HANDLE}" />
    <meta name="twitter:title" content="${TITLE}" />
    <meta name="twitter:description" content="${OG_DESC}" />
    <meta name="twitter:image" content="${SITE}/og.png" />
    <meta name="twitter:image:alt" content="Vältgeist — a prop desk of one on Solana. Flat €19/month, no cut of profits." />

    <!-- Structured data -->
${jsonld.map(o => `    <script type="application/ld+json">\n${JSON.stringify(o, null, 2)}\n    </script>`).join('\n')}
  </head>
  <body>
    <div id="root"></div>
    <noscript>
      <h1>Vältgeist — Everything a hedge fund has. Except the fund.</h1>
      <p>${OG_DESC}</p>
      <p>Vältgeist runs autonomous market-making pods on Solana. Your capital sits in a vault only you can
        withdraw from; the pod is authorized to rebalance your liquidity on whitelisted programs and is
        structurally unable to move funds out. A prop desk of one — flat €19 per month, no cut of profits,
        no pool, no manager. Contact: valtgeist@euroswarms.eu</p>
    </noscript>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
`;

writeFileSync(resolve(root, 'index.html'), html);
console.log(`index.html generated: ${faq.length} FAQ items, ${jsonld.length} JSON-LD blocks`);
