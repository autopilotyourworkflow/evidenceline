"""Blanks: any detection is flagged, and the field sheet links it to the samples that could share its cause.

A rinsate blank links to samples taken with the same equipment on the same day; a field blank to samples at the
same location that day; trip and container blanks to every sample that day. A linked result above a value by no
more than the marginal factor is marked marginal. No result is adjusted: blank water (ug/L) and soil (mg/kg) cannot
be subtracted from one another, and the question is the scientist's.
"""

from __future__ import annotations

from decimal import Decimal

from evidenceline.dataset import long_date
from evidenceline.tidy import sources
from evidenceline.tidy.arith import multiple
from evidenceline.tidy.config import QaConfig
from evidenceline.tidy.criteria import Comparison, compare
from evidenceline.tidy.findings import (
    Draft,
    Evidence,
    Outcome,
    Passed,
    Skipped,
    field_evidence,
    join_words,
    lab_evidence,
    plural,
)
from evidenceline.tidy.records import FieldRow, LabRow
from evidenceline.tidy.site import SiteData
from evidenceline.units import fmt


def rule_text(config: QaConfig) -> str:
    return (
        "Any detection in a blank is flagged with its multiple of the LOR. Linked samples come from the field "
        "sheet: the same equipment on the same day (rinsate blank), the same location on the same day (field "
        "blank), or the same day (trip and container blanks). A linked result above a value by no more than "
        f"{fmt(config.blank_marginal_factor)} x is marked marginal; {fmt(config.blank_marginal_factor)} x is a "
        "demonstration setting, not a published rule. No result is adjusted."
    )


def linked_samples(site: SiteData, blank: FieldRow) -> list[FieldRow]:
    """Field-sheet samples (not blanks) that share the blank's equipment, location or day."""
    same_day = [row for row in site.field if not row.is_blank and row.sampled.date == blank.sampled.date]
    if blank.qa_type == "rinsate_blank":
        wanted = blank.equipment.strip().lower()
        return [row for row in same_day if wanted and row.equipment.strip().lower() == wanted]
    if blank.qa_type == "field_blank":
        return [row for row in same_day if blank.location and row.location == blank.location]
    return same_day


def _closest(comparisons: list[Comparison]) -> Comparison:
    return max(comparisons, key=lambda c: c.lower / c.criterion.value)


def _linked_summary(
    site: SiteData, linked: list[FieldRow], analyte: str, config: QaConfig
) -> tuple[list[str], list[Comparison]]:
    lab = site.lab_samples()
    lines: list[str] = []
    marginal: list[Comparison] = []
    not_analysed: list[str] = []
    for sample in linked:
        rows = lab.get(sample.sample_id)
        if rows is None:
            coc = site.coc_row(sample.sample_id)
            held = " (held at the lab)" if coc is not None and coc.hold else ""
            lines.append(f"{sample.sample_id} has no lab results{held}.")
            continue
        results = {row.analyte: row for row in rows}
        if analyte not in results:
            not_analysed.append(sample.sample_id)
            continue
        comparisons = [
            c
            for criterion in site.criteria_for(results[analyte].matrix)
            if analyte in criterion.members
            for c in compare(criterion, results)
            if analyte in (part.analyte for part in c.parts)
        ]
        if not comparisons:
            lines.append(f"{sample.sample_id}: {analyte} {results[analyte].shown_both()}, no value loaded to compare.")
            continue
        closest = _closest(comparisons)
        times = fmt(multiple(closest.lower, closest.criterion.value))
        if closest.status == "above" and closest.lower <= config.blank_marginal_factor * closest.criterion.value:
            marginal.append(closest)
            verdict = f"{times} x the value: marginal (above it by no more than {fmt(config.blank_marginal_factor)} x)"
        elif closest.status == "above":
            verdict = f"{times} x the value, above it by more than {fmt(config.blank_marginal_factor)} x"
        elif closest.status == "not above":
            verdict = "not above the value"
        else:
            verdict = "not confirmed against the value, because a non-detect's LOR leaves it open"
        lines.append(
            f"{sample.sample_id}: {closest.quantity} {closest.arithmetic()} against {closest.against()}, {verdict}."
        )
    if not_analysed:
        lines.append(
            f"{join_words(not_analysed)} {'was' if len(not_analysed) == 1 else 'were'} not analysed for {analyte}."
        )
    return lines, marginal


def _draft(site: SiteData, blank: FieldRow, detects: list[LabRow], config: QaConfig) -> Draft:
    linked = linked_samples(site, blank)
    by = {
        "rinsate_blank": f"{blank.equipment} on the same day",
        "field_blank": f"location {blank.location} on the same day",
    }.get(blank.qa_type, "the same day")
    kind = blank.qa_type.replace("_", " ")
    shown = join_words(
        [
            f"{row.analyte} {row.shown_both()}, {fmt(multiple(row.value or Decimal(0), row.lor))} x its LOR "
            f"of {fmt(row.lor)} {row.canonical_unit}"
            for row in detects
        ]
    )
    parts = [
        f"{blank.sample_id} ({kind}, {long_date(blank.sampled.date)}) has {shown}.",
        f"The field sheet lists {by} for {join_words([row.sample_id for row in linked]) or 'no other sample'}.",
    ]
    evidence: list[Evidence] = [lab_evidence(row) for row in detects]
    evidence.append(field_evidence(blank))
    evidence += [field_evidence(row) for row in linked]
    marginal: list[Comparison] = []
    for row in detects:
        lines, found = _linked_summary(site, linked, row.analyte, config)
        parts += lines
        marginal += found
    for comparison in marginal:
        evidence += [lab_evidence(part) for part in comparison.parts]
        field = site.field_row(comparison.parts[0].sample_id)
        if field is not None and field.notes:
            parts.append(f"Field sheet note for {field.sample_id}: {field.notes!r}.")
    marginal_ids = list(dict.fromkeys(c.parts[0].sample_id for c in marginal))
    analytes = join_words([row.analyte for row in detects])
    title = f"{analytes} detected in {kind} {blank.sample_id}"
    if marginal_ids:
        title += f"; linked to a marginal result at {join_words(marginal_ids)}"
    soil_linked = any(row.matrix == "soil" for row in linked) and any(row.matrix == "water" for row in detects)
    adjust = (
        " Blank water (ug/L) and soil (mg/kg) results cannot be compared directly, so no result was adjusted."
        if soil_linked
        else " No result was adjusted."
    )
    decides = (
        f"The scientist decides whether the marginal result at {join_words(marginal_ids)} reflects the sample or "
        "carry-over from the equipment, and whether it needs re-sampling."
        if marginal_ids
        else f"The scientist decides whether the {analytes} detection in {blank.sample_id} affects any linked result."
    )
    return Draft(
        check="blanks",
        title=title,
        found=" ".join(parts),
        evidence=tuple(evidence),
        rule=rule_text(config),
        source=sources.BLANKS,
        scientist_decides=decides + adjust,
        samples=(blank.sample_id, *marginal_ids),
    )


def check_blanks(site: SiteData, config: QaConfig) -> Outcome:
    lab = site.lab_samples()
    blanks = [row for row in site.field if row.is_blank]
    drafts: list[Draft] = []
    passed: list[Passed] = []
    skipped: list[Skipped] = []
    checked = 0
    for blank in blanks:
        rows = lab.get(blank.sample_id)
        if rows is None:
            skipped.append(Skipped("blanks", blank.sample_id, "No lab results under this id."))
            continue
        checked += len(rows)
        detects = [row for row in rows if row.detected]
        if detects:
            drafts.append(_draft(site, blank, detects, config))
            continue
        passed.append(
            Passed(
                check="blanks",
                what=(
                    f"{blank.sample_id} ({blank.qa_type.replace('_', ' ')}): {join_words([r.analyte for r in rows])} "
                    f"all below the LOR."
                ),
                evidence=(field_evidence(blank),),
            )
        )
    return Outcome(
        check="blanks",
        what_was_checked=f"{plural(checked, 'result')} in {plural(len(blanks), 'blank')} from the field sheet.",
        rule=rule_text(config),
        source=sources.BLANKS,
        checked=checked,
        drafts=tuple(drafts),
        passed=tuple(passed),
        skipped=tuple(skipped),
    )
