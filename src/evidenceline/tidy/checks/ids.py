"""Sample ids across the lab file, chain of custody and field sheet, and the sampling dates they carry.

Ids match only when they are identical. A near match (same letters and digits once punctuation is ignored, same
matrix, same sampling date and time) is suggested for the scientist to confirm; results are never merged on a
suggestion.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from evidenceline.dataset import long_date
from evidenceline.tidy import sources
from evidenceline.tidy.dates import Stamp
from evidenceline.tidy.findings import (
    Draft,
    Evidence,
    Outcome,
    Passed,
    coc_evidence,
    field_evidence,
    join_words,
    lab_evidence,
    plural,
)
from evidenceline.tidy.records import CocRow, LabRow
from evidenceline.tidy.site import SiteData

RULE = (
    "Every sample id in the lab file must match a chain-of-custody id exactly, and every chain-of-custody id must "
    "be on the field sheet. A sample marked HOLD may have no lab results. A near match (same letters and digits, "
    "same matrix, same sampling date and time) is suggested, never merged. Sampling dates and times must agree "
    "between files, all read day first."
)


@dataclass(frozen=True, slots=True)
class IdReport:
    outcome: Outcome
    suggestions: Mapping[str, str]
    """Lab id -> chain-of-custody id it probably is (not confirmed)."""
    unmatched_lab_ids: frozenset[str]
    """Lab ids with no identical chain-of-custody id (with or without a suggestion)."""


def compact(sample_id: str) -> str:
    """Letters and digits only, lower case: ``SB3-1.5`` and ``SB3-15`` both become ``sb315``."""
    return re.sub(r"[^0-9a-z]", "", sample_id.lower())


def _when(stamp: Stamp) -> str:
    return long_date(stamp.date) + (f" {stamp.time:%H:%M}" if stamp.time else "")


def _lab_span(rows: list[LabRow]) -> str:
    first, last = rows[0].row, rows[-1].row
    return f"row {first}" if first == last else f"rows {first} to {last}"


def _suggest(lab_id: str, rows: list[LabRow], candidates: list[CocRow]) -> CocRow | None:
    sampled = rows[0].sampled
    found = [
        coc
        for coc in candidates
        if compact(coc.sample_id) == compact(lab_id)
        and coc.matrix == rows[0].matrix
        and coc.sampled.agrees_with(sampled)
    ]
    return found[0] if len(found) == 1 else None


def _suggestion_draft(site: SiteData, lab_id: str, rows: list[LabRow], coc: CocRow) -> Draft:
    field = site.field_row(coc.sample_id)
    also_field = " and the field sheet" if field is not None else ""
    evidence = [lab_evidence(row, f"{lab_id}, {row.analyte} (lab id as written)") for row in rows]
    evidence.append(coc_evidence(coc))
    if field is not None:
        evidence.append(field_evidence(field))
    found = (
        f"The lab file has {plural(len(rows), 'result')} for {lab_id} ({_lab_span(rows)}). No sample of that name is "
        f"on the chain of custody or the field sheet. {coc.sample_id} is on the chain of custody{also_field}, is not "
        f"marked HOLD, and has no lab results. The two agree on everything else checked: the same sampling date and "
        f"time ({_when(coc.sampled)}), the same matrix ({coc.matrix}), and the same letters and digits once "
        f"punctuation is ignored. The results stay under the lab's id, {lab_id}, until this is confirmed."
    )
    return Draft(
        check="sample ids",
        title=f"Sample id written two ways: {lab_id} in the lab file, {coc.sample_id} on the chain of custody",
        found=found,
        evidence=tuple(evidence),
        rule=RULE,
        source=sources.SAMPLE_IDS,
        scientist_decides=(
            f"The scientist decides whether {lab_id} is {coc.sample_id}, for example from the lab's sample receipt "
            "record, and whether to ask the lab to reissue the report with the corrected id."
        ),
        samples=(lab_id, coc.sample_id),
    )


def _missing_draft(sample_id: str, where: str, missing_from: str, evidence: list[Evidence]) -> Draft:
    return Draft(
        check="sample ids",
        title=f"{sample_id} is in the {where} but not in the {missing_from}",
        found=(
            f"{sample_id} appears in the {where} and not in the {missing_from}, and no near match was found. Nothing "
            "was merged or dropped."
        ),
        evidence=tuple(evidence),
        rule=RULE,
        source=sources.SAMPLE_IDS,
        scientist_decides=f"The scientist decides how {sample_id} should be recorded in each file.",
        samples=(sample_id,),
    )


def _date_checks(site: SiteData, lab: Mapping[str, list[LabRow]]) -> tuple[list[Draft], list[Passed]]:
    drafts: list[Draft] = []
    agreed = 0
    for coc in site.coc:
        stamps: list[tuple[str, Stamp, Evidence]] = [("chain of custody", coc.sampled, coc_evidence(coc))]
        if coc.sample_id in lab:
            rows = lab[coc.sample_id]
            first = rows[0]
            # Every lab row carries its own sampling date: compare all of them, not just the first.
            differing = [row for row in rows[1:] if not row.sampled.agrees_with(first.sampled)]
            for row in [first, *differing]:
                where, analyte = (f"lab file row {row.row}", f", {row.analyte}") if differing else ("lab file", "")
                detail = f"{row.sample_id}{analyte}, sampled {row.sampled.written}"
                stamps.append((where, row.sampled, lab_evidence(row, detail)))
        field = site.field_row(coc.sample_id)
        if field is not None:
            stamps.append(("field sheet", field.sampled, field_evidence(field)))
        if all(a.agrees_with(b) for _, a, _ in stamps for _, b, _ in stamps):
            agreed += 1
            continue
        listed = join_words([f"{where} {_when(stamp)} (written {stamp.written!r})" for where, stamp, _ in stamps])
        drafts.append(
            Draft(
                check="sample ids",
                title=f"Sampling date or time for {coc.sample_id} differs between files",
                found=f"The files give different sampling dates or times for {coc.sample_id}: {listed}.",
                evidence=tuple(evidence for _, _, evidence in stamps),
                rule=RULE,
                source=sources.SAMPLE_IDS,
                scientist_decides=f"The scientist decides which sampling date and time is right for {coc.sample_id}.",
                samples=(coc.sample_id,),
            )
        )
    examples = _format_examples(site, lab)
    passed = [
        Passed(
            check="sample ids",
            what=(
                f"Sampling dates and times agree across the files for {plural(agreed, 'sample')}, each read day first "
                f"({examples}). No date was read month first."
            ),
        )
    ]
    return drafts, passed


def _format_examples(site: SiteData, lab: Mapping[str, list[LabRow]]) -> str:
    for coc in site.coc:
        field = site.field_row(coc.sample_id)
        if coc.sample_id in lab and field is not None and coc.sampled.time is not None:
            return (
                f"for {coc.sample_id}: lab file {lab[coc.sample_id][0].sampled.written!r}, chain of custody "
                f"{coc.sampled.written!r}, field sheet {field.sampled.written!r}"
            )
    return "formats as written in each file"


def check_sample_ids(site: SiteData) -> IdReport:
    lab = site.lab_samples()
    coc = {row.sample_id: row for row in site.coc}
    field = {row.sample_id: row for row in site.field}
    drafts: list[Draft] = []
    passed: list[Passed] = []

    lab_only = [sample for sample in lab if sample not in coc]
    no_results = [row for row in site.coc if row.sample_id not in lab]
    open_candidates = [row for row in no_results if not row.hold]
    suggestions: dict[str, str] = {}
    for lab_id in lab_only:
        match = _suggest(lab_id, lab[lab_id], open_candidates)
        if match is None:
            rows = lab[lab_id]
            drafts.append(_missing_draft(lab_id, "lab file", "chain of custody", [lab_evidence(row) for row in rows]))
            continue
        suggestions[lab_id] = match.sample_id
        open_candidates.remove(match)
        drafts.append(_suggestion_draft(site, lab_id, lab[lab_id], match))
    for row in open_candidates:
        drafts.append(
            _missing_draft(row.sample_id, "chain of custody (not marked HOLD)", "lab file", [coc_evidence(row)])
        )
    for row in site.coc:
        if row.sample_id not in field:
            drafts.append(_missing_draft(row.sample_id, "chain of custody", "field sheet", [coc_evidence(row)]))
    for row in site.field:
        if row.sample_id not in coc:
            drafts.append(_missing_draft(row.sample_id, "field sheet", "chain of custody", [field_evidence(row)]))

    matched = [sample for sample in lab if sample in coc and sample in field]
    passed.append(
        Passed(
            check="sample ids",
            what=(
                f"{plural(len(matched), 'lab sample id')} match the chain of custody and the field sheet exactly, "
                "including the blind duplicates and blanks, which the lab reports like any other sample."
            ),
        )
    )
    for row in no_results:
        if row.hold:
            evidence = [coc_evidence(row)]
            if row.sample_id in field:
                evidence.append(field_evidence(field[row.sample_id]))
            note = f" ({row.comments!r})" if row.comments else ""
            passed.append(
                Passed(
                    check="sample ids",
                    what=(
                        f"{row.sample_id} is on the chain of custody marked HOLD{note} and has no lab results: held "
                        "at the lab, not missing."
                    ),
                    evidence=tuple(evidence),
                )
            )
    date_drafts, date_passed = _date_checks(site, lab)
    outcome = Outcome(
        check="sample ids",
        what_was_checked=(
            f"{plural(len(lab), 'lab sample id')}, {plural(len(site.coc), 'chain-of-custody row')} and "
            f"{plural(len(site.field), 'field-sheet row')}: ids matched exactly, HOLD samples, and sampling dates "
            "and times compared between files."
        ),
        rule=RULE,
        source=sources.SAMPLE_IDS,
        checked=len(set(lab) | set(coc) | set(field)),
        drafts=tuple(drafts + date_drafts),
        passed=tuple(passed + date_passed),
    )
    return IdReport(outcome=outcome, suggestions=suggestions, unmatched_lab_ids=frozenset(lab_only))
