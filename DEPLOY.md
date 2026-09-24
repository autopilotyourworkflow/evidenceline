# Going live

Evidenceline goes live in two stages. First the website launches on its own, with the question box and the connector
saying they are "coming soon". Later, once the owner has created the API service and an Anthropic key, both are
switched on. The owner's assistant runs every command; the owner's own steps are short and listed first under
stage 2.

## How the pieces fit

- **Website:** one Cloudflare Worker, `evidenceline`, with static assets, on the Autopilot Cloudflare account
  (`a2d57c1e05c835fb5f259ca1b2725b59`), at https://evidenceline.autopilotyourworkflow.com. Settings:
  `web/wrangler.jsonc`; code: `web/worker/index.js`. Free.
  - The static files come from `web/dist`. Cloudflare serves them directly, with `dist/_headers` applied
    (`X-Robots-Tag: noindex, nofollow` on every path). Unknown paths get `index.html` (the page shows its own "not
    found" view).
  - Only `/api`, `/api/*`, `/mcp` and `/mcp/*` run the Worker code, which hands them to the proxy
    (`web/functions/_lib/proxy.js`). The proxy forwards them to the API service named in `API_ORIGIN`, and marks its
    own responses noindex. A redirect the API sends to its own address (such as its trailing-slash redirect from
    `/api/ask/` to `/api/ask`) comes back as the same path on the website, so no caller is sent to Render directly.
    While `API_ORIGIN` is empty it answers 503 "The live service is not connected yet".
  - The address is created by the Worker's route (`custom_domain: true`): the first deploy creates the DNS record and
    the certificate in the `autopilotyourworkflow.com` zone. There is no `workers.dev` address and no preview address.
- **API and remote connector (stage 2):** one Render web service, `evidenceline-api`, Singapore, Starter instance
  (about 7 USD a month), described in `render.yaml`. It runs the question box (`/api/ask`) and the read-only MCP
  connector (`/mcp`). People only ever see the website's address; the Worker forwards to Render.
- **The Anthropic key** lives only in Render's settings, in a dedicated workspace with a 15 USD monthly limit.
  Without it, the question box still works but shows passages only.
- **Turnstile** (the "not a robot" check in the question box, optional): its public site key is baked into the website
  build (`VITE_TURNSTILE_SITE_KEY`); its secret lives only on Render (`TURNSTILE_SECRET`).

## Ground rules for the assistant

- **Only the isolated wrangler login.** Every Cloudflare command goes through `.deploy/wr` (git-ignored), which runs
  wrangler with its own home folder (`.deploy/wrangler-home`) and `CLOUDFLARE_ACCOUNT_ID` set to the Autopilot
  account. Never run a bare `wrangler` or `npx wrangler` against Cloudflare: this computer's own login belongs to
  other accounts. `web/wrangler.jsonc` pins the same `account_id`, so a deploy with any other login fails instead of
  publishing somewhere else.
- **Check the account first.** `.deploy/wr whoami` must list the Autopilot account. The same login can also see a
  client account: never deploy there.
- **Nothing secret in tracked files.** The proxy secret is in `.deploy/proxy_secret.txt` and the Turnstile keys in
  `.deploy/turnstile.env` (both git-ignored). `python scripts/prepublish_check.py --list` must end with "Clean" before
  any push; it accepts `web/.env.production` only while it holds nothing but public `VITE_` settings.
- **Before every deploy,** from `web/`: `npm ci`, `npm run check` (all smoke checks pass),
  `node --test tests/functions.test.mjs tests/worker.test.mjs`, `node tests/adversarial.mjs`,
  `node tests/adversarial_launch.mjs` (local `wrangler dev` only, about a minute), and a dry run that
  contacts no account: `../.deploy/wr deploy --dry-run --outdir "$TEMP/evl-dry"` (it lists the `ASSETS` binding and
  `API_ORIGIN`).

## Stage 1: launch the website (now)

The website goes live with the "Code on GitHub" link on, and the question box and connector card in "coming soon"
mode: the box says live answers are switching on soon and offers the prepared questions; the card says the connector
link will appear here soon. `/api` and `/mcp` answer 503 "not connected yet".

All from `web/`:

1. `npm ci` and `npm run check`.
2. `npm run build:launch`. This is the published build (`.env.production`) with `VITE_API_BASE`, `VITE_MCP_URL` and
   `VITE_TURNSTILE_SITE_KEY` switched off. It writes `dist/`. (Run it after `npm run check`, which leaves the full
   build in `dist/`.) Then `npm run verify:launch`: it reads `dist/build.json`, which every build writes, and must
   say "dist holds the launch build". If it names the full build, run `npm run build:launch` again.
3. Confirm `API_ORIGIN` is `""` in `wrangler.jsonc`, then the dry run above.
4. `../.deploy/wr whoami` (Autopilot account), then `../.deploy/wr deploy`. If a placeholder Worker named
   `evidenceline` is already on the address, this replaces it.
5. Checks on the live address:
   - `/`, `/tidy`, `/sources`, `/accuracy`, `/sample-site` and a made-up path load on a desktop and a phone, and every
     page says "Concept by Chanon (Beam) Poovaviranon, not affiliated with Western Environmental".
   - `curl -sI https://evidenceline.autopilotyourworkflow.com/` and `.../sources` show
     `X-Robots-Tag: noindex, nofollow`.
   - `curl -s -X POST https://evidenceline.autopilotyourworkflow.com/api/ask` answers 503 with
     `{"detail": "The live service is not connected yet. Please try again later."}` and the noindex header.
   - The question box and the connector card say "soon"; a prepared question shows its answer.

## Stage 2: switch on the question box and connector (later)

### The owner's steps (about 15 minutes, then a few minutes of waiting)

**Anthropic: a key with a 15 USD monthly limit**

1. At https://console.anthropic.com open **Settings**, **Workspaces**, **Create workspace**, named
   `evidenceline-demo`.
2. In that workspace, **Limits**: set the monthly spend limit to **15 USD** and save.
3. **Settings**, **API keys**, **Create key**, in workspace `evidenceline-demo`, named `render`. Copy it (shown
   once) and keep it for the next part only. If the account has no credit, add a small amount under **Billing**; the
   limit still caps spending at 15 USD a month.

**Render: the API service**

1. At https://render.com choose **Get Started**, then **Sign up with GitHub**, with the GitHub account that owns
   `autopilotyourworkflow/evidenceline`. When GitHub asks, allow **Only select repositories**: `evidenceline`.
2. Avatar (top right), **Billing**: add a card.
3. Open this link: https://render.com/deploy?repo=https://github.com/autopilotyourworkflow/evidenceline
   Render reads `render.yaml` and shows one service, `evidenceline-api`. Fill in:
   - `ANTHROPIC_API_KEY`: the key from Anthropic.
   - `EVIDENCELINE_PROXY_SECRET`: the value the assistant gives you (from `.deploy/proxy_secret.txt`).
   - `TURNSTILE_SECRET`: leave empty (the assistant adds it later).
4. Click **Deploy Blueprint** and wait until the service shows **Live** (the first build downloads the guidance and
   builds the search index). Send the assistant the address at the top of the service page
   (`https://evidenceline-api-xxxx.onrender.com`).

### What the assistant does

1. **Check the service:** `https://<render address>/api/health` answers `"status": "ok"`, `"index": true` and
   `"live_answers": true`.
2. **Give the Worker the shared secret** (from `web/`):
   `../.deploy/wr secret put PROXY_SHARED_SECRET < ../.deploy/proxy_secret.txt`. It must equal Render's
   `EVIDENCELINE_PROXY_SECRET`: with it the API trusts the visitor address the proxy sends, so per-visitor limits
   work and cannot be dodged by calling Render directly. Secrets stay across deploys.
3. **Point the Worker at the service:** set `"API_ORIGIN"` in `web/wrangler.jsonc` to the Render address (https, no
   trailing slash, no path) and commit it. It is not a secret, and keeping it in the file means every later deploy
   keeps it. (`tests/worker.test.mjs` accepts an empty value or an https origin only.)
4. **Build everything on:** `npm run check`, then `npm run build`, which leaves the full build in `dist/`
   (`.env.production`: the repository, the `/mcp` connector link, and the question box posting to this site's own
   `/api/ask`). Then `npm run verify:full`, which must say "dist holds the full build". To add the robot check in the same build, see "Turnstile" below; otherwise leave it for later.
5. Dry run, `../.deploy/wr whoami`, then `../.deploy/wr deploy`.
6. **Checks on the live address:**
   - `GET /api/health` answers through the website, with the noindex header.
   - One real question in the box gets an answer or passages, with sources.
   - The connector: `claude mcp add --transport http evidenceline-check https://evidenceline.autopilotyourworkflow.com/mcp`,
     list its nine tools, call `lookup_limit` for PFOS under both rules, then remove it.
   - Calling Render's own address directly with a made-up visitor address is refused (403 without the secret).
7. **Report back** with what passed, the costs shown, and the links.

### Turnstile (the "not a robot" check)

The widget is created on the Autopilot account for `evidenceline.autopilotyourworkflow.com`, managed mode; its keys
are in `.deploy/turnstile.env`. Order matters, because the API refuses every question once it has a secret:

1. **Website first:** build with the site key, `VITE_TURNSTILE_SITE_KEY=<site key> npm run build` (or put the key in
   `web/.env.production`, which is public), check that `npm run verify:full` lists "the robot check", and deploy.
   The box now shows the check and sends `turnstile_token`;
   the API ignores it while it has no secret.
2. **Then the API:** in Render, `evidenceline-api`, **Environment**, set `TURNSTILE_SECRET` and save (Render
   redeploys). `/api/health` then shows `"turnstile": true`.
3. To remove it, reverse the order: clear `TURNSTILE_SECRET` on Render first, then rebuild without the site key.

Locally, `npm run test:turnstile` (from `web/`, needs the internet) loads Cloudflare's real script with Cloudflare's
always-pass test key and checks the widget, the token sent with a question, the keyboard and a 390 px phone.

## Rollback

- **Website back to the previous version:** `../.deploy/wr deployments list`, then
  `../.deploy/wr rollback <version id>`; or Cloudflare dashboard, **Workers & Pages**, `evidenceline`,
  **Deployments**, **Rollback**. Takes effect in seconds.
- **Back to "coming soon", keep the website:** set `API_ORIGIN` to `""` in `web/wrangler.jsonc`, `npm run build:launch`,
  `npm run verify:launch`, and deploy. `/api` and `/mcp` then answer "The live service is not connected yet".
- **Take the website offline:** Cloudflare dashboard, `evidenceline`, **Settings**, **Domains & Routes**, remove
  `evidenceline.autopilotyourworkflow.com` (or `../.deploy/wr delete` to remove the Worker entirely).
- **API back to the previous version:** Render dashboard, `evidenceline-api`, **Events**, the last good deploy,
  **Rollback**. Render only deploys commits whose GitHub checks passed.
- **Stop all spending at once:** disable the `render` key in the `evidenceline-demo` workspace (answers fall back to
  passages only), and in Render **Settings**, **Suspend Service**. The 15 USD workspace limit is the backstop.
- **A bad commit:** revert it on GitHub; Render deploys the revert once CI passes. Rebuild and redeploy the website
  from the reverted commit.

## Costs

| Item | Cost |
|---|---|
| Cloudflare Worker, static assets, custom domain and Turnstile | free |
| Render Starter instance, Singapore (stage 2) | about 7 USD a month |
| Anthropic API, `evidenceline-demo` workspace (stage 2) | at most 15 USD a month (the limit) |
| GitHub public repository and Actions | free |
