# Guideline values: verification pass B

Date: 2026-09-24. Machine-readable detail, with a verbatim source quote for every item: `pass-b.json` in this folder.

## What was checked

Every guideline value in `src/evidenceline/data/guidelines.json` (6 drinking-water values in two rules) and
`src/evidenceline/data/fds01_site/soil_criteria.json` (10 soil HIL A values), plus each rule's document,
edition, table, page and WA-status line. That is 21 records in total.

This pass was independent: it did not read pass A or the research notes the data files were copied from.
Each source was downloaded again on 2026-09-24, each PDF page's text was extracted with pypdf, and each value
was read from its table row, column and footnotes. A script asserts that every quote in `pass-b.json`
appears in the extracted source text and that every number in the data files equals the number read from
the source.

## Result

**All 21 records confirmed**: every value, unit, scenario, document, edition, table and page reference
matches its primary source. No number is wrong and no table or page reference is off.

Two notes (the explanatory text next to a value, not the value itself) need attention:

| Record | Finding | Verdict |
|---|---|---|
| NEMP 3.0, sum of PFOS and PFHxS, 0.07 ug/L | The note says "This rule has no separate value for PFOS or PFHxS on its own". Table 4 footnote a says the sum criterion means "PFOS only, PFHxS only, and the sum of the two". The same wording appears in user-facing text in `core.py` and `checker.py`, and `water_criteria()` in `tidy/criteria.py` sets `each_member_too` to False for this rule. When both results exist the sum check already catches either one alone; when one is missing or not detected, the other is not screened on its own. | mismatch (note) |
| ASC NEPM arsenic HIL A, 100 mg/kg | "70% oral bioavailability" is confirmed by note 2. The word "Total" is not in Schedule B1 (the row is just "Arsenic"). | could not verify (note) |

## Where each value was found

| Values | Source | Table | PDF page | Printed page | File says |
|---|---|---|---|---|---|
| PFOS+PFHxS 0.07 ug/L, PFOA 0.56 ug/L | PFAS NEMP 3.0 (HEPA 2025) | Table 4 | 57 | 48 | page 57, PDF page: confirmed |
| PFOS 0.008, PFHxS 0.03, PFOA 0.2, PFBS 1 ug/L | PFAS NEMP 3.1 (HEPA 2026); also the NHMRC ADWG PFAS fact sheet (endorsed 2025) | Table 4 | 58 | 49 | page 49, printed page: confirmed |
| Soil PFOS+PFHxS 0.003 mg/kg, PFOA 0.06 mg/kg (HIL A) | PFAS NEMP 3.1; same values in NEMP 3.0 Table 5 (PDF 59, printed 50) | Table 5 | 61 | 52 | printed page 52: confirmed |
| As 100, Cd 20, Cr(VI) 100, Cu 6000, Pb 300, Hg (inorganic) 40, Ni 400, Zn 7400 mg/kg (HIL A) | ASC NEPM Schedule B1, compilation F2013C00288 (16 May 2013) | Table 1A(1) | 56 | 48 | printed page 48 (PDF page 56): confirmed |

WA status lines:

- NEMP 3.0: the Government of Western Australia's Environment WAtch PFAS page says "Version 3.0 of the PFAS
  NEMP was published in March 2025 and has been adopted for implementation in Western Australia."
  Confirmed.
- NEMP 3.1: that page (last modified 10 March 2026) does not mention 3.1, the NEMP 3.1 PDF has no WA adoption
  statement, and a web search found none. "No WA statement adopting NEMP 3.1 was found" is confirmed as a
  statement about what was found; it cannot prove that none exists.

## Worth knowing

- NEMP 3.1 Table 4 now labels the old 0.07 ug/L and 0.56 ug/L drinking-water values "For New Zealand only".
  The data file attributes them to NEMP 3.0, where they are the Australian values, so the file is right. The
  website may want to say this when it shows both editions side by side.
- The two water rules use different page bases (NEMP 3.0 as a PDF page, NEMP 3.1 as a printed page). Both are
  correctly labelled.
- The NHMRC news and review pages timed out, so the month of the ADWG update (June 2025) was not confirmed from
  NHMRC directly. The fact sheet itself says "endorsed 2025" and NEMP 3.1 says "NHMRC 2011 updated 2025",
  which is all the data files claim.
- The NEMP 3.1 PDF, marked unavailable in the corpus manifest, downloaded directly from DCCEEW on 2026-09-24
  when the request carried browser Accept, Accept-Language and Sec-Fetch headers
  (SHA-256 23b090c18e4328a8c7d2a08cbe274e5fd54331d3a44c1bb4567e8ad21ca22949).

## Sources

- PFAS NEMP 3.0: https://www.dcceew.gov.au/sites/default/files/documents/pfas-nemp-3.pdf (DCCEEW timed out;
  read from the Wayback Machine capture of 15 March 2025, SHA-256 abcc5c28...196e)
- PFAS NEMP 3.1: https://www.dcceew.gov.au/sites/default/files/documents/pfas-nemp-3-1.pdf (direct)
- ASC NEPM Schedule B1: https://www.legislation.gov.au/F2008B00713/2013-05-16/2013-05-16/text/original/pdf/2
  (direct, SHA-256 17788fb3...ad99)
- WA government PFAS page: https://environmentwatch.wa.gov.au/pfas-investigations-in-western-australia/
- NHMRC ADWG PFAS fact sheet:
  https://guidelines.nhmrc.gov.au/australian-drinking-water-guidelines/part-5/physical-chemical-characteristics/cas-numbers-1763-23-1-pfos-335-67-1-pfoa-355-46-4-pfhxs

Checked by an automated pass, not reviewed by a practitioner.
