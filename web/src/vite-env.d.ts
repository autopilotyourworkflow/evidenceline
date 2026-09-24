/// <reference types="vite/client" />

// Build-time settings (see .env.example). Each is optional; an empty or missing value switches that feature off.
interface ImportMetaEnv {
  readonly VITE_GITHUB_URL?: string;
  readonly VITE_MCP_URL?: string;
  readonly VITE_API_BASE?: string;
  readonly VITE_TURNSTILE_SITE_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
