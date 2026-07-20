# Vältgeist site

Vite + React one-pager. `npm install && npm run dev` to work on it, `npm run build` for the
deployable `dist/` (any static host: Cloudflare Pages, Netlify, GitHub Pages).

## Waitlist capture

The pre-registration form (`src/Waitlist.jsx`) POSTs JSON to the endpoint in the
`VITE_WAITLIST_ENDPOINT` env var at build time.

1. Create a form at [formspree.io](https://formspree.io) (free tier is fine to start) — you get an
   endpoint like `https://formspree.io/f/abcdwxyz`.
2. Build with it baked in:
   ```
   VITE_WAITLIST_ENDPOINT=https://formspree.io/f/abcdwxyz npm run build
   ```
   (or put it in `.env.production` — see `.env.example`.)
3. Submissions land in the Formspree dashboard / your inbox. Any service accepting a JSON POST
   with an `email` field works the same way (Web3Forms, Basin, your own endpoint later).

**With no endpoint configured** the form falls back to opening a prefilled mail compose to
Europa@Euroswarms.eu — it degrades, it never dead-ends.

The form ships with a honeypot field (`_gotcha`) and an explicit GDPR consent checkbox; the
consent copy promises single-purpose use and easy withdrawal — honor it.
