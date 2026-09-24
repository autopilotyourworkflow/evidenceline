# Notes re-check B (2026-09-24)

An independent re-check of two explanatory notes that were changed after the first two verification passes, and of
the NEMP 3.0 wording in `server.py`. The data is in `notes-recheck-b.json`. This is an automated check, not a review
by a practitioner.

## Method

- Read only the current data files, `server.py` and the primary source PDFs. The other re-check and the earlier pass
  files were not opened.
- Downloaded both PDFs fresh on 2026-09-24 and compared their SHA-256 with `corpus_manifest.json` (both match).
- PFAS NEMP 3.0: the official DCCEEW address timed out twice, so the 15 March 2025 Wayback Machine capture of the same
  file was used:
  http://web.archive.org/web/20250315173805id_/https://www.dcceew.gov.au/sites/default/files/documents/pfas-nemp-3.pdf
- ASC NEPM Schedule B1, compilation F2013C00288:
  https://www.legislation.gov.au/F2008B00713/2013-05-16/2013-05-16/text/original/pdf/2
- Text taken with pypdf and read in full around each table, every footnote included. A note passes only if every
  statement in it is in the source.

## Results

| Note | Verdict |
|---|---|
| NEMP 3.0, sum of PFOS and PFHxS, 0.07 ug/L (`guidelines.json`) | confirmed |
| Soil HIL A, arsenic (`soil_criteria.json`) | confirmed |
| Soil HIL A, sum of PFOS and PFHxS (`soil_criteria.json`, extra) | could not verify (NEMP 3.1 side) |
| `server.py` INSTRUCTIONS, NEMP 3.0 sentence | confirmed |
| `server.py` `lookup_limit` docstring | confirmed, two small wording points |

### 1. NEMP 3.0, sum of PFOS and PFHxS (0.07 ug/L)

File: "Applies to PFOS on its own, PFHxS on its own, and the sum of the two (Table 4, footnote a). Compare each of the
three with 0.07 ug/L."

Source, NEMP 3.0 PDF page 57 (printed page 48), Table 4 "Health-based guideline values developed by health
authorities": column "Sum of PFOS and PFHxS a"; row "Drinking water quality guideline 0.07µg/L 0.56µg/L NHMRC 2011"
(the text layer reads "NHMRC 201 1"). Footnote a, on the same page:

> Where the criteria refer to the sum of PFOS and PFHxS, this means concentrations of PFOS only, PFHxS only, and the
> sum of the two.

Confirmed. The note says what the footnote says, with the right table and footnote; the instruction to compare each
of the three with 0.07 ug/L follows directly from it. Value, unit, scenario, table and PDF page also match.

`soil_criteria.json` has no note on the 0.07 ug/L value (it holds soil values only). Its one note on the sum of PFOS
and PFHxS (HIL A, 0.003 mg/kg) quotes the same footnote wording. NEMP 3.0 Table 5 gives 0.003 mg/kg for HIL A (PDF
page 59) and footnote a with identical wording (PDF page 60), so the NEMP 3.0 half is confirmed. The file cites NEMP
3.1 Table 5, printed page 52, which could not be downloaded, so that note stays "could not verify".

### 2. Soil HIL A, arsenic

File: "Arsenic (as the table row reads); the value assumes 70% oral bioavailability (Table 1A(1), note 2)."

Source, Schedule B1 Table 1A(1) "Health investigation levels for soil contaminants", printed page 48 (PDF page 56),
under "Metals and Inorganics": "Arsenic2 100 500 300 3 000" (the 2 is the superscript note mark; columns HIL A, B, C,
D in mg/kg). Note 2, printed page 49 (PDF page 57):

> (2) Arsenic: HIL assumes 70% oral bioavailability. Site-specific bioavailability may be important and should be
> considered where appropriate (refer Schedule B7).

Confirmed. The row reads just "Arsenic" and HIL A is 100 mg/kg; the 70% oral bioavailability statement matches note 2.
The note leaves out note 2's second sentence, which is an omission, not an error. The file's page field names only
the table's page; note 2 is on the next page.

### 3. `server.py`

- INSTRUCTIONS: "screens the SUM of PFOS and PFHxS, and PFOS alone and PFHxS alone, against 0.07 ug/L (Table 4,
  footnote a)". Matches footnote a. The sentence on the "current" rule comes from NEMP 3.1 and was not checked.
- `lookup_limit` docstring: "0.07 ug/L applies to PFOS alone, PFHxS alone and their sum (Table 4, footnote a):
  asking for either returns that value, with the footnote quoted." Matches footnote a, and running
  `core.lookup_limit` confirms the value, table and page for all three analytes. Two small points: only the key
  phrase of the footnote is quoted, and only for a PFOS or PFHxS request; and for those requests the returned fields
  `applies_to` ("sum") and `compared_quantity` ("sum of PFOS and PFHxS") do not say the value also applies to the
  analyte on its own, though the note and caveat in the same result do.

## Suggested follow-ups (outside this re-check's files)

- `core.py`: for a PFOS or PFHxS request under nemp-3.0, let `compared_quantity` name the analyte on its own.
- `soil_criteria.json`: add "note 2 on printed page 49 (PDF page 57)" to the arsenic page field.
- Re-check the NEMP 3.1 side of the soil PFOS and PFHxS note once that PDF can be downloaded.
