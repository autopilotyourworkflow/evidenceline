// The website's proxy to the API service: /api/* and /mcp on the site are forwarded to the Render service named in
// the API_ORIGIN setting, so the browser and Claude only ever see one public address.
//
// All the logic is here, as plain functions with no Cloudflare-only APIs, so `node --test tests/functions.test.mjs`
// can check it. The Cloudflare Worker (worker/index.js) only decides which requests come here.
//
// What it forwards: the method, path, query, body and a short allowlist of headers. Cookies, credentials and any
// header the caller made up are dropped. It adds exactly two headers:
//   x-evidenceline-client-ip   the caller's address as Cloudflare saw it (cf-connecting-ip), for per-address limits;
//   x-evidenceline-proxy-secret  a shared value (PROXY_SHARED_SECRET) that lets the API tell this proxy apart from a
//                                direct caller, only when that setting exists. It is never sent to the browser.
// Responses are streamed back as they arrive, never buffered, so MCP Streamable HTTP works unchanged. A redirect the
// API sends to its own address (FastAPI's trailing-slash redirect names http://<API host>/api/ask) is rewritten to the
// same path on this site, so a caller is never sent past the proxy to the API service itself.

/** Request headers passed through to the API. Anything not listed is dropped. */
export const FORWARDED_REQUEST_HEADERS = Object.freeze([
  'accept',
  'content-type',
  'origin',
  'access-control-request-method',
  'access-control-request-headers',
  'mcp-protocol-version',
  'mcp-session-id',
  'last-event-id',
]);

/** Header carrying the caller's IP address to the API (set EVIDENCELINE_CLIENT_IP_HEADER to this on the API). */
export const CLIENT_IP_HEADER = 'x-evidenceline-client-ip';

/**
 * Header carrying PROXY_SHARED_SECRET to the API, so the API can trust CLIENT_IP_HEADER only from this proxy. It must
 * be the name the API reads: PROXY_SECRET_HEADER in src/evidenceline/api/settings.py (a test compares the two).
 */
export const PROXY_AUTH_HEADER = 'x-evidenceline-proxy-secret';

/** Largest request body forwarded. A question is at most 500 characters; an MCP call is a few kilobytes. */
export const MAX_BODY_BYTES = 64 * 1024;

/**
 * How long to wait for the API before answering 504. The question box itself gives up after 60 seconds
 * (TIMEOUT_MS in src/lib/answers.ts); this is longer so that a slow MCP tool call is not cut short.
 */
export const UPSTREAM_TIMEOUT_MS = 90_000;

const DROPPED_RESPONSE_HEADERS = Object.freeze(['set-cookie', 'connection', 'keep-alive', 'transfer-encoding']);
const NULL_BODY_STATUSES = new Set([101, 204, 205, 304]);
const IP_PATTERN = /^[0-9A-Fa-f:.]{3,45}$/;
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

/**
 * The API's origin (scheme, host and port) from the API_ORIGIN setting, or null when it is missing or unusable.
 * Only https is accepted, except http on this computer for local testing. A path, query or login is refused, so a
 * typo cannot send traffic somewhere unexpected.
 * @param {unknown} raw
 * @returns {string | null}
 */
export function parseApiOrigin(raw) {
  if (typeof raw !== 'string' || raw.trim() === '') return null;
  let url;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  const secure = url.protocol === 'https:';
  const local = url.protocol === 'http:' && LOCAL_HOSTS.has(url.hostname);
  if (!secure && !local) return null;
  if (url.username || url.password || url.search || url.hash) return null;
  if (url.pathname !== '/' && url.pathname !== '') return null;
  return url.origin;
}

/**
 * Whether a site path belongs to the API: exactly /mcp, or /api and anything under it.
 * @param {string} pathname
 * @returns {boolean}
 */
export function isProxiedPath(pathname) {
  return pathname === '/mcp' || pathname === '/api' || pathname.startsWith('/api/');
}

/**
 * The caller's IP address as Cloudflare reported it, or null when absent or not shaped like an address.
 * @param {Headers} headers
 * @returns {string | null}
 */
export function callerIp(headers) {
  const value = (headers.get('cf-connecting-ip') ?? '').trim();
  return IP_PATTERN.test(value) ? value : null;
}

/**
 * A JSON error in the shape the website already reads: {"detail": "..."}.
 * @param {number} status
 * @param {string} detail
 * @returns {Response}
 */
export function errorResponse(status, detail) {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      'x-robots-tag': 'noindex, nofollow',
    },
  });
}

/**
 * Work out the request to send to the API, or the error to send back instead.
 * @param {Request} request the request the site received
 * @param {Record<string, unknown>} env the Worker's settings (API_ORIGIN, optional PROXY_SHARED_SECRET)
 * @returns {Promise<{ ok: true, url: string, init: RequestInit, origin: string } | { ok: false, response: Response }>}
 */
export async function buildUpstreamRequest(request, env) {
  const origin = parseApiOrigin(env.API_ORIGIN);
  if (origin === null) {
    return { ok: false, response: errorResponse(503, 'The live service is not connected yet. Please try again later.') };
  }
  const url = new URL(request.url);
  if (!isProxiedPath(url.pathname)) {
    return { ok: false, response: errorResponse(404, 'There is nothing at this address.') };
  }

  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const ip = callerIp(request.headers);
  if (ip !== null) headers.set(CLIENT_IP_HEADER, ip);
  const secret = typeof env.PROXY_SHARED_SECRET === 'string' ? env.PROXY_SHARED_SECRET.trim() : '';
  if (secret !== '') headers.set(PROXY_AUTH_HEADER, secret);

  /** @type {RequestInit} */
  const init = { method: request.method, headers, redirect: 'manual' };
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    const declared = Number(request.headers.get('content-length') ?? '0');
    if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) {
      return { ok: false, response: errorResponse(413, 'The request is too large.') };
    }
    const body = await request.arrayBuffer();
    if (body.byteLength > MAX_BODY_BYTES) {
      return { ok: false, response: errorResponse(413, 'The request is too large.') };
    }
    if (body.byteLength > 0) init.body = body;
  }
  return { ok: true, url: `${origin}${url.pathname}${url.search}`, init, origin };
}

/**
 * A redirect target as the caller should see it. A Location on the API's own host (any scheme or port: behind
 * Render's TLS the API names itself over http) or a relative one becomes a path on this site, so the caller comes
 * back through the proxy. Any other Location is left as the API sent it, and one that cannot be read is dropped.
 * @param {string} location the upstream Location header
 * @param {string} apiOrigin the API origin the request went to (from parseApiOrigin)
 * @returns {string | null}
 */
export function siteLocation(location, apiOrigin) {
  let target;
  try {
    target = new URL(location, `${apiOrigin}/`);
  } catch {
    return null;
  }
  if (target.hostname !== new URL(apiOrigin).hostname) return location;
  return `${target.pathname}${target.search}${target.hash}`;
}

/**
 * The API's response as sent back to the caller: same status and body stream (never read here), minus cookies and
 * connection headers, plus noindex, and no-store when the API set no caching rule. With `apiOrigin`, a Location that
 * names the API itself is rewritten to this site (siteLocation).
 * @param {Response} upstream
 * @param {string | null} [apiOrigin]
 * @returns {Response}
 */
export function toClientResponse(upstream, apiOrigin = null) {
  const headers = new Headers(upstream.headers);
  for (const name of DROPPED_RESPONSE_HEADERS) headers.delete(name);
  const location = headers.get('location');
  if (location !== null && apiOrigin !== null) {
    const rewritten = siteLocation(location, apiOrigin);
    if (rewritten === null) headers.delete('location');
    else headers.set('location', rewritten);
  }
  headers.set('x-robots-tag', 'noindex, nofollow');
  if (!headers.has('cache-control')) headers.set('cache-control', 'no-store');
  const body = NULL_BODY_STATUSES.has(upstream.status) ? null : upstream.body;
  return new Response(body, { status: upstream.status, statusText: upstream.statusText, headers });
}

/**
 * Forward one request to the API and return its response, or a plain error when the API cannot be reached.
 * @param {Request} request
 * @param {Record<string, unknown>} env
 * @param {typeof fetch} fetchImpl the global fetch in production; a fake in tests
 * @param {number} [timeoutMs]
 * @returns {Promise<Response>}
 */
export async function handleProxy(request, env, fetchImpl, timeoutMs = UPSTREAM_TIMEOUT_MS) {
  const plan = await buildUpstreamRequest(request, env);
  if (!plan.ok) return plan.response;
  let upstream;
  try {
    upstream = await fetchImpl(plan.url, { ...plan.init, signal: AbortSignal.timeout(timeoutMs) });
  } catch (error) {
    if (error instanceof Error && (error.name === 'TimeoutError' || error.name === 'AbortError')) {
      return errorResponse(504, 'The live service took too long to answer. Please try again.');
    }
    return errorResponse(502, 'The live service could not be reached. Please try again later.');
  }
  return toClientResponse(upstream, plan.origin);
}
