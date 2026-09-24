// Tests for the proxy in functions/_lib/proxy.js, which the Cloudflare Worker (worker/index.js) uses for /api and /mcp.
// Run: node --test tests/functions.test.mjs   (from web/). No network: the API is a fake fetch.
// The Worker's own routing is in tests/worker.test.mjs.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  CLIENT_IP_HEADER,
  MAX_BODY_BYTES,
  PROXY_AUTH_HEADER,
  buildUpstreamRequest,
  callerIp,
  handleProxy,
  isProxiedPath,
  parseApiOrigin,
  siteLocation,
  toClientResponse,
} from '../functions/_lib/proxy.js';

const SITE = 'https://evidenceline.autopilotyourworkflow.com';
const API = 'https://evidenceline-api.onrender.com';
const ENV = { API_ORIGIN: API };

/** A fake fetch that records every call and answers with `reply(url, init)`. */
function fakeFetch(reply = () => new Response('{"status":"ok"}', { headers: { 'content-type': 'application/json' } })) {
  /** @type {{ url: string, init: RequestInit }[]} */
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url: String(url), init });
    return reply(String(url), init);
  };
  return { fn, calls };
}

describe('parseApiOrigin', () => {
  it('accepts an https origin, with or without a trailing slash', () => {
    assert.equal(parseApiOrigin(API), API);
    assert.equal(parseApiOrigin(`${API}/`), API);
    assert.equal(parseApiOrigin(`  ${API}  `), API);
  });
  it('accepts http only on this computer', () => {
    assert.equal(parseApiOrigin('http://127.0.0.1:8000'), 'http://127.0.0.1:8000');
    assert.equal(parseApiOrigin('http://localhost:8000'), 'http://localhost:8000');
    assert.equal(parseApiOrigin('http://evidenceline-api.onrender.com'), null);
  });
  it('refuses anything missing, malformed, or with a path, query or login', () => {
    for (const bad of [undefined, null, '', '   ', 42, 'not a url', 'ftp://example.com', `${API}/api`,
      `${API}?x=1`, `${API}#top`, 'https://user:pass@example.com']) {
      assert.equal(parseApiOrigin(bad), null, String(bad));
    }
  });
});

describe('isProxiedPath', () => {
  it('matches /mcp exactly and /api with anything under it', () => {
    for (const p of ['/mcp', '/api', '/api/', '/api/ask', '/api/health']) assert.ok(isProxiedPath(p), p);
    for (const p of ['/', '/mcp/', '/mcpx', '/apix', '/sources', '/data/answers.json']) {
      assert.ok(!isProxiedPath(p), p);
    }
  });
});

describe('callerIp', () => {
  it('reads cf-connecting-ip for IPv4 and IPv6', () => {
    assert.equal(callerIp(new Headers({ 'cf-connecting-ip': '203.0.113.7' })), '203.0.113.7');
    assert.equal(callerIp(new Headers({ 'cf-connecting-ip': '2001:db8::1' })), '2001:db8::1');
  });
  it('ignores a missing or odd value', () => {
    assert.equal(callerIp(new Headers()), null);
    assert.equal(callerIp(new Headers({ 'cf-connecting-ip': 'evil, 1.2.3.4' })), null);
  });
});

describe('buildUpstreamRequest', () => {
  it('maps the site path and query onto the API origin', async () => {
    const plan = await buildUpstreamRequest(new Request(`${SITE}/api/health?x=1&y=2`), ENV);
    assert.ok(plan.ok);
    assert.equal(plan.url, `${API}/api/health?x=1&y=2`);
    assert.equal(plan.init.method, 'GET');
    assert.equal(plan.init.body, undefined);
    assert.equal(plan.init.redirect, 'manual');
  });

  it('forwards only allowlisted headers and replaces a spoofed client IP with the real one', async () => {
    const request = new Request(`${SITE}/mcp`, {
      method: 'POST',
      body: '{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json, text/event-stream',
        'mcp-protocol-version': '2025-11-25',
        'mcp-session-id': 'abc',
        cookie: 'session=secret',
        authorization: 'Bearer should-not-pass',
        'x-forwarded-for': '198.51.100.9',
        [CLIENT_IP_HEADER]: '10.0.0.1',
        [PROXY_AUTH_HEADER]: 'guessed',
        'cf-connecting-ip': '203.0.113.7',
      },
    });
    const plan = await buildUpstreamRequest(request, ENV);
    assert.ok(plan.ok);
    const headers = /** @type {Headers} */ (plan.init.headers);
    assert.equal(headers.get('content-type'), 'application/json');
    assert.equal(headers.get('accept'), 'application/json, text/event-stream');
    assert.equal(headers.get('mcp-protocol-version'), '2025-11-25');
    assert.equal(headers.get('mcp-session-id'), 'abc');
    assert.equal(headers.get(CLIENT_IP_HEADER), '203.0.113.7');
    for (const dropped of ['cookie', 'authorization', 'x-forwarded-for', 'cf-connecting-ip', PROXY_AUTH_HEADER]) {
      assert.equal(headers.get(dropped), null, dropped);
    }
    assert.equal(new TextDecoder().decode(/** @type {ArrayBuffer} */ (plan.init.body)),
      '{"jsonrpc":"2.0","id":1,"method":"tools/list"}');
  });

  it('adds the proxy secret only when it is set', async () => {
    const withSecret = await buildUpstreamRequest(new Request(`${SITE}/api/health`),
      { ...ENV, PROXY_SHARED_SECRET: ' s3cret ' });
    assert.ok(withSecret.ok);
    assert.equal(/** @type {Headers} */ (withSecret.init.headers).get(PROXY_AUTH_HEADER), 's3cret');
    const blank = await buildUpstreamRequest(new Request(`${SITE}/api/health`), { ...ENV, PROXY_SHARED_SECRET: '' });
    assert.ok(blank.ok);
    assert.equal(/** @type {Headers} */ (blank.init.headers).get(PROXY_AUTH_HEADER), null);
  });

  it('answers 503 in plain words when API_ORIGIN is missing or unusable', async () => {
    for (const env of [{}, { API_ORIGIN: '' }, { API_ORIGIN: 'http://example.com' }, { API_ORIGIN: `${API}/api` }]) {
      const plan = await buildUpstreamRequest(new Request(`${SITE}/api/ask`, { method: 'POST', body: '{}' }), env);
      assert.ok(!plan.ok);
      assert.equal(plan.response.status, 503);
      const body = await plan.response.json();
      assert.match(body.detail, /not connected yet/);
    }
  });

  it('answers 404 for a path that is not the API', async () => {
    const plan = await buildUpstreamRequest(new Request(`${SITE}/sources`), ENV);
    assert.ok(!plan.ok);
    assert.equal(plan.response.status, 404);
  });

  it('refuses a body over the limit, by declared length or by actual size', async () => {
    const big = 'x'.repeat(MAX_BODY_BYTES + 1);
    const actual = await buildUpstreamRequest(new Request(`${SITE}/api/ask`, { method: 'POST', body: big }), ENV);
    assert.ok(!actual.ok);
    assert.equal(actual.response.status, 413);
    const declared = await buildUpstreamRequest(new Request(`${SITE}/api/ask`, {
      method: 'POST', body: '{}', headers: { 'content-length': String(MAX_BODY_BYTES + 1) },
    }), ENV);
    assert.ok(!declared.ok);
    assert.equal(declared.response.status, 413);
    const exact = await buildUpstreamRequest(new Request(`${SITE}/api/ask`, {
      method: 'POST', body: 'x'.repeat(MAX_BODY_BYTES),
    }), ENV);
    assert.ok(exact.ok);
  });

  it('forwards a CORS preflight with its request headers', async () => {
    const plan = await buildUpstreamRequest(new Request(`${SITE}/mcp`, {
      method: 'OPTIONS',
      headers: {
        origin: 'https://example.org',
        'access-control-request-method': 'POST',
        'access-control-request-headers': 'content-type, mcp-protocol-version',
      },
    }), ENV);
    assert.ok(plan.ok);
    assert.equal(plan.init.method, 'OPTIONS');
    assert.equal(plan.init.body, undefined);
    const headers = /** @type {Headers} */ (plan.init.headers);
    assert.equal(headers.get('origin'), 'https://example.org');
    assert.equal(headers.get('access-control-request-method'), 'POST');
  });
});

describe('toClientResponse', () => {
  it('keeps status and body, drops cookies, adds noindex and no-store', async () => {
    const upstream = new Response('{"a":1}', {
      status: 429,
      headers: { 'content-type': 'application/json', 'retry-after': '120', 'set-cookie': 'x=1' },
    });
    const response = toClientResponse(upstream);
    assert.equal(response.status, 429);
    assert.equal(response.headers.get('retry-after'), '120');
    assert.equal(response.headers.get('set-cookie'), null);
    assert.equal(response.headers.get('x-robots-tag'), 'noindex, nofollow');
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(await response.text(), '{"a":1}');
  });
  it('keeps the API caching rule when it sets one', () => {
    const response = toClientResponse(new Response('ok', { headers: { 'cache-control': 'max-age=60' } }));
    assert.equal(response.headers.get('cache-control'), 'max-age=60');
  });
  it('handles statuses that must have no body', () => {
    assert.equal(toClientResponse(new Response(null, { status: 204 })).status, 204);
    assert.equal(toClientResponse(new Response(null, { status: 304 })).body, null);
  });
});

describe('siteLocation', () => {
  it('turns a redirect to the API itself into a path on this site, whatever the scheme or port', () => {
    // FastAPI's trailing-slash redirect, as the API sees itself behind Render's TLS: http, same host.
    assert.equal(siteLocation('http://evidenceline-api.onrender.com/api/ask', API), '/api/ask');
    assert.equal(siteLocation(`${API}/api/health?x=1#top`, API), '/api/health?x=1#top');
    assert.equal(siteLocation('https://evidenceline-api.onrender.com:8443/mcp', API), '/mcp');
    assert.equal(siteLocation('//evidenceline-api.onrender.com/api/ask', API), '/api/ask');
    assert.equal(siteLocation('/api/ask', API), '/api/ask');
    assert.equal(siteLocation('ask', `${API}`), '/ask');
  });
  it('leaves a redirect to another host alone', () => {
    assert.equal(siteLocation('https://example.org/elsewhere', API), 'https://example.org/elsewhere');
    assert.equal(siteLocation('https://evidenceline-api.onrender.com.example.org/x', API), 'https://evidenceline-api.onrender.com.example.org/x');
  });
  it('drops a Location it cannot read', () => {
    assert.equal(siteLocation('http://[bad', API), null);
  });
});

describe('handleProxy', () => {
  it('a trailing-slash redirect from the API keeps the caller on this site', async () => {
    const api = fakeFetch(() => new Response(null, {
      status: 307, headers: { location: 'http://evidenceline-api.onrender.com/api/ask' },
    }));
    const response = await handleProxy(new Request(`${SITE}/api/ask/`, { method: 'POST', body: '{}' }), ENV, api.fn);
    assert.equal(response.status, 307);
    assert.equal(response.headers.get('location'), '/api/ask');
    assert.equal(response.headers.get('x-robots-tag'), 'noindex, nofollow');
    assert.equal(api.calls[0]?.init.redirect, 'manual');
  });

  it('a Location naming the API origin on any status is rewritten too', async () => {
    const api = fakeFetch(() => new Response('{}', { status: 201, headers: { location: `${API}/mcp` } }));
    const response = await handleProxy(new Request(`${SITE}/mcp`, { method: 'POST', body: '{}' }), ENV, api.fn);
    assert.equal(response.headers.get('location'), '/mcp');
  });

  it('passes a POST through and returns the API response', async () => {
    const api = fakeFetch(() => new Response('{"status":"answered"}', {
      status: 200, headers: { 'content-type': 'application/json' },
    }));
    const response = await handleProxy(new Request(`${SITE}/api/ask`, {
      method: 'POST', body: '{"question":"What is a conceptual site model?"}',
      headers: { 'content-type': 'application/json', 'cf-connecting-ip': '203.0.113.7' },
    }), ENV, api.fn);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { status: 'answered' });
    assert.equal(api.calls.length, 1);
    assert.equal(api.calls[0]?.url, `${API}/api/ask`);
    assert.ok(api.calls[0]?.init.signal instanceof AbortSignal);
  });

  it('streams the response: the first chunk arrives before the API has finished', async () => {
    const encoder = new TextEncoder();
    /** @type {() => void} */
    let finish = () => {};
    const released = new Promise((resolve) => { finish = () => resolve(undefined); });
    const api = fakeFetch(() => new Response(new ReadableStream({
      async start(controller) {
        controller.enqueue(encoder.encode('event: message\ndata: {"id":1}\n\n'));
        await released;
        controller.enqueue(encoder.encode('event: message\ndata: {"id":2}\n\n'));
        controller.close();
      },
    }), { headers: { 'content-type': 'text/event-stream' } }));
    const response = await handleProxy(new Request(`${SITE}/mcp`, {
      method: 'POST', body: '{}', headers: { accept: 'text/event-stream' },
    }), ENV, api.fn);
    assert.equal(response.headers.get('content-type'), 'text/event-stream');
    const reader = /** @type {ReadableStream<Uint8Array>} */ (response.body).getReader();
    const first = await reader.read();
    assert.equal(new TextDecoder().decode(first.value), 'event: message\ndata: {"id":1}\n\n');
    finish();
    const second = await reader.read();
    assert.equal(new TextDecoder().decode(second.value), 'event: message\ndata: {"id":2}\n\n');
    assert.ok((await reader.read()).done);
  });

  it('answers 502 when the API cannot be reached', async () => {
    const response = await handleProxy(new Request(`${SITE}/api/health`), ENV, async () => {
      throw new TypeError('fetch failed');
    });
    assert.equal(response.status, 502);
    assert.match((await response.json()).detail, /could not be reached/);
  });

  it('answers 504 when the API is too slow', async () => {
    const slow = (_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(init.signal.reason));
    });
    const response = await handleProxy(new Request(`${SITE}/api/health`), ENV, slow, 20);
    assert.equal(response.status, 504);
  });

  it('never calls the API when API_ORIGIN is not set', async () => {
    const api = fakeFetch();
    const response = await handleProxy(new Request(`${SITE}/mcp`, { method: 'POST', body: '{}' }), {}, api.fn);
    assert.equal(response.status, 503);
    assert.equal(api.calls.length, 0);
  });

  it('no user-facing message contains an em or en dash', async () => {
    const messages = [];
    for (const [env, fetchImpl, path] of [
      [{}, fakeFetch().fn, '/api/ask'],
      [ENV, fakeFetch().fn, '/nothing'],
      [ENV, async () => { throw new TypeError('x'); }, '/api/ask'],
    ]) {
      const response = await handleProxy(new Request(`${SITE}${path}`), env, fetchImpl);
      messages.push((await response.json()).detail);
    }
    for (const message of messages) assert.doesNotMatch(message, /[\u2013\u2014]/);
  });
});
