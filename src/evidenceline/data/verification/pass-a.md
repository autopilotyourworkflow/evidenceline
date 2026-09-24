# Guideline values: verification pass A

Date: 24 September 2026. Full record with verbatim quotes: `pass-a.json`.

This pass checked every guideline value in `guidelines.json` and `fds01_site/soil_criteria.json` against the
primary source documents, on its own. The compiled criteria notes and the other verification pass were not read.

## Result

| | Count |
|---|---|
| Values checked | 16 |
| Confirmed | 15 |
| Mismatch | 1 (the number is right; a note is wrong) |
| Could not verify | 0 |
| Rule details checked (document, edition, WA status, scenario) | 5, all confirmed |

Every number, unit, table and page reference matches its source.

## The one mismatch

**`guidelines.json`, rule `nemp-3.0`, PFOS+PFHxS 0.07 ug/L.** The value, Table 4 and PDF page 57 (printed page 48)
are correct. The note is not. It says "This rule has no separate value for PFOS or PFHxS on its own", but footnote a
of NEMP 3.0 Table 4 says: "Where the criteria refer to the sum of PFOS and PFHxS, this means concentrations of PFOS
only, PFHxS only, and the sum of the two." So 0.07 ug/L also applies to PFOS alone and to PFHxS alone. This matters
when a lab reports only one of the two: that result should still be compared with 0.07 ug/L. The soil file already
handles the same footnote correctly (`each_member_too: true`). Suggested fix: reword the note and, if the code
depends on it, treat the drinking-water sum the same way as the soil sum.

## One note that the source does not state

**`soil_criteria.json`, S-As-A.** The value (100 mg/kg), Table 1A(1) and page are confirmed, and so is "70% oral
bioavailability" (footnote 2). The word "Total" in the note does not appear in the table or its notes; the row
reads only "Arsenic". Comparing total arsenic with it is normal practice, but it is not a quote from the source.

## Where each value was confirmed

| Values | Source | Location |
|---|---|---|
| NEMP 3.0 drinking water: PFOS+PFHxS 0.07, PFOA 0.56 ug/L | PFAS NEMP 3.0 (HEPA 2025) | Table 4, PDF page 57, printed page 48 |
| Current drinking water: PFOS 0.008, PFHxS 0.03, PFOA 0.2, PFBS 1 ug/L | PFAS NEMP 3.1 (HEPA 2026), source column "NHMRC 2011 updated 2025" | Table 4, printed page 49 |
| Same four values, independently | NHMRC ADWG PFAS fact sheet (endorsed 2025; ADWG version 4.0, June 2025) | Guideline statements, e.g. "should not exceed 8 ng/L (0.008 ug/L)" |
| Soil HIL A: PFOS+PFHxS 0.003, PFOA 0.06 mg/kg | PFAS NEMP 3.1 | Table 5, printed page 52 (NEMP 3.0 has the same values in Table 5, PDF page 59, printed page 50; 3.1 says they "were not revised") |
| Soil HIL A: arsenic 100, cadmium 20, chromium (VI) 100, copper 6000, lead 300, mercury (inorganic) 40, nickel 400, zinc 7400 mg/kg | ASC NEPM Schedule B1, compilation F2013C00288 (16 May 2013) | Table 1A(1), PDF page 56, printed page 48 |
| NEMP 3.0 "adopted for implementation in Western Australia", published March 2025 | WA Environment WAtch PFAS page (last modified 10 March 2026) | Verbatim on the page |
| No WA statement adopting NEMP 3.1 | Same WA page, plus a web search | The page still names only 3.0 (as of 24 September 2026) |

## How it was checked, and limits

- NEMP 3.0 and ASC NEPM Schedule B1 were downloaded as PDFs and read page by page with pypdf, so their PDF and
  printed page numbers are exact. NEMP 3.0 came from the Wayback Machine copy of the official DCCEEW file
  (15 March 2025), because dcceew.gov.au times out from this machine.
- NEMP 3.1 could not be downloaded directly (dcceew.gov.au timed out, and there is no archive copy). It was read as a
  text rendering of the official PDF link through the r.jina.ai reader. Its printed page numbers come from the running
  page headers; how that rendering places headers was checked against NEMP 3.0, whose page breaks are known. The PDF
  page number of the NEMP 3.1 tables could not be read, and the file does not claim one.
- Context, not a finding: NEMP 3.1 Table 4 now labels the older 0.07 and 0.56 ug/L drinking-water values
  "For New Zealand only". The file cites those values to NEMP 3.0, which is correct for that edition. This pass does
  not say which rule applies to a site.
- Two quotes from the WA page contain the page's own dashes; they are kept exactly as published.
