// Routing helpers for the Cloudflare Worker (index.js). They live in their own module because Cloudflare treats every
// named export of a Worker's main module as an entry point: exporting a constant or a helper from index.js makes the
// Worker fail to start ("Incorrect type for map entry"). index.js exports only its default handler; tests import these.

/** The robots rule every response carries, the same as dist/_headers. */
export const NOINDEX = 'noindex, nofollow';

/**
 * Whether a request path belongs to the Worker rather than to the static files: /api, /mcp and anything under them.
 * The proxy forwards /api, /api/* and exactly /mcp; anything under /mcp/ gets its plain 404, not the site's HTML page,
 * so an MCP client given a mistyped address sees a clear error.
 * @param {string} pathname
 * @returns {boolean}
 */
export function isWorkerPath(pathname) {
  return pathname === '/api' || pathname.startsWith('/api/') || pathname === '/mcp' || pathname.startsWith('/mcp/');
}

/**
 * A response with the noindex header set, keeping its status, other headers and body stream.
 * @param {Response} response
 * @returns {Response}
 */
export function withNoindex(response) {
  if (response.headers.get('x-robots-tag') === NOINDEX) return response;
  const headers = new Headers(response.headers);
  headers.set('x-robots-tag', NOINDEX);
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}
