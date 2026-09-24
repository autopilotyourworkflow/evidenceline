// The question box with Cloudflare's real Turnstile script, on Cloudflare's documented always-pass test site key.
//
//   npm run test:turnstile        (from web/; needs the internet, so it is not part of npm run check)
//
// The smoke test (tests/smoke.mjs) replaces Turnstile's script with a local stand-in so that it never uses the
// internet. This test loads the real script from challenges.cloudflare.com instead, and checks that: the script is
// fetched only once the question box is near the screen; the widget appears and gives the test token; a typed
// question is sent to /api/ask with that token as turnstile_token; the widget is reset for the next question; the
// keyboard path (type, Enter; Tab from the field to Ask) works; and at 390 px the widget fits with no sideways scroll.
// The API is mocked; nothing is sent anywhere else. Test keys pass on localhost and never count against an account.
// CHROME_PATH overrides the browser location.

import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { build, preview } from 'vite';

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const outDir = join(webDir, 'node_modules', '.tmp', 'dist-turnstile');
const screensDir = join(webDir, 'tests', 'screens');
const chromePath = process.env.CHROME_PATH ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 4322;
const origin = `http://localhost:${PORT}`;

/** Cloudflare's always-pass test site key (visible widget) and the token every test key gives. */
const TEST_SITE_KEY = '1x00000000000000000000AA';
const TEST_TOKEN = 'XXXX.DUMMY.TOKEN.XXXX';
const TURNSTILE_HOST = 'challenges.cloudflare.com';

const env = { VITE_API_BASE: origin, VITE_MCP_URL: '', VITE_GITHUB_URL: '', VITE_TURNSTILE_SITE_KEY: TEST_SITE_KEY };
Object.assign(process.env, env);
// Mode "offline" keeps .env.production out: only the settings above are used.
await build({ root: webDir, mode: 'offline', logLevel: 'silent', build: { outDir, emptyOutDir: true } });
for (const key of Object.keys(env)) delete process.env[key];
const server = await preview({ root: webDir, logLevel: 'silent', build: { outDir }, preview: { port: PORT, strictPort: true, open: false } });
const browser = await puppeteer.launch({ executablePath: chromePath, headless: true });

const results = [];
const ok = (name, pass, detail = '') => results.push({ name, pass: !!pass, detail });

const ANSWER = {
  status: 'answered',
  answer: 'A tier 1 screening compares site data with the investigation and screening levels in Schedule B1 [1].',
  citations: [{ n: 1, document: 'ASC NEPM Schedule B1', edition: '2013', printed_page: '5', pdf_page: 11, section: '', excerpt: '', link: '' }],
  guideline_values: [],
  notes: [],
};

/** Opens the landing page with /api/ask mocked; everything else (including Cloudflare's script) is fetched for real. */
async function open(viewport) {
  const page = await browser.newPage();
  const errors = [];
  const requests = [];
  const asked = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('request', (r) => requests.push(r.url()));
  await page.setRequestInterception(true);
  page.on('request', (r) => {
    if (r.isInterceptResolutionHandled()) return;
    if (r.url() === `${origin}/api/ask` && r.method() === 'POST') {
      const posted = JSON.parse(r.postData() ?? '{}');
      asked.push({ question: posted.question ?? null, token: posted.turnstile_token ?? null });
      void r.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ result: ANSWER }) });
      return;
    }
    void r.continue();
  });
  await page.setViewport(viewport);
  await page.goto(`${origin}/`, { waitUntil: 'networkidle0' });
  return { page, errors, requests, asked };
}

/** Cloudflare puts the widget's frame in a closed shadow root, so it is found through the page's frames. */
const cloudflareFrame = (page) => page.frames().map((f) => f.url()).find((u) => u.startsWith(`https://${TURNSTILE_HOST}/`)) ?? '';

const tokenIn = (page) =>
  page.evaluate(() => document.querySelector('#robot input[name="cf-turnstile-response"]')?.value ?? '');

/** Scrolls to the question box and waits for the widget to give its token. */
async function waitForToken(page) {
  await page.evaluate(() => {
    document.documentElement.style.scrollBehavior = 'auto';
    document.getElementById('try')?.scrollIntoView();
  });
  await page
    .waitForFunction(() => document.getElementById('robot')?.getAttribute('data-state') === 'ready', { timeout: 30_000 })
    .catch(() => undefined);
}

/**
 * A viewport screenshot from the question field down. An element screenshot leaves Cloudflare's frame blank, so the
 * page is scrolled and captured as the reader sees it; the sticky header is unstuck so it does not cover the box.
 */
async function viewportShot(page, file) {
  await page.evaluate(() => {
    document.querySelector('.bar')?.style.setProperty('position', 'relative');
    document.getElementById('askf')?.scrollIntoView();
    window.scrollBy(0, -120);
  });
  await new Promise((r) => setTimeout(r, 800));
  await page.screenshot({ path: join(screensDir, file) });
}

async function askByKeyboard(page, question) {
  await page.$eval('#q', (i) => i.select());
  await page.type('#q', question);
  await page.keyboard.press('Enter');
  await page
    .waitForFunction(() => ['live', 'robot-waiting', 'robot-unavailable', 'error'].includes(document.querySelector('#answer')?.getAttribute('data-state') ?? ''), { timeout: 30_000 })
    .catch(() => undefined);
  return page.$eval('#answer', (a) => ({ state: a.getAttribute('data-state'), text: a.textContent ?? '' }));
}

try {
  // ---------- desktop ----------
  {
    const { page, errors, requests, asked } = await open({ width: 1440, height: 900 });
    ok('The Turnstile script is not fetched while the question box is far down the page', !requests.some((u) => u.includes(TURNSTILE_HOST)));
    await waitForToken(page);
    const scriptUrl = requests.find((u) => u.includes(`${TURNSTILE_HOST}/turnstile/v0/api.js`)) ?? '';
    ok('The script comes from challenges.cloudflare.com, explicit rendering', scriptUrl.startsWith(`https://${TURNSTILE_HOST}/turnstile/v0/api.js?render=explicit&onload=`), scriptUrl);
    const widget = await page.evaluate(() => ({
      state: document.getElementById('robot')?.getAttribute('data-state') ?? '',
      height: Math.round(document.querySelector('#robot > div')?.getBoundingClientRect().height ?? 0),
    }));
    const frame = cloudflareFrame(page);
    ok('The widget renders (a frame from challenges.cloudflare.com) and reaches "ready"', frame !== '' && widget.height >= 60 && widget.state === 'ready', JSON.stringify({ ...widget, frame }));
    ok('The widget holds the test token', (await tokenIn(page)) === TEST_TOKEN);

    // Keyboard: Tab from the question field goes to the Ask button (the widget comes after it).
    await page.$eval('#q', (i) => i.focus());
    await page.keyboard.press('Tab');
    const afterTab = await page.evaluate(() => document.activeElement?.textContent ?? '');
    ok('Keyboard: Tab from the question field goes to the Ask button', afterTab === 'Ask', afterTab);

    const first = await askByKeyboard(page, 'What is a tier 1 screening assessment?');
    ok('A typed question (Enter key) is answered', first.state === 'live' && first.text.includes('Schedule B1'), JSON.stringify(first));
    ok('It was sent with turnstile_token set to the test token', asked.length === 1 && asked[0].token === TEST_TOKEN, JSON.stringify(asked));

    // A token is good for one question: the widget is reset and gives a fresh one for the next.
    await page
      .waitForFunction(() => document.getElementById('robot')?.getAttribute('data-state') === 'ready', { timeout: 30_000 })
      .catch(() => undefined);
    const second = await askByKeyboard(page, 'What does an investigation level mean?');
    ok('A second question gets a fresh token after the reset', second.state === 'live' && asked.length === 2 && asked[1].token === TEST_TOKEN, JSON.stringify(asked));
    ok('No script errors', errors.length === 0, errors.join(' | '));
    await viewportShot(page, 'try-turnstile-desktop.png');
    await page.close();
  }

  // ---------- phone, 390 px ----------
  {
    const { page, errors, asked } = await open({ width: 390, height: 844, deviceScaleFactor: 1, isMobile: true, hasTouch: true });
    await waitForToken(page);
    const layout = await page.evaluate(() => {
      const r = document.getElementById('robot')?.getBoundingClientRect();
      // The widget's own box (Cloudflare's frame sits inside it, in a closed shadow root).
      const frame = document.querySelector('#robot > div')?.getBoundingClientRect();
      return {
        page: document.documentElement.scrollWidth,
        robot: r === undefined ? null : { left: Math.round(r.left), right: Math.round(r.right) },
        frame: frame === undefined ? null : { left: Math.round(frame.left), right: Math.round(frame.right), height: Math.round(frame.height) },
      };
    });
    ok(
      'Phone: the widget fits inside 390 px with no sideways scrolling',
      layout.page === 390 && cloudflareFrame(page) !== '' && layout.frame !== null && layout.frame.left >= 0 && layout.frame.right <= 390 && layout.frame.height >= 60,
      JSON.stringify(layout),
    );
    const said = await askByKeyboard(page, 'What is a tier 1 screening assessment?');
    ok('Phone: a typed question is answered with the token sent', said.state === 'live' && asked[0]?.token === TEST_TOKEN, JSON.stringify({ said: said.state, asked }));
    ok('Phone: no script errors', errors.length === 0, errors.join(' | '));
    await viewportShot(page, 'try-turnstile-phone.png');
    await page.close();
  }
} finally {
  await browser.close();
  await server.close();
}

const pass = results.filter((r) => r.pass).length;
for (const r of results) console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name}${r.detail && !r.pass ? `  -> ${r.detail}` : ''}`);
console.log(`\n${pass} of ${results.length} checks passed`);
process.exit(pass === results.length ? 0 : 1);
