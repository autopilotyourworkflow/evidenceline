// Independent adversarial tests for the launch of the Evidenceline website and its Cloudflare Worker, written by a
// tester who did not write the site, the Worker or the proxy.
//
//   cd web && node tests/adversarial_launch.mjs              (about three minutes; no internet, no Cloudflare account)
//   cd web && node tests/adversarial_launch.mjs --external   (also loads Cloudflare's real Turnstile script with the
//                                                             always-pass test key, and checks the GitHub address)
//
// What it does, all on this computer:
// - builds three variants into node_modules/.tmp/advl (web/dist is never touched):
//     launch:     scripts/build-launch.mjs, the stage 1 site (question box and connector "coming soon"), built while
//                 the live settings are deliberately left set in the environment, to prove the launch build ignores them;
//     full:       mode production (.env.production), everything on, as `npm run build`;
//     turnstile:  the full build plus Cloudflare's always-pass test site key 1x00000000000000000000AA;
// - runs the real Worker (worker/index.js with the real wrangler.jsonc, only the file paths made absolute) in LOCAL
//   `wrangler dev` (workerd on 127.0.0.1; never --remote, never a login, deploy or secret command), five times:
//     WL launch build, API_ORIGIN ""                 WF full build, API_ORIGIN "" (full build shipped too early)
//     WU full build, API_ORIGIN = the real FastAPI service from ../.venv with no model key, proxy secret on both sides
//     WS launch build, API_ORIGIN = a small fake API in this file (records what reaches it, streams slowly)
//     WT turnstile build, API_ORIGIN = the same fake API
// - runs `wrangler deploy --dry-run --outdir <temp>` on the real config (no account contact);
// - drives the local Chrome with puppeteer-core. Cloudflare's Turnstile script is replaced by a local stand-in
//   unless --external is given.
//
// Every case prints input, expected, actual and a verdict. A case that shows a real product failure is marked
// xfail (strict): it must fail. If it starts passing the run fails with XPASS, so the mark is removed. Severity first:
//   critical: leaks client data, passes a false number or claim, or shows a false claim on the site;
//   major: wrong result, crash, dead link or broken state;   minor: unclear output.
// Exit code: 0 when every case passed or failed as marked; 1 otherwise.

import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { build } from 'vite';
import { buildLaunch, LAUNCH_OFF } from '../scripts/build-launch.mjs';

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repoDir = resolve(webDir, '..');
const work = join(webDir, 'node_modules', '.tmp', 'advl');
const dirs = { launch: join(work, 'dist-launch'), full: join(work, 'dist-full'), turnstile: join(work, 'dist-turnstile') };
const python = join(repoDir, '.venv', 'Scripts', 'python.exe');
const wranglerBin = join(webDir, 'node_modules', 'wrangler', 'bin', 'wrangler.js');
const chromePath = process.env.CHROME_PATH ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const EXTERNAL = process.argv.includes('--external');

const PRODUCTION = {
  github: 'https://github.com/autopilotyourworkflow/evidenceline',
  mcp: 'https://evidenceline.autopilotyourworkflow.com/mcp',
};
const NOT_CONNECTED = 'The live service is not connected yet. Please try again later.';
const NOINDEX = 'noindex, nofollow';
const TEST_SITE_KEY = '1x00000000000000000000AA';
const REAL_TEST_TOKEN = 'XXXX.DUMMY.TOKEN.XXXX';
const TURNSTILE_HOST = 'challenges.cloudflare.com';
const PROXY_SECRET = 'launch-adversarial-secret';
const ROUTES = ['/', '/sample-site', '/tidy', '/sources', '/accuracy'];
const DASHES = /[\u2013\u2014]/;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const readJson = (path) => JSON.parse(readFileSync(path, 'utf8'));
const answersFile = readJson(join(webDir, 'public', 'data', 'answers.json'));
const accuracy = readJson(join(webDir, 'public', 'data', 'accuracy.json'));
const guidelines = readJson(join(repoDir, 'src', 'evidenceline', 'data', 'guidelines.json'));

// ------------------------------------------------------------------------------------------------------------------
// Case runner with strict xfail (the same convention as tests/adversarial.mjs).

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
// Builds.

mkdirSync(work, { recursive: true });
console.log('adversarial_launch: building the launch, full and Turnstile sites ...');

// The launch build is made while the live settings are still set in the environment (as they would be in a shell
// that just ran a stage 2 build). build-launch.mjs must switch them off anyway, and put the environment back.
const POLLUTED = { VITE_API_BASE: '/', VITE_MCP_URL: PRODUCTION.mcp, VITE_TURNSTILE_SITE_KEY: TEST_SITE_KEY };
Object.assign(process.env, POLLUTED);
await buildLaunch(dirs.launch, { logLevel: 'silent' });
const envAfterLaunch = Object.fromEntries(Object.keys(POLLUTED).map((k) => [k, process.env[k]]));
for (const k of Object.keys(POLLUTED)) delete process.env[k];

// The full build without the robot check: the real site key in .env.production only works on the published address
// (the smoke test checks the production build with it). The Turnstile build below uses Cloudflare's test key.
process.env.VITE_TURNSTILE_SITE_KEY = '';
await build({ root: webDir, mode: 'production', logLevel: 'silent', build: { outDir: dirs.full, emptyOutDir: true } });
process.env.VITE_TURNSTILE_SITE_KEY = TEST_SITE_KEY;
await build({ root: webDir, mode: 'production', logLevel: 'silent', build: { outDir: dirs.turnstile, emptyOutDir: true } });
delete process.env.VITE_TURNSTILE_SITE_KEY;

/** Every JavaScript file of a build, joined. */
const bundleText = (dir) =>
  readdirSync(join(dir, 'assets'))
    .filter((f) => f.endsWith('.js'))
    .map((f) => readFileSync(join(dir, 'assets', f), 'utf8'))
    .join('\n');

// ------------------------------------------------------------------------------------------------------------------
// A fake API behind the Worker: records what reaches it and answers like the real one would.

const fakeSeen = [];
let fakeTokens = [];
const FAKE_PORT = 8832;
const fake = createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks).toString('utf8');
  fakeSeen.push({ method: req.method, url: req.url, headers: req.headers, body });
  const path = (req.url ?? '/').split('?')[0];
  if (path === '/mcp' && req.method === 'GET') {
    // A slow event stream: the first event at once, the second after two seconds.
    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
    res.write('event: message\ndata: {"n":1}\n\n');
    await sleep(2000);
    res.end('event: message\ndata: {"n":2}\n\n');
    return;
  }
  if (path === '/api/echo') {
    res.writeHead(200, { 'content-type': 'application/json', 'set-cookie': 'track=1; Path=/' });
    res.end(JSON.stringify({ headers: req.headers, url: req.url }));
    return;
  }
  if (path === '/api/ask') {
    let parsed = {};
    try {
      parsed = JSON.parse(body);
    } catch {
      // left empty
    }
    fakeTokens.push(parsed.turnstile_token ?? null);
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(
      JSON.stringify({
        question: parsed.question ?? '',
        question_redactions: 0,
        status: 'passages_only',
        explanation: 'No model key on this test service, so no written answer.',
        answer: '',
        citations: [],
        guideline_values: [],
        notes: [],
        verification: { ran: false, passed: false, checks: [], summary: '' },
        model: '',
      }),
    );
    return;
  }
  res.writeHead(404, { 'content-type': 'application/json' });
  res.end('{"detail":"Not Found"}');
});
await new Promise((r) => fake.listen(FAKE_PORT, '127.0.0.1', r));
const fakeOrigin = `http://127.0.0.1:${FAKE_PORT}`;

// ------------------------------------------------------------------------------------------------------------------
// The real API (FastAPI from ../.venv), with no model key, so no model is ever called.

const API_PORT = 8833;
const apiOrigin = `http://127.0.0.1:${API_PORT}`;
const apiChild = spawn(
  python,
  ['-m', 'uvicorn', '--factory', 'evidenceline.api.app:create_app', '--host', '127.0.0.1', '--port', String(API_PORT), '--log-level', 'warning'],
  {
    cwd: repoDir,
    env: {
      ...process.env,
      ANTHROPIC_API_KEY: '',
      EVIDENCELINE_PROXY_SECRET: PROXY_SECRET,
      EVIDENCELINE_CLIENT_IP_HEADER: 'x-evidenceline-client-ip',
      PYTHONIOENCODING: 'utf-8',
    },
    stdio: ['ignore', 'ignore', 'pipe'],
  },
);
let apiErr = '';
apiChild.stderr.on('data', (d) => {
  apiErr += d.toString();
});

// ------------------------------------------------------------------------------------------------------------------
// Local wrangler dev instances (workerd on this computer only).

/** JSON with comments as plain JSON (comments outside strings removed). */
function readJsonc(path) {
  const text = readFileSync(path, 'utf8');
  let out = '';
  let inString = false;
  for (let k = 0; k < text.length; k++) {
    const c = text[k];
    if (inString) {
      out += c;
      if (c === '\\') out += text[++k];
      else if (c === '"') inString = false;
    } else if (c === '"') {
      inString = true;
      out += c;
    } else if (c === '/' && text[k + 1] === '/') {
      while (k < text.length && text[k] !== '\n') k++;
      out += '\n';
    } else if (c === '/' && text[k + 1] === '*') {
      k = text.indexOf('*/', k + 2) + 1;
    } else {
      out += c;
    }
  }
  return JSON.parse(out);
}
const realConfig = readJsonc(join(webDir, 'wrangler.jsonc'));

const wranglers = [];
/**
 * Starts `wrangler dev` (local) on the real config with only main and the assets folder made absolute.
 * @param {string} name
 * @param {number} port
 * @param {string} assetsDir
 * @param {Record<string, string>} vars --var overrides
 */
function startWrangler(name, port, assetsDir, vars) {
  const cfg = structuredClone(realConfig);
  delete cfg.$schema;
  cfg.main = join(webDir, 'worker', 'index.js');
  cfg.assets.directory = assetsDir;
  // Never the real service: once switched on, wrangler.jsonc names the Render origin. Only a --var below sets one.
  cfg.vars = { ...cfg.vars, API_ORIGIN: '' };
  const cfgPath = join(work, `wrangler.${name}.json`);
  writeFileSync(cfgPath, JSON.stringify(cfg, null, 2));
  const args = [
    wranglerBin, 'dev', '-c', cfgPath, '--ip', '127.0.0.1', '--port', String(port), '--inspector-port', String(port + 500),
    '--show-interactive-dev-session=false', '--persist-to', join(work, `state-${name}`), '--log-level', 'error',
  ];
  for (const [k, v] of Object.entries(vars)) args.push('--var', `${k}:${v}`);
  const child = spawn(process.execPath, args, {
    cwd: webDir,
    env: { ...process.env, WRANGLER_SEND_METRICS: 'false' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let log = '';
  child.stdout.on('data', (d) => (log += d.toString()));
  child.stderr.on('data', (d) => (log += d.toString()));
  const w = { name, port, origin: `http://127.0.0.1:${port}`, child, log: () => log };
  wranglers.push(w);
  return w;
}

async function waitFor(url, what, tries = 120) {
  for (let k = 0; k < tries; k++) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (r.status < 500) return;
    } catch {
      // not up yet
    }
    await sleep(500);
  }
  throw new Error(`${what} did not start: ${url}`);
}

console.log('adversarial_launch: starting the real API (no model key) and five local wrangler dev Workers ...');
const WL = startWrangler('launch', 8841, dirs.launch, {});
const WF = startWrangler('full-off', 8842, dirs.full, {});
const WU = startWrangler('full-on', 8843, dirs.full, { API_ORIGIN: apiOrigin, PROXY_SHARED_SECRET: PROXY_SECRET });
const WS = startWrangler('stream', 8844, dirs.launch, { API_ORIGIN: fakeOrigin, PROXY_SHARED_SECRET: PROXY_SECRET });
const WT = startWrangler('turnstile', 8845, dirs.turnstile, { API_ORIGIN: fakeOrigin });
await waitFor(`${apiOrigin}/api/health`, `the API (${apiErr.slice(-300)})`);
for (const w of wranglers) await waitFor(`${w.origin}/robots.txt`, `wrangler dev ${w.name} (${w.log().slice(-400)})`);

const browser = await puppeteer.launch({ executablePath: chromePath, headless: true });
const DESKTOP = { width: 1366, height: 900 };
const PHONE = { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };

// ------------------------------------------------------------------------------------------------------------------
// Browser helpers.

/** A local stand-in for Cloudflare's Turnstile script. mode "ok" gives a fresh token after each render and reset. */
const turnstileStub = (mode) => `(() => {
  const src = document.currentScript && document.currentScript.src || '';
  const onload = new URL(src).searchParams.get('onload');
  const stub = (window.__stub = { renders: [], resets: 0, issued: 0 });
  const widgets = new Map();
  let next = 0;
  const give = (id) => { if (${JSON.stringify(mode)} !== 'ok') return; setTimeout(() => { stub.issued += 1; widgets.get(id)?.callback('STUB.TOKEN.' + stub.issued); }, 60); };
  window.turnstile = {
    render(el, opts) {
      const id = 'w' + (next++);
      widgets.set(id, opts);
      stub.renders.push({ sitekey: opts.sitekey, size: opts.size, action: opts.action });
      const box = document.createElement('div');
      box.style.cssText = 'height:65px;border:1px solid #999';
      box.tabIndex = 0;
      box.textContent = 'Stand-in for the Turnstile check';
      el.appendChild(box);
      give(id);
      return id;
    },
    reset(id) { stub.resets += 1; give(id); },
    remove(id) { widgets.delete(id); },
  };
  if (onload) setTimeout(() => window[onload] && window[onload](), 0);
})();`;

/**
 * Opens a page. `respond` maps a path (or a full URL prefix starting with https://) to a canned response.
 * @returns {Promise<{ p: import('puppeteer-core').Page, errors: string[], requests: { url: string, method: string, body: string }[] }>}
 */
async function open(origin, path = '/', { vp = DESKTOP, respond = {}, turnstile = null } = {}) {
  const p = await browser.newPage();
  const errors = [];
  const requests = [];
  p.on('pageerror', (e) => errors.push(e.message));
  p.on('request', (r) => requests.push({ url: r.url(), method: r.method(), body: r.postData() ?? '' }));
  if (Object.keys(respond).length > 0 || turnstile !== null) {
    await p.setRequestInterception(true);
    p.on('request', (r) => {
      if (r.isInterceptResolutionHandled()) return;
      const url = new URL(r.url());
      if (turnstile !== null && url.hostname === TURNSTILE_HOST) {
        return r.respond({ status: 200, contentType: 'application/javascript', body: turnstileStub(turnstile) });
      }
      const rule = respond[url.pathname];
      if (rule === undefined) return r.continue();
      return r.respond(rule);
    });
  }
  await p.setViewport(vp);
  await p.goto(origin + path, { waitUntil: 'networkidle0' });
  return { p, errors, requests };
}

const allText = (p) =>
  p.evaluate(() =>
    [
      document.title,
      document.body.innerText ?? '',
      document.body.textContent ?? '',
      ...[...document.querySelectorAll('[alt],[aria-label],[placeholder],[title]')].flatMap((e) => ['alt', 'aria-label', 'placeholder', 'title'].map((a) => e.getAttribute(a) ?? '')),
    ].join('\n'),
  );
const openAllDetails = (p) => p.evaluate(() => document.querySelectorAll('details').forEach((d) => (d.open = true)));
const pageWidth = (p) => p.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth));
const around = (text, re, width = 50) => [...text.matchAll(new RegExp(`.{0,${width}}(?:${re.source}).{0,${width}}`, 'gi'))].map((m) => m[0].replace(/\s+/g, ' '));

async function clearBox(p) {
  await p.$eval('#q', (i) => i.scrollIntoView({ block: 'center' }));
  await p.focus('#q');
  await p.keyboard.down('Control');
  await p.keyboard.press('KeyA');
  await p.keyboard.up('Control');
  await p.keyboard.press('Backspace');
}
async function ask(p, question, timeout = 20_000) {
  await clearBox(p);
  await p.type('#q', question);
  await p.keyboard.press('Enter');
  await p.waitForFunction(() => !['loading', null].includes(document.querySelector('#answer')?.getAttribute('data-state') ?? null), { timeout });
  return p.evaluate(() => {
    const a = document.querySelector('#answer');
    return { state: a?.getAttribute('data-state') ?? null, text: a?.textContent ?? '', live: document.querySelector('#answer-status[role="status"]')?.textContent ?? '' };
  });
}
/** Opens every prepared answer in turn and returns their texts (drawers open). */
async function preparedTexts(p) {
  const n = await p.$$eval('.chips button', (b) => b.length);
  const out = [];
  for (let k = 0; k < n; k++) {
    await p.click(`.chips button[data-a="${k}"]`);
    await openAllDetails(p);
    out.push(await allText(p));
  }
  return out;
}
/** Where the keyboard focus is, and whether it shows a visible ring. */
const focusInfo = (p) =>
  p.evaluate(() => {
    const e = document.activeElement;
    if (e === null || e === document.body) return { tag: 'BODY', id: '', text: '', ring: false, visible: false };
    const s = getComputedStyle(e);
    const ring = (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) || (s.boxShadow !== 'none' && s.boxShadow !== '');
    const r = e.getBoundingClientRect();
    return {
      tag: e.tagName,
      id: e.id,
      text: (e.textContent ?? '').trim().slice(0, 50),
      ring,
      visible: r.width > 0 && r.height > 0 && s.visibility !== 'hidden',
      inConnect: !!e.closest('#connect'),
      chip: e.closest('.chips') !== null,
    };
  });
async function tabUntil(p, pred, max = 40) {
  for (let k = 0; k < max; k++) {
    await p.keyboard.press('Tab');
    const f = await focusInfo(p);
    if (pred(f)) return f;
  }
  return null;
}

/** Link problems on a page: empty or "#", missing in-page targets, site files that answer HTML, non-https externals. */
async function linkProblems(p, origin) {
  const links = await p.evaluate(() => [...document.querySelectorAll('a')].map((a) => ({ href: a.getAttribute('href') ?? '', text: (a.textContent ?? '').trim().slice(0, 40) })));
  const dead = [];
  for (const link of links) {
    const href = link.href;
    if (href === '' || href === '#' || href.startsWith('javascript:')) dead.push({ ...link, why: 'empty or #' });
    else if (href.startsWith('#')) {
      if (!(await p.evaluate((id) => !!document.getElementById(id), decodeURIComponent(href.slice(1))))) dead.push({ ...link, why: 'no target' });
    } else if (href.startsWith('/')) {
      const path = href.split('#')[0];
      if (path.startsWith('/api') || path.startsWith('/mcp')) dead.push({ ...link, why: 'links to the service that is not connected' });
      else if (!ROUTES.includes(path)) {
        const r = await fetch(origin + path);
        if (!r.ok || (r.headers.get('content-type') ?? '').includes('text/html')) dead.push({ ...link, why: `HTTP ${r.status} ${r.headers.get('content-type')}` });
      }
    } else if (!/^https:\/\/[^/]+\.[a-z]{2,}/i.test(href)) dead.push({ ...link, why: 'not an absolute https link' });
    else if (href.startsWith(PRODUCTION.mcp)) dead.push({ ...link, why: 'links to the connector that is not connected' });
  }
  return { count: links.length, dead };
}

// ==================================================================================================================
// W. The Worker in local wrangler dev
// ==================================================================================================================

test('W1', 'Launch Worker: every API and connector address answers the friendly 503 in JSON, never the site page', {
  input: 'WL (API_ORIGIN ""): GET/POST/HEAD/OPTIONS/DELETE on /api, /api/, /api/ask, /api/ask?x=1, /api/health, /mcp, /mcp/, /mcp/x',
  expected: '503; {"detail":"The live service is not connected yet. Please try again later."} (no body for HEAD); JSON; no-store; X-Robots-Tag noindex, nofollow',
}, async () => {
  const bad = [];
  let n = 0;
  const paths = ['/api', '/api/', '/api/ask', '/api/ask?x=1', '/api/health', '/mcp', '/mcp/', '/mcp/x'];
  for (const path of paths) {
    for (const method of ['GET', 'POST', 'HEAD', 'OPTIONS', 'DELETE']) {
      n++;
      const init = { method, headers: { accept: 'application/json, text/event-stream', 'content-type': 'application/json' } };
      if (method === 'POST') init.body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}';
      const r = await fetch(WL.origin + path, init);
      const body = await r.text();
      const okBody = method === 'HEAD' ? body === '' : body === JSON.stringify({ detail: NOT_CONNECTED });
      const ok = r.status === 503 && okBody && /application\/json/.test(r.headers.get('content-type') ?? '') && r.headers.get('cache-control') === 'no-store' && r.headers.get('x-robots-tag') === NOINDEX;
      if (!ok) bad.push(`${method} ${path}: ${r.status} ${r.headers.get('content-type')} ${body.slice(0, 60)}`);
    }
  }
  return { pass: bad.length === 0, actual: `${n} requests; ${bad.length === 0 ? 'all 503 with the friendly JSON' : bad.join(' | ')}` };
});

test('W2', 'Launch Worker: the single-page fallback does not swallow encoded API addresses', {
  input: 'WL: /%61pi/ask, /api%2Fask, /%6Dcp (redirects followed), and case variants /API/ask, /MCP',
  expected: 'encoded forms end at the Worker (503 JSON, not HTML); case variants are ordinary site paths (HTML page, noindex): reported only',
}, async () => {
  const out = [];
  let pass = true;
  for (const path of ['/%61pi/ask', '/api%2Fask', '/%6Dcp']) {
    const r = await fetch(WL.origin + path, { redirect: 'follow' });
    const ct = r.headers.get('content-type') ?? '';
    const ok = r.status === 503 && ct.includes('application/json');
    pass &&= ok;
    out.push(`${path} -> ${r.status} ${ct.split(';')[0]}${ok ? '' : ' WRONG'}`);
  }
  for (const path of ['/API/ask', '/MCP']) {
    const r = await fetch(WL.origin + path);
    out.push(`${path} -> ${r.status} ${(r.headers.get('content-type') ?? '').split(';')[0]} (info)`);
  }
  return { pass, actual: out.join('; ') };
});

test('W3', 'Launch Worker: every static response is noindex, and robots.txt disallows everything', {
  input: 'WL: the five routes, a made-up path, every /data/*.json, /img/credits.md, /robots.txt, /favicon.svg, the JS and CSS bundles, a missing image',
  expected: 'X-Robots-Tag: noindex, nofollow on every response; index.html has <meta name="robots" content="noindex, nofollow">; robots.txt "Disallow: /"',
}, async () => {
  const assets = readdirSync(join(dirs.launch, 'assets')).filter((f) => /\.(js|css)$/.test(f)).map((f) => `/assets/${f}`);
  const data = readdirSync(join(dirs.launch, 'data')).filter((f) => f.endsWith('.json')).map((f) => `/data/${f}`);
  const paths = [...ROUTES, '/no-such-page', '/index.html', ...data, '/img/credits.md', '/robots.txt', '/favicon.svg', ...assets, '/img/missing.png'];
  const bad = [];
  for (const path of paths) {
    const r = await fetch(WL.origin + path, { redirect: 'follow' });
    if (r.headers.get('x-robots-tag') !== NOINDEX) bad.push(`${path}: ${r.status} x-robots-tag=${r.headers.get('x-robots-tag')}`);
  }
  const html = await (await fetch(`${WL.origin}/`)).text();
  const meta = /<meta name="robots" content="noindex, nofollow"/.test(html);
  const robots = await (await fetch(`${WL.origin}/robots.txt`)).text();
  const pass = bad.length === 0 && meta && /User-agent: \*\s+Disallow: \/\s*$/m.test(robots);
  return { pass, actual: `${paths.length} paths; missing noindex: ${bad.length === 0 ? 'none' : bad.join(', ')}; meta robots ${meta}; robots.txt ${JSON.stringify(robots.trim())}` };
});

test('W4', 'Live Worker to the real API: health, a question, and the connector work through the proxy with the secret', {
  input: 'WU (API_ORIGIN = real FastAPI, PROXY_SHARED_SECRET = the API\'s EVIDENCELINE_PROXY_SECRET): GET /api/health, POST /api/ask, MCP initialize and tools/list on /mcp',
  expected: 'health 200 JSON; the question 200 (not 403: the secret matched); /mcp lists nine read-only tools; every response noindex; no Set-Cookie',
}, async () => {
  const out = [];
  const health = await fetch(`${WU.origin}/api/health`);
  const hj = await health.json().catch(() => null);
  out.push(`health ${health.status} ${JSON.stringify(hj).slice(0, 120)}`);
  const askR = await fetch(`${WU.origin}/api/ask`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ question: 'What is a conceptual site model?' }) });
  const aj = await askR.json().catch(() => null);
  out.push(`ask ${askR.status} status=${aj?.status ?? aj?.result?.status ?? JSON.stringify(aj).slice(0, 80)}`);
  const mcpHeaders = { 'content-type': 'application/json', accept: 'application/json, text/event-stream', 'mcp-protocol-version': '2025-11-25' };
  const init = await fetch(`${WU.origin}/mcp`, {
    method: 'POST',
    headers: mcpHeaders,
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'adv', version: '1' } } }),
  });
  const initText = await init.text();
  const list = await fetch(`${WU.origin}/mcp`, { method: 'POST', headers: mcpHeaders, body: JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} }) });
  const listText = await list.text();
  const names = [...listText.matchAll(/"name":"([a-z_]+)"/g)].map((m) => m[1]).filter((n, i, a) => a.indexOf(n) === i);
  const readOnly = (listText.match(/"readOnlyHint":true/g) ?? []).length;
  out.push(`mcp initialize ${init.status}, tools/list ${list.status}: ${names.length} names (${names.join(',')}), readOnlyHint true x${readOnly}`);
  const all = [health, askR, init, list];
  const headersOk = all.every((r) => r.headers.get('x-robots-tag') === NOINDEX && r.headers.get('set-cookie') === null);
  const pass = health.status === 200 && hj?.status === 'ok' && askR.status === 200 && init.status === 200 && list.status === 200 && names.includes('lookup_limit') && readOnly >= 9 && headersOk;
  return { pass, actual: `${out.join('; ')}; headers ok ${headersOk}; initialize body ${initText.slice(0, 80).replace(/\s+/g, ' ')}` };
});

test('W5', 'Live Worker to the real API: a trailing slash never sends the caller to the API\'s own address', {
  input: 'WU: POST /api/ask/ and GET /api/health/ (redirects not followed)',
  expected: 'no Location header naming the API service\'s origin (that would bypass the proxy and reveal the service address); any answer is JSON and noindex',
  where: 'web/functions/_lib/proxy.js toClientResponse (keeps an upstream Location that names API_ORIGIN); src/evidenceline/api/app.py (FastAPI redirect_slashes on by default)',
}, async () => {
  const out = [];
  let pass = true;
  for (const [method, path] of [['POST', '/api/ask/'], ['GET', '/api/health/']]) {
    const init = { method, redirect: 'manual', headers: { 'content-type': 'application/json' } };
    if (method === 'POST') init.body = '{"question":"What is PFOS?"}';
    const r = await fetch(WU.origin + path, init);
    const loc = r.headers.get('location') ?? '';
    const leaks = loc.includes('127.0.0.1:' + API_PORT) || loc.includes('onrender.com') || /^https?:\/\//.test(loc);
    const ok = !leaks && r.headers.get('x-robots-tag') === NOINDEX;
    pass &&= ok;
    out.push(`${method} ${path}: ${r.status} location=${JSON.stringify(loc)} ${(r.headers.get('content-type') ?? '').split(';')[0]}${ok ? '' : ' WRONG'}`);
  }
  return { pass, actual: out.join('; ') };
});

test('W6', 'Worker in the real runtime: a slow connector event stream is passed on as it arrives, not buffered', {
  input: 'WS -> fake API: GET /mcp (Accept: text/event-stream); the fake sends one event at once and the next after 2 s',
  expected: 'the first event reaches the caller in under 1.2 s; both arrive; noindex on the response',
}, async () => {
  const t0 = Date.now();
  const r = await fetch(`${WS.origin}/mcp`, { headers: { accept: 'text/event-stream' } });
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let text = '';
  let first = -1;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    text += dec.decode(value, { stream: true });
    if (first < 0 && text.includes('"n":1')) first = Date.now() - t0;
  }
  const total = Date.now() - t0;
  const pass = r.status === 200 && first >= 0 && first < 1200 && text.includes('"n":2') && r.headers.get('x-robots-tag') === NOINDEX;
  return { pass, actual: `status ${r.status}; first event after ${first} ms; stream ended after ${total} ms; noindex ${r.headers.get('x-robots-tag')}` };
});

test('W7', 'Worker in the real runtime: headers a caller makes up never reach the API; cookies never come back', {
  input: 'WS -> fake API: GET /api/echo with Cookie, Authorization, x-evidenceline-proxy-secret: forged, x-evidenceline-client-ip: 1.2.3.4, x-forwarded-for; the fake answers Set-Cookie',
  expected: 'the API sees the Worker\'s own secret (not "forged"), no cookie, no authorization, no forged client IP; the caller gets no Set-Cookie',
}, async () => {
  const r = await fetch(`${WS.origin}/api/echo?x=1`, {
    headers: { cookie: 'session=abc', authorization: 'Bearer stolen', 'x-evidenceline-proxy-secret': 'forged', 'x-evidenceline-client-ip': '1.2.3.4', 'x-forwarded-for': '1.2.3.4' },
  });
  const j = await r.json();
  const h = j.headers ?? {};
  const pass =
    h['x-evidenceline-proxy-secret'] === PROXY_SECRET &&
    h.cookie === undefined &&
    h.authorization === undefined &&
    h['x-evidenceline-client-ip'] !== '1.2.3.4' &&
    h['x-forwarded-for'] === undefined &&
    j.url === '/api/echo?x=1' &&
    r.headers.get('set-cookie') === null;
  const shown = Object.fromEntries(Object.entries(h).filter(([k]) => /secret|cookie|authorization|client-ip|forwarded/.test(k)).map(([k, v]) => [k, k.includes('secret') ? (v === PROXY_SECRET ? '<the Worker secret>' : v) : v]));
  return { pass, actual: `the API saw ${JSON.stringify(shown)}, url ${j.url}; Set-Cookie to caller: ${r.headers.get('set-cookie')}` };
});

test('W8', 'The deploy dry run on the real wrangler.jsonc contacts no account and ships only the Worker', {
  input: 'wrangler deploy --dry-run --outdir <temp> (the documented pre-deploy check; web/dist as it is now)',
  expected: 'exit 0; lists env.ASSETS and API_ORIGIN ("" at launch, the https Render origin once live); bundled worker has only a default export and no secret value',
}, async () => {
  const outdir = join(work, 'dry-run');
  rmSync(outdir, { recursive: true, force: true });
  const r = spawnSync(process.execPath, [wranglerBin, 'deploy', '--dry-run', '--outdir', outdir], {
    cwd: webDir,
    env: { ...process.env, WRANGLER_SEND_METRICS: 'false', CLOUDFLARE_API_TOKEN: '' },
    encoding: 'utf8',
    timeout: 120_000,
  });
  const log = `${r.stdout}\n${r.stderr}`;
  const files = existsSync(outdir) ? readdirSync(outdir) : [];
  const js = files.filter((f) => f.endsWith('.js')).map((f) => readFileSync(join(outdir, f), 'utf8')).join('\n');
  const exportsOnlyDefault = /export\s*\{\s*[\w$]+\s+as\s+default\s*\}/.test(js) && !/export\s*\{[^}]*,[^}]*\}/.test(js);
  const pass = r.status === 0 && /env\.ASSETS/.test(log) && /API_ORIGIN[^\n]*("|https:\/\/[\w.-]+\.onrender\.com)"/.test(log) && /--dry-run: exiting now/.test(log) && exportsOnlyDefault && !js.includes(PROXY_SECRET);
  return { pass, actual: `exit ${r.status}; bindings: ${log.split('\n').filter((l) => /ASSETS|API_ORIGIN|dry-run/.test(l)).map((l) => l.trim()).join(' / ')}; files ${files.join(',')}; only default export ${exportsOnlyDefault}` };
});

// ==================================================================================================================
// L. The launch build (stage 1), served by the real Worker
// ==================================================================================================================

test('L1', 'Launch build ignores live settings left in the environment, and puts the environment back', {
  input: 'buildLaunch() with VITE_API_BASE=/, VITE_MCP_URL=<connector>, VITE_TURNSTILE_SITE_KEY=<test key> already set',
  expected: 'the bundle holds neither the connector address nor the site key; the three variables are restored afterwards; LAUNCH_OFF clears exactly those three',
}, async () => {
  const js = bundleText(dirs.launch);
  const restored = Object.entries(POLLUTED).every(([k, v]) => envAfterLaunch[k] === v);
  const pass = !js.includes(PRODUCTION.mcp) && !js.includes(TEST_SITE_KEY) && js.includes(PRODUCTION.github) && restored && Object.keys(LAUNCH_OFF).sort().join() === 'VITE_API_BASE,VITE_MCP_URL,VITE_TURNSTILE_SITE_KEY';
  return { pass, actual: `connector in bundle ${js.includes(PRODUCTION.mcp)}; site key in bundle ${js.includes(TEST_SITE_KEY)}; GitHub in bundle ${js.includes(PRODUCTION.github)}; env restored ${restored}` };
});

test('L2', 'Launch: the box and the connector card say "soon", and nothing live is offered', {
  input: 'WL, landing page, desktop',
  expected: 'box note "switching on soon"; card note "will appear here soon"; no connector address, Copy button or link in the card; no robot check; "Code on GitHub" is the public repository',
}, async () => {
  const { p, requests, errors } = await open(WL.origin, '/');
  const box = await p.$eval('#coming-soon', (e) => ({ text: e.textContent ?? '', role: e.getAttribute('role') })).catch(() => null);
  const card = await p.$eval('#connect', (e) => ({
    text: e.textContent ?? '',
    code: e.querySelector('code') !== null,
    buttons: e.querySelectorAll('button').length,
    links: e.querySelectorAll('a').length,
  }));
  const robot = (await p.$('#robot')) !== null;
  const github = await p.$eval('#github', (a) => a.getAttribute('href')).catch(() => null);
  const turnstile = requests.some((r) => r.url.includes(TURNSTILE_HOST));
  await p.close();
  const pass =
    box !== null && /switching on soon/.test(box.text) && box.role === 'note' &&
    /will appear here soon/.test(card.text) && !card.code && card.buttons === 0 && card.links === 0 && !card.text.includes(PRODUCTION.mcp) &&
    !robot && !turnstile && github === PRODUCTION.github && errors.length === 0;
  return { pass, actual: `box ${JSON.stringify(box)}; card ${JSON.stringify({ ...card, text: card.text.slice(0, 160) })}; robot ${robot}; Turnstile script ${turnstile}; GitHub ${github}; errors ${errors.length}` };
});

test('L3', 'Launch: every prepared question shows its prepared answer, and nothing is sent to the service', {
  input: 'WL: click each of the prepared questions; then type one of them in different case and spacing; then a new question; then blank',
  expected: 'each click shows the answers.json text (answer or explanation); typed prepared question matches; a new question gets the "soon" message in the live region; blank does nothing; no request to /api or /mcp',
}, async () => {
  const { p, requests } = await open(WL.origin, '/');
  const norm = (s) => s.replace(/\[(G?\d+)\]/g, '').replace(/ug\/L/g, 'µg/L').replace(/\s+/g, ' ').trim();
  const bad = [];
  const n = await p.$$eval('.chips button', (b) => b.length);
  for (let k = 0; k < n; k++) {
    await p.click(`.chips button[data-a="${k}"]`);
    const shown = await p.$eval('#answer', (a) => ({ state: a.getAttribute('data-state'), text: a.textContent ?? '' }));
    const entry = answersFile.answers[k];
    const expectText = (entry.result.answer ?? '') !== '' ? entry.result.answer : (entry.result.explanation ?? '');
    const sentences = norm(expectText).split(/(?<=\.)\s+/).filter((s) => s.length > 20);
    const missing = sentences.filter((s) => !norm(shown.text).includes(s.replace(/\s*\.$/, '')));
    if (shown.state !== 'prepared' || missing.length > 0) bad.push(`#${k + 1} ${entry.question}: state ${shown.state}, missing ${JSON.stringify(missing.map((m) => m.slice(0, 50)))}`);
  }
  const typed = await ask(p, `   ${answersFile.answers[0].question.toUpperCase()}   `);
  const fresh = await ask(p, 'How deep should monitoring wells be screened for PFAS?');
  await clearBox(p);
  await p.keyboard.press('Enter');
  const afterBlank = await p.$eval('#answer', (a) => a.getAttribute('data-state'));
  const service = requests.filter((r) => /\/api(\/|$)|\/mcp/.test(new URL(r.url).pathname));
  await p.close();
  const pass =
    n === answersFile.answers.length && bad.length === 0 && typed.state === 'prepared' &&
    fresh.state === 'offline' && /switching on soon/.test(fresh.text) && /prepared questions/.test(fresh.text) && /switching on soon/.test(fresh.live) &&
    afterBlank === 'offline' && service.length === 0;
  return { pass, actual: `${n} prepared (file has ${answersFile.answers.length}); problems ${bad.length === 0 ? 'none' : bad.join(' | ')}; typed prepared -> ${typed.state}; new question -> ${fresh.state} "${fresh.text.slice(0, 90)}" (status line "${fresh.live.slice(0, 40)}"); blank -> ${afterBlank}; service requests ${service.length}` };
});

/** Collects the whole text of every route, with every drawer open and, on the landing page, every prepared answer. */
async function everyText(origin, vp = DESKTOP) {
  const texts = [];
  for (const route of [...ROUTES, '/no-such-page']) {
    const { p } = await open(origin, route, { vp });
    await openAllDetails(p);
    texts.push({ route, text: await allText(p) });
    if (route === '/') (await preparedTexts(p)).forEach((t, k) => texts.push({ route: `/ answer ${k + 1}`, text: t }));
    await p.close();
  }
  return texts;
}

test('L4', 'Launch: no page claims a live service that is not there yet', {
  input: 'WL: every route and every prepared answer, drawers open',
  expected: 'none of: the connector address, "claude mcp add", "Answered live", the connector steps ("Copy this link", "Add custom connector"), "live now", "is live", "available now"',
}, async () => {
  const texts = await everyText(WL.origin);
  const FORBIDDEN = /evidenceline\.autopilotyourworkflow\.com\/mcp|claude mcp add|Answered live|Copy this link|Add custom connector|live now|is live\b|available now|now available/i;
  const hits = texts.flatMap(({ route, text }) => around(text, FORBIDDEN).map((a) => `${route}: ${a}`));
  return { pass: hits.length === 0, actual: hits.length === 0 ? `${texts.length} pages and answers checked; none` : [...new Set(hits)].join(' | ') };
});

test('L5', 'Launch: the "Try it" section does not promise a way to try it that is not there yet', {
  input: 'WL: the "Try it yourself" heading, its lead and the connector card, as a first-time visitor reads them before the notes',
  expected: 'no present-tense promise of the connector ("Two ways", "you can add Evidenceline to it") while the card says the link is not there yet',
  where: 'web/src/content/landing.ts TRY.lead and TRY.connectText (shown unchanged in web/src/landing/TryIt.tsx when CONFIG.mcpUrl is null)',
}, async () => {
  const { p } = await open(WL.origin, '/');
  const lead = await p.$eval('#try .lead', (e) => e.textContent ?? '');
  const card = await p.$eval('#connect', (e) => [...e.querySelectorAll('p')].map((x) => x.textContent ?? '').join(' | '));
  await p.close();
  const promises = /two ways|you can add evidenceline/i;
  return { pass: !promises.test(lead) && !promises.test(card), actual: `lead "${lead}"; card "${card}"` };
});

test('L6', 'Launch: no dead links on any page, in any drawer or prepared answer, served by the real Worker', {
  input: 'WL: every <a> on the five routes and a made-up path, every drawer open, every prepared answer',
  expected: 'no empty or "#" links; in-page targets exist; site files load as non-HTML; externals are https; nothing links to /api, /mcp or the connector',
}, async () => {
  const dead = [];
  let total = 0;
  for (const route of [...ROUTES, '/no-such-page']) {
    const { p } = await open(WL.origin, route);
    await openAllDetails(p);
    if (route === '/') {
      const n = await p.$$eval('.chips button', (b) => b.length);
      for (let k = 0; k < n; k++) {
        await p.click(`.chips button[data-a="${k}"]`);
        await openAllDetails(p);
        const r = await linkProblems(p, WL.origin);
        total += r.count;
        dead.push(...r.dead.map((d) => ({ route: `/ answer ${k + 1}`, ...d })));
      }
    }
    const r = await linkProblems(p, WL.origin);
    total += r.count;
    dead.push(...r.dead.map((d) => ({ route, ...d })));
    await p.close();
  }
  return { pass: total > 50 && dead.length === 0, actual: `${total} links checked; problems: ${dead.length === 0 ? 'none' : JSON.stringify(dead)}` };
});

test('L7', 'Launch: no em or en dash anywhere a visitor can read', {
  input: 'WL: every route, drawer, prepared answer, tooltip, alt and aria text; the "soon" messages; the hand-written fallback answers',
  expected: 'no U+2013 or U+2014',
}, async () => {
  const texts = await everyText(WL.origin);
  const { p } = await open(WL.origin, '/', { respond: { '/data/answers.json': { status: 200, contentType: 'text/html', body: '<!doctype html><title>Evidenceline</title>' } } });
  for (let k = 0; k < 2; k++) {
    await p.click(`.chips button[data-a="${k}"]`);
    texts.push({ route: `/ fallback ${k + 1}`, text: await allText(p) });
  }
  await p.close();
  const hits = texts.flatMap(({ route, text }) => around(text, DASHES, 30).map((a) => `${route}: ${a}`));
  return { pass: hits.length === 0, actual: hits.length === 0 ? `${texts.length} texts checked; none` : [...new Set(hits)].join(' | ') };
});

test('L8', 'Launch: when a data file is missing the Worker serves the page instead, and the site says so plainly', {
  input: 'WL: /data/answers.json and /data/accuracy.json answered with the HTML page (what Cloudflare\'s single-page fallback serves for a missing file)',
  expected: 'landing: the two hand-written examples, both values (0.008 and 0.07 µg/L), no rule picked, no script error; /accuracy: a plain "could not be read" line, no numbers',
}, async () => {
  const html = { status: 200, contentType: 'text/html', body: '<!doctype html><title>Evidenceline</title>' };
  const land = await open(WL.origin, '/', { respond: { '/data/answers.json': html } });
  const fallbackTexts = [];
  for (let k = 0; k < 2; k++) {
    await land.p.click(`.chips button[data-a="${k}"]`);
    fallbackTexts.push(await land.p.$eval('#answer', (a) => ({ state: a.getAttribute('data-state'), text: a.textContent ?? '' })));
  }
  await land.p.close();
  const acc = await open(WL.origin, '/accuracy', { respond: { '/data/accuracy.json': html } });
  const accText = await acc.p.$eval('main', (m) => m.textContent ?? '').catch(() => '');
  await acc.p.close();
  const pfos = fallbackTexts[0]?.text ?? '';
  const current = guidelines.rules.find((r) => r.id === 'current').limits.find((l) => l.key === 'PFOS').value;
  const nemp = guidelines.rules.find((r) => r.id === 'nemp-3.0').limits[0].value;
  const pass =
    fallbackTexts.every((t) => t.state === 'fallback') && pfos.includes(`${current} µg/L`) && pfos.includes(`${nemp} µg/L`) && /scientist's call/.test(pfos) &&
    land.errors.length === 0 && /accuracy\.json/.test(accText) && /not valid JSON|could not be/.test(accText) && !/12 of 24/.test(accText);
  return { pass, actual: `fallback states ${fallbackTexts.map((t) => t.state).join(',')}; PFOS fallback "${pfos.slice(0, 260)}"; accuracy page "${accText.replace(/\s+/g, ' ').slice(0, 160)}"` };
});

test('L9', 'Launch: the hand-written PFOS fallback lays out both rules without leaning towards one', {
  input: 'WL, answers.json unreadable: the PFOS fallback example',
  expected: 'both values in neutral words, as the prepared answer and the search preview now do ("under each rule", "picks neither"); no "still names" suggesting the WA-named edition is out of date',
  where: 'web/src/content/landing.ts EXAMPLE_ANSWERS[0]',
}, async () => {
  const html = { status: 200, contentType: 'text/html', body: '<!doctype html>' };
  const { p } = await open(WL.origin, '/', { respond: { '/data/answers.json': html } });
  await p.click('.chips button[data-a="0"]');
  const t = await p.$eval('#answer', (a) => a.textContent ?? '');
  await p.close();
  return { pass: !/\bstill names\b|\bstill\b/i.test(t), actual: around(t, /still/i, 60).join(' | ') || t.slice(0, 200) };
});

test('L10', 'Launch, keyboard only: question field, Ask, prepared questions and the connector card', {
  input: 'WL: Tab from the question field; Enter on the first prepared question; type a new question and press Enter; Tab through the connector card',
  expected: 'focus goes field -> Ask -> first prepared question, each with a visible focus ring; Enter shows the prepared answer; the new question shows the "soon" message; the card has no focusable element (nothing dead to land on)',
}, async () => {
  const { p } = await open(WL.origin, '/');
  await p.$eval('#q', (i) => i.scrollIntoView({ block: 'center' }));
  await p.focus('#q');
  const steps = [await focusInfo(p)];
  await p.keyboard.press('Tab');
  steps.push(await focusInfo(p));
  const chip = await tabUntil(p, (f) => f.chip, 5);
  steps.push(chip);
  await p.keyboard.press('Enter');
  const afterChip = await p.$eval('#answer', (a) => a.getAttribute('data-state'));
  const typed = await ask(p, 'What PFAS analytes should a lab report?');
  const focusables = await p.$$eval('#connect a, #connect button, #connect input, #connect [tabindex]', (e) => e.length);
  await p.close();
  const pass =
    steps[0].id === 'q' && steps[1].tag === 'BUTTON' && /Ask/.test(steps[1].text) && steps[1].ring && chip !== null && chip.ring && chip.visible &&
    afterChip === 'prepared' && typed.state === 'offline' && focusables === 0;
  return { pass, actual: `focus ${JSON.stringify(steps.map((s) => s && `${s.tag}#${s.id} "${s.text.slice(0, 30)}" ring=${s.ring}`))}; Enter on chip -> ${afterChip}; typed -> ${typed.state}; focusable in card ${focusables}` };
});

test('L11', 'Launch, phone 390 px: no sideways scroll on any page, and the "soon" notes fit', {
  input: 'WL at 390 x 844: the five routes and a made-up path with every drawer open; the landing page after a typed question',
  expected: 'page width 390 everywhere; the box note, the card note and the "soon" message inside 390 px; prepared-question buttons at least 24 px tall',
}, async () => {
  const out = [];
  let pass = true;
  for (const route of [...ROUTES, '/no-such-page']) {
    const { p } = await open(WL.origin, route, { vp: PHONE });
    await openAllDetails(p);
    const w = await pageWidth(p);
    if (w > 390) {
      pass = false;
      out.push(`${route} ${w}px`);
    }
    if (route === '/') {
      await ask(p, 'Is groundwater near a PFAS site safe to drink?');
      const boxes = await p.evaluate(() =>
        ['#coming-soon', '#connect .pending', '#answer', '.chips button'].map((s) => {
          const e = document.querySelector(s);
          if (e === null) return { s, missing: true };
          const r = e.getBoundingClientRect();
          return { s, left: Math.round(r.left), right: Math.round(r.right), h: Math.round(r.height) };
        }),
      );
      const bad = boxes.filter((b) => b.missing || b.left < 0 || b.right > 390 || (b.s === '.chips button' && b.h < 24));
      if (bad.length > 0) pass = false;
      out.push(`try-it boxes ${JSON.stringify(boxes)}`);
      if ((await pageWidth(p)) > 390) {
        pass = false;
        out.push('landing after the message wider than 390');
      }
    }
    await p.close();
  }
  return { pass, actual: out.join('; ') || 'all 390 px' };
});

// ==================================================================================================================
// F. The full build (stage 2)
// ==================================================================================================================

test('F1', 'Full build: the box posts to this site\'s own /api/ask, and a live result through the Worker is labelled honestly', {
  input: 'WU (real API, no model key): type a new question and press Enter',
  expected: 'exactly one POST to <site>/api/ask (same origin, not //api/ask, not the API address), JSON {"question"} with no turnstile_token; a result that is not called "Answered live" (no written answer without a model key); no "soon" notes',
}, async () => {
  const { p, requests } = await open(WU.origin, '/');
  const shown = await ask(p, 'What does a Tier 1 screening compare results with?', 45_000);
  const posts = requests.filter((r) => r.method === 'POST');
  const soon = (await p.$('#coming-soon')) !== null;
  await p.close();
  let body = {};
  try {
    body = JSON.parse(posts[0]?.body ?? '{}');
  } catch {
    // left empty
  }
  const pass =
    posts.length === 1 && posts[0].url === `${WU.origin}/api/ask` && Object.keys(body).join() === 'question' &&
    shown.state === 'live' && !/Answered live/.test(shown.text) && /no written answer/i.test(shown.text) && !soon;
  return { pass, actual: `POSTs ${JSON.stringify(posts.map((x) => x.url))} body keys ${Object.keys(body).join(',')}; state ${shown.state}; text "${shown.text.slice(0, 140)}"; soon note ${soon}` };
});

test('F2', 'Full build: the connector card and GitHub link point at the published addresses', {
  input: 'WU landing page: #connect, #mcp, #copy, #github',
  expected: `#mcp text is exactly ${PRODUCTION.mcp}; a Copy button; three steps; #github is ${PRODUCTION.github}; no Turnstile script without a site key`,
}, async () => {
  const { p, requests } = await open(WU.origin, '/');
  const mcp = await p.$eval('#mcp', (e) => (e.textContent ?? '').trim()).catch(() => null);
  const copy = (await p.$('#copy')) !== null;
  const steps = await p.$$eval('#connect ol > li', (l) => l.length);
  const github = await p.$eval('#github', (a) => a.getAttribute('href')).catch(() => null);
  const robot = (await p.$('#robot')) !== null;
  const ts = requests.some((r) => r.url.includes(TURNSTILE_HOST));
  await p.close();
  const js = bundleText(dirs.full);
  const pass = mcp === PRODUCTION.mcp && copy && steps === 3 && github === PRODUCTION.github && !robot && !ts && !js.includes(TEST_SITE_KEY) && !js.includes('onrender.com');
  return { pass, actual: `mcp "${mcp}"; copy ${copy}; steps ${steps}; github ${github}; robot ${robot}; Turnstile script ${ts}` };
});

test('F3', 'Full build shipped before the service exists: the box says so plainly', {
  input: 'WF (full build, API_ORIGIN ""): a prepared question, then a new typed question',
  expected: 'the prepared answer still shows; the typed question gets a plain "paused" or "not connected" message, never "Answered live" or an HTTP number; no dash',
}, async () => {
  const { p } = await open(WF.origin, '/');
  await p.click('.chips button[data-a="0"]');
  const prepared = await p.$eval('#answer', (a) => a.getAttribute('data-state'));
  const typed = await ask(p, 'What is the investigation level for PFOA in drinking water?');
  await p.close();
  const pass = prepared === 'prepared' && typed.state === 'paused' && /paused|not connected/i.test(typed.text) && !/Answered live|HTTP \d/.test(typed.text) && !DASHES.test(typed.text);
  return { pass, actual: `prepared -> ${prepared}; typed -> ${typed.state} "${typed.text.slice(0, 160)}"` };
});

test('F4', 'Turnstile on (test key, local stand-in): the widget renders with the key, and every question carries a fresh token', {
  input: 'WT (site key 1x00000000000000000000AA) -> fake API: scroll to the box, ask two new questions',
  expected: 'script from challenges.cloudflare.com with render=explicit; one widget with the test key, action "ask"; each question reaches the API through the Worker with a new token; widget reset after each; no dash in the robot label',
}, async () => {
  fakeTokens = [];
  const { p, requests } = await open(WT.origin, '/', { turnstile: 'ok' });
  await p.$eval('#q', (i) => i.scrollIntoView({ block: 'center' }));
  await p.waitForFunction(() => window.__stub?.issued >= 1, { timeout: 10_000 }).catch(() => undefined);
  const a1 = await ask(p, 'How should PFAS samples be stored before analysis?');
  await p.waitForFunction(() => window.__stub?.issued >= 2, { timeout: 5000 }).catch(() => undefined);
  const a2 = await ask(p, 'What is a detailed site investigation?');
  const stub = await p.evaluate(() => window.__stub);
  const script = requests.find((r) => r.url.includes(TURNSTILE_HOST))?.url ?? '';
  const label = await p.$eval('#robot', (e) => e.getAttribute('aria-label')).catch(() => null);
  await p.close();
  const pass =
    script.startsWith(`https://${TURNSTILE_HOST}/turnstile/v0/api.js?render=explicit`) && stub?.renders.length === 1 && stub.renders[0].sitekey === TEST_SITE_KEY && stub.renders[0].action === 'ask' &&
    fakeTokens.length === 2 && fakeTokens[0] === 'STUB.TOKEN.1' && fakeTokens[1] === 'STUB.TOKEN.2' && stub.resets >= 2 && a1.state === 'live' && a2.state === 'live' && label !== null && !DASHES.test(label);
  return { pass, actual: `script ${script.slice(0, 90)}; renders ${JSON.stringify(stub?.renders)}; resets ${stub?.resets}; tokens at the API ${JSON.stringify(fakeTokens)}; states ${a1.state},${a2.state}; label "${label}"` };
});

test('F5', 'Turnstile on, but the check never finishes: the question is held back and the visitor is told what to do', {
  input: 'WT with a stand-in that renders but never gives a token; ask a new question and wait',
  expected: 'after the wait, the "has not finished yet" message; nothing reaches /api/ask; the prepared questions still work',
}, async () => {
  fakeTokens = [];
  const before = fakeSeen.filter((s) => s.url?.startsWith('/api/ask')).length;
  const { p } = await open(WT.origin, '/', { turnstile: 'silent' });
  await p.$eval('#q', (i) => i.scrollIntoView({ block: 'center' }));
  const shown = await ask(p, 'Where do I send a DSI report in WA?', 30_000);
  await p.click('.chips button[data-a="1"]');
  const prepared = await p.$eval('#answer', (a) => a.getAttribute('data-state'));
  await p.close();
  const after = fakeSeen.filter((s) => s.url?.startsWith('/api/ask')).length;
  const pass = shown.state === 'robot-waiting' && /not finished yet/.test(shown.text) && after === before && prepared === 'prepared';
  return { pass, actual: `state ${shown.state} "${shown.text.slice(0, 120)}"; requests to /api/ask ${after - before}; prepared -> ${prepared}` };
});

test('F6', 'Turnstile on: keyboard and 390 px phone', {
  input: 'WT with the stand-in, phone 390 x 844: Tab from the question field; the robot check box',
  expected: 'Tab from the field reaches Ask; the check sits inside 390 px; no sideways scroll; a typed question sent with Enter carries a token',
}, async () => {
  fakeTokens = [];
  const { p } = await open(WT.origin, '/', { vp: PHONE, turnstile: 'ok' });
  await p.$eval('#q', (i) => i.scrollIntoView({ block: 'center' }));
  await p.waitForFunction(() => window.__stub?.issued >= 1, { timeout: 10_000 }).catch(() => undefined);
  await p.focus('#q');
  await p.keyboard.press('Tab');
  const f = await focusInfo(p);
  const box = await p.$eval('#robot', (e) => {
    const r = e.getBoundingClientRect();
    return { left: Math.round(r.left), right: Math.round(r.right), h: Math.round(r.height) };
  }).catch(() => null);
  const shown = await ask(p, 'Which lab methods measure PFAS?');
  const w = await pageWidth(p);
  await p.close();
  const pass = /Ask/.test(f.text) && box !== null && box.left >= 0 && box.right <= 390 && w <= 390 && shown.state === 'live' && fakeTokens.length === 1 && fakeTokens[0] !== null;
  return { pass, actual: `after Tab: ${f.tag} "${f.text}"; robot box ${JSON.stringify(box)}; page ${w}px; state ${shown.state}; tokens ${JSON.stringify(fakeTokens)}` };
});

if (EXTERNAL) {
  test('F7', 'Turnstile with Cloudflare\'s real script and the always-pass test key (internet)', {
    input: 'WT -> fake API, real challenges.cloudflare.com script, site key 1x00000000000000000000AA',
    expected: `the question reaches the API with the documented test token ${REAL_TEST_TOKEN}`,
  }, async () => {
    fakeTokens = [];
    const { p } = await open(WT.origin, '/');
    await p.$eval('#q', (i) => i.scrollIntoView({ block: 'center' }));
    await sleep(4000);
    const shown = await ask(p, 'What is PFAS NEMP?', 40_000);
    await p.close();
    return { pass: fakeTokens[0] === REAL_TEST_TOKEN && shown.state === 'live', actual: `tokens ${JSON.stringify(fakeTokens)}; state ${shown.state}` };
  });
  test('F8', 'The GitHub address the site links to answers (read-only)', {
    input: PRODUCTION.github,
    expected: 'HTTP 200 (the repository exists; its contents are pushed at launch)',
  }, async () => {
    const r = await fetch(PRODUCTION.github, { headers: { 'user-agent': 'Mozilla/5.0' }, signal: AbortSignal.timeout(20_000) }).catch(() => null);
    return { pass: r?.status === 200, actual: `status ${r?.status}` };
  });
}

test('F9', 'Full build, keyboard only: the connector Copy button and the result it reports', {
  input: 'WU: Tab from the question field until the Copy button, press Enter',
  expected: 'Copy is reachable with a visible ring; after Enter it says "Copied" (at most with "Link copied.") or "Not copied" with its note, never both, never a silent no-op',
}, async () => {
  const { p } = await open(WU.origin, '/');
  await p.$eval('#q', (i) => i.scrollIntoView({ block: 'center' }));
  await p.focus('#q');
  const f = await tabUntil(p, (x) => x.id === 'copy', 20);
  await p.keyboard.press('Enter');
  await sleep(300);
  const after = await p.evaluate(() => ({ label: document.querySelector('#copy')?.textContent ?? '', note: document.querySelector('.copynote')?.textContent ?? '' }));
  await p.close();
  const truthful = (after.label === 'Copied' && (after.note === '' || after.note === 'Link copied.')) || (after.label === 'Not copied' && after.note.length > 10);
  return { pass: f !== null && f.ring && truthful, actual: `reached ${f === null ? 'never' : `#copy ring=${f.ring}`}; after Enter ${JSON.stringify(after)}` };
});

// ==================================================================================================================
// A. The Accuracy page and held-out set 2
// ==================================================================================================================

/** Parses the evaluator's "all:" / "casual:" summary lines. */
function parseEval(out) {
  const line = (tag) => out.split('\n').find((l) => l.startsWith(`${tag}:`)) ?? '';
  const nums = (l) => ({
    hit1: /hit@1 (\d+\/\d+)/.exec(l)?.[1],
    hit5: /hit@5 (\d+\/\d+)/.exec(l)?.[1],
    hit8: /hit@8 (\d+\/\d+)/.exec(l)?.[1],
    recall: /recall@8 ([\d.]+)/.exec(l)?.[1],
    oos: /out-of-scope (\d+\/\d+)/.exec(l)?.[1],
  });
  const misses = out.split('\n').filter((l) => /^\w\d\d (MISS|WRONG)\b/.test(l)).map((l) => l.slice(0, 3));
  return { all: nums(line('all')), casual: nums(line('casual')), misses };
}
const metric = (set, name) => {
  const m = set.metrics.find((x) => x.name === name);
  return m === undefined ? undefined : m.value ?? `${m.hits}/${m.of}`;
};

test('A1', '/accuracy leads with held-out set 2 as the fair score, then its casual questions, then the tuning sets labelled as such', {
  input: 'WL /accuracy, desktop: the "In short" box and the search cards in order',
  expected: 'first search line and first card: held-out set, 12 of 24; second card: casual only, 4 of 12; the other two cards are called "Tuning set"; the page says tuning sets flatter the search',
}, async () => {
  const { p } = await open(WL.origin, '/accuracy');
  const inShort = await p.$$eval('[data-results] p', (ps) => ps.map((x) => x.textContent ?? ''));
  const cards = await p.$$eval('#search ~ .rules .rulecard, section[aria-labelledby="search"] .rulecard', (cs) => cs.map((c) => ({ h: c.querySelector('h3')?.textContent ?? '', text: c.textContent ?? '' })));
  const note = await p.$eval('section[aria-labelledby="search"] > p.note', (e) => e.textContent ?? '').catch(() => '');
  await p.close();
  const searchLines = inShort.filter((l) => /right page came first/.test(l));
  const pass =
    /^Held-out set \(the fair score\): the right page came first for 12 of 24 questions\.$/.test(searchLines[0] ?? '') &&
    /Held-out set \(the fair score\)/.test(cards[0]?.h ?? '') && /12\s*of\s*24|12\/24/.test(cards[0]?.text ?? '') &&
    /casual/i.test(cards[1]?.h ?? '') && /4\s*of\s*12|4\/12/.test(cards[1]?.text ?? '') &&
    cards.slice(2).every((c) => /^Tuning set/.test(c.h)) && cards.length === 4 && /flatter/.test(note);
  return { pass, actual: `in short: ${JSON.stringify(searchLines)}; cards: ${JSON.stringify(cards.map((c) => c.h))}; card 1 shows ${JSON.stringify(around(cards[0]?.text ?? '', /of 24/, 20).slice(0, 3))}` };
});

test('A2', '/accuracy: held-out set 2\'s numbers and misses match a fresh run of the evaluator, and every miss is on the page', {
  input: 'python -m evidenceline.guidance.evaluate --heldout2 (run now) vs accuracy.json vs the rendered /accuracy with the misses opened',
  expected: 'hit@1, hit@5, hit@8, recall@8 and out-of-scope equal for the whole set and the casual subset; the missed ids equal; every missed question is shown with why it missed',
}, async () => {
  const r = spawnSync(python, ['-m', 'evidenceline.guidance.evaluate', '--heldout2'], { cwd: repoDir, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
  const ev = parseEval(r.stdout ?? '');
  const set = accuracy.search.find((s) => s.set === 'held-out-2');
  const casual = accuracy.search.find((s) => s.set === 'held-out-2-casual');
  const file = { hit1: metric(set, 'hit@1'), hit5: metric(set, 'hit@5'), hit8: metric(set, 'hit@8'), recall: metric(set, 'recall@8'), oos: metric(set, 'out-of-scope accuracy') };
  const fileC = { hit1: metric(casual, 'hit@1'), hit5: metric(casual, 'hit@5'), hit8: metric(casual, 'hit@8'), recall: metric(casual, 'recall@8'), oos: undefined };
  const same = JSON.stringify(ev.all) === JSON.stringify(file) && JSON.stringify(ev.casual) === JSON.stringify(fileC);
  const fileMisses = set.misses.map((m) => m.id).sort();
  const sameMisses = JSON.stringify(fileMisses) === JSON.stringify([...ev.misses].sort());
  const { p } = await open(WL.origin, '/accuracy');
  await openAllDetails(p);
  const firstCard = await p.$eval('section[aria-labelledby="search"] .rulecard', (c) => c.textContent ?? '');
  await p.close();
  const norm = (s) => s.replace(/\s+/g, ' ');
  const notShown = set.misses.filter((m) => !norm(firstCard).includes(norm(m.question)) || !norm(firstCard).includes(m.result));
  const pass = r.status === 0 && same && sameMisses && notShown.length === 0 && new RegExp(`missed \\(${set.misses.length}\\)`).test(firstCard);
  return { pass, actual: `evaluator ${JSON.stringify(ev.all)} casual ${JSON.stringify(ev.casual)}; file ${JSON.stringify(file)} casual ${JSON.stringify(fileC)}; misses evaluator ${ev.misses.join(',')} / file ${fileMisses.join(',')}; not shown on page: ${notShown.map((m) => m.id).join(',') || 'none'}` };
});

test('A3', 'Held-out set 2 names every question that is close to a tuning question, not only p07', {
  input: 'evals/guidance_heldout2.json vs the golden set and held-out set 1, content words only (common words removed): Jaccard >= 0.40; or >= 0.25 when both expect the same document page or section; or >= 0.30 when both are out-of-scope questions',
  expected: 'every near copy this check finds, and no other, is listed in accuracy.json (near_copies) and named in the fair set\'s note as "held ~ tuning"; the same run\'s score without them is published (without_near_copies) with exactly those questions left out, and its first-place count is in the note',
  where: 'evals/guidance_heldout2.json (questions); scripts/build_accuracy.py HELDOUT2_NEAR_COPIES and the fair set\'s note; tests/test_accuracy_rechecks.py (threshold)',
}, async () => {
  const load = (n) => readJson(join(repoDir, 'evals', n));
  const g = load('guidance_golden.json');
  const tuning = [...g.in_scope, ...g.out_of_scope, ...load('guidance_heldout.json')];
  const held = load('guidance_heldout2.json');
  const STOP = new Set('a an the of to in on for and or is are be it its this that what which how do does did i we you my our your with at by from as if can any there their they them than then so not no should must would could when where who whom have has had was were will just about into out up some such per each other only also more most very much many'.split(' '));
  const words = (s) => new Set(s.toLowerCase().replace(/[^a-z0-9+ ]/g, ' ').split(/\s+/).filter((w) => w.length > 2 && !STOP.has(w)));
  const jac = (a, b) => {
    let i = 0;
    for (const x of a) if (b.has(x)) i++;
    return i / (a.size + b.size - i);
  };
  const keys = (q) => new Set((Array.isArray(q.expected) ? q.expected : []).flatMap((e) => [...(e.sections ?? []).map((s) => `${e.doc_id ?? e.doc}#${s}`), ...(e.pdf_pages ?? []).map((pg) => `${e.doc_id ?? e.doc}@${pg}`)]));
  const near = [];
  for (const h of held) {
    for (const t of tuning) {
      const s = jac(words(h.question), words(t.question));
      const sharedPlace = [...keys(h)].some((k) => keys(t).has(k));
      const bothOutOfScope = !Array.isArray(h.expected) && !Array.isArray(t.expected);
      if (s >= 0.4 || (s >= 0.25 && sharedPlace) || (s >= 0.3 && bothOutOfScope)) near.push({ h: h.id, t: t.id, s });
    }
  }
  const fair = accuracy.search.find((s) => s.set === 'held-out-2');
  const pair = (h, t) => `${h}~${t}`;
  const found = near.map((n) => pair(n.h, n.t)).sort();
  const listed = (fair.near_copies ?? []).map((c) => pair(c.held_out, c.tuning)).sort();
  const unnamed = near.filter((n) => !fair.note.includes(`${n.h} ~ ${n.t}`)).map((n) => pair(n.h, n.t));
  const strict = fair.without_near_copies ?? null;
  const heldIds = [...new Set(near.map((n) => n.h))].sort();
  const hit1 = strict?.metrics.find((m) => m.name === 'hit@1');
  const leftOutOk = strict !== null && JSON.stringify(strict.left_out) === JSON.stringify(heldIds) && strict.questions === fair.questions - heldIds.length;
  const inNote = hit1 !== undefined && fair.note.includes(`comes first for ${hit1.hits} of ${hit1.of} questions`);
  const pass = found.length > 0 && JSON.stringify(found) === JSON.stringify(listed) && unnamed.length === 0 && leftOutOk && inNote;
  return { pass, actual: `near copies found: ${near.map((n) => `${pair(n.h, n.t)} (${n.s.toFixed(2)})`).join(', ')}; listed: ${listed.join(', ')}; not named in the note: ${unnamed.join(', ') || 'none'}; left out ${JSON.stringify(strict?.left_out ?? null)} of ${fair.questions}, questions after ${strict?.questions ?? 'none'}; first place without them ${hit1 === undefined ? 'missing' : `${hit1.hits} of ${hit1.of}`}, in the note ${inNote}` };
});

test('A4', '/accuracy states how guideline values are checked, and shows the open and the fixed re-check findings', {
  input: 'WL /accuracy with every drawer open',
  expected: 'the PRODUCT.md sentence word for word; the NEMP 3.1 soil note shown as "could not verify" and open; the lookup_limit finding shown with its fix; no "practitioner reviewed" claim',
}, async () => {
  const { p } = await open(WL.origin, '/accuracy');
  await openAllDetails(p);
  const t = (await p.$eval('main', (m) => m.textContent ?? '')).replace(/\s+/g, ' ');
  await p.close();
  const method = 'Guideline values are checked by two independent automated passes against the source pages, not reviewed by a practitioner.';
  const fixed = accuracy.verification.recheck_findings.filter((f) => f.resolution !== '');
  const open_ = accuracy.verification.recheck_findings.filter((f) => f.open);
  const pass =
    t.includes(method) && /could not verify/i.test(t) && open_.every((f) => t.includes(f.label)) && fixed.every((f) => t.includes(f.resolution.replace(/\s+/g, ' ').slice(0, 60))) &&
    !/reviewed by a practitioner(?!\.)|practitioner-reviewed|practitioner reviewed/i.test(t.replace(method, ''));
  return { pass, actual: `method sentence ${t.includes(method)}; open findings ${open_.map((f) => f.label).join(' / ')}; fixed findings ${fixed.map((f) => f.id).join(', ') || 'none'} shown ${fixed.every((f) => t.includes(f.resolution.replace(/\s+/g, ' ').slice(0, 60)))}` };
});

test('A5', '/accuracy on a 390 px phone and by keyboard: the misses open with Enter and nothing scrolls sideways', {
  input: 'WL /accuracy at 390 x 844; Tab to the first "Questions it missed" summary, press Enter',
  expected: 'the list opens; page width 390 before and after, with every drawer open',
}, async () => {
  const { p } = await open(WL.origin, '/accuracy', { vp: PHONE });
  const w0 = await pageWidth(p);
  const f = await tabUntil(p, (x) => x.tag === 'SUMMARY' && /missed/.test(x.text), 200);
  await p.keyboard.press('Enter');
  const opened = await p.$eval('details.misses', (d) => d.open).catch(() => false);
  await openAllDetails(p);
  const w1 = await pageWidth(p);
  await p.close();
  return { pass: f !== null && f.ring && opened && w0 <= 390 && w1 <= 390, actual: `summary reached ${f === null ? 'never' : `"${f.text}" ring=${f.ring}`}; opened ${opened}; width ${w0} then ${w1}` };
});

// ==================================================================================================================
// Run
// ==================================================================================================================

function killTree(child) {
  if (child.exitCode !== null) return;
  if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
  else child.kill('SIGTERM');
}

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
  for (const w of wranglers) killTree(w.child);
  killTree(apiChild);
  fake.close();
}

const count = (v) => results.filter((r) => r.verdict === v).length;
console.log(`\nadversarial_launch: ${results.length} cases: ${count('PASS')} passed, ${count('XFAIL')} failed as marked (known product failures), ${count('FAIL')} failed, ${count('XPASS')} passed unexpectedly (remove the mark).`);
for (const r of results.filter((x) => x.verdict === 'XFAIL')) console.log(`  known failure ${r.id} (${r.xfail.split(':')[0]}): ${r.where}`);
process.exit(count('FAIL') + count('XPASS') === 0 ? 0 : 1);
