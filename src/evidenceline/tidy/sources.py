"""The rule behind each QA check, with its source. Quoted words are verbatim from the source named.

Sources were read on 2026-09-23 (research: lab-data-and-sample-site.md, sections 3 and 4). NEPM = National
Environment Protection (Assessment of Site Contamination) Measure 1999, as amended 2013 (compilation F2013C00288).
"""

from __future__ import annotations

SAMPLE_IDS = (
    'DWER (2021) Guideline: Assessment and management of contaminated sites, PDF page 37: sample names should "be '
    'consistent in report text, site plans, tables, chain of custody forms and laboratory analytical certificates".'
)

UNITS = (
    'PFAS NEMP 3.0, Table B2 (PDF page 220), gives water limits of reporting in "Water (µg/L)" and soil in '
    '"Soil (mg/kg)". Conversions are arithmetic: 1 ug/L = 1,000 ng/L and 1 mg/L = 1,000 ug/L.'
)

DUPLICATES = (
    'NEPM Schedule B3 s3.5.1: "If results show greater than 30% difference, a review should be conducted of the '
    'cause (e.g. instrument calibration, extraction efficiency, appropriateness of the method used, etc.)." '
    'GHD (2014) Perth Airport Rail Link SAQP s9.1.3, quoting AS 4482.1: "typical RPDs are expected to range '
    'between 30% and 50%", and for a non-detect in a pair: "the sample will be assigned the concentration of the '
    'LOR for RPD calculation purposes." GHD (2014) Table 6: "< 10 x limit of reporting (LOR) No limits" (written '
    "for laboratory duplicates; applied here to field duplicates as a stated setting)."
)

REPORT_HIGHEST = (
    'NEPM Schedule B2 s19.9 asks reports to show "the highest measurement result wherever replicate measurements '
    'are taken (or all measurement results for each sample)".'
)

HOLDING = (
    'NEPM Schedule B3 s4.4.1 (PDF pages 34 to 35): "The holding times in Table 1 are the recommended maximum times '
    'before sample extraction." Table 1 (soil): "Mercury & Chromium VI ... 28 days"; other metals 6 months. PFAS '
    "holding times are not in PFAS NEMP 3.0; laboratory guidance (Envirolab Form 347 V023) gives 14 days (standard) "
    "or 28 days (trace) for water and 60 days (standard) or 28 days (trace) for soil. The shorter time is used here."
)

METALS_TOLERANCE = (
    'NEPM Schedule B3 s4.4.1: "Analytes such as metals and some semi-volatile organics (including PCBs, PAHs) are '
    "persistent in the environment and are not likely to change significantly after sampling; analysis slightly "
    "outside of these holding times is not likely to cause significant variation in results if samples have been "
    'handled and stored correctly." s4.4: "State regulatory agencies may specify different holding times or '
    'container types; in which case the jurisdictional requirements should be followed."'
)

BLANKS = (
    'PFAS NEMP 3.0, B.3.5: a rinsate or equipment blank is "used where PFAS-free water is poured over or through '
    'decontaminated field sampling equipment", and "Blanks should be analysed for the same analytes as all other '
    'samples." NEPM Schedule B2 s8.2.4.3: "Samples of the rinsate should be included in the QA/QC program."'
)

LOR = (
    'PFAS NEMP 3.0, Appendix B.3.4 (PDF page 219): "The limit of reporting (LOR) for an analytical method should '
    "be lower than the benchmark (such as water quality objective, water quality guidelines or trigger values) to "
    'which the results will be compared." and "If the LOR is higher than the benchmark, alternative methods of '
    'sampling and/or analysis should be investigated (for example, passive sampling)." NEPM Schedule B2 s18.2.5: '
    '"confirmation that the action level exceeds measurement detection limits".'
)

INVESTIGATION_LEVEL = (
    "A guideline value is an investigation level, not a finding that water is unsafe or that a site is contaminated."
)

RULE_CHOICE = (
    "Two drinking-water rules are shown side by side: PFAS NEMP 3.0 (named on the WA government PFAS page as "
    "adopted in WA) and the current national values (NEMP 3.1, ADWG updated 2025). Which applies is the "
    "scientist's call."
)
