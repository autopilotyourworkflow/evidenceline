# Evidenceline's hosted connector inside Claude Code, 24 Sep 2026

A real Claude Code session (headless, `claude -p`, version 2.1.281) connected only to the hosted connector at
`https://evidenceline.autopilotyourworkflow.com/mcp` (HTTP transport, no install), with four of its tools allowed:
`lookup_limit`, `tidy_lab_files`, `search_guidelines` and `get_results`. The request went through the website's
Cloudflare Worker to the API service on Render, which runs the same nine read-only tools as the local server.

The prompt:

> Use only the evidenceline tools. 1) Look up the current drinking-water guideline value for PFOS. 2) Tidy the sample
> lab files and tell me how many results and which wells. 3) Search the guidelines for what a conceptual site model
> is and give the page. Answer in five short lines with the tool name used for each.

Everything below is Claude's answer, unedited.

---

1. **lookup_limit**: Under the "current" rule (ADWG updated 2025, listed in NEMP 3.1), PFOS is **0.008 ug/L**, compared on its own (Table 4, printed p. 49).
2. **lookup_limit**: Under "nemp-3.0", the rule the WA government page names as adopted, the value is **0.07 ug/L**. It applies to PFOS alone, PFHxS alone and the two added together (Table 4, footnote a, PDF p. 57). Choosing between the two rules is your call, and both values are investigation levels.
3. **tidy_lab_files**: There are **73 results** from 15 samples (41 soil, 32 water; the data is synthetic). The groundwater wells are **MB1, MB2 and MB3**, and QC2 is a field duplicate of MB2.
4. **tidy_lab_files**: 6 items need your decision:
   - Sample id written two ways: SB3-15 in the lab file, SB3-1.5 on the chain of custody.
   - MB2 was reported in ng/L while the other water results are in ug/L.
   - Lead in the duplicate pair SB4-0.3 and QC1 differs by 61.8%.
   - Mercury in SB3-15 was extracted after 30 days (limit 28).
   - PFOS was detected in rinsate blank RB1.
   - MB3's PFOS reporting limit is above 0.008 ug/L.

   A further 24 things were checked and not flagged.
5. **search_guidelines**: DWER, *Assessment and management of contaminated sites* (Nov 2021), s7.1. A conceptual site model describes the environmental setting, where contaminants come from and how they move. It's on **printed p. 22 (PDF p. 27)**.
