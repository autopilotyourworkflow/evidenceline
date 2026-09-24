// Tests for the Cloudflare Worker (worker/index.js) and its settings (wrangler.jsonc).
// Run: node --test tests/worker.test.mjs   (from web/). No network: the static files and the API are fakes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

import { PROXY_AUTH_HEADER, isProxiedPath, parseApiOrigin } from '../functions/_lib/proxy.js';
import * as entry from '../worker/index.js';
import { NOINDEX, isWorkerPath, withNoindex } from '../worker/routing.js';

const worker = entry.default;

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SITE = 'https://evidenceline.autopilotyourworkflow.com';
const API = 'https://evidenceline-api.onrender.com';
const AUTOPILOT_ACCOUNT = 'a2d57c1e05c835fb5f259ca1b2725b59';

/** Paths the routing is checked on: the API, the connector, near misses and ordinary site pages. */
const SAMPLE_PATHS = [
  '/', '/index.html', '/sources', '/tidy', '/accuracy', '/sample-site', '/no-such-page', '/data/answers.json',
  '/img/hero.jpg', '/_headers', '/api', '/api/', '/api/ask', '/api/health', '/api/a/b', '/mcp', '/mcp/', '/mcp/x',
  '/apix', '/mcpx', '/API/ask', '/docs/api/ask', '/docs/mcp',
];

/** A fake assets binding that records every request and answers like the static files would. */
function fakeAssets() {
  /** @type {string[]} */
  const seen = [];
  return {
    seen,
    ASSETS: {
      /** @param {Request} request */
      async fetch(request) {
        const { pathname } = new URL(request.url);
        seen.push(pathname);
        if (pathname === '/missing.png') return new Response('Not found', { status: 404, headers: { 'content-type': 'text/plain' } });
        return new Response('<!doctype html><title>Evidenceline</title>', {
          status: 200,
          headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'public, max-age=0, must-revalidate', etag: '"abc"' },
        });
      },
    },
  };
}

/** Swaps the global fetch for a recorder while `run` runs. */
async function withFakeFetch(reply, run) {
  const original = globalThis.fetch;
  /** @type {{ url: string, init: RequestInit | undefined }[]} */
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return reply(String(url), init);
  };
  try {
    await run(calls);
  } finally {
    globalThis.fetch = original;
  }
}

/** JSON with comments (wrangler.jsonc) as plain JSON: comments outside strings removed. */
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

/** A run_worker_first pattern as Cloudflare documents it: "*" matches any characters, "/" included. */
const globToRegExp = (pattern) => new RegExp(`^${pattern.split('*').map((s) => s.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*')}$`);

const config = readJsonc(join(webDir, 'wrangler.jsonc'));

describe('the Worker module', () => {
  // Cloudflare reads every named export of the main module as an entry point; a constant or helper exported there
  // stops the Worker from starting ("Incorrect type for map entry"). wrangler dev caught this once.
  it('exports only its default handler', () => {
    assert.deepEqual(Object.keys(entry), ['default']);
    assert.equal(typeof worker.fetch, 'function');
  });
});

describe('isWorkerPath', () => {
  it('takes /api, /mcp and everything under them, and nothing else', () => {
    for (const p of ['/api', '/api/', '/api/ask', '/api/health', '/mcp', '/mcp/', '/mcp/x']) assert.ok(isWorkerPath(p), p);
    for (const p of ['/', '/apix', '/mcpx', '/API/ask', '/docs/api/ask', '/sources', '/data/answers.json']) assert.ok(!isWorkerPath(p), p);
  });
  it('covers every path the proxy forwards', () => {
    for (const p of SAMPLE_PATHS) if (isProxiedPath(p)) assert.ok(isWorkerPath(p), p);
  });
});

describe('the Worker', () => {
  it('answers /api/ask with the friendly 503 when API_ORIGIN is empty, without touching the static files', async () => {
    const { ASSETS, seen } = fakeAssets();
    for (const method of ['GET', 'POST']) {
      const init = method === 'POST' ? { method, body: '{"question":"What is PFOS?"}', headers: { 'content-type': 'application/json' } } : { method };
      const response = await worker.fetch(new Request(`${SITE}/api/ask`, init), { ASSETS, API_ORIGIN: '' });
      assert.equal(response.status, 503, method);
      assert.equal(response.headers.get('x-robots-tag'), NOINDEX);
      assert.equal(response.headers.get('cache-control'), 'no-store');
      assert.match(response.headers.get('content-type') ?? '', /application\/json/);
      assert.deepEqual(await response.json(), { detail: 'The live service is not connected yet. Please try again later.' });
    }
    assert.deepEqual(seen, []);
  });

  it('answers /mcp the same way when API_ORIGIN is missing altogether', async () => {
    const { ASSETS } = fakeAssets();
    const response = await worker.fetch(new Request(`${SITE}/mcp`, { method: 'POST', body: '{}' }), { ASSETS });
    assert.equal(response.status, 503);
    assert.match((await response.json()).detail, /not connected yet/);
  });

  it('serves / from the static files, with the noindex header added', async () => {
    const { ASSETS, seen } = fakeAssets();
    const response = await worker.fetch(new Request(`${SITE}/`), { ASSETS, API_ORIGIN: '' });
    assert.equal(response.status, 200);
    assert.deepEqual(seen, ['/']);
    assert.match(await response.text(), /<title>Evidenceline<\/title>/);
    assert.equal(response.headers.get('x-robots-tag'), NOINDEX);
    // The static file's own headers are kept.
    assert.equal(response.headers.get('etag'), '"abc"');
    assert.match(response.headers.get('content-type') ?? '', /text\/html/);
  });

  it('keeps the static files\' status, and sends site pages to them even when the API is connected', async () => {
    const { ASSETS, seen } = fakeAssets();
    await withFakeFetch(
      () => new Response('{}'),
      async (calls) => {
        const missing = await worker.fetch(new Request(`${SITE}/missing.png`), { ASSETS, API_ORIGIN: API });
        assert.equal(missing.status, 404);
        assert.equal(missing.headers.get('x-robots-tag'), NOINDEX);
        await worker.fetch(new Request(`${SITE}/sources?x=1`), { ASSETS, API_ORIGIN: API });
        await worker.fetch(new Request(`${SITE}/apix`), { ASSETS, API_ORIGIN: API });
        assert.equal(calls.length, 0);
      },
    );
    assert.deepEqual(seen, ['/missing.png', '/sources', '/apix']);
  });

  it('forwards /api/* and /mcp to API_ORIGIN, with the shared secret, and marks the answer noindex', async () => {
    const { ASSETS, seen } = fakeAssets();
    await withFakeFetch(
      () => new Response('{"status":"ok"}', { headers: { 'content-type': 'application/json' } }),
      async (calls) => {
        const env = { ASSETS, API_ORIGIN: API, PROXY_SHARED_SECRET: 'test-secret' };
        const health = await worker.fetch(new Request(`${SITE}/api/health?x=1`), env);
        const mcp = await worker.fetch(new Request(`${SITE}/mcp`, { method: 'POST', body: '{"jsonrpc":"2.0"}' }), env);
        assert.equal(health.status, 200);
        assert.equal(mcp.status, 200);
        assert.equal(health.headers.get('x-robots-tag'), NOINDEX);
        assert.deepEqual(calls.map((c) => c.url), [`${API}/api/health?x=1`, `${API}/mcp`]);
        for (const call of calls) assert.equal(new Headers(call.init?.headers).get(PROXY_AUTH_HEADER), 'test-secret');
      },
    );
    assert.deepEqual(seen, []);
  });

  it('gives anything under /mcp/ a plain JSON 404, not the site page, and never calls the API for it', async () => {
    const { ASSETS, seen } = fakeAssets();
    await withFakeFetch(
      () => new Response('{}'),
      async (calls) => {
        const response = await worker.fetch(new Request(`${SITE}/mcp/extra`), { ASSETS, API_ORIGIN: API });
        assert.equal(response.status, 404);
        assert.equal(response.headers.get('x-robots-tag'), NOINDEX);
        assert.match((await response.json()).detail, /nothing at this address/);
        assert.equal(calls.length, 0);
      },
    );
    assert.deepEqual(seen, []);
  });
});

describe('withNoindex', () => {
  it('adds the header and keeps status, headers and body', async () => {
    const response = withNoindex(new Response('body', { status: 201, statusText: 'Created', headers: { 'x-one': '1' } }));
    assert.equal(response.status, 201);
    assert.equal(response.statusText, 'Created');
    assert.equal(response.headers.get('x-one'), '1');
    assert.equal(response.headers.get('x-robots-tag'), NOINDEX);
    assert.equal(await response.text(), 'body');
  });
  it('returns a response that already carries it unchanged', () => {
    const original = new Response('x', { headers: { 'x-robots-tag': NOINDEX } });
    assert.equal(withNoindex(original), original);
  });
});

describe('wrangler.jsonc', () => {
  it('names the Worker, pins the Autopilot account and points at this Worker and dist/', () => {
    assert.equal(config.name, 'evidenceline');
    assert.equal(config.account_id, AUTOPILOT_ACCOUNT);
    assert.equal(resolve(webDir, config.main), join(webDir, 'worker', 'index.js'));
    assert.match(config.compatibility_date, /^\d{4}-\d{2}-\d{2}$/);
    assert.equal(config.assets.directory, './dist');
    assert.equal(config.assets.binding, 'ASSETS');
    assert.equal(config.assets.not_found_handling, 'single-page-application');
  });
  it('serves only the custom domain: no workers.dev or preview addresses', () => {
    assert.deepEqual(config.routes, [{ pattern: 'evidenceline.autopilotyourworkflow.com', custom_domain: true }]);
    assert.equal(config.workers_dev, false);
    assert.equal(config.preview_urls, false);
  });
  it('runs the Worker first on exactly the paths it handles', () => {
    const patterns = config.assets.run_worker_first.map(globToRegExp);
    for (const p of SAMPLE_PATHS) assert.equal(patterns.some((re) => re.test(p)), isWorkerPath(p), p);
  });
  it('has API_ORIGIN as its only setting: empty (launch) or an https origin (live), and no secret in the file', () => {
    assert.deepEqual(Object.keys(config.vars), ['API_ORIGIN']);
    const value = config.vars.API_ORIGIN;
    assert.ok(value === '' || (value.startsWith('https://') && parseApiOrigin(value) === value), value);
    assert.doesNotMatch(readFileSync(join(webDir, 'wrangler.jsonc'), 'utf8'), /PROXY_SHARED_SECRET"\s*:/);
  });
  it('the static files carry the noindex rule on every path (public/_headers)', () => {
    assert.match(readFileSync(join(webDir, 'public', '_headers'), 'utf8'), /^\/\*\s*\r?\n\s+X-Robots-Tag: noindex, nofollow/m);
  });
});
