// Smoke test for the built site. Serves web/dist with `vite preview` and drives it in the local Chrome.
//
//   npm run build && npm run smoke
//
// Ports every check from the design prototype's test (test-landing.mjs), then checks each route, the labelling,
// the no-index setup, phone layout, and that the sample-site page shows exactly what the lab files say.
// Then: the tidy preview and /tidy page against tidy.json, no dead links on any page, the "Try it" box with prepared
// answers (a fixture) and with a mocked live API (a second build with VITE_API_BASE set, and the Turnstile check on
// Cloudflare's always-pass test key, its script replaced by a local stand-in), the offline build (nothing live switched
// on), the launch build (npm run build:launch: only the repository link; the box and the connector say "soon"),
// web/dist as the production build (.env.production: same-origin /api/ask, the /mcp link, the repository), and the
// accuracy page with a sample results file. Nothing here reaches the internet: API calls and fixtures are intercepted
// (tests/turnstile.mjs checks the real Turnstile script separately).
// Screenshots of every page (desktop and phone) are saved to tests/screens/.
// CHROME_PATH overrides the browser location.

import { existsSync, mkdirSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { build, preview } from 'vite';
import { buildLaunch } from '../scripts/build-launch.mjs';

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const distDir = join(webDir, 'dist');
const liveDir = join(webDir, 'node_modules', '.tmp', 'dist-live');
const offlineDir = join(webDir, 'node_modules', '.tmp', 'dist-offline');
const launchDir = join(webDir, 'node_modules', '.tmp', 'dist-launch');
const screensDir = join(webDir, 'tests', 'screens');
const fixturesDir = join(webDir, 'tests', 'fixtures');
const labDir = resolve(webDir, '..', 'src', 'evidenceline', 'data');
const chromePath = process.env.CHROME_PATH ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const CONCEPT_NOTICE = 'not affiliated with Western Environmental';
const DASHES = /[\u2013\u2014]/;
const ROUTE_PATHS = new Set(['/', '/sample-site', '/tidy', '/sources', '/accuracy']);
const fixture = (name) => readFileSync(join(fixturesDir, name), 'utf8');
/** Cloudflare's documented always-pass Turnstile test site key, and the token its test keys give. */
const TURNSTILE_TEST_KEY = '1x00000000000000000000AA';
const TURNSTILE_TEST_TOKEN = 'XXXX.DUMMY.TOKEN.XXXX';
const TURNSTILE_HOST = 'challenges.cloudflare.com';
/**
 * A local stand-in for Cloudflare's Turnstile script (the smoke test never uses the internet). It follows the
 * documented explicit-render API: calls the onload function named in its URL, renders a 65 px box, gives the test
 * token shortly after render and after each reset, and counts calls in window.__turnstileStub.
 */
const TURNSTILE_STUB = `(() => {
  const onload = new URL(document.currentScript.src).searchParams.get('onload');
  const stub = (window.__turnstileStub = { renders: [], resets: 0, removes: 0 });
  const widgets = new Map();
  const give = (id) => setTimeout(() => widgets.get(id)?.callback(${JSON.stringify(TURNSTILE_TEST_TOKEN)}), 80);
  window.turnstile = {
    render(el, options) {
      const id = 'w' + (widgets.size + 1);
      widgets.set(id, options);
      stub.renders.push({ sitekey: options.sitekey, size: options.size, action: options.action });
      const box = document.createElement('div');
      box.style.cssText = 'box-sizing:border-box;width:100%;min-width:300px;height:65px;border:1px solid #ccc;background:#fafafa;font:14px sans-serif;padding:22px 12px';
      box.textContent = 'Stand-in for the Turnstile check';
      el.append(box);
      give(id);
      return id;
    },
    reset(id) { stub.resets += 1; give(id); },
    remove(id) { stub.removes += 1; widgets.delete(id); },
  };
  window[onload]();
})();`;
const turnstileScript = { status: 200, contentType: 'application/javascript', body: TURNSTILE_STUB };
/** What web/.env.production must give the published site. */
const PRODUCTION = { github: 'https://github.com/autopilotyourworkflow/evidenceline', mcp: 'https://evidenceline.autopilotyourworkflow.com/mcp', apiBase: '/' };
/** The Turnstile site key web/.env.production gives the build: empty until the robot check is switched on. */
const PRODUCTION_SITE_KEY = (/^VITE_TURNSTILE_SITE_KEY=(.*)$/m.exec(readFileSync(join(webDir, '.env.production'), 'utf8'))?.[1] ?? '').trim();
const tidyData = JSON.parse(readFileSync(join(webDir, 'public', 'data', 'tidy.json'), 'utf8'));
const soilData = JSON.parse(readFileSync(join(labDir, 'fds01_site', 'soil_criteria.json'), 'utf8'));

if (!existsSync(join(distDir, 'index.html'))) {
  console.error('smoke: web/dist is missing. Run npm run build first.');
  process.exit(1);
}
mkdirSync(screensDir, { recursive: true });

const results = [];
const ok = (name, pass, detail = '') => results.push({ name, pass: !!pass, detail });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const server = await preview({ root: webDir, logLevel: 'silent', preview: { port: 4317, strictPort: false, open: false } });
const base = (server.resolvedUrls?.local[0] ?? 'http://localhost:4317/').replace(/\/$/, '');
const b = await puppeteer.launch({ executablePath: chromePath, headless: true });

/**
 * Opens a page. `respond` maps a path such as "/data/answers.json" to a canned response; `handle(request)` may answer
 * any other request itself and return true (used to mock the live API). Everything else goes to the preview server.
 */
async function open(vp, path = '/', { origin = base, respond = {}, handle = null } = {}) {
  const p = await b.newPage();
  const errors = [];
  const failed = [];
  const requests = [];
  p.on('pageerror', (e) => errors.push(e.message));
  p.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text());
  });
  p.on('requestfailed', (r) => failed.push(r.url()));
  p.on('request', (r) => requests.push(r.url()));
  if (Object.keys(respond).length > 0 || handle !== null) {
    await p.setRequestInterception(true);
    p.on('request', async (r) => {
      if (r.isInterceptResolutionHandled()) return;
      const canned = respond[new URL(r.url()).pathname];
      if (canned !== undefined) return r.respond(canned);
      if (handle !== null && (await handle(r))) return undefined;
      return r.continue();
    });
  }
  await p.setViewport(vp);
  await p.goto(origin + path, { waitUntil: 'networkidle0' });
  await p.evaluate(() => document.fonts.ready);
  return { p, errors, failed, requests };
}

const json = (body, status = 200, headers = {}) => ({ status, contentType: 'application/json', headers, body: typeof body === 'string' ? body : JSON.stringify(body) });

/** Links a reader could follow that lead nowhere: empty or "#" targets, missing in-page targets, missing files. */
async function deadLinks(p, origin, landingIds) {
  const links = await p.evaluate(() => [...document.querySelectorAll('a')].map((a) => ({ href: a.getAttribute('href'), text: a.textContent.trim().slice(0, 40) })));
  const dead = [];
  for (const link of links) {
    const href = link.href ?? '';
    if (href === '' || href === '#' || href.startsWith('javascript:')) {
      dead.push(link);
    } else if (href.startsWith('#')) {
      if (!(await p.evaluate((id) => !!document.getElementById(id), decodeURIComponent(href.slice(1))))) dead.push(link);
    } else if (href.startsWith('/')) {
      const [path, hash] = href.split('#');
      if (ROUTE_PATHS.has(path)) {
        if (hash && path === '/' && !landingIds.includes(hash)) dead.push(link);
      } else {
        const r = await fetch(origin + path);
        if (!r.ok || (r.headers.get('content-type') ?? '').includes('text/html')) dead.push(link);
      }
    } else if (!/^https:\/\/[^/]+\.[a-z]{2,}/i.test(href)) {
      // External links must be absolute https URLs. They are not fetched: the smoke test does not use the internet.
      dead.push(link);
    }
  }
  return { count: links.length, dead };
}
let landingIds = [];

async function loadAllImages(p) {
  await p.evaluate(async () => {
    for (const i of document.images) {
      i.scrollIntoView();
      await new Promise((r) => (i.complete ? r() : i.addEventListener('load', r, { once: true })));
    }
    window.scrollTo(0, 0);
  });
}

async function shot(p, name) {
  await p.evaluate(() => {
    document.documentElement.style.scrollBehavior = 'auto';
    window.scrollTo(0, 0);
  });
  await sleep(250);
  await p.screenshot({ path: join(screensDir, `${name}.png`), fullPage: true });
}

/** Every piece of text a reader can meet: visible text, hidden explanations, alt, aria-label and placeholder. */
const allText = (p) =>
  p.evaluate(() =>
    [
      document.title,
      document.body.textContent ?? '',
      ...[...document.querySelectorAll('[alt],[aria-label],[placeholder],[title]')].flatMap((e) => [
        e.getAttribute('alt') ?? '',
        e.getAttribute('aria-label') ?? '',
        e.getAttribute('placeholder') ?? '',
        e.getAttribute('title') ?? '',
      ]),
    ].join('\n'),
  );

// ---------- landing, desktop (ported from test-landing.mjs) ----------
// answers.json is answered with an unusable body here, so the box shows the two hand-written examples these checks expect.
// web/dist is the production build (.env.production), so the question box posts to this site's own /api/ask, which is
// answered here with a canned result; the offline build is checked further down.
{
  const sameOrigin = [];
  const sameOriginAnswer = { status: 'not_covered', explanation: 'Canned production-build answer.', answer: null, citations: [], guideline_values: [], notes: [] };
  const handleSameOrigin = async (r) => {
    if (r.method() !== 'POST' || new URL(r.url()).pathname !== '/api/ask') return false;
    sameOrigin.push(r.url());
    sameOriginTokens.push(JSON.parse(r.postData() ?? '{}').turnstile_token ?? null);
    await r.respond(json(sameOriginAnswer));
    return true;
  };
  const sameOriginTokens = [];
  // With a site key in .env.production the build loads Turnstile; the local stand-in replaces Cloudflare's script,
  // whose real widget refuses this preview's localhost address.
  const { p, errors, failed, requests } = await open({ width: 1440, height: 900 }, '/', {
    respond: { '/data/answers.json': json({}), '/turnstile/v0/api.js': turnstileScript },
    handle: handleSameOrigin,
  });
  landingIds = await p.evaluate(() => [...document.querySelectorAll('[id]')].map((e) => e.id));
  await loadAllImages(p);
  const imgs = await p.evaluate(() => [...document.images].map((i) => ({ src: i.getAttribute('src'), w: i.naturalWidth, alt: i.alt })));
  ok('All content images load', imgs.length === 3 && imgs.every((i) => i.w > 0), imgs.map((i) => `${i.src}:${i.w}px`).join(', '));
  ok('Every image has alt text', imgs.every((i) => i.alt && i.alt.length > 5));
  const bgs = await p.evaluate(async () => {
    const urls = ['img/hero.jpg', 'img/aerial.jpg'];
    return Promise.all(
      urls.map(
        (u) =>
          new Promise((res) => {
            const i = new Image();
            i.onload = () => res(`${u}:${i.naturalWidth}px`);
            i.onerror = () => res(`${u}:FAILED`);
            i.src = u;
          }),
      ),
    );
  });
  ok('Background photos load', bgs.every((x) => !x.includes('FAILED')), bgs.join(', '));
  const cssBgs = await p.evaluate(() => [getComputedStyle(document.querySelector('.phero')).backgroundImage, getComputedStyle(document.querySelector('.band')).backgroundImage]);
  ok('Hero and trust band use the photos as backgrounds', cssBgs[0].includes('/img/hero.jpg') && cssBgs[1].includes('/img/aerial.jpg'), cssBgs.join(' | '));
  ok('Heading reads in plain English', (await p.$eval('h1', (h) => h.textContent)).includes('backed by the data'));

  const anchors = await p.evaluate(() =>
    [...document.querySelectorAll('.nav a')].map((a) => ({ href: a.getAttribute('href'), exists: !!document.querySelector(a.getAttribute('href')) })),
  );
  ok('Every menu link goes to a real section', anchors.length === 5 && anchors.every((a) => a.exists), anchors.map((a) => a.href).join(' '));

  const step = () => p.$eval('#count', (c) => c.textContent);
  ok('Walkthrough starts at step 1', (await step()) === 'Step 1 of 5');
  ok('Back button hidden on step 1', await p.$eval('#back', (x) => getComputedStyle(x).display === 'none'));
  for (let k = 2; k <= 5; k++) {
    await p.click('#next');
    await sleep(120);
  }
  ok('Next reaches step 5', (await step()) === 'Step 5 of 5');
  ok('Only one step visible at a time', await p.evaluate(() => document.querySelectorAll('.pane.on').length === 1));
  ok('Last button offers to start again', (await p.$eval('#next', (x) => x.textContent)) === 'Start again');
  await p.click('#back');
  await sleep(120);
  ok('Back goes to step 4', (await step()) === 'Step 4 of 5');
  await p.click('#steps button[data-i="1"]');
  await sleep(120);
  ok('Clicking a step name jumps to it', (await step()) === 'Step 2 of 5');
  const rulers = await p.evaluate(() => {
    document.querySelector('#steps button[data-i="3"]').click();
    return [...document.querySelectorAll('.verdict')].map((v) => v.textContent);
  });
  ok('Step 4 shows under the old rule and over the new rule', rulers[0].includes('under') && rulers[1].includes('over'), rulers.join(' | '));

  // glossary
  await p.click('#steps button[data-i="0"]');
  await sleep(120);
  const term = await p.$('.pane.on .term');
  await term.click();
  await sleep(80);
  ok(
    'Tapping an underlined word shows its explanation',
    await p.evaluate(() => {
      const t = document.querySelector('.pane.on .term[aria-expanded="true"] .tip');
      return !!t && getComputedStyle(t).display === 'block';
    }),
  );
  await p.keyboard.press('Escape');
  await sleep(60);
  ok('Escape closes the explanation', await p.evaluate(() => !document.querySelector('.term[aria-expanded="true"]')));
  // extra: hover shows, click pins, a click elsewhere closes
  await p.mouse.move(5, 5);
  await sleep(60);
  await term.hover();
  await sleep(60);
  const hoverShows = await p.evaluate(() => !!document.querySelector('.pane.on .term[aria-expanded="true"]'));
  await p.mouse.move(5, 5);
  await sleep(60);
  const hoverHides = await p.evaluate(() => !document.querySelector('.term[aria-expanded="true"]'));
  ok('Hovering an underlined word shows it, moving away hides it', hoverShows && hoverHides);
  await term.click();
  await p.mouse.move(5, 5);
  await sleep(60);
  const pinnedStays = await p.evaluate(() => !!document.querySelector('.pane.on .term[aria-expanded="true"]'));
  await p.click('.summary');
  await sleep(60);
  const clickAwayCloses = await p.evaluate(() => !document.querySelector('.term[aria-expanded="true"]'));
  ok('A tapped explanation stays open until you click elsewhere', pinnedStays && clickAwayCloses);

  // technical detail
  await p.evaluate(() => {
    const d = document.querySelector('.pane.on details');
    d.querySelector('summary').click();
  });
  ok('Technical detail opens', await p.evaluate(() => document.querySelector('.pane.on details').open));

  // decision: accept
  await p.click('#steps button[data-i="4"]');
  await sleep(120);
  await p.click('#accept');
  await sleep(80);
  const s1 = await p.$eval('#s1', (s) => s.textContent);
  const log = await p.$eval('#log', (l) => ({ t: l.textContent, shown: getComputedStyle(l).display !== 'none' }));
  ok('"Use the suggested sentence" replaces the flagged sentence', s1.startsWith('PFOS in well MB2 was slightly lower'));
  ok('The decision is recorded', log.shown && log.t.startsWith('Recorded: suggested sentence used'), log.t);
  await p.click('#next');
  await sleep(150);
  ok('Start again goes back to step 1', (await step()) === 'Step 1 of 5');
  await p.click('#steps button[data-i="4"]');
  await sleep(150);
  const reset = await p.evaluate(() => ({
    cls: document.getElementById('s1').className,
    log: getComputedStyle(document.getElementById('log')).display,
    sug: getComputedStyle(document.getElementById('suggest')).display,
    text: document.getElementById('s1').textContent,
  }));
  ok('Start again clears the earlier decision', reset.cls === 'bad' && reset.log === 'none' && reset.sug !== 'none' && reset.text.includes('increased'), JSON.stringify(reset));

  // ask
  await p.click('.chips button[data-a="0"]');
  await sleep(60);
  const a0 = await p.$eval('#answer', (a) => a.textContent);
  ok('Example question 1 answers 0.008 with its page', a0.includes('0.008') && a0.includes('page 49'));
  await p.click('.chips button[data-a="1"]');
  await sleep(60);
  ok('Example question 2 declines to call a site contaminated', (await p.$eval('#answer', (a) => a.textContent)).includes("won't answer"));
  await p.$eval('#q', (i) => i.select());
  await p.type('#q', 'How deep should monitoring wells be?');
  await p.keyboard.press('Enter');
  await p.waitForFunction(() => document.querySelector('#answer')?.getAttribute('data-state') !== 'loading', { timeout: 5000 }).catch(() => undefined);
  ok(
    'Production build: a typed question is posted to this site\'s own /api/ask (same origin)',
    sameOrigin.length === 1 && sameOrigin[0] === `${base}/api/ask` && requests.filter((u) => u.includes('/api/ask')).every((u) => u.startsWith(base)),
    JSON.stringify(sameOrigin),
  );
  ok('Production build: the answer from the same-origin service is shown', (await p.$eval('#answer', (a) => a.getAttribute('data-state'))) === 'live');
  if (PRODUCTION_SITE_KEY === '') {
    ok(
      'Production build: no robot check and no Turnstile script while no site key is set, and no "soon" note',
      !(await p.$('#robot')) && !requests.some((u) => u.includes(TURNSTILE_HOST)) && !(await p.$('#coming-soon')),
    );
  } else {
    const stub = await p.evaluate(() => window.__turnstileStub);
    ok(
      'Production build: the robot check uses the site key from .env.production, the question carries its token, and no "soon" note',
      !!(await p.$('#robot')) && stub?.renders.length === 1 && stub.renders[0].sitekey === PRODUCTION_SITE_KEY && sameOriginTokens[0] === TURNSTILE_TEST_TOKEN && !(await p.$('#coming-soon')),
      JSON.stringify({ renders: stub?.renders, tokens: sameOriginTokens }),
    );
  }
  ok('Without answers.json the small print says the examples are hand-written', (await p.$eval('#prepared-note', (e) => e.textContent)).includes('written by hand'));
  // The connector and the code repository come from .env.production in this build.
  ok('Production build: the connector card shows the site\'s own /mcp link', (await p.$eval('#mcp', (c) => c.textContent).catch(() => '')) === PRODUCTION.mcp);
  ok('Production build: "Code on GitHub" links to the public repository', (await p.$eval('#github', (a) => a.getAttribute('href')).catch(() => '')) === PRODUCTION.github);
  ok('Production build: the "Try it" line offers two ways', (await p.$eval('#try .lead', (e) => e.textContent ?? '')).startsWith('Two ways'));
  const kindOf = (dir) => {
    try {
      return JSON.parse(readFileSync(join(dir, 'build.json'), 'utf8')).kind;
    } catch {
      return 'unreadable';
    }
  };
  ok('Production build: dist/build.json records the full build (npm run verify:full)', kindOf(distDir) === 'full', kindOf(distDir));
  // The hand-written PFOS example (no answers.json): both values in neutral words, no rule picked.
  await p.click('.chips button[data-a="0"]');
  await sleep(60);
  const pfosFallback = await p.$eval('#answer', (a) => a.textContent ?? '');
  ok(
    'Without answers.json the PFOS example gives both values under their rules, picks neither and does not call either rule out of date',
    pfosFallback.includes('0.008 µg/L') && pfosFallback.includes('0.07 µg/L') && pfosFallback.includes('picks neither') && pfosFallback.includes("scientist's call") && !/still/i.test(pfosFallback),
    pfosFallback,
  );

  // The "Tidy lab results" preview lists what the real tool found (tidy.json), in short plain words.
  const preview = await p.evaluate(() => ({
    count: document.querySelector('.app-result[data-count]')?.getAttribute('data-count'),
    result: document.querySelector('.app-result[data-count]')?.textContent ?? '',
    items: [...document.querySelectorAll('.issues li')].map((li) => li.textContent),
    label: document.querySelector('.app[aria-label^="Preview of the Tidy"]')?.getAttribute('aria-label') ?? '',
    full: document.querySelector('.preview-note a[href="/tidy"]')?.textContent ?? '',
  }));
  const expectedItems = tidyData.review_items.length;
  ok('Tidy preview: item count read from tidy.json', expectedItems === 6 && Number(preview.count) === expectedItems && preview.items.length === expectedItems && preview.result.includes(`${expectedItems} things for you to check`), JSON.stringify(preview));
  const wanted = ['written "SB3-15"', 'different unit', 'differs by 62%', 'after its time limit', 'picked up PFOS', 'detection limit is too high'];
  ok('Tidy preview: all six items in plain words', wanted.every((w, k) => (preview.items[k] ?? '').includes(w)), preview.items.join(' | '));
  ok('Tidy preview: the label for screen readers gives the count', preview.label.includes(`${expectedItems} items`), preview.label);
  ok('Tidy preview: links to the full result', preview.full === 'See the full result');
  const links = await deadLinks(p, base, landingIds);
  ok('Landing: no dead links', links.count > 10 && links.dead.length === 0, JSON.stringify(links.dead));

  ok('No script errors', errors.length === 0, errors.join(' | '));
  ok('No failed downloads', failed.filter((u) => !u.includes('fonts.g')).length === 0, failed.join(' | '));

  // extra: fonts are self-hosted, nothing is fetched from Google
  ok('Roboto is self-hosted (no Google Fonts request)', !requests.some((u) => /fonts\.(googleapis|gstatic)\.com/.test(u)) && requests.some((u) => /roboto-latin-300.*\.woff2/.test(u)));
  ok('Roboto Light is in use', await p.evaluate(() => document.fonts.check('300 16px Roboto') && [...document.fonts].some((f) => f.family.includes('Roboto') && f.status === 'loaded')));

  // extra: the "For scientists" links lead to the detail pages
  const sci = await p.evaluate(() => [...document.querySelectorAll('.under a')].map((a) => a.getAttribute('href')));
  ok('"For scientists" links point at the three pages', ['/sample-site', '/sources', '/accuracy'].every((h) => sci.includes(h)), sci.join(' '));
  ok('"For scientists" links include the full tidy example', sci.includes('/tidy'), sci.join(' '));
  await p.click('.under a[href="/sources"]');
  await p.waitForSelector('[data-ready]');
  ok('Clicking a "For scientists" link opens its page in place', (await p.evaluate(() => location.pathname)) === '/sources' && (await p.$eval('h1', (h) => h.textContent)).includes('Guideline sources'));
  await p.goBack();
  await p.waitForSelector('.phero');
  ok('Back returns to the landing page', (await p.evaluate(() => location.pathname)) === '/');

  const text = await allText(p);
  ok('Landing: no em or en dashes in any text', !DASHES.test(text), (text.match(/.{0,30}[\u2013\u2014].{0,30}/g) ?? []).join(' | '));
  ok('Landing: labelled as a concept, not affiliated', text.includes('Not affiliated with Western Environmental'));
  await p.close();
}

// ---------- decision: keep with a reason (ported) ----------
{
  const { p } = await open({ width: 1440, height: 900 });
  await p.evaluate(() => document.querySelector('#steps button[data-i="4"]').click());
  await sleep(60);
  await p.click('#keep');
  await sleep(60);
  ok('Keeping opens the reason box, focused', await p.evaluate(() => document.activeElement?.id === 'rtext'));
  await p.click('#save');
  await sleep(60);
  ok('Keeping without a reason is not allowed', await p.$eval('#log', (l) => getComputedStyle(l).display === 'none'));
  await p.type('#rtext', 'Report uses the 2025 edition as agreed with the auditor');
  await p.click('#save');
  await sleep(60);
  ok('Keeping with a reason records the reason', (await p.$eval('#log', (l) => l.textContent)).includes('agreed with the auditor'));
  await p.close();
}

// ---------- landing, phone (ported) ----------
const phone = { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true };
{
  const { p, errors } = await open(phone);
  // Compared with the 390 px viewport, not innerWidth: in mobile emulation innerWidth grows with an overflowing page,
  // which is how the prototype's own check missed a 607 px wide "Try it" section.
  const pageWidth = () => p.evaluate(() => document.documentElement.scrollWidth);
  ok('Phone: no sideways scrolling', (await pageWidth()) === 390, `page is ${await pageWidth()} px wide`);
  const small = await p.evaluate(() =>
    [...document.querySelectorAll('.btn, .steps button, .chips button')]
      .filter((e) => e.offsetParent)
      .map((e) => {
        const r = e.getBoundingClientRect();
        return { t: e.textContent.trim().slice(0, 24), h: Math.round(r.height) };
      })
      .filter((x) => x.h < 40),
  );
  ok('Phone: buttons are big enough to tap (40px+)', small.length === 0, JSON.stringify(small));
  await p.evaluate(() => {
    document.documentElement.style.scrollBehavior = 'auto';
    const r = document.querySelector('#next').getBoundingClientRect();
    window.scrollBy(0, r.top - 400);
  });
  await sleep(200);
  const bx = await (await p.$('#next')).boundingBox();
  await p.touchscreen.tap(bx.x + bx.width / 2, bx.y + bx.height / 2);
  await sleep(200);
  ok('Phone: Next works by tapping', (await p.$eval('#count', (c) => c.textContent)) === 'Step 2 of 5');
  ok('Phone: no script errors', errors.length === 0, errors.join(' | '));
  await p.close();
}

// ---------- the detail pages and a missing page ----------
const ROUTES = [
  { path: '/sample-site', name: 'sample-site', h1: 'Full sample site' },
  { path: '/tidy', name: 'tidy', h1: 'Tidy lab results: the full example' },
  { path: '/sources', name: 'sources', h1: 'Guideline sources' },
  { path: '/accuracy', name: 'accuracy', h1: 'Accuracy results' },
  { path: '/no-such-page', name: 'not-found', h1: 'Page not found', noData: true },
];

for (const route of ROUTES) {
  const { p, errors, failed } = await open({ width: 1440, height: 900 }, route.path);
  if (!route.noData) await p.waitForSelector('[data-ready]', { timeout: 5000 }).catch(() => undefined);
  const info = await p.evaluate(() => ({
    h1: document.querySelector('h1')?.textContent ?? '',
    ready: !!document.querySelector('[data-ready]'),
    robots: document.querySelector('meta[name="robots"]')?.getAttribute('content') ?? '',
    notice: document.querySelector('.notice')?.textContent ?? '',
    footer: document.querySelector('footer')?.textContent ?? '',
    title: document.title,
  }));
  ok(`${route.path}: renders its page`, info.h1.includes(route.h1) && (route.noData || info.ready), `${info.h1} ready=${info.ready}`);
  ok(`${route.path}: no script errors or failed downloads`, errors.length === 0 && failed.length === 0, [...errors, ...failed].join(' | '));
  ok(`${route.path}: noindex and concept labelling`, info.robots.includes('noindex') && info.notice.includes(CONCEPT_NOTICE) && info.notice.includes('Fictional site, public guidance only') && info.footer.includes('Not affiliated'), JSON.stringify(info));
  ok(`${route.path}: page title set`, info.title.endsWith('| Evidenceline'), info.title);
  const text = await allText(p);
  ok(`${route.path}: no em or en dashes in any text`, !DASHES.test(text), (text.match(/.{0,30}[\u2013\u2014].{0,30}/g) ?? []).join(' | '));
  const head = await p.evaluate(() => ({
    first: document.querySelector('.pagehead .wrap')?.firstElementChild?.tagName ?? '',
    eyebrow: !!document.querySelector('.eyebrow'),
    back: [...document.querySelectorAll('.pagehead a, main .linkrow a')].some((a) => a.getAttribute('href') === '/'),
  }));
  ok(`${route.path}: no label above the heading, and a plain way back to the landing`, head.first === 'H1' && !head.eyebrow && head.back, JSON.stringify(head));
  const links = await deadLinks(p, base, landingIds);
  ok(`${route.path}: no dead links`, links.dead.length === 0, JSON.stringify(links.dead));
  await shot(p, `${route.name}-desktop`);

  if (route.name === 'sample-site') {
    // Every PFOS and PFHxS value, lab report id and row shown must match the lab files exactly.
    const expected = [];
    for (const name of readdirSync(labDir).filter((f) => /^mb2_round\d+_lab\.csv$/.test(f)).sort()) {
      const lines = readFileSync(join(labDir, name), 'utf8').trim().split(/\r?\n/);
      const header = lines[0].split(',');
      lines.slice(1).forEach((line, k) => {
        const cells = Object.fromEntries(line.split(',').map((c, j) => [header[j], c]));
        if (cells.analyte === 'PFOS' || cells.analyte === 'PFHxS') expected.push({ file: name, row: k + 2, id: cells.lab_report_id, value: cells.result });
      });
    }
    const shown = await p.evaluate(() => [...document.querySelectorAll('.dtable.stack tbody tr')].map((tr) => tr.textContent));
    const missing = expected.filter((e) => !shown.some((row) => row.includes(e.id) && row.includes(e.value) && row.includes(`${e.file}, row ${e.row}`)));
    ok('/sample-site: every PFOS and PFHxS value matches its lab file, report id and row', expected.length === 8 && missing.length === 0, JSON.stringify(missing));
    ok('/sample-site: says the data is synthetic', (await p.$eval('.synth', (s) => s.textContent)).includes('Synthetic data'));
  }
  if (route.name === 'tidy') {
    const page = await p.evaluate(() => ({
      stats: [...document.querySelectorAll('.stats b')].map((b) => b.textContent),
      files: [...document.querySelectorAll('#files ~ .tablewrap tbody tr')].map((tr) => tr.textContent),
      items: [...document.querySelectorAll('.ritem')].map((li) => ({ plain: li.querySelector('.plain')?.textContent ?? '', tech: li.querySelector('.tech')?.textContent ?? '' })),
      sections: [...document.querySelectorAll('main h2')].map((h) => h.textContent),
      synth: document.querySelector('.synth')?.textContent ?? '',
    }));
    const t = tidyData;
    const checked = typeof t.checked_not_flagged === 'number' ? t.checked_not_flagged : t.checked_not_flagged.length;
    ok('/tidy: files read and counts come from tidy.json', page.stats.join(',') === [t.files.length, t.results, t.review_items.length, checked].join(',') && t.files.every((f) => page.files.some((row) => row.includes(f.file) && row.includes(String(f.data_rows)))), JSON.stringify(page.stats));
    ok('/tidy: one plain line per review item', page.items.length === t.review_items.length && page.items.every((i) => i.plain.length > 30 && !i.plain.includes('LOR') && !i.plain.includes('RPD')), page.items.map((i) => i.plain).join(' | '));
    const techOk = t.review_items.every((item, k) => {
      const tech = page.items[k]?.tech ?? '';
      const rows = item.evidence.map((e) => /row (\d+)$/.exec(e)?.[1]).filter(Boolean);
      return tech.includes('What was found') && tech.includes('The rule') && tech.includes('Source') && tech.includes('What the scientist decides') && rows.every((r) => tech.includes(r)) && tech.includes(item.scientist_decides.slice(0, 40));
    });
    ok("/tidy: each technical detail has the finding, evidence rows, rule, source and the scientist's decision", techOk);
    await p.click('.ritem details summary');
    ok('/tidy: technical detail opens', await p.evaluate(() => document.querySelector('.ritem details').open));
    ok('/tidy: shows "Checked and not flagged" and "Not checked"', page.sections.includes('Checked and not flagged') && page.sections.includes('Not checked'), page.sections.join(' | '));
    ok('/tidy: every "not checked" reason is shown', t.not_checked.every((n) => text.includes(n.reason)));
    ok('/tidy: says the data is synthetic', page.synth.includes('Synthetic data'));
  }
  if (route.name === 'sources') {
    const body = await p.$eval('.rules', (r) => r.textContent);
    ok('/sources: both rules with values, tables and pages', ['0.07 µg/L', '0.56 µg/L', '0.008 µg/L', '0.03 µg/L', '0.2 µg/L', '57 (PDF page)', '49 (printed page)', 'Table 4'].every((s) => body.includes(s)));
    ok('/sources: shows both rules without choosing', (await p.$eval('.callout', (c) => c.textContent)).includes('does not choose'));
    await p.waitForSelector('.soil [data-ready]', { timeout: 5000 }).catch(() => undefined);
    const soil = await p.evaluate(() => ({ head: document.getElementById('soil')?.textContent ?? '', rows: [...document.querySelectorAll('.soiltable tbody tr')].map((tr) => tr.textContent), note: document.querySelector('.soil .note')?.textContent ?? '' }));
    const soilOk = soilData.criteria.every((c) => soil.rows.some((row) => row.includes(`${c.value} ${c.unit}`) && row.includes(c.table) && row.includes(c.page) && row.includes(c.document)));
    ok('/sources: soil HIL A values with document, table and page, read from the package', soil.rows.length === soilData.criteria.length && soilOk && ['0.003 mg/kg', '0.06 mg/kg', 'Table 1A(1)'].every((s) => soil.rows.join(' ').includes(s)), JSON.stringify(soil.rows.slice(0, 2)));
    ok('/sources: soil values labelled as used only in the tidy step', soil.head.includes('used only inside the tidy step') && soil.note.includes('check that detection limits'), soil.head);
    ok('/sources: no longer says soil criteria are not loaded', !text.includes('Only drinking-water values for groundwater are loaded') && !/soil criteria are not/i.test(text));
  }
  if (route.name === 'accuracy') {
    const real = JSON.parse(readFileSync(join(distDir, 'data', 'accuracy.json'), 'utf8'));
    if (real.status === 'pending') {
      const pending = await p.$eval('.pending', (e) => e.textContent);
      ok('/accuracy: placeholder says results will appear, with no numbers', pending.includes('Results will appear here when the automated tests run') && !/\d/.test(pending));
    } else {
      ok('/accuracy: the published results render (placeholder replaced)', await p.evaluate(() => !!document.querySelector('[data-results]') && !document.querySelector('.pending')));
      // The real file, as scripts/build_accuracy.py wrote it: every number on the page must be the file's.
      const page = await p.evaluate(() => ({
        short: document.querySelector('[data-results]')?.textContent ?? '',
        misses: [...document.querySelectorAll('details.misses')].map((d) => ({ summary: d.querySelector('summary')?.textContent ?? '', items: d.querySelectorAll('li').length })),
        notes: [...document.querySelectorAll('#notes + .note + .tablewrap tbody tr')].map((r) => ({ text: r.textContent, fail: r.classList.contains('failrow') })),
        findings: [...document.querySelectorAll('#findings + .note + .tablewrap tbody tr')].map((r) => ({ text: r.textContent, fail: r.classList.contains('failrow') })),
        cards: [...document.querySelectorAll('#search ~ .rules .rulecard')].map((c) => c.textContent),
      }));
      const passed = real.suites.reduce((n, s) => n + s.passed, 0);
      const failedCount = real.suites.reduce((n, s) => n + s.failed, 0);
      ok('/accuracy (real): test totals match accuracy.json', page.short.includes(`${passed} of ${passed + failedCount} automated tests passed`), page.short);
      const values = real.verification?.values ?? [];
      ok('/accuracy (real): the guideline value count matches accuracy.json', values.length === 0 || page.short.includes(`of ${values.length} guideline values matched the source page in every pass`), page.short);
      for (const set of real.search ?? []) {
        const hit1 = set.metrics.find((m) => m.name === 'hit@1');
        if (set.part_of) {
          // A group of another set: its own card, with its counts, and its misses listed once, with the whole set.
          ok(`/accuracy (real): ${set.label} has its own card with hit@1 ${hit1.hits} of ${hit1.of}`, page.cards.some((c) => c.includes(set.label) && c.includes(`${hit1.hits} of ${hit1.of}`)) && set.misses.length === 0, JSON.stringify(page.cards.map((c) => c.slice(0, 60))));
          continue;
        }
        ok(`/accuracy (real): ${set.label} shows hit@1 ${hit1.hits} of ${hit1.of} and lists its ${set.misses.length} misses`, page.short.includes(`${set.label}: the right page came first for ${hit1.hits} of ${hit1.of}`) && (set.misses.length === 0 || page.misses.some((m) => m.summary.includes(`(${set.misses.length})`) && m.items === set.misses.length)), JSON.stringify(page.misses));
      }
      const notes = real.verification?.notes ?? [];
      const standing = (n) => n.confirmed_now === true;
      ok('/accuracy (real): every note is listed, and only a note the re-checks did not confirm is a failure row', page.notes.length === notes.length && page.notes.every((n, k) => n.fail === !standing(notes[k])), JSON.stringify(page.notes.map((n) => [n.fail, n.text.slice(0, 60)])));
      ok('/accuracy (real): the page says when the re-checks confirmed the reworded notes', notes.filter(standing).length === 0 || page.short.includes('the later re-checks confirmed the new wording'), page.short);
      const findings = real.verification?.recheck_findings ?? [];
      ok('/accuracy (real): every re-check finding is listed, open ones as failure rows, fixes said', page.findings.length === findings.length && page.findings.every((f, k) => f.fail === findings[k].open && (findings[k].resolution === '' || f.text.includes(findings[k].resolution))), JSON.stringify(page.findings.map((f) => [f.fail, f.text.slice(0, 60)])));
    }
    ok('/accuracy: says how values are checked', (await p.$eval('main', (m) => m.textContent)).includes('not reviewed by a practitioner'));
    ok('/accuracy: "does not check" list no longer says soil criteria are not loaded', !text.includes('only drinking-water values for groundwater are loaded') && text.includes('soil HIL A values are loaded'));
  }
  await p.close();

  const ph = await open(phone, route.path);
  if (!route.noData) await ph.p.waitForSelector('[data-ready]', { timeout: 5000 }).catch(() => undefined);
  const routeWidth = await ph.p.evaluate(() => document.documentElement.scrollWidth);
  ok(`${route.path}: phone, no sideways scrolling at 390 px`, routeWidth === 390, `page is ${routeWidth} px wide`);
  const small = await ph.p.evaluate(() => [...document.querySelectorAll('.btn')].filter((e) => e.offsetParent && e.getBoundingClientRect().height < 40).length);
  ok(`${route.path}: phone, buttons big enough to tap`, small === 0);
  ok(`${route.path}: phone, no script errors`, ph.errors.length === 0, ph.errors.join(' | '));
  await shot(ph.p, `${route.name}-phone`);
  await ph.p.close();
}

// ---------- links across pages, deep links, no-index files ----------
{
  const { p } = await open({ width: 1440, height: 900 }, '/accuracy');
  await p.click('.nav a.cta');
  await sleep(400);
  const where = await p.evaluate(() => ({ path: location.pathname, hash: location.hash, top: Math.round(document.getElementById('try')?.getBoundingClientRect().top ?? -9999) }));
  ok('Menu on another page leads back to the landing section', where.path === '/' && where.hash === '#try' && Math.abs(where.top) < 120, JSON.stringify(where));
  await p.close();
}
{
  const { p } = await open({ width: 1440, height: 900 }, '/#about');
  await sleep(300);
  const top = await p.evaluate(() => Math.round(document.getElementById('about').getBoundingClientRect().top));
  ok('A deep link to a section scrolls to it on load', Math.abs(top) < 120, `top=${top}`);
  await p.close();
}
{
  const robots = await fetch(`${base}/robots.txt`).then((r) => r.text());
  ok('robots.txt disallows everything', /User-agent: \*\s+Disallow: \/\s*$/m.test(robots), robots);
  const headers = readFileSync(join(distDir, '_headers'), 'utf8');
  ok('_headers sends X-Robots-Tag: noindex on every path (Cloudflare)', /^\/\*\s*\n\s+X-Robots-Tag: noindex/m.test(headers), headers);
  const credits = await fetch(`${base}/img/credits.md`);
  ok('Photo credits are published with the photos', credits.ok && (await credits.text()).includes('Unsplash'));
  const html = readFileSync(join(distDir, 'index.html'), 'utf8');
  ok('Built HTML carries noindex and no Google Fonts link', html.includes('name="robots" content="noindex') && !html.includes('fonts.googleapis'));
}

// ---------- "Try it": prepared answers from answers.json (a fixture), and a missing answers.json ----------
{
  const { p, errors } = await open({ width: 1440, height: 900 }, '/', { respond: { '/data/answers.json': json(fixture('answers.sample.json')) } });
  const sample = JSON.parse(fixture('answers.sample.json'));
  const chips = await p.$$eval('.chips button', (bs) => bs.map((x) => x.textContent));
  ok('Try it: suggested questions come from answers.json', chips.length === sample.answers.length && sample.answers.every((a, k) => chips[k] === a.question), chips.join(' | '));
  const note = await p.$eval('#prepared-note', (e) => e.textContent);
  ok('Try it: small print says the answers were prepared in advance, with date and model', note.includes('answered in advance by the same pipeline') && note.includes('24 Sep 2026 with fixture-model'), note);
  await p.click('.chips button[data-a="0"]');
  await sleep(60);
  const a0 = await p.$eval('#answer', (a) => ({
    text: a.textContent,
    state: a.getAttribute('data-state'),
    cites: [...a.querySelectorAll(':scope > ol.cites > li')].map((li) => ({ n: li.querySelector('.cn')?.textContent, link: li.querySelector('a')?.getAttribute('href') ?? '', text: li.textContent })),
    more: [...a.querySelectorAll('details.more summary')].map((x) => x.textContent),
  }));
  ok(
    'Try it: a prepared answer shows the answer and numbered sources with page links',
    a0.state === 'prepared' && a0.text.includes('Schedule B2') && a0.cites.length === 1 && a0.cites[0].n === '1' && a0.cites[0].link.endsWith('#page=34') && a0.cites[0].text.includes('Open page 29 (PDF page 34)'),
    JSON.stringify(a0),
  );
  ok('Try it: each prepared answer carries its own small print', a0.text.includes('Fixture answer, prepared in advance by the same pipeline with fixture-model'));
  ok('Try it: passages the answer did not cite, and the checks, are one click away', a0.more.includes('1 more passage it was given') && a0.more.includes('How this answer was made and checked'), JSON.stringify(a0.more));
  await p.click('.chips button[data-a="1"]');
  await sleep(60);
  const a1 = await p.$eval('#answer', (a) => a.textContent);
  ok('Try it: guideline values are listed with their table and page', a1.includes('PFOS: 0.008 µg/L (current national values)') && a1.includes('PFAS NEMP 3.1, Table 4, printed page 49') && a1.includes("scientist's call"), a1);
  ok('Try it: a withheld answer says so and shows the passages instead', a1.includes('No written answer this time') && a1.includes('Open the web page') && a1.includes('Passages from the guidance'), a1);
  await p.click('.chips button[data-a="2"]');
  await sleep(60);
  ok('Try it: a not-covered answer says so instead of guessing', (await p.$eval('#answer', (a) => a.textContent)).includes('Not covered by the guidance it can read'));
  await p.click('.chips button[data-a="3"]');
  await sleep(60);
  ok('Try it: the guard-rail answer declines to judge the site', (await p.$eval('#answer', (a) => a.textContent)).includes("won't answer"));
  await p.$eval('#q', (i) => i.select());
  await p.type('#q', 'is this site CONTAMINATED?');
  await p.keyboard.press('Enter');
  await sleep(60);
  ok('Try it: typing a prepared question shows its prepared answer (no live call)', (await p.$eval('#answer', (a) => a.getAttribute('data-state'))) === 'prepared');
  const text = await allText(p);
  ok('Try it: no em or en dashes in prepared answers', !DASHES.test(text));
  ok('Try it with answers.json: no script errors', errors.length === 0, errors.join(' | '));
  await p.evaluate(() => {
    document.documentElement.style.scrollBehavior = 'auto';
    document.querySelector('.chips button[data-a="0"]').click();
  });
  await sleep(200);
  await (await p.$('#try')).screenshot({ path: join(screensDir, 'try-prepared-desktop.png') });
  await p.close();
}
{
  const { p } = await open({ width: 1440, height: 900 }, '/', { respond: { '/data/answers.json': { status: 404, contentType: 'text/plain', body: 'Not found' } } });
  const chips = await p.$$eval('.chips button', (bs) => bs.map((x) => x.textContent));
  await p.click('.chips button[data-a="0"]');
  await sleep(60);
  ok('Try it: a missing answers.json falls back to the hand-written examples', chips.length === 2 && (await p.$eval('#answer', (a) => a.textContent)).includes('0.008'), chips.join(' | '));
  await p.close();
}

// ---------- the real answers.json, when the pipeline has written one ----------
if (existsSync(join(distDir, 'data', 'answers.json'))) {
  const { p, errors } = await open({ width: 1440, height: 900 });
  const chips = await p.$$eval('.chips button', (bs) => bs.length);
  const states = [];
  for (let k = 0; k < chips; k++) {
    await p.click(`.chips button[data-a="${k}"]`);
    await sleep(60);
    states.push(await p.$eval('#answer', (a) => ({ state: a.getAttribute('data-state'), len: a.textContent.length })));
  }
  ok('answers.json: every suggested question renders a prepared answer', chips > 0 && states.every((x) => x.state === 'prepared' && x.len > 40), JSON.stringify(states));
  const text = await allText(p);
  ok('answers.json: no em or en dashes in any prepared answer', !DASHES.test(text), (text.match(/.{0,30}[\u2013\u2014].{0,30}/g) ?? []).join(' | '));
  const links = await deadLinks(p, base, landingIds);
  ok('answers.json: no dead links in the prepared answers', links.dead.length === 0, JSON.stringify(links.dead));
  ok('answers.json: no script errors', errors.length === 0, errors.join(' | '));
  // Screenshots of the first two suggested questions, as the real file answers them.
  await p.evaluate(() => (document.documentElement.style.scrollBehavior = 'auto'));
  for (let k = 0; k < Math.min(chips, 3); k++) {
    await p.click(`.chips button[data-a="${k}"]`);
    await sleep(150);
    await (await p.$('#try')).screenshot({ path: join(screensDir, `try-real-${k + 1}-desktop.png`) });
  }
  await p.close();
}

// ---------- accuracy page with a sample results file, and with the placeholder ----------
{
  const sample = JSON.parse(fixture('accuracy.sample.json'));
  const { p, errors } = await open({ width: 1440, height: 900 }, '/accuracy', { respond: { '/data/accuracy.json': json(sample) } });
  await p.waitForSelector('[data-ready]', { timeout: 5000 }).catch(() => undefined);
  const page = await p.evaluate(() => ({
    short: document.querySelector('[data-results]')?.textContent ?? '',
    failRows: [...document.querySelectorAll('tr.failrow')].map((tr) => tr.textContent),
    main: document.querySelector('main')?.textContent ?? '',
    pending: !!document.querySelector('.pending'),
  }));
  ok('/accuracy with results: the placeholder is replaced by the results', !page.pending && page.short.includes('238 of 239 automated tests passed; 1 test failed (2 skipped)'), page.short);
  ok('/accuracy with results: date and build are shown', page.short.includes('Last run: 24 Sep 2026, local build'), page.short);
  ok('/accuracy with results: failures are shown, not only passes', page.failRows.some((r) => r.includes('Paragraph checker') && r.includes('1 failure')) && page.failRows.some((r) => r.includes('PFOS on its own') && r.includes('mismatch')), JSON.stringify(page.failRows));
  ok('/accuracy with results: search metrics for the tuning and held-out sets', ['Tuning set', 'Held-out set', 'Right page ranked first', '12 of 22', '19 of 22', '4 of 4', '5 of 10', '8 of 10'].every((x) => page.main.includes(x)));
  ok('/accuracy with results: both verification passes, with a verdict per value', page.main.includes('Pass A') && page.main.includes('Pass B') && page.short.includes('1 of 2 guideline values matched the source page in every pass; 1 did not'));
  ok('/accuracy with results: still says values were checked by two automated passes, not a practitioner', page.main.includes('two independent automated passes') && page.main.includes('not reviewed by a practitioner'));
  const text = await allText(p);
  ok('/accuracy with results: no em or en dashes', !DASHES.test(text));
  ok('/accuracy with results: no script errors', errors.length === 0, errors.join(' | '));
  await shot(p, 'accuracy-results-desktop');
  await p.close();
  const ph = await open(phone, '/accuracy', { respond: { '/data/accuracy.json': json(sample) } });
  await ph.p.waitForSelector('[data-ready]', { timeout: 5000 }).catch(() => undefined);
  const w = await ph.p.evaluate(() => document.documentElement.scrollWidth);
  ok('/accuracy with results: phone, no sideways scrolling at 390 px', w === 390, `page is ${w} px wide`);
  await shot(ph.p, 'accuracy-results-phone');
  await ph.p.close();
}
{
  const { p } = await open({ width: 1440, height: 900 }, '/accuracy', { respond: { '/data/accuracy.json': json(fixture('accuracy.pending.json')) } });
  await p.waitForSelector('[data-ready]', { timeout: 5000 }).catch(() => undefined);
  const pending = await p.$eval('.pending', (e) => e.textContent).catch(() => '');
  ok('/accuracy with a pending file: keeps the placeholder, with no numbers', pending.includes('Results will appear here when the automated tests run') && !/\d/.test(pending));
  await p.close();
}

// ---------- .env.production holds the published site's settings ----------
{
  const prod = Object.fromEntries(
    readFileSync(join(webDir, '.env.production'), 'utf8')
      .split(/\r?\n/)
      .filter((line) => /^VITE_\w+=/.test(line))
      .map((line) => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1).trim()]),
  );
  ok('.env.production: repository, connector and same-origin API settings', prod.VITE_GITHUB_URL === PRODUCTION.github && prod.VITE_MCP_URL === PRODUCTION.mcp && prod.VITE_API_BASE === PRODUCTION.apiBase, JSON.stringify(prod));
}

// ---------- the offline build (npm run build:offline, and npm run dev): nothing live is switched on ----------
{
  const OFFLINE_PORT = 4320;
  const offlineBase = `http://localhost:${OFFLINE_PORT}`;
  await build({ root: webDir, mode: 'offline', logLevel: 'silent', build: { outDir: offlineDir, emptyOutDir: true } });
  ok('Offline build: build.json records the offline build', JSON.parse(readFileSync(join(offlineDir, 'build.json'), 'utf8')).kind === 'offline');
  const offline = await preview({ root: webDir, mode: 'offline', logLevel: 'silent', build: { outDir: offlineDir }, preview: { port: OFFLINE_PORT, strictPort: true, open: false } });
  const { p, errors, requests } = await open({ width: 1440, height: 900 }, '/', { origin: offlineBase, respond: { '/data/answers.json': json({}) } });
  await p.$eval('#q', (i) => i.select());
  await p.type('#q', 'How deep should monitoring wells be?');
  await p.keyboard.press('Enter');
  await sleep(60);
  const said = await p.$eval('#answer', (a) => a.textContent);
  ok('Offline build: a typed question says live answers are switching on soon, and nothing is sent', said.includes('Live answers are switching on soon') && said.includes('prepared questions') && !requests.some((u) => u.includes('/api/ask')), said);
  ok('Offline build: the box says so before anything is typed', (await p.$eval('#coming-soon', (e) => e.textContent).catch(() => '')).includes('switching on soon'));
  // The connector and the code repository are not set in this build: no copy button, no dead "Code on GitHub" button.
  const connect = await p.$eval('#connect', (c) => ({ text: c.textContent, copy: !!c.querySelector('#copy'), code: !!c.querySelector('code') }));
  ok('Offline build: the connector card says the link will appear here soon', connect.text.includes('will appear here soon') && !connect.copy && !connect.code, JSON.stringify(connect));
  ok('Offline build: no wording about the site going live', !/goes live|go live|site is live/i.test(await allText(p)));
  ok('Offline build: no "Code on GitHub" button', await p.evaluate(() => ![...document.querySelectorAll('a')].some((a) => a.textContent.includes('GitHub'))));
  const links = await deadLinks(p, offlineBase, landingIds);
  ok('Offline build: no dead links', links.dead.length === 0, JSON.stringify(links.dead));
  ok('Offline build: no script errors', errors.length === 0, errors.join(' | '));
  await p.close();
  await offline.close();
}

// ---------- the launch build (npm run build:launch): the repository link on, the question box and connector "soon" ----------
{
  const LAUNCH_PORT = 4321;
  const launchBase = `http://localhost:${LAUNCH_PORT}`;
  await buildLaunch(launchDir, { logLevel: 'silent' });
  const launch = await preview({ root: webDir, logLevel: 'silent', build: { outDir: launchDir }, preview: { port: LAUNCH_PORT, strictPort: true, open: false } });
  const { p, errors, requests } = await open({ width: 1440, height: 900 }, '/', { origin: launchBase });
  const note = await p.$eval('#coming-soon', (e) => ({ text: e.textContent, role: e.getAttribute('role') })).catch(() => ({ text: '', role: '' }));
  ok('Launch build: the question box says live answers are switching on soon, before anything is typed', note.text.includes('Live answers to typed questions are switching on soon') && note.role === 'note', JSON.stringify(note));
  const chips = await p.$$eval('.chips button', (bs) => bs.length);
  ok('Launch build: the prepared questions are offered', chips > 0, `chips=${chips}`);
  if (chips > 0) {
    await p.click('.chips button[data-a="0"]');
    await sleep(80);
  }
  const first = await p.$eval('#answer', (a) => a.getAttribute('data-state')).catch(() => '');
  ok('Launch build: a prepared question still shows its answer', ['prepared', 'fallback'].includes(first ?? ''), String(first));
  // Keyboard only: focus the question field, type, press Enter.
  await p.$eval('#q', (i) => i.focus());
  await p.$eval('#q', (i) => i.select());
  await p.keyboard.type('How deep should monitoring wells be?');
  await p.keyboard.press('Enter');
  await sleep(80);
  const said = await p.$eval('#answer', (a) => ({ state: a.getAttribute('data-state'), text: a.textContent }));
  ok('Launch build: a typed question says live answers are switching on soon and points to the prepared questions', said.state === 'offline' && said.text.includes('Live answers are switching on soon') && said.text.includes('prepared questions'), JSON.stringify(said));
  ok('Launch build: nothing is sent to /api/ask and no Turnstile script is loaded', !requests.some((u) => u.includes('/api/ask') || u.includes(TURNSTILE_HOST)));
  const connect = await p.$eval('#connect', (c) => ({ text: c.textContent, copy: !!c.querySelector('#copy'), code: !!c.querySelector('code') }));
  ok('Launch build: the connector card says the link will appear here soon, with no link or Copy button', connect.text.includes('The connector link will appear here soon') && !connect.copy && !connect.code, JSON.stringify(connect));
  const tryLead = await p.$eval('#try .lead', (e) => e.textContent ?? '');
  ok('Launch build: the "Try it" line and the connector card promise only what works now', !/two ways|you can add evidenceline/i.test(`${tryLead} ${connect.text}`) && tryLead.includes('prepared question'), `${tryLead} | ${connect.text}`);
  ok('Launch build: build.json records the launch build (npm run verify:launch)', JSON.parse(readFileSync(join(launchDir, 'build.json'), 'utf8')).kind === 'launch');
  ok('Launch build: "Code on GitHub" links to the public repository', (await p.$eval('#github', (a) => a.getAttribute('href')).catch(() => '')) === PRODUCTION.github);
  const text = await allText(p);
  ok('Launch build: no wording about the site going live, and no em or en dashes', !/goes live|go live|site is live/i.test(text) && !DASHES.test(text));
  const links = await deadLinks(p, launchBase, landingIds);
  ok('Launch build: no dead links', links.dead.length === 0, JSON.stringify(links.dead));
  ok('Launch build: no script errors', errors.length === 0, errors.join(' | '));
  // The sticky header would cover the section's heading in an element screenshot; it stays in the flow when unstuck.
  await p.evaluate(() => {
    document.documentElement.style.scrollBehavior = 'auto';
    document.querySelector('.bar')?.style.setProperty('position', 'relative');
  });
  await (await p.$('#try')).screenshot({ path: join(screensDir, 'try-launch-desktop.png') });
  await p.close();

  const ph = await open({ ...phone, deviceScaleFactor: 1 }, '/', { origin: launchBase });
  await ph.p.$eval('#q', (i) => i.focus());
  await ph.p.keyboard.type('Where do PFAS guideline values come from?');
  await ph.p.keyboard.press('Enter');
  await sleep(80);
  const phoneSaid = await ph.p.$eval('#answer', (a) => a.getAttribute('data-state'));
  const phoneLayout = await ph.p.evaluate(() => ({
    page: document.documentElement.scrollWidth,
    note: Math.round(document.getElementById('coming-soon')?.getBoundingClientRect().right ?? 9999),
    card: Math.round(document.getElementById('connect')?.getBoundingClientRect().right ?? 9999),
  }));
  ok('Launch build: phone, the box and the card fit 390 px with no sideways scrolling', phoneLayout.page === 390 && phoneLayout.note <= 390 && phoneLayout.card <= 390 && phoneSaid === 'offline', JSON.stringify({ ...phoneLayout, phoneSaid }));
  await ph.p.evaluate(() => (document.documentElement.style.scrollBehavior = 'auto'));
  await (await ph.p.$('#try')).screenshot({ path: join(screensDir, 'try-launch-phone.png') });
  await ph.p.close();
  await launch.close();
}

// ---------- the live build: VITE_API_BASE, VITE_MCP_URL and VITE_GITHUB_URL set, the API mocked ----------
{
  const LIVE_PORT = 4319;
  const liveBase = `http://localhost:${LIVE_PORT}`;
  const env = {
    VITE_API_BASE: liveBase,
    VITE_MCP_URL: 'https://connector.example.org/mcp',
    VITE_GITHUB_URL: 'https://github.com/example/evidenceline',
    VITE_TURNSTILE_SITE_KEY: TURNSTILE_TEST_KEY,
  };
  Object.assign(process.env, env);
  // Mode "offline" keeps .env.production out: only the settings above are used.
  await build({ root: webDir, mode: 'offline', logLevel: 'silent', build: { outDir: liveDir, emptyOutDir: true } });
  for (const key of Object.keys(env)) delete process.env[key];
  const live = await preview({ root: webDir, logLevel: 'silent', build: { outDir: liveDir }, preview: { port: LIVE_PORT, strictPort: true, open: false } });

  const asked = [];
  const answer = {
    status: 'answered',
    answer: 'A tier 1 screening compares site data with the investigation and screening levels in Schedule B1 [1].',
    citations: [
      { n: 1, document: 'ASC NEPM Schedule B1', edition: '2013', printed_page: '5', pdf_page: 11, section: '2 Tier 1 assessment', excerpt: '', link: 'https://www.legislation.gov.au/F2013C00288/latest/text#page=11' },
    ],
    guideline_values: [],
    notes: ['An investigation level is not a clean-up level.'],
  };
  const handle = async (r) => {
    if (!r.url().endsWith('/api/ask')) return false;
    if (r.method() !== 'POST') return false;
    const posted = JSON.parse(r.postData() ?? '{}');
    const q = posted.question ?? '';
    asked.push({ url: r.url(), q, type: r.headers()['content-type'], token: posted.turnstile_token ?? null });
    await sleep(500);
    if (q.includes('rate')) await r.respond(json({ detail: 'Too many requests' }, 429, { 'Retry-After': '60' }));
    else if (q.includes('pause')) await r.respond(json({ status: 'paused' }, 503));
    else if (q.includes('broken')) await r.respond(json({ detail: 'Internal error' }, 500));
    else if (q.includes('offline')) await r.abort('failed');
    else if (q.includes('bare')) await r.respond(json(answer));
    else if (q.includes('budget')) await r.respond(json({ status: 'paused', explanation: "Live answers are paused: today's limit has been reached.", answer: null, citations: answer.citations, guideline_values: [], notes: [] }));
    else await r.respond(json({ question: q, result: answer }));
    return true;
  };
  const liveRespond = { '/data/answers.json': json(fixture('answers.sample.json')), '/turnstile/v0/api.js': turnstileScript };
  const { p, errors, failed, requests } = await open({ width: 1440, height: 900 }, '/', { origin: liveBase, respond: liveRespond, handle });
  ok('Turnstile: the script is not loaded while the question box is far down the page', !requests.some((u) => u.includes(TURNSTILE_HOST)));
  ok('Turnstile: no "soon" note when live answers are on', !(await p.$('#coming-soon')));
  const ask = async (q) => {
    await p.$eval('#q', (i) => i.select());
    await p.type('#q', q);
    await p.keyboard.press('Enter');
    await sleep(80);
    const during = await p.$eval('#answer', (a) => ({ state: a.getAttribute('data-state'), text: a.textContent }));
    await p.waitForFunction(() => document.querySelector('#answer')?.getAttribute('data-state') !== 'loading', { timeout: 5000 }).catch(() => undefined);
    return { during, after: await p.$eval('#answer', (a) => ({ state: a.getAttribute('data-state'), text: a.textContent, link: a.querySelector('.cites a')?.getAttribute('href') ?? '' })) };
  };
  const good = await ask('What is a tier 1 screening assessment?');
  const turnstileUrl = requests.find((u) => u.includes(TURNSTILE_HOST)) ?? '';
  ok('Turnstile: the script comes from challenges.cloudflare.com with explicit rendering, once the box is near', turnstileUrl.startsWith(`https://${TURNSTILE_HOST}/turnstile/v0/api.js?render=explicit&onload=`), turnstileUrl);
  const stub = await p.evaluate(() => window.__turnstileStub);
  ok('Turnstile: one widget, with the site key from the build, flexible width', stub?.renders.length === 1 && stub.renders[0].sitekey === TURNSTILE_TEST_KEY && stub.renders[0].size === 'flexible', JSON.stringify(stub));
  ok('Turnstile: the question is sent with turnstile_token', asked[0]?.token === TURNSTILE_TEST_TOKEN, JSON.stringify(asked[0]));
  ok('Live: a typed question shows a loading message first', good.during.state === 'loading' && good.during.text.includes('Looking through the guidelines'), JSON.stringify(good.during));
  ok('Live: posts the question as JSON to {VITE_API_BASE}/api/ask', asked.length === 1 && asked[0].url === `${liveBase}/api/ask` && asked[0].q === 'What is a tier 1 screening assessment?' && (asked[0].type ?? '').includes('application/json'), JSON.stringify(asked));
  ok('Live: the answer shows with numbered sources and page links', good.after.state === 'live' && good.after.text.includes('Schedule B1') && good.after.text.includes('Open page 5 (PDF page 11)') && good.after.link.endsWith('#page=11'), JSON.stringify(good.after));
  const bare = await ask('A bare result without a wrapper?');
  ok('Live: a bare result (no "result" wrapper) is read too', bare.after.state === 'live' && bare.after.text.includes('Schedule B1'));
  const limited = await ask('rate limited question');
  ok('Live: rate-limited gets a friendly message', limited.after.state === 'rate-limited' && limited.after.text.includes('Too many questions') && limited.after.text.includes('try again in a minute'), limited.after.text);
  const paused = await ask('pause question');
  ok('Live: paused gets a friendly message', paused.after.state === 'paused' && paused.after.text.includes('paused'), paused.after.text);
  const budget = await ask('budget question');
  ok('Live: a paused result shows the reason and the passages', budget.after.state === 'live' && budget.after.text.includes('Live answers are paused') && budget.after.text.includes("today's limit") && budget.after.text.includes('Schedule B1'), budget.after.text);
  const broken = await ask('broken question');
  ok('Live: a server error gets a friendly message', broken.after.state === 'error' && broken.after.text.includes('HTTP 500') && broken.after.text.includes('try again later'), broken.after.text);
  const offline = await ask('offline question');
  ok('Live: an unreachable service gets a friendly message', offline.after.state === 'error' && offline.after.text.includes('could not be reached'), offline.after.text);
  ok('Turnstile: every live question carried a token', asked.length > 1 && asked.every((a) => a.token === TURNSTILE_TEST_TOKEN), JSON.stringify(asked.map((a) => a.token)));
  const resets = await p.evaluate(() => window.__turnstileStub.resets);
  ok('Turnstile: the widget is reset after each question (a token is good once)', resets === asked.length, `resets=${resets}, questions=${asked.length}`);
  const before = asked.length;
  await p.click('.chips button[data-a="0"]');
  await sleep(60);
  ok('Live: suggested questions still use the prepared answers (no live call)', asked.length === before && (await p.$eval('#answer', (a) => a.getAttribute('data-state'))) === 'prepared');
  // Connector and code links exist in this build.
  ok('Live: the connector card shows the configured link', (await p.$eval('#mcp', (c) => c.textContent)) === env.VITE_MCP_URL);
  await p.click('#copy');
  await sleep(60);
  ok('Copy button responds', (await p.$eval('#copy', (x) => x.textContent)) === 'Copied');
  ok('Live: "Code on GitHub" links to the configured repository', (await p.$eval('#github', (a) => a.getAttribute('href')).catch(() => '')) === env.VITE_GITHUB_URL);
  const links = await deadLinks(p, liveBase, landingIds);
  ok('Live: no dead links', links.dead.length === 0, JSON.stringify(links.dead));
  ok('Live: no script errors', errors.filter((e) => !e.includes('Failed to load resource')).length === 0, errors.join(' | '));
  ok('Live: only the deliberately failed API call failed', failed.every((u) => u.endsWith('/api/ask')), failed.join(' | '));
  // Keyboard only: from the question field, Tab reaches the Ask button; nothing traps focus.
  await p.$eval('#q', (i) => i.focus());
  await p.keyboard.press('Tab');
  const afterTab = await p.evaluate(() => document.activeElement?.textContent ?? '');
  ok('Keyboard: Tab from the question field goes to the Ask button', afterTab === 'Ask', afterTab);
  await ask('What is a tier 1 screening assessment?');
  // The sticky header would cover the section's heading in an element screenshot; it stays in the flow when unstuck.
  await p.evaluate(() => {
    document.documentElement.style.scrollBehavior = 'auto';
    document.querySelector('.bar')?.style.setProperty('position', 'relative');
  });
  await (await p.$('#try')).screenshot({ path: join(screensDir, 'try-live-desktop.png') });
  await p.close();

  const ph = await open({ ...phone, deviceScaleFactor: 1 }, '/', { origin: liveBase, respond: liveRespond, handle });
  await ph.p.$eval('#q', (i) => i.select());
  await ph.p.type('#q', 'What is a tier 1 screening assessment?');
  await ph.p.keyboard.press('Enter');
  await ph.p.waitForFunction(() => document.querySelector('#answer')?.getAttribute('data-state') === 'live', { timeout: 5000 }).catch(() => undefined);
  const w = await ph.p.evaluate(() => document.documentElement.scrollWidth);
  ok('Live: phone, no sideways scrolling at 390 px with an answer shown', w === 390, `page is ${w} px wide`);
  const robotBox = await ph.p.evaluate(() => {
    const r = document.getElementById('robot')?.getBoundingClientRect();
    return r === undefined ? null : { left: Math.round(r.left), right: Math.round(r.right), height: Math.round(r.height) };
  });
  ok('Turnstile: phone, the check fits inside 390 px', robotBox !== null && robotBox.left >= 0 && robotBox.right <= 390 && robotBox.height >= 65, JSON.stringify(robotBox));
  await ph.p.evaluate(() => (document.documentElement.style.scrollBehavior = 'auto'));
  await (await ph.p.$('#try')).screenshot({ path: join(screensDir, 'try-live-phone.png') });
  await ph.p.close();
  await live.close();
}

// ---------- tidy: an item's technical detail, open ----------
{
  const { p } = await open({ width: 1440, height: 900 }, '/tidy');
  await p.waitForSelector('[data-ready]', { timeout: 5000 }).catch(() => undefined);
  await p.evaluate(() => {
    document.documentElement.style.scrollBehavior = 'auto';
    const d = document.querySelector('#item-5 details');
    d.open = true;
  });
  await sleep(200);
  await (await p.$('#item-5')).screenshot({ path: join(screensDir, 'tidy-item-open-desktop.png') });
  await p.close();
}

// ---------- landing screenshots ----------
for (const [name, vp] of [
  ['landing-desktop', { width: 1440, height: 900 }],
  // At 2x the phone page is over 16,384 px tall, where Chrome's full-page capture starts repeating tiles; 1x avoids that.
  ['landing-phone', { ...phone, deviceScaleFactor: 1 }],
]) {
  const { p } = await open(vp);
  await loadAllImages(p);
  await sleep(700);
  await shot(p, name);
  await p.close();
}
{
  // The walkthrough at step 4 and step 5, where most of the design detail is.
  const { p } = await open({ width: 1440, height: 900 });
  await p.evaluate(() => {
    document.documentElement.style.scrollBehavior = 'auto';
    document.querySelector('#steps button[data-i="3"]').click();
  });
  await sleep(900);
  const tour = await p.$('#how');
  await tour.screenshot({ path: join(screensDir, 'walkthrough-step4-desktop.png') });
  await p.evaluate(() => document.querySelector('#steps button[data-i="4"]').click());
  await sleep(500);
  await tour.screenshot({ path: join(screensDir, 'walkthrough-step5-desktop.png') });
  await p.close();
}

await b.close();
await server.close();

const pass = results.filter((r) => r.pass).length;
for (const r of results) console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name}${r.detail && !r.pass ? `  -> ${r.detail}` : ''}`);
console.log(`\n${pass} of ${results.length} checks passed`);
console.log(`Screenshots: ${screensDir}`);
process.exit(pass === results.length ? 0 : 1);
