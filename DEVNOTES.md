# Evidenceline dev notes

Evidenceline is an MCP server with nine read-only tools for PFAS monitoring work at one fictional Western Australian
site, FDS-01: tidy messy lab files, screen groundwater results under both drinking-water rules, search the public
guidance, and write and check report text. Every lab value is synthetic; every guideline value is real and cited to
its document, table and page. Nothing in the MCP server calls a language model.

A small web API (`src/evidenceline/api/`) serves the website's live question box and a hosted, read-only copy of the
same nine tools. The question box is the only place a model is called, and its answers are checked in code before
anyone sees them (see "Answer pipeline").

## Set up

Python 3.12 or later. Everything installs into a project-local virtual environment.

```sh
python -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip        # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m pip install -e . --group dev     # needs pip 25.1+ for --group
```

Dependencies in `pyproject.toml`:

- runtime: `mcp` 2.2+ and `pydantic` 2.11+. That is all the stdio MCP server needs; `tests/test_packaging.py` checks
  that importing it loads neither `fastapi` nor `anthropic`.
- `api` (an optional extra, and a dependency group with the same list): `fastapi` 0.141+, `uvicorn` 0.53+ and
  `anthropic` 1.8+, for the web API only. On a host: `pip install ".[api]"`; in a checkout: `--group api`.
- `corpus`: `pypdf` 6.19+ and `pdfplumber` 0.11.10+, only for building the guidance search index.
- `dev`: the `corpus` and `api` groups plus pytest, anyio, ruff and pyright (pyright type-checks the PDF extraction
  and web code, so it needs those packages installed).

With uv instead (uv is not installed on this machine, so this path is untested): `uv sync`.

## Fetch the guidance corpus and build the search index

`search_guidelines` reads a local SQLite index built from the public documents. The documents are never stored in
the repository or the package; they live in `.cache/corpus/` (git-ignored), or in `$EVIDENCELINE_CORPUS_DIR`.

```sh
.venv/Scripts/python scripts/fetch_corpus.py        # download what is missing, check SHA-256 against the manifest
.venv/Scripts/python scripts/build_index.py         # extract text, cut passages, write .cache/corpus/guidance.sqlite
.venv/Scripts/python -m evidenceline.guidance.evaluate             # tuning set 1: the golden questions
.venv/Scripts/python -m evidenceline.guidance.evaluate --heldout   # tuning set 2: held-out set 1 (used for tuning)
.venv/Scripts/python -m evidenceline.guidance.evaluate --heldout2  # held-out set 2 (never tuned on: the fair score)
```

`fetch_corpus.py` also takes `--force`, `--update-manifest` (record SHA-256 and retrieval date), `--timeout` and
`--only <ids>`. The document list, licences and checksums are in `src/evidenceline/data/corpus_manifest.json`.
Indexed on 2026-09-24: ASC NEPM Schedule B1, PFAS NEMP 3.0 (pinned Wayback copy), the two DWER contaminated sites
guidelines (2021 assessment and management; February 2025 identification, reporting and classification) and the
ADWG PFAS fact sheet. The index (format version 2) holds 1,060 passages of 150 to 300 words, each within one page,
cut at section headings, with its PDF page, printed page, nearest heading and heading path; about 8 ms per query and
2.6 MB. An index in the old format gives a plain "rebuild it" error. PFAS NEMP 3.1 could not be fetched (the host
timed out and there is no archived copy); it is listed as unavailable, never indexed, and the fetch script retries
it every run. (Verification pass B later downloaded it directly by sending browser Accept, Accept-Language and
Sec-Fetch headers; the fetch script does not send those yet.)

Search scores at k = 8 (the passages the question box reads), measured 2026-09-24 after the casual-question fix.
Held-out set 2 was scored once more after the launch-round fixes (other states and countries, instructions to the
system, the NEMP edition note), with nothing tuned on it: every line of the output was the same.

| Question set | hit@1 | hit@5 | hit@8 | recall@8 | "Not covered" when it should |
|---|---|---|---|---|---|
| **Held-out set 2** (24 in scope, 6 out; never used for tuning; first run) | **12/24** | **12/24** | **12/24** | **0.46** | **5/6** |
| Held-out set 2, casual questions only (c01 to c12) | 4/12 | 4/12 | 4/12 | 0.29 | |
| Held-out set 2, practitioner questions only (p01 to p12) | 8/12 | 8/12 | 8/12 | 0.62 | |
| Held-out set 2 without its seven near copies (20 in, 3 out) | 9/20 | 9/20 | 9/20 | 0.40 | 2/3 |
| Same, casual questions only (10) | 3/10 | 3/10 | 3/10 | 0.25 | |
| Golden, original 29 in / 9 out, before the fix | 21/29 | 27/29 | 29/29 | 0.80 | 9/9 |
| Same questions, after | 21/29 | 28/29 | 29/29 | 0.81 | 9/9 |
| Golden with the new casual group (35 in, 15 out), after | 26/35 | 34/35 | 35/35 | 0.79 | 15/15 |
| Held-out set 1, first run, before the fix | 6/24 | 7/24 | 7/24 | 0.28 | 6/6 |
| Held-out set 1, after (now a tuning set) | 17/24 | 20/24 | 20/24 | 0.83 | 6/6 |

**Held-out set 2 is the only fair score.** Held-out set 1 (`evals/guidance_heldout.json`, method in
`evals/HELDOUT.md`) was written from the documents alone, and its first run was honest: 13 of its 17 misses were a
wrong "not covered" on chatty wording. The fix was then tuned on it together with the golden set, so it now flatters
the search as much as the golden set does. Held-out set 2 (`evals/guidance_heldout2.json`, method in
`evals/HELDOUT2.md`) was written afterwards without seeing the search, the other sets or any results, and scored
once, above, with nothing tuned on it. It shows the fix only partly carried over: casual questions are still the
main weakness (8 of the 12 misses; 7 of those are a wrong "not covered"), and one out-of-scope question
(microplastic reporting) returns passages instead of "not covered". Its misses: c01 to c05, c08, c10, c12, p01, p04,
p05, p11 and x04. Seven of its questions turned out close in wording to a tuning question (held-out id ~ tuning
id): c02 ~ h21, c09 ~ g13, p06 ~ h01, p07 ~ h05, x03 ~ x03, x05 ~ o04 and x06 ~ x06. p07 is almost word for word; the
others share the main words (content words only, Jaccard at least 0.40, or 0.25 with the same expected page, or 0.30
when both are out of scope; `evals/HELDOUT2.md` lists each). Three of them (p06, c09, p07) are first-place hits and
three (x03, x05, x06) are correct "not covered" answers, so the whole-set score flatters the search a little. They are
kept and scored as written; the Accuracy page names all seven and also shows the same run without them (the rows
above). `HELDOUT2_NEAR_COPIES` in `scripts/build_accuracy.py` lists them, and a test in
`tests/test_accuracy_rechecks.py` (and case A3 in `web/tests/adversarial_launch.mjs`) fails if the check finds a pair
the list does not name, or the list names one the check no longer finds. Its ids c01 to c12 happen to repeat the
golden set's new casual ids; the files are separate, so nothing mixes them. Anything tuned on held-out set 2 turns it
into a tuning set too: a new held-out set would then be needed. CI does not run `evaluate --heldout2` as its own
step; `build_accuracy.py` scores the set when it builds the Accuracy data.

Counting the near-miss passages the question box hands to the model (below), golden is 35/35 and held-out set 1
21/24; held-out set 2 stays at 12/24, and 1 of its 6 out-of-scope questions reaches the model (x04, through its
passages). One golden out-of-scope question reaches the model this way, "What noise limits apply to drilling rigs
working at night?" (o03): its search result is still "not covered", and the model has to reply NOT_COVERED.

Held-out scoring follows `evals/HELDOUT.md`: a hit is a passage from the expected document on an expected PDF page;
the ADWG fact sheet (a web page) is scored on its section, and a subsection counts ("Health considerations > GenX
Chemicals" is in "Health considerations"). The golden set keeps its exact section match.

Without an index the server still starts; `search_guidelines` then returns a tool error that says how to build it,
and the golden tests skip.

## Run the tests and checks

```sh
.venv/Scripts/python -m pytest
.venv/Scripts/python -m ruff check src tests scripts
.venv/Scripts/python -m ruff format --check src tests scripts
.venv/Scripts/python -m pyright                          # strict; covers src, tests and scripts
.venv/Scripts/python scripts/export_web_data.py --check  # the website's data files match the package
.venv/Scripts/python scripts/build_accuracy.py           # run the tests and searches once; write the Accuracy data
.venv/Scripts/python scripts/prepublish_check.py         # nothing private, no secret, no dash in what would be pushed
cd web && npm run check                                  # website: data check, lint, the proxy and Worker tests,
                                                         # production build, browser smoke test (213 checks)
cd web && node --test tests/*.test.mjs                   # the proxy (functions) and the Cloudflare Worker (worker)
cd web && node tests/adversarial.mjs                     # the independent tester's website cases (67)
cd web && node tests/adversarial_launch.mjs              # the launch tester's cases, behind local wrangler dev (31)
cd web && npm run worker:check                           # wrangler deploy --dry-run: no account contact
cd web && npm run verify:launch                          # or verify:full: which build dist/ holds (dist/build.json)
```

Test files:

- `test_server.py`: the real server through the SDK's own `Client`, once in process and twice as a subprocess over
  stdio (the stateless 2026-07-28 protocol and the older initialize handshake), plus once through the installed
  `evidenceline-mcp` script. It lists every tool and calls every tool, including the drafting flow the model is told
  to follow (fill_numbers on a template, then check_paragraph on the result: every item consistent). It also checks
  that every tool rejects unknown arguments and that no description contains a dash character.
- `test_server_redaction.py`: the redaction guard rail over stdio. The fictional client name and site address are
  read from the FDS-01 field sheet header, pushed into every text argument of every tool (and into a rejected
  argument, whose validation error quotes it), and must never appear in any output. Also: one audit line per call
  with counts and no values, a result-side leak is redacted, and a missing identifier file stops every tool.
- `test_tidy*.py`, `test_guidance_*.py`, `test_drafting.py`, `test_redact.py`: each module on its own.
- `test_adversarial_phase1.py`: an independent tester's cases for tidy, fill_numbers, search and redaction, through
  direct calls, a real stdio subprocess and the in-process client. Every case passes; none is marked `xfail`.
- `test_tools.py`, `test_checker.py`, `test_adversarial.py`, `test_textparse.py`, `test_dataset.py`,
  `test_units.py`: the original four tools and their parts.
- `test_export_web_data.py`: runs `scripts/export_web_data.py --check`.
- `test_answer.py`, `test_answer_verify.py`, `test_answer_precompute.py`: the answer pipeline with a fake model, the
  verifier's rules one by one, and the prepared answers in `web/public/data/answers.json`.
- `test_api.py`, `test_api_proxy.py`: the web API through FastAPI's test client (limits, Turnstile, CORS, question
  text never logged, the proxy secret), and the hosted MCP connector through the SDK's own Streamable HTTP client,
  in memory and against a real uvicorn server on a local port.
- `test_answer_guards.py`, `test_api_gate.py`: the answer guards added after the second test round (units in words,
  a value tied to its own rule, verdict and rule-picking paraphrases, name redaction, dash look-alikes) and the
  API's body cap and proxy-secret order.
- `test_adversarial_phase2_api.py`, `test_adversarial_phase2_tools.py`: the second independent tester's cases for
  the question box and API, and for the tools, the hosted connector through the real proxy, the pre-publish scan
  and search. Every case passes; none is marked `xfail`.
- `test_phase2_fixes.py`: regression tests for the tools fixes from that round (NEMP 3.0 footnote a, the hosted
  redaction note, the held-out loader, the token rules of the scan).
- `test_accuracy_build.py`: the verification summary, the held-out scorer and `scripts/build_accuracy.py`
  (including that every test file has a named area and that an expected failure is published as a failure).
- `test_accuracy_rechecks.py`: the notes re-checks in the summary (a note is confirmed only on the wording the file
  holds now; a re-check mismatch is counted and never changes the data), the lookup_limit fix re-check A asked for,
  held-out set 2 (its loader, groups and the one known near copy) and how the Accuracy data shows them.
- `test_adversarial_launch_api.py`, `test_adversarial_launch_search.py`: the launch tester's cases for the web API,
  hosted connector and redaction (names glued to other text, URL-encoded, split words, odd Turnstile replies, unknown
  prompt and resource names), and for search scope, the question box's routing and wording, the prepared answers and
  the Accuracy data. None is marked `xfail` now.
- `test_guidance_scope.py`: other states (EPA Vic, ACT, Tas EPA, NT) and other countries' rules, instructions to the
  system, and the ordinary questions that share their words and must still be searched.
- `test_answer_plain_wording.py`: the two writing limits checked in code (short sentences, no long quotes) and the
  wording of a withheld near-miss answer.
- `test_guidance_casual.py`: casual questions, the unknown-word rule, framing phrases, the plain-language synonyms,
  price questions and the borderline band, with a fake model for the question box's near-miss path.
- `test_packaging.py`: runtime dependencies, the `api` extra and group, and that the stdio server does not load the
  web packages.

`tests/conftest.py` points `HOME`, `USERPROFILE` and the redaction audit log at a temporary folder for every test, so
no test reads a real identifier file or writes to the real `~/.evidenceline`.

## Run the server

```sh
.venv/Scripts/evidenceline-mcp          # console script, stdio
.venv/Scripts/python -m evidenceline    # same thing
```

Claude Code or Claude Desktop config (use the absolute path of the script):

```json
{ "mcpServers": { "evidenceline": { "command": "C:/path/to/evidenceline/.venv/Scripts/evidenceline-mcp.exe" } } }
```

Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `EVIDENCELINE_REDACT` | `~/.evidenceline/redact.toml` | Identifier file for redaction, or `builtin:fds01-demo` for the packaged demo file (the fictional client and address). If set, the file must exist, or every tool fails closed; an unknown built-in name fails closed too. |
| `EVIDENCELINE_REDACT_LOG` | `~/.evidenceline/redaction-audit.jsonl` | Local audit log: time, tool and counts per placeholder type, never values. |
| `EVIDENCELINE_CORPUS_DIR` | `.cache/corpus` in the checkout | Downloads, extracted text and the search index. |

The web API also reads these (see "Web API and hosted connector"):

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | The live question box's key. Unset: every question gets the passages only, with a note that live answers are off. |
| `EVIDENCELINE_MODEL` | `claude-sonnet-5` | The model for live answers (effort medium). |
| `EVIDENCELINE_ALLOWED_ORIGINS` | the site, plus localhost and 127.0.0.1 on 5173 and 4173 | CORS origins, comma separated. |
| `EVIDENCELINE_ASK_PER_HOUR`, `EVIDENCELINE_ASK_PER_DAY` | 10, 30 | Questions per IP per rolling hour and per rolling 24 hours. |
| `EVIDENCELINE_ANSWERS_PER_DAY` | 300 | Model calls across everyone per rolling 24 hours; beyond it answers pause and passages are still shown. |
| `EVIDENCELINE_MCP_PER_HOUR` | 600 | Requests per IP per hour on `/mcp`. |
| `EVIDENCELINE_CLIENT_IP_HEADER` | none (the socket address) | Header holding the caller's IP behind a proxy; for `x-forwarded-for` the right-most entry is used. |
| `EVIDENCELINE_PROXY_SECRET` | none | When set, `/api/ask` and `/mcp` serve only requests that carry it in `x-evidenceline-proxy-secret` (the site's Cloudflare Worker adds it), so the client IP header can be trusted. `/api/health` stays open. |
| `TURNSTILE_SECRET` | none | Cloudflare Turnstile secret. The website shows its optional widget only when built with `VITE_TURNSTILE_SITE_KEY`; set the secret only after that build is live (DEPLOY.md gives the order), or every question gets a 403. |
| `HOST`, `PORT` | 127.0.0.1, 8000 | Where `python -m evidenceline.api` listens; on a host set `HOST=0.0.0.0`. |
| `EVIDENCELINE_CLAUDE_CLI` | a VS Code copy under `~/.vscode/extensions`, else `claude` on the PATH | The Claude Code CLI, used only by `scripts/precompute_answers.py`. |

## Tools

| Tool | Title | Inputs | Returns |
|---|---|---|---|
| `tidy_lab_files` | Tidy lab results | `site` (default `FDS-01`), `include_rows` (default false) | The lab file, chain of custody and field sheet combined into 73 rows with their row numbers (the rows only with `include_rows=true`; by default `row_count` and a closing note say how to get them, which keeps the result near 35 KB instead of 60 KB); six numbered review items (sample ids, units, duplicate RPD, holding times, blanks, LOR against criteria), each with the evidence rows, the rule, its quoted source and what the scientist decides; everything checked and not flagged; what was not checked. |
| `get_review_item` | Get one review item | `site`, `number` (1 to 6) | One review item with every evidence line quoted exactly from its file. |
| `get_results` | Get lab results | `well`, optional `analyte` | Well MB2's results over four rounds, each with its lab report, file and row. |
| `lookup_limit` | Look up a guideline limit | `analyte`, `rule` (`nemp-3.0` or `current`) | One drinking-water value with document, table, page and WA status. `applies_to` says what the rule sets the value for (one analyte or the sum); `compared_quantity` says what to compare with it for the analyte asked about: under `nemp-3.0`, PFOS gives "PFOS on its own" (footnote a). |
| `compare_rules` | Compare both rules for one round | `well`, `date` | One round screened under both rules side by side, with the arithmetic. Never picks a rule. |
| `check_paragraph` | Check a report paragraph | `text`, `well` | Every number, change, guideline and detection claim traced or flagged; what was not checked, with reasons. |
| `fill_numbers` | Fill in the numbers | `text` with placeholders, `well` | The text with exact values put in by code, the same text with `[n]` source markers, and a numbered source list. All or nothing: one bad placeholder and nothing is filled. |
| `search_guidelines` | Ask the guidelines | `question` (up to 500 characters), `k` (1 to 10, default 5) | Passages with document, edition, WA status, PDF and printed page, section, a licence-limited excerpt and a link; or "not covered" with the reason. |
| `show_redactions` | Show redactions | none | Placeholders in use this session with counts, and the patterns and identifier types loaded. Never raw values. |

Placeholders for `fill_numbers`: `{PFOS|MB2|Sep 2025}` (a result), `{sum PFOS+PFHxS|MB2|2025-09-16}`,
`{lor|PFOA|MB2|Sep 2025}` (detection limit), `{limit|PFOS|current}` (the rule is required, no default; under
`nemp-3.0` only the table's own label `{limit|PFOS+PFHxS|nemp-3.0}` is accepted, and the sentence says whether PFOS
alone or the sum is compared) and
`{change|PFOS|MB2|Nov 2024|Sep 2025}` (for example "a fall of 0.003 ug/L (7.3%)").

The server's `INSTRUCTIONS` tell the model the order to use them in and the ground rules: never type a number, show
both rules and never pick one, equal is not above, a guideline value is an investigation level, never read a value
from a search excerpt, keep placeholders such as `[CLIENT-1]` as they are, run check_paragraph after drafting, and
never say "no issues".

## How tools are built (`tooling.py`)

Every tool is built by `tooling.guarded_tool(fn, title)`, not the SDK's `add_tool`:

- **Read-only annotations** on every tool.
- **Strict arguments.** The SDK's argument model ignores unknown keys. `forbid_extra_arguments` swaps in a subclass
  with `extra="forbid"`, so a typo such as `wel` is an error and the input schema says `additionalProperties: false`.
- **Redaction at the tool boundary.** `GuardedTool.run` redacts every argument, of any type, and every argument
  name before the tool runs (so echoed text carries placeholders and `fill_numbers` offsets match the text the model
  sees), then the result, then any error message, including the SDK's own validation errors, which quote the
  rejected input. One audit line per call. If the identifier file named in `EVIDENCELINE_REDACT` is missing, broken
  or the variable is set but empty, the tool returns an error and no output.
- **No echo of unknown names.** `GuardedServer` answers a call to a tool that does not exist with the list of
  tools, not the name it was sent, and before the SDK's handler would log that name. An unknown prompt or resource
  gets a fixed message ("Unknown prompt. This server has no prompts." or "Unknown resource. This server has no
  resources."), with no URI in the error data and no traceback in the log (the SDK's own messages repeat the name).
- **Whole-number arguments are strict.** `k` and `number` reject `true` instead of reading it as 1.
- Two results are not redacted, only their arguments and errors: `search_guidelines` quotes public documents
  verbatim (its only user text is the question, already redacted on the way in), and `show_redactions` holds
  placeholder names and pattern descriptions by construction (redacting it rewrote its own example "Lot 123" into
  a placeholder).
- `fill_numbers` is redacted around its filled values, never through them (`drafting.redact_filled`): each
  stretch of the caller's words is redacted on its own and the values go back between them, so "Lot " followed
  by "0.038 ug/L" is not read as a land lot and every offset still points at its value.

Each feature module keeps a `TOOL_SPECS` tuple of `(function, title)` and a `register_tools(server)` for use
outside this server; `server.build_tools()` builds from the specs.

## Redaction

See `src/evidenceline/redact.py` and `examples/redact.example.toml` (all values made up). Identifiers come from a
local TOML file (`[[client]]`, `[[site]]`, `[[person]]`, `[[address]]`, `[[lot]]`, `[[email]]`, `[[phone]]`, each
with `names = [...]`; every alias of one identity gets the same placeholder) and from built-in patterns for emails,
WA lots, street addresses ending in a suburb and "WA" (any letter case; "W.A." and "Western Australia" too; or
suburb first, "Welshpool WA 6106, 12 Example Road"), and Australian phone numbers. A listed name matches in any
letter case with any separator between its words or none ("Harbourline Logistics", "HARBOURLINE-LOGISTICS",
"harbourline_logistics", "harbourlinelogistics.com.au", "Harbourline%20Logistics"), with soft hyphens or
zero-width characters inside words, and when quoted with escapes such as `\n` or `\u200b`. A name that starts or
ends with a letter needs only no letter beside it, so digits, underscores and a change from lower case to a capital
count as edges ("DSI_Harbourline.pdf", "Harbourline2025", "dsiHarbourline"); one that starts with a digit must not
follow a digit ("112 Example Road" is not "12 Example Road"). The built-in demo file also lists "Harbour Line" and
every spelling of the fictional address (with and without "12", "Road" or "Rd", and each ending). Placeholders are
stable for
the session and numbered in reading order. The map back to raw values stays in memory and is only used by
`restore()` for local exports.

The packaged data holds no real identifiers. The FDS-01 field sheet names a fictional client and address in its
header; `tidy_lab_files` leaves both out of its output, and the redaction test proves they never reach the model even
when a call tries to make a tool echo them.

## Website data

`web/` is the Vite + React site (see `web/README.md`). Its data files in `web/public/data/` come from the package:

```sh
.venv/Scripts/python scripts/export_web_data.py          # write sample-site.json, sources.json, tidy.json, search-example.json
.venv/Scripts/python scripts/export_web_data.py --check  # exit 1 if any is out of date
```

`sample-site.json` and `sources.json` are byte for byte what `web/scripts/build-data.mjs` (`npm run data`) writes;
both generators exist, and both checks run. `tidy.json` is a short `tidy_lab_files` result (no row table).
`search-example.json` holds three `search_guidelines` answers and is only rewritten when the local index exists.
The script fails if a redaction pattern or a field-sheet identifier appears in anything it would write.
`soil-criteria.json` is written only by `web/scripts/build-data.mjs` (`npm run data`). `answers.json` holds the
question box's prepared answers, written by `scripts/precompute_answers.py` (see "Answer pipeline").
`accuracy.json` is written by `scripts/build_accuracy.py` (see "Accuracy page data").

## Web API and hosted connector

`src/evidenceline/api/`: `app.py` (FastAPI), `settings.py`, `gate.py` (proxy secret and body cap before any body is
parsed), `limits.py` (in-memory rolling windows) and `turnstile.py`. Run it with `python -m evidenceline.api`, the `evidenceline-api` script, or
`uvicorn --factory evidenceline.api.app:create_app`. It needs the `api` extra and the guidance index.

- `GET /api/health`: status, whether live answers are on, the model, whether the index exists, Turnstile, whether
  only proxied requests are served, where the client IP comes from (`client_ip_from`), the body cap
  (`request_bytes`) and the limits.
- Every `/api/*` and `/mcp` request: the proxy secret (if set, 403), then a 64 KB body cap (413; a body sent without
  a length is read only up to the cap), before any body is parsed. The proxy (`web/functions/_lib/proxy.js`) sends
  the same header name and has the same cap; a test compares both.
- `POST /api/ask` with `{"question": "..."}`: the answer result. Order of checks after the gate: an empty question
  or one over 500 characters (400), per-IP limits (429 with `Retry-After`), then Turnstile (if on, 403; a reply from
  Cloudflare that is not a JSON object counts as not verified), then the pipeline in a worker thread. The global daily brake pauses live answers but still returns
  passages. Question text is never logged; the log has status counts only.
- `/mcp`: the nine tools from `server.build_tools()` over Streamable HTTP, stateless, with JSON responses and the
  same guard rails as stdio (read-only, strict arguments, redaction). The instructions add that this is the hosted
  read-only copy. DNS-rebinding protection is off: it protects servers on localhost, and this one is public,
  read-only and holds no credentials. The host loads `EVIDENCELINE_REDACT=builtin:fds01-demo` (set in
  `render.yaml`), which lists only the fictional client name and site address; any other client, site or people's
  names are not redacted there, and the hosted note in the instructions and `show_redactions` both say so.
- A client IP header other than `x-forwarded-for` with no proxy secret could be faked by any caller, so `from_env`
  logs a warning and ignores it (limits then go by the connecting address).

Going live (decided 2026-09-24; nothing here deploys anything; steps in DEPLOY.md): the site runs as a Cloudflare
Worker with static assets at https://evidenceline.autopilotyourworkflow.com (`web/worker/`, `web/wrangler.jsonc`;
the custom domain comes from wrangler's `custom_domain` route). The Worker forwards `/api/*` and `/mcp` through
`web/functions/_lib/proxy.js` to a Render web service (Singapore, starter). It launches in two stages: first the site
alone (`npm run build:launch`), with the question box and the connector card saying "coming soon" and `/api/*` and
`/mcp` answering a friendly 503; then, once the Render service and an Anthropic key exist, `API_ORIGIN` and the
Worker secret are set and the site is rebuilt with `npm run build`. The connector URL is https://evidenceline.autopilotyourworkflow.com/mcp and the
browser calls the API on its own origin, so production needs no CORS. Behind that proxy every request reaches Render
from Cloudflare, so the per-IP limits need the proxy to pass the visitor's IP in the header named by
`EVIDENCELINE_CLIENT_IP_HEADER`, and `EVIDENCELINE_PROXY_SECRET` makes sure only the proxy can set it. The Render
service also needs the guidance index built at deploy time (`fetch_corpus.py` and `build_index.py`, `corpus` group).

## Answer pipeline (the question box)

`src/evidenceline/answer/`: `answer(question, client, *, index_path=None)` never raises for bad input.

1. Redact the question with the built-in patterns (a fresh redactor per question), plus company and site names found
   by shape (`names.py`): capitalised words ending in a company word ("Harbourline Logistics Pty Ltd", "Acme
   Mining") or a place word ("Kwinana Terminal"), and a name the question calls a client ("my client Redgum"). The
   result counts replacements.
2. A PFAS drinking-water value question (PFOS, PFHxS, PFOA, PFBS or the sum) gets BOTH rules' values from
   `core.lookup_limit`; a rule with no value is listed as "no value". Soil or fresh-water questions get no values
   and a note saying why.
3. Search for up to 8 passages. "Not covered" returns without a model call, except a borderline one: when the
   search still says "not covered" but its closest passages are a near miss (coverage of at least 0.30, at least 3
   matched terms and at least half of the question's terms; `BORDERLINE_COVERAGE`, `BORDERLINE_MATCHED` in
   `guidance/search.py`), live answers are on and the question is not a verdict question, those passages go to the
   model with `BORDERLINE_INSTRUCTION`: reply NOT_COVERED unless they answer the question. The verifier and the
   NOT_COVERED path apply unchanged. Questions that name something no document mentions, other states' rules, prices
   and instructions to the system carry no near-miss passages. A verdict question ("is this site
   contaminated", "safe to drink", "OK to drink", "a problem", "can my kids swim"; the soft words count only after
   the site, water or result is named, so "Is it OK to composite samples?" still goes to the model) returns
   `guard_rail`: a fixed reply plus the passages, no model call. Without a
   model client the result is `passages_only` with "Live answers are switched off".
4. The model reads the whole indexed page text for each passage (from `chunks` in the index, dashes tidied), or the
   excerpt if the page cannot be read.
5. The answer is checked in code (`verify.py`). If it fails, the model is asked once more, told only which checks
   failed (never shown its own answer), and the second answer goes through every check again; the explanation says
   it was the second. A second failure withholds it and returns `passages_only`. A withheld answer is never shown,
   even in part: the explanation names only the failed checks, sentence numbers, the failing number or a dash's
   code point.

Verifier rules, all deterministic: every `[n]` and `[Gn]` citation exists and there is at least one; every sentence
is cited (sentences split at every line break and after any letter or digit, with abbreviations such as "Fig." and
single initials kept whole); every number appears in a cited passage or in the given guideline values (units
normalised in symbols and words: ug/L, µg/L, μg/L, ng/L, mg/L, "micrograms per litre", "ng/litre", "ug L-1", ppt
and ppb; a mass with no volume is never traced; percentages only match percentages; dotted section numbers match
exactly); a concentration must equal the value of a `[G]` marker cited in its own sentence, and when the words
before it name a rule, a marker of that rule, never a number taken from passage text; when values were given, every
one is stated next to its own marker, with no wording that picks a rule ("rely on", "takes precedence",
"supersede", "in force", "outdated" and similar); no verdict wording, paraphrases included ("fine to drink", "poses
no health risk", "considered safe", "is therefore contaminated"), except inside a condition ("if they suspect it is
contaminated") or a category word ("clean fill"); no em or en dashes or their look-alikes (U+2015, figure dash,
two- and three-em dashes); "short sentences": every sentence under 30 words, citations not counted
(`MAX_SENTENCE_WORDS` in `prompt.py`); "no long quotes": no sentence repeats more than ten words in a row from a
passage (`MAX_QUOTED_WORDS`). The last two put the prompt's own writing limits into code; a failure names the
sentence and passage numbers, never the words, and triggers the one retry.

Routing before any model call: a request to state a verdict ("write that the site is contaminated", "confirm in
writing that the water is safe"; the verb must be followed by "that") gets the fixed guard-rail reply. Other states
(also EPA Vic, ACT, Tas EPA and NT; "ACT" and "NT" only in capitals, and "ACT" not when the whole question is in
capitals), other countries' rules (a country with a rules word such as rule, limit, ban or allow) and instructions to
the system ("SYSTEM:", "Assistant:" or "Override:" opening a sentence, "new instruction", "from now on you",
"answer without citations", "stop citing", "pretend you are") are "not covered" with no near-miss passages. A PFAS
NEMP question with no edition gets a note that says so, that 3.1 is not indexed and that only 3.0 was searched. A
withheld near-miss answer's explanation says its passages were the search's near miss, not a match.

Clients: `AnthropicClient` (official SDK; key and model from the environment; effort medium; a 402, billing error,
spend-cap message or 429 becomes `paused`, other failures `error`), `ClaudeCliClient` (the local Claude Code CLI,
headless, every tool off, `--safe-mode`, in an empty temporary folder; only for precomputing) and `FakeClient`
(tests). `scripts/precompute_answers.py` writes `web/public/data/answers.json` through the same pipeline; each entry
carries its check record, date and model. Rebuilt on 2026-09-24 with Claude Sonnet 5 after the NEMP 3.0 fix: 4
answered (all pass every check), 1 guard rail and 1 not covered. `--only N` reruns question N alone and keeps every
other entry byte for byte; it refuses when the stored file used another model or system prompt. After the launch
round, question 4 (reporting to DWER) was rerun this way for the new writing checks, and question 1 (the PFOS limit)
for the compared-quantity fix below; each claim was read against its cited page. Rerun it whenever the search, the index or the
guideline notes change, or its stored passages go stale (a test compares them with the live pipeline).

## Guideline verification

Every value in `guidelines.json` and `fds01_site/soil_criteria.json` was checked on 2026-09-24 by two independent
automated passes (`src/evidenceline/data/verification/pass-a.json` and `pass-b.json`, each with a `.md` note). Each
pass read the primary sources itself and never read the other pass. `evidenceline.verification` pairs them and
writes `verification/summary.json` (regenerated by `scripts/build_accuracy.py`; a test fails if it is stale). A value
and its explanatory note are judged separately. Result: all 16 values (number, unit, scenario, table and page) and
all 5 document and WA-status statements are confirmed by both passes. Two notes were confirmed by neither pass, and
both were reworded:

- **NEMP 3.0, sum of PFOS and PFHxS (0.07 ug/L).** The old note said "This rule has no separate value for PFOS or
  PFHxS on its own." NEMP 3.0 Table 4, footnote a says: "Where the criteria refer to the sum of PFOS and PFHxS, this
  means concentrations of PFOS only, PFHxS only, and the sum of the two." Both passes: mismatch. The note now reads
  "Applies to PFOS on its own, PFHxS on its own, and the sum of the two (Table 4, footnote a). Compare each of the
  three with 0.07 ug/L." `screening.py` (`FOOTNOTE_A`, `screen_member`, `screens_for_rule`), `core.py`,
  `checker.py`, `drafting.py`, `tidy/criteria.py`, `scripts/expected_tidy.py`, `server.py`, the website and
  `answers.json` follow it. One checker verdict changed: "In March 2024, PFOS exceeded the drinking-water guideline"
  (PFOS 0.062, sum 0.083 ug/L) used to pass under both rules and is now "depends on rule" (above the current 0.008,
  below NEMP 3.0's 0.07 for PFOS alone).
- **Soil HIL A, arsenic.** The note said "Total arsenic"; Schedule B1 Table 1A(1) reads just "Arsenic" (the 70%
  bioavailability part is confirmed). Both passes: could not verify. The owner's assistant removed "Total": the
  note now reads "Arsenic (as the table row reads); the value assumes 70% oral bioavailability (Table 1A(1), note 2)."

`summary.json` keeps the wording the passes judged as `checked_note` and counts `notes_changed_since_verification`
(2).

**Notes re-checks.** Two later independent re-checks (`verification/notes-recheck-a.json` and
`notes-recheck-b.json`, each with a `.md` note) downloaded NEMP 3.0 (the pinned archived copy; the official host timed
out) and Schedule B1 again and judged the reworded notes, without reading each other or the first passes. Both
confirmed both notes, word for word against the wording the files hold now. `evidenceline.verification` reads every
`notes-recheck-*.json`, maps each note by its `location`, and sets `confirmed_by_every_recheck` only when every
re-check judged the current wording and confirmed it; a later rewording, or a re-check that judged older wording,
turns it off again. A re-check mismatch is counted (`recheck_mismatches`, `recheck_mismatches_open`), listed on the
Accuracy page and printed as a WARNING by `build_accuracy.py`; it never changes the data. Everything else the
re-checks judged is listed as a finding:

- **Soil HIL A, sum of PFOS and PFHxS (a related note):** could not verify in both re-checks. The NEMP 3.0 half is
  confirmed (same footnote, same 0.003 mg/kg); the note cites NEMP 3.1, which neither re-check could download. Both
  first passes confirmed it, and it has not changed since. It stays an open row on the Accuracy page.
- **The NEMP 3.0 sentence in the server's instructions and the lookup_limit description:** confirmed by both. The
  summary also checks that the sentences they judged are still in `server.py` (`judged_wording_unchanged`).
- **What lookup_limit returned (re-check A: mismatch).** Asked for PFOS or PFHxS under `nemp-3.0`, it said
  `compared_quantity: "sum of PFOS and PFHxS"`, although footnote a applies the value to each on its own. Real, and
  fixed: `compared_quantity` now reads "PFOS on its own" (or "PFHxS on its own"), `applies_to` stays "sum" (what the
  rule sets the value for) with a field description that says the value also applies to each member, and the
  tool description no longer promises an either/or answer. The question box now uses the same compared quantity
  ("PFOS on its own", from `lookup_limit`), with a note that the rule sets the value for the sum and it also applies
  to PFOS on its own; the prepared PFOS answer was rerun to match. The fix is
  recorded in `verification.RESOLUTIONS`, which a test checks against the running code; the re-check's verdict stays
  as it recorded it.

Minor points the re-checks raised, left as they are because they are not errors: the arsenic note leaves out the
second sentence of note 2 (site-specific bioavailability may matter, see Schedule B7), and note 2 sits on printed
page 49 while the criterion's page field gives page 48, the row's page. Changing the data would need another re-check.

These are automated passes, not a review by a practitioner, and the Accuracy page says so.

## Accuracy page data

`scripts/build_accuracy.py` runs the whole pytest suite once (JUnit report; `--junit file.xml` reuses one from CI),
scores the search sets (left out, with a message, when there is no index), rebuilds the verification summary, and
writes `web/public/data/accuracy.json`: tests by area with every failure named; held-out set 2 first as "the fair
score", then its casual questions alone (`part_of`, the same run, misses listed once with the whole set), then the
two tuning sets labelled as such (golden, and held-out set 1 with its first-run score in the note), each with every
miss listed; each guideline value with both passes' verdicts; the reworded notes with the first passes' verdicts on
the old wording and the re-checks' on the new (`confirmed_now`); the re-checks' other findings (`open`,
`resolution`); the date; and "local build" or "commit <sha>" from `GITHUB_SHA`. It writes the file even when a test fails (the page shows failures) and
then exits 1, and it refuses to write an em or en dash. `--summary` and `--check-summary` handle `summary.json` only.

## Website build settings

Launch mode: `npm run build:launch` (`web/scripts/build-launch.mjs`) is the production build with the live
settings cleared except the GitHub link, so the question box and the connector card say "coming soon"; DEPLOY.md
stage 1 publishes it, and stage 2 switches to `npm run build` once the API exists. The "Try it" lead names only the
features the build switched on. Every build writes `dist/build.json` (its kind: full, launch, offline or custom, and
which features are on; never a key or an address), and `npm run verify:launch` or `verify:full`
(`web/scripts/verify-build.mjs`) exits 1 when `dist/` holds another kind. DEPLOY.md runs it before each deploy.

The proxy (`web/functions/_lib/proxy.js`) rewrites a redirect that points at the API's own host (any scheme or
port, or a relative one) into the same path on the site, so `POST /api/ask/` answers `307 location="/api/ask"`
instead of sending the visitor to the Render address. A redirect to another host passes through unchanged; one it
cannot read is dropped.

`web/.env.production` (read by `npm run build`, which runs in mode production) sets `VITE_GITHUB_URL` to
https://github.com/autopilotyourworkflow/evidenceline, `VITE_MCP_URL` to
https://evidenceline.autopilotyourworkflow.com/mcp and `VITE_API_BASE=/`. "/" means the site's own origin: the box
posts to `/api/ask`. An empty `VITE_API_BASE` still means "off", so do not set it to an empty string in the deploy
environment (a variable set there overrides the file). `npm run dev` and `npm run build:offline` (mode `offline`)
leave every live feature off: the box says live answers switch on when the site goes live, and there is no connector
link or GitHub button. `web/dist` from `npm run build` (and from `npm run check`) is the production build; the smoke
test checks it as such and builds the offline variant and a test-settings variant itself.

## MCP SDK

- Package `mcp` 2.2.0 (with `mcp-types` 2.2.0), checked against the installed source, not from memory.
- Server: `from mcp.server import MCPServer` (also exported as `mcp.server.mcpserver.MCPServer`). `FastMCP` is
  gone in 2.x: `mcp.server.fastmcp` now raises an error that points to `MCPServer`.
- Tool errors: `from mcp.server.mcpserver.exceptions import ToolError`. Raising it returns `is_error=True` with the
  message, so the model can read it. `UnexpectedToolError` (a crash) is a subclass whose message is generic.
- `Tool.run(arguments, context, convert_result)` validates arguments, calls the function and converts the result;
  `GuardedTool` overrides it and calls it with `convert_result=False`, then converts after redaction.
- Tool hints: `from mcp.types import ToolAnnotations` (snake_case fields such as `read_only_hint`).
- Client used in tests: `from mcp import Client, StdioServerParameters`. `Client(server)` connects in process;
  `Client(StdioServerParameters(...))` spawns a subprocess. The subprocess gets only a short list of safe
  environment variables plus `env=`, so tests pass `HOME`, `USERPROFILE` and the redaction variables explicitly.
- Protocol: the default `Client(mode="auto")` negotiates `2026-07-28` (stateless, no initialize handshake);
  `mode="legacy"` negotiates `2025-11-25`. Both are tested.
- Tools return pydantic models, so each tool publishes an output schema and returns structured content.

## Layout

```
src/evidenceline/
  data/            mb2_round1_lab.csv ... mb2_round4_lab.csv, guidelines.json, corpus_manifest.json,
                   fds01_site/ (lab results, chain of custody, field sheet, site settings, soil criteria, README)
  units.py         exact unit conversion (Decimal, ug/L canonical)
  dataset.py       loads the MB2 lab files and rules into frozen records
  screening.py     one round against one limit; sums, non-detects, equal-is-not-above
  textparse.py     dates, numbers, analytes and rule names found in prose (regex, spans)
  checker.py       the paragraph checker
  core.py          get_results, lookup_limit, compare_rules, check_paragraph as plain functions
  models.py        pydantic output models for those four tools
  drafting.py      fill_numbers: placeholders to exact values, with numbered sources
  redact.py        the redaction guard rail and show_redactions
  tooling.py       builds every MCP tool: read-only, strict arguments, redaction around each call
  server.py        MCPServer wiring, INSTRUCTIONS and the console entry point
  tidy/            tidy_lab_files and get_review_item: reading, exact conversion, six QA checks, findings
  guidance/        search_guidelines: manifest, PDF extraction, headings, passages, synonyms, scope rules, BM25
                   index, ranking, eval
  answer/          the question box pipeline: routing, values, context, prompt, verifier (numbers, wording),
                   name redaction, model clients
  api/             the web API: /api/ask, /api/health and the hosted /mcp; gate.py (proxy secret, body cap)
  verification.py  compares the two guideline verification passes
  data/verification/  pass-a and pass-b (json and md), notes-recheck-a and -b (json and md), summary.json
scripts/           fetch_corpus.py, build_index.py, export_web_data.py, expected_tidy.py (independent tidy check),
                   precompute_answers.py, build_accuracy.py, prepublish_check.py (scan before any push)
evals/             guidance_golden.json (tuning), guidance_heldout.json (held-out set 1, now tuning), HELDOUT.md,
                   guidance_heldout2.json (held-out set 2, never tuned on), HELDOUT2.md
examples/          redact.example.toml
tests/             pytest, one file per module plus protocol and redaction tests over real stdio
web/               the website (Vite + React + TypeScript)
```

## Design notes

- **No floats.** Every concentration is a `Decimal` (ug/L for water, mg/kg for soil). ng/L and mg/L are converted
  by moving the decimal point (`Decimal.scaleb`), so 8 ng/L equals 0.008 ug/L exactly. Tool outputs carry values as
  strings.
- **Provenance on everything.** Each lab value carries its lab report id, file and row (the header is row 1, as in a
  spreadsheet). Each limit carries its document, table and page, and says whether the page number is the PDF page
  or the printed page. Each search passage carries its PDF and printed page and how the printed page was derived.
- **Two rules, never a choice.** `nemp-3.0` screens the sum of PFOS and PFHxS, and PFOS alone and PFHxS alone
  (Table 4, footnote a), against 0.07 ug/L (PFOA 0.56); the sum is listed first.
  `current` screens PFOS 0.008, PFHxS 0.03, PFOA 0.2 and PFBS 1 separately. `compare_rules`, `tidy_lab_files` and
  `fill_numbers` show or name both and say the choice is the scientist's call. A result equal to a limit is not above
  it.
- **Non-detects.** A `<0.001` result has no value, only a detection limit. In a sum, a non-detect is bounded
  between zero and its detection limit; if the bounds straddle the limit the result is "not confirmed". The tidy LOR
  check treats a non-detect whose LOR is above a criterion the same way: "not confirmed", never "below".
- **The checker is deterministic.** It never calls a model. It finds (a) numbers with units and traces them to a lab
  value, detection limit, computed sum or difference, or guideline limit, according to what the sentence says the
  number is; (b) change claims between two dates; (c) above, below or within claims, evaluated under each rule;
  (d) detected or not detected claims. It handles negation, month and day date forms, reference dates, "now" in
  guideline claims, and dates implied by quoted lab values.
- **Nothing passes silently.** Every checked item has a status (consistent, inconsistent, untraced,
  depends_on_rule, needs_judgement). Anything a tool cannot read confidently goes under `not_checked` with the
  reason. No summary ever says "no issues".
- **A number must match what the sentence says it is.** The words right before a number decide its role: after
  "exceeded" or "below" it is a value compared against; after "detection limit of" it is a detection limit; after
  "guideline of" or "ADWG value of" it is a guideline value (and only the named rule's values count); after "by" or
  "a fall of" it is a change between rounds. Otherwise it is the measured result of the nearest analyte named before
  it, on the date in its own clause. So "PFOS was 8 ng/L" is flagged even though 8 ng/L is the PFOS guideline, and
  "PFOA was 0.001 ug/L" is flagged when PFOA was `<0.001`.
- **The model never types a number.** `fill_numbers` puts values in from code; `check_paragraph` then checks the
  words around them.
- **Search says "not covered" instead of guessing.** Below a coverage threshold, for another state's rules, or for a
  document that is not indexed, `search_guidelines` answers "not covered" with the reason. Questions that ask for a
  value are pointed to `lookup_limit`; values are never read from extracted text.
- **Judgement stays with people.** "Similar" or "stable" is only confirmed when the values are identical. Tidy
  review items end with what the scientist decides; the tool never merges, rejects or corrects a result.

## Data notes

- The four MB2 files are EDD-style CSVs with 14 rows each. PFOS is row 14 and PFHxS row 15; rows 2 to 13 are other
  PFAS analytes reported as not detected (`<0.001`).
- `data/fds01_site/` is the messy-files scenario for `tidy_lab_files`: 73 results (soil metals and PFAS sampled
  12 Sep 2025, groundwater 16 Sep 2025), a chain of custody and a field sheet, each marked "SYNTHETIC DATA". It
  plants six issues, each flagged exactly once; `data/fds01_site/README.md` lists them and the traps that must not
  be flagged. MB2's values there match `mb2_round3_lab.csv`, reported in ng/L. `scripts/expected_tidy.py` recomputes
  the expected findings independently (standard library, exact fractions).
- Guideline values and pages come from the verified criteria set compiled on 2026-09-23 (NEMP 3.0 Table 4, PDF
  page 57; NEMP 3.1 Table 4, printed page 49, listing the ADWG values updated in 2025). Soil values for the tidy LOR
  check are HIL A from the same verified set.

## Known limits

Paragraph checker (each comes back as "not checked" with a reason, never as a silent pass):

- **Values listed after several analytes** ("PFOS and PFHxS were 0.038 and 0.019 ug/L respectively") are not paired.
- **Units written as words or exponents** ("micrograms per litre", "ug L-1") are not read.
- **"Similar" and "stable"** are confirmed only when the values are identical; any other change is "needs a person
  to judge" with the size shown.
- **Comparisons between analytes**, **"the latest round" inside a change claim**, **numbers with no analyte** and
  **pronouns across sentences** are not resolved.
- **Percentages** from `fill_numbers` change placeholders are listed as not checked (the notes say so).
- **Contamination, safety and risk wording** is always listed as a judgement the data cannot support.

Tidy:

- Only the packaged FDS-01 site; reading a local folder of lab files is not a tool yet.
- Detected results are not screened against investigation levels by tidy (only non-detect LORs are); `not_checked`
  says so. `compare_rules` screens MB2 rounds; nothing screens detected soil results yet.
- Soil is compared with HIL A only (the scenario is residential with garden); water with drinking-water values only.
- An extraction date before the sampling date is a review item, and the holding time is not worked out for it. An
  analyte reported for only one sample of a duplicate pair is listed under `not_checked`. A LOR of zero or less and
  a detected result of zero are read errors naming the row.
- An RPD just above a threshold is shown with as many decimal places as it takes (up to six) to show it above;
  otherwise one place.
- PFAS holding times come from lab guidance (NEMP 3.0 has none) and are labelled that way. The 1.5 x "marginal"
  blank factor is a demonstration setting and says so; RPD thresholds are configurable.
- Only the PFOS, PFHxS and PFOA CAS numbers were checked against a source.

Search:

- Lexical BM25 with a hand-written synonym map, heading and phrase bonuses and one passage per page. Every constant
  was tuned on the golden set, and several settings scored within one question of each other.
- **Casual questions are still the main weakness.** On held-out set 2, never tuned on, the right page came first
  for 4 of 12 casual questions, and 7 of the 8 casual misses were a wrong "not covered" (see the scores above). The
  fix lifted held-out set 1 from 6/24 to 17/24 because it was tuned there; most of that did not carry over.
- Remaining held-out set 1 misses: h03 (mixing zone) is "not covered" but its near-miss passages reach the model;
  h06 (QC duplicates) has the right page at rank 12; h10 (stock watering) shows passages but not the expected page,
  because the common word "site" counts in the more-than-half rule; h12 misses the lead table page.
- Of 35 extra off-topic questions written during the fix, 8 still find passages ("Who is the Minister for
  Environment?", "How much is the landfill levy in WA?", which the guidance does mention, and "...auditor in
  Victoria?", since "Victoria" alone is left out on purpose). They go to the model, which must reply NOT_COVERED. On
  held-out set 2, x04 (microplastic size fractions) returns passages instead of "not covered".
- Section headings come from regular expressions, with wrapped headings joined and numbering checked; they can
  still be wrong. Contents pages, reference lists and lists of abbreviations are not indexed.
- The question word "container" maps to sample containers, so a question about waste containers is searched as
  sample containers. The G22 asbestos-reporting page and the DSI checklist are not ranked first.
- The value note (use `lookup_limit`) and the verdict note (Evidenceline does not decide whether a site is
  contaminated) come from regular expressions. They cover the common wordings ("standard", "level of PFOS", "PFOS
  guideline", "is the groundwater contaminated", "does X mean the site is contaminated") but not every phrasing, and
  the verdict note can also appear on a question that only mentions contamination after a verb.
- A word no indexed document contains adds no weight, but it still counts in the more-than-half rule (that keeps
  "What earthquake design loads apply to buildings in Perth?" not covered). A capitalised name no document uses
  ("RAAF Base Pearce") keeps full weight. On eight chatty questions with new filler words the rule lifted 4 of 8
  found to 7 of 8; on its own it changed neither tuning set once the synonyms were in.
- PFAS NEMP 3.1 is not indexed. Embeddings are future work.
- Another country counts as out of scope only with a rules word (rule, regulation, limit, ban, restrict, allow). A US
  EPA method name or number never counts, because the indexed guidance cites the US EPA and Canada about 100 times
  (vapour models, test methods, sample volumes, the ADWG derivation), so "What does the US EPA recommend?" is still
  searched and reaches the model.
- "ACT", "NT" and "Tas" are recognised as states only in capitals, as written.
- A document named in passing still limits the search to that document: "instead of the NEPM default in WA" searches
  only the NEPM, which is why the launch tester's L-p3 missed DWER p. 43.
- The launch tester's in-scope misses (L-c1, L-c5, L-p1, L-p3) were not tuned on; they stay as reported data.
- Seven held-out set 2 questions are close in wording to tuning questions (see the scores above); the Accuracy page
  shows the score with and without them.

Answers and the web API:

- A borderline "not covered" costs one model call (two if the first answer fails a check), against the daily limit.
- The model sometimes writes an uncited opening sentence. The one checked second attempt recovers most of these, but
  it can double the model calls on a failed answer, and both count against the daily limit.
- The verifier reads numbers with regular expressions: numbers written as words are not read, and a bare number (not
  a concentration) is traced to any cited passage, not to the one cited in its own sentence.
- Correct answers that repeat a concentration from a passage are withheld on purpose: concentrations must come from
  the verified guideline values.
- Rule names are recognised only as "NEMP 3.0/3.1" and "current rule/values"; "ADWG" alone is not tied to either
  rule.
- The condition exception for verdict wording is a four-word window, so "When low the water is safe" would pass.
- Question-box name redaction works by shape: a lone name with no company or place word ("Is Harbourline OK?") is
  not redacted, and a name that looks like guidance ("Water Corporation") is left in.
- `web/public/data/answers.json` goes stale whenever the search, the index or a guideline note changes; rerun
  `scripts/precompute_answers.py` (local Claude Code CLI, about a dozen model calls).
- Rate limits live in memory and reset when the service restarts. On the hosted connector the redaction session is
  shared by all callers: `show_redactions` shows everyone's placeholder counts (never values) until a restart.
- Until `EVIDENCELINE_PROXY_SECRET` is set on the host, an untrusted client IP header is ignored, so every visitor
  behind the proxy shares one set of limits.
- The model's page text comes from the index's `chunks` columns; if the index layout changes, it falls back to the
  short excerpts.
- `cache_dir()` defaults to `.cache/corpus` in the source checkout; a non-editable install needs
  `EVIDENCELINE_CORPUS_DIR`.
- The live box now withholds answers over the length or copying limit ("short sentences", "no long quotes"), which
  costs more retries: two of the six model attempts on the prepared reporting question failed on length, and the
  prepared PFOS answer passed on its second attempt.
- The prepared reporting answer says "such as the owner, occupier or an auditor"; DWER 2025 section 6.1 (p. 20) says
  "an auditor engaged to provide a report that is required", so that wording is a simplification.
- A failed Turnstile check now uses up one of that address's questions for the hour (the per-IP limit runs first, so
  a flood of bad tokens gets 429 and stops calling Cloudflare).
- The hostname Cloudflare's Turnstile reply names is not checked. `TURNSTILE_SECRET` set to one of Cloudflare's test
  secrets logs a start-up warning (never the secret).

Redaction:

- People's names are only caught when listed in the identifier file; addresses without "WA" and bare 8-digit phone
  numbers likewise. The hosted connector loads only the built-in demo file (`builtin:fds01-demo`: the fictional
  client and site address), so it redacts no other client, site or people's names. Restoring a client alias gives
  back the first listed name.
- A suburb-first address is caught only when the suburb is capitalised ("Welshpool" or "WELSHPOOL"), so the words
  before it are not swept in.
- Matching errs towards redacting: a listed name split by up to eight punctuation characters ("Harbourline. Logistics
  firms") and prose shaped like "<number> <words> road <words> wa" are replaced too.
- `search_guidelines` results are not redacted, so public addresses and phone numbers in the guidance appear as
  printed.
- A listed name now matches next to digits, underscores and a lower-to-capital change, which errs towards redacting:
  a short listed name can be caught inside a code such as "QRH2" or "xyzQRH". A name glued to more capitals
  ("HARBOURLINEDSI") or to lower-case letters ("harbourlinedsi", "Harbourlines") is not caught.
- A separator inside a single listed word is matched only when that split spelling is listed, as "Harbour Line" now
  is in the demo file.
- A listed address takes a suburb, state or postcode only when it is part of a listed spelling. With a custom file,
  "42 Imaginary Road Sampleton WA 6999" still becomes "[ADDRESS-1] WA 6999" (a test pins this); the demo file lists
  every ending of its address.

Other:

- Wrong-type tool arguments (a number where text is expected) return the SDK's validation text, which is accurate
  but technical.
- `drafting.py` has its own copies of the round-date and analyte lookups, because the ones in `core.py` are private.
- Both `scripts/export_web_data.py` and `web/scripts/build-data.mjs` generate `sample-site.json` and `sources.json`;
  the source links are listed in both.
- The pre-publish scan's token rules need a name ("token", "secret", "password", "..._API_KEY") or "Bearer"/"Basic"
  next to the value; a bare token with no label is not caught. `web/.env.production` is the only environment file it
  accepts (and `.gitignore` lets through), and only with `VITE_` settings. `design-options/` (local design drafts
  with local drive paths) is git-ignored.

Website:

- /sample-site opens each result's row from copies of the MB2 lab files in `web/public/data/lab/`; `npm run data`
  writes them and `npm run data:check` checks them. The row reader assumes CSV with no quoted fields, which
  `build-data.mjs` enforces.
- The page waits 60 seconds for a live answer and the proxy waits 90 seconds, so slow tool calls through the
  connector are not cut short.
- /accuracy publishes the pytest suite, the search scores and the guideline verification, but not the website's own
  checks (`npm run check`, the proxy tests, `adversarial.mjs`), and a local run says "local build". That needs a CI
  area; the site's wording is toned down until then, and case E3 in `web/tests/adversarial.mjs` stays marked.
- The proxy rewrites a redirect's Location only when it names the API's own host; FastAPI never redirects elsewhere.
  A rewritten path outside `/api` or `/mcp` would open the site's "not found" page.
- `dist/build.json` is public. It holds only whether each feature is on, never a key or an address.
- The "Try it" wording follows the features the build switched on. A build with the connector on and the question box
  off gets its own wording, but no deploy stage uses that mix.

## Not done yet

- Recreational and ecological criteria are not loaded; soil criteria are used only by the tidy LOR check.
- Not deployed: the public GitHub repository exists but is empty, and the site, the Render service and the DNS
  record are not set up yet. The live question box and the hosted connector are built and tested locally.
- The NEMP 3.1 side of the soil PFOS and PFHxS note could not be re-checked (the PDF could not be downloaded
  during the re-checks); both first passes had confirmed it.
- Held-out set 2 has now been scored twice (the second time after the launch fixes, with the same result); a new
  held-out set is needed before its score can be quoted again after any further tuning.
