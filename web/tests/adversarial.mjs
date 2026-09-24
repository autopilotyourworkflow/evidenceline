// Independent adversarial tests for the Evidenceline website, written by a tester who did not write the site.
//
//   cd web && node tests/adversarial.mjs              (about three minutes; no internet)
//   cd web && node tests/adversarial.mjs --external   (also fetches the external links once)
//
// What it does, all on this computer:
// - builds the site twice with Vite into node_modules/.tmp: the published settings (mode production, .env.production)
//   and the offline settings (mode offline). web/dist is not touched, so it can run beside `npm run check`;
// - serves each build with `vite preview`. The production preview also runs the real Cloudflare Worker
//   (worker/index.js, which uses functions/_lib/proxy.js) on /api/* and /mcp, in front of the real FastAPI service started from ../.venv with no
//   model key (so no model is ever called and nothing is paid for);
// - drives the local Chrome with puppeteer-core. Data files and the question service are mocked with request
//   interception where a case needs it.
//
// Every case prints input, expected, actual and a verdict. A case that shows a real product failure is marked
// xfail (strict): it must fail. If it starts passing, the run fails with XPASS so the mark is removed. Severity first:
//   critical: leaks client data, passes a false number or claim, or shows a false claim on the site;
//   major: wrong result, crash, dead link or broken state;   minor: unclear output.
// Exit code: 0 when every case passed or failed as marked; 1 otherwise.

import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { build, preview } from 'vite';
import { CLIENT_IP_HEADER, PROXY_AUTH_HEADER, buildUpstreamRequest, handleProxy, toClientResponse } from '../functions/_lib/proxy.js';
import worker from '../worker/index.js';
import { isWorkerPath } from '../worker/routing.js';

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repoDir = resolve(webDir, '..');
const tmpDir = join(webDir, 'node_modules', '.tmp');
const prodDir = join(tmpDir, 'adv-dist-production');
const offlineDir = join(tmpDir, 'adv-dist-offline');
const dataDir = join(webDir, 'public', 'data');
const python = join(repoDir, '.venv', 'Scripts', 'python.exe');
const chromePath = process.env.CHROME_PATH ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const EXTERNAL = process.argv.includes('--external');

const PRODUCTION = {
  github: 'https://github.com/autopilotyourworkflow/evidenceline',
  mcp: 'https://evidenceline.autopilotyourworkflow.com/mcp',
};
const ROUTES = ['/', '/sample-site', '/tidy', '/sources', '/accuracy'];
const DASHES = /[\u2013\u2014]/;
const readData = (name) => JSON.parse(readFileSync(join(dataDir, name), 'utf8'));
const tidy = readData('tidy.json');
const site = readData('sample-site.json');
const sources = readData('sources.json');
const accuracy = readData('accuracy.json');
const answers = readData('answers.json');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body, status = 200, headers = {}) => ({
  status,
  contentType: 'application/json',
  headers,
  body: typeof body === 'string' ? body : JSON.stringify(body),
});
const around = (text, re, width = 40) => [...text.matchAll(new RegExp(`.{0,${width}}(?:${re.source}).{0,${width}}`, 'g'))].map((m) => m[0].replace(/\s+/g, ' '));

// ------------------------------------------------------------------------------------------------------------------
// Exact decimal helpers (concentrations are compared as decimals, never as floating point).

/** "0.038" -> { int: 38n, scale: 3 } */
function dec(text) {
  const m = /^(\d+)(?:\.(\d+))?$/.exec(String(text).trim());
  if (m === null) throw new Error(`not a decimal: ${text}`);
  const frac = m[2] ?? '';
  return { int: BigInt(m[1] + frac), scale: frac.length };
}
const rescale = (d, scale) => d.int * 10n ** BigInt(scale - d.scale);
function cmpDec(a, b) {
  const s = Math.max(dec(a).scale, dec(b).scale);
  const x = rescale(dec(a), s);
  const y = rescale(dec(b), s);
  return x === y ? 0 : x < y ? -1 : 1;
}
function addDec(a, b) {
  const s = Math.max(dec(a).scale, dec(b).scale);
  const total = (rescale(dec(a), s) + rescale(dec(b), s)).toString().padStart(s + 1, '0');
  return s === 0 ? total : `${total.slice(0, -s)}.${total.slice(-s)}`.replace(/\.?0+$/, '');
}
/** value / full * 100, in hundredths of a percent, rounded down. */
function percentHundredths(value, full) {
  const s = Math.max(dec(value).scale, dec(full).scale);
  return (rescale(dec(value), s) * 10000n) / rescale(dec(full), s);
}
const styleHundredths = (style) => {
  const m = /^(\d+)(?:\.(\d{1,2}))?%$/.exec(style.trim());
  return m === null ? -1n : BigInt(m[1]) * 100n + BigInt((m[2] ?? '0').padEnd(2, '0'));
};

// ------------------------------------------------------------------------------------------------------------------
// Case runner with strict xfail.

const cases = [];
/**
 * @param {string} id
 * @param {string} name
 * @param {{ input: string, expected: string, xfail?: string, where?: string }} meta
 * @param {() => Promise<{ pass: boolean, actual: string }>} fn
 */
function test(id, name, meta, fn) {
  cases.push({ id, name, ...meta, fn });
}

// ------------------------------------------------------------------------------------------------------------------
// The real API (FastAPI, from ../.venv), with no model key. Two copies: without and with the proxy secret.

const PROXY_SECRET = 'adversarial-test-secret';

async function startApi(port, extraEnv) {
  const env = { ...process.env, ANTHROPIC_API_KEY: '', EVIDENCELINE_CLIENT_IP_HEADER: CLIENT_IP_HEADER, PYTHONIOENCODING: 'utf-8', ...extraEnv };
  const child = spawn(python, ['-m', 'uvicorn', '--factory', 'evidenceline.api.app:create_app', '--host', '127.0.0.1', '--port', String(port), '--log-level', 'warning'], {
    cwd: repoDir,
    env,
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  let stderr = '';
  child.stderr.on('data', (d) => {
    stderr += d.toString();
  });
  const origin = `http://127.0.0.1:${port}`;
  for (let k = 0; k < 120; k++) {
    try {
      const r = await fetch(`${origin}/api/health`);
      if (r.ok) return { origin, child, health: await r.json() };
    } catch {
      // not up yet
    }
    if (child.exitCode !== null) break;
    await sleep(500);
  }
  child.kill();
  throw new Error(`The API did not start on ${origin}: ${stderr.slice(-800)}`);
}

// ------------------------------------------------------------------------------------------------------------------
// Builds and servers.

/** The Cloudflare Worker inside vite preview: the paths it runs first on (/api, /mcp and under them) go to worker/index.js. */
const proxyEnv = { API_ORIGIN: undefined, PROXY_SHARED_SECRET: undefined };
const proxied = [];
/** The static files are vite preview's job here; the Worker is only ever given its own paths. */
const noAssets = { fetch: async () => new Response('not used', { status: 500 }) };
const siteWorker = {
  name: 'adversarial-site-worker',
  configurePreviewServer(server) {
    server.middlewares.use(async (req, res, next) => {
      const path = (req.url ?? '/').split('?')[0];
      if (!isWorkerPath(path)) return next();
      try {
        const chunks = [];
        for await (const c of req) chunks.push(c);
        const headers = new Headers();
        for (const [k, v] of Object.entries(req.headers)) {
          if (typeof v === 'string' && !['host', 'connection', 'content-length', 'transfer-encoding', 'keep-alive'].includes(k)) headers.set(k, v);
        }
        headers.set('cf-connecting-ip', '203.0.113.7');
        const method = req.method ?? 'GET';
        const request = new Request(`http://localhost${req.url}`, { method, headers, body: ['GET', 'HEAD'].includes(method) ? undefined : Buffer.concat(chunks) });
        const response = await worker.fetch(request, { ...proxyEnv, ASSETS: noAssets });
        proxied.push({ path, status: response.status });
        res.statusCode = response.status;
        response.headers.forEach((v, k) => res.setHeader(k, v));
        res.end(Buffer.from(await response.arrayBuffer()));
      } catch (error) {
        res.statusCode = 500;
        res.end(String(error));
      }
    });
  },
};

console.log('adversarial: building the production and offline sites (vite build) ...');
mkdirSync(tmpDir, { recursive: true });
// The production build without the robot check: the real site key in .env.production only works on the published
// address (smoke.mjs checks the production build with it; adversarial_launch.mjs checks Turnstile on the test key).
const siteKeyBefore = process.env.VITE_TURNSTILE_SITE_KEY;
process.env.VITE_TURNSTILE_SITE_KEY = '';
await build({ root: webDir, mode: 'production', logLevel: 'silent', build: { outDir: prodDir, emptyOutDir: true } });
if (siteKeyBefore === undefined) delete process.env.VITE_TURNSTILE_SITE_KEY;
else process.env.VITE_TURNSTILE_SITE_KEY = siteKeyBefore;
await build({ root: webDir, mode: 'offline', logLevel: 'silent', build: { outDir: offlineDir, emptyOutDir: true } });

console.log('adversarial: starting two copies of the API (no model key) ...');
const api = await startApi(8791, { EVIDENCELINE_PROXY_SECRET: '' });
const apiLocked = await startApi(8792, { EVIDENCELINE_PROXY_SECRET: PROXY_SECRET });

const prodServer = await preview({
  root: webDir,
  mode: 'production',
  logLevel: 'silent',
  build: { outDir: prodDir },
  preview: { port: 4391, strictPort: false, open: false },
  plugins: [siteWorker],
});
const offlineServer = await preview({ root: webDir, mode: 'offline', logLevel: 'silent', build: { outDir: offlineDir }, preview: { port: 4392, strictPort: false, open: false } });
const prod = (prodServer.resolvedUrls?.local[0] ?? 'http://localhost:4391/').replace(/\/$/, '');
const offline = (offlineServer.resolvedUrls?.local[0] ?? 'http://localhost:4392/').replace(/\/$/, '');
const browser = await puppeteer.launch({ executablePath: chromePath, headless: true });

const DESKTOP = { width: 1366, height: 900 };
const PHONE = { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };

/**
 * Opens a page. `respond` maps a path to a canned response (or a function returning one, or null to hold the request
 * forever); `beforeLoad(page)` runs before navigation.
 */
async function open(path = '/', { origin = prod, vp = DESKTOP, respond = {}, beforeLoad = null } = {}) {
  const p = await browser.newPage();
  const errors = [];
  const posted = [];
  const held = [];
  p.on('pageerror', (e) => errors.push(e.message));
  p.on('request', (r) => {
    if (r.method() === 'POST') posted.push({ url: r.url(), body: r.postData() ?? '' });
  });
  if (Object.keys(respond).length > 0) {
    await p.setRequestInterception(true);
    p.on('request', async (r) => {
      if (r.isInterceptResolutionHandled()) return;
      const rule = respond[new URL(r.url()).pathname];
      if (rule === undefined) return r.continue();
      const answer = typeof rule === 'function' ? await rule(r) : rule;
      if (answer === null) {
        held.push(r);
        return undefined;
      }
      if (answer === 'abort') return r.abort('connectionrefused');
      return r.respond(answer);
    });
  }
  if (beforeLoad !== null) await beforeLoad(p);
  await p.setViewport(vp);
  await p.goto(origin + path, { waitUntil: 'networkidle0' });
  return { p, errors, posted, held };
}

const text = (p, sel = 'body') => p.$eval(sel, (e) => e.textContent ?? '').catch(() => '');
/** Every string a reader can meet on the page, hidden explanations included. */
const allText = (p) =>
  p.evaluate(() =>
    [
      document.title,
      document.body.textContent ?? '',
      ...[...document.querySelectorAll('[alt],[aria-label],[placeholder],[title]')].flatMap((e) => ['alt', 'aria-label', 'placeholder', 'title'].map((a) => e.getAttribute(a) ?? '')),
    ].join('\n'),
  );
const openAllDetails = (p) => p.evaluate(() => document.querySelectorAll('details').forEach((d) => (d.open = true)));
const pageWidth = (p) => p.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth));

/** Empties the question box the way a person would: select all, delete. */
async function clearBox(p) {
  await p.$eval('#q', (i) => i.scrollIntoView({ block: 'center' }));
  await p.focus('#q');
  await p.keyboard.down('Control');
  await p.keyboard.press('KeyA');
  await p.keyboard.up('Control');
  await p.keyboard.press('Backspace');
}

async function ask(p, question, { wait = 'settled', timeout = 15_000 } = {}) {
  await clearBox(p);
  await p.type('#q', question);
  await p.keyboard.press('Enter');
  if (wait === 'settled') {
    await p.waitForFunction(() => !['loading', null].includes(document.querySelector('#answer')?.getAttribute('data-state') ?? null), { timeout });
  }
  return p.$eval('#answer', (a) => ({ state: a.getAttribute('data-state'), text: a.textContent ?? '' }));
}

/** A complete AnswerResult as the API sends it, with overrides. */
const liveResult = (over = {}) => ({
  question: 'q',
  question_redactions: 0,
  status: 'answered',
  explanation: 'Written from the numbered passages only, then checked in code.',
  answer: 'Tier 1 screening compares results with investigation levels [1].',
  citations: [
    {
      number: 1,
      cited: true,
      document: 'ASC NEPM Schedule B1',
      edition: '2013',
      printed_page: '3',
      pdf_page: 11,
      location: 'p. 3 (PDF p. 11), 2 Tier 1',
      section: '2 Tier 1',
      excerpt: 'Investigation levels are the concentrations of a contaminant above which further appropriate investigation is required.',
      notice: 'Public guidance.',
      link: 'https://www.legislation.gov.au/F2008B00713/2013-05-16/2013-05-16/text/original/pdf/2#page=11',
    },
  ],
  guideline_values: [],
  notes: [],
  verification: { ran: true, passed: true, checks: [{ name: 'numbers traced', passed: true, detail: 'ok' }], summary: 'All checks passed.' },
  model: 'claude-sonnet-5',
  ...over,
});

// ==================================================================================================================
// A. Links and routing
// ==================================================================================================================

/** Every link on a page: none empty or "#", in-page targets exist, site files load as non-HTML, external https. */
async function linkProblems(p, origin, landingIds) {
  const links = await p.evaluate(() =>
    [...document.querySelectorAll('a')].map((a) => ({ href: a.getAttribute('href'), text: (a.textContent ?? '').trim().slice(0, 40) })),
  );
  const dead = [];
  for (const link of links) {
    const href = link.href ?? '';
    if (href === '' || href === '#' || href.startsWith('javascript:')) dead.push({ ...link, why: 'empty or #' });
    else if (href.startsWith('#')) {
      if (!(await p.evaluate((id) => !!document.getElementById(id), decodeURIComponent(href.slice(1))))) dead.push({ ...link, why: 'no target on page' });
    } else if (href.startsWith('/')) {
      const [path, hash] = href.split('#');
      if (ROUTES.includes(path)) {
        if (hash && path === '/' && !landingIds.includes(hash)) dead.push({ ...link, why: 'no such landing section' });
      } else {
        const r = await fetch(origin + path);
        if (!r.ok || (r.headers.get('content-type') ?? '').includes('text/html')) dead.push({ ...link, why: `HTTP ${r.status} ${r.headers.get('content-type')}` });
      }
    } else if (!/^https:\/\/[^/]+\.[a-z]{2,}/i.test(href)) dead.push({ ...link, why: 'not an absolute https link' });
  }
  const fakeButtons = await p.evaluate(() =>
    [...document.querySelectorAll('a:not([href]), [role=button]:not(button), .btn:not(a):not(button), .mbtn, .go')]
      .filter((e) => !e.closest('[role=img]'))
      .map((e) => e.outerHTML.slice(0, 80)),
  );
  return { count: links.length, dead, fakeButtons, externals: links.filter((l) => /^https?:/.test(l.href ?? '')).map((l) => l.href) };
}

let landingIds = [];
const externalLinks = new Set();

test('A1', 'Every link on every page resolves (production build, real data, all drawers open)', {
  input: 'the five routes and a missing route; every <a> and every prepared answer',
  expected: 'no empty or "#" links, every in-page and /#section target exists, every site file loads, no fake buttons',
}, async () => {
  const problems = [];
  let total = 0;
  {
    const { p } = await open('/');
    landingIds = await p.evaluate(() => [...document.querySelectorAll('[id]')].map((e) => e.id));
    await p.close();
  }
  for (const route of [...ROUTES, '/no-such-page']) {
    const { p } = await open(route);
    await openAllDetails(p);
    if (route === '/') {
      // every prepared answer's links too
      const chips = await p.$$eval('.chips button', (b) => b.length);
      for (let k = 0; k < chips; k++) {
        await p.click(`.chips button[data-a="${k}"]`);
        await openAllDetails(p);
        const r = await linkProblems(p, prod, landingIds);
        total += r.count;
        problems.push(...r.dead.map((d) => ({ route: `/ answer ${k + 1}`, ...d })));
        r.externals.forEach((u) => externalLinks.add(u));
      }
    }
    const r = await linkProblems(p, prod, landingIds);
    total += r.count;
    problems.push(...r.dead.map((d) => ({ route, ...d })), ...r.fakeButtons.map((f) => ({ route, fake: f })));
    r.externals.forEach((u) => externalLinks.add(u));
    await p.close();
  }
  return { pass: total > 50 && problems.length === 0, actual: `${total} links checked; problems: ${problems.length === 0 ? 'none' : JSON.stringify(problems)}` };
});

test('A2', '/tidy: every "item N" link in the checks table jumps to that item', {
  input: '/tidy, click each "item N" link',
  expected: 'each link has a matching #item-N and the item scrolls into view',
}, async () => {
  const { p } = await open('/tidy');
  const links = await p.$$eval('a[href^="#item-"]', (a) => a.map((x) => x.getAttribute('href')));
  const bad = [];
  for (const href of links) {
    await p.click(`a[href="${href}"]`);
    await sleep(400);
    const top = await p.$eval(href, (e) => e.getBoundingClientRect().top).catch(() => null);
    if (top === null || top < -5 || top > 900) bad.push(`${href}: top=${top}`);
  }
  const expected = tidy.checks.flatMap((c) => c.review_items).length;
  await p.close();
  return { pass: links.length === expected && bad.length === 0, actual: `${links.length} links (expected ${expected}); ${bad.length === 0 ? 'all land in view' : bad.join(', ')}` };
});

test('A3', 'A missing page and a trailing slash', {
  input: '/no-such-page, /tidy/, /sources/',
  expected: '"Page not found" with a link home; /tidy/ and /sources/ open their pages',
}, async () => {
  const out = [];
  let pass = true;
  for (const [path, h1] of [['/no-such-page', 'Page not found'], ['/tidy/', 'Tidy lab results'], ['/sources/', 'Guideline sources']]) {
    const { p, errors } = await open(path);
    const got = await text(p, 'h1');
    const home = await p.$('a[href="/"]');
    const ok = got.includes(h1) && home !== null && errors.length === 0;
    pass &&= ok;
    out.push(`${path}: "${got}"${ok ? '' : ' WRONG'}`);
    await p.close();
  }
  return { pass, actual: out.join('; ') };
});

test('A4', 'Download links for the data files give JSON, not the site page', {
  input: 'GET /data/{tidy,sample-site,sources,soil-criteria,accuracy,answers}.json and /img/credits.md',
  expected: 'HTTP 200, parseable JSON (credits: text), never the HTML fallback',
}, async () => {
  const out = [];
  let pass = true;
  for (const f of ['tidy', 'sample-site', 'sources', 'soil-criteria', 'accuracy', 'answers']) {
    const r = await fetch(`${prod}/data/${f}.json`);
    const body = await r.text();
    let ok = r.ok;
    try {
      JSON.parse(body);
    } catch {
      ok = false;
    }
    pass &&= ok;
    if (!ok) out.push(`${f}.json: ${r.status}`);
  }
  const credits = await fetch(`${prod}/img/credits.md`);
  const creditsText = await credits.text();
  const creditsOk = credits.ok && !creditsText.includes('<div id="root">');
  pass &&= creditsOk;
  return { pass, actual: out.length === 0 && creditsOk ? 'all load' : `${out.join(', ')} credits=${credits.status}` };
});

test('A5', 'Prepared answers: each "Open page N (PDF page M)" link opens PDF page M', {
  input: 'every citation in answers.json, rendered in the Try it box',
  expected: 'the #page= fragment of each link equals the PDF page named in its link text',
}, async () => {
  const { p } = await open('/');
  const chips = await p.$$eval('.chips button', (b) => b.length);
  const bad = [];
  let n = 0;
  for (let k = 0; k < chips; k++) {
    await p.click(`.chips button[data-a="${k}"]`);
    await openAllDetails(p);
    const links = await p.$$eval('#answer .cites a', (a) => a.map((x) => ({ href: x.getAttribute('href'), t: x.textContent })));
    for (const l of links) {
      const named = /PDF page (\d+)/.exec(l.t ?? '');
      if (named === null) continue;
      n++;
      const frag = /#page=(\d+)$/.exec(l.href ?? '');
      if (frag === null || frag[1] !== named[1]) bad.push(`${l.t} -> ${l.href}`);
    }
  }
  await p.close();
  return { pass: n > 10 && bad.length === 0, actual: `${n} page links; ${bad.length === 0 ? 'all match' : bad.join(' | ')}` };
});

test('A6', 'The walkthrough\'s PDF link is the same document /sources lists for the current values', {
  input: 'step 4 technical detail link vs sources.json rule "current"',
  expected: 'same URL',
}, async () => {
  const { p } = await open('/');
  const href = await p.$eval('[data-p="3"] .tech a', (a) => a.getAttribute('href')).catch(() => '');
  await p.close();
  const listed = sources.rules.find((r) => r.id === 'current')?.links.map((l) => l.url) ?? [];
  return { pass: listed.includes(href), actual: `${href} in [${listed.join(', ')}]` };
});

// ==================================================================================================================
// B. /tidy against tidy.json, item by item
// ==================================================================================================================

const prettyUnits = (s) => s.replace(/\bug\/L\b/g, 'µg/L');
const norm = (s) => s.replace(/\s+/g, ' ').trim();
/** How many things tidy checked and did not flag: tidy.json holds the list (older exports held only the count). */
const notFlaggedCount = typeof tidy.checked_not_flagged === 'number' ? tidy.checked_not_flagged : tidy.checked_not_flagged.length;

test('B1', '/tidy: the numbers at the top and the files table come from tidy.json', {
  input: '/tidy with the real tidy.json',
  expected: `stats ${tidy.files.length} files, ${tidy.results} results for ${tidy.samples} samples, ${tidy.review_items.length} items, ${notFlaggedCount} not flagged; one row per file with header row, rows read and date example`,
}, async () => {
  const { p } = await open('/tidy');
  const stats = await p.$$eval('.stats li', (li) => li.map((l) => l.textContent));
  const rows = await p.$$eval('section[aria-labelledby="files"] tbody tr', (tr) => tr.map((r) => r.textContent));
  await p.close();
  const wantStats = [`${tidy.files.length}files read`, `${tidy.results}lab results for ${tidy.samples} samples, in one table`, `${tidy.review_items.length}things for a person to check`, `${notFlaggedCount}things checked and not flagged`];
  const statsOk = wantStats.every((w, k) => stats[k] === w);
  const filesOk = rows.length === tidy.files.length && tidy.files.every((f, k) => [f.file, `header on row ${f.header_row}`, String(f.data_rows), f.date_example].every((x) => (rows[k] ?? '').includes(x)));
  return { pass: statsOk && filesOk, actual: `stats=${JSON.stringify(stats)}; file rows=${rows.length}${filesOk ? ' all match' : ` MISMATCH ${JSON.stringify(rows)}`}` };
});

test('B2', '/tidy: each review item\'s technical detail is the tool\'s own output, every evidence row included', {
  input: 'each #item-N with its drawer open',
  expected: 'heading, found, rule, source, decision equal tidy.json (units shown as µg/L); every file and row number in evidence is listed',
}, async () => {
  const { p } = await open('/tidy');
  await openAllDetails(p);
  const items = await p.$$eval('.ritem', (li) =>
    li.map((l) => ({ id: l.id, evidence: [...l.querySelectorAll('.evidence li')].map((e) => e.textContent ?? ''), fields: [...l.querySelectorAll('.tfield')].map((f) => ({ k: f.querySelector('.k')?.textContent ?? '', v: (f.textContent ?? '').slice((f.querySelector('.k')?.textContent ?? '').length) })) })),
  );
  await p.close();
  const bad = [];
  for (const item of tidy.review_items) {
    const shown = items.find((i) => i.id === `item-${item.number}`);
    if (shown === undefined) {
      bad.push(`item ${item.number} missing`);
      continue;
    }
    const get = (k) => norm(shown.fields.find((f) => f.k === k)?.v ?? '');
    for (const [k, want] of [["The tool's own heading", item.title], ['What was found', item.found], ['The rule', item.rule], ['Source', item.source], ['What the scientist decides', item.scientist_decides]]) {
      if (get(k) !== norm(prettyUnits(want))) bad.push(`item ${item.number} "${k}" differs`);
    }
    for (const line of item.evidence) {
      // The page groups rows by file: "lab_results.csv: rows 20, 21".
      const m = /^(.+?) row (\d+)$/.exec(line);
      const ok = shown.evidence.some((e) => (m === null ? e === line : e.startsWith(`${m[1]}: row`) && e.slice(m[1].length).split(/\D+/).includes(m[2])));
      if (!ok) bad.push(`item ${item.number} evidence "${line}" not shown`);
    }
  }
  return { pass: items.length === tidy.review_items.length && bad.length === 0, actual: `${items.length} items; ${bad.length === 0 ? 'all fields and evidence rows match' : bad.join('; ')}` };
});

test('B3', '/tidy: every number in each plain-English line is traceable to that item\'s own heading', {
  input: 'the plain line of each review item',
  expected: 'each number is in the heading, or is the heading\'s number rounded half up, or is the difference the line says it is',
}, async () => {
  const { p } = await open('/tidy');
  const lines = await p.$$eval('.ritem .plain', (e) => e.map((x) => x.textContent ?? ''));
  await p.close();
  const bad = [];
  tidy.review_items.forEach((item, k) => {
    const line = (lines[k] ?? '').replace(/^Item \d+: /, '');
    const heading = `${item.title} ${item.found}`;
    const inHeading = new Set((heading.match(/\d+(?:\.\d+)?/g) ?? []));
    const rounded = new Set([...inHeading].map((n) => {
      const m = /^(\d+)\.(\d)/.exec(n);
      return m === null ? n : String(BigInt(m[1]) + (m[2] >= '5' ? 1n : 0n));
    }));
    const diffs = new Set();
    const nums = [...inHeading].map((x) => /^\d+$/.test(x) ? BigInt(x) : null).filter((x) => x !== null);
    for (const a of nums) for (const b of nums) if (a > b) diffs.add(String(a - b));
    // Sample names such as SB3-1.5 and MB2 carry digits; take them out first.
    const bare = line.replace(/\b[A-Z]{1,3}\d+(?:-[\d.]+)?\b/g, ' ');
    for (const n of bare.match(/\d+(?:\.\d+)?/g) ?? []) {
      if (!inHeading.has(n) && !rounded.has(n) && !diffs.has(n)) bad.push(`item ${item.number}: "${n}" in "${line}"`);
    }
  });
  return { pass: lines.length === tidy.review_items.length && bad.length === 0, actual: bad.length === 0 ? `${lines.length} lines, every number traced` : bad.join(' | ') };
});

test('B4', '/tidy: the checks table and the "not checked" and ground-rules lists match tidy.json', {
  input: '/tidy',
  expected: 'one row per check with its item links; every not_checked reason and every note shown',
}, async () => {
  const { p } = await open('/tidy');
  const rows = await p.$$eval('section[aria-labelledby="checked"] tbody tr', (tr) => tr.map((r) => ({ t: r.textContent ?? '', items: [...r.querySelectorAll('a')].map((a) => a.getAttribute('href')) })));
  const body = norm(await text(p, 'main'));
  await p.close();
  const bad = [];
  tidy.checks.forEach((c, k) => {
    const row = rows[k];
    if (row === undefined || !row.t.includes(c.check) || !row.t.includes(c.what_was_checked)) bad.push(`check ${c.check}`);
    else if (JSON.stringify(row.items) !== JSON.stringify(c.review_items.map((n) => `#item-${n}`))) bad.push(`check ${c.check} items ${JSON.stringify(row.items)}`);
  });
  for (const n of tidy.not_checked) if (!body.includes(norm(n.reason))) bad.push(`not checked: ${n.what}`);
  for (const n of tidy.notes) if (!body.includes(norm(prettyUnits(n)))) bad.push(`note: ${n.slice(0, 40)}`);
  return { pass: rows.length === tidy.checks.length && bad.length === 0, actual: `${rows.length} check rows; ${bad.length === 0 ? 'all match' : bad.join('; ')}` };
});

test('B5', '/tidy: the tool\'s summary says the 24 not-flagged things are "each listed"; the page must list them', {
  input: '/tidy with the real tidy.json (checked_not_flagged is the list of 24)',
  expected: 'if the summary shown word for word says "each listed", the page shows that list',
}, async () => {
  const { p } = await open('/tidy');
  await openAllDetails(p);
  const says = (await text(p, 'main')).includes('each listed');
  const listed = await p.$$eval('section[aria-labelledby="checked"] details li', (li) => li.length);
  await p.close();
  return { pass: !says || listed >= notFlaggedCount, actual: `summary says "each listed": ${says}; items listed on the page: ${listed}` };
});

/** A tidy.json variant served in place of the real one. */
function tidyWith(change) {
  const copy = structuredClone(tidy);
  change(copy);
  return { '/data/tidy.json': json(copy) };
}

test('B6', 'Plain line for a field duplicate on a WATER sample must not call it a soil sample', {
  input: 'tidy.json with item 3 retitled "Field duplicate pair MB1 and QC2: PFOS RPD 41.2%, above 30%"',
  expected: 'the plain line does not say "soil"',
  where: 'web/src/lib/tidyPlain.ts RULES["field duplicates"].make',
}, async () => {
  const respond = tidyWith((t) => {
    const it = t.review_items.find((i) => i.check === 'field duplicates');
    it.title = 'Field duplicate pair MB1 and QC2: PFOS RPD 41.2%, above 30%';
    it.samples = ['MB1', 'QC2'];
  });
  const { p } = await open('/tidy', { respond });
  const line = await p.$eval('#item-3 .plain', (e) => e.textContent ?? '').catch(() => '');
  await p.close();
  return { pass: line !== '' && !/\bsoil\b/.test(line), actual: line };
});

test('B6b', 'Plain line for the real field duplicate names the kind of sample only from the tool\'s own units', {
  input: '/tidy with the real tidy.json (item 3: SB4-0.3 and QC1, results in mg/kg), then the same item with its units removed from "found"',
  expected: 'the real line says "soil sample"; with no unit to read it says "same sample" and names no kind',
}, async () => {
  const { p } = await open('/tidy');
  const real = await p.$eval('#item-3 .plain', (e) => e.textContent ?? '').catch(() => '');
  await p.close();
  const respond = tidyWith((t) => {
    const it = t.review_items.find((i) => i.check === 'field duplicates');
    it.found = it.found.replace(/ mg\/kg/g, '');
  });
  const { p: q } = await open('/tidy', { respond });
  const bare = await q.$eval('#item-3 .plain', (e) => e.textContent ?? '').catch(() => '');
  await q.close();
  return { pass: /same soil sample/.test(real) && /same sample/.test(bare) && !/soil|water/.test(bare), actual: `real: "${real}" | no units: "${bare}"` };
});

test('B7', 'Landing preview and /tidy follow tidy.json when it changes (3 items, 2 files)', {
  input: 'tidy.json cut to its first 3 review items and first 2 files',
  expected: 'preview shows 3 items and says 3; /tidy says 3; the preview does not claim three files were read',
  where: 'web/src/content/landing.ts TIDY_PREVIEW.files and label',
}, async () => {
  const respond = tidyWith((t) => {
    t.review_items = t.review_items.slice(0, 3);
    t.files = t.files.slice(0, 2);
    t.checks = t.checks.map((c) => ({ ...c, review_items: c.review_items.filter((n) => n <= 3) }));
  });
  const { p } = await open('/', { respond });
  const preview = await p.$eval('.app[aria-label^="Preview of the Tidy"]', (a) => ({ label: a.getAttribute('aria-label') ?? '', items: a.querySelectorAll('.issues li').length, files: a.querySelectorAll('.filelist li').length, count: a.querySelector('.app-result')?.getAttribute('data-count') }));
  await p.close();
  const { p: q } = await open('/tidy', { respond });
  const h = await text(q, '#items');
  await q.close();
  const pass = preview.items === 3 && preview.count === '3' && h.startsWith('3 ') && preview.files === 2 && !/Three files/.test(preview.label);
  return { pass, actual: `preview items=${preview.items} count=${preview.count} files=${preview.files}; label="${preview.label}"; /tidy heading="${h}"` };
});

test('B8', 'A broken tidy.json is reported plainly, not shown as an empty result', {
  input: 'tidy.json answered with "{not json" and, separately, with {"site":"FDS-01"}',
  expected: '/tidy shows a plain error; the landing preview says the result could not be loaded; no script errors',
}, async () => {
  const out = [];
  let pass = true;
  for (const body of ['{not json', JSON.stringify({ site: 'FDS-01' })]) {
    const respond = { '/data/tidy.json': json(body) };
    const { p, errors } = await open('/tidy', { respond });
    const msg = await text(p, '.status-line.error');
    await p.close();
    const { p: q, errors: e2 } = await open('/', { respond });
    const prev = await text(q, '.app[aria-label^="Preview of the Tidy"] .app-result');
    await q.close();
    const ok = msg.includes('tidy.json') && prev.includes('could not be loaded') && errors.length + e2.length === 0;
    pass &&= ok;
    out.push(`"${msg}" / preview "${prev}"`);
  }
  return { pass, actual: out.join(' | ') };
});

// ==================================================================================================================
// C. Numbers and claims on the landing page against the data files
// ==================================================================================================================

const round = (iso) => site.rounds.find((r) => r.date.startsWith(iso));
const value = (r, analyte) => r.results.find((x) => x.analyte === analyte)?.reported;
const limit = (rule, key) => sources.rules.find((r) => r.id === rule)?.limits.find((l) => l.key === key)?.value;
const MONTH = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTH_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

test('C1', 'Landing preview lists the same 6 items as tidy.json, in the same order', {
  input: 'the "Tidy lab results" preview with the real tidy.json',
  expected: '6 items, "6 things for you to check", each line naming what its item is about',
}, async () => {
  const { p } = await open('/');
  const got = await p.$eval('.app[aria-label^="Preview of the Tidy"]', (a) => ({ tail: a.querySelector('.app-result')?.textContent ?? '', items: [...a.querySelectorAll('.issues li')].map((l) => l.textContent ?? '') }));
  await p.close();
  const markers = { 'sample ids': ['SB3-1.5', 'SB3-15'], units: ['unit'], 'field duplicates': ['62%'], 'holding times': ['time limit'], blanks: ['PFOS'], 'LOR against criteria': ['detection limit'] };
  const bad = tidy.review_items.filter((it, k) => !(markers[it.check] ?? []).every((m) => (got.items[k] ?? '').includes(m))).map((it) => it.number);
  const pass = got.items.length === tidy.review_items.length && got.tail.includes(`${tidy.review_items.length} things for you to check`) && bad.length === 0;
  return { pass, actual: `${got.items.length} items; "${got.tail}"; ${bad.length === 0 ? 'order and content match' : `mismatch at items ${bad.join(', ')}`}` };
});

test('C2', 'Every number in the walkthrough and the "Check a report" preview matches the data files', {
  input: 'chart, step 1 detail, draft numbers, flag text, rule rulers, preview bubble',
  expected: 'PFOS and PFHxS for MB2 as in sample-site.json, test dates as sampled, 0.07 and 0.008 as in sources.json, 0.057 = PFOS + PFHxS (Sep 2025)',
}, async () => {
  const { p } = await open('/');
  const got = await p.evaluate(() => ({
    bars: [...document.querySelectorAll('.chart .col .val')].map((e) => e.textContent),
    labels: [...document.querySelectorAll('.xl span')].map((e) => e.textContent),
    dl: Object.fromEntries([...document.querySelectorAll('[data-p="0"] .tech dt')].map((dt) => [dt.textContent, dt.nextElementSibling?.textContent ?? ''])),
    draft: document.querySelector('[data-p="1"] .doc p')?.textContent ?? '',
    flag: document.querySelector('[data-p="2"] .flag p')?.textContent ?? '',
    rulers: [...document.querySelectorAll('.ruler')].map((r) => ({ limit: r.querySelector('.limit em')?.textContent ?? '', verdict: r.querySelector('.verdict')?.textContent ?? '' })),
    bubble: document.querySelector('.bubble p')?.textContent ?? '',
    preview: document.querySelector('.docpane')?.textContent ?? '',
  }));
  await p.close();
  const bad = [];
  const rounds = site.rounds;
  const pfos = rounds.map((r) => value(r, 'PFOS'));
  const pfhxs = rounds.map((r) => value(r, 'PFHxS'));
  if (JSON.stringify(got.bars) !== JSON.stringify(pfos)) bad.push(`chart ${got.bars} vs ${pfos}`);
  const wantLabels = rounds.map((r) => `${MONTH[Number(r.date.slice(5, 7)) - 1]} ${r.date.slice(0, 4)}`);
  if (JSON.stringify(got.labels) !== JSON.stringify(wantLabels)) bad.push(`chart labels ${got.labels}`);
  const wantDates = rounds.map((r) => `${Number(r.date.slice(8, 10))} ${MONTH[Number(r.date.slice(5, 7)) - 1]} ${r.date.slice(0, 4)}`).join(', ');
  if (got.dl.Tests !== wantDates) bad.push(`tests "${got.dl.Tests}" vs "${wantDates}"`);
  if (got.dl.PFHxS !== `${pfhxs.join(', ')} µg/L`) bad.push(`PFHxS "${got.dl.PFHxS}"`);
  const dls = [...new Set(rounds.flatMap((r) => r.results.map((x) => x.detection_limit)))];
  if (got.dl['Detection limit'] !== `${dls.join(', ')} µg/L`) bad.push(`detection limit "${got.dl['Detection limit']}"`);
  for (const m of got.draft.matchAll(/In (\w+) (\d{4}), PFOS was ([\d.]+)/g)) {
    const month = String(MONTH_LONG.indexOf(m[1]) + 1).padStart(2, '0');
    const r = round(`${m[2]}-${month}`);
    if (r === undefined || value(r, 'PFOS') !== m[3]) bad.push(`draft "${m[0]}"`);
  }
  const current = limit('current', 'PFOS');
  const old = limit('nemp-3.0', 'PFOS+PFHxS');
  if (!got.draft.includes(`guideline value of ${current}`)) bad.push('draft guideline value');
  const sep = round('2025-09');
  const nov = round('2024-11');
  const wantFlag = `from ${value(nov, 'PFOS')} to ${value(sep, 'PFOS')}`;
  if (!got.flag.includes(wantFlag) || !got.bubble.includes(wantFlag)) bad.push(`flag/bubble "${got.flag}" / "${got.bubble}"`);
  const sum = addDec(value(sep, 'PFOS'), value(sep, 'PFHxS'));
  if (got.rulers[0]?.limit !== `limit ${old}` || !got.rulers[0]?.verdict.startsWith(`${sum} in the water`)) bad.push(`old ruler ${JSON.stringify(got.rulers[0])} (sum ${sum})`);
  if (got.rulers[1]?.limit !== `limit ${current}` || !got.rulers[1]?.verdict.startsWith(`${value(sep, 'PFOS')} in the water`)) bad.push(`new ruler ${JSON.stringify(got.rulers[1])}`);
  const under = cmpDec(sum, old) < 0;
  const over = cmpDec(value(sep, 'PFOS'), current) > 0;
  if (!got.rulers[0]?.verdict.includes(under ? 'under' : 'over') || !got.rulers[1]?.verdict.includes(over ? 'over' : 'under')) bad.push('ruler verdicts');
  if (!got.preview.includes(`${value(sep, 'PFOS')}\u00a0µg/L`) || !got.preview.includes(`${current}\u00a0µg/L`)) bad.push('preview numbers');
  return { pass: bad.length === 0, actual: bad.length === 0 ? `all ${pfos.length + pfhxs.length + 8} numbers match` : bad.join(' | ') };
});

test('C3', 'The rule rulers are drawn to scale', {
  input: 'step 4 rulers on the 0 to 0.08 µg/L scale',
  expected: 'bar width = value / 0.08 and limit mark = limit / 0.08, to 0.01%',
}, async () => {
  const { p } = await open('/');
  const rulers = await p.$$eval('.ruler', (r) => r.map((x) => ({ fill: x.querySelector('.fillbar')?.style.width ?? '', at: x.querySelector('.limit')?.style.left ?? '' })));
  const scale = await p.$$eval('.scale span', (s) => s.map((x) => x.textContent ?? ''));
  await p.close();
  const full = (scale.at(-1) ?? '').replace(/[^\d.]/g, '');
  const sep = round('2025-09');
  const want = [
    [addDec(value(sep, 'PFOS'), value(sep, 'PFHxS')), limit('nemp-3.0', 'PFOS+PFHxS')],
    [value(sep, 'PFOS'), limit('current', 'PFOS')],
  ];
  const bad = [];
  want.forEach(([v, l], k) => {
    if (styleHundredths(rulers[k]?.fill ?? '') !== percentHundredths(v, full)) bad.push(`ruler ${k + 1} fill ${rulers[k]?.fill} for ${v}`);
    if (styleHundredths(rulers[k]?.at ?? '') !== percentHundredths(l, full)) bad.push(`ruler ${k + 1} limit ${rulers[k]?.at} for ${l}`);
  });
  return { pass: full === '0.08' && bad.length === 0, actual: bad.length === 0 ? `drawn to scale (${JSON.stringify(rulers)})` : bad.join('; ') };
});

test('C4', 'Evidenceline\'s own suggested sentence (step 5) is true of the data shown and picks no rule', {
  input: 'step 5 "Suggested sentence", with the step 1 chart showing May 2026 PFOS 0.006 µg/L',
  expected: 'no "now above the drinking-water guideline" while the latest shown result (0.006) is below 0.008, and no single rule called "the" guideline',
  where: 'web/src/content/landing.ts SUGGESTED_SENTENCE and ORIGINAL_SENTENCE (scope them to the September 2025 round and name the rule)',
}, async () => {
  const { p } = await open('/');
  const sentence = await text(p, '#sugg');
  await p.close();
  const latest = site.rounds.at(-1);
  const below = cmpDec(value(latest, 'PFOS'), limit('current', 'PFOS')) < 0;
  const claimsNowAbove = /\bnow above\b/i.test(sentence);
  const picksRule = /\bthe drinking-water guideline\b/i.test(sentence);
  return { pass: !(claimsNowAbove && below) && !picksRule, actual: `"${sentence}" (latest round ${latest.date}: PFOS ${value(latest, 'PFOS')})` };
});

const FOOTNOTE_A = 'Table 4 footnote a (NEMP 3.0, PDF page 57): "Where the criteria refer to the sum of PFOS and PFHxS, this means concentrations of PFOS only, PFHxS only, and the sum of the two."';

test('C5', '/sources: the NEMP 3.0 note agrees with Table 4 footnote a', {
  input: '/sources, the PFOS and PFHxS row of PFAS NEMP 3.0',
  expected: `no claim that the rule has no value for PFOS or PFHxS on its own (${FOOTNOTE_A})`,
  where: 'src/evidenceline/data/guidelines.json (the note), then sources.json via scripts/export_web_data.py; the same wording is in core.py, checker.py, drafting.py and server.py per the integrator',
}, async () => {
  const { p } = await open('/sources');
  const t = norm(await text(p, 'main'));
  await p.close();
  const hit = around(t, /no separate value for PFOS or PFHxS on its own/);
  return { pass: hit.length === 0, actual: hit.length === 0 ? 'no such claim' : hit.join(' | ') };
});

test('C6', 'Try it: the first suggested answer agrees with Table 4 footnote a', {
  input: 'click "What is the drinking-water limit for PFOS?" (prepared answer, "passed every check")',
  expected: `no claim that NEMP 3.0 has no value for PFOS on its own (${FOOTNOTE_A})`,
  where: 'src/evidenceline/data/guidelines.json note, then web/public/data/answers.json via scripts/precompute_answers.py',
}, async () => {
  const { p } = await open('/');
  await p.click('.chips button[data-a="0"]');
  const t = norm(await text(p, '#answer'));
  await p.close();
  const hit = around(t, /no (separate )?value for PFOS( or PFHxS)? on its own/);
  return { pass: hit.length === 0, actual: hit.length === 0 ? 'no such claim' : hit.join(' | ') };
});

test('C7', 'The "Ask the guidelines" preview matches the verified values', {
  input: 'the hand-drawn "Ask the guidelines" preview',
  expected: '0.008 µg/L for PFOS on its own from NEMP 3.1 Table 4 page 49; 0.07 µg/L for the NEMP 3.0 sum',
}, async () => {
  const { p } = await open('/');
  const t = norm(await p.$eval('.app[aria-label^="Preview of the Ask"]', (a) => a.textContent ?? ''));
  const note = norm(await p.$eval('.app[aria-label^="Preview of the Ask"] .note', (a) => a.textContent ?? ''));
  await p.close();
  const cur = sources.rules.find((r) => r.id === 'current');
  const pass = t.includes(`${limit('current', 'PFOS')} µg/L`) && note.includes(`${limit('nemp-3.0', 'PFOS+PFHxS')} µg/L`) && t.includes(`${cur.table}, page ${cur.page}`);
  return { pass, actual: t.slice(0, 260) };
});

test('C8', 'The promise "every result opens its lab file row" is kept on /sample-site', {
  input: 'walkthrough step 1 technical detail ("Every result opens its lab file row on the full sample site") and the trust pillar ("each one links to where it came from"), then /sample-site',
  expected: 'each PFOS and PFHxS result on /sample-site is a link or control that opens its row, or the wording promises only that the row is named',
  where: 'web/src/content/landing.ts STEPS[0].tech and PILLARS[0], or web/src/pages/SampleSite.tsx Value()',
}, async () => {
  const { p } = await open('/sample-site');
  const cells = await p.$$eval('section[aria-labelledby="results"] tbody td.num', (td) => td.map((c) => ({ text: c.textContent ?? '', control: !!c.querySelector('a, button') })));
  await p.close();
  const values = cells.filter((c) => /row \d+/.test(c.text));
  return { pass: values.length > 0 && values.every((c) => c.control), actual: `${values.length} results with a row reference; ${values.filter((c) => c.control).length} of them open anything` };
});

test('C9', 'Each result on /sample-site opens the lab file row it names, and that row holds the value shown', {
  input: 'press every PFOS and PFHxS result on /sample-site; then the first one again with its lab file row changed',
  expected: 'each opened row shows the analyte and result from sample-site.json; a row that does not match is refused with a plain message',
}, async () => {
  const want = site.rounds.flatMap((r) => ['PFOS', 'PFHxS'].map((a) => ({ file: r.file, row: r.results.find((x) => x.analyte === a).row, analyte: a, reported: value(r, a) })));
  const { p } = await open('/sample-site');
  const buttons = await p.$$('section[aria-labelledby="results"] tbody td.num button');
  const got = [];
  for (const b of buttons) {
    await b.click();
    const id = await b.evaluate((e) => e.getAttribute('aria-controls'));
    await p.waitForFunction((x) => !!document.getElementById(x)?.querySelector('dl, .error'), { timeout: 5000 }, id).catch(() => undefined);
    got.push(
      await p.evaluate((x) => {
        const panel = document.getElementById(x);
        const cells = Object.fromEntries([...(panel?.querySelectorAll('dt') ?? [])].map((dt) => [dt.textContent, dt.nextElementSibling?.textContent ?? '']));
        return { title: panel?.querySelector('.k')?.textContent ?? '', cells, error: panel?.querySelector('.error')?.textContent ?? '' };
      }, id),
    );
  }
  await p.close();
  const bad = [];
  want.forEach((w, k) => {
    const g = got[k];
    if (g === undefined || g.error !== '' || g.cells.analyte !== w.analyte || g.cells.result !== w.reported || !g.title.includes(`${w.file}, row ${w.row}`)) bad.push(`${w.file} row ${w.row}: ${JSON.stringify(g)}`);
  });
  // A lab file that no longer holds the value shown is refused, not shown.
  const first = want[0];
  const lines = readFileSync(join(dataDir, 'lab', first.file), 'utf8').split('\n');
  lines[first.row - 1] = lines[first.row - 1].replace(`,${first.reported},`, ',0.999,');
  const { p: q } = await open('/sample-site', { respond: { [`/data/lab/${first.file}`]: { status: 200, contentType: 'text/csv', body: lines.join('\n') } } });
  await q.click('section[aria-labelledby="results"] tbody td.num button');
  await q.waitForSelector('.labrow .error, .labrow dl', { timeout: 5000 }).catch(() => undefined);
  const refused = await text(q, '.labrow .error');
  await q.close();
  if (!refused.includes('does not hold')) bad.push(`changed row not refused: "${refused}"`);
  return { pass: buttons.length === want.length && bad.length === 0, actual: bad.length === 0 ? `${buttons.length} rows opened and matched; a changed row is refused` : bad.join(' | ') };
});

// ==================================================================================================================
// D. Try it: prepared answers and the live box against a mocked service (production build, same-origin /api/ask)
// ==================================================================================================================

test('D1', 'Prepared answers show the date and model they were made with', {
  input: 'click each suggested question',
  expected: 'every answer\'s small print has 24 September 2026; model answers name claude-sonnet-5; answers with no model say no model was called',
}, async () => {
  const { p } = await open('/');
  const n = await p.$$eval('.chips button', (b) => b.length);
  const top = await text(p, '#prepared-note');
  const bad = [];
  for (let k = 0; k < n; k++) {
    await p.click(`.chips button[data-a="${k}"]`);
    const note = await text(p, '#answer .prepared');
    const entry = answers.answers[k];
    if (!note.includes('24 September 2026')) bad.push(`${k + 1}: no date`);
    if (entry.result.model !== null && !note.includes(entry.result.model)) bad.push(`${k + 1}: no model`);
    if (entry.result.model === null && !/No model was called/.test(note)) bad.push(`${k + 1}: does not say no model was called`);
  }
  await p.close();
  const pass = n === answers.answers.length && top.includes('24 Sep 2026') && top.includes(answers.model) && bad.length === 0;
  return { pass, actual: `${n} answers; top note "${top}"; ${bad.length === 0 ? 'each dated with its model' : bad.join(', ')}` };
});

test('D2', 'Typed questions: prepared match ignores case and spacing; blank does nothing; 500 character cap', {
  input: '"  WHAT is the drinking-water   limit for PFOS?  ", then "   ", then 600 characters',
  expected: 'prepared answer with no request; blank sends nothing and shows nothing; the box holds 500 characters',
}, async () => {
  const { p, posted } = await open('/', { respond: { '/api/ask': () => json(liveResult()) } });
  const a = await ask(p, '  WHAT is the drinking-water   limit for PFOS?  ');
  await clearBox(p);
  await p.type('#q', '   ');
  await p.keyboard.press('Enter');
  await sleep(200);
  const afterBlank = await p.$eval('#answer', (e) => e.getAttribute('data-state'));
  await clearBox(p);
  await p.keyboard.sendCharacter('x'.repeat(600));
  const len = await p.$eval('#q', (i) => i.value.length);
  await p.close();
  const asks = posted.filter((x) => x.url.endsWith('/api/ask')).length;
  return { pass: a.state === 'prepared' && asks === 0 && afterBlank === 'prepared' && len === 500, actual: `state=${a.state}, requests=${asks} ${JSON.stringify(posted.map((x) => x.body))}, after blank=${afterBlank}, length=${len}` };
});

/** Ask one live question with /api/ask answered by `reply`. */
async function live(reply, { question = 'How are health investigation levels used?', vp = DESKTOP, beforeLoad = null, timeout } = {}) {
  const { p, errors, posted, held } = await open('/', { vp, respond: { '/api/ask': reply }, beforeLoad });
  const got = await ask(p, question, { timeout });
  const button = await p.$eval('#askf button', (b) => b.disabled);
  return { p, errors, posted, held, got, button };
}

test('D3', 'Service missing: /api/ask answers 404 with an HTML page', {
  input: 'POST /api/ask -> 404 text/html',
  expected: 'a plain error with the status and a pointer to the prepared examples; Ask enabled again',
}, async () => {
  const r = await live(() => ({ status: 404, contentType: 'text/html', body: '<!doctype html><title>Not found</title>' }));
  await r.p.close();
  const pass = r.got.state === 'error' && r.got.text.includes('HTTP 404') && r.got.text.includes('prepared example') && !r.button && r.errors.length === 0;
  return { pass, actual: `${r.got.state}: "${r.got.text}"` };
});

test('D4', 'Service slow: no reply at all', {
  input: 'POST /api/ask held open; the page\'s 60 s timer is sped up to 0.4 s',
  expected: 'loading state with Ask disabled, then "took too long" and Ask enabled again',
}, async () => {
  const beforeLoad = (p) =>
    p.evaluateOnNewDocument(() => {
      const real = window.setTimeout.bind(window);
      window.setTimeout = (fn, ms, ...rest) => real(fn, typeof ms === 'number' && ms >= 30_000 ? 400 : ms, ...rest);
    });
  const { p, held } = await open('/', { respond: { '/api/ask': null }, beforeLoad });
  await p.type('#q', 'How are health investigation levels used?');
  await p.keyboard.press('Enter');
  await sleep(100);
  const during = await p.evaluate(() => ({ state: document.querySelector('#answer')?.getAttribute('data-state'), busy: document.querySelector('#answer')?.getAttribute('aria-busy'), disabled: document.querySelector('#askf button')?.disabled }));
  await p.waitForFunction(() => document.querySelector('#answer')?.getAttribute('data-state') === 'error', { timeout: 5000 }).catch(() => undefined);
  const after = await p.$eval('#answer', (a) => ({ state: a.getAttribute('data-state'), text: a.textContent ?? '' }));
  const disabled = await p.$eval('#askf button', (b) => b.disabled);
  held.forEach((r) => r.abort().catch(() => undefined));
  await p.close();
  const pass = during.state === 'loading' && during.busy === 'true' && during.disabled && after.state === 'error' && after.text.includes('took too long') && !disabled;
  return { pass, actual: `during ${JSON.stringify(during)}; after ${after.state}: "${after.text}", Ask disabled=${disabled}` };
});

test('D5', 'The loading message matches how long the page actually waits', {
  input: 'the loading line vs the timeout in lib/answers.ts, the README and the proxy',
  expected: 'the line promises no less than the page waits',
  where: 'web/src/content/landing.ts TRY.loading and web/src/lib/answers.ts TIMEOUT_MS',
}, async () => {
  const src = readFileSync(join(webDir, 'src', 'lib', 'answers.ts'), 'utf8');
  const ms = Number((/TIMEOUT_MS = ([\d_]+)/.exec(src)?.[1] ?? '0').replace(/_/g, ''));
  const copy = readFileSync(join(webDir, 'src', 'content', 'landing.ts'), 'utf8');
  const line = /loading: '([^']+)'/.exec(copy)?.[1] ?? '';
  const promised = /half a minute/.test(line) ? 30_000 : /a minute/.test(line) ? 60_000 : Infinity;
  return { pass: ms <= promised, actual: `page waits ${ms / 1000} s; the line says "${line}"` };
});

test('D6', 'Rate limited: the wait is worded from Retry-After or retry_after', {
  input: '429 with Retry-After 120; 429 with Retry-After 7200; 429 with only {"retry_after": 3600} (the API\'s shape)',
  expected: '"in about 2 minutes", "in about 2 hours", "in about 60 minutes", each pointing to the prepared examples',
}, async () => {
  const out = [];
  let pass = true;
  for (const [reply, want] of [
    [json({ error: 'Too many questions: the limit is 10 questions per hour.' }, 429, { 'Retry-After': '120' }), 'in about 2 minutes'],
    [json({ error: 'Too many questions.' }, 429, { 'Retry-After': '7200' }), 'in about 2 hours'],
    [json({ error: 'Too many questions.', retry_after: 3600 }, 429), 'in about 60 minutes'],
  ]) {
    const r = await live(() => reply);
    await r.p.close();
    const ok = r.got.state === 'rate-limited' && r.got.text.includes(want) && r.got.text.includes('prepared example');
    pass &&= ok;
    out.push(`"${r.got.text}"`);
  }
  return { pass, actual: out.join(' | ') };
});

test('D7', 'Paused: 503 from the proxy when the service is not connected', {
  input: 'POST /api/ask -> 503 {"detail": "The live service is not connected yet. Please try again later."}',
  expected: 'the service\'s reason and that the prepared examples still work',
}, async () => {
  const r = await live(() => json({ detail: 'The live service is not connected yet. Please try again later.' }, 503));
  await r.p.close();
  const pass = r.got.state === 'paused' && r.got.text.includes('not connected yet') && r.got.text.includes('prepared examples above still work');
  return { pass, actual: `${r.got.state}: "${r.got.text}"` };
});

test('D8', 'A paused or failed result (HTTP 200) must not be labelled "Answered live"', {
  input: '200 with status "paused" (daily cap) and, separately, status "error" (model failed), both with passages',
  expected: 'the heading says paused / could not be answered, and the small print does not say it was answered',
  where: 'web/src/landing/TryIt.tsx Answer() case "live" (note chosen without looking at view.kind)',
}, async () => {
  const out = [];
  let pass = true;
  for (const status of ['paused', 'error']) {
    const r = await live(() => json(liveResult({ status, answer: null, explanation: "Live answers are paused: today's limit of 300 answers has been reached. The passages are shown instead.", model: 'claude-sonnet-5' })));
    await r.p.close();
    const ok = /paused|could not be answered/.test(r.got.text) && !r.got.text.includes('Answered live');
    pass &&= ok;
    out.push(`${status}: ${around(r.got.text, /Answered live/, 10).join('') || 'no "Answered live"'}`);
  }
  return { pass, actual: out.join(' | ') };
});

test('D9', 'Server errors: long messages are not quoted, dashes never shown', {
  input: '500 with a 300-character detail; 500 with "Upstream \u2014 failed \u2013 retry"; 200 with invalid JSON; connection refused',
  expected: 'HTTP 500 without the long text; the short one quoted without dashes; "cannot show"; "could not be reached"',
}, async () => {
  const out = [];
  let pass = true;
  const runs = [
    [() => json({ detail: 'x'.repeat(300) }, 500), (t) => t.includes('HTTP 500') && !t.includes('xxxxxxxxxx')],
    [() => json({ detail: 'Upstream \u2014 failed \u2013 retry' }, 500), (t) => t.includes('Upstream, failed, retry') && !DASHES.test(t)],
    [() => ({ status: 200, contentType: 'application/json', body: '{"status": "answ' }), (t) => t.includes('cannot show')],
    [() => 'abort', (t) => t.includes('could not be reached')],
  ];
  for (const [reply, check] of runs) {
    const r = await live(reply);
    await r.p.close();
    const ok = r.got.state === 'error' && check(r.got.text) && !r.button;
    pass &&= ok;
    out.push(`"${r.got.text.slice(0, 90)}"`);
  }
  return { pass, actual: out.join(' | ') };
});

test('D10', 'A slow live answer that arrives after the visitor picked a suggested question is dropped', {
  input: 'type a question (reply held), click suggested question 3, then release the live reply',
  expected: 'suggested answer 3 stays on screen',
}, async () => {
  const { p, held } = await open('/', { respond: { '/api/ask': null } });
  await p.type('#q', 'How are health investigation levels used?');
  await p.keyboard.press('Enter');
  await sleep(150);
  await p.click('.chips button[data-a="2"]');
  for (const r of held) await r.respond(json(liveResult({ answer: 'LATE LIVE ANSWER [1].' }))).catch(() => undefined);
  await sleep(500);
  const got = await p.$eval('#answer', (a) => ({ state: a.getAttribute('data-state'), text: a.textContent ?? '' }));
  await p.close();
  return { pass: got.state === 'prepared' && !got.text.includes('LATE LIVE ANSWER'), actual: `${got.state}; late answer shown: ${got.text.includes('LATE LIVE ANSWER')}` };
});

test('D11', 'Hostile live answer: markup is shown as text and a javascript: link is not a link', {
  input: 'answer "<img src=x onerror=window.__pwned=1>", a note with <script>, citation link "javascript:window.__pwned=2"',
  expected: 'nothing runs, no <img> or javascript: href inside the answer',
}, async () => {
  const r = await live(() =>
    json(
      liveResult({
        answer: 'See <img src=x onerror="window.__pwned=1"> [1].',
        notes: ['<script>window.__pwned=3</script>'],
        citations: [{ ...liveResult().citations[0], link: 'javascript:window.__pwned=2' }],
      }),
    ),
  );
  await sleep(300);
  const got = await r.p.evaluate(() => ({ pwned: window.__pwned ?? null, imgs: document.querySelectorAll('#answer img').length, js: document.querySelectorAll('#answer a[href^="javascript"]').length }));
  await r.p.close();
  return { pass: got.pwned === null && got.imgs === 0 && got.js === 0 && r.got.text.includes('<img'), actual: JSON.stringify(got) };
});

test('D12', 'No em or en dash anywhere in a live answer (outside quoted excerpts)', {
  input: 'live answer whose citation section, document, edition, value source and check name contain "\u2013" or "\u2014"',
  expected: 'no dash in the answer box except inside the quoted excerpt',
  where: 'web/src/lib/answers.ts readCitations(), readValues(), readChecks()',
}, async () => {
  const r = await live(() =>
    json(
      liveResult({
        citations: [{ ...liveResult().citations[0], document: 'ASC NEPM \u2013 Schedule B1', edition: '1999 \u2013 2013', section: '2.1 Tier 1 \u2014 screening', excerpt: 'Quoted \u2013 text.' }],
        guideline_values: [{ marker: 'G1', rule_name: 'Rule \u2013 A', compared_quantity: 'PFOS', value: '0.008', unit: 'ug/L', source_document: 'NEMP 3.1 \u2014 2026', table: 'Table 4', page: '49', page_basis: 'printed page', note: 'n' }],
        verification: { ran: true, passed: true, checks: [{ name: 'numbers \u2013 traced', passed: true, detail: 'ok' }], summary: '' },
      }),
    ),
  );
  await openAllDetails(r.p);
  const shown = await r.p.evaluate(() => {
    const box = document.querySelector('#answer').cloneNode(true);
    box.querySelectorAll('.excerpt').forEach((e) => e.remove());
    return box.textContent ?? '';
  });
  await r.p.close();
  const hits = around(shown, /[\u2013\u2014]/, 20);
  return { pass: hits.length === 0, actual: hits.length === 0 ? 'no dashes' : hits.join(' | ') };
});

test('D13', 'Copy says "Copied" only when the link was copied', {
  input: 'production build; the browser refuses clipboard access (writeText rejects); press Copy',
  expected: 'the button does not claim "Copied" (it says the copy failed or selects the link instead)',
  where: 'web/src/landing/TryIt.tsx ConnectBox copy()',
}, async () => {
  const beforeLoad = (p) =>
    p.evaluateOnNewDocument(() => {
      Object.defineProperty(navigator, 'clipboard', { value: { writeText: () => Promise.reject(new DOMException('denied', 'NotAllowedError')) }, configurable: true });
    });
  const { p } = await open('/', { beforeLoad });
  await p.$eval('#copy', (b) => b.scrollIntoView());
  await p.click('#copy');
  await sleep(200);
  const label = await text(p, '#copy');
  await p.close();
  return { pass: label !== 'Copied', actual: `button reads "${label}" after a refused copy` };
});

// ==================================================================================================================
// E. Accuracy page
// ==================================================================================================================

test('E1', '/accuracy shows the real numbers from accuracy.json, failures and misses included', {
  input: '/accuracy with the real accuracy.json',
  expected: 'test total, every area\'s counts, each whole question set\'s hit@1 in the summary, every set\'s missed questions one click away, the notes the re-checks did not confirm as failure rows, the build accuracy.json names ("commit abc1234" or "local build")',
}, async () => {
  const { p } = await open('/accuracy');
  const passed = accuracy.suites.reduce((n, s) => n + s.passed, 0);
  const failed = accuracy.suites.reduce((n, s) => n + s.failed, 0);
  const short = norm(await text(p, '[data-results]'));
  const rows = await p.$$eval('section[aria-labelledby="tests"] tbody tr', (tr) => tr.map((r) => [...r.querySelectorAll('th,td')].map((c) => (c.textContent ?? '').trim())));
  const wholeSets = accuracy.search.filter((s) => !s.part_of);
  const missesClosed = await p.$$eval('details.misses', (d) => d.map((x) => x.open));
  await openAllDetails(p);
  const missText = await p.$$eval('details.misses', (d) => d.map((x) => x.querySelectorAll('li').length));
  const noteRows = await p.$$eval('#notes + .note + .tablewrap tbody tr.failrow', (r) => r.length);
  await p.close();
  const bad = [];
  if (!short.includes(`${passed} of ${passed + failed} automated tests passed`)) bad.push('total');
  accuracy.suites.forEach((s, k) => {
    const row = rows[k] ?? [];
    if (!row[0]?.startsWith(s.name) || row[1] !== String(s.passed) || row[2] !== String(s.failed)) bad.push(`row ${s.name}`);
  });
  for (const set of wholeSets) {
    const hit1 = set.metrics.find((m) => m.name === 'hit@1');
    if (!short.includes(`${set.label}: the right page came first for ${hit1.hits} of ${hit1.of} questions`)) bad.push(`${set.set} hit@1`);
  }
  const wantMisses = accuracy.search.map((s) => (s.misses ?? []).length).filter((n) => n > 0);
  if (JSON.stringify(missText) !== JSON.stringify(wantMisses) || missesClosed.some((o) => o)) bad.push(`misses ${missText} closed=${missesClosed}`);
  if (noteRows !== accuracy.verification.notes.filter((n) => n.confirmed_now !== true).length) bad.push(`note rows ${noteRows}`);
  if (!short.includes(accuracy.build)) bad.push(`build (${accuracy.build})`);
  return { pass: bad.length === 0, actual: bad.length === 0 ? `${passed} of ${passed + failed}; ${rows.length} areas; misses ${missText}; ${noteRows} note failure rows` : bad.join(', ') };
});

test('E2', 'A known product failure (pytest xfail) is shown as a failure on /accuracy, not as "all passed"', {
  input: 'a real pytest JUnit report with one xfail(strict=True) "PRODUCT FAILURE (critical)" test, read by scripts/build_accuracy.py parse_junit(), then shown on /accuracy',
  expected: 'the area shows a failure (or a "known failure"), never "all passed"',
}, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'evl-adv-'));
  try {
    writeFileSync(join(dir, 'test_known.py'), 'import pytest\n\n@pytest.mark.xfail(strict=True, reason="PRODUCT FAILURE (critical): example")\ndef test_known_failure():\n    assert False\n\ndef test_ok():\n    assert True\n');
    writeFileSync(join(dir, 'run.py'), [
      'import importlib.util, json, pathlib, subprocess, sys',
      `repo = pathlib.Path(r"${repoDir}")`,
      'out = pathlib.Path(sys.argv[1]) / "junit.xml"',
      'subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", sys.argv[1], str(pathlib.Path(sys.argv[1]) / "test_known.py"), f"--junitxml={out}"], cwd=sys.argv[1], check=False, capture_output=True)',
      'spec = importlib.util.spec_from_file_location("ba", repo / "scripts" / "build_accuracy.py")',
      'm = importlib.util.module_from_spec(spec); sys.modules["ba"] = m; spec.loader.exec_module(m)',
      'print(json.dumps([a.as_json() for a in m.parse_junit(out.read_text(encoding="utf-8"))]))',
    ].join('\n'));
    const run = spawnSync(python, [join(dir, 'run.py'), dir], { cwd: repoDir, encoding: 'utf8' });
    const areas = JSON.parse(run.stdout.trim().split('\n').at(-1) ?? '[]');
    const fixture = { status: 'published', last_run: '2026-09-24', build: 'local build', suites: areas.map((a) => ({ ...a, name: 'Independent adversarial tests' })) };
    const { p } = await open('/accuracy', { respond: { '/data/accuracy.json': json(fixture) } });
    const row = norm(await text(p, 'section[aria-labelledby="tests"] tbody tr'));
    await p.close();
    return { pass: areas.length > 0 && areas.every((a) => a.failed > 0) && !row.includes('all passed'), actual: `parse_junit -> ${JSON.stringify(areas)}; row "${row}"` };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('E3', 'The site\'s own browser checks are in the published results the site points to', {
  input: 'the claims "Every automated test result is published, including the ones that fail" and "Automated tests on every change, results published" vs accuracy.json',
  expected: 'the website checks (npm run check, the proxy tests) appear on /accuracy, and the build is from a CI run, not "local build"',
  xfail: 'minor: /accuracy lists only the pytest suite; the website\'s own checks (npm run check, functions.test.mjs, this file) are not published, and the published file says "local build", so "on every change, results published" is not yet true',
  where: 'scripts/build_accuracy.py (no website area); web/src/content/landing.ts PILLARS and ABOUT.under',
}, async () => {
  const names = accuracy.suites.map((s) => s.name.toLowerCase()).join(' | ');
  const hasWeb = /browser|website checks|smoke|site checks/.test(names);
  return { pass: hasWeb && accuracy.build !== 'local build', actual: `areas: ${names}; build: ${accuracy.build}` };
});

// ==================================================================================================================
// F. Keyboard only
// ==================================================================================================================

async function tabUntil(p, predicateSrc, max = 80) {
  for (let k = 0; k < max; k++) {
    await p.keyboard.press('Tab');
    if (await p.evaluate(predicateSrc)) return k + 1;
  }
  return -1;
}

test('F1', 'Keyboard: the skip link is the first stop, visible, and moves to the content', {
  input: 'Tab once on /tidy, Enter, Tab',
  expected: '"Skip to content" focused and on screen; after Enter the next Tab lands inside <main>',
}, async () => {
  const { p } = await open('/tidy');
  await p.keyboard.press('Tab');
  const first = await p.evaluate(() => ({ t: document.activeElement?.textContent ?? '', left: document.activeElement?.getBoundingClientRect().left ?? -1 }));
  await p.keyboard.press('Enter');
  await p.keyboard.press('Tab');
  const inMain = await p.evaluate(() => !!document.activeElement?.closest('main'));
  await p.close();
  return { pass: first.t === 'Skip to content' && first.left >= 0 && inMain, actual: `first="${first.t}" left=${first.left}; next Tab in main: ${inMain}` };
});

test('F2', 'Keyboard: the walkthrough, and focus never lands in a hidden step', {
  input: 'Tab to the step buttons, Enter on step 3, Tab to "Next step", Space; then 60 more Tabs',
  expected: 'Step 3 then Step 4; every focused element is visible and in the shown step (or outside the steps)',
}, async () => {
  const { p } = await open('/');
  await p.focus('#steps button[data-i="0"]');
  await p.keyboard.press('Tab');
  await p.keyboard.press('Tab');
  await p.keyboard.press('Enter');
  const s3 = await text(p, '#count');
  const n = await tabUntil(p, () => document.activeElement?.id === 'next', 40);
  await p.keyboard.press('Space');
  const s4 = await text(p, '#count');
  const hidden = [];
  await p.focus('#steps button[data-i="0"]');
  for (let k = 0; k < 60; k++) {
    await p.keyboard.press('Tab');
    const where = await p.evaluate(() => {
      const a = document.activeElement;
      if (!a || a === document.body) return null;
      const pane = a.closest('.pane');
      const visible = a.getClientRects().length > 0;
      return pane !== null && !pane.classList.contains('on') ? `hidden pane: ${a.outerHTML.slice(0, 50)}` : visible ? null : `invisible: ${a.outerHTML.slice(0, 50)}`;
    });
    if (where !== null) hidden.push(where);
  }
  await p.close();
  return { pass: s3 === 'Step 3 of 5' && n > 0 && s4 === 'Step 4 of 5' && hidden.length === 0, actual: `${s3}; Next reached in ${n} Tabs; ${s4}; hidden focus: ${hidden.length === 0 ? 'none' : hidden.join(' | ')}` };
});

test('F3', 'Keyboard: an underlined word opens with Enter and closes with Escape, with a visible focus ring', {
  input: 'Tab to the first underlined word on the landing page, Enter, Escape',
  expected: 'aria-expanded true and the explanation visible; then closed; focus ring drawn',
}, async () => {
  const { p } = await open('/');
  await p.focus('#steps button[data-i="4"]');
  const n = await tabUntil(p, () => document.activeElement?.classList.contains('term') ?? false, 40);
  const ring = await p.evaluate(() => {
    const a = document.activeElement;
    return a ? a.matches(':focus-visible') && getComputedStyle(a).outlineStyle !== 'none' : false;
  });
  await p.keyboard.press('Enter');
  const open1 = await p.evaluate(() => {
    const a = document.activeElement;
    return { exp: a?.getAttribute('aria-expanded'), shown: a?.querySelector('.tip') ? getComputedStyle(a.querySelector('.tip')).display !== 'none' : false };
  });
  await p.keyboard.press('Escape');
  const closed = await p.evaluate(() => document.activeElement?.getAttribute('aria-expanded'));
  await p.close();
  return { pass: n > 0 && ring && open1.exp === 'true' && open1.shown && closed === 'false', actual: `reached in ${n}; ring=${ring}; open=${JSON.stringify(open1)}; after Escape=${closed}` };
});

test('F4', 'Keyboard: step 5 decision, keep mine with a reason', {
  input: 'step 5, Tab to "Keep mine, with a reason", Enter, type a reason, Tab to Save, Enter',
  expected: 'the reason box takes focus; the log records the reason',
}, async () => {
  const { p } = await open('/');
  await p.focus('#steps button[data-i="4"]');
  await p.keyboard.press('Enter');
  const n = await tabUntil(p, () => document.activeElement?.id === 'keep', 40);
  await p.keyboard.press('Enter');
  const focused = await p.evaluate(() => document.activeElement?.id);
  await p.keyboard.type('Client asked for the earlier rule');
  await p.keyboard.press('Tab');
  const onSave = await p.evaluate(() => document.activeElement?.id);
  await p.keyboard.press('Enter');
  const log = await text(p, '#log');
  await p.close();
  return { pass: n > 0 && focused === 'rtext' && onSave === 'save' && log.includes('Client asked for the earlier rule'), actual: `keep in ${n} Tabs; focus=${focused}; then ${onSave}; log="${log}"` };
});

test('F5', 'Keyboard: Try it, typed question, suggested question and the "how it was checked" drawer', {
  input: 'focus the box, type a prepared question, Enter; Tab to suggested question 3, Enter; Tab to "How this answer was made and checked", Enter',
  expected: 'each step works without a mouse',
}, async () => {
  const { p } = await open('/');
  await p.focus('#q');
  await p.keyboard.type('what should a detailed site investigation report include?');
  await p.keyboard.press('Enter');
  const s1 = await p.$eval('#answer', (a) => a.getAttribute('data-state'));
  const n = await tabUntil(p, () => document.activeElement?.getAttribute('data-a') === '3', 20);
  await p.keyboard.press('Enter');
  const s2 = norm(await text(p, '#answer'));
  const m = await tabUntil(p, () => (document.activeElement?.tagName === 'SUMMARY' && (document.activeElement.textContent ?? '').includes('How this answer was made')), 60);
  await p.keyboard.press('Enter');
  const opened = await p.evaluate(() => document.activeElement?.parentElement?.open ?? false);
  await p.close();
  const pass = s1 === 'prepared' && n > 0 && s2.includes('21 days') && m > 0 && opened;
  return { pass, actual: `typed -> ${s1}; chip in ${n} Tabs; answer 4 shown: ${s2.includes('21 days')}; drawer in ${m} Tabs, open=${opened}` };
});

test('F6', 'Keyboard: /tidy "Technical detail" opens with Enter', {
  input: 'Tab to the first "Technical detail" on /tidy, Enter',
  expected: 'the drawer opens',
}, async () => {
  const { p } = await open('/tidy');
  const n = await tabUntil(p, () => document.activeElement?.tagName === 'SUMMARY', 40);
  await p.keyboard.press('Enter');
  const opened = await p.evaluate(() => document.activeElement?.parentElement?.open ?? false);
  await p.close();
  return { pass: n > 0 && opened, actual: `summary in ${n} Tabs; open=${opened}` };
});

// ==================================================================================================================
// G. 390 px phone
// ==================================================================================================================

test('G1', 'Phone 390 px: no sideways scroll on any page with every drawer open', {
  input: 'each route at 390 x 844 with all <details> open (and every prepared answer on the landing page)',
  expected: 'page width 390 px everywhere',
}, async () => {
  const bad = [];
  for (const route of [...ROUTES, '/no-such-page']) {
    const { p } = await open(route, { vp: PHONE });
    await openAllDetails(p);
    await sleep(100);
    let w = await pageWidth(p);
    if (w > 390) bad.push(`${route}: ${w}px`);
    if (route === '/') {
      const n = await p.$$eval('.chips button', (b) => b.length);
      for (let k = 0; k < n; k++) {
        await p.click(`.chips button[data-a="${k}"]`);
        await openAllDetails(p);
        w = await pageWidth(p);
        if (w > 390) bad.push(`/ answer ${k + 1}: ${w}px`);
      }
    }
    await p.close();
  }
  return { pass: bad.length === 0, actual: bad.length === 0 ? 'all 390 px' : bad.join(', ') };
});

test('G2', 'Phone 390 px: every underlined word\'s explanation fits on screen when tapped', {
  input: 'tap each .term in each walkthrough step',
  expected: 'no sideways scroll and the explanation box inside 0 to 390 px',
  where: 'web/src/styles/landing.css .tip (and web/src/components/Term.tsx)',
}, async () => {
  const { p } = await open('/', { vp: PHONE });
  const bad = [];
  for (let step = 0; step < 5; step++) {
    await p.$eval(`#steps button[data-i="${step}"]`, (b) => b.click());
    const terms = await p.$$('.pane.on .term');
    for (const t of terms) {
      await t.evaluate((e) => e.scrollIntoView({ block: 'center' }));
      await t.tap();
      await sleep(80);
      const box = await t.evaluate((e) => {
        const r = e.querySelector('.tip')?.getBoundingClientRect();
        return r ? { left: Math.round(r.left), right: Math.round(r.right), label: e.firstChild?.textContent } : null;
      });
      const w = await pageWidth(p);
      if (box === null || box.left < 0 || box.right > 390 || w > 390) bad.push(`step ${step + 1} "${box?.label}": ${JSON.stringify(box)} page ${w}px`);
      await p.keyboard.press('Escape');
    }
  }
  await p.close();
  return { pass: bad.length === 0, actual: bad.length === 0 ? 'all fit' : bad.join(' | ') };
});

/** A long unbroken run as the indexed guidance really has them (contents-page dot leaders, long web addresses). */
const LEADER = `safety${'.'.repeat(84)}`;
const LONG_URL = 'https://guidelines.nhmrc.gov.au/australian-drinking-water-guidelines/part-5/physical-chemical-characteristics/cas-numbers-1763-23-1-pfos';

test('G3', 'Phone 390 px: a live answer whose passages hold a long unbroken run stays on screen', {
  input: 'live passages-only result, the excerpt holding a 90-character dot leader and the note a 140-character web address (both occur in the indexed guidance)',
  expected: 'page width stays 390 px',
  where: 'web/src/styles/pages.css .cites .excerpt (needs overflow-wrap:anywhere)',
}, async () => {
  const out = [];
  let pass = true;
  for (const [label, over] of [
    ['excerpt with a dot leader', { citations: [{ ...liveResult().citations[0], cited: null, excerpt: `Contents ${LEADER} 12` }] }],
    ['note with a long address', { notes: [`See ${LONG_URL}`] }],
  ]) {
    const r = await live(() => json(liveResult({ status: 'passages_only', answer: null, ...over })), { vp: PHONE });
    const w = await pageWidth(r.p);
    await r.p.close();
    pass &&= w === 390;
    out.push(`${label}: page ${w}px`);
  }
  return { pass, actual: out.join('; ') };
});

test('G5', 'Phone 390 px: the connector address can be read, not only copied', {
  input: 'production build at 390 px, the "Use it inside your own Claude" card',
  expected: 'the whole address https://evidenceline.autopilotyourworkflow.com/mcp is visible (wrapped if need be)',
  where: 'web/src/styles/landing.css .url code',
}, async () => {
  const { p } = await open('/', { vp: PHONE });
  await p.$eval('#connect', (c) => c.scrollIntoView());
  const code = await p.$eval('#mcp', (c) => ({ cut: c.scrollWidth > c.clientWidth + 1, shownPx: c.clientWidth, needPx: c.scrollWidth }));
  await p.close();
  return { pass: !code.cut, actual: `box ${code.shownPx}px wide, address needs ${code.needPx}px; cut off: ${code.cut}` };
});

test('G4', 'Phone 390 px: tap targets at least 24 by 24 px (WCAG 2.2 AA 2.5.8)', {
  input: 'every visible button, .btn, summary, nav link, input and "item N" link on each route (inline text links and underlined words are exempt)',
  expected: 'none smaller than 24 x 24, unless a 24 px circle on its centre touches no other target (the WCAG spacing exception)',
}, async () => {
  const bad = [];
  for (const route of ROUTES) {
    const { p } = await open(route, { vp: PHONE });
    await openAllDetails(p);
    const small = await p.evaluate(() => {
      const targets = [...document.querySelectorAll('a[href], button, summary, input, textarea')]
        .filter((e) => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden' && !e.closest('.skip'))
        .map((e) => ({ e, r: e.getBoundingClientRect() }));
      const checked = targets.filter(({ e }) => e.matches('button:not(.term), .btn, summary, .nav a, input, textarea, a.brand'));
      /** Distance from a point to a rectangle. */
      const dist = (x, y, r) => Math.hypot(Math.max(r.left - x, 0, x - r.right), Math.max(r.top - y, 0, y - r.bottom));
      return checked
        .filter(({ r }) => r.width < 24 || r.height < 24)
        .filter(({ e, r }) => {
          const cx = r.left + r.width / 2;
          const cy = r.top + r.height / 2;
          return targets.some((o) => o.e !== e && !o.e.contains(e) && !e.contains(o.e) && dist(cx, cy, o.r) < 12);
        })
        .map(({ e, r }) => `${e.tagName.toLowerCase()} "${(e.textContent ?? '').trim().slice(0, 24)}" ${Math.round(r.width)}x${Math.round(r.height)}`);
    });
    small.forEach((s) => bad.push(`${route}: ${s}`));
    await p.close();
  }
  return { pass: bad.length === 0, actual: bad.length === 0 ? 'all at least 24 x 24' : bad.join(' | ') };
});

// ==================================================================================================================
// H. Words
// ==================================================================================================================

test('H1', 'No em or en dash on any page, in any drawer, prepared answer, tooltip, alt text or linked text file', {
  input: 'all routes with drawers open, all six prepared answers, /img/credits.md',
  expected: 'none',
}, async () => {
  const hits = [];
  for (const route of [...ROUTES, '/no-such-page']) {
    const { p } = await open(route);
    await openAllDetails(p);
    around(await allText(p), DASHES, 25).forEach((h) => hits.push(`${route}: ${h}`));
    if (route === '/') {
      const n = await p.$$eval('.chips button', (b) => b.length);
      for (let k = 0; k < n; k++) {
        await p.click(`.chips button[data-a="${k}"]`);
        await openAllDetails(p);
        around(await text(p, '#answer'), DASHES, 25).forEach((h) => hits.push(`answer ${k + 1}: ${h}`));
      }
    }
    await p.close();
  }
  const credits = await (await fetch(`${prod}/img/credits.md`)).text();
  around(credits, DASHES, 25).forEach((h) => hits.push(`credits.md: ${h}`));
  return { pass: hits.length === 0, actual: hits.length === 0 ? 'none found' : hits.slice(0, 8).join(' | ') + (hits.length > 8 ? ` (+${hits.length - 8})` : '') };
});

test('H2', 'No "no issues found" wording and no verdict words as the tool\'s own conclusion', {
  input: 'all routes with drawers open and all prepared answers',
  expected: 'no "no issues found" style reassurance (except the sentence saying the checker never says it); every sentence using safe, unsafe, contaminated or fails is negated or names a law, guideline or role',
}, async () => {
  const sentences = [];
  for (const route of [...ROUTES, '/no-such-page']) {
    const { p } = await open(route);
    await openAllDetails(p);
    // Quoted text is not Evidenceline speaking: passage excerpts, the quoted rule on /tidy, test questions on /accuracy.
    const own = (sel) =>
      p.$eval(sel, (root) => {
        const copy = root.cloneNode(true);
        copy.querySelectorAll('.excerpt, blockquote, details.misses').forEach((e) => e.remove());
        copy.querySelectorAll('p, li, dd, dt, h1, h2, h3, h4, summary, small, td, th').forEach((e) => e.append(' \n'));
        return copy.textContent ?? '';
      });
    const chunks = [await own('body')];
    if (route === '/') {
      const n = await p.$$eval('.chips button', (b) => b.length);
      for (let k = 0; k < n; k++) {
        await p.click(`.chips button[data-a="${k}"]`);
        await openAllDetails(p);
        chunks.push(await own('#answer'));
      }
    }
    for (const c of chunks) c.split('\n').flatMap((line) => norm(line).split(/(?<=[.!?:])\s+/)).forEach((s) => sentences.push({ route, s }));
    await p.close();
  }
  const reassure = sentences.filter(({ s }) => /\bno (issues|problems|errors|concerns)( were)? found\b|\ball clear\b|\bnothing to worry\b|\blooks (fine|good)\b/i.test(s) && !/never reports "no issues found"/.test(s));
  const verdict = sentences.filter(
    ({ s }) =>
      /\b(safe|unsafe|contaminated|fails)\b/i.test(s) &&
      !/\b(not|no|never|doesn't|does not|won't|isn't|without|whether|if they know or suspect)\b/i.test(s) &&
      !/Contaminated Sites|contaminated sites|contaminated-land|PFAS-contaminated|contaminated land|non - contaminated|suspected contaminated site/i.test(s),
  );
  const found = [...reassure, ...verdict].map(({ route, s }) => `${route}: "${s.slice(0, 120)}"`);
  return { pass: found.length === 0, actual: found.length === 0 ? `${sentences.length} sentences, none` : [...new Set(found)].join(' | ') };
});

test('H3', 'Every page says it is a concept, not affiliated, on a made-up site with public guidance', {
  input: 'all routes',
  expected: 'each page names Chanon (Beam) Poovaviranon, says not affiliated with Western Environmental, and says the site is fictional or made up',
}, async () => {
  const bad = [];
  for (const route of [...ROUTES, '/no-such-page']) {
    const { p } = await open(route);
    const t = await text(p, 'body');
    if (!t.includes('Chanon (Beam) Poovaviranon') || !/not affiliated with Western Environmental/i.test(t) || !/(Fictional|Made-up) site/i.test(t)) bad.push(route);
    await p.close();
  }
  return { pass: bad.length === 0, actual: bad.length === 0 ? 'on every page' : `missing on ${bad.join(', ')}` };
});

// ==================================================================================================================
// I. Local build vs published build, and the whole chain through the real proxy and API
// ==================================================================================================================

test('I1', 'Offline build claims nothing live', {
  input: 'the offline build (mode offline); type a question',
  expected: 'no connector link, no GitHub button, no /api request, the "switching on soon" message, and no "Answered live" or "live answer" wording',
}, async () => {
  const { p, posted } = await open('/', { origin: offline });
  const got = await ask(p, 'How are health investigation levels used?');
  const body = await allText(p);
  const has = await p.evaluate(() => ({ mcp: !!document.querySelector('#mcp'), gh: [...document.querySelectorAll('a')].some((a) => /github\.com/.test(a.href)) }));
  await p.close();
  const liveWords = around(body, /Answered live|answered live|live now|is live\b/, 30);
  const pass = got.state === 'offline' && got.text.includes('Live answers are switching on soon') && !has.mcp && !has.gh && posted.length === 0 && liveWords.length === 0;
  return { pass, actual: `${got.state}: "${got.text.slice(0, 80)}"; mcp=${has.mcp} github=${has.gh} posts=${posted.length}; live words: ${liveWords.join(' | ') || 'none'}` };
});

test('I2', 'Production build points the connector, GitHub and the question box at the published addresses', {
  input: 'vite build --mode production (.env.production)',
  expected: `#mcp = ${PRODUCTION.mcp}; "Code on GitHub" = ${PRODUCTION.github}; a typed question posts to this site's own /api/ask`,
}, async () => {
  const { p, posted } = await open('/', { respond: { '/api/ask': () => json(liveResult()) } });
  const mcp = await text(p, '#mcp');
  const gh = await p.$eval('#github', (a) => a.getAttribute('href')).catch(() => '');
  await ask(p, 'How are health investigation levels used?');
  await p.close();
  const asked = posted.filter((x) => x.url.includes('/api/ask')).map((x) => x.url);
  const pass = mcp === PRODUCTION.mcp && gh === PRODUCTION.github && asked.length === 1 && asked[0] === `${prod}/api/ask`;
  return { pass, actual: `mcp=${mcp}; github=${gh}; posted to ${asked.join(', ')}` };
});

test('I3', 'The built files carry no local paths, private names, secrets, test addresses or source maps', {
  input: 'every file in the production build',
  expected: 'no drive paths (any C: to Z: folder), no localhost or 127.0.0.1, no API keys, no .map files',
}, async () => {
  const hits = [];
  const walk = (d) => readdirSync(d).flatMap((f) => (statSync(join(d, f)).isDirectory() ? walk(join(d, f)) : [join(d, f)]));
  for (const file of walk(prodDir)) {
    if (file.endsWith('.map')) hits.push(`source map ${file}`);
    if (!/\.(js|css|html|json|md|txt|svg)$/.test(file) && !file.endsWith('_headers')) continue;
    const body = readFileSync(file, 'utf8');
    for (const re of [/\b[C-Zc-z]:[\\/]+[A-Za-z]/, /localhost|127\.0\.0\.1/, /sk-ant-[A-Za-z0-9]/, /ANTHROPIC_API_KEY\s*=/, /PROXY_SHARED_SECRET|EVIDENCELINE_PROXY_SECRET/]) {
      const m = re.exec(body);
      if (m) hits.push(`${file.slice(prodDir.length + 1)}: ${body.slice(Math.max(0, m.index - 30), m.index + 40).replace(/\s+/g, ' ')}`);
    }
  }
  return { pass: hits.length === 0, actual: hits.length === 0 ? 'clean' : hits.join(' | ') };
});

test('I4', 'End to end: box -> Worker proxy -> real API (no model key), no proxy secret', {
  input: 'production build, API_ORIGIN = the local API, PROXY_SHARED_SECRET unset; type a question that is not prepared',
  expected: 'HTTP 200 through the proxy, passages shown in the box, the caller\'s address passed on',
}, async () => {
  proxyEnv.API_ORIGIN = api.origin;
  proxyEnv.PROXY_SHARED_SECRET = undefined;
  proxied.length = 0;
  const { p } = await open('/');
  const got = await ask(p, 'How are health investigation levels used in site assessment?', { timeout: 30_000 });
  await p.close();
  const last = proxied.at(-1);
  const pass = last?.status === 200 && got.state === 'live' && /passages|Not covered/i.test(got.text);
  return { pass, actual: `proxy status ${last?.status}; box ${got.state}: "${norm(got.text).slice(0, 120)}"` };
});

test('I5', 'End to end with the proxy secret set on both sides, as DEPLOY.md instructs', {
  input: 'API with EVIDENCELINE_PROXY_SECRET=s; proxy with PROXY_SHARED_SECRET=s; a typed question, and an MCP initialize to /mcp',
  expected: 'the question is answered (not 403) and the connector initializes',
  where: 'web/functions/_lib/proxy.js PROXY_AUTH_HEADER vs src/evidenceline/api/settings.py PROXY_SECRET_HEADER',
}, async () => {
  proxyEnv.API_ORIGIN = apiLocked.origin;
  proxyEnv.PROXY_SHARED_SECRET = PROXY_SECRET;
  proxied.length = 0;
  const { p } = await open('/');
  const got = await ask(p, 'How are health investigation levels used in site assessment?', { timeout: 30_000 });
  await p.close();
  const mcp = await fetch(`${prod}/mcp`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'adversarial', version: '0' } } }),
  });
  const statuses = proxied.map((x) => `${x.path} ${x.status}`).join(', ');
  const pass = got.state === 'live' && mcp.status === 200;
  return { pass, actual: `${statuses}; box ${got.state}: "${norm(got.text).slice(0, 150)}"` };
});

test('I6', 'End to end: the connector through the proxy lists the nine read-only tools', {
  input: 'POST /mcp initialize, then tools/list, through the proxy to the real API (no secret)',
  expected: 'HTTP 200 and 9 tools',
}, async () => {
  proxyEnv.API_ORIGIN = api.origin;
  proxyEnv.PROXY_SHARED_SECRET = undefined;
  const headers = { 'content-type': 'application/json', accept: 'application/json, text/event-stream' };
  const init = await fetch(`${prod}/mcp`, { method: 'POST', headers, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'adversarial', version: '0' } } }) });
  const initBody = await init.json().catch(() => ({}));
  const version = initBody?.result?.protocolVersion ?? '2025-06-18';
  const list = await fetch(`${prod}/mcp`, { method: 'POST', headers: { ...headers, 'mcp-protocol-version': version }, body: JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} }) });
  const body = await list.json().catch(() => ({}));
  const tools = body?.result?.tools ?? [];
  return { pass: init.status === 200 && list.status === 200 && tools.length === 9, actual: `initialize ${init.status} (${initBody?.result?.serverInfo?.name ?? '?'}), tools/list ${list.status}: ${tools.length} tools` };
});

test('I7', 'End to end: the site with no API_ORIGIN (service not connected) answers the box plainly', {
  input: 'production build, API_ORIGIN unset in the proxy',
  expected: 'the box says the service is not connected and the prepared examples still work',
}, async () => {
  proxyEnv.API_ORIGIN = undefined;
  const { p } = await open('/');
  const got = await ask(p, 'How are health investigation levels used in site assessment?');
  await p.close();
  return { pass: got.state === 'paused' && got.text.includes('not connected') && got.text.includes('prepared examples'), actual: `${got.state}: "${got.text}"` };
});

// ==================================================================================================================
// J. The Worker's proxy on its own
// ==================================================================================================================

test('J1', 'Proxy and Worker tests shipped with the site pass', {
  input: 'node --test tests/functions.test.mjs tests/worker.test.mjs',
  expected: 'exit 0, no failures',
}, async () => {
  const run = spawnSync(process.execPath, ['--test', 'tests/functions.test.mjs', 'tests/worker.test.mjs'], { cwd: webDir, encoding: 'utf8' });
  const line = (run.stdout.match(/^(?:#|ℹ) (?:tests|pass|fail) \d+$/gm) ?? []).map((l) => l.slice(2)).join(', ');
  return { pass: run.status === 0, actual: `exit ${run.status}; ${line}` };
});

test('J2', 'Proxy: a caller cannot fake its address or the proxy secret, and cookies never pass', {
  input: 'request with x-evidenceline-client-ip: 1.2.3.4, x-evidenceline-proxy-auth: guess, x-evidenceline-proxy-secret: guess, cookie, authorization; cf-connecting-ip 198.51.100.9',
  expected: 'client ip is 198.51.100.9; neither secret header is forwarded without a secret, and the real one replaces a guess when set; no cookie or authorization',
}, async () => {
  const req = () =>
    new Request('https://evidenceline.autopilotyourworkflow.com/api/ask', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'cf-connecting-ip': '198.51.100.9', [CLIENT_IP_HEADER]: '1.2.3.4', [PROXY_AUTH_HEADER]: 'guess', 'x-evidenceline-proxy-secret': 'guess', cookie: 'a=b', authorization: 'Bearer x' },
      body: '{"question":"q"}',
    });
  const a = await buildUpstreamRequest(req(), { API_ORIGIN: 'https://api.example.com' });
  const b = await buildUpstreamRequest(req(), { API_ORIGIN: 'https://api.example.com', PROXY_SHARED_SECRET: 'real' });
  const ha = a.ok ? a.init.headers : new Headers();
  const hb = b.ok ? b.init.headers : new Headers();
  // Every header with "proxy" in its name that reaches the API. Without a secret: none (neither guess passes). With
  // one: exactly the real value under PROXY_AUTH_HEADER, whatever that name is (after the I5/J6 fix it is the API's
  // own x-evidenceline-proxy-secret, so the guess sent under that name must have been replaced, not kept).
  const proxyHeaders = (h) => [...h].filter(([k]) => k.includes('proxy'));
  const pass =
    ha.get(CLIENT_IP_HEADER) === '198.51.100.9' && proxyHeaders(ha).length === 0 && ha.get('cookie') === null && ha.get('authorization') === null &&
    JSON.stringify(proxyHeaders(hb)) === JSON.stringify([[PROXY_AUTH_HEADER, 'real']]) && hb.get('x-evidenceline-proxy-auth') === null;
  return { pass, actual: `no secret: ${JSON.stringify([...ha])}; with secret: ${JSON.stringify([...hb].map(([k, v]) => [k, k.includes('proxy') ? v : '...']))}` };
});

test('J3', 'Proxy: an oversized body with no Content-Length is refused', {
  input: 'POST /api/ask streaming 70 KB with no Content-Length',
  expected: '413 before anything is sent upstream',
}, async () => {
  const chunk = new Uint8Array(10 * 1024).fill(97);
  let sent = 0;
  const stream = new ReadableStream({
    pull(c) {
      if (sent >= 7) return c.close();
      sent++;
      c.enqueue(chunk);
    },
  });
  const request = new Request('https://evidenceline.autopilotyourworkflow.com/api/ask', { method: 'POST', body: stream, duplex: 'half', headers: { 'content-type': 'application/json' } });
  let called = false;
  const r = await handleProxy(request, { API_ORIGIN: 'https://api.example.com' }, async () => {
    called = true;
    return new Response('{}');
  });
  return { pass: r.status === 413 && !called, actual: `status ${r.status}; upstream called: ${called}` };
});

test('J4', 'Proxy: paths outside /api and /mcp, and odd origins, are refused', {
  input: '/api/../admin, /mcpx, /apis; API_ORIGIN http://api.example.com, https://api.example.com/v1, https://a@api.example.com',
  expected: '404 for the paths; 503 for each bad origin; nothing sent upstream',
}, async () => {
  let calls = 0;
  const fake = async () => {
    calls++;
    return new Response('{}');
  };
  const out = [];
  for (const path of ['/api/../admin', '/mcpx', '/apis']) {
    const r = await handleProxy(new Request(`https://evidenceline.autopilotyourworkflow.com${path}`), { API_ORIGIN: 'https://api.example.com' }, fake);
    out.push(`${path} ${r.status}`);
  }
  for (const origin of ['http://api.example.com', 'https://api.example.com/v1', 'https://a@api.example.com']) {
    const r = await handleProxy(new Request('https://evidenceline.autopilotyourworkflow.com/api/ask'), { API_ORIGIN: origin }, fake);
    out.push(`${origin} ${r.status}`);
  }
  const pass = out.slice(0, 3).every((x) => x.endsWith(' 404')) && out.slice(3).every((x) => x.endsWith(' 503')) && calls === 0;
  return { pass, actual: `${out.join('; ')}; upstream calls ${calls}` };
});

test('J5', 'Proxy: responses lose Set-Cookie and gain noindex', {
  input: 'upstream 200 with Set-Cookie: s=1 and no cache header',
  expected: 'no Set-Cookie; X-Robots-Tag noindex, nofollow; Cache-Control no-store',
}, async () => {
  const r = toClientResponse(new Response('{}', { status: 200, headers: { 'set-cookie': 's=1', 'content-type': 'application/json' } }));
  const pass = r.headers.get('set-cookie') === null && r.headers.get('x-robots-tag') === 'noindex, nofollow' && r.headers.get('cache-control') === 'no-store';
  return { pass, actual: JSON.stringify([...r.headers]) };
});

test('J6', 'Proxy and API agree on the name of the secret header', {
  input: 'PROXY_AUTH_HEADER in functions/_lib/proxy.js vs PROXY_SECRET_HEADER in src/evidenceline/api/settings.py',
  expected: 'the same header name',
  where: 'web/functions/_lib/proxy.js PROXY_AUTH_HEADER; src/evidenceline/api/settings.py PROXY_SECRET_HEADER',
}, async () => {
  const py = readFileSync(join(repoDir, 'src', 'evidenceline', 'api', 'settings.py'), 'utf8');
  const apiHeader = /PROXY_SECRET_HEADER\s*=\s*"([^"]+)"/.exec(py)?.[1] ?? '';
  return { pass: apiHeader === PROXY_AUTH_HEADER, actual: `proxy "${PROXY_AUTH_HEADER}", API "${apiHeader}"` };
});

// ==================================================================================================================
// Optional: external links (only with --external; reading public pages, nothing is sent)
// ==================================================================================================================

if (EXTERNAL) {
  test('X1', 'External links answer (read-only check of the public pages the site links to)', {
    input: 'every external link found in A1, plus the GitHub repository',
    expected: 'HTTP status below 400 (the GitHub repository is expected to be missing until it is created)',
  }, async () => {
    const out = [];
    let pass = true;
    const urls = new Set([...externalLinks].map((u) => u.split('#')[0]));
    urls.add(PRODUCTION.github);
    for (const u of urls) {
      if (u.startsWith(PRODUCTION.mcp)) continue;
      let status = 0;
      try {
        status = (await fetch(u, { method: 'GET', redirect: 'follow', headers: { 'user-agent': 'Mozilla/5.0' }, signal: AbortSignal.timeout(20_000) })).status;
      } catch {
        status = -1;
      }
      const ok = status > 0 && status < 400;
      if (!ok && u !== PRODUCTION.github) pass = false;
      out.push(`${status} ${u.slice(0, 90)}`);
    }
    return { pass, actual: out.join(' | ') };
  });
}

// ==================================================================================================================
// Run
// ==================================================================================================================

const results = [];
try {
  for (const c of cases) {
    let outcome;
    try {
      outcome = await c.fn();
    } catch (error) {
      outcome = { pass: false, actual: `ERROR ${error instanceof Error ? error.stack?.split('\n').slice(0, 3).join(' ') : String(error)}` };
    }
    const verdict = c.xfail ? (outcome.pass ? 'XPASS' : 'XFAIL') : outcome.pass ? 'PASS' : 'FAIL';
    results.push({ ...c, ...outcome, verdict });
    console.log(`${verdict.padEnd(5)} ${c.id.padEnd(3)} ${c.name}`);
    console.log(`      input:    ${c.input}`);
    console.log(`      expected: ${c.expected}`);
    console.log(`      actual:   ${outcome.actual}`);
    if (c.xfail) console.log(`      known product failure (${c.xfail.split(':')[0]}), most likely in ${c.where}`);
  }
} finally {
  await browser.close().catch(() => undefined);
  await prodServer.close().catch(() => undefined);
  await offlineServer.close().catch(() => undefined);
  api.child.kill();
  apiLocked.child.kill();
}

const count = (v) => results.filter((r) => r.verdict === v).length;
console.log(`\nadversarial: ${results.length} cases: ${count('PASS')} passed, ${count('XFAIL')} failed as marked (known product failures), ${count('FAIL')} failed, ${count('XPASS')} passed unexpectedly (remove the mark).`);
for (const r of results.filter((x) => x.verdict === 'XFAIL')) console.log(`  known failure ${r.id} (${r.xfail.split(':')[0]}): ${r.where}`);
process.exit(count('FAIL') + count('XPASS') === 0 ? 0 : 1);
