/**
 * Prerender the built SPA to static HTML so crawlers (and non-JS clients) get the
 * real content, not an empty <div id="root">. react-snap style: serve dist, load it
 * in a headless browser, wait for animations to settle to final text, and write the
 * rendered DOM back over dist/index.html. React still takes over on load in the browser.
 *
 * Runs as `postbuild`. Uses the local Playwright chromium; override with CHROME_PATH.
 */
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { writeFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, extname } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const dist = resolve(here, '..', 'dist');

// Local dev pins a specific cached chromium (version-match quirk). In CI (e.g. Cloudflare
// Pages) that path won't exist, so fall through to Playwright's own resolved browser —
// install it in the build with `npx playwright install chromium`.
const LOCAL_CHROME = '/home/lummy/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome';
const CHROME =
  process.env.CHROME_PATH ||
  (existsSync(LOCAL_CHROME) ? LOCAL_CHROME : undefined);

const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.png': 'image/png', '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2',
  '.xml': 'application/xml', '.txt': 'text/plain',
};

const server = createServer(async (req, res) => {
  let p = decodeURIComponent(req.url.split('?')[0]);
  if (p === '/') p = '/index.html';
  try {
    const buf = await readFile(resolve(dist, '.' + p));
    res.writeHead(200, { 'Content-Type': MIME[extname(p)] || 'application/octet-stream' });
    res.end(buf);
  } catch {
    res.writeHead(404); res.end('not found');
  }
});

await new Promise(r => server.listen(0, r));
const port = server.address().port;

// Prerender is an enhancement, never a build-breaker: on any failure (no browser on
// this machine, etc.) we keep the CSR index.html, which still carries all meta + JSON-LD.
try {
  const browser = await chromium.launch(CHROME ? { executablePath: CHROME } : {});
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(`http://localhost:${port}/`, { waitUntil: 'networkidle' });
  // Let SplitText/DecryptedText settle to their final, real text before snapshotting.
  await page.waitForTimeout(5000);

  const html = await page.evaluate(() => '<!doctype html>\n' + document.documentElement.outerHTML);
  // sanity: the snapshot must contain the real headline, not scrambled/empty content.
  const ok = html.includes('hedge fund') && html.includes('§01') && html.length > 8000;
  if (ok) {
    writeFileSync(resolve(dist, 'index.html'), html);
    console.log(`prerendered dist/index.html: ${html.length} bytes`);
  } else {
    console.warn('prerender sanity check failed — keeping CSR index.html');
  }
  if (errors.length) console.log('page errors:', errors);
  await browser.close();
} catch (e) {
  console.warn(`prerender skipped (${e.message.split('\n')[0]}) — keeping CSR index.html`);
} finally {
  server.close();
}
