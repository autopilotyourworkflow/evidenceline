import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'
import { buildFeatures, buildKind, type BuildSettings } from './src/lib/settings.ts'

/**
 * Writes dist/build.json: which kind of build this is ("full", "launch", "offline" or "custom") and which features it
 * switched on, read with the page's own rules (src/lib/settings.ts). Nothing else in dist/ shows it, and DEPLOY.md
 * checks it before each deploy (`npm run verify:launch` or `npm run verify:full`), so the wrong build cannot go out
 * unnoticed. It holds only public settings.
 */
function buildInfo(): Plugin {
  let settings: BuildSettings = {}
  let mode = ''
  return {
    name: 'evidenceline-build-info',
    apply: 'build',
    configResolved(config) {
      settings = config.env
      mode = config.mode
    },
    generateBundle() {
      const features = buildFeatures(settings)
      const info = { kind: buildKind(features), mode, ...features }
      this.emitFile({ type: 'asset', fileName: 'build.json', source: `${JSON.stringify(info, null, 2)}\n` })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), buildInfo()],
})
