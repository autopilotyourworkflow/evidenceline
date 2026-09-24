# Evidenceline website

The public concept site for Evidenceline: Vite, React 19 and TypeScript (strict), static. The only backend it talks to
is the optional live question service (`VITE_API_BASE`), reached through the site's own Cloudflare Worker.

Concept by Chanon (Beam) Poovaviranon, not affiliated with Western Environmental. Fictional site, public guidance only.

## Pages

- `/` the landing page, ported from a local design draft (v3, not in the repository)
- `/sample-site` every PFOS and PFHxS result for well MB2, with lab report, file and row
- `/tidy` the full `tidy_lab_files` result for FDS-01: files read, the six review items in plain English with a
  "Technical detail" drawer each (finding, evidence rows, rule, quoted source, what the scientist decides), what was
  checked and not flagged, and what was not checked
- `/sources` the drinking-water values, and the soil HIL A values the tidy step uses, with document, table and page
- `/accuracy` automated test results from `accuracy.json`, or a placeholder until the test run publishes them

Data files in `public/data/`:

| File | Written by | Used by |
|---|---|---|
| `sample-site.json`, `sources.json` | `npm run data` and `../scripts/export_web_data.py` (byte for byte the same) | `/sample-site`, `/sources` |
| `soil-criteria.json` | `npm run data` only, from `../src/evidenceline/data/fds01_site/soil_criteria.json` | `/sources` |
| `tidy.json` | `../scripts/export_web_data.py` | `/tidy`, the landing's tidy preview |
| `answers.json` | the answering pipeline (optional) | the landing's "Try it yourself" box |
| `accuracy.json` | the test run | `/accuracy` |

A missing or unreadable `answers.json` is not an error: the box falls back to two hand-written examples.

## Build settings and the three builds

Every live feature is a build setting, so an unset value hides the feature, or says it is coming soon, instead of
leaving a dead link (`src/lib/config.ts`; the full list with comments is in `.env.example`; `.env.local` is
git-ignored):

| Variable | Effect when set | When empty |
|---|---|---|
| `VITE_GITHUB_URL` | "Code on GitHub" button in the About section | no button |
| `VITE_MCP_URL` | the connector link with a Copy button | the card says the connector link will appear here soon |
| `VITE_API_BASE` | a typed question is posted to `{VITE_API_BASE}/api/ask`; `/` posts to this site's own `/api/ask` | the box says live answers are switching on soon and offers the prepared questions |
| `VITE_TURNSTILE_SITE_KEY` | the question box shows Cloudflare Turnstile (managed) and sends its token as `turnstile_token` | no check, and no request to Cloudflare |

The three builds:

| Command | Settings | Used for |
|---|---|---|
| `npm run build` | `.env.production`: the repository, `https://evidenceline.autopilotyourworkflow.com/mcp`, `VITE_API_BASE=/` | the site with everything on (stage 2 in `../DEPLOY.md`) |
| `npm run build:launch` | the same, with `VITE_API_BASE`, `VITE_MCP_URL` and `VITE_TURNSTILE_SITE_KEY` switched off (`scripts/build-launch.mjs`) | the first launch: the repository link on, the box and the connector "coming soon" |
| `npm run build:offline` | nothing (mode `offline` does not read `.env.production`) | local work; `npm run dev` is the same |

A variable set in the build environment overrides `.env.production`. Do not set `VITE_API_BASE` to an empty string
for the full build, because empty means off.

Every build also writes `dist/build.json` (from `vite.config.ts`, with the page's own rules in `src/lib/settings.ts`):
its kind (`full`, `launch`, `offline` or `custom`) and which features it switched on. `npm run verify:launch` and
`npm run verify:full` (`scripts/verify-build.mjs`) read it and exit 1 when `dist/` holds another kind, so the wrong
build is caught before a deploy.

Turnstile, when on: Cloudflare's script (`challenges.cloudflare.com/turnstile/v0/api.js`, explicit rendering) is
fetched only when the question box comes within about a screen of the viewport, and only in a build with both a site
key and `VITE_API_BASE`. Whether the check is managed, non-interactive or invisible is a setting of the site key in
Cloudflare, not of this code. A question waits up to 15 seconds for a token; with none (still checking, blocked
script, widget error) it is not sent and the box says why. Each token is used once, then the widget is reset. The
API checks the token only once `TURNSTILE_SECRET` is set on it; set the site key on the website first (`../DEPLOY.md`).

### The live question service (`POST {VITE_API_BASE}/api/ask`)

Request: `{"question": "..."}` (plus `"turnstile_token"` when Turnstile is on) with `Content-Type: application/json`
(the page limits it to 500 characters). On the published site the call stays on the site's own origin, so no CORS is
involved; a service on another origin must answer the CORS preflight for the site's origin.
Responses the page understands:

- `200` with the answer result, either bare or as `{"result": {...}}`.
- `429` (optionally `Retry-After`): "too many questions" message.
- `503`, or a body with `"status": "paused"`: "live answers are paused" message.
- Any other status: a short error message (a string `detail` of up to 200 characters is quoted).
- No answer within 60 seconds, or no connection: an error message. Nothing is retried automatically.

A question that matches a prepared one (ignoring case and spacing) is answered from `answers.json` with no call.

### Answer result (prepared and live)

```json
{ "status": "answered | not covered | guard rail (any wording; see lib/answers.ts)",
  "answer": "text; paragraphs separated by a blank line",
  "citations": [{ "n": 1, "document": "", "edition": "", "printed_page": "29", "pdf_page": 34,
                  "section": "", "excerpt": "", "link": "https://...#page=34" }],
  "guideline_values": [{ "analyte": "PFOS", "rule_name": "", "value": "0.008", "unit": "ug/L",
                         "document": "", "table": "", "page": "" }],
  "notes": ["..."] }
```

`answers.json` is `{"prepared": {date, model, label}, "answers": [{question, result, prepared?}]}` (a bare list also
works; each entry's `prepared` overrides the top-level one). The first four entries become the suggested questions.
Every field is read defensively: missing or wrongly typed fields are left out, links that are not http(s) are shown
as text, and em or en dashes in answer text and notes are replaced for display.

### `accuracy.json` with results

The page shows results when `status` is not `"pending"` and at least one of these is present; otherwise it keeps
the placeholder (`message`). Failures are listed as failures.

```json
{ "status": "published", "last_run": "2026-09-24", "build": "local build", "commit": "abc1234 (used when build is empty)",
  "suites": [{ "name": "Tidy lab results", "passed": 120, "failed": 0, "skipped": 0, "failures": [] }],
  "search": [{ "set": "golden", "label": "Tuning set", "note": "...", "questions": 26,
               "metrics": [{ "name": "hit@1", "hits": 12, "of": 22 }],
               "misses": [{ "id": "h01", "question": "...", "result": "said \"not covered\"" }] },
             { "set": "golden-casual", "part_of": "golden", "label": "...", "metrics": [], "misses": [] }],
  "verification": { "passes": [{ "id": "A", "method": "..." }, { "id": "B", "method": "..." }],
                    "rechecks": [{ "id": "A", "label": "...", "method": "..." }],
                    "values": [{ "id": "current/PFOS", "label": "...", "value": "0.008", "unit": "ug/L",
                                 "source": "Table 4, printed page 49", "verdicts": { "A": "match", "B": "mismatch" },
                                 "note": "" }],
                    "notes": [{ "id": "...", "label": "...", "file_note": "...", "checked_note": "...",
                                "status": "...", "source_says": "...", "confirmed_now": true,
                                "verdicts": { "A": { "verdict": "mismatch", "note": "..." }, "B": "mismatch" },
                                "rechecks": { "A": { "verdict": "confirmed", "note": "..." } } }],
                    "recheck_findings": [{ "id": "...", "label": "...", "context": "...", "resolution": "...",
                                           "open": false, "verdicts": { "A": { "verdict": "mismatch", "note": "..." } } }] } }
```

The real file is written by `../scripts/build_accuracy.py`. `failures` names each failed test; `misses` lists the
questions a search set got wrong (shown one click away). A set with `part_of` is a group of another set's questions
from the same run (held-out set 2's casual questions): it gets its own card but no line in "In short", and its
misses stay with the whole set. The fair set also carries `near_copies` (`[{"held_out": "p07", "tuning": "h05"}]`)
and `without_near_copies` (`{"left_out": [...], "questions": 23, "metrics": [...]}`): the same run with the
questions close to a tuning question left out, shown as a second "In short" line. `notes` lists explanatory notes next to values that a verification pass did not
confirm or that were reworded after the passes; `rechecks` are the later re-checks' verdicts on the current wording,
and a note is a failure row unless `confirmed_now` is true (every re-check confirmed it). `recheck_findings` lists
what else the re-checks judged; an `open` finding is a failure row, and a `resolution` says how a finding was fixed.

Accepted variants: `tests` or `test_counts` for `suites`; metrics as an object (`{"hit@1": "12 of 22"}`); verdicts
as a list (`[{"pass": "A", "verdict": "match", "note": ""}]`). A verdict of match, matches, pass, passed, ok,
agree(s/d), confirmed, verified, correct or true counts as agreeing; any other word is shown as a disagreement. The
"does not check" list and the line about two automated passes come from `src/content/pages.ts`, not from the file.
`tests/fixtures/accuracy.sample.json` is a complete example.

## Commands

```sh
npm install
npm run dev            # local dev server (nothing live switched on)
npm run data           # regenerate public/data from ../src/evidenceline/data
npm run data:check     # fail if public/data is out of date
npm run lint           # oxlint (correctness rules, React hooks, jsx-a11y)
npm run test:node      # node --test: the proxy (tests/functions.test.mjs) and the Worker (tests/worker.test.mjs)
npm run build          # tsc -b (strict) + vite build into dist/, everything on (.env.production)
npm run build:launch   # the launch build into dist/: repository link on, question box and connector "coming soon"
npm run build:offline  # every live feature off (mode offline)
npm run verify:launch  # dist/build.json says dist holds the launch build (exit 1 otherwise); verify:full likewise
npm run smoke          # serve dist with vite preview and run tests/smoke.mjs in the local Chrome
npm run check          # data:check, lint, test:node, build, smoke, in that order
npm run test:turnstile # the question box with Cloudflare's real Turnstile script (needs the internet)
npm run worker:check   # wrangler deploy --dry-run: bundles the Worker and reads dist/, contacts no account
node tests/adversarial.mjs   # adversarial checks of the built site
node tests/adversarial_launch.mjs   # launch and full builds behind the real Worker in local wrangler dev (about a minute)
```

`tests/smoke.mjs` uses `puppeteer-core` with Chrome at `C:/Program Files/Google/Chrome/Application/chrome.exe`
(override with `CHROME_PATH`). It saves desktop and phone screenshots of every page to `tests/screens/`. It treats
`dist/` as the production build (same-origin `/api/ask`, the `/mcp` link and the repository from `.env.production`)
and makes three more builds under `node_modules/.tmp/`: offline (nothing live is switched on), launch (the same
`buildLaunch()` as `npm run build:launch`: the box and the connector card say "soon", on a desktop and a 390 px
phone), and live (the `VITE_` settings pointing at test values, with Turnstile on Cloudflare's always-pass test key
`1x00000000000000000000AA` and its script replaced by a local stand-in). It mocks the question service and the data
files with request interception (`tests/fixtures/`), so it never uses the internet. `tests/turnstile.mjs` is the one
test that does: it loads the real Turnstile script on the same test key and checks the widget, the token sent with a
question, the keyboard path and a 390 px phone.

## Layout

```
src/content/    typed page copy (landing.ts, pages.ts, site.ts, types.ts)
src/landing/    landing sections (Hero, FlowCard, Jobs, Previews, Walkthrough, StepVisuals, Decision, TrustBand, TryIt, About)
src/pages/      one component per route (Landing, SampleSite, Tidy, Sources, Accuracy, NotFound)
src/components/ shared pieces (Header, Footer, Link, Term, RichText, TechDetail, Icon, PageShell)
src/lib/        router (history API, no library), glossary state, data loading with runtime shape checks, formatting,
                config (VITE_ settings, read by settings.ts), answers (prepared and live), accuracy (results reader), tidyPlain (plain lines),
                turnstile (the "not a robot" check)
src/styles/     landing.css (the design's tokens and rules, unchanged apart from two phone-width fixes), pages.css
public/         img/ (photos + credits.md), data/, robots.txt, _headers, favicon.svg
worker/         the Cloudflare Worker: index.js (the entry, default export only), routing.js (paths, noindex)
functions/_lib/ proxy.js, the one proxy to the API service (used by the Worker; plain functions, tested in Node)
scripts/        build-data.mjs (npm run data), build-launch.mjs (npm run build:launch), verify-build.mjs (verify:*)
wrangler.jsonc  the Worker's settings: account, custom domain route, static assets, API_ORIGIN
```

## Deploying (Cloudflare Worker)

The site is one Cloudflare Worker with static assets (`wrangler.jsonc`), at its custom domain
evidenceline.autopilotyourworkflow.com; steps and rollback are in `../DEPLOY.md`.

- **Static files** in `dist/` are served by Cloudflare directly, with `_headers` applied. Routes are client-side, so
  `not_found_handling` is `single-page-application`: an unknown path gets `index.html`, which shows its own "not
  found" view.
- **`/api`, `/api/*`, `/mcp` and `/mcp/*`** run the Worker first (`assets.run_worker_first`), which passes them to
  `functions/_lib/proxy.js`. The proxy forwards to `API_ORIGIN` (a plain setting in `wrangler.jsonc`, empty until the
  API service exists) with the visitor's address and, when the Worker has the `PROXY_SHARED_SECRET` secret, the
  shared value the API checks. While `API_ORIGIN` is empty it answers 503 `{"detail": "The live service is not
  connected yet. Please try again later."}`.
- **Not indexed:** every page has `<meta name="robots" content="noindex, nofollow">`, `robots.txt` disallows
  everything, `_headers` sends `X-Robots-Tag: noindex, nofollow` on every static file, and the proxy and the Worker
  add the same header to every response they make (Cloudflare does not apply `_headers` to those).
- **Pinned account:** `account_id` is the Autopilot account, so a deploy with any other login fails. There is no
  `workers.dev` or preview address.
- **Checked locally without an account:** `npm run worker:check` (a dry run that bundles the Worker) and
  `npx wrangler dev` (the Worker and `dist/` on this computer; `/api/ask` answers the 503 above).

`web/functions/` is no longer deployed as Pages Functions; only `functions/_lib/proxy.js` is used, by the Worker.
