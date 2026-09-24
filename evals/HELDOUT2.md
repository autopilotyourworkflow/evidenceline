# Second held-out question set for the guidelines search

File: `evals/guidance_heldout2.json`. Written 2026-09-24.

## Why it exists

The first held-out set (`evals/guidance_heldout.json`) has now been used to find and fix weak spots in the search,
so it has become a tuning set. This second set was written afterwards so the improved search can be scored on
questions nobody has tuned it on. Report its first run as it comes out. Once anything is tuned on it, it stops
being held out and a new set is needed.

## How it was written

- Written **without seeing the search code, the other question sets or any results**. The author did not open
  `src/evidenceline/guidance/`, `evals/guidance_golden.json` or the questions in `evals/guidance_heldout.json`,
  did not run the search or the evaluator, and did not look at any search output.
- What the author did read: `PRODUCT.md`, `README.md` and `DEVNOTES.md` (as instructed for every task); the
  first 20 lines of `evals/HELDOUT.md` and its short paragraph on the file format; and the key structure of
  `evals/guidance_heldout.json` printed by a script with every question and note masked, so the shape could be
  copied without reading any question. `README.md` and `DEVNOTES.md` mention, in general terms, that
  conversational wording and questions about storing PFAS samples were weak for the old search. The author knew
  that when writing; question c06 (field contamination from sunscreen and clothing) is close to that topic.
- Documents: the five indexed documents in `src/evidenceline/data/corpus_manifest.json` (`nepm-b1`,
  `nemp-3.0`, `dwer-amcs`, `dwer-irc`, `adwg-pfas`). Before writing, the SHA-256 of each file in
  `.cache/corpus/downloads/` was checked against the manifest; all five matched. PFAS NEMP 3.1 is not indexed,
  so no question expects it.
- Each question was written from the page text in `.cache/corpus/text/<id>.json` (and the downloaded web page
  for `adwg-pfas`), reading the whole page before choosing it as an answer.
- Every expected page was then checked again against the original PDF with `pypdf`: a script extracts that
  PDF page and confirms a key phrase from the answer is on it (for example "M270" on NEMP 3.0 PDF page 112,
  "Within 21 days" on DWER 2025 PDF page 32). All 44 page checks and the 3 ADWG section checks passed. One page
  that failed this check (DWER 2025 PDF page 10 for c05, which is about disturbing natural minerals rather than
  naturally high background) was removed rather than kept.
- Page numbers are **PDF pages** (1 = the first page of the file), not printed page numbers. Each note gives
  the printed page and the section or table. Printed page = PDF page minus 5 for both DWER guidelines, minus 9
  for PFAS NEMP 3.0 and minus 8 for ASC NEPM Schedule B1.
- Every out-of-scope question was checked by a case-insensitive text search of all five documents for its key
  terms (for example "preliminary risk screen", "Environmental Management Register", "Pearce", "microplastic",
  lab prices), with no match that answers it.
- No em or en dashes anywhere in the file (checked by script).

## What is in it

30 questions:

- **12 casual questions** (`c01` to `c12`), typed the way a non-expert visitor, a landowner, a neighbour or a new
  graduate would: everyday words, some lower case, some chattiness ("Sorry if this is a silly question").
  DWER 2025 identification, reporting and classification x3, DWER 2021 assessment and management x3 (plus a
  shared one), ASC NEPM B1 x2, PFAS NEMP 3.0 x1 (plus a shared one), ADWG PFAS fact sheet x3.
- **12 practitioner questions** (`p01` to `p12`) in varied wording that does not copy headings. Six are
  answered on table pages (p01, p02, p06, p07, p08, p11). DWER 2025 x3, DWER 2021 x2 (plus a shared one),
  PFAS NEMP 3.0 x5, ASC NEPM B1 x2.
- **6 out-of-scope questions** (`x01` to `x06`), each expected `"not_covered"`: two about other states' rules
  (Victoria, Queensland), one about a specific real site's results (RAAF Base Pearce), two on topics the
  documents do not cover (microplastics, lab prices), and one prompt injection that also asks for a claim that
  water is safe.

## Format and scoring

Same shape as `evals/guidance_heldout.json`: a list of `{id, question, expected, note}`. `expected` is either
`"not_covered"` or a list of `{doc_id, pdf_pages}`. A passage counts as a hit if its document matches and its
PDF page is in `pdf_pages`. When the list has more than one entry (c04, c05, c09, c10, p05, p12), a hit on any
of them counts. Where `pdf_pages` has several pages, the first is the main answer page and the rest also
answer it.

`adwg-pfas` is a web page with no page numbers, so its entries have `pdf_pages: []` and an extra `sections`
list (the fact sheet's own top-level headings). Score these on document, and on section if the result gives
one; a subsection counts (for example "Levels detected in Australian drinking water > PFOS" is in "Levels
detected in Australian drinking water").

The id prefix gives the group: `c` casual, `p` practitioner, `x` out of scope. If the evaluator reports the
first held-out set by group, it can split this one the same way from the prefix.

## Known limits

- Written by an AI agent from the documents, not by a practitioner. The expected pages are what the author
  judged to answer each question; a reasonable reader might accept a nearby page too.
- Some casual questions have more than one fair answer across documents; those list each one found, which
  makes them slightly easier to hit than a single-page question.
- The set is small (24 answerable), so one question moves hit@k by about four points.

## First run (2026-09-24)

Scored once by the integrator, after the casual-question fix and with nothing tuned on this set
(`.venv/Scripts/python -m evidenceline.guidance.evaluate --heldout2`, k = 8):

| Group | hit@1 | hit@5 | hit@8 | recall@8 | "Not covered" when it should |
|---|---|---|---|---|---|
| All (24 in scope, 6 out) | 12/24 | 12/24 | 12/24 | 0.46 | 5/6 |
| Casual (c01 to c12) | 4/12 | 4/12 | 4/12 | 0.29 | |
| Practitioner (p01 to p12) | 8/12 | 8/12 | 8/12 | 0.62 | |

Misses: c01, c02, c03, c04, c05, c10 and c12 came back "not covered"; c08, p05 and p11 returned passages but not an
expected page; p01 and p04 came back "not covered"; x04 (microplastics) returned passages instead of "not covered".
Counting the near-miss passages the question box gives its model, the score is still 12/24.

Found when scoring: p07 (landfill disposal limits) is close in wording to h05 in the first held-out set, which the
search was tuned on (word overlap 0.67). The author did not see that question; it is kept and scored as written (it
was a hit at rank 1), and the Accuracy page says so. The ids c01 to c12 also repeat the golden set's casual ids; the
files are scored separately, so this mixes nothing.

## Rescored once after the launch fixes (2026-09-24)

After the launch-round fixes to search scope (other states and countries, instructions to the system, the PFAS NEMP
edition note), the set was scored once more, with nothing tuned on it. Every line of the evaluator's output was the
same as the first run: 12/24 first place, casual 4/12, practitioner 8/12, out of scope 5/6, the same misses.

A stricter near-copy check then found six more questions close to a tuning question, besides p07 ~ h05. It uses
content words only (common words removed) and counts a pair when the word overlap (Jaccard) is at least 0.40, or at
least 0.25 when both questions expect the same page or section, or at least 0.30 when both are out of scope:

| Held-out 2 | Tuning question | Overlap | Why it counts |
|---|---|---|---|
| c02 | h21 (held-out set 1) | 0.35 | same DWER page |
| c09 | g13 (golden) | 0.25 | same ADWG section |
| p06 | h01 (held-out set 1) | 0.28 | same NEMP 3.0 table page |
| p07 | h05 (held-out set 1) | 0.69 | almost word for word |
| x03 | x03 (held-out set 1) | 0.31 | both out of scope |
| x05 | o04 (golden) | 0.44 | both out of scope |
| x06 | x06 (held-out set 1) | 0.30 | both out of scope |

They stay in the set and are scored as written. Left out, the same run gives: the right page first for 9 of 20
(recall@8 0.40), casual 3 of 10 (0.25), and "not covered" for 2 of 3 out-of-scope questions. The Accuracy page names
all seven and shows both scores; `tests/test_accuracy_rechecks.py` and case A3 in `web/tests/adversarial_launch.mjs`
fail if the check finds a pair the list does not name, or the list names one it no longer finds.

This set has now been scored twice. If the search is tuned on it, it becomes a tuning set and a new held-out set is
needed.
