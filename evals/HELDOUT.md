# Held-out question set for the guidelines search

File: `evals/guidance_heldout.json`. Written 2026-09-24.

## Why it exists

The golden set (`evals/guidance_golden.json`) was used to tune the search, so its scores flatter it. This set
was written separately so the search can be scored on questions it was never tuned on.

## How it was written

- Written **without seeing the search code or its results**. The author did not open
  `src/evidenceline/guidance/`, did not open `evals/guidance_golden.json`, and did not run the search or the
  evaluator. The only sources were the documents themselves.
- Documents: the five indexed documents in `src/evidenceline/data/corpus_manifest.json` (`nepm-b1`,
  `nemp-3.0`, `dwer-amcs`, `dwer-irc`, `adwg-pfas`). Before writing, the SHA-256 of each downloaded file in
  `.cache/corpus/downloads/` was checked against the manifest, and all five matched. PFAS NEMP 3.1 is not
  indexed, so no question expects it.
- Each question was written from the page text in `.cache/corpus/text/<id>.json` and the downloaded web page
  for `adwg-pfas`. Wording is how a graduate environmental scientist in Perth would ask it: plain and varied,
  not copied from headings.
- Each expected page was then checked again against the original PDF with `pypdf`, page by page (34 key
  phrases, all found on the stated pages). Page numbers are **PDF page numbers** (1 is the first page of the
  file), not printed page numbers. Each note gives the printed page and the section or table.

## What is in it

- 24 answerable questions: NEMP 3.0 x7, ASC NEPM B1 x5, DWER 2021 assessment and management x5
  (plus one shared question), DWER 2025 identification, reporting and classification x4, ADWG PFAS fact sheet x3.
  9 have their answer on a table page (the note says "Table page").
- 6 out-of-scope questions, each expected `"not_covered"`: two about other states' rules (NSW, SA), one about
  a specific real site's results, two that the documents don't cover (landfill prices, lab prices and
  turnaround), and one prompt-injection attempt.

## Shape and scoring

`[{id, question, expected, note}]`. `expected` is either `"not_covered"` or a list of
`{doc_id, pdf_pages}`. A passage counts as a hit if its document matches and its PDF page is in
`pdf_pages`. When the list has more than one entry (h10), a hit on any of them counts. Where
`pdf_pages` has several pages, the first is the main answer page and the rest also answer it.

`adwg-pfas` is a web page with no page numbers, so its entries have `pdf_pages: []` and an extra `sections`
list (the fact sheet's own headings). Score these on document, and on section if the result gives one.

## Limits

- The author compiled the set alone and checked every page, but no practitioner has reviewed it.
- Whether an answer is "on" a page is a judgement: the notes say what the page says so a reviewer can
  disagree.
- Once the search is tuned against this set, it stops being held out. Report scores from the first run, and
  write a new set for later checks.
