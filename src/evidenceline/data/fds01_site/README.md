# FDS-01: synthetic site files for "Tidy lab results"

**Synthetic data.** FDS-01 ("Fictional Depot Site") is invented. Every result, sample, person, laboratory, client,
address and date in this folder is made up for the Evidenceline concept. The client ("Harbourline Logistics
(fictional)") and site address ("12 Example Road, Welshpool WA (fictional)") in the field sheet header exist only
so redaction can be shown later; the tidy tools never copy them into their output.

Scope: metals and PFAS only (petroleum hydrocarbons are out of scope). One field event, FE-2025-09: soil on
12 September 2025 (hand augers), groundwater on 16 September 2025 (peristaltic pump, dedicated tubing). MB2's
values match the existing MB2 round of 16 September 2025 (`mb2_round3_lab.csv`), reported here in ng/L.

## Files

| File | What it is | Header row | Data rows |
|---|---|---|---|
| `lab_results.csv` | The lab's results, one row per result (73) | 2 | 3 to 75 |
| `chain_of_custody.csv` | Two chain-of-custody forms (COC-FDS-01 soil day, COC-FDS-02 groundwater day) | 2 | 3 to 18 |
| `field_sheet.csv` | The consultant's own field record, with QA types, duplicate parents and equipment | 7 | 8 to 23 |
| `site.json` | Site id, name, the synthetic label and the scenario notes (HIL A; drinking water) | | |
| `soil_criteria.json` | Soil HIL A values, copied from the verified criteria set (2026-09-23) | | |

Lines starting with `#` before the header are a preamble. Row numbers in tool output are the file's own line
numbers, as a spreadsheet shows them. Dates are always read day first.

## Columns, and the ESdat-style names they correspond to

Evidenceline uses its own documented column names. The ESdat names are listed for orientation only: ESdat's ELDF
format is proprietary (EarthScience Information Systems), and Evidenceline does not read ESdat files or claim to be
compatible with them.

`lab_results.csv`

| Column | Meaning | ESdat-style name |
|---|---|---|
| lab_report | Lab report number | Lab_Report_Number |
| lab_sample_id | Lab's own sample code ([report]_[number]) | SampleCode / Lab_SampleID |
| sample_id | Client sample id as the lab wrote it | Field_ID |
| sampled | Sampling date and time, `12 Sep 2025 11:40 AM` | Sampled_Date_Time |
| matrix | Soil or Water | Matrix_Type |
| analyte | Analyte name as reported | OriginalChemName |
| cas | CAS number | ChemCode |
| prefix | `<` when not detected, else empty | Prefix |
| result | The number; for a non-detect, the LOR | Result |
| unit | Unit of the result | Result_Unit |
| lor | Limit of reporting | EQL |
| lor_unit | Unit of the LOR | Detection_Limit_Units |
| method | Method name | Method_Name |
| extracted | Extraction date | Extraction_Date |
| analysed | Analysis date | Analysed_Date |
| lab_comment | Lab comment on the result | Lab_Comments |

`chain_of_custody.csv`: coc_number, sample_id, sampled (`12/09/25 11:40`), matrix (`S` or `W`), containers,
analyses, hold (`Y` or `N`), comments, relinquished_by, relinquished, received, temp_on_receipt_c.

`field_sheet.csv`: sample_id, date (`12/09/2025`), time, location, depth_m, matrix, equipment, qa_type (`primary`,
`field_duplicate`, `rinsate_blank`, `field_blank`, `trip_blank`, `container_blank`), parent_sample, description,
notes.

CAS numbers for PFOS (1763-23-1), PFHxS (355-46-4) and PFOA (335-67-1) were checked against PFAS NEMP 3.0
Table B2. The metal and PFHxA CAS numbers are standard values, not re-checked.

## The six deliberate issues (each should be flagged exactly once)

| # | Check | Where | Expected |
|---|---|---|---|
| 1 | Sample ids | Lab `SB3-15` (rows 20 to 27) vs chain of custody and field sheet `SB3-1.5` | Suggested match (same time 12/09/25 11:40, same matrix, same letters and digits); not merged |
| 2 | Units | MB2 in ng/L (rows 52 to 55); other water results in ug/L | 38 ng/L = 0.038 ug/L, 19 ng/L = 0.019 ug/L, <1 ng/L = <0.001 ug/L. Without conversion the MB2/QC2 PFOS RPD would be a false 199.6% |
| 3 | Field duplicates | SB4-0.3 / QC1 lead 180 vs 95 mg/kg (rows 32, 40) | RPD 85 / 137.5 x 100 = 61.8%; mean 137.5 is at least 10 x LOR (50): above 50%, investigate |
| 4 | Holding times | SB3-15 mercury (row 25): sampled 12 Sep 2025, extracted 12 Oct 2025 | 30 days against 28 days (NEPM B3 Table 1): 2 days over |
| 5 | Blanks | RB1 PFOS 0.004 ug/L, LOR 0.001 (row 44); RB1 rinsed hand auger A | 4 x LOR. Auger A that day: SB1-0.2, SB1-1.0, SB2-0.2, SB4-0.3, SB4-1.0, QC1. Marginal: SB2-0.2 sum of PFOS and PFHxS 0.0022 + 0.0012 = 0.0034 mg/kg against HIL A 0.003 (1.13 x) |
| 6 | LOR against criteria | MB3 PFOS <0.01 ug/L (row 56), standard-level method | LOR above the current drinking-water value for PFOS (0.008 ug/L): not confirmed. Under NEMP 3.0 the sum is at most 0.02, not above 0.07. Both shown; the rule choice is the scientist's |

## Traps (checked, and correctly not flagged)

- SB4-1.0 is on the chain of custody marked HOLD and has no lab results: held at the lab, not missing.
- Water units written three ways: `ug/L`, `µg/L` (micro sign, MB1) and `μg/L` (Greek mu, MB3). One unit.
- Three date styles (lab `12 Sep 2025 09:10 AM`, chain of custody `12/09/25 09:10`, field sheet `12/09/2025` plus a
  time column), all read day first. Month-first reading would put 12/09/25 in December.
- SB4-0.3 / QC1: mercury 0.2 vs 0.1 (RPD 66.7%) and cadmium 0.6 vs <0.4 (40.0%, the LOR used) have pair means
  below 10 x LOR, so no limit applies. Zinc 15.7% is at or below 30%.
- MB2 / QC2 after conversion: PFOS 8.2%, PFHxS 5.4%; PFOA and PFHxA below the LOR in both, so no RPD.
- SB3-0.5 mercury extracted on day 28: at the limit, not over it. SB3-15's other metals at day 30 are within
  6 months.
- FB1, CB1 and TB1: every result below the LOR. Auger B (SB3) and the groundwater samples are not linked to RB1.
- SB1-1.0 PFOS <0.0002 inside the sum: between 0.0015 and 0.0017 mg/kg, not above 0.003.
- MB3 PFHxS and PFOA <0.01: LOR not above their values (0.03, 0.2 and 0.56 ug/L).
- PFHxA has no investigation level loaded: listed as not checked, not flagged.

Expected outcomes are computed independently by `scripts/expected_tidy.py` and pinned in `tests/test_tidy*.py`.
