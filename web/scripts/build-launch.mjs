// The launch build: the published site's settings (.env.production) with the live question box, the connector and
// the robot check switched off, so the box and the connector card say they are coming soon. Only the "Code on
// GitHub" link stays on. It is what goes live first, before the API service exists (DEPLOY.md).
//
//   npm run build:launch        (tsc -b, then this script: writes dist/)
//
// The smoke test imports buildLaunch() to build the same thing into a temporary folder.
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');

/**
 * The settings switched off for the launch. Vite lets a variable already set in the environment win over the .env
 * files, and an empty value switches the feature off (src/lib/config.ts).
 */
export const LAUNCH_OFF = Object.freeze({ VITE_API_BASE: '', VITE_MCP_URL: '', VITE_TURNSTILE_SITE_KEY: '' });

/**
 * Builds the launch variant into `outDir` (mode production, so .env.production supplies VITE_GITHUB_URL). The
 * environment is put back as it was afterwards.
 * @param {string} outDir
 * @param {{ logLevel?: 'info' | 'warn' | 'error' | 'silent' }} [options]
 */
export async function buildLaunch(outDir, { logLevel = 'info' } = {}) {
  /** @type {Record<string, string | undefined>} */
  const saved = {};
  for (const [key, value] of Object.entries(LAUNCH_OFF)) {
    saved[key] = process.env[key];
    process.env[key] = value;
  }
  try {
    await build({ root: webDir, mode: 'production', logLevel, build: { outDir, emptyOutDir: true } });
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

if (process.argv[1] !== undefined && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await buildLaunch(resolve(webDir, 'dist'));
}
