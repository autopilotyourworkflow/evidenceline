// Links and endpoints that are switched on per build. They come from Vite build settings (.env.example for local
// builds, .env.production for the published site, scripts/build-launch.mjs for the launch build), never from the page
// copy, so an unset value hides the feature, or says it is coming soon, instead of leaving a dead link.

import { readApiBase, readSiteKey, readUrl } from './settings';

export type SiteConfig = {
  /** The public code repository ("Code on GitHub"). */
  readonly githubUrl: string | null;
  /** The read-only remote MCP connector ("Use it inside your own Claude"). */
  readonly mcpUrl: string | null;
  /** Base of the live question service; the box posts to `${apiBase}/api/ask`. "" is the site's own origin. */
  readonly apiBase: string | null;
  /** Turnstile site key: when set (and the live service is on), the question box shows the robot check. */
  readonly turnstileSiteKey: string | null;
};

export const CONFIG: SiteConfig = {
  githubUrl: readUrl(import.meta.env.VITE_GITHUB_URL),
  mcpUrl: readUrl(import.meta.env.VITE_MCP_URL),
  apiBase: readApiBase(import.meta.env.VITE_API_BASE),
  turnstileSiteKey: readSiteKey(import.meta.env.VITE_TURNSTILE_SITE_KEY),
};
