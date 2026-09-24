# Example: Claude Code checking a draft with the Evidenceline MCP server (2026-09-24)

Run with Claude Code in headless mode, the Evidenceline server added at local scope, and only the Evidenceline tools allowed.
Synthetic data (fictional site FDS-01, well MB2); real guideline values.

**Prompt**

> Use the evidenceline tools (not your own judgement) to check this draft paragraph about well MB2, then tell me in plain
> English which sentences are wrong and why, and suggest a corrected version of each wrong sentence. Paragraph: "PFOS at well
> MB2 rose between March 2024 and November 2024. In September 2025 PFHxS was 0.019 ug/L, above its drinking-water guideline.
> By May 2026 PFOS had fallen to 0.006 ug/L."

**Outcome (summarised from Claude's answer)**

- Sentence 1 wrong: PFOS fell from 0.062 ug/L (12 March 2024) to 0.041 ug/L (19 November 2024).
- Sentence 2: the number is right; "above its drinking-water guideline" is wrong under both rules (current: 0.019 is not above
  0.03; NEMP 3.0: sum 0.038 + 0.019 = 0.057 is not above 0.07). Claude kept both rules and left the choice to the author.
- Sentence 3: the number is right; "fallen" was listed as not checked (only one date given). After naming the previous round,
  the fall checks out (0.038 to 0.006, 84.2% lower).
- The corrected paragraph was re-checked: 13 items confirmed, none left unchecked.
- Claude added that being below a guideline value only means no investigation is triggered; it does not show the water is safe.
