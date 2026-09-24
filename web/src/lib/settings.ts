// How the build settings are read. Shared by the page (config.ts, through import.meta.env) and by vite.config.ts, which
// records in dist/build.json which features a build switched on, so both always agree. No import.meta here: Node
// loads this file when it reads vite.config.ts.

/** An absolute http(s) URL with any trailing slashes removed, or null when the setting is empty or not a URL. */
export function readUrl(raw: string | undefined): string | null {
  const value = (raw ?? '').trim();
  if (value === '') return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return null;
  } catch {
    return null;
  }
  return value.replace(/\/+$/, '');
}

/**
 * The question service's base: "/" means the site's own origin (the published site proxies /api/* to the service),
 * so the result is "" and the box posts to "/api/ask". Otherwise an absolute URL as in readUrl, or null (switched off).
 */
export function readApiBase(raw: string | undefined): string | null {
  return (raw ?? '').trim() === '/' ? '' : readUrl(raw);
}

/**
 * A Cloudflare Turnstile site key, or null when the setting is empty or not shaped like one. Site keys are public
 * (they are in the page anyway); the matching secret lives only on the API service.
 */
export function readSiteKey(raw: string | undefined): string | null {
  const value = (raw ?? '').trim();
  return /^[0-9A-Za-z_-]{10,100}$/.test(value) ? value : null;
}

/** The settings a build reads, by their Vite names. */
export type BuildSettings = Readonly<Record<string, string | undefined>>;

/** Which features a build switches on, as the page will see them. */
export type BuildFeatures = {
  readonly questionBox: boolean;
  readonly connector: boolean;
  readonly robotCheck: boolean;
  readonly codeLink: boolean;
};

/**
 * The kind of build, named as DEPLOY.md names it: "full" (question box, connector and code link on, npm run build),
 * "launch" (only the code link, npm run build:launch), "offline" (nothing, npm run dev and build:offline) or
 * "custom" (any other mix, such as a test build).
 */
export type BuildKind = 'full' | 'launch' | 'offline' | 'custom';

export function buildFeatures(env: BuildSettings): BuildFeatures {
  const questionBox = readApiBase(env['VITE_API_BASE']) !== null;
  return {
    questionBox,
    connector: readUrl(env['VITE_MCP_URL']) !== null,
    // The robot check exists only when the live service does (TryIt.tsx).
    robotCheck: questionBox && readSiteKey(env['VITE_TURNSTILE_SITE_KEY']) !== null,
    codeLink: readUrl(env['VITE_GITHUB_URL']) !== null,
  };
}

export function buildKind(f: BuildFeatures): BuildKind {
  if (f.questionBox && f.connector && f.codeLink) return 'full';
  if (f.codeLink && !f.questionBox && !f.connector) return 'launch';
  if (!f.codeLink && !f.questionBox && !f.connector) return 'offline';
  return 'custom';
}
