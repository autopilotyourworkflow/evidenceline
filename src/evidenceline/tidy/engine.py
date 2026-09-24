"""Run the six checks on one site and assemble the tidy result: combined table, counts, numbered review items,
what was checked and not flagged, and what was not checked. Deterministic; no language model is involved."""

from __future__ import annotations

from collections.abc import Iterable
from functools import cache
from typing import Literal

from evidenceline.errors import EvidencelineError
from evidenceline.tidy import sources
from evidenceline.tidy.checks.blanks import check_blanks
from evidenceline.tidy.checks.duplicates import check_duplicates
from evidenceline.tidy.checks.holding import check_holding, days_held
from evidenceline.tidy.checks.ids import IdReport, check_sample_ids
from evidenceline.tidy.checks.lor import check_lor
from evidenceline.tidy.checks.units import check_units
from evidenceline.tidy.config import DEFAULT_CONFIG, QaConfig
from evidenceline.tidy.findings import Draft, Evidence, Outcome, plural
from evidenceline.tidy.models import (
    CheckedNotFlagged,
    CheckRun,
    CountRow,
    EvidenceRow,
    FileRead,
    NotChecked,
    QaSettings,
    ReviewItem,
    ReviewItemDetail,
    SourceLine,
    TidyResult,
    TidyRow,
)
from evidenceline.tidy.records import COC_FILE, FIELD_FILE, LAB_FILE, LabRow
from evidenceline.tidy.site import SiteData, packaged_site, resolve_site
from evidenceline.units import fmt

_ROLES: dict[str, Literal["lab results", "chain of custody", "field sheet"]] = {
    LAB_FILE: "lab results",
    COC_FILE: "chain of custody",
    FIELD_FILE: "field sheet",
}


def _evidence_rows(evidence: Iterable[Evidence]) -> list[EvidenceRow]:
    return [
        EvidenceRow(file=e.file, row=e.row, sample_id=e.sample_id, analyte=e.analyte, detail=e.detail) for e in evidence
    ]


def _number(outcomes: list[Outcome]) -> list[ReviewItem]:
    drafts: list[Draft] = [draft for outcome in outcomes for draft in outcome.drafts]
    items: list[ReviewItem] = []
    for index, draft in enumerate(drafts, start=1):
        related = [
            other_index
            for other_index, other in enumerate(drafts, start=1)
            if other_index != index and set(other.samples) & set(draft.samples)
        ]
        items.append(
            ReviewItem(
                number=index,
                check=draft.check,
                title=draft.title,
                found=draft.found,
                samples=list(draft.samples),
                evidence=_evidence_rows(draft.evidence),
                rule=draft.rule,
                source=draft.source,
                scientist_decides=draft.scientist_decides,
                related_items=related,
            )
        )
    return items


def _rows(site: SiteData, ids: IdReport, items: list[ReviewItem]) -> list[TidyRow]:
    by_row: dict[int, list[int]] = {}
    for item in items:
        for evidence in item.evidence:
            if evidence.file == LAB_FILE:
                by_row.setdefault(evidence.row, []).append(item.number)
    id_item = {sample: item.number for item in items if item.check == "sample ids" for sample in item.samples}
    rows: list[TidyRow] = []
    for lab in site.lab:
        coc = site.coc_row(lab.sample_id)
        field = site.field_row(lab.sample_id)
        if coc is not None:
            status = "matched"
        elif lab.sample_id in ids.suggestions:
            status = (
                f"not matched: probably {ids.suggestions[lab.sample_id]}, not confirmed "
                f"(review item {id_item.get(lab.sample_id, '?')})"
            )
        else:
            status = f"not matched (review item {id_item.get(lab.sample_id, '?')})"
        rows.append(
            TidyRow(
                sample_id=lab.sample_id,
                id_status=status,
                lab_report=lab.lab_report,
                matrix=lab.matrix,
                qa_type=field.qa_type if field is not None else None,
                sampled=lab.sampled.iso(),
                analyte=lab.analyte,
                reported=lab.shown(),
                value=None if lab.value is None else fmt(lab.value),
                lor=fmt(lab.lor),
                unit=lab.canonical_unit,
                detected=lab.detected,
                extracted=None if lab.extracted is None else lab.extracted.isoformat(),
                days_to_extraction=days_held(lab),
                lab_row=lab.row,
                coc_row=coc.row if coc is not None else None,
                field_row=field.row if field is not None else None,
                review_items=sorted(set(by_row.get(lab.row, []))),
            )
        )
    return rows


def _counts(lab: Iterable[LabRow]) -> list[CountRow]:
    grouped: dict[tuple[str, str], list[LabRow]] = {}
    for row in lab:
        grouped.setdefault((row.matrix, row.analyte), []).append(row)
    ordered = sorted(grouped.items(), key=lambda item: item[0][0])
    return [
        CountRow(
            matrix=rows[0].matrix,
            analyte=analyte,
            unit=rows[0].canonical_unit,
            results=len(rows),
            detected=sum(row.detected for row in rows),
            not_detected=sum(not row.detected for row in rows),
        )
        for (_, analyte), rows in ordered
    ]


def _files(site: SiteData) -> list[FileRead]:
    examples = {
        LAB_FILE: site.lab[0].sampled.written,
        COC_FILE: site.coc[0].sampled.written,
        FIELD_FILE: site.field[0].sampled.written,
    }
    return [
        FileRead(
            file=name,
            role=_ROLES[name],
            header_row=table.header_row,
            data_rows=len(table.records),
            date_example=examples[name],
        )
        for name, table in site.tables.items()
    ]


def _summary(site: SiteData, items: list[ReviewItem], outcomes: list[Outcome], passed: int) -> str:
    soil = sum(row.matrix == "soil" for row in site.lab)
    water = len(site.lab) - soil
    samples = len(site.lab_samples())
    return (
        f"Read 3 files for {site.site_id} (synthetic): {plural(len(site.lab), 'lab result')} for "
        f"{plural(samples, 'sample')} ({soil} soil, {water} water), {plural(len(site.coc), 'chain-of-custody row')} "
        f"and {plural(len(site.field), 'field-sheet row')}. Units were converted exactly to ug/L (water) and mg/kg "
        f"(soil) and dates read day first. {plural(len(outcomes), 'check')} ran: "
        f"{plural(len(items), 'review item')} for the scientist to decide, {plural(passed, 'thing')} checked and not "
        "flagged, each listed. Nothing was merged, rejected or corrected silently."
    )


def _notes(site: SiteData) -> list[str]:
    notes = [
        site.synthetic,
        sources.INVESTIGATION_LEVEL,
        sources.RULE_CHOICE,
        site.soil_scenario,
        site.water_scenario,
        "Every review item ends with what the scientist decides. The tool flags and explains; it does not accept, "
        "reject or correct any result.",
    ]
    if site.has_private_header:
        notes.append(
            "The field sheet header names a client and a site address (both fictional). They are left out of this "
            "output."
        )
    return notes


def run(site: SiteData, config: QaConfig = DEFAULT_CONFIG) -> TidyResult:
    """Run every check on ``site`` and build the result."""
    ids = check_sample_ids(site)
    outcomes = [
        ids.outcome,
        check_units(site),
        check_duplicates(site, config),
        check_holding(site, ids.unmatched_lab_ids),
        check_blanks(site, config),
        check_lor(site),
    ]
    items = _number(outcomes)
    passed = [
        CheckedNotFlagged(check=p.check, what=p.what, evidence=_evidence_rows(p.evidence))
        for outcome in outcomes
        for p in outcome.passed
    ]
    runs = [
        CheckRun(
            check=outcome.check,
            what_was_checked=outcome.what_was_checked,
            rule=outcome.rule,
            source=outcome.source,
            checked=outcome.checked,
            review_items=[item.number for item in items if item.check == outcome.check],
        )
        for outcome in outcomes
    ]
    return TidyResult(
        site=site.site_id,
        synthetic=site.synthetic,
        summary=_summary(site, items, outcomes, len(passed)),
        files=_files(site),
        samples=len(site.lab_samples()),
        results=len(site.lab),
        counts=_counts(site.lab),
        review_items=items,
        checks=runs,
        checked_not_flagged=passed,
        not_checked=[
            NotChecked(check=s.check, what=s.what, reason=s.reason) for outcome in outcomes for s in outcome.skipped
        ],
        rows=_rows(site, ids, items),
        settings=QaSettings(
            rpd_review_above_percent=fmt(config.rpd_review_above),
            rpd_investigate_above_percent=fmt(config.rpd_investigate_above),
            rpd_no_limit_below_lor_multiple=fmt(config.rpd_no_limit_below_lor_multiple),
            blank_marginal_factor=fmt(config.blank_marginal_factor),
        ),
        notes=_notes(site),
    )


@cache
def _packaged(site: str) -> TidyResult:
    return run(packaged_site(site))


def tidy_lab_files(site: str = "FDS-01", *, data: SiteData | None = None, config: QaConfig | None = None) -> TidyResult:
    """Tidy one site's files. ``data`` and ``config`` let tests use their own site or thresholds."""
    if data is not None or config is not None:
        return run(data if data is not None else packaged_site(resolve_site(site)), config or DEFAULT_CONFIG)
    return _packaged(resolve_site(site))


def get_review_item(site: str, number: int, *, data: SiteData | None = None) -> ReviewItemDetail:
    """One review item in full, with every evidence row quoted exactly as it is in its file."""
    loaded = data if data is not None else packaged_site(resolve_site(site))
    result = tidy_lab_files(site, data=data)
    items = result.review_items
    if not items:
        raise EvidencelineError(f"{loaded.site_id} has no review items; see checked_not_flagged for what was checked.")
    if not 1 <= number <= len(items):
        raise EvidencelineError(
            f"Review item {number} does not exist for {loaded.site_id}. Items are numbered 1 to {len(items)}."
        )
    item = items[number - 1]
    seen: set[tuple[str, int]] = set()
    lines: list[SourceLine] = []
    for evidence in item.evidence:
        key = (evidence.file, evidence.row)
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            SourceLine(file=evidence.file, row=evidence.row, text=loaded.tables[evidence.file].line(evidence.row))
        )
    return ReviewItemDetail(
        site=loaded.site_id,
        synthetic=loaded.synthetic,
        item=item,
        of_total=len(items),
        source_lines=lines,
        note=(f"{item.scientist_decides} Row numbers are the file's own line numbers. {sources.INVESTIGATION_LEVEL}"),
    )
