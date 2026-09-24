# Evidenceline inside Claude Code, 24 Sep 2026 (phase 1 tools)

A real Claude Code session (headless, `claude -p`) with only the Evidenceline tools allowed. The prompt asked Claude to tidy the FDS-01 lab files, explain review item 2, draft a paragraph with fill_numbers placeholders and check it, and ask the guidelines what a detailed site investigation report should include. Everything below is Claude's answer, unedited. Numbers re-checked by hand afterwards: PFOS 0.041 to 0.006 is a fall of 0.035 (85.4%); PFHxS 0.018 to 0.009 is 0.009 (50.0%); lead 180 and 95 mg/kg give an RPD of 61.8%; 38 against 0.035 gives 199.6%, 0.038 against 0.035 gives 8.2%.

---

## 1. What a person must check at FDS-01

The tidy step read 3 files: 73 lab results for 15 samples, 16 chain-of-custody rows and 16 field-sheet rows. It raised 6 review items. Nothing was merged, rejected or corrected.

1. **Sample name mismatch:** the lab calls a soil sample SB3-15, but the chain of custody and field sheet call it SB3-1.5. Confirm they are the same sample, for example from the lab's receipt record.
2. **Different unit at MB2:** MB2 was reported in ng/L while every other water result is in ug/L. Confirm with the lab whether ng/L was intended or is a labelling error.
3. **Duplicate pair disagrees:** lead in SB4-0.3 (180 mg/kg) and its blind duplicate QC1 (95 mg/kg) differ by an RPD of 61.8%, above the 50% "investigate" setting. Decide the likely cause and how to report the pair.
4. **Late extraction:** mercury in SB3-15 was extracted 30 days after sampling, but the recommended maximum is 28 days. Decide whether to accept, qualify or re-sample that result.
5. **PFOS in an equipment blank:** rinsate blank RB1 has PFOS 0.004 ug/L (4 x its LOR). The same Hand auger A was used for SB2-0.2, whose sum of PFOS and PFHxS is marginal at 1.13 x the HIL A value. Decide whether that result comes from the soil or from carry-over on the auger.
6. **Detection limit too high at MB3:** PFOS is reported as <0.01 ug/L, but the current national drinking-water value is 0.008 ug/L, so it can't be confirmed as below it. Under NEMP 3.0, the sum of PFOS and PFHxS is not above 0.07 ug/L. Decide whether to re-analyse with a lower-LOR method, and which rule applies.

**Checked and not flagged (24 items):**
- 14 sample IDs match exactly.
- SB4-1.0 is on HOLD at the lab, not missing.
- All dates agree when read day first.
- The different spellings of µg/L are the same unit.
- The other duplicate RPDs are fine, including MB2/QC2 after conversion: PFOS 8.2%, PFHxS 5.4%.
- All other holding times are within the limit. SB3-0.5 mercury is exactly 28 days, which is not over.
- The field, container and trip blanks are all below the LOR.
- The other non-detect LORs are not above their values.

**Not checked:**
- PFHxA LORs, because no investigation level for PFHxA is loaded.
- Blanks were not compared with investigation levels.
- **Detected results were not screened against any value.** So this list of 6 items is not a list of results above a guideline.

## 2. Review item 2: what would have gone wrong without the conversion

MB2's four September 2025 results (lab_results.csv rows 52 to 55) were converted exactly. For example, PFOS 38 ng/L became 0.038 ug/L and PFHxS 19 ng/L became 0.019 ug/L.

- **False problem with the duplicate:** MB2's field duplicate QC2 is reported in ug/L. Without the conversion, PFOS would have been compared as 38 against 0.035, giving an RPD of **199.6% instead of 8.2%**. A pair that actually agrees well would have been flagged for investigation, and the scientist would have chased a sampling or lab problem that doesn't exist.
- **Every MB2 value 1,000 times too high:** the other MB2 rounds and every drinking-water value are in ug/L. So the unconverted number would have been 1,000 times too large in any trend or screening comparison. This point is my arithmetic from the tool's rule that 1 ug/L = 1,000 ng/L; the tool itself only worked out the duplicate case.

Still for the scientist to decide: whether ng/L was what the lab meant.

## 3. Paragraph: PFOS and PFHxS at MB2, November 2024 vs May 2026

**Draft with placeholders:**
> At well MB2, PFOS showed {change|PFOS|MB2|Nov 2024|May 2026} between November 2024 and May 2026, from {PFOS|MB2|Nov 2024} to {PFOS|MB2|May 2026}. Over the same period PFHxS showed {change|PFHxS|MB2|Nov 2024|May 2026}, from {PFHxS|MB2|Nov 2024} to {PFHxS|MB2|May 2026}.

**Final paragraph:**
> At well MB2, PFOS showed a fall of 0.035 ug/L (85.4%) between November 2024 and May 2026, from 0.041 ug/L to 0.006 ug/L. Over the same period PFHxS showed a fall of 0.009 ug/L (50.0%), from 0.018 ug/L to 0.009 ug/L.

**Checker's verdicts:** 8 items checked, 8 consistent, 0 needing judgement, 2 not checked.

| Sentence | Item | Verdict | Traced to |
|---|---|---|---|
| 1 | "fall" (PFOS) | consistent | 0.041 ug/L on 19 Nov 2024 to 0.006 ug/L on 5 May 2026 |
| 1 | 0.035 ug/L | consistent | difference computed by code |
| 1 | 0.041 ug/L | consistent | mb2_round2_lab.csv row 14 (SYN-241119) |
| 1 | 0.006 ug/L | consistent | mb2_round4_lab.csv row 14 (SYN-260505) |
| 2 | "fall" (PFHxS) | consistent | 0.018 ug/L to 0.009 ug/L |
| 2 | 0.009 ug/L (the fall) | consistent | difference computed by code |
| 2 | 0.018 ug/L | consistent | mb2_round2_lab.csv row 15 |
| 2 | 0.009 ug/L (May 2026) | consistent | mb2_round4_lab.csv row 15 |

**Not checked:** 85.4 and 50.0. The checker doesn't trace numbers that have no concentration unit. Both percentages were computed by code in fill_numbers (rounded to one decimal place), and its source list shows the working.

In sentence 2, the fall and the May 2026 result are both 0.009 ug/L. That's a coincidence in the data; the checker traced each one to its own source.

## 4. What a detailed site investigation report should include

- **Main passage:** DWER, *Guideline: Assessment and management of contaminated sites* (November 2021, last updated 16 June 2023), section 9 "Detailed site investigation", **printed page 29 (PDF page 34)**. It says to compile the report following Schedule B2 of the ASC NEPM, and points to Appendix A for what to include.
  - Link: https://www.wa.gov.au/system/files/2023-05/guideline-assessment-and-management-of-contaminated-sites.pdf#page=34
- **The checklist:** Appendix A, Table A1, "Reporting checklist for preliminary site investigation reports and detailed site investigation reports", **printed page 106 (PDF page 111)**. The tool inferred the printed page number from the pages either side.
  - Link: https://www.wa.gov.au/system/files/2023-05/guideline-assessment-and-management-of-contaminated-sites.pdf#page=111
- **Report title:** section 6.5, printed page 19 (PDF page 24). The title must identify the site, the type of report and the month and year of the work.

The document's licence only allows short excerpts, so read the checklist items on the page itself. Which document or edition applies to the site is the scientist's call.

None of this decides whether the site is contaminated. Guideline values are investigation levels, and both drinking-water rules are shown side by side. The tidy output was too long to display in full, so I read the saved copy of that same result.
