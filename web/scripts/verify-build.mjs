// Checks which build is in dist/ before a deploy: `npm run verify:launch` (stage 1, DEPLOY.md) or
// `npm run verify:full` (stage 2). It reads dist/build.json, which vite.config.ts writes on every build, and exits 1
// when the build is a different kind, is missing, or cannot be read.
//
//   node scripts/verify-build.mjs <launch|full> [dist folder]
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const KINDS = new Set(['launch', 'full']);
/** Plain names for the features build.json records. */
const FEATURES = Object.freeze({ questionBox: 'the question box', connector: 'the connector', robotCheck: 'the robot check', codeLink: 'the GitHub link' });
const webDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');

/**
 * The kind recorded in `<distDir>/build.json`, or an error message.
 * @param {string} distDir
 * @returns {{ ok: true, info: Record<string, unknown> } | { ok: false, message: string }}
 */
export function readBuildInfo(distDir) {
  const path = join(distDir, 'build.json');
  let raw;
  try {
    raw = readFileSync(path, 'utf8');
  } catch {
    return { ok: false, message: `${path} is missing. Build first: npm run build:launch (stage 1) or npm run build (stage 2).` };
  }
  try {
    const info = JSON.parse(raw);
    if (info === null || typeof info !== 'object' || typeof info.kind !== 'string') throw new Error('no kind');
    return { ok: true, info };
  } catch {
    return { ok: false, message: `${path} could not be read. Rebuild.` };
  }
}

if (process.argv[1] !== undefined && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const expected = process.argv[2] ?? '';
  if (!KINDS.has(expected)) {
    console.error('Usage: node scripts/verify-build.mjs <launch|full> [dist folder]');
    process.exit(2);
  }
  const distDir = resolve(webDir, process.argv[3] ?? 'dist');
  const read = readBuildInfo(distDir);
  if (!read.ok) {
    console.error(`verify-build: ${read.message}`);
    process.exit(1);
  }
  const { info } = read;
  const on = Object.entries(FEATURES).filter(([k]) => info[k] === true).map(([, name]) => name).join(', ') || 'nothing';
  const folder = process.argv[3] ?? 'dist';
  if (info.kind !== expected) {
    console.error(`verify-build: ${folder} holds the ${String(info.kind)} build (switched on: ${on}), not the ${expected} build. Run ${expected === 'launch' ? 'npm run build:launch' : 'npm run build'}, then check again.`);
    process.exit(1);
  }
  console.log(`verify-build: ${folder} holds the ${expected} build (switched on: ${on}).`);
}
