# Notes re-check A (2026-09-24)

An independent re-check of the two explanatory notes that were corrected after the first guideline verification.
The full record, with every quote, is in `notes-recheck-a.json`. This is an automated check, not a review by a
practitioner. It did not read the other re-check or the earlier pass files.

## How it was checked

- **PFAS NEMP 3.0.** The official file at dcceew.gov.au timed out, so the pinned Wayback capture of that file was
  downloaded afresh (SHA-256 `abcc5c28...ce196e`, the same file the corpus manifest records).
- **ASC NEPM Schedule B1.** Downloaded afresh from legislation.gov.au, compilation F2013C00288 (SHA-256
  `17788fb3...ad99`).
- Text was extracted with pdfplumber, and the table pages were rendered to images and read by eye to confirm the
  footnote markers.
- `lookup_limit` was run for PFOS, PFHxS and PFOS+PFHxS under `nemp-3.0` to see what it really returns.

## Results

| Note | Verdict | Why |
|---|---|---|
| `guidelines.json`, NEMP 3.0, PFOS+PFHxS 0.07 ug/L | confirmed | Says what Table 4 footnote a says: the value applies to PFOS only, PFHxS only, and the sum. |
| `soil_criteria.json`, arsenic HIL A | confirmed | The row reads "Arsenic" with note marker 2; note 2 says the HIL assumes 70% oral bioavailability. |
| `soil_criteria.json`, PFOS+PFHxS HIL A (related) | could not verify | The NEMP 3.0 half is confirmed (same footnote, same 0.003 mg/kg); NEMP 3.1 was not fetched in this re-check. |
| `server.py` INSTRUCTIONS | confirmed | Names all three quantities and cites Table 4, footnote a. |
| `server.py` `lookup_limit` docstring | confirmed | Matches the footnote. PFOS and PFHxS lookups do return 0.07 ug/L with the footnote's words quoted. |
| `core.py` `lookup_limit` fields (related) | mismatch | Asked about PFOS alone, the result still says the compared quantity is the sum. |

## The source text

**PFAS NEMP 3.0, Table 4** (PDF page 57, printed page 48). Column header "Sum of PFOS and PFHxS a"; drinking-water
row "0.07µg/L". Footnote a:

> Where the criteria refer to the sum of PFOS and PFHxS, this means concentrations of PFOS only, PFHxS only, and
> the sum of the two.

Table 5 (soil) carries the same footnote a, word for word (PDF page 60, printed page 51).

**ASC NEPM Schedule B1, Table 1A(1)** (row on PDF page 56, printed page 48; notes on PDF page 57, printed page 49).
Row, under "Metals and Inorganics": "Arsenic2 | 100 | 500 | 300 | 3 000" (mg/kg; residential A, residential B,
recreational C, commercial/industrial D). Note 2:

> (2) Arsenic: HIL assumes 70% oral bioavailability. Site-specific bioavailability may be important and should be
> considered where appropriate (refer Schedule B7).

## Points for the owner

- **`lookup_limit` fields.** For PFOS or PFHxS under `nemp-3.0`, `applies_to` is "sum" and `compared_quantity` is
  "sum of PFOS and PFHxS". The note and caveat in the same result are right, but these two fields point at the
  sum only. Suggested fix, in `core.py` (line 129) and `models.py`: for a member lookup, name the member on its
  own as the compared quantity, and either add an "each and sum" value to `applies_to` or reword the docstring so
  it no longer promises an either/or answer.
- **Arsenic note.** Correct as written. It leaves out the second sentence of note 2 (site-specific bioavailability
  may matter), and note 2 is on printed page 49 while the page field gives 48, which is right for the row.
- **Soil PFOS+PFHxS note.** The NEMP 3.1 half (Table 5, printed page 52, and "the same in NEMP 3.0 and 3.1") still
  needs a check against NEMP 3.1 itself.
