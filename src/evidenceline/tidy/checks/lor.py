"""Limits of reporting against investigation levels: can a non-detect show the result is below the value?

A non-detect whose LOR is above a value is "not confirmed", never "below". A LOR equal to the value counts as not
above it. Sums use exact bounds (non-detects at zero and at their LOR). For water both drinking-water rules are
shown side by side; the tool never picks one.
"""

from __future__ import annotations

from evidenceline.tidy import sources
from evidenceline.tidy.criteria import RULE_NAMES, Comparison, compare
from evidenceline.tidy.findings import Draft, Outcome, Passed, Skipped, join_words, lab_evidence, plural
from evidenceline.tidy.records import LabRow
from evidenceline.tidy.site import SiteData
from evidenceline.units import fmt

RULE = (
    "A non-detect whose LOR is above a value cannot show the result is below it: it is 'not confirmed', never "
    "'below'. A LOR equal to the value counts as not above it. For a sum, a non-detect counts as zero (lower bound) "
    "and as its LOR (upper bound); if the value lies between the two, the sum is not confirmed. Soil results are "
    "compared with HIL A values; water results with both drinking-water rules, side by side. Blanks are not compared."
)


def explain(comparison: Comparison) -> str:
    """One sentence on what the LOR allows us to say about ``comparison``."""
    text = _explain(comparison)
    return text[:1].upper() + text[1:]


def _explain(comparison: Comparison) -> str:
    value = comparison.criterion.shown()
    if len(comparison.parts) == 1:
        part = comparison.parts[0]
        subject = f"{comparison.quantity} {part.shown_both()} against {comparison.against()}"
        if comparison.status == "not confirmed":
            return (
                f"{subject}: the LOR ({fmt(part.lor)} {part.canonical_unit}) is above the value, so it cannot be "
                f"confirmed that {part.analyte} is below it (not confirmed)."
            )
        return f"{subject}: the LOR is not above the value."
    subject = f"{comparison.quantity} {comparison.arithmetic()} against {comparison.against()}"
    if comparison.status == "not confirmed":
        return f"{subject}: {value} lies between the two bounds, so it cannot be confirmed either way (not confirmed)."
    if comparison.status == "above":
        return f"{subject}: above the value even with non-detects at zero."
    return f"{subject}: not above the value even with non-detects at their LOR."


def _non_detect_comparisons(site: SiteData, rows: list[LabRow]) -> list[Comparison]:
    results = {row.analyte: row for row in rows}
    return [
        comparison
        for criterion in site.criteria_for(rows[0].matrix)
        for comparison in compare(criterion, results)
        if comparison.has_non_detect
    ]


def _draft(site: SiteData, sample: str, flagged: list[Comparison], others: list[Comparison]) -> Draft:
    analytes = list(dict.fromkeys(part.analyte for c in flagged for part in c.parts if not part.detected))
    first = flagged[0]
    related = [c for c in others if any(part.analyte in analytes for part in c.parts)]
    rows = list(dict.fromkeys(part for c in flagged + related for part in c.parts))
    comments = sorted({row.comment for row in rows if row.comment})
    note = f" The lab comment reads: {join_words([repr(c) for c in comments])}." if comments else ""
    found = " ".join([f"{sample}:", *(explain(c) for c in flagged + related)]) + note
    water = rows[0].matrix == "water"
    rule = first.criterion.rule
    title_rule = f" under {RULE_NAMES.get(rule, rule)}" if rule else ""
    decides = (
        "The scientist decides whether to ask the lab to re-analyse by a method with a lower LOR (for example "
        "trace level)"
        + (
            ", which drinking-water rule applies, and whether drinking water is the right yardstick for this "
            "groundwater."
            if water
            else ", and which land use applies."
        )
    )
    return Draft(
        check="LOR against criteria",
        title=(
            f"{sample} {join_words(analytes)}: LOR above {first.criterion.scenario} value "
            f"({first.criterion.shown()}){title_rule}; not confirmed"
        ),
        found=found,
        evidence=tuple(lab_evidence(row) for row in rows),
        rule=RULE,
        source=sources.LOR,
        scientist_decides=f"{decides} {sources.RULE_CHOICE}" if water else decides,
        samples=(sample,),
    )


def check_lor(site: SiteData) -> Outcome:
    drafts: list[Draft] = []
    passed: list[Passed] = []
    no_value: dict[str, list[str]] = {}
    blanks = {row.sample_id for row in site.field if row.is_blank}
    compared = 0
    for sample, rows in site.lab_samples().items():
        if sample in blanks:
            continue
        comparisons = _non_detect_comparisons(site, rows)
        for row in rows:
            if not row.detected and not any(row.analyte in c.members for c in site.criteria_for(row.matrix)):
                no_value.setdefault(row.analyte, []).append(sample)
        compared += len({part.row for c in comparisons for part in c.parts if not part.detected})
        if not comparisons:
            continue
        flagged = [c for c in comparisons if c.status == "not confirmed"]
        if flagged:
            drafts.append(_draft(site, sample, flagged, [c for c in comparisons if c.status != "not confirmed"]))
            flagged_analytes = {part.analyte for c in flagged for part in c.parts if not part.detected}
            rest = [c for c in comparisons if not any(p.analyte in flagged_analytes for p in c.parts)]
        else:
            rest = comparisons
        if rest:
            evidence = tuple(dict.fromkeys(lab_evidence(p) for c in rest for p in c.parts if not p.detected))
            passed.append(Passed("LOR against criteria", " ".join([f"{sample}:", *map(explain, rest)]), evidence))
    skipped = [
        Skipped(
            "LOR against criteria",
            f"{analyte} ({plural(len(samples), 'non-detect')}: {join_words(samples)})",
            f"No investigation level for {analyte} is loaded, so its LOR was not compared with one.",
        )
        for analyte, samples in no_value.items()
    ]
    detected = [row for row in site.lab if row.detected and row.sample_id not in blanks]
    if detected:
        skipped.append(
            Skipped(
                "LOR against criteria",
                f"Detected results against investigation levels ({plural(len(detected), 'detected result')} in "
                "samples)",
                "This check compares only the LOR of non-detects with investigation levels. Detected results were not "
                "screened against any value here, so the review items are not a list of results above a value. For "
                "well MB2, compare_rules screens one round under both drinking-water rules; the other results are "
                "not screened by any tool yet.",
            )
        )
    if blanks:
        skipped.append(
            Skipped(
                "LOR against criteria",
                f"Blanks ({join_words(sorted(blanks))})",
                "Blanks are checked for detections, not compared with investigation levels.",
            )
        )
    return Outcome(
        check="LOR against criteria",
        what_was_checked=(
            f"{plural(compared, 'non-detect')} in samples (not blanks), compared with the soil HIL A values and with "
            "both drinking-water rules."
        ),
        rule=RULE,
        source=sources.LOR,
        checked=compared,
        drafts=tuple(drafts),
        passed=tuple(passed),
        skipped=tuple(skipped),
    )
