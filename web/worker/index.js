// The Cloudflare Worker for evidenceline.autopilotyourworkflow.com (configured in ../wrangler.jsonc).
//
// It does one thing: /api, /api/*, /mcp and /mcp/* go to the proxy in ../functions/_lib/proxy.js (the only proxy
// implementation, shared with the tests), and everything else is the static site in dist/.
//
// In production Cloudflare serves the static files itself, without running this code, because wrangler.jsonc lists
// only the API and connector paths under assets.run_worker_first. The asset branch below is the fallback for any other
// request that reaches the Worker. Cloudflare applies dist/_headers only to files it serves directly, not to responses
// made here, so this code adds the noindex header itself on both branches (the proxy's responses already carry it).
//
// This module exports only the default handler: Cloudflare reads every named export of a Worker's main module as an
// entry point, so the helpers live in ./routing.js.
import { handleProxy } from '../functions/_lib/proxy.js';
import { isWorkerPath, withNoindex } from './routing.js';

/**
 * @typedef {object} Env
 * @property {{ fetch(request: Request): Promise<Response> }} ASSETS the static files in dist/ (assets binding)
 * @property {string} [API_ORIGIN] the API service's origin, such as https://evidenceline-api-xxxx.onrender.com; empty
 *   until the service exists, and then /api and /mcp answer 503 "not connected yet"
 * @property {string} [PROXY_SHARED_SECRET] the value the API expects in x-evidenceline-proxy-secret (a Worker secret)
 */

export default {
  /**
   * @param {Request} request
   * @param {Env} env
   * @returns {Promise<Response>}
   */
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (isWorkerPath(pathname)) {
      // A wrapper, not the bare global: the proxy calls it as a plain function.
      return handleProxy(request, env, (input, init) => fetch(input, init));
    }
    return withNoindex(await env.ASSETS.fetch(request));
  },
};
